"""Separate AI module (Gemini or Anthropic) with API-key ROTATION. Never raises: returns None when unavailable.

Keys (any mix; all optional):  AI_API_KEYS=k1,k2,k3   AI_API_KEY=k   AI_API_KEY_1..AI_API_KEY_9=k
Config: AI_PROVIDER=gemini|anthropic (auto from AI_MODEL/key), AI_MODEL, AI_API_URL (base/override),
        AI_RPM (per-key requests/min pacing, default 10), AI_MAX_WAIT (max seconds to wait for a free key, default 20)
Rotation: each call goes to the key that is ready soonest (round-robin when equal). A key that returns
429/quota is cooled down and the SAME request is retried on the next key. Bad keys (400/401/403) are parked.
"""
import os, re, json, time, threading, urllib.request, urllib.error, logging
log = logging.getLogger("codescribe.ai")
UNAVAILABLE = "AI generation is currently unavailable. Analysis and template-based documentation are still available."
SYSTEM = ("You write software documentation. Use ONLY the structured project data provided. "
          "Never invent endpoints, fields, tables, features or behaviour. If something is missing say: "
          "'Information not detected in the provided project.' Be concise.")

# ---------------- key pool ----------------
def keys():
    raw = [os.getenv("AI_API_KEYS", ""), os.getenv("AI_API_KEY", "")] + [os.getenv(f"AI_API_KEY_{i}", "") for i in range(1, 10)]
    out = []
    for r in raw:
        for k in re.split(r"[,;\s]+", r or ""):
            k = k.strip().strip("'\"")
            if k and k not in out: out.append(k)
    return out
def available(): return bool(keys())
def _tag(k): return "…" + k[-4:]          # safe label for logs/UI; never log a full key

_lock = threading.Lock()
_ready = {}          # key -> time.time() when it may be used again (cooldown + pacing)
_dead = {}           # key -> time until which it is parked (invalid/forbidden)
_rr = [0]
_stats = {"calls": 0, "ok": 0, "rotations": 0, "failures": 0}
def reset(): 
    with _lock: _ready.clear(); _dead.clear(); _rr[0] = 0; _stats.update(calls=0, ok=0, rotations=0, failures=0)
def _interval(): return 60.0 / max(0.1, float(os.getenv("AI_RPM", "10")))
def _reserve(tried):
    """Pick the usable key (not in `tried`) that is ready soonest; reserve its next pacing slot. -> (key, wait_seconds, previous_ready) or (None, 0, 0)"""
    ks = keys(); now = time.time()
    with _lock:
        cands = [(k, max(_ready.get(k, 0), _dead.get(k, 0), now)) for k in ks if k not in tried and _dead.get(k, 0) <= now]
        if not cands: return None, 0, 0
        n = len(ks); start = _rr[0] % max(n, 1)
        k, at = min(cands, key=lambda c: (round(c[1], 1), (ks.index(c[0]) - start) % n))
        _rr[0] = ks.index(k) + 1
        prev = _ready.get(k, 0); _ready[k] = at + _interval()
        return k, max(0.0, at - now), prev
def _cool(k, secs):
    with _lock: _ready[k] = max(_ready.get(k, 0), time.time() + secs)
def _park(k, secs):
    with _lock: _dead[k] = time.time() + secs
def status():
    ks = keys(); now = time.time()
    with _lock:
        info = [dict(key=_tag(k), state="invalid" if _dead.get(k, 0) > now else ("cooling" if _ready.get(k, 0) > now + _interval() else "ready")) for k in ks]
        return dict(provider=provider(), model=model(), keys=len(ks), pool=info, **_stats)

# ---------------- provider ----------------
def model(): return os.getenv("AI_MODEL") or ("claude-sonnet-4-6" if provider() == "anthropic" else "gemini-2.5-flash")
def provider():
    p = os.getenv("AI_PROVIDER", "").lower()
    if p in ("gemini", "anthropic"): return p
    m = os.getenv("AI_MODEL", "").lower(); ks = keys()
    if m.startswith("claude") or (ks and ks[0].startswith("sk-ant")): return "anthropic"
    return "gemini"
def redact(t):
    t = re.sub(r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"]+", r"\1=[REDACTED]", t)
    return re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", "[REDACTED KEY]", t, flags=re.S)
def _build(key, prompt, max_tokens, json_mode):
    m = model()
    if provider() == "anthropic":
        url = os.getenv("AI_API_URL", "https://api.anthropic.com/v1/messages")
        body = {"model": m, "max_tokens": max_tokens, "system": SYSTEM, "messages": [{"role": "user", "content": prompt}]}
        hdr = {"content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        base = os.getenv("AI_API_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        url = f"{base}/models/{m}:generateContent"
        gc = {"maxOutputTokens": max_tokens * 3, "temperature": 0.2}   # headroom: thinking tokens count toward the limit
        if "2.5-flash" in m: gc["thinkingConfig"] = {"thinkingBudget": 0}
        if json_mode: gc["responseMimeType"] = "application/json"
        body = {"systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gc}
        hdr = {"content-type": "application/json", "x-goog-api-key": key}
    return urllib.request.Request(url, json.dumps(body).encode(), hdr)
def _parse(j):
    if provider() == "anthropic": return "".join(b.get("text", "") for b in j.get("content", []))
    return "".join(p.get("text", "") for c in (j.get("candidates") or [])[:1] for p in (c.get("content") or {}).get("parts", []))

def complete(prompt, max_tokens=500, json_mode=False):
    """One completion, rotating across keys on rate-limit/quota/server errors. Returns text or None."""
    if not available(): return None
    prompt = redact(prompt)[:6000]; tried = set(); max_wait = float(os.getenv("AI_MAX_WAIT", "20"))
    with _lock: _stats["calls"] += 1
    while True:
        k, wait, prev = _reserve(tried)
        if k is None: break
        if wait > max_wait:
            with _lock: _ready[k] = prev
            break            # every key busy for too long -> give up quickly instead of hanging the UI
        if wait: time.sleep(wait)
        try:
            with urllib.request.urlopen(_build(k, prompt, max_tokens, json_mode), timeout=40) as r:
                txt = _parse(json.load(r)).strip()
            with _lock: _stats["ok"] += 1
            return txt or None                             # empty/blocked answer: not a key problem, don't rotate
        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode("utf-8", "ignore")[:400]
            except Exception: pass
            c = e.code; low = body.lower()
            if c == 429:
                daily = "perday" in low.replace("_", "").replace(" ", "") or "daily" in low
                try: ra = float(e.headers.get("Retry-After", 0))
                except Exception: ra = 0
                _cool(k, 3600 if daily else max(ra, 30))
            elif c in (400, 401, 403) and ("api key" in low or "api_key" in low or "permission" in low or c in (401, 403)):
                _park(k, 3600)                             # invalid / disabled key
            elif c in (500, 502, 503, 504): _cool(k, 5)
            else:
                log.warning("AI call rejected (%s) by key %s", c, _tag(k)); break   # bad request: another key won't help
            log.warning("AI key %s -> HTTP %s, rotating", _tag(k), c)
        except Exception as e:                             # network/timeout
            _cool(k, 5); log.warning("AI key %s -> %s, rotating", _tag(k), type(e).__name__)
        tried.add(k)
        with _lock: _stats["rotations"] += 1
    with _lock: _stats["failures"] += 1
    return None

# ---------------- task helpers (unchanged API) ----------------
def describe_api(a):
    return complete(f"Write one sentence describing this API endpoint from the detected data only.\nMethod: {a['method']}\nPath: {a['path']}\nFunction: {a['function']}\nParameters: {a['query_params']} {a['body_fields']}", max_tokens=120)
def summarize(kind, facts):
    return complete(f"Write a short (max 120 words) {kind} summary based only on these detected facts:\n{facts}")

def draft_srs(facts, keys_):
    """Ask the AI to draft ONLY the given empty SRS sections. Returns {key: text} or None."""
    t = complete("Draft these SRS sections: " + ", ".join(keys_) + ". Return ONLY a JSON object with exactly those keys; each value is a string of at most 60 words, "
                 "based only on the facts below. If a section cannot be reasonably inferred, use 'Information not detected in the provided project.'\n\nFACTS:\n" + facts, max_tokens=700, json_mode=True)
    if not t: return None
    try: j = json.loads(t[t.index("{"): t.rindex("}") + 1])
    except Exception: return None
    out = {k: v for k, v in j.items() if k in keys_ and isinstance(v, str) and v.strip() and "Information not detected" not in v}
    return out or None

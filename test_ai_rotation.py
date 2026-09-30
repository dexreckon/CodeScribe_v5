"""Tests key rotation against a local fake Gemini server (no network, no real keys)."""
import os, json, threading, importlib
from http.server import BaseHTTPRequestHandler, HTTPServer
BEHAVIOR = {}; HITS = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        key = self.headers.get("x-goog-api-key"); self.rfile.read(int(self.headers.get("content-length", 0))); HITS.append(key)
        code, body = BEHAVIOR.get(key, (200, None))
        if callable(code): code, body = code()
        if code == 200: body = {"candidates": [{"content": {"parts": [{"text": f"ok-{key}"}]}}]}
        self.send_response(code); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(json.dumps(body or {}).encode())
def setup_module(m):
    m.srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=m.srv.serve_forever, daemon=True).start()
    os.environ.update(AI_API_URL=f"http://127.0.0.1:{m.srv.server_port}", AI_RPM="6000", AI_PROVIDER="gemini", AI_MAX_WAIT="2")
    for k in ("AI_API_KEY", "AI_API_KEYS", "AI_MODEL"): os.environ.pop(k, None)
def teardown_module(m):
    m.srv.shutdown()
    for k in ("AI_API_KEYS", "AI_API_KEY", "AI_API_URL", "AI_PROVIDER", "AI_RPM", "AI_MAX_WAIT"): os.environ.pop(k, None)
def fresh(keys):
    os.environ["AI_API_KEYS"] = keys; import ai_service as AI; AI.reset(); HITS.clear(); BEHAVIOR.clear(); return AI

def test_no_keys():
    os.environ.pop("AI_API_KEYS", None); import ai_service as AI
    assert not AI.available() and AI.complete("x") is None
def test_round_robin_spreads_load():
    AI = fresh("A,B,C"); outs = [AI.complete("hi") for _ in range(6)]
    assert all(outs) and HITS == ["A", "B", "C", "A", "B", "C"]
def test_429_rotates_same_request_to_next_key():
    AI = fresh("A,B,C"); BEHAVIOR["A"] = (429, {"error": {"message": "quota exceeded"}})
    assert AI.complete("hi") == "ok-B" and HITS == ["A", "B"]
    HITS.clear(); assert AI.complete("hi") in ("ok-B", "ok-C") and "A" not in HITS   # A is cooling down, skipped
def test_invalid_key_is_parked():
    AI = fresh("bad,good"); BEHAVIOR["bad"] = (400, {"error": {"message": "API key not valid. Please pass a valid API key."}})
    assert AI.complete("hi") == "ok-good"; HITS.clear(); AI.complete("hi"); assert HITS == ["good"]
    assert AI.status()["pool"][0]["state"] == "invalid"
def test_503_outage_rotates():
    AI = fresh("A,B"); BEHAVIOR["A"] = (503, {}); assert AI.complete("hi") == "ok-B"
def test_all_keys_exhausted_returns_none_fast():
    AI = fresh("A,B,C")
    for k in "ABC": BEHAVIOR[k] = (429, {})
    assert AI.complete("hi") is None and len(HITS) == 3
    HITS.clear(); assert AI.complete("hi") is None and HITS == []           # all cooling -> no more calls, no hang
def test_key_recovers_after_cooldown():
    AI = fresh("A,B"); BEHAVIOR["A"] = (429, {}); AI.complete("hi"); BEHAVIOR.pop("A"); AI._ready["A"] = 0
    HITS.clear(); AI.complete("x"); AI.complete("y"); assert "A" in HITS
def test_bad_request_does_not_burn_keys():
    AI = fresh("A,B"); BEHAVIOR["A"] = (400, {"error": {"message": "bad prompt"}}); assert AI.complete("hi") is None and HITS == ["A"]
def test_json_mode_and_draft_srs():
    AI = fresh("A")
    BEHAVIOR["A"] = (200, None); import ai_service
    orig = AI.complete; AI.complete = lambda *a, **k: 'noise {"purpose": "Manage events.", "scope": "Information not detected in the provided project."} tail'
    try: assert AI.draft_srs("f", ["purpose", "scope"]) == {"purpose": "Manage events."}
    finally: AI.complete = orig
def test_status_never_leaks_full_key():
    AI = fresh("AIzaSySECRETKEY1234"); s = json.dumps(AI.status()); assert "SECRET" not in s and "1234" in s
def test_env_formats():
    os.environ["AI_API_KEYS"] = "a, b;c\nd"; os.environ["AI_API_KEY"] = "a"; os.environ["AI_API_KEY_2"] = "e"
    import ai_service as AI
    try: assert AI.keys() == ["a", "b", "c", "d", "e"]
    finally: os.environ.pop("AI_API_KEY", None); os.environ.pop("AI_API_KEY_2", None)

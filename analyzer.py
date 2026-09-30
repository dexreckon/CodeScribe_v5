import ast, io, os, re, zipfile, json, hashlib, posixpath
from html.parser import HTMLParser
EXTS = {".html":"HTML",".htm":"HTML",".css":"CSS",".js":"JavaScript",".ts":"TypeScript",".py":"Python",".java":"Java",".cpp":"C++",".cs":"C#",".php":"PHP",".dart":"Dart",".sql":"SQL",".md":"Markdown",".txt":"Text",".json":"JSON",".yaml":"YAML",".yml":"YAML",".pdf":"PDF",".docx":"Word"}
SKIP_DIRS = {".git","node_modules","__pycache__","venv",".venv","dist","build"}
HTTP = {"get","post","put","delete","patch"}
def ext_of(n): return os.path.splitext(n)[1].lower()
def skip(path):
    parts = path.replace("\\","/").split("/"); n = parts[-1].lower()
    if any(p in SKIP_DIRS for p in parts): return True
    if n.startswith(".env") and n not in (".env.example",".env.sample"): return True
    return n.endswith((".pem",".key",".pfx",".p12")) or n in ("id_rsa","id_ed25519","credentials.json") or "secret" in n
def safe_name(n):
    n = n.replace("\\","/"); parts = n.split("/")
    return None if n.startswith("/") or ".." in parts or (parts and ":" in parts[0]) else n
def text_of(name, data):
    e = ext_of(name)
    try:
        if e == ".docx":
            import docx; return "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
        if e == ".pdf":
            from pypdf import PdfReader; return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    except Exception: return ""
    return data.decode("utf-8", "replace")
def read_zip(data, limit):
    files, total = {}, 0
    try: z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile: raise ValueError("Unable to extract the ZIP file.")
    for i in z.infolist():
        if i.is_dir(): continue
        n = safe_name(i.filename)
        if n is None: raise ValueError("Unsafe file path detected inside the ZIP file.")
        if skip(n) or ext_of(n) not in EXTS: continue
        total += i.file_size
        if i.file_size > limit or total > limit * 5: raise ValueError("ZIP contents are too large.")
        files[n] = text_of(n, z.read(i))
    return files
def md5(s): return hashlib.md5(s.encode("utf-8", "ignore")).hexdigest()

def _py(path, src, out):
    try: tree = ast.parse(src)
    except SyntaxError: return
    seen = set()
    def fn(node, cls=None):
        seen.add(id(node))
        args = [a.arg for a in node.args.args + node.args.kwonlyargs if a.arg not in ("self","cls")]
        name = f"{cls}.{node.name}" if cls else node.name; doc = ast.get_docstring(node) or ""
        out["entities"].append(dict(kind="method" if cls else "function", name=name, file=path, params=", ".join(args),
            returns=ast.unparse(node.returns) if node.returns else "", doc=doc, line_start=node.lineno, line_end=node.end_lineno))
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str):
                at = d.func.attr; methods = []
                if at in HTTP: methods = [at.upper()]
                elif at == "route":
                    kw = [k for k in d.keywords if k.arg == "methods"]
                    methods = [ast.literal_eval(kw[0].value)[i].upper() for i in range(len(ast.literal_eval(kw[0].value)))] if kw else ["GET"]
                for m in methods:
                    p = d.args[0].value; pp = re.findall(r"[{<](?:\w+:)?(\w+)[}>]", p)
                    other = [a for a in args if a not in pp]
                    body = other if m in ("POST","PUT","PATCH") else []
                    out["apis"].append(dict(method=m, path=p, description=doc.split("\n")[0], path_params=", ".join(pp),
                        query_params=", ".join(o for o in other if o not in body), body_fields=", ".join(body),
                        auth="Dependency-based auth detected (Depends)" if "Depends(" in ast.unparse(node.args) else "", file=path, function=name))
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef):
            out["entities"].append(dict(kind="class", name=n.name, file=path, params="", returns="", doc=ast.get_docstring(n) or "", line_start=n.lineno, line_end=n.end_lineno))
            for s in n.body:
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)): fn(s, n.name)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and id(n) not in seen: fn(n)
def _prev_comment(src, pos):
    lines = src[:pos].rstrip().split("\n"); l = lines[-1].strip() if lines else ""
    return re.sub(r"^(//|/\*+|\*+/?|\*)\s*|\*/$", "", l).strip() if l.startswith(("//","*","/*")) or l.endswith("*/") else ""
def _js(path, src, out):
    ln = lambda pos: src.count("\n", 0, pos) + 1
    for m in re.finditer(r"function\s+(\w+)\s*\(([^)]*)\)|(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>", src):
        out["entities"].append(dict(kind="function", name=m.group(1) or m.group(3), file=path, params=(m.group(2) or m.group(4) or "").strip(), returns="", doc=_prev_comment(src, m.start()), line_start=ln(m.start()), line_end=None))
    for m in re.finditer(r"\bclass\s+(\w+)", src):
        out["entities"].append(dict(kind="class", name=m.group(1), file=path, params="", returns="", doc=_prev_comment(src, m.start()), line_start=ln(m.start()), line_end=None))
    _calls(path, src, out, 0)
    for m in re.finditer(r"\b(?:app|router)\.(get|post|put|delete|patch)\(\s*['\"`]([^'\"`]+)['\"`]", src):
        out["apis"].append(dict(method=m.group(1).upper(), path=m.group(2), description=_prev_comment(src, m.start()), path_params=", ".join(re.findall(r":(\w+)", m.group(2))), query_params="", body_fields="", auth="", file=path, function=""))
def _calls(path, src, out, line_off):
    """Frontend->backend calls: fetch('/x', {method:'POST'}) and axios.post('/x'). Stored as kind 'call'."""
    seen = set()
    for m in re.finditer(r"\bfetch\(\s*(['\"`])(.+?)\1(?:\s*,\s*\{[^}]*?method\s*:\s*['\"](\w+)['\"])?", src, re.S):
        seen.add(((m.group(3) or "GET").upper(), m.group(2), src.count("\n", 0, m.start()) + 1 + line_off))
    for m in re.finditer(r"\baxios\.(get|post|put|delete|patch)\(\s*(['\"`])(.+?)\2", src):
        seen.add((m.group(1).upper(), m.group(3), src.count("\n", 0, m.start()) + 1 + line_off))
    done = set()
    for meth, url, ln in sorted(seen, key=lambda t: t[2]):
        url = re.sub(r"\$\{[^}]*\}", "{}", url).strip()
        if (meth, url) in done or not url or url.startswith(("#", "data:")): continue
        done.add((meth, url))
        out["entities"].append(dict(kind="call", name=url, file=path, params="", returns=meth, doc="", line_start=ln, line_end=None))
class _Html(HTMLParser):
    """Collects title, headings, links, assets, forms/inputs and inline scripts from one HTML file."""
    def __init__(s):
        super().__init__(convert_charrefs=True)
        s.title = s.desc = ""; s.heads = []; s.links = []; s.assets = []; s.forms = []; s.scripts = []
        s.labels = {}; s._lab_for = None; s.cur = None; s.loose = None; s._cap = None; s._buf = []; s._script = None; s._lab_wrap = None
    def _field(s, a, tag):
        t = (a.get("type") or ("select" if tag == "select" else "textarea" if tag == "textarea" else "text")).lower()
        if t in ("hidden", "submit", "button", "reset", "image"): return
        nm = a.get("name") or a.get("id") or a.get("placeholder") or t
        f = dict(name=nm.strip(), type=t, required="required" in a, id=a.get("id") or "", label=(a.get("aria-label") or "").strip())
        tgt = s.cur or s.loose
        if tgt is None: s.loose = tgt = dict(id="", action="", method="", fields=[], submit="", line=s.getpos()[0], loose=True)
        tgt["fields"].append(f)
        if s._lab_wrap is not None: s._lab_wrap.append(f)
    def handle_starttag(s, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in ("title", "h1", "h2", "h3", "label", "button", "option") and s._cap is None: s._cap, s._buf = tag, []
        if tag == "label":
            s._lab_for = a.get("for"); s._lab_wrap = None if s._lab_for else []
        elif tag == "meta" and a.get("name", "").lower() == "description": s.desc = a.get("content", "").strip()
        elif tag == "a" and a.get("href"): s.links.append(a["href"].strip())
        elif tag == "link" and a.get("href") and "stylesheet" in a.get("rel", "").lower(): s.assets.append(a["href"].strip())
        elif tag == "script":
            if a.get("src"): s.assets.append(a["src"].strip())
            else: s._script = (s.getpos()[0], [])
        elif tag == "form":
            s.cur = dict(id=a.get("id") or a.get("name") or "", action=a.get("action", "").strip(), method=(a.get("method") or "GET").upper(), fields=[], submit="", line=s.getpos()[0])
            s.forms.append(s.cur)
        elif tag in ("input", "select", "textarea"): s._field(a, tag)
    def handle_endtag(s, tag):
        if tag == s._cap:
            txt = re.sub(r"\s+", " ", "".join(s._buf)).strip(); s._cap = None
            if tag == "title" and not s.title: s.title = txt
            elif tag in ("h1", "h2", "h3") and txt: s.heads.append((tag, txt))
            elif tag == "label" and txt:
                if s._lab_for: s.labels[s._lab_for] = txt
                for f in (s._lab_wrap or []): f["label"] = f["label"] or txt
                s._lab_wrap = None; s._lab_for = None
            elif tag == "button" and txt and (s.cur or s.loose) and not (s.cur or s.loose)["submit"]: (s.cur or s.loose)["submit"] = txt
        elif tag == "script" and s._script: s.scripts.append(("".join(s._script[1]), s._script[0])); s._script = None
        elif tag == "form": s.cur = None
    def handle_data(s, data):
        if s._script: s._script[1].append(data)
        elif s._cap: s._buf.append(data)
def _html(path, src, out, known):
    p = _Html()
    try: p.feed(src); p.close()
    except Exception: pass
    base = posixpath.dirname(path)
    def local(h):
        h = h.split("#")[0].split("?")[0].strip()
        if not h or re.match(r"^(?:[a-z][a-z0-9+.-]*:|//)", h, re.I): return ""
        r = posixpath.normpath(posixpath.join(base, h)) if not h.startswith("/") else h.lstrip("/")
        return r if r in known or not h.startswith("/") else h.lstrip("/")
    nav = list(dict.fromkeys(t for t in (local(h) for h in p.links) if t.lower().endswith((".html", ".htm")) and t != path))
    assets = list(dict.fromkeys(t for t in (local(h) for h in p.assets) if t))
    h1 = next((t for k, t in p.heads if k == "h1"), "")
    out["entities"].append(dict(kind="page", name=p.title or h1 or os.path.basename(path), file=path, params=", ".join(nav), returns=", ".join(assets),
                                doc=p.desc or h1, line_start=1, line_end=None))
    for f in p.forms + ([p.loose] if p.loose else []):
        if not f["fields"] and not f["action"] and not f["id"] and not f.get("loose"): continue
        for x_ in f["fields"]: x_["label"] = x_["label"] or p.labels.get(x_["id"], "")
        fl = ", ".join(f'{x_["label"] or x_["name"]}:{x_["type"]}{"*" if x_["required"] else ""}' for x_ in f["fields"])
        nm = f["id"] or ("inputs without a form element" if f.get("loose") else "unnamed form")
        out["entities"].append(dict(kind="form", name=nm, file=path, params=fl, returns=(f'{f["method"]} {f["action"]}'.strip() if not f.get("loose") else ""),
                                    doc=f["submit"], line_start=f["line"], line_end=None))
    for code, ln in p.scripts:
        sub = dict(entities=[], apis=[], tables=[]); _js(path, code, sub)
        for e in sub["entities"]:
            if e.get("line_start"): e["line_start"] += ln - 1
            out["entities"].append(e)
def _match(s, i):
    d = 0
    for j in range(i, len(s)):
        d += s[j] == "("; d -= s[j] == ")"
        if d == 0: return j
    return -1
def _sql(path, src, out):
    for m in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?(\w+)[`\"\]]?\s*\(", src, re.I):
        end = _match(src, m.end() - 1)
        if end < 0: continue
        body, parts, d, cur = src[m.end():end], [], 0, ""
        for ch in body:
            d += ch == "("; d -= ch == ")"
            if ch == "," and d == 0: parts.append(cur); cur = ""
            else: cur += ch
        parts.append(cur)
        prev = src[:m.start()].rstrip().split("\n")[-1].strip()
        desc = prev[2:].strip() if prev.startswith("--") else ""
        cols, pks, fks = [], set(), {}
        for p in parts:
            p = re.sub(r"--.*", "", p).strip()
            if not p: continue
            mp = re.match(r"(?:CONSTRAINT\s+\w+\s+)?PRIMARY\s+KEY\s*\(([^)]*)\)", p, re.I)
            mf = re.match(r"(?:CONSTRAINT\s+\w+\s+)?FOREIGN\s+KEY\s*\((\w+)\)\s*REFERENCES\s+(\w+)\s*\((\w+)\)", p, re.I)
            if mp: pks |= {c.strip(" `\"") for c in mp.group(1).split(",")}
            elif mf: fks[mf.group(1)] = f"{mf.group(2)}.{mf.group(3)}"
            elif re.match(r"(CONSTRAINT|UNIQUE|INDEX|KEY|CHECK)\b", p, re.I): pass
            else:
                mc = re.match(r"[`\"]?(\w+)[`\"]?\s+([A-Za-z]+(?:\s*\([^)]*\))?)(.*)", p, re.S)
                if not mc: continue
                rest = mc.group(3); r = re.search(r"REFERENCES\s+(\w+)\s*\((\w+)\)", rest, re.I)
                cols.append(dict(name=mc.group(1), dtype=mc.group(2), nullable=0 if re.search(r"NOT\s+NULL|PRIMARY\s+KEY", rest, re.I) else 1,
                                 pk=1 if re.search(r"PRIMARY\s+KEY", rest, re.I) else 0, fk=f"{r.group(1)}.{r.group(2)}" if r else ""))
        for c in cols:
            if c["name"] in pks: c["pk"], c["nullable"] = 1, 0
            if c["name"] in fks: c["fk"] = fks[c["name"]]
        out["tables"].append(dict(name=m.group(1), file=path, description=desc, cols=cols))
def analyze(files):
    out = dict(entities=[], apis=[], tables=[])
    for path, src in files.items():
        e = ext_of(path)
        if e == ".py": _py(path, src, out)
        elif e in (".js", ".ts"): _js(path, src, out)
        elif e in (".java", ".cs", ".php", ".dart", ".cpp"):
            for m in re.finditer(r"\bclass\s+(\w+)", src):
                out["entities"].append(dict(kind="class", name=m.group(1), file=path, params="", returns="", doc="", line_start=src.count("\n", 0, m.start()) + 1, line_end=None))
        elif e == ".sql": _sql(path, src, out)
        elif e in (".html", ".htm"): _html(path, src, out, set(files))
        base = os.path.basename(path).lower()
        if base == "requirements.txt":
            for l in src.splitlines():
                l = l.split("#")[0].strip()
                if l: out["entities"].append(dict(kind="dependency", name=re.split(r"[=<>~! ]", l)[0], file=path, params=l, returns="", doc="", line_start=None, line_end=None))
        if base == "package.json":
            try:
                j = json.loads(src)
                for k in list(j.get("dependencies", {})) + list(j.get("devDependencies", {})):
                    out["entities"].append(dict(kind="dependency", name=k, file=path, params="", returns="", doc="", line_start=None, line_end=None))
            except Exception: pass
    return out

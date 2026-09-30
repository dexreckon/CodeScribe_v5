import os, io, zipfile, tempfile
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "h.db"); os.environ.pop("AI_API_KEY", None); os.environ.pop("AI_API_KEYS", None)
from fastapi.testclient import TestClient
import main, analyzer as A
SITE = {
 "site/index.html": '<html><head><title>Food Web - Home</title><meta name="description" content="Order food online"><link rel="stylesheet" href="css/s.css"></head><body><h1>Welcome</h1>'
   '<a href="menu.html">Menu</a><a href="gone.html">Gone</a><a href="https://x.com">ext</a>'
   '<form id="loginForm" action="/api/login" method="post"><label for="e">Email address</label><input id="e" name="email" type="email" required><input name="pw" type="password"><input type="hidden" name="t"><button>Sign in</button></form>'
   '<script>\nfunction loadMenu(c){ fetch(`/api/menu/${c}`); }\nfetch("/api/order",{method:"POST"});\n</script></body></html>',
 "site/menu.html": "<html><head><title>Menu Listing</title></head><body><h1>Our Menu</h1><a href='index.html'>home</a></body></html>",
 "site/css/s.css": "body{color:red}",
 "app.py": 'from fastapi import FastAPI\napp = FastAPI()\n@app.post("/api/login")\ndef login(email, pw):\n    """Login."""\n    return {}\n',
}
def test_parser():
    r = A.analyze(SITE); k = lambda kind: [e for e in r["entities"] if e["kind"] == kind]
    p = {e["file"]: e for e in k("page")}
    assert p["site/index.html"]["name"] == "Food Web - Home" and p["site/index.html"]["doc"] == "Order food online"
    assert p["site/index.html"]["params"] == "site/menu.html, site/gone.html"          # external link ignored, paths resolved
    assert p["site/index.html"]["returns"] == "site/css/s.css"
    f = k("form")[0]; assert f["params"] == "Email address:email*, pw:password" and f["returns"] == "POST /api/login" and f["doc"] == "Sign in"   # hidden skipped, label used
    assert {(c["returns"], c["name"]) for c in k("call")} == {("GET", "/api/menu/{}"), ("POST", "/api/order")}
    fn = [e for e in k("function") if e["name"] == "loadMenu"][0]; assert fn["line_start"] == 2                        # inline script line offset
def test_parser_never_crashes_on_garbage():
    A.analyze({"a.html": "<<<>><form><input <div></form></a>&&;", "b.html": "", "c.htm": "<script>fetch("})
def test_full_pipeline_html_project():
    with TestClient(main.app) as c:
        pid = c.post("/projects", json={"name": "FoodWeb"}).json()["id"]
        z = io.BytesIO(); zf = zipfile.ZipFile(z, "w")
        for n, s in SITE.items(): zf.writestr(n, s)
        zf.close(); assert c.post(f"/projects/{pid}/upload", files={"files": ("p.zip", z.getvalue())}).status_code == 200
        st = c.post(f"/projects/{pid}/analyze").json(); assert st["pages"] == 2 and st["forms"] == 1
        c.post(f"/projects/{pid}/requirements", json={"title": "Menu Listing", "description": "Users can browse the menu", "type": "Functional"})
        ids = c.post(f"/projects/{pid}/generate-documentation", json={"types": list(main.G.DOC_TYPES)}).json()["document_ids"]
        md = {i: c.get(f"/documents/{i}").json() for i in ids}; txt = "\n".join(str(v.get("content", v)) for v in md.values())
        for needle in ["User Interface (detected pages)", "Food Web - Home", "Frontend Pages & Navigation", "Frontend → Backend Communication", "POST /api/login (login)",
                       "No matching endpoint detected", "site/index.html → site/gone.html", "Pages and screens", "Email address:email*"]:
            assert needle in txt, needle
        a = c.get(f"/projects/{pid}/analysis").json()
        assert a["trace"][0]["functions"][0] == "Menu Listing (page)"                       # requirement traced to the page
        assert any(g["type"] == "UI" and "gone.html" in g["item"] for g in a["gaps"])      # broken link reported as a gap
        for i in ids[:3]:
            for fmt in ("pdf", "docx"): assert c.get(f"/documents/{i}/export?fmt={fmt}").status_code == 200

import os, io, zipfile, tempfile
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ.pop("AI_API_KEY", None)
from fastapi.testclient import TestClient
import main
def test_flow():
    with TestClient(main.app) as c:
        assert c.get("/health").json() == {"status": "ok"}
        assert c.post("/projects", json={"name": "  "}).status_code == 400
        pid = c.post("/projects", json={"name": "CodeScribe"}).json()["id"]
        assert c.post("/projects", json={"name": "codescribe"}).status_code == 409
        z = io.BytesIO(); zf = zipfile.ZipFile(z, "w"); zf.writestr("a.py", 'from fastapi import FastAPI\napp=FastAPI()\n@app.get("/x")\ndef f():\n    """d"""\n'); zf.writestr("s.sql", "CREATE TABLE t (id INTEGER PRIMARY KEY);"); zf.close()
        assert c.post(f"/projects/{pid}/upload", files={"files": ("p.zip", z.getvalue())}).status_code == 200
        assert c.post(f"/projects/{pid}/analyze").json()["apis"] == 1
        assert c.post(f"/projects/{pid}/upload", files={"files": ("bad.zip", b"nope")}).status_code == 400
        c.post(f"/projects/{pid}/requirements", json={"title": "X thing", "description": "d"})
        assert c.post(f"/projects/{pid}/test-cases/generate").json()["generated"] >= 2
        ids = c.post(f"/projects/{pid}/generate-documentation", json={"types": list(main.G.DOC_TYPES)}).json()["document_ids"]
        for fmt in ("pdf", "docx", "md", "txt"): assert c.get(f"/documents/{ids[0]}/export?fmt={fmt}").status_code == 200
        c.post(f"/projects/{pid}/generate-report"); assert c.get(f"/projects/{pid}/report").status_code == 200
        assert c.get("/search?term=login").status_code == 200
        assert c.post("/demo/load").json()["id"]

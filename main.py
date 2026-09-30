import os, io, json, logging, re, sqlite3, datetime
from typing import List
from fastapi import FastAPI, UploadFile, File, Form, Body, HTTPException
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError: pass
import database as D, analyzer as A, docs as G, ai_service as AI, demo
from database import q, x, xm

BASE = os.path.dirname(os.path.abspath(__file__))
SERVERLESS = D.SERVERLESS
FMT = "%(asctime)s %(levelname)s %(message)s"
try:
    for p in (("data",), ("static", "assets")): os.makedirs(os.path.join(BASE, *p), exist_ok=True)
    if SERVERLESS: raise OSError
    logging.basicConfig(filename=os.path.join(BASE, "data", "codescribe.log"), level=logging.INFO, format=FMT)
except OSError:
    logging.basicConfig(level=logging.INFO, format=FMT)   # read-only disk (Vercel): log to stdout -> Vercel Logs
log = logging.getLogger("codescribe")
MAX_MB = int(os.getenv("MAX_UPLOAD_MB", "20")); LIMIT = MAX_MB * 1024 * 1024
DUP = "Project with this name already exists. Please choose a different project name."
app = FastAPI(title="CodeScribe")
@app.on_event("startup")
def _start():
    D.init_db()
    if SERVERLESS and not q("select 1 from projects limit 1"): load_demo()   # /tmp is wiped on cold start: keep the demo populated for visitors
@app.exception_handler(Exception)
async def _err(request, exc):
    log.exception("Unhandled error"); return JSONResponse({"detail": "Unable to complete the request. Please try again."}, status_code=500)
def need(pid):
    p = q("select * from projects where id=?", (pid,), one=True)
    if not p: raise HTTPException(404, "Project not found.")
    return p
def touch(pid): x("update projects set updated_at=CURRENT_TIMESTAMP where id=?", (pid,))
def pname(b, pid=None):
    n = " ".join(str(b.get("name", "")).split())
    if not n: raise HTTPException(400, "Please enter a project name.")
    if len(n) > 80: raise HTTPException(400, "Project name is too long (maximum 80 characters).")
    if q("select id from projects where name=? collate nocase and id is not ?", (n, pid)): raise HTTPException(409, DUP)
    return n
@app.get("/health")
def health(): return {"status": "ok"}
@app.get("/settings")
def settings(): return dict(ai_available=AI.available(), ai_keys=len(AI.keys()), ai_provider=AI.provider(), ai_model=AI.model(), max_upload_mb=MAX_MB, supported=sorted(A.EXTS), ai_message=None if AI.available() else AI.UNAVAILABLE)

# ---------- projects ----------
@app.post("/projects")
def create_project(b: dict = Body(...)):
    n = pname(b)
    try: pid = x("insert into projects(name,description,tech,version) values(?,?,?,?)", (n, str(b.get("description", ""))[:1000], str(b.get("tech", ""))[:200], str(b.get("version") or "1.0.0")[:20]))
    except sqlite3.IntegrityError: raise HTTPException(409, DUP)
    return need(pid)
@app.get("/projects")
def list_projects(search: str = ""):
    out = q("select * from projects where name like ? or description like ? order by updated_at desc", (f"%{search}%",) * 2)
    for p in out: p["coverage"] = G.overall(G.get(p["id"]))
    return out
@app.get("/projects/{pid}")
def get_project(pid: int): return need(pid)
@app.put("/projects/{pid}")
def update_project(pid: int, b: dict = Body(...)):
    p = need(pid); n = pname(b, pid) if "name" in b else p["name"]
    x("update projects set name=?,description=?,tech=?,version=?,updated_at=CURRENT_TIMESTAMP where id=?", (n, b.get("description", p["description"]), b.get("tech", p["tech"]), b.get("version", p["version"]), pid)); return need(pid)
@app.delete("/projects/{pid}")
def delete_project(pid: int): need(pid); x("delete from projects where id=?", (pid,)); return {"deleted": True}

# ---------- upload / analysis ----------
def store_files(pid, files, replace=False):
    if replace: x("delete from project_files where project_id=?", (pid,))
    for path, txt in files.items():
        e = A.ext_of(path)
        x("insert into project_files(project_id,path,ext,size,lang,content,hash) values(?,?,?,?,?,?,?) on conflict(project_id,path) do update set size=excluded.size,content=excluded.content,hash=excluded.hash,lang=excluded.lang",
          (pid, path, e, len(txt.encode("utf-8", "ignore")), A.EXTS.get(e, ""), txt, A.md5(txt)))
@app.post("/projects/{pid}/upload")
async def upload(pid: int, files: List[UploadFile] = File(...)):
    need(pid); stored, rejected = {}, []
    for f in files:
        data = await f.read()
        if len(data) > LIMIT: raise HTTPException(413, f"File too large. The upload limit is {MAX_MB} MB.")
        name = A.safe_name(f.filename or "") ; e = A.ext_of(f.filename or "")
        if name is None: rejected.append(f.filename); continue
        if e == ".zip":
            try: stored.update(A.read_zip(data, LIMIT))
            except ValueError as ex: raise HTTPException(400, str(ex))
        elif e in A.EXTS and not A.skip(name): stored[os.path.basename(name)] = A.text_of(name, data)
        else: rejected.append(f.filename)
    if not stored: raise HTTPException(400, "Please upload a supported file.")
    store_files(pid, stored); touch(pid)
    return dict(stored=len(stored), rejected=rejected)
def run_analysis(pid):
    files = {r["path"]: r["content"] or "" for r in q("select path,content from project_files where project_id=?", (pid,))}
    res = A.analyze(files)
    x("delete from code_entities where project_id=?", (pid,)); x("delete from api_endpoints where project_id=?", (pid,)); x("delete from database_tables where project_id=?", (pid,))
    xm("insert into code_entities(project_id,kind,name,file,params,returns,doc,line_start,line_end) values(?,?,?,?,?,?,?,?,?)", [(pid, e["kind"], e["name"], e["file"], e["params"], e["returns"], e["doc"], e["line_start"], e["line_end"]) for e in res["entities"]])
    xm("insert into api_endpoints(project_id,method,path,description,path_params,query_params,body_fields,auth,file,function) values(?,?,?,?,?,?,?,?,?,?)", [(pid, a["method"], a["path"], a["description"], a["path_params"], a["query_params"], a["body_fields"], a["auth"], a["file"], a["function"]) for a in res["apis"]])
    for t in res["tables"]:
        tid = x("insert into database_tables(project_id,name,file,description) values(?,?,?,?)", (pid, t["name"], t["file"], t["description"]))
        xm("insert into database_columns(table_id,name,dtype,nullable,pk,fk) values(?,?,?,?,?,?)", [(tid, k["name"], k["dtype"], k["nullable"], k["pk"], k["fk"]) for k in t["cols"]])
    x("update projects set status='Analyzed',updated_at=CURRENT_TIMESTAMP where id=?", (pid,))
    s = G.stats(G.get(pid)); x("insert into project_analysis(project_id,stats) values(?,?)", (pid, json.dumps(s))); return s
@app.post("/projects/{pid}/analyze")
def analyze(pid: int):
    need(pid)
    if not q("select 1 from project_files where project_id=? limit 1", (pid,)): raise HTTPException(400, "Please upload a supported file.")
    try: return run_analysis(pid)
    except Exception: log.exception("analysis failed"); raise HTTPException(500, "Unable to analyze the project.")
@app.get("/projects/{pid}/analysis")
def analysis(pid: int):
    p = need(pid); d = G.get(pid)
    return dict(project=p, stats=G.stats(d), coverage=G.coverage(d), gaps=G.gaps(d), languages=G.langs(d), trace=G.trace(d), functions=d["funcs"][:500], classes=d["classes"][:200], deps=[e["name"] for e in d["deps"]], files=d["files"])
@app.get("/projects/{pid}/apis")
def apis(pid: int): need(pid); return q("select * from api_endpoints where project_id=?", (pid,))
@app.get("/projects/{pid}/database")
def database(pid: int): need(pid); return G.get(pid)["tables"]
@app.post("/projects/{pid}/ai/enhance")
def ai_enhance(pid: int):
    need(pid)
    if not AI.available(): return dict(updated=0, message=AI.UNAVAILABLE)
    limit = int(os.getenv("AI_ENHANCE_LIMIT", "25")); n = fails = 0
    rows = q("select * from api_endpoints where project_id=? and description='' and ai_description=''", (pid,))
    for a in rows[:limit]:
        t = AI.describe_api(a)
        if t: x("update api_endpoints set ai_description=? where id=?", (t, a["id"])); n += 1; fails = 0
        else:
            fails += 1
            if fails >= 2: break                       # every key is exhausted/cooling: stop instead of burning time
    left = len(rows) - n
    msg = None if n and not left else (f"{left} endpoint(s) still without an AI description - run again in a minute (free-tier limits)." if n else AI.UNAVAILABLE)
    return dict(updated=n, remaining=left, message=msg)
@app.get("/ai/status")
def ai_status(): return AI.status()

# ---------- requirements / tickets ----------
def next_id(table, col, pre, pid):
    nums = [int(m.group(1)) for r in q(f"select {col} v from {table} where project_id=?", (pid,)) if (m := re.search(r"(\d+)$", r["v"] or ""))]
    return f"{pre}{(max(nums) if nums else 0) + 1:03d}"
def one_of(v, opts, dflt): return v if v in opts else dflt
@app.post("/projects/{pid}/requirements")
def add_req(pid: int, b: dict = Body(...)):
    need(pid); t = " ".join(str(b.get("title", "")).split())
    if not t: raise HTTPException(400, "Please enter a requirement title.")
    i = x("insert into requirements(project_id,req_id,title,description,type,priority,status,source) values(?,?,?,?,?,?,?,?)", (pid, next_id("requirements", "req_id", "REQ-", pid), t[:200], str(b.get("description", ""))[:2000], one_of(b.get("type"), ["Functional", "Non-functional"], "Functional"), one_of(b.get("priority"), ["Low", "Medium", "High", "Critical"], "Medium"), one_of(b.get("status"), ["Proposed", "Planned", "In Progress", "Implemented", "Unknown"], "Proposed"), str(b.get("source") or "Manual entry")[:200]))
    touch(pid); return q("select * from requirements where id=?", (i,), one=True)
@app.get("/projects/{pid}/requirements")
def get_reqs(pid: int): need(pid); return q("select * from requirements where project_id=? order by id", (pid,))
@app.put("/requirements/{rid}")
def put_req(rid: int, b: dict = Body(...)):
    r = q("select * from requirements where id=?", (rid,), one=True)
    if not r: raise HTTPException(404, "Requirement not found.")
    for k in ("title", "description", "type", "priority", "status"): r[k] = b.get(k, r[k])
    x("update requirements set title=?,description=?,type=?,priority=?,status=? where id=?", (r["title"], r["description"], r["type"], r["priority"], r["status"], rid)); return r
@app.delete("/requirements/{rid}")
def del_req(rid: int): x("delete from requirements where id=?", (rid,)); return {"deleted": True}
@app.post("/projects/{pid}/requirements/extract")
def extract_reqs(pid: int):
    need(pid); have = {r["description"].lower() for r in q("select description from requirements where project_id=?", (pid,))}; out = []
    for f in q("select path,content from project_files where project_id=? and ext in ('.md','.txt','.docx','.pdf')", (pid,)):
        for l in (f["content"] or "").splitlines():
            l = re.sub(r"^\s*([-*#]+|\d+[.)])\s*", "", l).strip()
            if 15 < len(l) < 300 and re.search(r"\b(shall|should|must|will be able to|able to)\b", l, re.I) and l.lower() not in have:
                out.append(dict(title=l[:80], description=l, source=f["path"], type="Non-functional" if re.search(r"perform|secur|availab|response time|scalab", l, re.I) else "Functional"))
    return out[:50]
@app.post("/projects/{pid}/tickets")
def add_ticket(pid: int, b: dict = Body(...)):
    need(pid); t = " ".join(str(b.get("title", "")).split())
    if not t: raise HTTPException(400, "Please enter a ticket title.")
    i = x("insert into tickets(project_id,ticket_id,title,description,type,priority,status) values(?,?,?,?,?,?,?)", (pid, str(b.get("ticket_id") or next_id("tickets", "ticket_id", "TCK-", pid))[:30], t[:200], str(b.get("description", ""))[:2000], one_of(b.get("type"), ["Feature", "Bug", "Improvement", "Task"], "Task"), one_of(b.get("priority"), ["Low", "Medium", "High", "Critical"], "Medium"), str(b.get("status") or "Open")[:30]))
    return q("select * from tickets where id=?", (i,), one=True)
@app.get("/projects/{pid}/tickets")
def get_tickets(pid: int): need(pid); return q("select * from tickets where project_id=?", (pid,))
@app.delete("/tickets/{tid}")
def del_ticket(tid: int): x("delete from tickets where id=?", (tid,)); return {"deleted": True}

# ---------- test cases ----------
@app.post("/projects/{pid}/test-cases/generate")
def gen_tests(pid: int): need(pid); return {"generated": G.gen_tests(pid)}
@app.get("/projects/{pid}/test-cases")
def get_tests(pid: int): need(pid); return q("select * from test_cases where project_id=? order by id", (pid,))
@app.put("/test-cases/{tid}")
def put_test(tid: int, b: dict = Body(...)):
    t = q("select * from test_cases where id=?", (tid,), one=True)
    if not t: raise HTTPException(404, "Test case not found.")
    for k in ("title", "objective", "preconditions", "steps", "expected", "actual"): t[k] = b.get(k, t[k])
    t["status"] = one_of(b.get("status", t["status"]), ["Not Executed", "Passed", "Failed", "Blocked"], t["status"])
    x("update test_cases set title=?,objective=?,preconditions=?,steps=?,expected=?,actual=?,status=?,generated_by='edited' where id=?", (t["title"], t["objective"], t["preconditions"], t["steps"], t["expected"], t["actual"], t["status"], tid)); return t
@app.delete("/test-cases/{tid}")
def del_test(tid: int): x("delete from test_cases where id=?", (tid,)); return {"deleted": True}
def export(md, title, p, fmt, fname):
    fmt = fmt.lower(); safe = re.sub(r"[^\w.-]+", "_", f"{p['name']}_{fname}")
    if fmt == "pdf": data, mt = G.to_pdf(md, title, p["name"], p["version"]), "application/pdf"
    elif fmt == "docx": data, mt = G.to_docx(md, title, p["name"], p["version"]), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif fmt == "md": data, mt = md.encode(), "text/markdown"
    elif fmt == "txt": data, mt = G.to_txt(md).encode(), "text/plain"
    else: raise HTTPException(400, "Unsupported export format.")
    return Response(data, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{safe}.{fmt}"'})
@app.get("/projects/{pid}/test-cases/export")
def export_tests(pid: int, fmt: str = "pdf"): p = need(pid); return export(G.md_tests(G.get(pid)), "Test Cases", p, fmt, "test_cases")

# ---------- documents ----------
def save_doc(pid, dtype, title, content, note):
    p = need(pid); doc = q("select * from documents where project_id=? and doc_type=?", (pid, dtype), one=True)
    did = doc["id"] if doc else x("insert into documents(project_id,doc_type,title) values(?,?,?)", (pid, dtype, title))
    n = q("select count(*) c from document_versions where document_id=?", (did,), one=True)["c"] + 1
    x("insert into document_versions(document_id,version,content,note,project_version) values(?,?,?,?,?)", (did, f"v1.{n-1}" if n > 1 else "v1.0", content, note, p["version"])); touch(pid); return did
@app.post("/projects/{pid}/generate-documentation")
def gen_docs(pid: int, b: dict = Body(...)):
    need(pid); types = [t for t in b.get("types", []) if t in G.DOC_TYPES]
    if not types: raise HTTPException(400, "Please select at least one document type.")
    d = G.get(pid); ai_used = False; out = []
    if "test_cases" in types and not d["tests"]: G.gen_tests(pid)
    for t in types:
        d = G.get(pid); ai_sec = None
        if t == "srs" and b.get("use_ai") and AI.available():
            facts, missing = G.srs_facts(d)
            if missing:
                ai_sec = AI.draft_srs(facts, missing); ai_used = ai_used or bool(ai_sec)
        md = G.g_srs(d, ai_sec) if t == "srs" else G.GEN[t](d)
        if b.get("use_ai") and AI.available() and t in ("overview", "srs", "architecture", "developer", "user"):
            s = AI.summarize(G.DOC_TYPES[t], json.dumps(G.stats(d)) + " " + json.dumps(G.langs(d)) + " " + (d["p"]["description"] or ""))
            if s: md += f"\n## AI Summary [AI-generated]\n\n{s}\n"; ai_used = True
        out.append(save_doc(pid, t, G.DOC_TYPES[t], md, "Generated by CodeScribe"))
    return dict(document_ids=out, ai_used=ai_used, ai_message=None if AI.available() else AI.UNAVAILABLE)
@app.get("/projects/{pid}/documents")
def list_docs(pid: int): need(pid); return q("select d.*,(select count(*) from document_versions where document_id=d.id) versions,(select max(created_at) from document_versions where document_id=d.id) updated from documents d where project_id=? order by id", (pid,))
@app.get("/documents/{did}")
def get_doc(did: int, version: str = ""):
    d = q("select * from documents where id=?", (did,), one=True)
    if not d: raise HTTPException(404, "Document not found.")
    vs = q("select id,version,note,project_version,created_at from document_versions where document_id=? order by id desc", (did,))
    v = q("select * from document_versions where document_id=? and version=?", (did, version), one=True) if version else q("select * from document_versions where document_id=? order by id desc limit 1", (did,), one=True)
    return dict(document=d, current=v, versions=vs)
@app.put("/documents/{did}")
def put_doc(did: int, b: dict = Body(...)):
    d = q("select * from documents where id=?", (did,), one=True)
    if not d: raise HTTPException(404, "Document not found.")
    save_doc(d["project_id"], d["doc_type"], d["title"], str(b.get("content", "")), str(b.get("note") or "Edited by user")[:200]); return get_doc(did)
@app.get("/documents/{did}/export")
def export_doc(did: int, fmt: str = "pdf", version: str = ""):
    r = get_doc(did, version)
    if not r["current"]: raise HTTPException(404, "Document version not found.")
    return export(r["current"]["content"], r["document"]["title"], need(r["document"]["project_id"]), fmt, r["document"]["doc_type"] + "_" + r["current"]["version"])

# ---------- report / history / compare ----------
@app.post("/projects/{pid}/generate-report")
def gen_report(pid: int):
    need(pid); return {"document_id": save_doc(pid, "report", "Project Report", G.g_report(G.get(pid)), "Report generated")}
@app.get("/projects/{pid}/report")
def get_report(pid: int):
    need(pid); d = q("select id from documents where project_id=? and doc_type='report'", (pid,), one=True)
    if not d: raise HTTPException(404, "No report has been generated yet.")
    dd = G.get(pid); r = get_doc(d["id"]); r.update(coverage=G.coverage(dd), gaps=G.gaps(dd), stats=G.stats(dd)); return r
@app.get("/projects/{pid}/history")
def history(pid: int):
    need(pid); return dict(documents=[dict(d, versions=q("select version,note,project_version,created_at from document_versions where document_id=? order by id", (d["id"],))) for d in q("select * from documents where project_id=?", (pid,))], changes=q("select * from project_changes where project_id=? order by id desc", (pid,)))
@app.post("/projects/{pid}/compare")
async def compare(pid: int, file: UploadFile = File(...), version: str = Form(...)):
    p = need(pid); version = version.strip()[:20]
    if not version: raise HTTPException(400, "Please enter the new version number.")
    if not (file.filename or "").lower().endswith(".zip"): raise HTTPException(400, "Please upload the new project version as a ZIP file.")
    data = await file.read()
    if len(data) > LIMIT: raise HTTPException(413, f"File too large. The upload limit is {MAX_MB} MB.")
    try: new = A.read_zip(data, LIMIT)
    except ValueError as e: raise HTTPException(400, str(e))
    old = {r["path"]: r["hash"] for r in q("select path,hash from project_files where project_id=?", (pid,))}
    nh = {k: A.md5(v) for k, v in new.items()}; ch = []
    for k in nh:
        if k not in old: ch.append(("added", "file", k, ""))
        elif old[k] != nh[k]: ch.append(("modified", "file", k, ""))
    ch += [("removed", "file", k, "") for k in old if k not in nh]
    oa = {(a["method"], a["path"]): a for a in q("select * from api_endpoints where project_id=?", (pid,))}
    res = A.analyze(new); na = {(a["method"], a["path"]): a for a in res["apis"]}
    for k, a in na.items():
        if k not in oa: ch.append(("added", "API", f"{k[0]} {k[1]}", ""))
        elif (oa[k]["body_fields"], oa[k]["query_params"], oa[k]["function"]) != (a["body_fields"], a["query_params"], a["function"]): ch.append(("modified", "API", f"{k[0]} {k[1]}", ""))
    ch += [("removed", "API", f"{k[0]} {k[1]}", "") for k in oa if k not in na]
    of = {e["name"] for e in q("select name from code_entities where project_id=? and kind in ('function','method')", (pid,))}; nf = {e["name"] for e in res["entities"] if e["kind"] in ("function", "method")}
    ch += [("added", "function", n, "") for n in sorted(nf - of)] + [("removed", "function", n, "") for n in sorted(of - nf)]
    op = {e["file"] for e in q("select file from code_entities where project_id=? and kind='page'", (pid,))}; np_ = {e["file"] for e in res["entities"] if e["kind"] == "page"}
    ch += [("added", "page", n, "") for n in sorted(np_ - op)] + [("removed", "page", n, "") for n in sorted(op - np_)]
    ot = {t["name"] for t in q("select name from database_tables where project_id=?", (pid,))}; nt = {t["name"] for t in res["tables"]}
    ch += [("added", "table", n, "") for n in nt - ot] + [("removed", "table", n, "") for n in ot - nt]
    xm("insert into project_changes(project_id,from_version,to_version,change_type,item_type,item,detail) values(?,?,?,?,?,?,?)", [(pid, p["version"], version, a, b_, c_, d_) for a, b_, c_, d_ in ch])
    store_files(pid, new, replace=True); x("update projects set version=? where id=?", (version, pid)); run_analysis(pid)
    types = {i[1] for i in ch}; aff = ["Changelog", "Release Notes"] + (["API Documentation", "Test Documentation", "Developer Guide", "User Guide"] if "API" in types else []) + (["User Guide", "Architecture Documentation"] if "page" in types else []) + (["Database Documentation"] if "table" in types else []) + (["Developer Guide"] if "file" in types and "API" not in types else [])
    return dict(from_version=p["version"], to_version=version, changes=[dict(change=a, type=b_, item=c_) for a, b_, c_, _ in ch], affected_documents=list(dict.fromkeys(aff)) if ch else [])

# ---------- search / dashboard / demo ----------
@app.get("/search")
def search_all(term: str = ""):
    t = f"%{term.strip()}%"; R = []
    if len(term.strip()) < 2: return R
    add = lambda rows, typ, f, ctx, nav: R.extend(dict(type=typ, title=f(r), context=ctx(r), nav=nav, project_id=r["project_id"]) for r in rows)
    for r in q("select id project_id,name,description from projects where name like ? or description like ?", (t, t)): R.append(dict(type="Project", title=r["name"], context=r["description"], nav="projects", project_id=r["project_id"]))
    add(q("select project_id,path from project_files where path like ?", (t,)), "File", lambda r: r["path"], lambda r: "", "upload")
    add(q("select project_id,kind,name,file from code_entities where name like ? and kind!='dependency'", (t,)), "Code", lambda r: r["name"], lambda r: f'{r["kind"]} in {r["file"]}', "upload")
    add(q("select project_id,method,path,file from api_endpoints where path like ? or function like ? or description like ?", (t, t, t)), "API", lambda r: f'{r["method"]} {r["path"]}', lambda r: r["file"], "apis")
    add(q("select project_id,req_id,title,description from requirements where title like ? or description like ?", (t, t)), "Requirement", lambda r: f'{r["req_id"]} {r["title"]}', lambda r: r["description"], "requirements")
    add(q("select project_id,name,file from database_tables where name like ?", (t,)), "Table", lambda r: r["name"], lambda r: r["file"], "database")
    add(q("select project_id,tc_id,title from test_cases where title like ? or tc_id like ?", (t, t)), "Test Case", lambda r: f'{r["tc_id"]} {r["title"]}', lambda r: "", "tests")
    add(q("select project_id,title from documents where title like ?", (t,)), "Document", lambda r: r["title"], lambda r: "", "docs")
    add(q("select project_id,ticket_id,title from tickets where title like ? or ticket_id like ?", (t, t)), "Ticket", lambda r: f'{r["ticket_id"]} {r["title"]}', lambda r: "", "requirements")
    names = {p["id"]: p["name"] for p in q("select id,name from projects")}
    for r in R: r["project"] = names.get(r["project_id"], "")
    return R[:100]
@app.get("/dashboard")
def dashboard():
    c = lambda t: q(f"select count(*) c from {t}", one=True)["c"]
    ps = q("select * from projects order by updated_at desc"); cov = []; gp = []
    for p in ps:
        d = G.get(p["id"]); p["coverage"] = G.overall(d)
        if p["coverage"] is not None: cov.append(p["coverage"])
        gp += [dict(g, project=p["name"], project_id=p["id"]) for g in G.gaps(d)[:5]]
    return dict(totals=dict(projects=len(ps), files=c("project_files"), requirements=c("requirements"), apis=c("api_endpoints"), tables=c("database_tables"), test_cases=c("test_cases"), documents=q("select count(*) c from documents where doc_type!='report'", one=True)["c"], coverage=round(sum(cov) / len(cov)) if cov else None),
        projects=ps[:5], documents=q("select d.id,d.title,p.name project,(select max(created_at) from document_versions where document_id=d.id) updated from documents d join projects p on p.id=d.project_id where doc_type!='report' order by 4 desc limit 5"),
        reports=q("select d.id,d.title,p.name project,d.project_id from documents d join projects p on p.id=d.project_id where doc_type='report' limit 5"), gaps=gp[:8])
@app.post("/demo/load")
def load_demo():
    ex = q("select id from projects where name='Campus Connect' collate nocase", one=True)
    if ex: return dict(id=ex["id"], existing=True)
    pid = x("insert into projects(name,description,tech,version) values(?,?,?,?)", ("Campus Connect", "A campus events and student portal.", "Python, FastAPI, SQL", "1.2.0"))
    store_files(pid, demo.FILES)
    for t, dsc, ty, pr in demo.REQS: x("insert into requirements(project_id,req_id,title,description,type,priority,status,source) values(?,?,?,?,?,?,?,?)", (pid, next_id("requirements", "req_id", "REQ-", pid), t, dsc, ty, pr, "Proposed", "demo data"))
    for tid, t, dsc, ty, pr in demo.TICKETS: x("insert into tickets(project_id,ticket_id,title,description,type,priority) values(?,?,?,?,?,?)", (pid, tid, t, dsc, ty, pr))
    run_analysis(pid); return dict(id=pid, existing=False)

if os.path.isdir(os.path.join(BASE, "static", "assets")): app.mount("/assets", StaticFiles(directory=os.path.join(BASE, "static", "assets")), name="assets")
@app.get("/style.css")
def css(): return FileResponse(os.path.join(BASE, "static", "style.css"))
@app.get("/script.js")
def js(): return FileResponse(os.path.join(BASE, "static", "script.js"))
@app.get("/")
def index(): return FileResponse(os.path.join(BASE, "static", "index.html"))

import re, io, datetime, textwrap
from database import q, x, xm
NA = "Information not detected in the provided project."
DOC_TYPES = {"overview":"Project Overview","srs":"SRS","api":"API Documentation","database":"Database Documentation","architecture":"Architecture Documentation","developer":"Developer Guide","user":"User Guide","test_doc":"Test Documentation","test_cases":"Test Cases","changelog":"Changelog","release_notes":"Release Notes"}
def today(): return datetime.date.today().strftime("%d %B %Y")
def c(s): return str(s if s not in (None, "") else NA).replace("|", "¦").replace("\n", " ")
def is_test(p): return "test" in p.lower()
def get(pid):
    ents = q("select * from code_entities where project_id=?", (pid,))
    tables = q("select * from database_tables where project_id=?", (pid,))
    for t in tables: t["cols"] = q("select * from database_columns where table_id=?", (t["id"],))
    return dict(p=q("select * from projects where id=?", (pid,), one=True), files=q("select path,ext,size,lang from project_files where project_id=?", (pid,)),
        funcs=[e for e in ents if e["kind"] in ("function", "method")], pages=[e for e in ents if e["kind"] == "page"], forms=[e for e in ents if e["kind"] == "form"], calls=[e for e in ents if e["kind"] == "call"], classes=[e for e in ents if e["kind"] == "class"], deps=[e for e in ents if e["kind"] == "dependency"],
        apis=q("select * from api_endpoints where project_id=?", (pid,)), tables=tables, reqs=q("select * from requirements where project_id=? order by id", (pid,)),
        tickets=q("select * from tickets where project_id=?", (pid,)), tests=q("select * from test_cases where project_id=? order by id", (pid,)),
        changes=q("select * from project_changes where project_id=? order by id", (pid,)))
def stats(d):
    return dict(files=len(d["files"]), functions=len(d["funcs"]), classes=len(d["classes"]), apis=len(d["apis"]), tables=len(d["tables"]), requirements=len(d["reqs"]),
                test_files=sum(1 for f in d["files"] if is_test(f["path"])), test_cases=len(d["tests"]), dependencies=len(d["deps"]), tickets=len(d["tickets"]), pages=len(d["pages"]), forms=len(d["forms"]))
def langs(d):
    r = {}
    for f in d["files"]: r[f["lang"]] = r.get(f["lang"], 0) + 1
    return r
def coverage(d):
    fn = [f for f in d["funcs"] if not is_test(f["file"]) and not f["name"].split(".")[-1].startswith("test")]
    def mk(a, b): return dict(done=a, total=b, pct=round(100 * a / b) if b else None)
    return dict(functions=mk(sum(1 for f in fn if f["doc"]), len(fn)), apis=mk(sum(1 for a in d["apis"] if a["description"]), len(d["apis"])),
        requirements=mk(sum(1 for t in trace(d) if t["tests"]), len(d["reqs"])), database=mk(sum(1 for t in d["tables"] if t["description"]), len(d["tables"])))
def overall(d):
    v = [i["pct"] for i in coverage(d).values() if i["pct"] is not None]
    return round(sum(v) / len(v)) if v else None
STOP = {"user","users","should","system","able","shall","must","with","that","this","from","their","have","will","into","when","them","receive","account","list"}
def stem(w):
    for suf in ("ation","ing","ion","ers","er","es","s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4: return w[:-len(suf)]
    return w
def toks(s):
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s or "")
    return {stem(t) for t in re.findall(r"[a-z]{4,}", s.lower().replace("_", " ")) if t not in STOP}
def trace(d):
    out = []
    for r in d["reqs"]:
        t = toks(r["title"] + " " + r["description"])
        # APIs: keep only the best-scoring ones
        sc = [(len(t & toks(a["path"] + " " + a["function"])), a) for a in d["apis"]]
        best = max((n for n, _ in sc), default=0)
        apis = [a for n, a in sc if n and n == best]
        ap = [f'{a["method"]} {a["path"]}' for a in apis]
        # Functions: handlers of matched APIs first; otherwise best-scoring functions only
        handlers = {a["function"] for a in apis if a["function"]}
        fs = [f["name"] + "()" for f in d["funcs"] if f["name"] in handlers]; code_sc = best if fs else 0
        if not fs:
            cand = [(len(t & toks(f["name"])), f) for f in d["funcs"] if not is_test(f["file"])]
            code_sc = max((n for n, _ in cand), default=0)
            fs = [f["name"] + "()" for n, f in cand if n and n == code_sc]
        # UI (HTML pages/forms): listed first when they match at least as well as the code
        ui = [(len(t & toks(p["name"] + " " + p["doc"] + " " + p["file"])), p["name"] + " (page)") for p in d["pages"]] + \
             [(len(t & toks(p["name"] + " " + p["params"] + " " + p["doc"])), f'{p["name"]} (form, {p["file"]})') for p in d["forms"]]
        top = max((n for n, _ in ui), default=0)
        if top and top >= code_sc: fs = [lbl for n, lbl in ui if n == top] + fs
        ts = [x_["tc_id"] for x_ in d["tests"] if x_["req_id"] == r["req_id"]]
        out.append(dict(req_id=r["req_id"], title=r["title"], functions=fs[:5], apis=ap[:5], tests=ts))
    return out
def gaps(d):
    g, cv = [], coverage(d)
    for f in d["funcs"]:
        if not is_test(f["file"]) and not f["name"].split(".")[-1].startswith("test") and not f["doc"]:
            g.append(dict(type="Function", item=f'{f["name"]}() in {f["file"]}', problem="Description/docstring not detected.", action="Add a docstring or comment describing purpose, parameters and return value.", nav="upload"))
    for a in d["apis"]:
        if not a["description"]: g.append(dict(type="API", item=f'{a["method"]} {a["path"]}', problem="Description not detected.", action="Add a description and request/response documentation.", nav="apis"))
    for t in trace(d):
        if not t["tests"]: g.append(dict(type="Requirement", item=f'{t["req_id"]} {t["title"]}', problem="No related test case detected.", action="Generate or write a test case for this requirement.", nav="tests"))
        if not t["functions"] and not t["apis"]: g.append(dict(type="Requirement", item=f'{t["req_id"]} {t["title"]}', problem="No related code or API detected (keyword match).", action="Link the requirement to its implementation or review the wording.", nav="requirements"))
    for a_, b_ in broken_links(d): g.append(dict(type="UI", item=f"{a_} → {b_}", problem="Linked page not found in the project.", action="Fix the link or upload the missing page.", nav="upload"))
    for t in d["tables"]:
        if not t["description"]: g.append(dict(type="Database", item=t["name"], problem="Table description not detected.", action="Add a SQL comment above CREATE TABLE describing the table.", nav="database"))
    paths = [f["path"].lower() for f in d["files"]]
    if d["files"] and not any(p.endswith("readme.md") or p.endswith("readme.txt") for p in paths): g.append(dict(type="Project", item="README", problem="No README file detected.", action="Add a README with setup and usage.", nav="upload"))
    if d["files"] and not any(is_test(p) for p in paths): g.append(dict(type="Project", item="Tests", problem="No test files detected.", action="Add automated tests.", nav="tests"))
    return g
def gen_tests(pid):
    d = get(pid); tr = {t["req_id"]: t for t in trace(d)}
    x("delete from test_cases where project_id=? and generated_by='template'", (pid,))
    n = q("select count(*) c from test_cases where project_id=?", (pid,), one=True)["c"]; rows = []
    def add(req, title, obj, steps, exp):
        nonlocal n; n += 1
        rows.append((pid, f"TC-{n:03d}", req, title, obj, NA, "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps)), exp))
    for r in d["reqs"]:
        api = next((a for a in d["apis"] if f'{a["method"]} {a["path"]}' in tr[r["req_id"]]["apis"]), None)
        if api:
            steps = [f'Send {api["method"]} {api["path"]}'] + ([f'Provide detected inputs: {api["body_fields"] or api["query_params"] or api["path_params"]}'] if (api["body_fields"] or api["query_params"] or api["path_params"]) else []) + ["Observe the response"]
        else: steps = ["Identify the feature described by the requirement", "Perform the described action", "Compare the outcome with the requirement"]
        add(r["req_id"], f'Verify: {r["title"]}', f'Verify that the system satisfies {r["req_id"]}.', steps, r["description"] or NA)
    for a in d["apis"]:
        req = next((t["req_id"] for t in tr.values() if f'{a["method"]} {a["path"]}' in t["apis"]), "")
        add(req, f'Request {a["method"]} {a["path"]}', f'Verify the endpoint behaves as implemented in {a["file"]}.', [f'Send {a["method"]} {a["path"]}', "Observe the status code and body"], "Response is consistent with the implementation. Status codes: " + NA)
    xm("insert into test_cases(project_id,tc_id,req_id,title,objective,preconditions,steps,expected) values(?,?,?,?,?,?,?,?)", rows)
    return len(rows)

def table(h, rows): return "| " + " | ".join(h) + " |\n|" + "---|" * len(h) + "\n" + "\n".join("| " + " | ".join(c(v) for v in r) + " |" for r in rows) + "\n"
def head(d, title): return f'*CodeScribe* — Project: **{d["p"]["name"]}** | Version: **{d["p"]["version"]}** | Generated: **{today()}**\n\n'
def g_overview(d):
    s = stats(d); p = d["p"]; tops = sorted({f["path"].split("/")[0] for f in d["files"]})
    return f'# Project Overview\n\n{head(d,"")}## Description\n\n{c(p["description"])}\n\n## Technology Stack\n\n{c(p["tech"])}\n\nDetected languages: {", ".join(f"{k} ({v})" for k, v in langs(d).items()) or NA}\n\n## Project Structure\n\nTop-level entries: {", ".join(tops) or NA}\n\n## Major Components\n\n' + (table(["File", "Functions", "Classes"], [(f["path"], sum(1 for e in d["funcs"] if e["file"] == f["path"]), sum(1 for e in d["classes"] if e["file"] == f["path"])) for f in d["files"] if any(e["file"] == f["path"] for e in d["funcs"] + d["classes"])]) or NA) + f'\n## Detected Features\n\n' + ("\n".join(f'- {r["req_id"]}: {r["title"]}' for r in d["reqs"]) or NA) + f'\n\n## Statistics\n\n' + table(["Metric", "Count"], list(s.items())) + f'\n## Summary\n\nThe analyzed project contains {s["files"]} supported files, {s["functions"]} functions/methods, {s["classes"]} classes, {s["apis"]} API endpoints and {s["tables"]} database tables.\n'
ROLE_TABLES = {"user","student","admin","teacher","staff","customer","member","instructor","organizer","attendee","employee","doctor","patient","manager","client"}
NOT_ACTORS = {"system","application","app","software","it","service","api","site","website","platform"}
def readme_text(d):
    """First paragraph of an uploaded README (max 400 chars), or ''."""
    for f in q("select path,content from project_files where project_id=?", (d["p"]["id"],)):
        if f["path"].lower().endswith(("readme.md", "readme.txt")):
            out = []
            for l in (f["content"] or "").splitlines():
                t = l.strip()
                if re.fullmatch(r"[=\-*_#\s]*", t) or t.startswith("#"):
                    if out: break
                    continue
                out.append(t)
            if out: return " ".join(out)[:400]
    return ""
def actors(d):
    out = {}
    for r in d["reqs"]:
        m = re.match(r"\s*(?:the\s+)?([A-Za-z]+)\s+(?:should|shall|must|will|can|may)\b", r["description"] or "", re.I)
        if m:
            w = m.group(1); w = w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
            if w.lower() not in NOT_ACTORS: out.setdefault(w.capitalize(), f'requirement {r["req_id"]}')
    for t in d["tables"]:
        n = t["name"].lower(); n = n[:-1] if n.endswith("s") else n
        if n in ROLE_TABLES: out.setdefault(n.capitalize(), f'table {t["name"]}')
    return out
def srs_base(d):
    """Sections derived only from detected project data ('' when nothing found)."""
    p, rd = d["p"], readme_text(d); fr = [r for r in d["reqs"]]
    purpose = c(p["description"]) if p["description"] else (f"{rd} (from README)" if rd else "")
    sc = []
    if fr: sc.append("Requirements in scope: " + "; ".join(r["title"] for r in fr) + ".")
    if d["apis"] or d["tables"]: sc.append(f'Detected interface: {len(d["apis"])} API endpoint(s), {len(d["tables"])} database table(s).')
    ac = actors(d)
    cons = [f'- {r["req_id"]} {r["title"]}: {c(r["description"])}' for r in d["reqs"] if r["type"] != "Functional"]
    if p["tech"]: cons.append(f'- Technology: {p["tech"]}')
    return dict(purpose=purpose, scope=" ".join(sc), stakeholders="",
                actors="\n".join(f"- {k} (from {v})" for k, v in ac.items()), constraints="\n".join(cons), assumptions="")
def srs_facts(d):
    """(facts text for the AI, list of SRS sections still empty)."""
    base = srs_base(d); missing = [k for k, v in base.items() if not v]
    f = [f'Project: {d["p"]["name"]}', f'Description: {d["p"]["description"] or "n/a"}', f'Technology: {d["p"]["tech"] or "n/a"}', f'README: {readme_text(d) or "n/a"}',
         "Requirements: " + ("; ".join(f'{r["req_id"]} {r["title"]}' for r in d["reqs"][:20]) or "none"),
         "Endpoints: " + ("; ".join(f'{a["method"]} {a["path"]}' for a in d["apis"][:20]) or "none"),
         "Tables: " + (", ".join(t["name"] for t in d["tables"]) or "none"),
         "Pages: " + ("; ".join(f'{p["name"]} ({p["file"]})' for p in d["pages"][:15]) or "none"),
         "Forms: " + ("; ".join(f'{p["name"]}: {p["params"]}' for p in d["forms"][:10]) or "none"),
         "Actors: " + (", ".join(actors(d)) or "none")]
    return "\n".join(f)[:3000], missing
def g_srs(d, ai=None):
    base, ai = srs_base(d), ai or {}
    def sec(k):
        if base[k]: return base[k]
        return ai[k].strip() + " [AI-generated]" if ai.get(k) else NA
    fr = [r for r in d["reqs"] if r["type"] == "Functional"]; nf = [r for r in d["reqs"] if r["type"] != "Functional"]
    rt = lambda L: table(["ID", "Title", "Description", "Priority", "Status", "Source"], [(r["req_id"], r["title"], r["description"], r["priority"], r["status"], r["source"]) for r in L]) if L else NA + "\n"
    return (f'# Software Requirements Specification\n\n{head(d,"")}## 1. Introduction\n\n{c(d["p"]["description"])}\n\n## 2. Purpose\n\n{sec("purpose")}\n\n## 3. Scope\n\n{sec("scope")}\n\n'
            f'## 4. System Overview\n\nTechnology: {c(d["p"]["tech"])}. Detected APIs: {len(d["apis"])}; tables: {len(d["tables"])}.\n\n## 5. Stakeholders\n\n{sec("stakeholders")}\n\n## 6. Actors\n\n{sec("actors")}\n\n'
            f'## 7. Functional Requirements\n\n{rt(fr)}\n## 8. Non-Functional Requirements\n\n{rt(nf)}\n## 9. System Features\n\n' + ("\n".join(f'- {a["method"]} {a["path"]}' for a in d["apis"]) or NA)
            + f'\n\n## 10. Constraints\n\n{sec("constraints")}\n\n## 11. Assumptions\n\n{sec("assumptions")}\n\n## 12. Dependencies\n\n' + ("\n".join(f'- {e["name"]}' for e in d["deps"]) or NA) + "\n")
def g_api(d):
    s = f'# API Documentation\n\n{head(d,"")}## 1. API Overview\n\nDetected endpoints: {len(d["apis"])}.\n\n## 2. Authentication\n\n' + ("\n".join(f'- {a["method"]} {a["path"]}: {a["auth"]}' for a in d["apis"] if a["auth"]) or NA) + "\n\n## 3. Endpoints\n\n"
    if not d["apis"]: return s + NA + "\n"
    s += table(["Method", "Path", "Description", "Source", "Function"], [(a["method"], a["path"], a["description"] or a["ai_description"], a["file"], a["function"]) for a in d["apis"]])
    s += "\n## 4-6. Parameters, Request Body and Response\n\n"
    for a in d["apis"]:
        s += f'### {a["method"]} {a["path"]}\n\n- Path parameters: {c(a["path_params"])}\n- Query parameters: {c(a["query_params"])}\n- Request body fields: {c(a["body_fields"])}\n- Response structure: {NA}\n'
        if a["ai_description"]: s += f'- Description: {a["ai_description"]} [AI-generated]\n'
        s += "\n"
    return s + f'## 7. Status Codes\n\n{NA}\n\n## 8. Error Handling\n\n{NA}\n\n## 9. Related Source Code\n\n' + "\n".join(f'- {a["method"]} {a["path"]} → {a["file"]} {a["function"]}' for a in d["apis"]) + "\n"
def g_db(d):
    s = f'# Database Documentation\n\n{head(d,"")}## 1. Database Overview\n\nDetected tables: {len(d["tables"])}.\n\n'
    if not d["tables"]: return s + NA + "\n"
    s += "## 2-8. Tables\n\n"
    for t in d["tables"]:
        s += f'### {t["name"]}\n\n{c(t["description"])} (source: {t["file"]})\n\n' + table(["Column", "Type", "Nullable", "Primary Key", "Foreign Key"], [(k["name"], k["dtype"], "Yes" if k["nullable"] else "No", "Yes" if k["pk"] else "No", k["fk"] or "-") for k in t["cols"]]) + "\n"
    rel = [f'{t["name"]}.{k["name"]} → {k["fk"]}' for t in d["tables"] for k in t["cols"] if k["fk"]]
    return s + "## Relationships\n\n" + ("\n".join("- " + r for r in rel) or NA) + f"\n\n## Indexes\n\n{NA}\n"
def g_arch(d):
    fe = [f for f in d["files"] if f["ext"] in (".html", ".css", ".js", ".ts")]; be = [f for f in d["files"] if f["ext"] in (".py", ".java", ".cs", ".php", ".cpp", ".dart")]
    layers = [x_ for x_ in [f"Frontend ({len(fe)} files)" if fe else "", f"API layer ({len(d['apis'])} endpoints)" if d["apis"] else "", f"Backend ({len(be)} files)" if be else "", f"Database ({len(d['tables'])} tables)" if d["tables"] else ""] if x_]
    return f'# Architecture Documentation\n\n{head(d,"")}## Architecture Summary\n\nDetected layers: {" → ".join(layers) or NA}\n\n```\n{"  →  ".join(layers) or NA}\n```\n\n## Frontend\n\n' + ("\n".join("- " + f["path"] for f in fe[:30]) or NA) + "\n\n## Backend\n\n" + ("\n".join("- " + f["path"] for f in be[:30]) or NA) + f'\n\n## Database\n\n' + (", ".join(t["name"] for t in d["tables"]) or NA) + "\n\n## Services / Classes\n\n" + ("\n".join(f'- {e["name"]} ({e["file"]})' for e in d["classes"]) or NA) + "\n\n## External Dependencies\n\n" + (", ".join(e["name"] for e in d["deps"]) or NA) + "\n\n## Component Communication\n\nInformation not detected in the provided project beyond the layers listed above.\n"
def g_dev(d):
    return f'# Developer Guide\n\n{head(d,"")}## Project Overview\n\n{c(d["p"]["description"])}\n\n## Technology Stack\n\n{c(d["p"]["tech"])}\n\n## Folder Structure\n\n```\n' + "\n".join(f["path"] for f in d["files"][:60]) + f'\n```\n\n## Installation\n\n' + ("Dependency manifest(s) detected: " + ", ".join(sorted({e["file"] for e in d["deps"]})) + "".join(f"\n\n```\n{cmd}\n```" for m, cmd in (("requirements.txt", "pip install -r requirements.txt"), ("package.json", "npm install")) if any(e["file"].split("/")[-1] == m for e in d["deps"])) if d["deps"] else NA) + "\n\n## Configuration\n\n" + (", ".join(f["path"] for f in d["files"] if ".env" in f["path"]) or NA) + "\n\n## Major Modules\n\n" + ("\n".join(f'- {e["name"]} — {e["file"]}' for e in d["classes"]) or NA) + "\n\n## APIs\n\n" + ("\n".join(f'- {a["method"]} {a["path"]}' for a in d["apis"]) or NA) + "\n\n## Database\n\n" + (", ".join(t["name"] for t in d["tables"]) or NA) + f"\n\n## Development Workflow\n\n{NA}\n\n## Troubleshooting\n\n{NA}\n"
def g_user(d):
    return f'# User Guide\n\n{head(d,"")}## Features described by requirements\n\n' + ("\n".join(f'- {r["title"]}: {c(r["description"])}' for r in d["reqs"] if r["type"] == "Functional") or NA) + "\n\n## How to use the software\n\n" + ("Available actions (from detected endpoints):\n\n" + "\n".join(f'- {a["method"]} {a["path"]}' + (f' — {a["description"]}' if a["description"] else "") for a in d["apis"]) + "\n" if d["apis"] else f"User-facing behavior: {NA}\n")
def g_testdoc(d):
    tr = trace(d)
    return f'# Test Documentation\n\n{head(d,"")}## Test Strategy Summary\n\n{len(d["tests"])} test cases recorded; {stats(d)["test_files"]} test files detected. Strategy details: {NA}\n\n## Test Cases\n\n' + (table(["ID", "Requirement", "Title", "Status"], [(t["tc_id"], t["req_id"], t["title"], t["status"]) for t in d["tests"]]) if d["tests"] else NA + "\n") + "\n## Requirements Coverage\n\n" + (table(["Requirement", "Tests"], [(t["req_id"], ", ".join(t["tests"]) or "No related test case detected.") for t in tr]) if tr else NA + "\n") + "\n## Detected Test Files\n\n" + ("\n".join("- " + f["path"] for f in d["files"] if is_test(f["path"])) or NA) + "\n\n## Testing Gaps\n\n" + ("\n".join(f'- {g["item"]}: {g["problem"]}' for g in gaps(d) if "test" in g["problem"].lower()) or "No testing gaps detected.") + "\n"
def md_tests(d):
    s = f'# Test Cases\n\n{head(d,"")}## Summary\n\nTotal: {len(d["tests"])}. Executed: {sum(1 for t in d["tests"] if t["status"]!="Not Executed")}.\n\n'
    if not d["tests"]: return s + "No test cases have been generated yet.\n"
    for t in d["tests"]:
        s += f'## {t["tc_id"]} — {t["title"]}\n\n- Requirement: {c(t["req_id"])}\n- Objective: {c(t["objective"])}\n- Preconditions: {c(t["preconditions"])}\n- Steps:\n' + "".join(f"  - {l}\n" for l in t["steps"].split("\n")) + f'- Expected Result: {c(t["expected"])}\n- Actual Result: {c(t["actual"])}\n- Status: {t["status"]}\n- Generated By: {t["generated_by"]}\n\n'
    return s
def g_changelog(d):
    if not d["changes"]: return f'# Changelog\n\n{head(d,"")}No verified changes were detected from the provided project versions.\n'
    s = f'# Changelog\n\n{head(d,"")}'
    for v in dict.fromkeys(ch["to_version"] for ch in d["changes"]):
        s += f"## Version {v}\n\n"
        for k, lbl in (("added", "Added"), ("modified", "Changed"), ("removed", "Removed")):
            it = [ch for ch in d["changes"] if ch["to_version"] == v and ch["change_type"] == k]
            if it: s += f"**{lbl}:**\n\n" + "\n".join(f'- {ch["item_type"]}: {ch["item"]}' for ch in it) + "\n\n"
    return s
def g_release(d):
    return g_changelog(d).replace("# Changelog", "# Release Notes", 1) if d["changes"] else f'# Release Notes\n\n{head(d,"")}No verified changes were detected from the provided project versions.\n'
def _sp(v): return [t.strip() for t in (v or "").split(",") if t.strip()]
def broken_links(d):
    known = {f["path"] for f in d["files"]}
    return [(p["file"], t) for p in d["pages"] for t in _sp(p["params"]) if t not in known]
def _match_api(d, url, method=None):
    u = re.sub(r"\{[^}]*\}", "{}", url.split("?")[0]).rstrip("/") or "/"
    for a in d["apis"]:
        ap = re.sub(r"[{<][^}>]*[}>]", "{}", a["path"]).rstrip("/") or "/"
        if (ap == u or u.endswith(ap)) and (not method or a["method"] == method): return a
    return None
def ui_overview(d):
    if not d["pages"]: return ""
    rows = [(p["name"], p["file"], p["doc"], sum(1 for f in d["forms"] if f["file"] == p["file"]), len(_sp(p["params"]))) for p in d["pages"]]
    return "\n## User Interface (detected pages)\n\n" + table(["Page", "File", "Description", "Forms", "Links to pages"], rows)
def ui_arch(d):
    if not d["pages"] and not d["calls"]: return ""
    o = "\n## Frontend Pages & Navigation\n\n"
    nav = [f'- {p["file"]} → {", ".join(_sp(p["params"]))}' for p in d["pages"] if p["params"]]
    o += ("\n".join(nav) if nav else NA) + "\n"
    bl = broken_links(d)
    if bl: o += "\n**Links to pages not found in the project:**\n\n" + "\n".join(f"- {a} → {b}" for a, b in bl) + "\n"
    comm = []
    for f in d["forms"]:
        if f["returns"] and f["returns"].split(" ", 1)[-1]:
            m, _, u = f["returns"].partition(" "); a = _match_api(d, u, m) if u else None
            comm.append((f'Form "{f["name"]}" ({f["file"]})', f["returns"], f'{a["method"]} {a["path"]} ({a["function"] or a["file"]})' if a else "No matching endpoint detected"))
    for cl in d["calls"]:
        a = _match_api(d, cl["name"], cl["returns"]); comm.append((f'Script call ({cl["file"]}:{cl["line_start"]})', f'{cl["returns"]} {cl["name"]}', f'{a["method"]} {a["path"]} ({a["function"] or a["file"]})' if a else "No matching endpoint detected"))
    if comm: o += "\n## Frontend → Backend Communication\n\n" + table(["Source", "Request", "Matched endpoint"], comm)
    return o
def ui_user(d):
    if not d["pages"]: return ""
    o = "\n## Pages and screens\n"
    for p in d["pages"]:
        o += f'\n### {p["name"]}\n\nFile: `{p["file"]}`' + (f'\n\n{p["doc"]}' if p["doc"] else "") + "\n"
        if p["params"]: o += "\nYou can navigate from here to: " + ", ".join(f"`{t}`" for t in _sp(p["params"])) + "\n"
        for f in [f for f in d["forms"] if f["file"] == p["file"]]:
            o += f'\n**Form: {f["name"]}**' + (f' (button: "{f["doc"]}")' if f["doc"] else "") + "\n\n" + ("\n".join(f'- {x_}' for x_ in _sp(f["params"])) or NA) + "\n\n(`*` = required field)\n"
    return o
_ov, _ar, _us, _dv = g_overview, g_arch, g_user, g_dev
def g_overview(d): return _ov(d) + ui_overview(d)
def g_arch(d): return _ar(d) + ui_arch(d)
def g_user(d): return _us(d) + ui_user(d)
def g_dev(d):
    o = _dv(d)
    if d["pages"]: o += "\n## Frontend Files\n\n" + table(["Page", "File", "Uses (scripts/styles)"], [(p["name"], p["file"], p["returns"]) for p in d["pages"]])
    if d["forms"] or d["calls"]: o += "\n## Frontend Functions & Calls\n\nSee Architecture Documentation → Frontend → Backend Communication.\n"
    return o
GEN = dict(overview=g_overview, srs=g_srs, api=g_api, database=g_db, architecture=g_arch, developer=g_dev, user=g_user, test_doc=g_testdoc, test_cases=md_tests, changelog=g_changelog, release_notes=g_release)
def g_report(d):
    s, cv, gp = stats(d), coverage(d), gaps(d); p = d["p"]
    cvt = table(["Area", "Documented", "Total", "Coverage (automated estimate)"], [(k.title(), v["done"], v["total"], f'{v["pct"]}%' if v["pct"] is not None else "N/A") for k, v in cv.items()])
    docs = q("select d.doc_type,d.title,(select count(*) from document_versions where document_id=d.id) n from documents d where project_id=? and doc_type!='report'", (p["id"],))
    rec = []
    if any(g["type"] == "Function" for g in gp): rec.append(f'Document the {sum(1 for g in gp if g["type"]=="Function")} undocumented function(s).')
    if any(g["type"] == "API" for g in gp): rec.append(f'Review {sum(1 for g in gp if g["type"]=="API")} API endpoint(s) with missing descriptions.')
    if any("test" in g["problem"].lower() for g in gp): rec.append("Create tests for requirements without test coverage.")
    if any(g["type"] == "Database" for g in gp): rec.append("Add descriptions for undocumented database tables.")
    return f'''# CodeScribe — Software Project Analysis Report

{head(d,"")}## 1. Project Information

{table(["Field","Value"],[("Project",p["name"]),("Version",p["version"]),("Description",p["description"]),("Created by",p["created_by"]),("Status",p["status"])])}
## 2. Executive Summary

The analyzed project "{p["name"]}" contains {s["files"]} supported files, {s["functions"]} detected functions/methods, {s["classes"]} classes, {s["apis"]} API endpoints, {s["tables"]} database tables and {s["requirements"]} requirements. {len(gp)} documentation gap(s) were detected.

## 3. Technology Stack

{c(p["tech"])}. Detected languages: {", ".join(f"{k} ({v})" for k,v in langs(d).items()) or NA}

## 4. Project Structure

{", ".join(sorted({f["path"].split("/")[0] for f in d["files"]})) or NA}

## 5. Project Statistics

{table(["Metric","Count"],list(s.items()))}
The analyzed project contains {s["functions"]} detected functions distributed across the supported source files.

## 6. Architecture Summary

{g_arch(d).split("## Architecture Summary")[1].split("## Frontend")[0].strip()}

## 7. Requirements Summary

{table(["ID","Title","Type","Priority","Status"],[(r["req_id"],r["title"],r["type"],r["priority"],r["status"]) for r in d["reqs"]]) if d["reqs"] else NA}
## 8. API Summary

{table(["Method","Path","Source"],[(a["method"],a["path"],a["file"]) for a in d["apis"]]) if d["apis"] else NA}
## 9. Database Summary

{table(["Table","Columns"],[(t["name"],len(t["cols"])) for t in d["tables"]]) if d["tables"] else NA}
## 10. Test Case Summary

{s["test_cases"]} test cases recorded ({sum(1 for t in d["tests"] if t["status"]=="Not Executed")} not executed); {s["test_files"]} test files detected.

## 11. Documentation Coverage

Coverage values are an automated estimate, not an exact quality score.

{cvt}
## 12. Documentation Gaps

{table(["Type","Item","Problem","Suggested Action"],[(g["type"],g["item"],g["problem"],g["action"]) for g in gp]) if gp else "No documentation gaps detected."}
## 13. Version Changes

{table(["Version","Change","Type","Item"],[(ch["to_version"],ch["change_type"],ch["item_type"],ch["item"]) for ch in d["changes"]]) if d["changes"] else "No verified changes were detected from the provided project versions."}
## 14. Generated Documents

{table(["Document","Versions"],[(x_["title"],x_["n"]) for x_ in docs]) if docs else "No documentation has been generated yet."}
## 15. Recommendations

{chr(10).join("- "+r for r in rec) or "No recommendations: no gaps detected."}

## 16. Conclusion

This report was generated from analyzed project data. Anything not present in the provided project is marked as: {NA}
'''

def parse_md(md):
    B, L, i = [], md.splitlines(), 0
    while i < len(L):
        l = L[i]
        if l.startswith("```"):
            i += 1; buf = []
            while i < len(L) and not L[i].startswith("```"): buf.append(L[i]); i += 1
            B.append(("code", "\n".join(buf)))
        elif l.startswith("#"): B.append(("h", len(l) - len(l.lstrip("#")), l.lstrip("# ").strip()))
        elif l.startswith("|"):
            rows = []
            while i < len(L) and L[i].startswith("|"):
                cells = [k.strip() for k in L[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", k) for k in cells): rows.append(cells)
                i += 1
            B.append(("table", rows)); continue
        elif re.match(r"\s*([-*]|\d+\.) ", l): B.append(("li", re.sub(r"^\s*([-*]|\d+\.) ", "", l)))
        elif l.strip(): B.append(("p", l.strip()))
        i += 1
    return B
def to_txt(md): return re.sub(r"[*`]", "", md)
def to_pdf(md, title, project, version):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Preformatted
    from xml.sax.saxutils import escape
    ss = getSampleStyleSheet(); body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=10, leading=14)
    cell = ParagraphStyle("c", parent=body, fontSize=8, leading=10)
    hs = {n: ParagraphStyle(f"h{n}", parent=ss["Heading2"], fontSize=20 - 3 * n, keepWithNext=1, spaceBefore=10, textColor=colors.HexColor("#3730a3")) for n in range(1, 7)}
    inl = lambda s: re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", escape(s))))
    W = A4[0] - 4 * cm
    st = [Paragraph("CODESCRIBE", ParagraphStyle("br", parent=ss["Title"], textColor=colors.HexColor("#6366f1"), fontSize=14, alignment=0)),
          Paragraph(escape(title), ParagraphStyle("t", parent=ss["Title"], alignment=0)), Paragraph(f"Project: {escape(project)} | Version: {escape(version)} | Generated: {today()}", body), Spacer(1, 12)]
    for b in parse_md(md):
        if b[0] == "h": st.append(Paragraph(inl(b[2]), hs[min(b[1], 6)]))
        elif b[0] == "p": st.append(Paragraph(inl(b[1]), body))
        elif b[0] == "li": st.append(Paragraph("• " + inl(b[1]), ParagraphStyle("l", parent=body, leftIndent=14)))
        elif b[0] == "code": st.append(Preformatted("\n".join(textwrap.fill(l, 90) for l in b[1].split("\n")), ParagraphStyle("cd", parent=ss["Code"], fontSize=8, backColor=colors.HexColor("#f1f5f9"))))
        elif b[0] == "table" and b[1]:
            n = max(len(r) for r in b[1]); rows = [[Paragraph(inl(k), cell) for k in r + [""] * (n - len(r))] for r in b[1]]
            t = Table(rows, colWidths=[W / n] * n, repeatRows=1)
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e0e7ff")), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            st += [t, Spacer(1, 8)]
    def deco(cv, doc):
        cv.setFont("Helvetica", 8); cv.drawString(2 * cm, 1.2 * cm, "CodeScribe — Turn Software Into Documentation."); cv.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
    buf = io.BytesIO(); SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm, title=title).build(st, onFirstPage=deco, onLaterPages=deco)
    return buf.getvalue()
def to_docx(md, title, project, version):
    import docx
    from docx.shared import Pt
    D = docx.Document(); D.add_heading("CodeScribe — " + title, 0)
    D.add_paragraph(f"Project: {project}\nVersion: {version}\nGenerated: {today()}")
    strip = lambda s: re.sub(r"[*`]", "", s)
    for b in parse_md(md):
        if b[0] == "h": D.add_heading(strip(b[2]), min(b[1], 4))
        elif b[0] == "p": D.add_paragraph(strip(b[1]))
        elif b[0] == "li": D.add_paragraph(strip(b[1]), style="List Bullet")
        elif b[0] == "code":
            r = D.add_paragraph().add_run(b[1]); r.font.name = "Courier New"; r.font.size = Pt(8)
        elif b[0] == "table" and b[1]:
            n = max(len(r) for r in b[1]); t = D.add_table(rows=len(b[1]), cols=n); t.style = "Table Grid"
            for i, row in enumerate(b[1]):
                for j, v in enumerate(row):
                    t.cell(i, j).text = strip(v)
                    if i == 0:
                        for r in t.cell(i, j).paragraphs[0].runs: r.bold = True
    buf = io.BytesIO(); D.save(buf); return buf.getvalue()

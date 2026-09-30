=====================================================================
CODESCRIBE — PROJECT SUMMARY / HANDOFF FILE  (README.txt)
Give this file to another AI to continue work. Date built: 30 Sep 2026
=====================================================================

WHAT IT IS
AI-assisted software documentation platform. Pipeline:
SOURCE DATA -> ANALYZER -> STRUCTURED DATA (SQLite) -> TEMPLATES (+optional AI) -> DOCS / TESTS / COVERAGE / GAPS / REPORT -> EXPORT
Rule everywhere: "SOURCE DATA FIRST". Undetected info is shown as:
"Information not detected in the provided project."  AI text is labelled [AI-generated].

STACK
Python + FastAPI backend, SQLite, plain HTML/CSS/JS frontend served by FastAPI.
Export: ReportLab (PDF), python-docx (DOCX), Markdown, TXT.  AI: ai_service.py (Gemini or Anthropic, rotating API keys: env AI_API_KEYS=k1,k2,k3).

RUN (Windows)
  python -m venv venv
  venv\Scripts\activate
  pip install -r requirements.txt
  copy .env.example .env        (optional: put AI_API_KEY there)
  python database.py            (optional; DB is auto-created on startup)
  python -m uvicorn main:app --reload
  App: http://127.0.0.1:8000    API docs: http://127.0.0.1:8000/docs
  Tests: python -m pytest -q test_app.py
DEMO: click "Load Demo Project" (Campus Connect: real sample files, analyzed by the real pipeline).

ENV VARS
  AI_API_KEYS (comma-separated, rotated on 429/quota/outage; single AI_API_KEY also works), AI_PROVIDER (gemini|anthropic), AI_RPM (per-key pacing, default 10), AI_MAX_WAIT (default 20s), AI_ENHANCE_LIMIT (default 25)
  (only feature needing external keys: AI summaries, SRS drafting, API description enhancement). GET /ai/status shows key pool health (last 4 chars only).
  AI_MODEL (default gemini-2.5-flash), AI_API_URL (default Anthropic messages endpoint), MAX_UPLOAD_MB (default 20), DB_PATH (optional)

FILES
  main.py        FastAPI routes, upload/ZIP handling, analysis runner, compare, search, dashboard, demo loader, exports wiring
  database.py    SQLite schema (14 tables), q()/x()/xm() helpers, init_db()
  analyzer.py    Safe ZIP reader (path traversal blocked, size limits, secret files skipped), parsers:
                 Python (ast: functions, methods, classes, FastAPI/Flask routes), JS/TS (regex: functions, classes, Express routes),
                 SQL (CREATE TABLE: columns, types, nullable, PK, FK, preceding "-- comment" as description),
                 requirements.txt / package.json dependencies. HTML/HTM (stdlib html.parser): pages (title, description, links to other pages, script/style refs), forms + input fields, inline-script functions, fetch()/axios calls (stored as code_entities kinds page/form/call). Java/C#/PHP/Dart/C++: classes only.
  docs.py        Data loading, stats, coverage %, gap analysis, keyword traceability, test-case generation,
                 markdown generators for 11 doc types + project report, PDF/DOCX/TXT exporters
  ai_service.py  Optional LLM calls with key rotation + cooldowns (tests: test_ai_rotation.py, mocked), secret redaction, never raises (returns None when unavailable)
  demo.py        Campus Connect sample project data
  test_app.py    pytest end-to-end test (passes)
  static/index.html, style.css, script.js   SPA (dark/light/system theme saved in localStorage)

MAIN ENDPOINTS
  GET /health, /settings, /dashboard, /search?term=
  POST/GET /projects ; GET/PUT/DELETE /projects/{id}
  POST /projects/{id}/upload (multi-file or ZIP) ; POST /projects/{id}/analyze ; GET /projects/{id}/analysis
  GET /projects/{id}/apis|database ; POST /projects/{id}/ai/enhance
  requirements: POST/GET /projects/{id}/requirements, PUT/DELETE /requirements/{id}, POST /projects/{id}/requirements/extract
  tickets: POST/GET /projects/{id}/tickets, DELETE /tickets/{id}
  tests: POST /projects/{id}/test-cases/generate, GET /projects/{id}/test-cases, PUT/DELETE /test-cases/{id}, GET /projects/{id}/test-cases/export?fmt=pdf|docx|md|txt
  docs: POST /projects/{id}/generate-documentation {types:[...],use_ai}, GET /projects/{id}/documents, GET/PUT /documents/{id}, GET /documents/{id}/export?fmt=&version=
  report: POST /projects/{id}/generate-report, GET /projects/{id}/report
  history/compare: GET /projects/{id}/history, POST /projects/{id}/compare (ZIP + version form fields)
  POST /demo/load
Doc types: overview, srs, api, database, architecture, developer, user, test_doc, test_cases, changelog, release_notes.

IMPLEMENTED AND TESTED (pytest + smoke run)
  Duplicate project name rejected in DB (UNIQUE COLLATE NOCASE) and API (409 with required message); empty/long names rejected
  ZIP + single file upload, malformed ZIP rejection, size limit, whitelist, .env/.git/keys skipped
  Code/API/SQL analysis, requirements + tickets CRUD, requirement extraction from md/txt/docx/pdf text (review-then-add)
  Test-case generation (template based, "Not executed" by default), edit/status/delete, export PDF/DOCX/MD/TXT
  Documentation generation (11 types), versioned documents (edits create new versions, old kept), viewer/editor/copy
  Coverage (automated estimate), gaps (clickable to relevant screen), keyword traceability, 16-section report + exports
  Version comparison (files/APIs/functions/tables), stored changes, changelog/release notes, affected-doc hints
  Global search, dashboard from real DB values, empty states, toasts, dark/light/system theme, responsive layout

KNOWN LIMITATIONS / TODO (be honest with users)
  1. AI features are UNVERIFIED without a real AI_API_KEY (code path written; not exercised against the live API). Without a key, app works with templates.
  2. Progress stepper on Upload is real for upload bytes; analysis stages are shown as steps around synchronous requests (no background job queue).
  3. Analyzer depth: Python is AST-accurate; JS/TS is regex; HTML is parsed structurally but CSS content is not analysed (files are only listed); pages built entirely by JavaScript at runtime are not seen; Java/C#/PHP/Dart/C++ detect classes only; nested Python functions are ignored.
     Response fields, status codes, auth (only FastAPI Depends detected) are mostly "not detected".
  4. Traceability is keyword-prefix matching, not semantic; may miss or over-match.
  5. Compare covers files, APIs, functions, tables — NOT requirements add/remove/modify. Comparing replaces stored files with the new version.
  6. No login/users (users table exists but unused); created_by is "Local User".
  7. PDF has branding, page numbers, tables, headings kept with content — but NO table of contents. No architecture diagram image (text layer diagram only).
  8. Only 1 pytest end-to-end test; the requested full test list is not individually covered (upload size limit, AI-unavailable path only exercised indirectly).
  9. Frontend visually untested in a real browser (JS syntax-checked, endpoints smoke-tested). Expect small UI polish fixes.
  10. Docs "Actual result" for tests is manual; no test execution engine.
  11. Uses deprecated FastAPI on_event (works; can migrate to lifespan).
  Ideas: background jobs, TOC in PDF, requirement diffs, more language parsers, auth, semantic traceability with AI.

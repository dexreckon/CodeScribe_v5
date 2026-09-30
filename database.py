import sqlite3, os
SERVERLESS = bool(os.getenv("VERCEL"))   # Vercel: filesystem is read-only except /tmp
DB = os.getenv("DB_PATH", "/tmp/codescribe.db" if SERVERLESS else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "codescribe.db"))
P = "project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE"
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, description TEXT DEFAULT '', tech TEXT DEFAULT '', version TEXT DEFAULT '1.0.0', created_by TEXT DEFAULT 'Local User', status TEXT DEFAULT 'Not analyzed', created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS project_files(id INTEGER PRIMARY KEY, {P}, path TEXT NOT NULL, ext TEXT, size INTEGER, lang TEXT, content TEXT, hash TEXT, UNIQUE(project_id,path));
CREATE TABLE IF NOT EXISTS requirements(id INTEGER PRIMARY KEY, {P}, req_id TEXT, title TEXT, description TEXT DEFAULT '', type TEXT DEFAULT 'Functional', priority TEXT DEFAULT 'Medium', status TEXT DEFAULT 'Proposed', source TEXT DEFAULT 'Manual entry');
CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY, {P}, ticket_id TEXT, title TEXT, description TEXT DEFAULT '', type TEXT DEFAULT 'Task', priority TEXT DEFAULT 'Medium', status TEXT DEFAULT 'Open', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS code_entities(id INTEGER PRIMARY KEY, {P}, kind TEXT, name TEXT, file TEXT, params TEXT DEFAULT '', returns TEXT DEFAULT '', doc TEXT DEFAULT '', line_start INTEGER, line_end INTEGER);
CREATE TABLE IF NOT EXISTS api_endpoints(id INTEGER PRIMARY KEY, {P}, method TEXT, path TEXT, description TEXT DEFAULT '', ai_description TEXT DEFAULT '', path_params TEXT DEFAULT '', query_params TEXT DEFAULT '', body_fields TEXT DEFAULT '', auth TEXT DEFAULT '', file TEXT, function TEXT);
CREATE TABLE IF NOT EXISTS database_tables(id INTEGER PRIMARY KEY, {P}, name TEXT, file TEXT, description TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS database_columns(id INTEGER PRIMARY KEY, table_id INTEGER NOT NULL REFERENCES database_tables(id) ON DELETE CASCADE, name TEXT, dtype TEXT, nullable INTEGER DEFAULT 1, pk INTEGER DEFAULT 0, fk TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS test_cases(id INTEGER PRIMARY KEY, {P}, tc_id TEXT, req_id TEXT DEFAULT '', title TEXT, objective TEXT, preconditions TEXT, steps TEXT, expected TEXT, actual TEXT DEFAULT 'Not executed.', status TEXT DEFAULT 'Not Executed', generated_by TEXT DEFAULT 'template');
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, {P}, doc_type TEXT, title TEXT, UNIQUE(project_id,doc_type));
CREATE TABLE IF NOT EXISTS document_versions(id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE, version TEXT, content TEXT, note TEXT, project_version TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS project_analysis(id INTEGER PRIMARY KEY, {P}, stats TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS project_changes(id INTEGER PRIMARY KEY, {P}, from_version TEXT, to_version TEXT, change_type TEXT, item_type TEXT, item TEXT, detail TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_files ON project_files(project_id); CREATE INDEX IF NOT EXISTS ix_ent ON code_entities(project_id);
CREATE INDEX IF NOT EXISTS ix_api ON api_endpoints(project_id); CREATE INDEX IF NOT EXISTS ix_req ON requirements(project_id);
"""
def conn():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c
def init_db():
    c = conn(); c.executescript(SCHEMA); c.commit(); c.close()
def q(sql, a=(), one=False):
    c = conn(); r = [dict(i) for i in c.execute(sql, a).fetchall()]; c.close()
    return (r[0] if r else None) if one else r
def x(sql, a=()):
    c = conn(); cur = c.execute(sql, a); c.commit(); i = cur.lastrowid; c.close(); return i
def xm(sql, rows):
    c = conn(); c.executemany(sql, rows); c.commit(); c.close()
if __name__ == "__main__":
    init_db(); print("Database ready:", DB)

"""Agent execution and observation tables co-located with one namespace's facts."""
UPGRADE = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY, title TEXT NOT NULL, actor TEXT NOT NULL,
    parent_run_id TEXT REFERENCES agent_runs, status TEXT NOT NULL DEFAULT 'open',
    started_at TEXT NOT NULL, updated_at TEXT NOT NULL, summary TEXT
);
CREATE INDEX IF NOT EXISTS runs_recent ON agent_runs(updated_at DESC);
CREATE TABLE IF NOT EXISTS traces (
    trace_id TEXT PRIMARY KEY, run_id TEXT REFERENCES agent_runs ON DELETE CASCADE,
    op TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL, latency_ms REAL NOT NULL,
    input TEXT NOT NULL, output TEXT NOT NULL, diagnostics TEXT NOT NULL, write INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS traces_by_run ON traces(run_id, at);
CREATE INDEX IF NOT EXISTS traces_by_date ON traces(at DESC);
CREATE TABLE IF NOT EXISTS run_refs (
    source_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES agent_runs ON DELETE CASCADE,
    PRIMARY KEY(source_id,run_id)
);
CREATE TABLE IF NOT EXISTS trace_refs (
    source_id TEXT NOT NULL, trace_id TEXT NOT NULL REFERENCES traces ON DELETE CASCADE,
    PRIMARY KEY(source_id,trace_id)
);
CREATE TABLE IF NOT EXISTS agent_requests (
    actor TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
    trace_id TEXT REFERENCES traces ON DELETE SET NULL, PRIMARY KEY(actor,request_id)
);
CREATE TABLE IF NOT EXISTS lesson_queue (
    lesson_id TEXT PRIMARY KEY, type TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
    decision_at TEXT, decision_by TEXT, write_id TEXT, UNIQUE(type,key,value)
);
CREATE TABLE IF NOT EXISTS lesson_support (
    lesson_id TEXT NOT NULL REFERENCES lesson_queue ON DELETE CASCADE,
    trace_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES agent_runs ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES documents(request_id) ON DELETE CASCADE,
    actor TEXT NOT NULL, success INTEGER NOT NULL, trusted INTEGER NOT NULL,
    PRIMARY KEY(lesson_id,trace_id)
);
CREATE INDEX IF NOT EXISTS lesson_sources ON lesson_support(source_id);
CREATE TABLE IF NOT EXISTS run_policy (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), retention_days INTEGER,
    auto_types TEXT NOT NULL DEFAULT '[]'
);
INSERT OR IGNORE INTO run_policy(singleton) VALUES(1);
PRAGMA user_version=3;
COMMIT;
"""

UPGRADE_V3 = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS run_refs (
    source_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES agent_runs ON DELETE CASCADE,
    PRIMARY KEY(source_id,run_id)
);
INSERT OR IGNORE INTO run_refs SELECT r.source_id,t.run_id FROM trace_refs r JOIN traces t USING(trace_id)
    WHERE t.run_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS lesson_derivations (
    lesson_id TEXT NOT NULL REFERENCES lesson_queue ON DELETE CASCADE,
    write_id TEXT NOT NULL REFERENCES documents(request_id) ON DELETE CASCADE,
    PRIMARY KEY(lesson_id,write_id)
);
INSERT OR IGNORE INTO lesson_derivations SELECT l.lesson_id,l.write_id FROM lesson_queue l
    JOIN documents d ON d.request_id=l.write_id;
PRAGMA user_version=4;
COMMIT;
"""

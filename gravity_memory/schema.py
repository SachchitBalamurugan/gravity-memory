GITHUB_SCHEMA = """
CREATE TABLE IF NOT EXISTS github_files (
    id TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    path TEXT NOT NULL,
    ref TEXT NOT NULL,
    blob_sha TEXT NOT NULL,
    request_id TEXT NOT NULL REFERENCES documents(request_id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    last_synced_at TEXT NOT NULL,
    version TEXT NOT NULL
);
"""

UPGRADE_V1 = "BEGIN IMMEDIATE;\n" + GITHUB_SCHEMA + "PRAGMA user_version=2;\nCOMMIT;"

SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    fingerprint TEXT NOT NULL,
    payload TEXT NOT NULL,
    source TEXT NOT NULL,
    body TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    doc INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT,
    start INTEGER NOT NULL,
    stop INTEGER NOT NULL,
    effective_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS review_origin ON documents(json_extract(payload,'$.origin'));
CREATE INDEX IF NOT EXISTS fact_timeline ON facts(key, effective_at DESC, doc DESC);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    doc INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    start INTEGER NOT NULL,
    stop INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS chunk_doc ON chunks(doc);
CREATE INDEX IF NOT EXISTS fact_doc ON facts(doc);
CREATE TABLE IF NOT EXISTS vectors (
    chunk INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY(model, chunk)
);
CREATE VIRTUAL TABLE IF NOT EXISTS fact_search USING fts5(key, value, tokenize='porter unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS passage_search USING fts5(text, tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS delete_fact AFTER DELETE ON facts BEGIN
    DELETE FROM fact_search WHERE rowid=old.id;
END;
CREATE TRIGGER IF NOT EXISTS delete_chunk AFTER DELETE ON chunks BEGIN
    DELETE FROM passage_search WHERE rowid=old.id;
END;
""" + GITHUB_SCHEMA + """
PRAGMA user_version=2;
COMMIT;
"""

"""Namespace-owned private connectors. Network calls precede atomic reconciliation."""
import hashlib
import inspect
import re
import time
import uuid

from .connector_providers import AccessLost, PROVIDERS, SyncError, Transport, load_credential, target_id
from .store import Conflict, string, utc


TEXT = {"type": "string"}
CONNECTOR_OPS = {
    "connector_list": ("List sources published into this namespace.", [], {}),
    "connector_add": ("Register an approved provider selection and server credential alias.",
                      ["provider", "name", "target", "credential"],
                      {"provider": {"enum": ["github", "notion", "drive"]}, "name": TEXT,
                       "target": TEXT, "credential": TEXT, "interval_minutes": {"type": "integer"}}),
    "connector_sync": ("Pull changed source content and remove content outside the selection.", ["id"], {"id": TEXT}),
    "connector_remove": ("Disconnect the provider and erase its published content.", ["id"], {"id": TEXT}),
}
COLUMNS = ("c.id,c.provider,c.name,c.target,c.credential,c.status,c.cursor,c.last_synced_at,"
           "c.last_error,c.interval_minutes,c.skipped")
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS team_connectors(
       id TEXT PRIMARY KEY,provider TEXT NOT NULL,name TEXT NOT NULL,target TEXT NOT NULL,
       credential TEXT NOT NULL,interval_minutes INTEGER NOT NULL,status TEXT NOT NULL,
       cursor TEXT NOT NULL DEFAULT '',last_synced_at TEXT,last_error TEXT,
       next_due REAL NOT NULL DEFAULT 0,lease TEXT,lease_until REAL NOT NULL DEFAULT 0,
       skipped INTEGER NOT NULL DEFAULT 0, UNIQUE(provider,target))""",
    """CREATE TABLE IF NOT EXISTS connector_items(
       connection_id TEXT NOT NULL REFERENCES team_connectors(id) ON DELETE CASCADE,
       item TEXT NOT NULL,revision TEXT NOT NULL,source TEXT NOT NULL,
       request_id TEXT UNIQUE REFERENCES documents(request_id) ON DELETE SET NULL,
       PRIMARY KEY(connection_id,item))""",
    """CREATE TRIGGER IF NOT EXISTS connector_source_erased AFTER UPDATE OF request_id ON connector_items
       WHEN NEW.request_id IS NULL BEGIN
       UPDATE connector_items SET revision='',source='' WHERE connection_id=NEW.connection_id AND item=NEW.item; END""",
    """CREATE TABLE IF NOT EXISTS connector_ancestry(
       connection_id TEXT NOT NULL,item TEXT NOT NULL,ancestor TEXT NOT NULL,
       PRIMARY KEY(connection_id,ancestor,item),
       FOREIGN KEY(connection_id,item) REFERENCES connector_items(connection_id,item) ON DELETE CASCADE)""",
)


def setup(ledger):
    with ledger._tx() as db:
        if db.execute("SELECT count(*) FROM sqlite_master WHERE name='connector_ancestry'").fetchone()[0]:
            return
    with ledger._tx(write=True) as db:
        for statement in SCHEMA:
            db.execute(statement)


def _connection(db, ident):
    row = db.execute(f"""SELECT {COLUMNS},(SELECT count(*) FROM connector_items i
                      WHERE i.connection_id=c.id AND i.request_id IS NOT NULL) AS documents
                      FROM team_connectors c WHERE c.id=?""", (ident,)).fetchone()
    if not row:
        raise ValueError("Unknown connector in this namespace.")
    return dict(row)


def list_connections(ledger):
    setup(ledger)
    with ledger._tx() as db:
        return {"items": [_connection(db, row[0]) for row in db.execute(
            "SELECT id FROM team_connectors ORDER BY name,id")]}


def add(ledger, provider, name, target, credential, interval_minutes=60):
    target = target_id(provider, target)
    name = string(name, "name", 100).strip()
    if not isinstance(credential, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", credential):
        raise ValueError("Enter a configured credential alias.")
    if type(interval_minutes) is not int or not 1 <= interval_minutes <= 10_080:
        raise ValueError("Choose a sync interval between 1 and 10080 minutes.")
    load_credential(credential, provider, getattr(ledger, "connector_namespace", "personal"), target)
    setup(ledger)
    with ledger._tx(write=True) as db:
        existing = db.execute("SELECT id FROM team_connectors WHERE provider=? AND target=?", (provider, target)).fetchone()
        if existing:
            item = _connection(db, existing[0])
            if (item["name"], item["credential"], item["interval_minutes"]) != (name, credential, interval_minutes):
                raise Conflict("A connector already publishes this selection.")
            return item
        ident = uuid.uuid4().hex
        db.execute("""INSERT INTO team_connectors(id,provider,name,target,credential,interval_minutes,status)
                   VALUES(?,?,?,?,?,?,'pending')""", (ident, provider, name, target, credential, interval_minutes))
        return _connection(db, ident)


def _erase(db, request):
    from .runs import erase_source_tx
    if request:
        erase_source_tx(db, request)


def remove(ledger, id):
    string(id, "id", 64)
    setup(ledger)
    with ledger._tx(write=True) as db:
        requests = [r[0] for r in db.execute(
            "SELECT request_id FROM connector_items WHERE connection_id=? AND request_id IS NOT NULL", (id,))]
        # Remove membership first so normal disconnection does not create individual erase tombstones.
        db.execute("DELETE FROM team_connectors WHERE id=?", (id,))
        for request in requests:
            _erase(db, request)
        return {"id": id, "removed": len(requests)}


def _digest(item):
    return hashlib.sha256(item.encode()).hexdigest()


class Revisions:
    def __init__(self, rows):
        self.by_hash = {r["item"]: r for r in rows}

    def get(self, item, default=None):
        row = self.by_hash.get(_digest(item))
        return (row["revision"], row["source"]) if row and row["request_id"] else default

    def suppressed(self, item):
        row = self.by_hash.get(_digest(item))
        return row is not None and row["request_id"] is None


def _own_lease(db, ident, lease):
    row = db.execute("SELECT lease FROM team_connectors WHERE id=?", (ident,)).fetchone()
    if not row or row[0] != lease:
        raise Conflict("The connector changed while the provider request was in flight. Refresh and retry.")


def _ancestry(db, ident, item, parents):
    db.execute("DELETE FROM connector_ancestry WHERE connection_id=? AND item=?", (ident, item))
    db.executemany("INSERT OR IGNORE INTO connector_ancestry VALUES(?,?,?)",
                   [(ident, item, _digest(parent)) for parent in parents])


def _apply(ledger, config, lease, result, *, revoked=False, withdrawn=0):
    ident = config["id"]
    checked = utc()
    counts = {"added": 0, "updated": 0, "removed": withdrawn, "unchanged": 0}
    with ledger._tx(write=True) as db:
        _own_lease(db, ident, lease)
        # Re-read rows here. A human may have erased a source during the network read.
        existing = {r["item"]: r for r in db.execute("SELECT * FROM connector_items WHERE connection_id=?", (ident,))}
        for remote_id, (revision, source, text) in (result.items.items() if result else ()):
            item = _digest(remote_id)
            old = existing.pop(item, None)
            if old and old["request_id"] is None:
                continue
            if text is None or (old and (revision, source) == (old["revision"], old["source"])):
                if not old:
                    raise Conflict("Connector membership changed while syncing. Retry.")
                counts["unchanged"] += 1
                _ancestry(db, ident, item, result.ancestors.get(remote_id, ()))
                continue
            if old:
                db.execute("DELETE FROM connector_items WHERE connection_id=? AND item=?", (ident, item))
                _erase(db, old["request_id"])
            request_id = f"connector:{ident}:{item}:{_digest(revision + source)[:24]}"
            ledger._remember(db, request_id, text, source, "document", None, [], checked,
                             metadata={"provider": config["provider"], "connector": ident})
            db.execute("INSERT INTO connector_items VALUES(?,?,?,?,?)", (ident, item, revision, source, request_id))
            _ancestry(db, ident, item, result.ancestors.get(remote_id, ()))
            counts["updated" if old else "added"] += 1
        for item, old in existing.items():
            if old["request_id"] is None:
                continue  # Keep an explicitly erased resource suppressed on future indexes.
            db.execute("DELETE FROM connector_items WHERE connection_id=? AND item=?", (ident, item))
            _erase(db, old["request_id"])
            counts["removed"] += 1
        status = "revoked" if revoked else "synced"
        db.execute("""UPDATE team_connectors SET status=?,cursor=?,last_error=?,last_synced_at=?,
                   next_due=?,skipped=?,lease=NULL,lease_until=0 WHERE id=?""",
                   (status, result.cursor if result else "", "Provider or credential access was removed." if revoked else None,
                    checked, time.time() + config["interval_minutes"] * 60, result.skipped if result else 0, ident))
    return {"id": ident, "status": status, **counts, "skipped": result.skipped if result else 0}


def sync(ledger, id):
    string(id, "id", 64)
    setup(ledger)
    lease = uuid.uuid4().hex
    with ledger._tx(write=True) as db:
        config = _connection(db, id)
        # Superseded workers may finish a network call but cannot commit after this lease changes.
        now = time.time()
        updated = db.execute("""UPDATE team_connectors SET lease=?,lease_until=?,status='syncing'
                                WHERE id=? AND (lease IS NULL OR lease_until<=?)""",
                             (lease, now + 900, id, now))
        if not updated.rowcount:
            raise Conflict("This connector is already syncing.")
        previous = Revisions(db.execute("SELECT * FROM connector_items WHERE connection_id=?", (id,)))

    withdrawn = 0
    next_heartbeat = time.monotonic() + 60

    def heartbeat():
        nonlocal next_heartbeat
        if time.monotonic() >= next_heartbeat:
            with ledger._tx(write=True) as db:
                _own_lease(db, id, lease)
                db.execute("UPDATE team_connectors SET lease_until=? WHERE id=?", (time.time() + 900, id))
            next_heartbeat = time.monotonic() + 60

    def withdraw(remote_id):
        # A denied directory must disappear even if an unrelated page fails later in the same pass.
        nonlocal withdrawn
        item = _digest(remote_id)
        with ledger._tx(write=True) as db:
            _own_lease(db, id, lease)
            rows = db.execute("""SELECT item,request_id FROM connector_items WHERE connection_id=?
                AND request_id IS NOT NULL AND (item=? OR item IN (SELECT item FROM connector_ancestry
                WHERE connection_id=? AND ancestor=?))""", (id, item, id, item)).fetchall()
            for row in rows:
                db.execute("DELETE FROM connector_items WHERE connection_id=? AND item=?", (id, row["item"]))
                _erase(db, row["request_id"])
            withdrawn += len(rows)
    try:
        credential = load_credential(config["credential"], config["provider"],
                                     getattr(ledger, "connector_namespace", "personal"), config["target"])
        net = Transport(heartbeat, withdraw)
        result = PROVIDERS[config["provider"]](config["target"], credential, previous, config["cursor"], net)
        return _apply(ledger, config, lease, result, withdrawn=withdrawn)
    except AccessLost:
        return _apply(ledger, config, lease, None, revoked=True, withdrawn=withdrawn)
    except Exception as exc:
        # Only authored provider messages may cross this boundary. Provider payloads may contain private data.
        detail = str(exc) if isinstance(exc, (SyncError, Conflict)) else "Sync did not finish. The last complete selection is preserved."
        with ledger._tx(write=True) as db:
            db.execute("""UPDATE team_connectors SET status='error',last_error=?,next_due=?,
                          lease=NULL,lease_until=0 WHERE id=? AND lease=?""",
                       (detail, time.time() + min(300, config["interval_minutes"] * 60), id, lease))
        raise SyncError(detail) from None


def run_due(ledger):
    setup(ledger)
    now = time.time()
    with ledger._tx() as db:
        ids = [r[0] for r in db.execute("""SELECT id FROM team_connectors WHERE next_due<=?
              AND (lease IS NULL OR lease_until<=?) ORDER BY next_due,id LIMIT 8""", (now, now))]
    results = []
    for ident in ids:
        try:
            results.append(sync(ledger, ident))
        except ValueError:
            results.append({"id": ident, "status": "retry"})
    return results


def call(ledger, op, args):
    method = {"connector_list": list_connections, "connector_add": add, "connector_sync": sync,
              "connector_remove": remove}.get(op)
    if not method or not isinstance(args, dict):
        raise ValueError("Unknown connector operation or invalid arguments.")
    try:
        inspect.signature(method).bind(ledger, **args)
    except TypeError as exc:
        raise ValueError(str(exc)) from None
    return method(ledger, **args)

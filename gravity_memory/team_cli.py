"""Self-hosted team lifecycle and portable offline SQLite backups."""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading

from .team_registry import Registry


def options(commands):
    team = commands.add_parser("team", help="manage a shared Gravity Memory service")
    actions = team.add_subparsers(dest="action", required=True)
    serve = actions.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--origin", default=os.getenv("GRAVITY_PUBLIC_ORIGIN"))
    backup = actions.add_parser("backup", help="back up a stopped server to a new directory")
    backup.add_argument("--to", type=Path, required=True)
    restore = actions.add_parser("restore", help="restore a backup into a new directory")
    restore.add_argument("--from", dest="source", type=Path, required=True)
    migrate = actions.add_parser("migrate", help="copy a local database to a new private namespace")
    migrate.add_argument("--source", type=Path, required=True)
    migrate.add_argument("--namespace", default="personal-import")
    for sub in (serve, backup, restore, migrate):
        sub.add_argument("--directory", type=Path, default=os.getenv("GRAVITY_TEAM_DIR", ".gravity-team"))


@contextmanager
def exclusive(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / ".service.lock").open("a+b") as handle:
        if not handle.tell():
            handle.write(b"1")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("stop the team service before this maintenance operation") from exc
        yield


def checksum(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def database_files(root):
    with closing(sqlite3.connect((root / "registry.sqlite").as_uri()+"?mode=ro", uri=True)) as db:
        files = [row[0] for row in db.execute("SELECT file FROM spaces ORDER BY file")]
    if any(not isinstance(f, str) or not re.fullmatch(r"[0-9a-f]{32}\.sqlite", f) for f in files):
        raise ValueError("invalid namespace database name")
    return ["registry.sqlite", *files]


def transfer(source, dest, restore=False):
    source, dest = source.resolve(strict=True), dest.resolve()
    if dest.exists() or source == dest or source in dest.parents:
        raise ValueError("select a new destination outside the source directory")
    dest.parent.mkdir(parents=True, exist_ok=True)
    names = database_files(source)
    manifest = {}
    if restore:
        expected = json.loads((source / "backup.json").read_text("utf-8"))
        if expected.get("version") != 1 or set(expected["files"]) != set(names):
            raise ValueError("backup manifest does not match registry")
    with tempfile.TemporaryDirectory(dir=dest.parent) as temp:
        stage = Path(temp) / "data"
        stage.mkdir(mode=0o700)
        for name in names:
            original, target = source / name, stage / name
            if restore:
                shutil.copyfile(original, target)
                if checksum(target) != expected["files"][name]:
                    raise ValueError("backup checksum mismatch: " + name)
            else:
                with closing(sqlite3.connect(original.as_uri()+"?mode=ro", uri=True)) as old, closing(sqlite3.connect(target)) as new:
                    old.backup(new)
            with closing(sqlite3.connect(target)) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("SQLite integrity check failed: " + name)
                if restore and name == "registry.sqlite":
                    db.execute("DELETE FROM sessions")
                    db.commit()
            manifest[name] = checksum(target)
        if not restore:
            (stage / "backup.json").write_text(json.dumps({"version": 1, "files": manifest}, indent=2), "utf-8")
        stage.rename(dest)
    return {"directory": str(dest), "databases": len(names)}


def execute(args):
    if args.action == "restore":
        return transfer(args.source, args.directory, restore=True)
    with exclusive(args.directory):
        if args.action == "backup":
            return transfer(args.directory, args.to)
        registry = Registry(args.directory)
        with registry.db() as db:
            initialized = db.execute("SELECT 1 FROM users WHERE owner=1").fetchone()
        if not initialized:
            password_file = os.getenv("GRAVITY_OWNER_PASSWORD_FILE")
            password = Path(password_file).read_text("utf-8").strip() if password_file else os.getenv("GRAVITY_OWNER_PASSWORD")
            registry.initialize(os.getenv("GRAVITY_OWNER", "owner"), password)
        if args.action == "migrate":
            return registry.create_space(args.namespace, args.namespace, args.source)
        from .team_server import run_worker, team_server
        stop = threading.Event()
        with team_server(registry, args.host, args.port, args.origin) as server:
            address = args.origin or f"http://{args.host}:{server.server_port}"
            print(address, flush=True)
            worker = run_worker(registry, stop)
            try:
                server.serve_forever()
            finally:
                stop.set()
                worker.join(timeout=5)
    return {"stopped": True}

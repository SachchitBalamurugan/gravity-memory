"""Accounts, namespace grants and revocable credentials for one self-hosted team."""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path

from .store import Conflict, Ledger

SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+-]{0,126}\Z")
ROLES = {"viewer": {"read"}, "member": {"read", "write", "feedback"},
         "admin": {"read", "write", "feedback", "manage"}, "owner": {"read", "write", "feedback", "manage"}}
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY COLLATE NOCASE,salt BLOB NOT NULL,pw BLOB NOT NULL,owner INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS spaces(id TEXT PRIMARY KEY,name TEXT NOT NULL,file TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS grants(username TEXT REFERENCES users(name),space TEXT REFERENCES spaces(id),role TEXT NOT NULL,PRIMARY KEY(username,space));
CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY,username TEXT REFERENCES users(name),expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS keys(id TEXT PRIMARY KEY,digest TEXT UNIQUE NOT NULL,space TEXT REFERENCES spaces(id),name TEXT NOT NULL,permissions TEXT NOT NULL,trusted INTEGER NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS attempts(name TEXT PRIMARY KEY,failures INTEGER NOT NULL,since INTEGER NOT NULL);
"""


class Denied(Exception):
    pass


class Unauthorized(Exception):
    pass


def digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def derive(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)


def identity(value):
    if not isinstance(value, str) or not USERNAME.fullmatch(value):
        raise ValueError("enter a username or email up to 127 characters")
    return value.lower()


def label(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
        raise ValueError("name must be between 1 and 100 characters")
    return value.strip()


class Registry:
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "registry.sqlite"
        self.stores = {}
        self.lock = threading.Lock()
        with self.db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path, timeout=20)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            with db:
                yield db

    def initialize(self, username, password):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users WHERE owner=1").fetchone():
                return
            self._user(db, username, password, True)
            self._space(db, "personal", "Personal", None)

    @staticmethod
    def _user(db, user, password, owner=False):
        user = identity(user)
        if not isinstance(password, str) or not 12 <= len(password) <= 512:
            raise ValueError("a new account needs a password of 12 to 512 characters")
        salt = secrets.token_bytes(16)
        try:
            db.execute("INSERT INTO users VALUES(?,?,?,?)", (user, salt, derive(password, salt), owner))
        except sqlite3.IntegrityError as exc:
            raise Conflict("username already exists") from exc
        return {"user": user}

    def _space(self, db, id, name, source):
        if not isinstance(id, str) or not SLUG.fullmatch(id):
            raise ValueError("namespace ID must use lowercase letters, numbers, hyphens or underscores")
        name = label(name)
        if db.execute("SELECT 1 FROM spaces WHERE id=?", (id,)).fetchone():
            raise Conflict("namespace ID already exists")
        filename = secrets.token_hex(16) + ".sqlite"
        path = self.root / filename
        try:
            if source is not None:
                original = Path(source).resolve(strict=True)
                with closing(sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)) as old, closing(sqlite3.connect(path)) as dest:
                    if not old.execute("SELECT 1 FROM sqlite_master WHERE name='documents'").fetchone():
                        raise ValueError("source is not a Gravity Memory database")
                    old.backup(dest)
            Ledger(path)
            db.execute("INSERT INTO spaces VALUES(?,?,?)", (id, name, filename))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {"id": id, "name": name}

    def create_space(self, id, name, source=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._space(db, id, name, source)

    def ledger(self, namespace):
        with self.lock:
            if namespace not in self.stores:
                with self.db() as db:
                    row = db.execute("SELECT file FROM spaces WHERE id=?", (namespace,)).fetchone()
                if not row:
                    raise Denied("namespace unavailable")
                ledger = Ledger(self.root / row["file"])
                ledger.connector_namespace = namespace
                self.stores[namespace] = ledger
            return self.stores[namespace]

    def login(self, user, password):
        user = identity(user)
        if not isinstance(password, str) or len(password) > 512:
            raise Unauthorized("invalid credentials")
        now = int(time.time())
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE since<?", (now-300,))
            blocked = db.execute("SELECT failures FROM attempts WHERE name=?", (user,)).fetchone()
            if blocked and blocked[0] >= 10:
                raise Unauthorized("too many attempts; try again in five minutes")
            row = db.execute("SELECT * FROM users WHERE name=?", (user,)).fetchone()
            got = derive(password, row["salt"] if row else bytes(16))
            if row and hmac.compare_digest(got, row["pw"]):
                token = secrets.token_urlsafe(40)
                db.execute("DELETE FROM attempts WHERE name=?", (user,))
                db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
                db.execute("INSERT INTO sessions VALUES(?,?,?)", (digest(token), user, now+43200))
                return token
            db.execute("INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(name) DO UPDATE SET failures=failures+1", (user, now))
        raise Unauthorized("invalid credentials")

    def change_password(self, who, current_password, new_password):
        if not isinstance(current_password, str) or len(current_password) > 512:
            raise Unauthorized("invalid current password")
        if not isinstance(new_password, str) or not 12 <= len(new_password) <= 512:
            raise ValueError("use a password of 12 to 512 characters")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT salt,pw FROM users WHERE name=?", (who["name"],)).fetchone()
            if not row or not hmac.compare_digest(derive(current_password, row["salt"]), row["pw"]):
                raise Unauthorized("invalid current password")
            salt = secrets.token_bytes(16)
            db.execute("UPDATE users SET salt=?,pw=? WHERE name=?", (salt, derive(new_password,salt), who["name"]))
            db.execute("DELETE FROM sessions WHERE username=?", (who["name"],))
            token = secrets.token_urlsafe(40)
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (digest(token), who["name"], int(time.time())+43200))
            return token

    def authenticate(self, token, agent=False):
        if not token or len(token) > 256:
            raise Unauthorized("sign in or supply a valid key")
        with self.db() as db:
            if agent:
                row = db.execute("SELECT id,space,permissions,trusted,name FROM keys WHERE digest=?", (digest(token),)).fetchone()
                if row:
                    return {"actor": "key:"+row["id"], "name": row["name"], "namespace": row["space"],
                            "permissions": set(row["permissions"].split()), "trusted": bool(row["trusted"]), "agent": True}
            else:
                row = db.execute("SELECT name,owner FROM sessions JOIN users ON username=name WHERE digest=? AND expires>?",
                                 (digest(token), time.time())).fetchone()
                if row:
                    return {"actor": "user:"+row["name"], "name": row["name"], "owner": bool(row["owner"]), "agent": False}
        raise Unauthorized("credential expired or revoked")

    def role(self, who, namespace):
        if who["agent"]:
            return None if who["namespace"] != namespace else "agent"
        with self.db() as db:
            if who["owner"]:
                return "owner" if db.execute("SELECT 1 FROM spaces WHERE id=?", (namespace,)).fetchone() else None
            row = db.execute("SELECT role FROM grants WHERE username=? AND space=?", (who["name"], namespace)).fetchone()
            return row[0] if row else None

    def require(self, who, namespace, permission):
        role = self.role(who, namespace)
        permissions = who["permissions"] if role == "agent" else ROLES.get(role, set())
        if permission not in permissions:
            raise Denied("permission required for this namespace")
        return role

    def spaces(self, who):
        with self.db() as db:
            if who.get("owner"):
                rows = db.execute("SELECT id,name,'owner' role FROM spaces ORDER BY name,id")
            else:
                rows = db.execute("SELECT id,name,role FROM spaces JOIN grants ON space=id WHERE username=? ORDER BY name,id",
                                  (who["name"],))
            return [dict(row) for row in rows]

    def admin(self, who, op, args):
        if who["agent"]:
            raise Denied("account access required")
        namespace = args.get("namespace")
        if op in {"create_namespace", "create_user"}:
            if not who["owner"]:
                raise Denied("team owner access required")
            if op == "create_namespace":
                return self.create_space(args.get("id"), args.get("name"))
            with self.db() as db:
                return self._user(db, args.get("user"), args.get("password"))
        with self.db() as db:
            if op == "revoke_key":
                row = db.execute("SELECT space FROM keys WHERE id=?", (args.get("id"),)).fetchone()
                namespace = row[0] if row else None
            self.require(who, namespace, "manage")
            if op == "grant":
                user, role = identity(args.get("user")), args.get("role")
                if role not in ("admin", "member", "viewer", "none"):
                    raise ValueError("choose admin, member, viewer or none")
                if not db.execute("SELECT 1 FROM users WHERE name=?", (user,)).fetchone():
                    raise ValueError("create the account first")
                db.execute("DELETE FROM grants WHERE username=? AND space=?", (user, namespace))
                if role != "none":
                    db.execute("INSERT INTO grants VALUES(?,?,?)", (user, namespace, role))
                return {"user": user, "namespace": namespace, "role": role}
            if op == "list_members":
                rows = db.execute("SELECT name user,'owner' role FROM users WHERE owner=1 UNION ALL SELECT username,role FROM grants JOIN users ON name=username WHERE space=? AND owner=0 ORDER BY 1", (namespace,))
                return {"items": [dict(row) for row in rows]}
            if op == "issue_key":
                permissions = args.get("permissions")
                if not isinstance(permissions, list) or not permissions or any(p not in ("read","write","feedback") for p in permissions):
                    raise ValueError("choose one or more agent permissions")
                trusted = args.get("trusted", False)
                if type(trusted) is not bool or trusted and "feedback" not in permissions:
                    raise ValueError("only feedback keys can report authoritative outcomes")
                key_id, token = secrets.token_hex(8), "gm_"+secrets.token_urlsafe(40)
                db.execute("INSERT INTO keys VALUES(?,?,?,?,?,?,?)", (key_id, digest(token), namespace, label(args.get("name")),
                           " ".join(sorted(set(permissions))), trusted, int(time.time())))
                return {"id": key_id, "key": token, "namespace": namespace}
            if op == "list_keys":
                rows = db.execute("SELECT id,name,permissions,trusted,created FROM keys WHERE space=? ORDER BY created DESC,id", (namespace,))
                return {"items": [{**dict(r), "permissions": r["permissions"].split()} for r in rows]}
            if op == "revoke_key":
                db.execute("DELETE FROM keys WHERE id=?", (args["id"],))
                return {"revoked": True}
        raise ValueError("unknown management operation")

"""One team's HTTP service. TLS terminates at the configured reverse proxy."""
import json
import logging
import sqlite3
import threading
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import connectors, models
from .service import OPS, dispatch, tool_list
from .store import Conflict
from .studio import namespace_activity
from .team_registry import Denied, Unauthorized, ROLES
from .webassets import asset

PERMISSIONS = {
    "read": {"tools", "status", "lookup", "library", "profile", "search", "context", "history", "source", "sources",
             "connections", "pending", "overview", "catalog", "runs", "run_detail", "recall", "trace", "compare_trace",
             "activity", "lessons", "get_policy"},
    "write": {"remember", "capture_conversation", "import_documents", "import_provider", "start_run", "run_note", "handoff"},
    "feedback": {"outcome"},
    "manage": {"review_lesson", "policy", "erase_source", "annotate", "retire", *connectors.CONNECTOR_OPS},
}


def needed(op):
    for permission, ops in PERMISSIONS.items():
        if op in ops:
            return permission
    raise ValueError("unknown namespace operation")


def operate(registry, who, namespace, op, args, *, studio=False):
    if op == "tools":
        role = registry.role(who, namespace)
        if not role:
            raise Denied("permission required for this namespace")
        permissions = who["permissions"] if who["agent"] else ROLES[role]
        return {"tools": [item for item in tool_list() if needed(item["name"]) in permissions]}
    role = registry.require(who, namespace, needed(op))
    if op == "start_run" and args.get("parent_run_id"):
        registry.require(who, namespace, "read")
    db = registry.ledger(namespace)
    admin = role in ("admin", "owner")
    if op in OPS:
        track = not studio or needed(op) != "read" or op in ("context", "search")
        return dispatch(db, op, args, actor=who["actor"], admin=admin, trusted=admin or who.get("trusted", False), track=track)
    if op in connectors.CONNECTOR_OPS:
        return connectors.call(db, op, args)
    if op == "overview":
        return {"counts": db.status(), "recent": db.sources(limit=5)["items"], "memories": db.library(limit=12)["items"],
                "pending": db.pending_count(), "workspace": namespace, "has_personal": True}
    if op == "catalog":
        return models.inventory()
    raise ValueError("unknown operation")


def team_server(registry, host="127.0.0.1", port=8787, origin=None):
    target = urlsplit(origin) if origin else None
    if target and (target.scheme not in ("https", "http") or not target.hostname or
                   target.path not in ("", "/") or target.query or target.fragment or target.username):
        raise ValueError("public origin must be an HTTPS origin or a loopback HTTP origin")
    if target and target.scheme == "http" and target.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("use HTTPS for the public team origin")
    if not target and host not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("configure the public HTTPS origin when exposing the service")
    secure = bool(target and target.scheme == "https")
    cookie_name = "__Host-gravity_session" if secure else "gravity_session"

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(60)

        def log_message(self, *args):
            pass

        def send_data(self, code, value, media="application/json", cookie=None):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", media)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            if cookie is not None:
                suffix = "; Secure" if secure else ""
                age = 43200 if cookie else 0
                self.send_header("Set-Cookie", f"{cookie_name}={cookie}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{suffix}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def token(self):
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
            except CookieError:
                return ""
            return cookies[cookie_name].value if cookie_name in cookies else ""

        def allowed(self):
            netloc = target.netloc if target else f"{host}:{self.server.server_port}"
            public = f"{target.scheme if target else 'http'}://{netloc}"
            return self.headers.get("Host", "").lower() == netloc.lower() and self.headers.get("Origin", public).rstrip("/") == public

        def do_GET(self):
            if not self.allowed():
                return self.send_data(403, {"error": "unrecognized host or origin"})
            if self.path == "/health":
                return self.send_data(200, {"status": "ready"})
            if self.path == "/session":
                try:
                    who = registry.authenticate(self.token())
                except Unauthorized:
                    return self.send_data(200, {"team": True, "authenticated": False, "namespaces": []})
                spaces = registry.spaces(who)
                counts = namespace_activity({ns["id"]: registry.ledger(ns["id"]) for ns in spaces}, spaces)
                return self.send_data(200, {"team": True, "authenticated": True, "user": who["name"],
                                           "owner": who["owner"], "namespaces": spaces, "namespace_activity": counts})
            item = asset(self.path.removeprefix("/"))
            if item:
                return self.send_data(200, *item)
            self.send_data(404, {"error": "unknown resource"})

        def do_POST(self):
            try:
                if not self.allowed():
                    raise Denied("unrecognized host or origin")
                if self.headers.get_content_type() != "application/json":
                    return self.send_data(415, {"error": "send application/json"})
                size = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= size <= 16_000_000:
                    return self.send_data(413, {"error": "request body exceeds the limit"})
                args = json.loads(self.rfile.read(size))
                if not isinstance(args, dict):
                    raise ValueError("send a JSON object")
                if self.path == "/auth/login":
                    token = registry.login(args.get("user"), args.get("password"))
                    return self.send_data(200, {"authenticated": True}, cookie=token)
                if self.path == "/auth/logout":
                    from .team_registry import digest
                    with registry.db() as db:
                        db.execute("DELETE FROM sessions WHERE digest=?", (digest(self.token()),))
                    return self.send_data(200, {"authenticated": False}, cookie="")
                if self.path == "/auth/password":
                    who = registry.authenticate(self.token())
                    token = registry.change_password(who, args.get("current_password"), args.get("new_password"))
                    return self.send_data(200, {"authenticated": True}, cookie=token)
                parts = self.path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["v2", "namespaces"]:
                    scheme, _, token = self.headers.get("Authorization", "").partition(" ")
                    if scheme.lower() != "bearer":
                        raise Unauthorized("bearer key required")
                    who = registry.authenticate(token, agent=True)
                    answer = operate(registry, who, parts[2], parts[3], args)
                elif len(parts) == 2 and parts[0] in ("admin", "studio"):
                    who = registry.authenticate(self.token())
                    if parts[0] == "admin":
                        answer = registry.admin(who, parts[1], args)
                    else:
                        answer = operate(registry, who, self.headers.get("X-Workspace", ""), parts[1], args, studio=True)
                else:
                    return self.send_data(404, {"error": "unknown route"})
                self.send_data(200, answer)
            except Unauthorized as exc:
                self.send_data(401, {"error": str(exc)})
            except (Denied, PermissionError) as exc:
                self.send_data(403, {"error": str(exc)})
            except Conflict as exc:
                self.send_data(409, {"error": str(exc)})
            except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
                self.send_data(400, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (sqlite3.Error, OSError):
                self.send_data(503, {"error": "storage or upstream unavailable"})

    return ThreadingHTTPServer((host, port), Handler)


def run_worker(registry, stop):
    def work():
        while not stop.is_set():
            for ns in registry.spaces({"owner": True}):
                if stop.is_set():
                    return
                try:
                    db = registry.ledger(ns["id"])
                    connectors.run_due(db)
                    from . import runs
                    runs.call(db, "activity", {"days": 7}, actor="maintenance", admin=True)
                except Exception as exc:
                    logging.warning("maintenance failed for %s (%s)", ns["id"], type(exc).__name__)
            stop.wait(60)
    thread = threading.Thread(target=work, daemon=True, name="gravity-maintenance")
    thread.start()
    return thread

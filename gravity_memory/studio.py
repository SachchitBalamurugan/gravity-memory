"""Local browser workspace. Administrative file paths stay in the CLI."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import shutil
import sys
import sqlite3
import tempfile
from pathlib import Path
import threading
import webbrowser

from . import connectors, github_sources, models
from .webassets import asset
from .runtime import MiniLM, route_review
from .semantic import index_passages
from .service import dispatch, OPS
from .store import Conflict, Ledger


def seed(db):
    entries = [
        ("Profile interview", "My name is Mara.", "profile.name", "Mara"),
        ("Account handoff", "I work at Northstar Studio.", "work.employer", "Northstar Studio"),
        ("Support preferences", "Please contact me via email.", "contact.channel", "email"),
        ("Project brief", "The active project is Atlas.", "project.name", "Atlas"),
        ("Kickoff notes", "The project owner is Mika Chen.", "project.owner", "Mika Chen"),
        ("Calendar sync", "Our working timezone is Europe/London.", "profile.timezone", "Europe/London"),
        ("Launch checklist", "The release channel is Canary.", "release.channel", "Canary"),
        ("Location update", "I moved to Bristol.", "home.city", "Bristol"),
        ("Contact correction", "Please contact me via Signal.", "contact.channel", "Signal"),
        ("Design review", "The interface accent is amber.", "design.accent", "amber"),
        ("Support transcript", "Evenings work best for a call.", None, None),
        ("Engineering handoff", "Deployment ownership is moving to the reliability team.", None, None),
    ]
    for i,(source, body, key, value) in enumerate(entries):
        facts = [{"key": key, "value": value, "quote": body}] if key else None
        db.remember("atlas-" + str(i+1), body, source, effective_at=f"2026-01-{i+1:02}T10:00:00Z", role="user", facts=facts)


def namespace_activity(stores, namespaces):
    counts = []
    for ns in namespaces:
        store = stores[ns["id"]]
        activity = dispatch(store, "activity", {"days": 30})
        counts.append({"id": ns["id"], "name": ns["name"], "requests": activity["reads"] + activity["writes"]})
    return counts


class Studio:
    def __init__(self, workspace):
        self.scratch = tempfile.TemporaryDirectory(prefix="gravity-sample-")
        self.stores = {"personal": workspace, "sample": Ledger(Path(self.scratch.name) / "sample.sqlite")}
        for name, store in self.stores.items():
            store.connector_namespace = name
        seed(self.stores["sample"])
        self.lock = threading.Lock()
        self.jobs = {}

    def job(self, identity, work):
        with self.lock:
            for job_id, state in self.jobs.items():
                if state["identity"] == identity and state["status"] == "working":
                    return {"job": job_id}
            if sum(s["status"] == "working" for s in self.jobs.values()) >= 4:
                raise ValueError("four local jobs are already running")
            for k in list(self.jobs):
                if len(self.jobs) < 80:
                    break
                if self.jobs[k]["status"] != "working":
                    del self.jobs[k]
            job_id = secrets.token_hex(10)
            self.jobs[job_id] = {"identity": identity, "status": "working"}
        def run():
            try:
                state = {"status": "done", "result": work()}
            except Exception as exc:
                state = {"status": "failed", "error": str(exc)[:400]}
            with self.lock:
                self.jobs[job_id].update(state)
        threading.Thread(target=run, daemon=True).start()
        return {"job": job_id}

    def call(self, scope, op, args):
        db = self.stores[scope]
        if op in OPS:
            return dispatch(db, op, args, track=op in ("remember", "capture_conversation", "retire", "annotate",
                "import_provider", "import_documents", "search", "context"))
        if op in connectors.CONNECTOR_OPS:
            return connectors.call(db, op, args)
        if op == "overview":
            return {"counts": db.status(), "recent": db.sources(limit=5)["items"],
                    "memories": db.library(limit=12)["items"], "pending": db.pending_count(),
                    "workspace": scope, "has_personal": self.stores["personal"].status()["documents"] > 0}
        if op == "github_list":
            return github_sources.list_files(db)
        if op == "github_connect":
            return github_sources.connect(db, args.get("repository"), args.get("path"), args.get("ref", ""))
        if op == "github_sync":
            return github_sources.sync(db, args.get("id"))
        if op == "github_disconnect":
            return github_sources.disconnect(db, args.get("id"))
        if op == "catalog":
            return models.inventory()
        if op == "routing":
            return models.configure(**args)
        if op == "install":
            model = models.entry(args.get("model"))["id"]
            return self.job(("models", "install", model), lambda: models.install(model))
        if op == "index":
            def index():
                if not models.installed("minilm"):
                    raise ValueError("install MiniLM in the model library first")
                encoder = MiniLM()
                result = index_passages(db, encoder)
                db.embedder = encoder
                return result
            return self.job((scope, "index"), index)
        if op == "suggest":
            request_id, fields = args.get("original_id"), args.get("fields")
            return self.job((scope, "suggest", request_id, json.dumps(fields, sort_keys=True)),
                            lambda: route_review(db, request_id, fields))
        if op == "job":
            with self.lock:
                state = self.jobs.get(args.get("id"))
                if not state or state["identity"][0] not in (scope, "models"):
                    raise ValueError("unknown job")
                return {k:v for k,v in state.items() if k != "identity"}
        raise ValueError("unknown workspace operation")


def studio_server(ledger, port=0):
    studio = Studio(ledger)
    ticket, session = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def output(self, code, body, media="application/json", headers=None):
            if media == "application/json":
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", media)
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Security-Policy","default-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            for k,v in (headers or {}).items():
                self.send_header(k,v)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def allowed_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def authenticated(self):
            expected = f"gm_{self.server.server_port}={session}".encode()
            return any(hmac.compare_digest(c.strip().encode(), expected) for c in self.headers.get("Cookie", "").split(";"))

        def send_pack(self, model):
            if not self.authenticated():
                return self.output(401, {"error": "Open the local launcher first."})
            try:
                with_runtime = model.startswith("runtime/")
                model = models.entry(model.removeprefix("runtime/"))["id"]
                if not models.installed(model):
                    raise ValueError("install the model before exporting")
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / (model + ".gmpack")
                    models.pack(model, path, with_runtime=with_runtime)
                    with path.open("rb") as stream:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Content-Length", str(path.stat().st_size))
                        self.end_headers()
                        shutil.copyfileobj(stream, self.wfile, 1024 * 1024)
            except (BrokenPipeError, ConnectionResetError):
                return
            except (ValueError, OSError) as exc:
                return self.output(400, {"error": str(exc)})

        def receive_pack(self, size):
            largest = max(sum(f[1] for f in m["files"]) for m in models.CATALOG.values())
            if not 0 < size <= largest + models.RUNTIME_BYTES + 1024 * 1024:
                return self.output(413, {"error": "model pack size is outside the catalog limit"})
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "upload.gmpack"
                with path.open("xb") as dest:
                    remaining = size
                    while remaining:
                        chunk = self.rfile.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError("model pack transfer interrupted")
                        dest.write(chunk)
                        remaining -= len(chunk)
                return self.output(200, models.unpack(path))

        def do_GET(self):
            if not self.allowed_host():
                return self.output(403, {"error":"invalid local host"})
            if self.path.startswith("/open/") and hmac.compare_digest(self.path[6:].encode(), ticket.encode()):
                return self.output(303, b"", "text/plain", {"Location":"/", "Set-Cookie":
                    f"gm_{self.server.server_port}={session}; HttpOnly; SameSite=Strict; Path=/"})
            if self.path.startswith("/packs/"):
                return self.send_pack(self.path[7:])
            if self.path == "/session":
                if not self.authenticated():
                    return self.output(401, {"team": False, "authenticated": False})
                namespaces = [{"id": k, "name": "My workspace" if k == "personal" else "Sample workspace", "role": "owner"} for k in studio.stores]
                return self.output(200, {"team": False, "authenticated": True, "user": "Local operator", "owner": True,
                                        "namespaces": namespaces, "namespace_activity": namespace_activity(studio.stores, namespaces)})
            item = asset(self.path[1:])
            if not item:
                return self.output(404, {"error":"unknown resource"})
            return self.output(200, *item)

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self.allowed_host() or (origin and origin != "http://" + self.headers["Host"]):
                return self.output(403, {"error":"wrong origin"})
            if not self.authenticated():
                return self.output(401, {"error":"Open Gravity Memory from its launcher to connect this browser."})
            if not self.path.startswith("/studio/"):
                return self.output(404, {"error":"unknown route"})
            try:
                size = int(self.headers.get("Content-Length","-1"))
                if self.path == "/studio/import-pack":
                    return self.receive_pack(size)
                if not 0 <= size <= 16_000_000:
                    return self.output(413, {"error":"request is too large"})
                scope = self.headers.get("X-Workspace","personal")
                if scope not in studio.stores:
                    raise ValueError("unknown workspace")
                args = json.loads(self.rfile.read(size))
                if not isinstance(args, dict):
                    raise ValueError("expected a JSON object")
                data = studio.call(scope, self.path[8:], args)
                self.output(200, data)
            except Conflict as exc:
                self.output(409, {"error":str(exc)})
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                self.output(400, {"error":str(exc)})
            except (sqlite3.Error, OSError) as exc:
                self.output(503, {"error":str(exc)})
    server = ThreadingHTTPServer(("127.0.0.1",port),Handler)
    return server, f"http://127.0.0.1:{server.server_port}/open/{ticket}", studio


def launch(db, port=0, open_browser=True):
    server, url, studio = studio_server(db, port)
    stop = threading.Event()
    def sync_personal():
        import logging
        while not stop.is_set():
            try:
                connectors.run_due(db)
                dispatch(db, "activity", {"days": 7})
            except (OSError, ValueError, sqlite3.Error) as error:
                logging.warning("Local maintenance failed (%s)", type(error).__name__)
            stop.wait(60)
    worker = threading.Thread(target=sync_personal, daemon=True, name="gravity-local-sync")
    worker.start()
    try:
        with server:
            if sys.stdout is not None:
                print("Gravity Memory is ready. Open this local session:", flush=True)
                print(url, flush=True)
            if open_browser:
                webbrowser.open(url)
            server.serve_forever()
    finally:
        stop.set()
        worker.join(timeout=5)
        studio.scratch.cleanup()

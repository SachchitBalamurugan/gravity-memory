"""Small synchronous Python client for the Gravity Memory team API."""
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request
from uuid import uuid4

from .team_registry import SLUG


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class GravityClient:
    def __init__(self, url=None, namespace=None, key=None):
        url = url or os.getenv("GRAVITY_URL", "")
        self.namespace = namespace or os.getenv("GRAVITY_NAMESPACE", "")
        self.key = key or os.getenv("GRAVITY_KEY", "")
        target = urlsplit(url)
        if not target.hostname or target.username or target.password or target.query or target.fragment or target.path not in ("", "/"):
            raise ValueError("GRAVITY_URL must be a server origin")
        if target.scheme != "https" and not (target.scheme == "http" and target.hostname in ("127.0.0.1", "localhost", "::1")):
            raise ValueError("use HTTPS for a remote Gravity Memory service")
        if not SLUG.fullmatch(self.namespace) or not self.key:
            raise ValueError("set GRAVITY_NAMESPACE and GRAVITY_KEY")
        self.base = url.rstrip("/") + "/v2/namespaces/" + self.namespace + "/"
        self.transport = build_opener(NoRedirect())

    def call(self, operation, arguments=None):
        if not isinstance(operation, str) or not SLUG.fullmatch(operation):
            raise ValueError("invalid operation name")
        data = json.dumps({} if arguments is None else arguments, allow_nan=False).encode()
        req = Request(self.base+operation, data=data, headers={"Content-Type": "application/json", "Authorization": "Bearer "+self.key})
        try:
            with self.transport.open(req, timeout=90) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                detail = json.load(exc).get("error", exc.reason)
            except (ValueError, AttributeError):
                detail = exc.reason
            raise ValueError(f"Gravity Memory returned HTTP {exc.code}: {detail}") from exc

    def start_run(self, title, *, request_id=None, parent_run_id=None):
        args = {"title": title, "request_id": request_id or uuid4().hex}
        if parent_run_id:
            args["parent_run_id"] = parent_run_id
        return self.call("start_run", args)

    def recall(self, run_id, query, *, request_id=None, budget_bytes=4096, **options):
        return self.call("recall", {"run_id": run_id, "query": query, "request_id": request_id or uuid4().hex,
                                   "budget_bytes": budget_bytes, **options})

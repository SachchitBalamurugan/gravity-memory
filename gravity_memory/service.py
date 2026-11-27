"""Shared operation registry for local HTTP, CLI and MCP clients."""
import hmac
import inspect
import json
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .store import Conflict

TEXT = {"type": "string"}
INT = {"type": "integer"}
FACT = {"type": "object", "required": ["key", "value", "quote"], "additionalProperties": False,
        "properties": {"key": TEXT, "quote": TEXT, "value": {"type": ["string", "null"]},
                       "start": INT, "expires_at": TEXT}}
TIMES = {"as_of": TEXT, "known_at": TEXT, "require_review": {"type": "boolean"}}
OPS = {
    "remember": ("Store an attributed source. Use a stable request_id on retries. Supply quoted facts or mark a first-person turn as user.",
                 ["request_id", "text", "source"],
                 {"request_id": TEXT, "text": TEXT, "source": TEXT, "effective_at": TEXT,
                  "facts": {"type": "array", "items": FACT}, "role": {"enum": ["document", "user", "assistant"]}}),
    "pending": ("List original turns or documents with no recognized facts that await review.", [], {"role": {"enum": ["user", "document"]}, "limit": INT, "offset": INT}),
    "annotate": ("Attach validated facts to an existing original, preserving its time and citation. Use [] to dismiss.",
                 ["request_id", "original_id", "facts"],
                 {"request_id": TEXT, "original_id": TEXT, "facts": {"type": "array", "items": FACT}}),
    "library": ("Browse memory keys and their resolved status.", [], {"limit": INT, "offset": INT}),
    "sources": ("Browse original sources in this workspace.", [], {"limit": INT, "offset": INT}),
    "lookup": ("Resolve a named fact with source offsets, effective time and ingestion time.",
               ["key"], {"key": TEXT, **TIMES}),
    "search": ("Find current facts or explicitly search historical source passages, which may be obsolete.",
               ["query"], {"query": TEXT, "view": {"enum": ["facts", "archive", "hybrid_archive", "auto_archive"]}, "limit": INT, **TIMES}),
    "history": ("Inspect the evidence for revisions, including superseded or revoked facts.",
                ["key"], {"key": TEXT, "limit": INT, "offset": INT}),
    "source": ("Read an original by its request_id.", ["request_id"], {"request_id": TEXT}),
    "status": ("Count stored sources, passages, assertions and keys.", [], {}),
}


def dispatch(ledger, name, args):
    if not isinstance(name, str) or name not in OPS:
        raise ValueError("unknown operation")
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    operation = getattr(ledger, name)
    try:
        inspect.signature(operation).bind(**args)
    except TypeError as error:
        raise ValueError(str(error)) from error
    return operation(**args)


def tool_list():
    return [{"name": name, "description": description,
             "inputSchema": {"type": "object", "required": required, "properties": fields, "additionalProperties": False}}
            for name, (description, required, fields) in OPS.items()]


def rpc(ledger, message):
    def error(code, detail):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": detail}}
    msg_id = message.get("id") if isinstance(message, dict) else None
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return error(-32600, "invalid JSON-RPC request")
    if "id" not in message:
        return None
    method, params = message["method"], message.get("params", {})
    if not isinstance(params, dict):
        return error(-32602, "params must be an object")
    if method == "initialize":
        supported = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
        version = params.get("protocolVersion")
        result = {"protocolVersion": version if version in supported else supported[0],
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "gravity-memory", "version": "0.2.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        try:
            value = dispatch(ledger, params.get("name"), params.get("arguments", {}))
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}
        except (ValueError, sqlite3.Error, OverflowError) as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        return error(-32601, "method not found")
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def stdio(ledger):
    for line in sys.stdin.buffer:
        try:
            response = rpc(ledger, json.loads(line))
        except (ValueError, RecursionError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "invalid JSON"}}
        if response is not None:
            sys.stdout.buffer.write(json.dumps(response).encode() + b"\n")
            sys.stdout.buffer.flush()


def http_server(ledger, port, token):
    if not token or not token.isascii() or len(token) < 24:
        raise ValueError("set GRAVITY_TOKEN to an ASCII secret of at least 24 characters")
    authorization = ("Bearer " + token).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            supplied = self.headers.get("Authorization", "").encode("utf-8")
            if not hmac.compare_digest(authorization, supplied):
                return self.respond(401, {"error": "unauthorized"})
            name = self.path.removeprefix("/v1/")
            if self.path != "/v1/" + name or name not in OPS:
                return self.respond(404, {"error": "unknown route"})
            try:
                size = int(self.headers.get("Content-Length", -1))
                if not 0 <= size <= 16_000_000:
                    return self.respond(413, {"error": "expected bounded JSON request"})
                args = json.loads(self.rfile.read(size))
                self.respond(200, dispatch(ledger, name, args))
            except Conflict as exc:
                self.respond(409, {"error": str(exc)})
            except (ValueError, OverflowError, RecursionError) as exc:
                self.respond(400, {"error": str(exc)})
            except sqlite3.Error:
                self.respond(503, {"error": "storage unavailable; retry with the same request_id"})

        def respond(self, status, data):
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)

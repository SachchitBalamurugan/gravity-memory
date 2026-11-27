"""Task-aware local inference. Weight transfer and model approval happen outside the write path."""
import json
import os
import secrets
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from . import models
from .review import prepare_review, validated

# One native review at a time. Loading a second model during memory pressure makes
# recovery worse. Each child releases the weights before the next attempt starts.
REVIEW_SLOT = threading.BoundedSemaphore(1)


class Unavailable(RuntimeError):
    pass


class MiniLM:
    name = "minilm/" + models.entry("minilm")["revision"] + "/mean-pool-v1"

    def __init__(self):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        m = models.entry("minilm")
        self.np = np
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        try:
            self.session = ort.InferenceSession(str(models.asset("minilm", m["files"][0][0])), options,
                                                providers=["CPUExecutionProvider"])
            self.tokenizer = Tokenizer.from_file(str(models.asset("minilm", "tokenizer.json")))
        except Exception as exc:
            raise Unavailable("unable to load local embedding files") from exc
        self.tokenizer.enable_truncation(max_length=256)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")

    def embed(self, texts):
        rows = self.tokenizer.encode_batch(texts)
        inputs = {key: self.np.asarray([getattr(r, field) for r in rows], dtype="int64")
                  for key, field in [("input_ids", "ids"), ("attention_mask", "attention_mask"), ("token_type_ids", "type_ids")]}
        try:
            hidden = self.session.run(["last_hidden_state"], inputs)[0]
        except Exception as exc:
            raise Unavailable("local encoder failed during inference") from exc
        mask = inputs["attention_mask"][:, :, None]
        return ((hidden * mask).sum(axis=1) / mask.sum(axis=1).clip(min=1)).tolist()


def archive_search(ledger, query, limit, as_of, known_at):
    from .semantic import hybrid
    from .store import limit_rows, string, utc
    limit_rows(limit)
    string(query, "query", 2048)
    utc(as_of)
    utc(known_at)
    reason = "no local embedding model installed"
    if ledger.embedder is not None or models.installed("minilm"):
        try:
            name = ledger.embedder.name if ledger.embedder is not None else MiniLM.name
            with ledger._tx() as db:
                indexed = db.execute("SELECT 1 FROM vectors WHERE model=? LIMIT 1", (name,)).fetchone()
            if indexed:
                if ledger.embedder is None:
                    ledger.embedder = MiniLM()
                result = hybrid(ledger, query, limit, as_of, known_at)
                return {**result, "route": "hybrid", "reason": "matching embedding index"}
            reason = "no index for the selected embedding model"
        except (ImportError, ValueError, OSError, RuntimeError, MemoryError) as exc:
            reason = "local embedding unavailable: " + type(exc).__name__
    result = ledger.search(query, view="archive", limit=limit, as_of=as_of, known_at=known_at)
    return {**result, "route": "full_text", "reason": reason}


def runtime_error(exc):
    if exc.code in (408, 413, 429, 500, 502, 503, 504, 507):
        return Unavailable(f"runtime HTTP {exc.code}")
    if exc.code == 400:
        detail = exc.read(4096).decode("utf-8", errors="replace").lower()
        if any(fragment in detail for fragment in ("exceed_context_size", "exceeds the available context",
                                                   "context length exceeded", "out of memory", "context window is full")):
            return Unavailable("local context or memory capacity exhausted")
    return ValueError(f"local runtime rejected the request (HTTP {exc.code})")


def native_chat(model, instructions, data, schema):
    executable = models.native_server()
    if not executable or not models.installed(model):
        raise Unavailable("weights or llama.cpp runtime missing")
    path = models.asset(model, models.entry(model)["files"][0][0])
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    key = secrets.token_urlsafe(32)
    base = f"http://127.0.0.1:{port}"
    local = build_opener(ProxyHandler({}))
    child = subprocess.Popen([str(executable), "-m", str(path), "--host", "127.0.0.1", "--port", str(port),
                              "--api-key", key, "-c", "4096", "-t", "4", "-np", "1", "--no-webui"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    try:
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise Unavailable(f"local runtime exited during load ({child.returncode})")
            try:
                with local.open(Request(base + "/health", headers=headers), timeout=0.6) as r:
                    if json.load(r).get("status") == "ok":
                        break
            except (OSError, ValueError):
                time.sleep(0.12)
        else:
            raise Unavailable("model load timed out")
        payload = {"messages": [{"role": "system", "content": instructions + " Return a JSON object with a facts array. "
                                 "Each fact has key, quote and value. Use the shortest exact value that answers the field."},
                                {"role": "user", "content": json.dumps(data, ensure_ascii=False)}],
                   "temperature": 0, "seed": 19, "max_tokens": 384,
                   "response_format": {"type": "json_schema", "json_schema": {"name": "facts", "schema": schema}}}
        req = Request(base + "/v1/chat/completions", headers=headers, data=json.dumps(payload).encode())
        try:
            with local.open(req, timeout=65) as response:
                choice = json.load(response)["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise Unavailable("local generation budget exhausted")
                return json.loads(choice["message"]["content"])
        except HTTPError as exc:
            raise runtime_error(exc) from exc
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise Unavailable("local model returned an unreadable response") from exc
        except (TimeoutError, URLError, ConnectionError) as exc:
            raise Unavailable("local runtime unavailable during inference") from exc
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=4)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def route_review(ledger, original_id, fields, *, generate=native_chat, policy=None):
    row, instructions, schema = prepare_review(ledger, original_id, fields)
    policy = policy or models.preferences()
    order = [policy["preferred"]]
    if policy["fallback"]:
        order += [m["id"] for m in models.CATALOG.values() if m["task"] == "review" and m["id"] not in order]
    attempts = []
    base = {"original_id": original_id, "status": "pending_review", "facts": [], "attempts": attempts}
    if not REVIEW_SLOT.acquire(timeout=0.2):
        return {**base, "reason": "local review busy; source retained"}
    try:
        for model in order:
            if not models.installed(model):
                attempts.append({"model": model, "outcome": "not_installed"})
                continue
            try:
                reply = generate(model, instructions, {"fields": fields, "source_text": row["text"]}, schema)
            except (Unavailable, MemoryError, OSError, json.JSONDecodeError) as exc:
                attempts.append({"model": model, "outcome": "unavailable", "reason": str(exc)[:160]})
                continue
            try:
                facts = validated(row, fields, reply)
            except ValueError:
                attempts.append({"model": model, "outcome": "ungrounded_proposal"})
                continue
            attempts.append({"model": model, "outcome": "completed"})
            return {**base, "model": model, "status": "proposed", "facts": facts}
        return {**base, "reason": "no eligible local model produced a validated proposal"}
    finally:
        REVIEW_SLOT.release()

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from . import Ledger
from .importers import import_records
from .review import suggest
from .semantic import OllamaEmbedder, index_passages
from .service import OPS, dispatch, http_server, stdio


def demo():
    with tempfile.TemporaryDirectory() as folder:
        ledger = Ledger(Path(folder) / "demo.sqlite")
        messages = [
            ("turn-1", "chat/first-visit", "I live in Seattle.", "2026-01-01"),
            ("turn-2", "chat/follow-up", "I moved to Tucson.", "2026-02-01"),
            ("turn-0", "chat/late-import", "I live in Reno.", "2025-12-01"),
        ]
        for event, source, body, date in messages:
            ledger.remember(event, body, source, role="user", effective_at=date + "T00:00:00Z")
        replay = ledger.remember("turn-2", "I moved to Tucson.", "chat/follow-up",
                                 role="user", effective_at="2026-02-01T00:00:00Z")
        return {"current": ledger.lookup("home.city", as_of="2026-03-01T00:00:00Z"),
                "in_january": ledger.lookup("home.city", as_of="2026-01-15T00:00:00Z"),
                "retry": replay, "counts": ledger.status()}


def main():
    parser = argparse.ArgumentParser(prog="recall-ledger")
    parser.add_argument("--db", default=".ledger/memory.sqlite", help="one trusted user or project per database")
    parser.add_argument("--model", help="installed Ollama embedding model for optional hybrid archive search")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="run the moving-city example in a temporary database")
    command = commands.add_parser("call", help="run an API operation using a JSON file or stdin")
    command.add_argument("operation", choices=OPS)
    command.add_argument("--file", type=Path)
    add = commands.add_parser("add", help="store text or the contents of a UTF-8 file")
    add.add_argument("--id", required=True)
    add.add_argument("--source", required=True)
    content = add.add_mutually_exclusive_group(required=True)
    content.add_argument("--text")
    content.add_argument("--file", type=Path)
    add.add_argument("--at")
    add.add_argument("--role", choices=["user", "assistant", "document"], default="document")
    get = commands.add_parser("lookup")
    get.add_argument("key")
    get.add_argument("--at")
    get.add_argument("--known-at")
    get.add_argument("--require-review", action="store_true")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--view", choices=["facts", "archive", "hybrid_archive"], default="facts")
    search.add_argument("--limit", type=int, default=8)
    search.add_argument("--at")
    search.add_argument("--require-review", action="store_true")
    history = commands.add_parser("history")
    history.add_argument("key")
    queue = commands.add_parser("pending", help="find sources awaiting classification")
    queue.add_argument("--role", choices=["user", "document"], default="user")
    review = commands.add_parser("annotate", help="confirm one cited fact or dismiss a source")
    review.add_argument("original_id")
    review.add_argument("--id", required=True, help="stable id for this review")
    review.add_argument("--key")
    review.add_argument("--value")
    review.add_argument("--quote")
    review.add_argument("--dismiss", action="store_true")
    proposal = commands.add_parser("suggest", help="propose cited facts from a local Ollama chat model")
    proposal.add_argument("original_id")
    proposal.add_argument("--fields", type=Path, required=True, help="JSON object with canonical keys and definitions")
    proposal.add_argument("--chat-model", required=True)
    ingest = commands.add_parser("import-provider", help="archive one user's records from a provider JSON export page")
    ingest.add_argument("provider", choices=["mem0", "supermemory"])
    ingest.add_argument("file", type=Path)
    ingest.add_argument("--scope", required=True, help="Mem0 user_id or Supermemory container tag")
    commands.add_parser("status")
    commands.add_parser("mcp", help="launch the workspace-bound MCP stdio server")
    serve = commands.add_parser("serve", help="start the local HTTP API using RECALL_TOKEN")
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser("index-vectors", help="embed pending passages with --model, resumable after interruption")
    commands.add_parser("export", help="write a UTF-8 JSONL snapshot to stdout").add_argument("--file", type=Path)
    commands.add_parser("restore", help="restore a JSONL snapshot into an empty database").add_argument("file", type=Path)
    commands.add_parser("erase", help="remove all data from the selected workspace").add_argument("--confirm", required=True)
    args = parser.parse_args()
    try:
        if args.command == "demo":
            result = demo()
        else:
            embedder = OllamaEmbedder(args.model) if args.model else None
            ledger = Ledger(args.db, embedder=embedder)
            if args.command == "call":
                raw = args.file.read_bytes() if args.file else sys.stdin.buffer.read()
                result = dispatch(ledger, args.operation, json.loads(raw))
            elif args.command == "add":
                body = args.file.read_bytes().decode("utf-8-sig") if args.file else args.text
                result = ledger.remember(args.id, body, args.source, role=args.role, effective_at=args.at)
            elif args.command == "lookup":
                result = ledger.lookup(args.key, as_of=args.at, known_at=args.known_at, require_review=args.require_review)
            elif args.command == "search":
                result = ledger.search(args.query, view=args.view, limit=args.limit, as_of=args.at, require_review=args.require_review)
            elif args.command == "history":
                result = ledger.history(args.key)
            elif args.command == "pending":
                result = ledger.pending(role=args.role)
            elif args.command == "annotate":
                fields = [args.key, args.value, args.quote]
                if (args.dismiss and any(fields)) or (not args.dismiss and not all(fields)):
                    raise ValueError("choose --dismiss or supply --key, --value and --quote")
                facts = [] if args.dismiss else [{"key": args.key, "value": args.value, "quote": args.quote}]
                result = ledger.annotate(args.id, args.original_id, facts)
            elif args.command == "suggest":
                result = suggest(ledger, args.original_id, json.loads(args.fields.read_bytes()), args.chat_model)
            elif args.command == "import-provider":
                result = import_records(ledger, args.provider, args.scope, json.loads(args.file.read_bytes()))
            elif args.command == "status":
                result = ledger.status()
            elif args.command == "index-vectors":
                if embedder is None:
                    raise ValueError("choose an installed Ollama model with --model")
                result = index_passages(ledger, embedder)
            elif args.command == "mcp":
                stdio(ledger)
                return 0
            elif args.command == "serve":
                with http_server(ledger, args.port, os.environ.get("RECALL_TOKEN")) as server:
                    print(f"Serving {ledger.path} on 127.0.0.1:{server.server_port}", file=sys.stderr, flush=True)
                    server.serve_forever()
                return 0
            elif args.command == "export":
                if args.file:
                    with args.file.open("x", encoding="utf-8", newline="\n") as dest:
                        for row in ledger.export():
                            dest.write(json.dumps(row, ensure_ascii=False) + "\n")
                else:
                    for row in ledger.export():
                        sys.stdout.buffer.write(json.dumps(row).encode() + b"\n")
                return 0
            elif args.command == "restore":
                with args.file.open(encoding="utf-8-sig") as stream:
                    result = ledger.restore(json.loads(line) for line in stream)
            elif args.command == "erase":
                if args.confirm != str(ledger.path):
                    raise ValueError(f"--confirm must match the absolute workspace path {ledger.path}")
                result = ledger.erase()
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error, OverflowError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

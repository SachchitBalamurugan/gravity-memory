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
    parser = argparse.ArgumentParser(prog="gravity-memory")
    parser.add_argument("--db", default=".ledger/memory.sqlite", help="one trusted user or project per database")
    parser.add_argument("--model", help="minilm or an installed Ollama embedding model for hybrid archive search")
    commands = parser.add_subparsers(dest="command", required=True)
    from . import agent_cli, team_cli
    team_cli.options(commands)
    agent_cli.options(commands)
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
    search.add_argument("--view", choices=["facts", "archive", "hybrid_archive", "auto_archive"], default="facts")
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
    proposal.add_argument("--chat-model", default="auto", help="auto routes catalog models locally; other names select Ollama")
    ingest = commands.add_parser("import-provider", help="archive one user's records from a provider JSON export page")
    ingest.add_argument("provider", choices=["mem0", "supermemory"])
    ingest.add_argument("file", type=Path)
    ingest.add_argument("--scope", required=True, help="Mem0 user_id or Supermemory container tag")
    studio = commands.add_parser("studio", help="open the local browser workspace")
    studio.add_argument("--port", type=int, default=0)
    studio.add_argument("--no-browser", action="store_true")
    shelf = commands.add_parser("models", help="manage optional local models")
    model_ops = shelf.add_subparsers(dest="action", required=True)
    model_ops.add_parser("list")
    model_ops.add_parser("install").add_argument("model_id")
    model_ops.add_parser("runtime", help="install or locate the local review runtime")
    pref = model_ops.add_parser("prefer")
    pref.add_argument("model_id")
    pref.add_argument("--no-fallback", action="store_true")
    pack = model_ops.add_parser("pack")
    pack.add_argument("model_id")
    pack.add_argument("destination", type=Path)
    pack.add_argument("--with-runtime", action="store_true", help="include the verified cached Windows x64 review runtime")
    model_ops.add_parser("unpack").add_argument("bundle", type=Path)
    github = commands.add_parser("github", help="connect and sync public GitHub text files")
    git_ops = github.add_subparsers(dest="action", required=True)
    git_ops.add_parser("list")
    connect = git_ops.add_parser("connect")
    connect.add_argument("repository")
    connect.add_argument("path")
    connect.add_argument("--ref", default="")
    git_ops.add_parser("sync").add_argument("id")
    git_ops.add_parser("disconnect").add_argument("id")
    commands.add_parser("status")
    commands.add_parser("mcp", help="launch the workspace-bound MCP stdio server")
    serve = commands.add_parser("serve", help="start the local HTTP API using GRAVITY_TOKEN")
    serve.add_argument("--port", type=int, default=8765)
    commands.add_parser("index-vectors", help="embed pending passages with --model, resumable after interruption")
    commands.add_parser("export", help="write a UTF-8 JSONL snapshot to stdout").add_argument("--file", type=Path)
    commands.add_parser("restore", help="restore a JSONL snapshot into an empty database").add_argument("file", type=Path)
    commands.add_parser("erase", help="remove all data from the selected workspace").add_argument("--confirm", required=True)
    args = parser.parse_args()
    try:
        if args.command == "agent":
            agent_cli.execute(args)
            return 0
        if args.command == "team":
            result = team_cli.execute(args)
        elif args.command == "models":
            from . import models
            if args.action == "list":
                result = models.inventory()
            elif args.action == "install":
                result = models.install(args.model_id)
            elif args.action == "runtime":
                models.ensure_runtime()
                result = {"runtime": str(models.native_server())}
            elif args.action == "prefer":
                result = models.configure(args.model_id, not args.no_fallback)
            elif args.action == "pack":
                result = models.pack(args.model_id, args.destination, with_runtime=args.with_runtime)
            elif args.action == "unpack":
                result = models.unpack(args.bundle)
        elif args.command == "demo":
            result = demo()
        else:
            from .runtime import MiniLM, route_review
            embedder = MiniLM() if args.model == "minilm" else (OllamaEmbedder(args.model) if args.model else None)
            ledger = Ledger(args.db, embedder=embedder)
            if args.command == "studio":
                from .studio import launch
                launch(ledger, args.port, not args.no_browser)
                return 0
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
                fields = json.loads(args.fields.read_bytes())
                if args.chat_model == "auto":
                    result = route_review(ledger, args.original_id, fields)
                else:
                    result = suggest(ledger, args.original_id, fields, args.chat_model)
            elif args.command == "import-provider":
                result = import_records(ledger, args.provider, args.scope, json.loads(args.file.read_bytes()))
            elif args.command == "github":
                from . import github_sources
                if args.action == "list":
                    result = github_sources.list_files(ledger)
                elif args.action == "connect":
                    result = github_sources.connect(ledger, args.repository, args.path, args.ref)
                elif args.action == "sync":
                    result = github_sources.sync(ledger, args.id)
                else:
                    result = github_sources.disconnect(ledger, args.id)
            elif args.command == "status":
                result = ledger.status()
            elif args.command == "index-vectors":
                if embedder is None:
                    raise ValueError("choose minilm or an installed Ollama embedding model with --model")
                result = index_passages(ledger, embedder)
            elif args.command == "mcp":
                stdio(ledger)
                return 0
            elif args.command == "serve":
                with http_server(ledger, args.port, os.environ.get("GRAVITY_TOKEN") or os.environ.get("RECALL_TOKEN")) as server:
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

"""Replay identical natural-language writes and queries. No answer model or gold fields enter the write path."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import sqlite3
import statistics
import tempfile
import time
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from gravity_memory import Ledger

CASES = Path(__file__).with_name("cases.json")


class Local:
    def __init__(self, folder, scope, engine):
        self.ledger = Ledger(Path(folder) / (scope + ".sqlite"))
        self.require_review = engine == "ledger-review-gated"
        self.view = "archive" if engine == "transcript-bm25" else "facts"

    def add(self, index, text):
        self.ledger.remember(str(index), text, f"turn/{index}", role="user")

    def search(self, query):
        hits = self.ledger.search(query, view=self.view, limit=3, require_review=self.require_review)["results"]
        return [row["quote"] if self.view == "archive" else row["evidence"][0]["quote"] for row in hits]


class Remote:
    def __init__(self, url, scope, engine, token):
        self.url = url.rstrip("/")
        self.scope = scope
        self.engine = engine
        self.headers = {"Content-Type": "application/json"}
        if token:
            if engine == "mem0-oss":
                self.headers["X-API-Key"] = token
            else:
                self.headers["Authorization"] = "Bearer " + token

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        with urlopen(Request(self.url + path, data=data, headers=self.headers), timeout=120) as response:
            return json.load(response)

    def add(self, index, text):
        if self.engine == "mem0-oss":
            self.request("/memories", {"messages": [{"role": "user", "content": text}], "user_id": self.scope})
            return
        result = self.request("/v3/documents", {"content": text, "containerTag": self.scope,
                              "customId": f"{self.scope}-{index}", "dreaming": "instant"})
        end = time.monotonic() + 180
        while result.get("status") != "done":
            if result.get("status") == "failed" or time.monotonic() >= end:
                raise RuntimeError(f"indexing did not finish for turn {index}")
            time.sleep(1)
            result = self.request("/v3/documents/" + quote(result["id"], safe=""))

    def search(self, query):
        if self.engine == "mem0-oss":
            response = self.request("/search", {"query": query, "filters": {"user_id": self.scope}, "top_k": 3})
        else:
            response = self.request("/v4/search", {"q": query, "containerTag": self.scope,
                                    "searchMode": "memories", "limit": 3})
        rows = response["results"] if isinstance(response, dict) else response
        if not isinstance(rows, list):
            raise ValueError("unexpected provider response")
        texts = [row.get("memory") or row.get("chunk") for row in rows[:3]]
        if any(not isinstance(t, str) for t in texts):
            raise ValueError("missing result text in provider response")
        return texts


def contains(text, value):
    return re.search(r"(?<!\w)" + re.escape(value.casefold()) + r"(?!\w)", text.casefold()) is not None


def summary_latency(samples):
    return {"count": len(samples), "median_ms": round(statistics.median(samples), 3),
            "p95_ms": round(sorted(samples)[math.ceil(0.95 * len(samples)) - 1], 3)}


def run(engine, cases, folder, url=None, token=None, version=None):
    results, writes, reads = [], [], []
    prefix = "recall-eval-" + uuid4().hex[:12]
    for case in cases:
        scope = prefix + "-" + case["case"]
        provider = Remote(url, scope, engine, token) if url else Local(folder, scope, engine)
        for index, text in enumerate(case["messages"]):
            begin = time.perf_counter()
            provider.add(index, text)
            writes.append((time.perf_counter() - begin) * 1000)
        for query, gold, obsolete in case["queries"]:
            begin = time.perf_counter()
            hits = provider.search(query)
            reads.append((time.perf_counter() - begin) * 1000)
            results.append({"case": case["case"], "query": query, "gold": gold, "obsolete": obsolete,
                            "hits": hits, "hit_at_1": bool(gold and hits and contains(hits[0], gold)),
                            "hit_at_3": bool(gold and any(contains(t, gold) for t in hits)),
                            "stale_at_3": any(contains(t, word) for word in obsolete for t in hits),
                            "empty": not hits})
    positives = [r for r in results if r["gold"]]
    negatives = [r for r in results if r["gold"] is None]
    changed = [r for r in results if r["obsolete"]]
    return {"engine": engine, "version": version, "run_scope_prefix": prefix if url else None,
            "counts": {"answerable": len(positives), "hit_at_1": sum(r["hit_at_1"] for r in positives),
                       "hit_at_3": sum(r["hit_at_3"] for r in positives),
                       "obsolete_probes": len(changed), "stale_at_3": sum(r["stale_at_3"] for r in changed),
                       "unknown": len(negatives), "empty_on_unknown": sum(r["empty"] for r in negatives)},
            "write": summary_latency(writes), "read": summary_latency(reads), "queries": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["local", "ledger", "ledger-review-gated", "transcript-bm25", "mem0-oss", "supermemory"], default="local")
    parser.add_argument("--url", help="explicit disposable deployment, each run writes new synthetic namespaces")
    parser.add_argument("--version", help="deployed server version and model configuration for repeatability")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    remote = args.engine in ("mem0-oss", "supermemory")
    if remote != bool(args.url) or remote and not args.version:
        parser.error("remote comparisons require --url and --version; local comparisons use neither")
    token = os.getenv("RECALL_BENCH_TOKEN") if remote else None
    if remote:
        target = urlsplit(args.url)
        if target.scheme != "https" and not (target.scheme == "http" and target.hostname in ("localhost", "127.0.0.1", "::1")):
            parser.error("use HTTPS for a non-loopback deployment")
    manifest = CASES.read_bytes()
    cases = json.loads(manifest)
    engines = ["ledger", "ledger-review-gated", "transcript-bm25"] if args.engine == "local" else [args.engine]
    with tempfile.TemporaryDirectory() as directory:
        reports = [run(engine, cases, directory, args.url, token, args.version or "0.3.0") for engine in engines]
    output = {"fixture_sha256": hashlib.sha256(manifest).hexdigest(),
              "environment": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version, "os": platform.system()},
              "reports": reports}
    if args.out:
        args.out.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for report in reports:
        print(json.dumps({k: v for k, v in report.items() if k != "queries"}, indent=2))


if __name__ == "__main__":
    main()

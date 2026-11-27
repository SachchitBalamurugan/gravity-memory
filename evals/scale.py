"""Single-machine cold-connection timings and exact-key verification on shuffled synthetic updates."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import platform
import random
import sqlite3
import tempfile
import time

from recall_ledger import Ledger
from .run import summary_latency


def run(folder, keys, revisions):
    rng = random.Random(916032)
    timeline = [(k, r) for k in range(keys) for r in range(revisions)]
    rng.shuffle(timeline)
    epoch = datetime(2024, 1, 1, tzinfo=timezone.utc)
    ledger = Ledger(Path(folder) / "scale.sqlite")
    writes, exact, searches = [], [], []
    correct = 0
    for k, r in timeline:
        key, value = f"account.field_{k}", f"owner-{k:05}-{r:02}"
        body = f"The current assignee for account {k} is {value}."
        at = (epoch + timedelta(hours=r)).isoformat()
        start = time.perf_counter()
        ledger.remember(f"{k}/{r}", body, "synthetic handoff", effective_at=at,
                        facts=[{"key": key, "value": value, "quote": body}])
        writes.append((time.perf_counter() - start) * 1000)
    for k in range(keys):
        key, value = f"account.field_{k}", f"owner-{k:05}-{revisions-1:02}"
        start = time.perf_counter()
        hit = ledger.lookup(key)
        exact.append((time.perf_counter() - start) * 1000)
        correct += hit["value"] == value
    for k in rng.sample(range(keys), min(150, keys)):
        start = time.perf_counter()
        ledger.search(f"field_{k} owner", limit=5)
        searches.append((time.perf_counter() - start) * 1000)
    return {"seed": 916032, "keys": keys, "revisions": revisions, "correct_current": correct,
            "python": platform.python_version(), "sqlite": sqlite3.sqlite_version, "platform": platform.system(),
            "counts": ledger.status(), "write": summary_latency(writes), "lookup": summary_latency(exact),
            "broad_search_limit_5": summary_latency(searches),
            "database_bytes_after_close": ledger.path.stat().st_size}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keys", type=int, default=500)
    parser.add_argument("--revisions", type=int, default=3)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.keys < 1 or args.revisions < 1:
        parser.error("keys and revisions must be positive")
    with tempfile.TemporaryDirectory() as folder:
        report = run(folder, args.keys, args.revisions)
    body = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(body + "\n", encoding="utf-8")
    print(body)

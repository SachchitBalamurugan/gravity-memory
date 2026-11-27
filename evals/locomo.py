"""Public evidence retrieval replay. Read the authors' LoCoMo10 file without redistributing conversations."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from gravity_memory import Ledger
from gravity_memory.runtime import MiniLM
from gravity_memory.semantic import index_passages

SHA = "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"


def run(dataset, out):
    raw = dataset.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SHA:
        raise ValueError("expected the documented LoCoMo10 revision")
    model = MiniLM()
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for case in json.loads(raw):
            db = Ledger(Path(tmp) / (case["sample_id"] + ".sqlite"), embedder=model)
            chat = case["conversation"]
            with db._tx(write=True) as conn:
                for session, turns in chat.items():
                    if not isinstance(turns, list):
                        continue
                    for turn in turns:
                        text = turn["speaker"] + ": " + turn["text"]
                        db._remember(conn, turn["dia_id"], text, session, "document", None, [], "2025-01-01T00:00:00.000000+00:00")
            index_passages(db, model, 32)
            candidates = [(i,q) for i,q in enumerate(case["qa"]) if q.get("category") != 5 and q.get("evidence")]
            candidates.sort(key=lambda item: hashlib.sha256((case["sample_id"] + "|" + str(item[0])).encode()).digest())
            for i, q in candidates[:12]:
                gold = set(q["evidence"])
                item = {"conversation": case["sample_id"], "question_index": i, "evidence": sorted(gold)}
                for view in ("archive", "hybrid_archive"):
                    hits = [h["request_id"] for h in db.search(q["question"], view=view, limit=5)["results"]]
                    item[view] = {"hits": hits, "any": bool(gold.intersection(hits)), "all": gold.issubset(hits)}
                rows.append(item)
            print(case["sample_id"], len(rows), flush=True)
    counts = {view: {metric: sum(row[view][metric] for row in rows) for metric in ("any", "all")}
              for view in ("archive", "hybrid_archive")}
    out.write_text(json.dumps({"dataset_sha256": SHA, "model": model.name, "k": 5, "questions": len(rows),
                               "counts": counts, "details": rows}, indent=2) + "\n", encoding="utf-8")
    print(counts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    opts = parser.parse_args()
    run(opts.dataset, opts.out)

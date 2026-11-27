"""Exercise two real review models, an offline transfer and an empty model cache."""
import argparse
import json
import os
from pathlib import Path
import platform
import tempfile
import time
from unittest.mock import patch

from gravity_memory import Ledger, models
from gravity_memory.runtime import route_review


def measure():
    if not all(models.installed(model) for model in models.CATALOG) or not models.native_server():
        raise ValueError("install qwen-small, smol-backup and minilm, then set up the review runtime")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)
        ledger = Ledger(path / "memory.sqlite")
        source = "My preferred language is French."
        ledger.remember("note", source, "customer onboarding", role="user")
        fields = {"profile.language": "Preferred language"}
        policy = {"preferred": "qwen-small", "fallback": True}
        start = time.monotonic()
        actual = route_review(ledger, "note", fields, policy=policy)
        elapsed = round(time.monotonic() - start, 2)
        assert ledger.lookup("profile.language")["status"] == "unknown"
        independent = route_review(ledger, "note", fields, policy={**policy, "fallback": False})
        bundle = path / "minilm.gmpack"
        models.pack("minilm", bundle)
        with patch.dict(os.environ, {"GRAVITY_MODEL_DIR": str(path / "other-machine")}):
            empty = route_review(ledger, "note", fields, policy=policy)
            assert not empty["facts"] and ledger.source("note")["text"] == source
            models.unpack(bundle)
            moved = {}
            for name, size, sha in models.entry("minilm")["files"]:
                models.verify(models.asset("minilm", name), size, sha)
                moved[name] = sha
        review_transfer = None
        if platform.system() == "Windows" and (models.cache() / models.RUNTIME_MEMBER).is_file():
            review_pack = path / "review.gmpack"
            models.pack("smol-backup", review_pack, with_runtime=True)
            with patch.dict(os.environ, {"GRAVITY_MODEL_DIR": str(path / "other-machine")}):
                with patch.object(models, "urlopen", side_effect=AssertionError("unexpected download")):
                    restored = models.unpack(review_pack)
                    rerun = route_review(ledger, "note", fields, policy=policy)
                review_transfer = {"bytes": review_pack.stat().st_size, "import": restored,
                                   "runtime_ready": bool(models.native_server()), "result": rerun}
        if actual["facts"]:
            ledger.annotate("confirmed", "note", actual["facts"])
        return {"os": platform.system(), "architecture": platform.machine(), "source": source,
                "runtime": "llama.cpp b12807 (Windows CPU on reference run)",
                "revisions": {k:v["revision"] for k,v in models.CATALOG.items()}, "fields": fields,
                "seconds_for_routed_request": elapsed, "routed": actual, "fallback_disabled": independent,
                "empty_cache": empty, "original_retained": ledger.source("note")["text"] == source,
                "after_operator_confirmation": ledger.lookup("profile.language")["value"],
                "offline_review_transfer": review_transfer, "offline_transfer": {"bytes": bundle.stat().st_size, "verified_files": moved}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    data = json.dumps(measure(), indent=2)
    if args.out:
        args.out.write_text(data + "\n", encoding="utf-8")
    print(data)

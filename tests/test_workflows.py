import json
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from recall_ledger import Ledger
from recall_ledger.importers import import_records
from recall_ledger.review import suggest


class Workflows(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.db = Ledger(self.folder / "app.sqlite")

    def test_import_mem0_records_with_stable_scope_and_retries(self):
        data = {"results": [{"id": "k", "user_id": "a", "memory": "Moved to Verona", "created_at": "2026-01-01T00:00:00Z"},
                            {"id": "k", "user_id": "b", "memory": "Moved to Delft", "created_at": "2026-01-02T00:00:00Z"}]}
        self.assertEqual(import_records(self.db, "mem0", "a", data), {"imported": 1, "replayed": 0, "outside_scope": 1})
        self.assertEqual(import_records(self.db, "mem0", "a", data)["replayed"], 1)
        self.assertEqual(self.db.search("Delft", view="archive")["results"], [])
        orig = self.db.pending(role="document")[0]["request_id"]
        self.db.annotate("review", orig, [{"key": "home.city", "value": "Verona", "quote": "Moved to Verona"}])
        self.assertEqual(self.db.lookup("home.city")["evidence"][0]["source"], "mem0/k")
        self.assertEqual(self.db.pending(role="document"), [])

    def test_import_supermemory_malformed_page_rolls_back(self):
        doc = {"id": "one", "containerTags": ["a"], "content": "Cedar was chosen.", "updatedAt": "2026-03-01T06:00:00Z"}
        with self.assertRaises(ValueError):
            import_records(self.db, "supermemory", "a", {"documents": [doc, {**doc, "containerTags": []}]})
        self.assertEqual(self.db.status()["documents"], 0)
        self.assertEqual(import_records(self.db, "supermemory", "a", [doc])["imported"], 1)

    def test_model_proposal_validated_without_mutation(self):
        self.db.remember("ticket", "The importer runs on Cedar.", "ticket/45")
        def fake(model, instructions, data, schema):
            self.assertEqual(data["source_text"], "The importer runs on Cedar.")
            self.assertEqual(schema["properties"]["facts"]["items"]["properties"]["key"]["enum"], ["importer.host"])
            return {"facts": [{"key": "importer.host", "quote": "runs on Cedar", "value": "Cedar"}]}
        result = suggest(self.db, "ticket", {"importer.host": "Active worker host"}, "fixture", generate=fake)
        self.assertEqual(self.db.status()["assertions"], 0)
        self.db.annotate("approved", "ticket", result["facts"])
        self.assertEqual(self.db.lookup("importer.host")["value"], "Cedar")
        for key, value in [("other.key", "Cedar"), ("importer.host", "Ash")]:
            with self.assertRaises(ValueError):
                suggest(self.db, "ticket", {"importer.host": "Active worker host"}, "fixture",
                        generate=lambda *unused: {"facts": [{"key": key, "quote": "runs on Cedar", "value": value}]})

    def test_review_gate_can_withhold_a_stale_fact_until_newer_turn_is_classified(self):
        self.db.remember("a", "I live in Oslo.", "chat", role="user", effective_at="2025-01-01T00:00:00Z")
        at = self.db.remember("b", "These days I call Ghent home.", "chat", role="user",
                              effective_at="2025-02-01T00:00:00Z")["recorded_at"]
        self.assertEqual(self.db.lookup("home.city")["value"], "Oslo")
        held = self.db.lookup("home.city", require_review=True)
        self.assertEqual((held["status"], held["pending_sources"]), ("pending_review", ["b"]))
        self.assertEqual(self.db.search("Oslo", require_review=True)["results"], [])
        self.db.annotate("r", "b", [{"key":"home.city", "value":"Ghent", "quote":"call Ghent home"}])
        self.assertEqual(self.db.lookup("home.city", require_review=True)["value"], "Ghent")
        self.assertEqual(self.db.lookup("home.city", require_review=True, known_at=at)["status"], "pending_review")

    def test_cli_review_export_restore(self):
        def cli(path, *args):
            run = subprocess.run([sys.executable, "-m", "recall_ledger", "--db", str(path), *args],
                                 capture_output=True, text=True, check=True)
            return json.loads(run.stdout) if run.stdout else None
        src = self.folder / "first.sqlite"
        dst = self.folder / "second.sqlite"
        backup = self.folder / "export.jsonl"
        cli(src, "add", "--id", "raw", "--source", "ticket", "--role", "user", "--text", "My base is Cork.")
        self.assertEqual(cli(src, "pending")[0]["request_id"], "raw")
        cli(src, "annotate", "raw", "--id", "review", "--key", "home.city", "--value", "Cork", "--quote", "base is Cork")
        cli(src, "export", "--file", str(backup))
        cli(dst, "restore", str(backup))
        self.assertEqual(cli(dst, "lookup", "home.city")["value"], "Cork")
        self.assertEqual(cli(dst, "status")["reviews"], 1)

    def test_snapshot_detects_truncation_and_preserves_extraction(self):
        from unittest.mock import patch
        self.db.remember("a", "I live in Riga.", "chat", role="user")
        lines = list(self.db.export())
        restored = Ledger(self.folder / "fresh.sqlite")
        with self.assertRaises(ValueError):
            restored.restore(lines[:-1])
        self.assertEqual(restored.status()["documents"], 0)
        with self.assertRaises(ValueError):
            restored.restore([lines[0], {**lines[1], "source": "changed"}, lines[2]])
        self.assertIsNone(restored.source("a"))
        with patch("recall_ledger.store.extract", side_effect=AssertionError("restore must keep historical facts")):
            restored.restore(lines)
        self.assertEqual(restored.lookup("home.city"), self.db.lookup("home.city"))

    def test_randomized_timeline_against_reference(self):
        rng = random.Random(527)
        entries = []
        epoch = datetime(2024, 1, 1, tzinfo=timezone.utc)
        def t(n):
            return (epoch + timedelta(days=n)).isoformat()
        for i in range(150):
            day = rng.randrange(1, 80)
            value = rng.choice(["Lima", "Cork", "Oslo", None])
            expires = day + 3 if value and rng.randrange(3) == 0 else None
            body = value or "withdraw"
            fact = {"key": "home.city", "quote": body, "value": value}
            if expires:
                fact["expires_at"] = t(expires)
            self.db.remember(str(i), body, "replay", facts=[fact], effective_at=t(day))
            entries.append((day, value, expires))
        for day in range(90):
            eligible = [item for item in entries if item[0] <= day]
            expected_status, expected_value = "unknown", None
            if eligible:
                latest = max(e[0] for e in eligible)
                variants = {(v, e) for d, v, e in eligible if d == latest}
                if len(variants) > 1:
                    expected_status = "disputed"
                else:
                    value, end = variants.pop()
                    if value is None:
                        expected_status = "revoked"
                    elif end and end <= day:
                        expected_status = "expired"
                    else:
                        expected_status, expected_value = "current", value
            result = self.db.lookup("home.city", as_of=t(day))
            self.assertEqual((result["status"], result["value"]), (expected_status, expected_value))

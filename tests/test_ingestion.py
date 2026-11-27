import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from gravity_memory import Conflict, Ledger
from gravity_memory.ingestion import capture_conversation, import_documents


class IngestionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "workspace.sqlite"
        self.ledger = Ledger(self.path)
        self.messages = [
            {"role": "user", "content": "I live in Dublin."},
            {"role": "assistant", "content": "I live in Odessa."},
            {"role": "user", "content": "I moved to Ghent."},
        ]

    def capture(self, messages, request_id="chat-1", source="Support handoff"):
        return capture_conversation(self.ledger, request_id, source, messages)

    def test_chat_roles_citations_and_both_time_axes(self):
        batch = self.capture(self.messages)
        self.assertEqual((batch["imported"], batch["replayed"]), (3, 0))
        first, reply, last = batch["receipts"]
        self.assertLess(first["effective_at"], last["effective_at"])
        self.assertEqual(first["recorded_at"], last["recorded_at"])
        self.assertEqual(self.ledger.lookup("home.city", as_of=first["effective_at"])["value"], "Dublin")
        before = (datetime.fromisoformat(last["recorded_at"]) - timedelta(microseconds=1)).isoformat()
        self.assertEqual(self.ledger.lookup("home.city", known_at=before)["status"], "unknown")
        current = self.ledger.lookup("home.city", known_at=last["recorded_at"])
        self.assertEqual((current["value"], current["evidence"][0]["request_id"]), ("Ghent", last["request_id"]))
        self.assertEqual(self.ledger.source(reply["request_id"])["role"], "assistant")
        self.assertEqual(reply["keys"], [])
        archive = self.ledger.search("Odessa", view="archive")["results"]
        self.assertEqual(archive[0]["request_id"], reply["request_id"])

    def test_replay_is_stable_after_export_restore(self):
        first = self.capture(self.messages)
        other = Ledger(self.path.with_name("restored.sqlite"))
        other.restore(self.ledger.export())
        restored = capture_conversation(other, "chat-1", "Support handoff", self.messages)
        self.assertEqual(restored["replayed"], 3)
        self.assertEqual(restored["receipts"], [dict(r, replayed=True) for r in first["receipts"]])

    def test_consistent_retry_supports_equivalent_instants_and_late_arrivals(self):
        first = self.capture([{"role":"user", "content":"I live in Lyon.", "effective_at":"2025-03-01T01:00:00+01:00"}])
        again = self.capture([{"role":"user", "content":"I live in Lyon.", "effective_at":"2025-03-01T00:00:00Z"}])
        self.assertEqual(again["receipts"][0], dict(first["receipts"][0], replayed=True))
        self.capture([{"role":"user", "content":"I live in Cork.", "effective_at":"2025-01-01T00:00:00Z"}],
                     request_id="chat-2")
        self.assertEqual(self.ledger.lookup("home.city")["value"], "Lyon")
        self.assertEqual(self.ledger.lookup("home.city", as_of="2025-02-01T00:00:00Z")["value"], "Cork")

    def test_reusing_batch_id_checks_every_turn_and_length(self):
        first = self.capture(self.messages)
        variants = [
            self.messages[:-1], self.messages + [self.messages[-1]],
            [self.messages[1], self.messages[0], self.messages[2]],
            [*self.messages[:-1], {"role":"assistant", "content":self.messages[-1]["content"]}],
            [*self.messages[:-1], {**self.messages[-1], "effective_at":first["receipts"][-1]["effective_at"]}],
        ]
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(Conflict):
                self.capture(variant)
        with self.assertRaises(Conflict):
            self.capture(self.messages, source="A different customer")
        self.assertEqual(self.ledger.status()["documents"], 3)
        self.assertEqual(self.ledger.lookup("home.city")["value"], "Ghent")

    def test_invalid_turn_does_not_publish_partial_transcript(self):
        for message in [ {"role":"user", "content":"   "}, {"role":"system", "content":"context"},
                         {"role":"user", "content":"hello", "effective_at":"2025-01-05"},
                         {"role":"user", "content":"a", "facts":[]} ]:
            with self.subTest(message=message), self.assertRaises(ValueError):
                self.capture([self.messages[0], message])
        self.assertEqual(self.ledger.status()["documents"], 0)

    def test_concurrent_capture_has_one_winner(self):
        with ThreadPoolExecutor(max_workers=3) as workers:
            results = list(workers.map(self.capture, [self.messages] * 3))
        self.assertEqual(sorted(r["imported"] for r in results), [0, 0, 3])
        self.assertEqual(self.ledger.status()["documents"], 3)

    def test_files_preserve_source_text_and_review_role(self):
        text = "# Handoff\r\n🎙️\r\nI live in Quito.\r\n"
        files = [{"name":"Customer.md", "text":text}, {"name":"Notes.TXT", "text":"Decision pending."}]
        result = import_documents(self.ledger, files)
        self.assertEqual(result["imported"], 2)
        receipt = result["receipts"][0]
        stored = self.ledger.source(receipt["request_id"])
        self.assertEqual((stored["text"], stored["source"], stored["role"]), (text, "Customer.md", "document"))
        self.assertEqual(self.ledger.lookup("home.city")["status"], "unknown")
        hit = self.ledger.search("Quito", view="archive")["results"][0]
        self.assertEqual(text[hit["start"]:hit["stop"]], hit["quote"])
        self.assertEqual(import_documents(self.ledger, files[::-1])["replayed"], 2)
        edited = import_documents(self.ledger, [{"name":"Customer.md", "text":"New decision."}])
        self.assertEqual(edited["imported"], 1)
        self.assertEqual(self.ledger.source(receipt["request_id"])["text"], text)

    def test_document_provenance_and_dates_participate_in_identity(self):
        first = {"name":"A.txt", "text":"A statement.", "effective_at":"2025-01-01T00:00:00Z"}
        same = {**first, "effective_at":"2025-01-01T03:00:00+03:00"}
        other = {**first, "name":"B.txt"}
        results = import_documents(self.ledger, [first, same, other])
        self.assertEqual((results["imported"], results["replayed"]), (2, 1))
        self.assertEqual(len({r["request_id"] for r in results["receipts"]}), 2)

    def test_bad_filename_or_payload_rolls_back_first_file(self):
        first = {"name":"ok.txt", "text":"Unpublished handoff."}
        for bad in [{"name": name, "text":"body"} for name in
                    ("../../note.md", "C:\\docs\\note.md", "a.exe", "file:note.txt", "a\n.txt")] + [
                        {"name":"b.txt", "text":""},
                        {"name":"b.txt", "text":"body", "path":"somewhere"},
                    ]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                import_documents(self.ledger, [first, bad])
            self.assertEqual(self.ledger.status()["documents"], 0)


if __name__ == "__main__":
    unittest.main()

import concurrent.futures
import json
from contextlib import closing
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from recall_ledger import Conflict, Ledger
from recall_ledger.semantic import index_passages
from recall_ledger.service import dispatch, http_server


def date(month):
    return f"2025-{month:02d}-01T00:00:00Z"


class StoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "customer.sqlite"
        self.db = Ledger(self.path)

    def note(self, request, text, when=1, **options):
        return self.db.remember(request, text, "chat/" + request, effective_at=date(when), role="user", **options)

    def test_old_import_cannot_win(self):
        self.note("move", "I moved to Fresno.", 3)
        self.note("old", "I live in Dublin.")
        self.assertEqual(self.db.lookup("home.city")["value"], "Fresno")
        self.assertEqual(self.db.lookup("home.city", as_of=date(2))["value"], "Dublin")
        self.assertEqual(self.db.search("Dublin")["results"], [])
        self.assertEqual(self.db.search("Dublin", view="archive")["results"][0]["request_id"], "old")

    def test_time_known_distinct_from_effective_time(self):
        first = self.note("old", "I live in Lima.", 1)
        self.note("new", "I moved to Oslo.", 2)
        self.assertEqual(self.db.lookup("home.city", as_of=date(3), known_at=first["recorded_at"])["value"], "Lima")
        self.assertEqual(self.db.search("city", as_of=date(3), known_at=first["recorded_at"])["results"][0]["value"], "Lima")

    def test_conflict_is_explicit_and_newer_evidence_resolves_it(self):
        for request, town in [("a", "Troy"), ("b", "Rome")]:
            self.note(request, f"I live in {town}.")
        disputed = self.db.lookup("home.city")
        self.assertEqual(disputed["status"], "disputed")
        self.assertIsNone(disputed["value"])
        self.assertEqual({e["value"] for e in disputed["evidence"]}, {"Troy", "Rome"})
        self.assertEqual(self.db.search("city")["results"], [])
        self.note("c", "I live in Nice.", 2)
        self.assertEqual(self.db.lookup("home.city")["value"], "Nice")

    def test_agreement_does_not_fabricate_a_dispute(self):
        self.note("a", "I live in Lima.")
        self.note("b", "I live in Lima.")
        self.assertEqual(self.db.lookup("home.city")["status"], "current")
        self.assertEqual(len(self.db.history("home.city")), 2)
        self.assertEqual(len(self.db.search("Lima")["results"]), 1)

    def test_expired_backfill_and_no_resurrection(self):
        self.note("a", "I live in Lima.")
        self.note("b", "I live in Rome.", 2, facts=[{"key": "home.city", "quote": "in Rome", "value": "Rome",
                                                     "expires_at": date(3)}])
        self.assertEqual(self.db.lookup("home.city", as_of=date(2))["value"], "Rome")
        self.assertEqual(self.db.lookup("home.city", as_of=date(3))["status"], "expired")
        self.assertEqual(self.db.search("city")["results"], [])

    def test_revocation_hides_all_earlier_values_in_current_view(self):
        self.note("a", "My name is Len.")
        self.note("b", "Please withdraw my name.", 2, facts=[{"key": "profile.name", "value": None, "quote": "withdraw my name"}])
        self.assertEqual(self.db.lookup("profile.name")["status"], "revoked")
        self.assertEqual(self.db.search("name")["results"], [])
        self.assertEqual(len(self.db.history("profile.name")), 2)

    def test_receipt_retry_concurrency(self):
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            receipts = list(pool.map(lambda _: self.note("same", "I live in Lima."), range(16)))
        self.assertEqual(sum(not r["replayed"] for r in receipts), 1)
        with self.assertRaises(Conflict):
            self.note("same", "I live in Reno.")
        self.assertEqual(self.db.status()["documents"], 1)

    def test_source_spans_are_unicode_codepoints(self):
        body = "🌎 — café\nI live in Québec."
        self.note("a", body)
        citation = self.db.lookup("home.city")["evidence"][0]
        self.assertEqual(body[citation["start"]:citation["stop"]], citation["quote"])
        self.assertEqual(citation["value"], "Québec")
        self.assertEqual(self.db.source("a")["text"], body)

    def test_unsupported_and_assistant_claims_do_not_become_facts(self):
        self.note("a", "I might move to Bern. I live in Lima on weekends. My name is Pat?")
        self.db.remember("b", "I live in Paris.", "assistant", role="assistant")
        self.assertEqual(self.db.status()["assertions"], 0)
        self.assertEqual(self.db.search("Paris", view="archive")["results"][0]["source"], "assistant")

    def test_statements_in_a_plain_document_are_archival_by_default(self):
        self.db.remember("a", "I live in Lisbon.", "attachment")
        self.assertEqual(self.db.lookup("home.city")["status"], "unknown")

    def test_invalid_source_fails_atomically(self):
        with self.assertRaises(ValueError):
            self.note("a", "My city is Cork", facts=[{"key": "home.city", "value": "Bern", "quote": "My city is Cork"}])
        self.assertEqual(self.db.status()["documents"], 0)

    def test_long_document_retains_early_and_late_passages(self):
        body = "tamarind\n" + "blue sky " * 10000 + "\nnectarine"
        self.db.remember("doc", body, "fixture")
        reopened = Ledger(self.path)
        self.assertEqual(reopened.source("doc")["text"], body)
        for term in ["tamarind", "nectarine"]:
            hit = reopened.search(term, view="archive")["results"][0]
            self.assertIn(term, hit["quote"])
            self.assertEqual(body[hit["start"]:hit["stop"]], hit["quote"])

    def test_restart_after_forced_exit(self):
        # First write commits; terminate the process inside the second write, after raw insertion.
        code = """
import os, sys
from recall_ledger import Ledger
import recall_ledger.store as store
db = Ledger(sys.argv[1])
db.remember('complete','I live in Lima.','chat',role='user')
def crash(body):
    os._exit(73)
store.chunks = crash
db.remember('half','I live in Cork.','chat',role='user')
"""
        child = subprocess.run([sys.executable, "-c", code, str(self.path)], capture_output=True)
        self.assertEqual(child.returncode, 73, child.stderr)
        self.assertEqual(self.db.status()["documents"], 1)
        self.assertIsNone(self.db.source("half"))
        self.assertEqual(self.db.lookup("home.city")["value"], "Lima")
        self.assertFalse(self.db.remember("half", "I live in Cork.", "chat", role="user")["replayed"])

    def test_scope_bound_at_open_and_tool_cannot_select_another(self):
        separate = Ledger(self.path.with_name("other.sqlite"))
        self.note("a", "I live in Tallinn.")
        self.assertEqual(separate.search("Tallinn", view="archive")["results"], [])
        with self.assertRaises(ValueError):
            dispatch(separate, "source", {"request_id": "a", "db": str(self.path)})

    def test_condition_specific_values_coexist(self):
        body = "Email at work, Signal after hours."
        self.note("p", body, facts=[{"key": "contact.work", "value": "Email", "quote": "Email at work"},
                                    {"key": "contact.after_hours", "value": "Signal", "quote": "Signal after hours"}])
        self.assertEqual(self.db.lookup("contact.work")["value"], "Email")
        self.assertEqual(self.db.lookup("contact.after_hours")["value"], "Signal")

    def test_file_export_roundtrip_preserves_times(self):
        self.note("a", "I live in Bogotá.")
        self.note("b", "I moved to Cork.", 2)
        rows = list(self.db.export())
        other = Ledger(self.path.with_name("copy.sqlite"))
        other.restore(rows)
        self.assertEqual(other.history("home.city"), self.db.history("home.city"))
        self.assertTrue(other.remember("a", "I live in Bogotá.", "chat/a", role="user", effective_at=date(1))["replayed"])
        with self.assertRaises(Conflict):
            other.restore(rows)

    def test_restore_failure_rolls_back_complete_batch(self):
        self.note("a", "I live in Rome.")
        rows = list(self.db.export())
        other = Ledger(self.path.with_name("copy.sqlite"))
        with self.assertRaises(ValueError):
            other.restore([*rows, {"broken": True}])
        self.assertEqual(other.status()["documents"], 0)

    def test_erase_clears_archive_and_receipts(self):
        self.note("a", "I live in Riga.")
        self.assertEqual(self.db.erase(), {"erased_documents": 1})
        self.assertIsNone(self.db.source("a"))
        self.assertEqual(self.db.search("Riga", view="archive")["results"], [])
        self.assertEqual(self.db.history("home.city"), [])
        self.assertFalse(self.note("a", "I live in Cork.")["replayed"])

    def test_lexical_limit_after_temporal_filter(self):
        for i in range(40):
            self.note(str(i), "I live in Hull.")
        self.note("latest", "I live in Galway.", 2)
        self.note("job", "My employer is Hull.")
        rows = self.db.search("Hull", limit=1)["results"]
        self.assertEqual(rows[0]["key"], "work.employer")

    def test_manual_review_roundtrip_keeps_original_time_and_offsets(self):
        initial = self.note("raw", "These days I call Malmö home.")
        self.assertEqual(self.db.pending()[0]["request_id"], "raw")
        facts = [{"key": "home.city", "value": "Malmö", "quote": "I call Malmö home"}]
        self.db.annotate("review-1", "raw", facts)
        self.assertTrue(self.db.annotate("review-1", "raw", facts)["replayed"])
        self.assertFalse(self.db.pending())
        self.assertEqual(self.db.lookup("home.city", known_at=initial["recorded_at"])["status"], "unknown")
        state = self.db.lookup("home.city", as_of=date(2))
        self.assertEqual(state["evidence"][0]["request_id"], "raw")
        self.assertEqual(state["value"], "Malmö")
        self.assertEqual(self.db.status()["passages"], 1)
        other = Ledger(self.path.with_name("restore.sqlite"))
        other.restore(self.db.export())
        self.assertEqual(other.lookup("home.city"), state)
        self.assertEqual(other.status(), self.db.status())

    def test_hybrid_resume_and_paraphrase(self):
        class Encoder:
            name = "test-semantic-space"
            def embed(self, texts):
                return [[1, 0] if "train" in t or "rail" in t else [0, 1] for t in texts]
        model = Encoder()
        self.db.embedder = model
        self.note("a", "I board the train at seven.")
        self.note("b", "Gardening happens at noon.")
        self.assertEqual(index_passages(self.db, model, 1)["new_vectors"], 2)
        self.assertEqual(index_passages(self.db, model)["new_vectors"], 0)
        self.assertEqual(self.db.search("rail", view="archive")["results"], [])
        hit = self.db.search("rail", view="hybrid_archive")["results"][0]
        self.assertEqual(hit["request_id"], "a")
        self.assertEqual(hit["ranks"], {"semantic": 1})
        self.note("c", "A train leaves at ten.")
        result = self.db.search("train", view="hybrid_archive")
        self.assertEqual((result["indexed_passages"], result["eligible_passages"]), (2, 3))
        self.db.erase()
        self.assertEqual(self.db.search("rail", view="hybrid_archive")["results"], [])

    def test_embedding_failure_keeps_raw_sources_and_batch_retriable(self):
        class Broken:
            name = "broken"
            def embed(self, texts):
                return [[1, 0], [float("nan"), 1]]
        self.note("a", "Streetcar.")
        self.note("b", "Ferry.")
        with self.assertRaises(ValueError):
            index_passages(self.db, Broken())
        self.assertEqual(self.db.status()["documents"], 2)
        with closing(sqlite3.connect(self.path)) as check:
            self.assertEqual(check.execute("SELECT count(*) FROM vectors").fetchone()[0], 0)

    def test_stdio_protocol_real_process(self):
        def message(i, method, **params):
            return json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        self.note("a", "My name is Jing.")
        lines = [message(1, "initialize", protocolVersion="2025-11-25"),
                 '{"jsonrpc":"2.0","method":"notifications/initialized"}', message(2, "tools/list"),
                 message(3, "tools/call", name="lookup", arguments={"key": "profile.name"})]
        result = subprocess.run([sys.executable, "-m", "recall_ledger", "--db", str(self.path), "mcp"],
                                input="\n".join(lines) + "\n", text=True, capture_output=True, check=True)
        init, listing, hit = map(json.loads, result.stdout.splitlines())
        self.assertIn("tools", init["result"]["capabilities"])
        self.assertTrue(any(t["name"] == "remember" for t in listing["result"]["tools"]))
        self.assertEqual(json.loads(hit["result"]["content"][0]["text"])["value"], "Jing")

    def test_http_entry_points(self):
        token = "a-long-synthetic-test-token"
        with http_server(self.db, 0, token) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def call(name, body, auth=token):
                    req = Request(f"http://127.0.0.1:{server.server_port}/v1/{name}",
                                  headers={"Authorization": "Bearer " + auth}, data=json.dumps(body).encode())
                    with urlopen(req, timeout=5) as response:
                        return json.load(response)
                body = {"source": "session", "request_id": "a", "role": "user", "text": "My name is Jing."}
                self.assertFalse(call("remember", body)["replayed"])
                self.assertTrue(call("remember", body)["replayed"])
                self.assertEqual(call("lookup", {"key": "profile.name"})["value"], "Jing")
                for name, args, key, code in [("status", {}, "bad", 401), ("erase", {}, token, 404),
                                               ("remember", {**body, "text": "my name changed"}, token, 409)]:
                    with self.assertRaises(HTTPError) as raised:
                        call(name, args, key)
                    self.assertEqual(raised.exception.code, code)
            finally:
                server.shutdown()
                thread.join()

"""Regressions for dated profiles and context assembled from different retrieval paths."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gravity_memory import Ledger
from gravity_memory.semantic import index_passages
from gravity_memory.service import dispatch, rpc, tool_list


def day(number):
    return f"2024-02-{number:02d}T12:00:00Z"


def profile_items(profile):
    return [fact for group in profile["groups"] for fact in group["items"]]


class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Ledger(Path(folder.name) / "account.sqlite")

    def turn(self, rid, text, date, **kwargs):
        return self.db.remember(rid, text, "conversation", role="user", effective_at=day(date), **kwargs)

    def test_cutoffs_and_historical_passage_survive_a_change(self):
        initial = self.turn("first", "I live in Kyoto.", 2)
        self.turn("move", "I moved to Accra.", 8)
        self.turn("late", "I live in Porto.", 1)
        for kwargs, expected in [({}, "Accra"), ({"as_of": day(5)}, "Kyoto"),
                                 ({"known_at": initial["recorded_at"]}, "Kyoto")]:
            context = self.db.context("Where do I live?", retrieval="full_text", **kwargs)
            self.assertEqual([f["value"] for f in context["facts"]], [expected])
            self.assertEqual([f["value"] for f in profile_items(context["profile"])], [expected])
            self.assertEqual(context["as_of"], context["profile"]["as_of"])
            if "known_at" in kwargs:
                self.assertEqual({p["request_id"] for p in context["passages"]}, {"first"})
        history = self.db.context("Porto", retrieval="full_text")
        self.assertEqual(history["facts"], [])
        self.assertEqual(history["passages"][0]["request_id"], "late")
        passage = history["passages"][0]
        original = self.db.source("late")["text"]
        self.assertEqual(original[passage["start"]:passage["stop"]], passage["quote"])

    def test_status_filter_does_not_resurrect_expired_or_disputed_facts(self):
        self.turn("city1", "I live in Kyoto.", 2)
        self.turn("city2", "I live in Milan.", 2)
        self.turn("base", "My base is Turin.", 1, facts=[{"key":"work.base","value":"Turin","quote":"Turin"}])
        self.turn("tour", "My base is Tokyo.", 3,
                  facts=[{"key":"work.base","value":"Tokyo","quote":"Tokyo","expires_at":day(10)}])
        self.turn("old", "My code is BLUE.", 2, facts=[{"key":"access.code","value":"BLUE","quote":"BLUE"}])
        self.turn("revoke", "Remove the code.", 3,
                  facts=[{"key":"access.code","value":None,"quote":"Remove the code."}])
        self.turn("new-name", "My name is Oona.", 9)
        profile = self.db.profile(as_of=day(10))
        self.assertEqual(profile["excluded"], {"disputed":1,"expired":1,"revoked":1})
        self.assertEqual([f["value"] for f in profile_items(profile)], ["Oona"])
        early = self.db.profile(as_of=day(4))
        self.assertEqual([f["value"] for f in profile_items(early)], ["Tokyo"])
        self.assertEqual(self.db.context("BLUE", retrieval="facts_only")["facts"], [])

    def test_subject_grouping_citation_and_deterministic_pagination(self):
        self.turn("family", "My name is Jane. My sister lives in Prague. My brother lives in Quito.", 2)
        self.turn("home", "I live in Lima.", 3)
        raw = self.turn("colour", "🎨 Paint chosen: café.", 4, facts=[])
        self.db.annotate("review", raw["request_id"], [{"key":"design.colour","value":"café","quote":"café"}])
        profile = self.db.profile(limit=2)
        self.assertEqual((profile["total"], profile["more"]), (5, True))
        self.assertEqual(profile_items(profile)[0]["key"], "profile.name")
        second = self.db.profile(limit=2, offset=2)
        third = self.db.profile(limit=2, offset=4)
        keys = [f["key"] for page in (profile,second,third) for f in profile_items(page)]
        self.assertEqual(len(set(keys)),5)
        self.assertFalse(third["more"])
        citation = self.db.lookup("design.colour")["evidence"][0]
        self.assertEqual(citation, next(f for f in profile_items(profile) if f["key"]=="design.colour")["evidence"][0])
        self.assertEqual(self.db.source(citation["request_id"])["text"][citation["start"]:citation["stop"]], "café")
        sister = self.db.context("Where does my sister live?", retrieval="facts_only")
        self.assertEqual([g["topic"] for g in sister["profile"]["groups"]], ["relation.sister"])
        self.assertEqual([r["value"] for r in sister["facts"]], ["Prague"])
        self.assertEqual(self.db.profile(subject="self")["total"], 3)
        self.assertEqual(self.db.profile(subject="relation.partner")["groups"], [])
        self.assertEqual(self.db.profile(offset=200)["total"], 5)

    def test_review_policy_applies_to_profile_and_matched_facts(self):
        self.turn("first", "I live in Kyoto.", 1)
        held = self.turn("pending", "Back in Europe for good.", 3)
        result = self.db.context("Where do I live?", retrieval="full_text", require_review=True)
        self.assertEqual((result["profile"]["total"], result["facts"]), (0, []))
        self.assertEqual(result["profile"]["excluded"], {"pending_review":1})
        self.db.annotate("dismiss", "pending", [])
        self.assertEqual(self.db.profile(require_review=True)["total"],1)
        before_review = self.db.profile(known_at=held["recorded_at"], require_review=True)
        self.assertEqual(before_review["excluded"], {"pending_review":1})

    def test_default_auto_and_lexical_have_truthful_routes(self):
        self.turn("ticket", "Rain on Friday.", 4)
        with patch("gravity_memory.models.installed", return_value=False):
            auto = self.db.context("rain")
        self.assertEqual(auto["route"],"full_text")
        self.assertIn("no local embedding",auto["reason"])
        literal = self.db.context("rain", retrieval="full_text")
        self.assertEqual(literal["passages"],auto["passages"])
        self.assertEqual(self.db.context("rain", retrieval="facts_only")["passages"],[])

    def test_context_keeps_compatible_vectors_and_facts_in_one_snapshot(self):
        self.turn("city", "I live in Faro.", 1)
        class Encoder:
            name = "fixture/one"
            def embed(self,texts):
                return [[1.,0.] for _ in texts]
        self.db.embedder = Encoder()
        index_passages(self.db, self.db.embedder)
        other = Ledger(self.db.path)
        def embed_while_erasing(texts):
            other.erase()
            return [[1.,0.] for _ in texts]
        with patch.object(self.db.embedder,"embed",side_effect=embed_while_erasing):
            ctx = self.db.context("Where do I live?")
        self.assertEqual((ctx["route"],ctx["indexed_passages"],ctx["eligible_passages"]),("hybrid",1,1))
        self.assertEqual([f["value"] for f in ctx["facts"]],["Faro"])
        self.assertEqual(ctx["passages"][0]["request_id"],"city")
        self.assertEqual(self.db.profile()["total"],0)

    def test_api_mcp_and_cli_are_wired_and_reject_invalid_inputs(self):
        self.turn("one","My name is Jin.",1)
        names = {t["name"] for t in tool_list()}
        self.assertTrue({"profile","context"}.issubset(names))
        result = rpc(self.db,{"jsonrpc":"2.0","id":1,"method":"tools/call",
                              "params":{"name":"context","arguments":{"query":"my name","retrieval":"facts_only"}}})
        self.assertEqual(json.loads(result["result"]["content"][0]["text"])["facts"][0]["value"],"Jin")
        call = subprocess.run([sys.executable,"-m","gravity_memory","--db",str(self.db.path),"call","profile"],
                              input="{}",text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(call.stdout)["total"],1)
        for name,args in [("profile",{"subject":"relation.*"}),("profile",{"subject":"nobody"}),
                          ("profile",{"limit":True}),("profile",{"offset":-1}),("profile",{"require_review":1}),
                          ("context",{"query":" "}),("context",{"query":"ok","profile_limit":101}),
                          ("context",{"query":"ok","as_of":"2025-01-01"}),("context",{"query":"ok","retrieval":"ml"})]:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                dispatch(self.db,name,args)

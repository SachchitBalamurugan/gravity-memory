import tempfile
import unittest
from pathlib import Path

from gravity_memory import Ledger
from gravity_memory.service import dispatch


def fact(key, word):
    return {"key": key, "value": word, "quote": word}


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.ledger = Ledger(Path(directory.name) / "workspace.sqlite")

    def remember(self, request_id, day, *facts):
        # Different originals can share a display label.
        self.ledger.remember(request_id, "Rome, Lima, Oslo, Eva, Blue.", "Account notes",
                             effective_at=f"2025-05-{day:02d}T00:00:00Z", facts=list(facts))

    def test_reviews_and_originals_share_witnesses_without_duplicate_links(self):
        city = fact("home.city", "Rome")
        team = fact("work.team", "Blue")
        self.remember("home.city", 1, city, city)
        self.ledger.annotate("approval", "home.city", [city, team])
        self.ledger.annotate("second-approval", "home.city", [team])
        self.remember("other", 2, team)
        self.ledger.annotate("corrected", "other", [fact("home.city", "Lima")])
        self.ledger.annotate("additional", "other", [fact("profile.name", "Eva")])
        self.remember("unrelated", 3, fact("work.office", "Oslo"))
        graph = dispatch(self.ledger, "connections", {"key": "home.city"})
        self.assertFalse(graph["truncated"])
        nodes = {n["id"]: n for n in graph["nodes"]}
        self.assertEqual(set(nodes), {"memory:home.city", "memory:work.team", "memory:profile.name",
                                     "source:home.city", "source:other"})
        self.assertEqual(len(nodes), len(graph["nodes"]))
        for n in nodes.values():
            if n["kind"] == "source":
                self.assertEqual(n["label"], self.ledger.source(n["request_id"])["source"])
        supports = {(e["from"], e["to"]) for e in graph["edges"] if e["type"] == "supports"}
        self.assertEqual(supports, {
            ("source:home.city", "memory:home.city"), ("source:home.city", "memory:work.team"),
            ("source:other", "memory:home.city"), ("source:other", "memory:work.team"),
            ("source:other", "memory:profile.name"),
        })
        shared = [e for e in graph["edges"] if e["type"] == "co_occurs"]
        self.assertEqual([e["from"] for e in shared], ["memory:home.city"] * 2)
        self.assertEqual([(e["to"], e["label"]) for e in shared],
                         [("memory:work.team", "2 shared sources in this view"),
                          ("memory:profile.name", "1 shared source in this view")])
        self.assertEqual(len(graph["edges"]), len(supports) + len(shared))

    def test_revoked_key_retains_its_history(self):
        self.remember("first", 1, fact("home.city", "Rome"), fact("work.base", "Oslo"))
        self.remember("revoke", 2, {"key": "home.city", "value": None, "quote": "Rome"})
        self.assertEqual(self.ledger.lookup("home.city")["status"], "revoked")
        graph = self.ledger.connections("home.city")
        self.assertEqual([n["request_id"] for n in graph["nodes"] if n["kind"] == "source"],
                         ["revoke", "first"])
        self.assertEqual([n["key"] for n in graph["nodes"] if n["kind"] == "memory"],
                         ["home.city", "work.base"])

    def test_source_and_key_bounds_have_witnesses_and_deterministic_ties(self):
        city = fact("home.city", "Rome")
        self.remember("late-arrival", 1, city, fact("work.old", "Blue"))
        self.remember("new-b", 4, city, fact("work.blue", "Blue"))
        self.remember("new-a", 4, city, fact("work.red", "Lima"), fact("work.blue", "Blue"),
                      fact("work.amber", "Rome"))
        graph = self.ledger.connections("home.city", limit=2)
        self.assertEqual(graph, self.ledger.connections("home.city", limit=2))
        self.assertTrue(graph["truncated"])
        self.assertEqual([n["request_id"] for n in graph["nodes"] if n["kind"] == "source"],
                         ["new-a", "new-b"])
        self.assertEqual([n["key"] for n in graph["nodes"] if n["kind"] == "memory"],
                         ["home.city", "work.blue", "work.amber"])
        self.assertTrue(self.ledger.connections("work.red", limit=1)["truncated"])
        self.assertFalse(self.ledger.connections("home.city", limit=4)["truncated"])

    def test_unknown_and_input_validation(self):
        empty = {"key": "home.city", "nodes": [], "edges": [], "truncated": False}
        self.remember("archived", 1)
        self.assertEqual(self.ledger.connections("home.city"), empty)
        for key, limit in [("bad key", 12), ("home.city", True), ("home.city", 0), ("home.city", 101)]:
            with self.subTest(key=key, limit=limit), self.assertRaises(ValueError):
                self.ledger.connections(key, limit=limit)

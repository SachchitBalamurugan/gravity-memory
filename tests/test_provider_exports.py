import tempfile
import unittest
from pathlib import Path

from gravity_memory import Ledger
from gravity_memory.service import dispatch


class ProviderExportTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.ledger = Ledger(Path(folder.name) / "imports.sqlite")
        self.document = {
            "id": "doc-1", "type": "text", "content": "I live in Kyoto.", "summary": "Prior address",
            "system": {"createdAt": "2025-10-01T01:00:00Z", "updatedAt": "2025-10-02T01:00:00Z"},
        }
        self.page = {"documents": [self.document], "chunks": [], "memories": [],
                     "pagination": {"currentPage": 1, "totalItems": 1, "nextCursor": None}}

    def load(self, provider, scope, page):
        return dispatch(self.ledger, "import_provider", {"provider": provider, "scope": scope, "payload": page})

    def test_v5_scoped_document_page_uses_system_time_and_full_text(self):
        export = {"namespace": "tenant:a", **self.page}
        self.assertEqual(self.load("supermemory", "tenant:a", export),
                         {"imported": 1, "replayed": 0, "outside_scope": 0})
        original = self.ledger.pending(role="document")[0]
        saved = self.ledger.source(original["request_id"])
        self.assertEqual((saved["source"], saved["text"], saved["effective_at"]),
                         ("supermemory/doc-1", "I live in Kyoto.", "2025-10-02T01:00:00.000000+00:00"))
        self.assertEqual(self.ledger.lookup("home.city")["status"], "unknown")
        self.assertEqual(self.load("supermemory", "tenant:a", export)["replayed"], 1)
        self.assertEqual(self.load("supermemory", "tenant:b", export),
                         {"imported": 0, "replayed": 0, "outside_scope": 1})
        edited = {**self.document, "content": "I moved to Roma.",
                  "system": {"createdAt": "2025-10-11T20:30:00Z"}}
        self.assertEqual(self.load("supermemory", "tenant:a", {**export, "documents": [edited]})["imported"], 1)
        self.assertEqual(self.ledger.source(original["request_id"])["text"], self.document["content"])

    def test_v5_requires_scope_and_scope_conflicts_never_import(self):
        with self.assertRaisesRegex(ValueError, "namespace"):
            self.load("supermemory", "tenant:a", self.page)
        for row_scope in [{"namespace": "tenant:b"}, {"containerTags": ["tenant:b"]},
                          {"containerTag": "tenant:b"}, {"containerTags": ["tenant:a"], "containerTag": "tenant:b"}]:
            with self.subTest(scope=row_scope):
                docs = [{**self.document, **row_scope}]
                result = self.load("supermemory", "tenant:a", {"namespace": "tenant:a", "documents": docs})
                self.assertEqual(result["outside_scope"], 1)
        for page in [{**self.page, "namespace": None},
                     {**self.page, "namespace": "tenant:a", "documents": [{**self.document, "namespace": None}]},
                     {"namespace": "tenant:a", "documents": [], "memories": [{"memory": "derived"}]}]:
            with self.subTest(page=page), self.assertRaises(ValueError):
                self.load("supermemory", "tenant:a", page)
        self.assertEqual(self.ledger.status()["documents"], 0)

    def test_metadata_only_or_invalid_revision_rolls_back_whole_page(self):
        for invalid in [{**self.document, "content": None},
                        {**self.document, "system": {"updatedAt": "2025-10-01"}},
                        {**self.document, "system": None}, {**self.document, "system": {}}]:
            with self.subTest(document=invalid), self.assertRaises(ValueError):
                self.load("supermemory", "a", {"namespace": "a", "documents": [self.document, invalid]})
            self.assertEqual(self.ledger.sources()["items"], [])

    def test_legacy_document_list_replays_as_v5_and_mem0_checks_top_level_user(self):
        legacy = {"id": self.document["id"], "content": self.document["content"],
                  "containerTags": ["a", "b"], "updatedAt": self.document["system"]["updatedAt"]}
        self.assertEqual(self.load("supermemory", "a", {"memories": [legacy]})["imported"], 1)
        self.assertEqual(self.load("supermemory", "a", {**self.page, "namespace": "a"})["replayed"], 1)

        mem = {"id": "m1", "memory": "Customer prefers email.", "created_at": "2025-10-01T03:00:00Z"}
        page = {"results": [mem, {**mem, "user_id": "b", "memory": "Other customer's detail."}],
                "count": 2, "next": None, "previous": None}
        with self.assertRaisesRegex(ValueError, "user_id"):
            self.load("mem0", "a", page)
        self.assertEqual(self.load("mem0", "a", {"user_id": "a", **page}),
                         {"imported": 1, "replayed": 0, "outside_scope": 1})
        # A row cannot override a page belonging to a different user.
        self.assertEqual(self.load("mem0", "a", {"user_id": "b", "results": [{**mem, "user_id": "a"}]}),
                         {"imported": 0, "replayed": 0, "outside_scope": 1})
        self.assertEqual(self.ledger.search("detail", view="archive")["results"], [])

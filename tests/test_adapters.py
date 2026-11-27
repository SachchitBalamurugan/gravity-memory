"""Wire-format checks with recorded API shapes. These fixtures do not measure vendor quality."""
import json
import unittest
from unittest.mock import patch
from io import BytesIO
from urllib.parse import urlsplit

from evals.run import Remote
from recall_ledger.review import local_chat
from recall_ledger.semantic import OllamaEmbedder


def response(payload):
    return BytesIO(json.dumps(payload).encode())


class AdapterContracts(unittest.TestCase):
    def test_mem0_oss_write_and_search(self):
        ops = []
        def handle(request, timeout):
            self.assertEqual(request.get_header("X-api-key"), "fixture-key")
            ops.append((urlsplit(request.full_url).path, json.loads(request.data)))
            return response({"results": [{"id": "001", "memory": "User lives in Reno"}]})
        with patch("evals.run.urlopen", handle):
            provider = Remote("http://localhost:8888", "test-user", "mem0-oss", "fixture-key")
            provider.add(0, "I live in Reno")
            self.assertEqual(provider.search("city"), ["User lives in Reno"])
        self.assertEqual(ops, [("/memories", {"messages": [{"role": "user", "content": "I live in Reno"}], "user_id": "test-user"}),
                               ("/search", {"query": "city", "filters": {"user_id": "test-user"}, "top_k": 3})])

    def test_supermemory_polls_before_scoring(self):
        ops = []
        outputs = iter([{"id": "doc-7", "status": "queued"}, {"id": "doc-7", "status": "done"},
                        {"results": [{"memory": "User moved to Reno"}]}])
        def handle(request, timeout):
            self.assertEqual(request.get_header("Authorization"), "Bearer sample")
            ops.append((urlsplit(request.full_url).path, json.loads(request.data) if request.data else None))
            return response(next(outputs))
        with patch("evals.run.urlopen", handle), patch("evals.run.time.sleep"):
            provider = Remote("http://localhost:3000", "scope-1", "supermemory", "sample")
            provider.add(4, "I moved to Reno.")
            self.assertEqual(provider.search("city"), ["User moved to Reno"])
        self.assertEqual(ops[0][1]["customId"], "scope-1-4")
        self.assertEqual([op[0] for op in ops], ["/v3/documents", "/v3/documents/doc-7", "/v4/search"])
        self.assertEqual(ops[2][1]["containerTag"], "scope-1")

    def test_ollama_protocol(self):
        def embed(request, timeout):
            self.assertEqual(urlsplit(request.full_url).hostname, "127.0.0.1")
            self.assertEqual(json.loads(request.data), {"model": "local-v1", "input": ["hello"], "truncate": False})
            return response({"embeddings": [[0.1, 0.2]], "model": "local-v1"})
        def chat(request, timeout):
            body = json.loads(request.data)
            self.assertFalse(body["stream"])
            self.assertEqual(body["format"]["type"], "object")
            return response({"message": {"content": '{"facts":[]}'}, "done": True})
        with patch("recall_ledger.semantic.urlopen", embed), patch("recall_ledger.review.urlopen", chat):
            self.assertEqual(OllamaEmbedder("local-v1").embed(["hello"]), [[0.1, 0.2]])
            self.assertEqual(local_chat("local-v1", "Extract", {}, {"type": "object"}), {"facts": []})

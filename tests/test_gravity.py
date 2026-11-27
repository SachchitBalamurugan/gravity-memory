import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from zipfile import ZipFile

from gravity_memory import Ledger, models
from gravity_memory.language import extract
from gravity_memory.runtime import Unavailable, route_review, runtime_error
from gravity_memory.semantic import index_passages
from gravity_memory.studio import studio_server


class NewWorkflows(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.enterContext(patch.dict(os.environ, {"GRAVITY_MODEL_DIR": str(self.root / "models")}))
        self.ledger = Ledger(self.root / "customer.sqlite")
        self.ledger.remember("source", "My preferred language is French.", "call", role="user")
        self.fields = {"profile.language": "Preferred language"}
        self.policy = {"preferred":"qwen-small", "fallback":True}
        self.fact = {"key":"profile.language", "value":"French","quote":"My preferred language is French."}

    def test_subject_paraphrase_and_exact_citations(self):
        note = "🎙️\nThese days I call Ghent home. My sister lives in Malmö."
        self.ledger.remember("family", note, "call", role="user")
        for key, query, value in [("home.city", "Where do I live?", "Ghent"),
                                  ("relation.sister.home.city", "Where does my sister live?", "Malmö")]:
            hit = self.ledger.search(query)["results"][0]
            self.assertEqual((hit["key"], hit["value"]), (key, value))
            proof = hit["evidence"][0]
            self.assertEqual(note[proof["start"]:proof["stop"]], proof["quote"])
        self.assertEqual(self.ledger.search("Where does my father live?")["results"], [])

    def test_conditional_and_partial_abbreviation_stay_unclassified(self):
        for text in ["I live in St. Louis.", "My sister lives in Salem on weekdays.", "I might live in Cairo."]:
            self.assertEqual(extract(text), [])

    def test_newer_family_statement_preserves_self(self):
        self.ledger.remember("self","I live in Oslo.","chat",role="user")
        self.ledger.remember("family","My partner lives in Leeds.","chat",role="user")
        self.assertEqual(self.ledger.lookup("home.city")["value"],"Oslo")
        self.assertEqual(self.ledger.lookup("relation.partner.home.city")["value"],"Leeds")

    def test_fallback_is_bounded_and_proposal_needs_confirmation(self):
        for failure in [Unavailable("runtime HTTP 429"), MemoryError("model load"), OSError("offline"),
                        {"facts":[{**self.fact, "value":"German"}]}]:
            def generate(model, *args):
                if model == "qwen-small":
                    if isinstance(failure, Exception):
                        raise failure
                    return failure
                return {"facts":[self.fact]}
            with patch.object(models,"installed",return_value=True):
                output = route_review(self.ledger,"source",self.fields,generate=generate,policy=self.policy)
            self.assertEqual([a["model"] for a in output["attempts"]],["qwen-small","smol-backup"])
            self.assertEqual(output["facts"],[self.fact])
            self.assertEqual(self.ledger.lookup("profile.language")["status"],"unknown")

    def test_native_capacity_responses_do_not_hide_configuration_errors(self):
        def error(code, body):
            return HTTPError("http://127.0.0.1",code,"error",{},io.BytesIO(body.encode()))
        for code,body in [(429,"rate"),(503,"loading"),(400,'{"type":"exceed_context_size_error"}')]:
            self.assertIsInstance(runtime_error(error(code,body)),Unavailable)
        for code in (400,401,403):
            self.assertIsInstance(runtime_error(error(code,"bad schema or authentication")),ValueError)

    def test_empty_proposal_is_an_abstention(self):
        with patch.object(models,"installed",return_value=True):
            r = route_review(self.ledger,"source",self.fields,generate=lambda *args: {"facts":[]},policy=self.policy)
        self.assertEqual(len(r["attempts"]),1)
        self.assertEqual(r["facts"],[])
        self.assertEqual(self.ledger.pending_count(),1)

    def test_opt_out_missing_models_and_nonretryable_input(self):
        with patch.object(models,"installed",return_value=False):
            r = route_review(self.ledger,"source",self.fields,policy={**self.policy,"fallback":False})
        self.assertEqual(r["attempts"],[{"model":"qwen-small","outcome":"not_installed"}])
        self.assertEqual(self.ledger.source("source")["text"],self.fact["quote"])
        with self.assertRaises(ValueError):
            route_review(self.ledger,"missing",self.fields,policy=self.policy)
        def invalid(*args):
            raise ValueError("authentication failed")
        with patch.object(models,"installed",return_value=True), self.assertRaisesRegex(ValueError,"authentication"):
            route_review(self.ledger,"source",self.fields,generate=invalid,policy=self.policy)

    def test_degraded_search_keeps_original_and_encoder_revision(self):
        class Encoder:
            name="space/v1"
            def embed(self,texts): return [[1,0] for _ in texts]
        self.ledger.embedder = Encoder()
        index_passages(self.ledger,self.ledger.embedder)
        self.assertEqual(self.ledger.search("French",view="auto_archive")["route"],"hybrid")
        self.ledger.embedder.name="space/v2"
        r = self.ledger.search("French",view="auto_archive")
        self.assertEqual((r["route"],r["results"][0]["request_id"]),("full_text","source"))
        self.ledger.embedder.name="space/v1"
        with patch.object(self.ledger.embedder,"embed",side_effect=Unavailable("capacity")):
            self.assertEqual(self.ledger.search("French",view="auto_archive")["route"],"full_text")

    def test_verified_bundle_rejects_changes_without_touching_installed_files(self):
        data = {"one":b"first","two":b"second"}
        row = {"id":"probe","revision":"v1","repo":"test/fixture","license":"Apache-2.0","task":"review","files":[(k,len(v),hashlib.sha256(v).hexdigest()) for k,v in data.items()]}
        with patch.dict(models.CATALOG,{"probe":row}):
            for key,value in data.items():
                path=models.asset("probe",key)
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(value)
            package=self.root/"valid.gmpack"
            models.pack("probe",package)
            with self.assertRaises(FileExistsError): models.pack("probe",package)
            with ZipFile(package) as src, ZipFile(self.root/"bad","w") as dst:
                for k in src.namelist(): dst.writestr(k,b"badbad" if k=="two" else src.read(k))
            with self.assertRaisesRegex(ValueError,"checksum"): models.unpack(self.root/"bad")
            for key,value in data.items(): self.assertEqual(models.asset("probe",key).read_bytes(),value)
            with patch.dict(os.environ,{"GRAVITY_MODEL_DIR":str(self.root/"fresh")}):
                self.assertEqual(models.unpack(package)["installed"],"probe")
                self.assertTrue(models.installed("probe"))
            with self.assertRaises(ValueError): models.unpack(self.root/"customer.sqlite")

    def test_legacy_export_opens_with_current_names(self):
        rows=list(self.ledger.export())
        rows[0]["format"]="recall-ledger/2"
        other=Ledger(self.root/"restored.sqlite")
        other.restore(rows)
        self.assertEqual(other.source("source"),self.ledger.source("source"))

    def test_studio_sessions_sample_isolation_and_full_pending_count(self):
        server,url,app=studio_server(self.ledger)
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(app.scratch.cleanup)
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join,3)
        self.addCleanup(server.shutdown)
        local=url.split("/open/")[0]
        http=build_opener(HTTPCookieProcessor())
        def call(scope,op,args,**headers):
            req=Request(local+"/studio/"+op,data=json.dumps(args).encode(),headers={"X-Workspace":scope,**headers})
            with http.open(req) as reply: return json.load(reply)
        with self.assertRaises(HTTPError) as caught: call("sample","overview",{})
        self.assertEqual(caught.exception.code,401)
        with http.open(url) as reply: self.assertIn(b"Gravity Memory",reply.read())
        with self.assertRaises(HTTPError) as caught: call("sample","overview",{},Origin="http://example.invalid")
        self.assertEqual(caught.exception.code,403)
        self.assertTrue(call("sample","library",{})["items"])
        self.assertFalse(call("personal","library",{})["items"])
        for i in range(101): self.ledger.remember(str(i),"Document evidence","doc")
        self.assertEqual(call("personal","overview",{})["pending"],102)
        with self.assertRaises(HTTPError): call("personal","import-pack",{})

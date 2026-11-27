import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gravity_memory import Ledger
from gravity_memory.runs import call, tracked
from gravity_memory.semantic import index_passages
from gravity_memory.store import Conflict, encoded


class AgentRuns(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Ledger(Path(self.temp.name) / "agents.sqlite")
        self.run = self.api("start_run", request_id="start", title="Support session")["run_id"]

    def api(self, op, *, actor="support", admin=False, trusted=False, **args):
        return call(self.ledger,op,args,actor=actor,admin=admin,trusted=trusted)

    def recall(self, req, query, **args):
        return self.api("recall",request_id=req,run_id=self.run,query=query,**args)

    def put(self, rid, text, **kwargs):
        return self.ledger.remember(rid,text,rid,**kwargs)

    def test_original_vs_current_evidence_and_trace(self):
        self.put("before","I live in Paris.",role="user")
        old = self.recall("r1","Where do I live?")
        self.assertEqual(old["items"][0]["value"],"Paris")
        self.put("after","I moved to Porto.",role="user")
        compare = self.api("compare_trace",trace_id=old["trace_id"])
        self.assertEqual(compare["recorded"],old)
        self.assertEqual(compare["current"]["items"][0]["value"],"Porto")
        self.assertNotIn("Paris",encoded(compare["current"]))
        trace = self.api("trace",trace_id=compare["current"]["trace_id"])
        self.assertEqual(trace["output"],compare["current"])
        self.assertTrue(any(d["source_id"]=="before" and d["reason"]=="superseded" for d in trace["diagnostics"]["excluded"]))
        self.assertEqual(self.recall("r1","Where do I live?"),old)

    def test_read_only_caller_can_recall_without_starting_a_run(self):
        self.put("fact","I live in Kyoto.",role="user")
        answer=self.api("recall",request_id="q",actor="reader",query="Where do I live?")
        self.assertEqual(answer["items"][0]["value"],"Kyoto")
        self.assertIsNone(self.api("trace",trace_id=answer["trace_id"])["run_id"])
        self.assertEqual(self.api("runs",agent="reader")["items"],[])

    def test_budget_multibyte_and_citations(self):
        value = "京都🌜"*15
        self.put("original","City: "+value,facts=[{"key":"home.city","value":value,"quote":value}])
        large = self.recall("l","city",budget_bytes=4096)
        item = large["items"][0]
        self.assertEqual(self.ledger.source("original")["text"][item["start"]:item["stop"]],item["quote"])
        for n in [256,370,480,700,1024]:
            r = self.recall(str(n),"city",budget_bytes=n)
            self.assertLessEqual(len(json.dumps(r).encode()),n)
            self.assertEqual(len(encoded(r).encode()),r["bytes"])
        self.assertEqual(self.recall("none","xylophonist",retrieval="facts_only")["status"],"abstained")

    def test_question_subject_outweighs_a_qualifying_clause(self):
        cases = [
            ("Which mailbox receives requests from the active team?", "routing.mailbox", "triage inbox",
             "Active team receives requests", "team.name", "team"),
            ("What timeout applies to the active worker?", "client.timeout", "45 seconds",
             "The active worker is Cedar", "worker.name", "Cedar"),
            ("What is the export format for the active exporter?", "output.format", "NDJSON",
             "The active exporter is Quartz", "exporter.name", "Quartz"),
        ]
        for i,(question,key,value,qualifier,other_key,other_value) in enumerate(cases):
            ledger=Ledger(Path(self.temp.name)/f"holdout-{i}.sqlite")
            ledger.remember("decision",value,"decision",facts=[{"key":key,"value":value,"quote":value}])
            ledger.remember("qualifier",qualifier,"qualifier",facts=[{"key":other_key,"value":other_value,"quote":qualifier}])
            run=call(ledger,"start_run",{"request_id":"new","title":"Handoff"},actor="agent")["run_id"]
            answer=call(ledger,"recall",{"request_id":"read","run_id":run,"query":question,"limit":1},actor="agent")
            with self.subTest(question=question):
                self.assertEqual(answer["items"][0]["source_id"],"decision")

    def test_agent_reported_telemetry_is_bounded_and_distinct_from_server_latency(self):
        metrics={"tool":"deploy","model":"local/test","route":"fallback","fallback_reason":"capacity",
                 "latency_ms":1200,"input_tokens":30,"output_tokens":5,"cost_usd":0.001}
        note=self.api("run_note",request_id="tool",run_id=self.run,text="Tool completed",telemetry=metrics)
        trace=self.api("trace",trace_id=note["trace_id"])
        self.assertEqual(trace["diagnostics"]["reported_by_agent"],metrics)
        self.assertEqual(trace["input"]["telemetry"],metrics)
        for bad in ({"input_tokens":-1},{"latency_ms":float("inf")},{"api_key":"never record this"}):
            with self.assertRaises(ValueError):
                self.api("outcome",request_id="bad",run_id=self.run,success=True,detail="bad",telemetry=bad)
        self.assertEqual(self.api("get_policy")["trace_count"],2)

    def test_dispute_and_assistant_claims_do_not_become_actionable(self):
        for city in ["Milan","Rome"]:
            self.put(city,"I live in "+city+".",role="user",effective_at="2020-01-01T00:00:00Z")
        self.put("robot","I will move the customer to Havana.",role="assistant")
        self.assertEqual(self.recall("c","Where do I live?")["reason"],"conflicting_evidence")
        self.assertEqual(self.recall("a","Havana")["items"],[])

    def test_filter_before_ranking_in_every_route(self):
        for i in range(13):
            self.put("other"+str(i),"Signal release "+str(i),metadata={"team":"sales","priority":8},
                     facts=[{"key":"release.channel","value":"Signal","quote":"Signal"}])
        self.put("wanted","Quartz release checklist",metadata={"team":"eng","priority":9},
                 facts=[{"key":"release.channel","value":"Quartz","quote":"Quartz"}])
        self.ledger.annotate("review","wanted",[{"key":"project.name","value":"Quartz","quote":"Quartz"}])
        where = {"operator":"and","operands":[{"field":"metadata.team","operator":"eq","value":"eng"},
                 {"operator":"or","operands":[{"field":"metadata.priority","operator":"gte","value":8},
                                              {"field":"source","operator":"eq","value":"absent"}]}]}
        class Embedder:
            name = "fixed"
            def embed(self, texts):
                return [[1,0] for _ in texts]
        self.ledger.embedder = Embedder()
        index_passages(self.ledger,self.ledger.embedder)
        result = self.recall("filter","release",filter=where,limit=2,include_profile=True)
        self.assertEqual(result["route"],"hybrid")
        self.assertEqual({item["source_id"] for item in result["items"]},{"wanted"})
        self.assertEqual(self.ledger.search("release",view="hybrid_archive",filter=where,limit=1)["results"][0]["request_id"],"wanted")
        graph = self.ledger.connections("release.channel",filter=where)
        self.assertEqual([n["request_id"] for n in graph["nodes"] if n["kind"]=="source"],["wanted"])
        self.assertNotIn("Signal",encoded(self.ledger.profile(filter=where)))
        self.assertTrue(any(x["request_id"]!="wanted" for x in self.ledger.search("release",view="archive")["results"]))
        with self.assertRaises(ValueError):
            self.recall("bad","release",filter={"field":"random.path","operator":"eq","value":"1"})

    def test_retries_concurrency_run_ownership_and_handoff(self):
        args={"request_id":"note","run_id":self.run,"text":"Use the maintenance window"}
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _: self.api("run_note",**args),range(16)))
        self.assertEqual(len({r["trace_id"] for r in results}),1)
        with self.assertRaises(Conflict):
            self.api("run_note",**{**args,"text":"Different"})
        with self.assertRaises(ValueError):
            self.api("run_note",actor="second",**args)
        self.api("handoff",run_id=self.run,request_id="handoff",summary="Run the migration on Friday")
        with self.assertRaises(Conflict):
            self.recall("after-close","Friday")
        new=self.api("start_run",actor="second",request_id="resume",title="Continue",parent_run_id=self.run)
        self.assertEqual(new["handoff"],"Run the migration on Friday")
        self.assertEqual(self.api("run_detail",run_id=new["run_id"])["run"]["actor"],"second")

    def lesson(self, actor, run_id, source, *, success=True, trusted=False, key="release.procedure", value="Canary"):
        return self.api("outcome",actor=actor,trusted=trusted,run_id=run_id,request_id="out-"+source,
                        success=success,detail="Deployment result",lesson={"type":"procedure","key":key,
                        "value":value,"source_id":source})["lesson"]

    def test_learning_requires_independent_success_and_retracts(self):
        self.api("policy",admin=True,auto_types=["procedure"])
        other = self.api("start_run",actor="deployer",request_id="start",title="Deploy")["run_id"]
        for rid,text in [("one","Release via Canary."),("two","Release via Canary."),("three","Canary passed verification.")]:
            self.put(rid,text)
        a=self.lesson("support",self.run,"one")
        self.assertEqual(a["status"],"pending")
        self.assertEqual(self.lesson("deployer",other,"two")["status"],"pending")
        approved=self.lesson("deployer",other,"three")
        self.assertEqual(approved["status"],"approved")
        self.assertEqual(self.ledger.lookup("release.procedure")["value"],"Canary")
        with self.assertRaises(PermissionError):
            self.api("review_lesson",lesson_id=a["lesson_id"],decision="retract")
        self.api("review_lesson",admin=True,lesson_id=a["lesson_id"],decision="retract")
        self.assertEqual(self.recall("after","Canary")["items"],[])
        self.api("erase_source",admin=True,request_id="three")
        self.assertEqual(self.ledger.history("release.procedure"),[])

    def test_trusted_outcomes_cannot_silently_replace_confirmed_or_failed_lessons(self):
        self.api("policy",admin=True,auto_types=["procedure"])
        self.put("handbook","Use Stable",facts=[{"key":"release.procedure","value":"Stable","quote":"Stable"}])
        self.put("report","Use Canary.")
        l=self.lesson("support",self.run,"report",trusted=True)
        self.assertEqual(l["status"],"pending")
        self.assertEqual(self.ledger.lookup("release.procedure")["value"],"Stable")
        self.api("review_lesson",admin=True,lesson_id=l["lesson_id"],decision="approve")
        self.assertEqual(self.ledger.lookup("release.procedure")["value"],"Canary")
        self.put("unrelated","Another Canary example")
        self.assertEqual(self.lesson("support",self.run,"unrelated",success=False,trusted=True,
                                    key="release.failure")["status"],"pending")
        self.put("signed","Successful Canary test")
        self.assertEqual(self.lesson("support",self.run,"signed",trusted=True,key="project.setting")["status"],"approved")
        copy=Ledger(Path(self.temp.name)/"copy.sqlite")
        copy.restore(self.ledger.export())
        self.assertEqual(copy.history("release.procedure"),self.ledger.history("release.procedure"))

    def test_erasure_removes_trace_contents_descendant_handoffs_and_learned_rows(self):
        self.put("sensitive","Confidential Canary")
        proposal=self.lesson("support",self.run,"sensitive")
        self.api("review_lesson",admin=True,lesson_id=proposal["lesson_id"],decision="approve")
        recall=self.recall("old","Canary")
        self.api("handoff",run_id=self.run,request_id="done",summary="Continue with Confidential Canary")
        new=self.api("start_run",request_id="next",title="Canary handoff",parent_run_id=self.run)["run_id"]
        self.api("run_note",run_id=new,request_id="n",text="Canary again")
        with self.assertRaises(PermissionError):
            self.api("erase_source",request_id="sensitive")
        self.api("erase_source",admin=True,request_id="sensitive")
        self.assertEqual(self.ledger.library()["items"],[])
        self.assertEqual(self.api("lessons")["items"],[])
        self.assertFalse(self.api("run_detail",run_id=new)["events"])
        self.assertNotIn("Canary",encoded(self.api("runs")))
        with self.assertRaises(ValueError):
            self.api("trace",trace_id=recall["trace_id"])
        self.assertEqual(self.recall_in(new,"now","Canary")["items"],[])
        with self.assertRaises(Conflict):
            self.recall("old","Canary")

    def recall_in(self, run, req, query):
        return self.api("recall",request_id=req,run_id=run,query=query)

    def test_policy_retention_and_full_request_transaction(self):
        tracked(self.ledger,"remember",{"request_id":"plain","text":"Paperclip"},actor="ops",
                invoke=lambda:self.put("plain","Paperclip"))
        new=self.api("activity",days=7)
        self.assertEqual((new["writes"],new["reads"]),(2,0))
        self.assertEqual(len(new["days"]),7)
        self.assertEqual(self.api("get_policy")["retention_days"],None)
        old=self.recall("old","Paperclip")
        with self.ledger._tx(write=True) as db:
            db.execute("UPDATE traces SET at='2000-01-01' WHERE trace_id=?",(old["trace_id"],))
        self.api("policy",admin=True,retention_days=7)
        with self.assertRaises(ValueError):
            self.api("trace",trace_id=old["trace_id"])
        self.api("erase_source",admin=True,request_id="plain")
        self.assertEqual(self.api("run_detail",run_id=self.run)["run"]["title"],"Erased run")
        with self.ledger._tx() as db:
            self.assertFalse(db.execute("SELECT 1 FROM traces WHERE input LIKE '%Paperclip%' OR output LIKE '%Paperclip%'").fetchone())
        def failing():
            self.put("partial","Rolled back")
            raise ValueError("invalid second item")
        with self.assertRaises(ValueError):
            tracked(self.ledger,"remember",{"request_id":"partial"},actor="ops",invoke=failing)
        self.assertIsNone(self.ledger.source("partial"))
        with self.ledger._tx() as db:
            self.assertTrue(db.execute("SELECT 1 FROM traces WHERE json_extract(output,'$.error')='invalid second item'").fetchone())
        self.ledger.erase()
        self.assertFalse(self.api("runs")["items"])
        self.assertEqual(self.api("get_policy")["trace_count"],0)

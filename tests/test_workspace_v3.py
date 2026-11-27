"""Customer journeys spanning the optional connector, context and correction paths."""
import base64
from contextlib import closing
from hashlib import sha1
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from gravity_memory import Conflict, Ledger, github_sources
from gravity_memory.language import extract
from gravity_memory.service import dispatch


class WorkspaceRelease(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.db = Ledger(self.folder / "memory.sqlite")
        self.cfg = {"repository":"example/docs","path":"guides/Start here.md","ref":"release/v1"}

    def test_conditions_preserve_general_preference_and_cite_the_source(self):
        text = "Please contact me via email. On call I prefer PagerDuty. Outside work please use Matrix."
        self.db.remember("chat",text,"support",role="user")
        for query,key,value in [
            ("How do I want to be contacted when on call?","contact.channel.on_call","PagerDuty"),
            ("How should I be contacted outside work?","contact.channel.outside_work","Matrix"),
            ("How can you contact me?","contact.channel","email"),
            ("contact.channel.on_call","contact.channel.on_call","PagerDuty")]:
            facts = self.db.context(query,retrieval="facts_only")["facts"]
            self.assertEqual([(f["key"],f["value"]) for f in facts],[(key,value)])
            ev=facts[0]["evidence"][0]
            self.assertEqual(text[ev["start"]:ev["stop"]],ev["quote"])
        self.assertFalse(self.db.search("How should I be contacted when I'm not on call?")["results"])
        for phrase in ["On call I prefer decaf.", "My partner prefers Slack on call.",
                       "Please contact me via Signal on call on weekdays."]:
            self.assertEqual(extract(phrase),[])

    def test_retire_preserves_historical_read_and_replays(self):
        self.db.remember("first","I live in York.","call",role="user",effective_at="2020-01-01T00:00:00Z")
        body={"key":"home.city","request_id":"stop","reason":"Account closure confirmed.",
              "effective_at":"2021-01-01T00:00:00Z"}
        self.assertFalse(dispatch(self.db,"retire",body)["replayed"])
        self.assertTrue(dispatch(self.db,"retire",body)["replayed"])
        state=self.db.lookup("home.city")
        self.assertEqual((state["status"],state["evidence"][0]["quote"]),("revoked",body["reason"]))
        self.assertEqual(self.db.lookup("home.city",as_of="2020-02-01T00:00:00Z")["value"],"York")
        with self.assertRaises(Conflict): dispatch(self.db,"retire",{**body,"reason":"Different action"})
        with self.assertRaises(ValueError): dispatch(self.db,"retire",{**body,"key":"missing.key"})

    def test_github_fetch_checks_blob_and_url(self):
        raw="βeta\n".encode()
        blob=sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()
        payload={"content":base64.b64encode(raw).decode(),"sha":blob,"type":"file","encoding":"base64","size":len(raw)}
        with patch.object(github_sources,"urlopen",return_value=BytesIO(json.dumps(payload).encode())) as req:
            sha,text=github_sources.fetch_file(self.cfg)
            self.assertEqual((sha,text),(blob,"βeta\n"))
            self.assertEqual(req.call_args.args[0].full_url,"https://api.github.com/repos/example/docs/contents/guides/Start%20here.md?ref=release%2Fv1")
        with patch.object(github_sources,"urlopen",return_value=BytesIO(json.dumps({**payload,"sha":"0"*40}).encode())):
            with self.assertRaisesRegex(ValueError,"revision"): github_sources.fetch_file(self.cfg)
        for bad in [{"repository":"https://example.com"},{"path":"../../a.md"},{"path":"image.jpg"},{"ref":"x?file=y"}]:
            with self.assertRaises(ValueError): github_sources.selection(**{**self.cfg,**bad})

    def test_change_repeat_disconnect_and_failure(self):
        def revision(config): return "a"*40,"The operator is Rhea."
        first=github_sources.connect(self.db,**self.cfg,fetch=revision)
        ident=first["connection"]["id"]
        repeat=github_sources.sync(self.db,ident,fetch=revision)
        self.assertFalse(repeat["imported"])
        def unavailable(config): raise ValueError("temporarily unavailable")
        with self.assertRaises(ValueError): github_sources.sync(self.db,ident,fetch=unavailable)
        self.assertEqual(github_sources.list_files(self.db)["items"][0]["blob_sha"],"a"*40)
        next_version=github_sources.sync(self.db,ident,fetch=lambda c:("b"*40,"The operator is Sam."))
        self.assertTrue(next_version["imported"])
        self.assertEqual(self.db.source(first["request_id"])["text"],"The operator is Rhea.")
        self.assertEqual(self.db.source(next_version["request_id"])["text"],"The operator is Sam.")
        self.assertTrue(github_sources.disconnect(self.db,ident)["disconnected"])
        self.assertEqual(self.db.status()["documents"],2)
        self.assertFalse(github_sources.list_files(self.db)["items"])
        with self.assertRaises(ValueError): github_sources.sync(self.db,ident,fetch=revision)

    def test_disconnect_during_sync_cannot_resurrect_source(self):
        first=github_sources.connect(self.db,**self.cfg,fetch=lambda c:("a"*40,"First"))
        ident=first["connection"]["id"]
        def remote(config):
            github_sources.disconnect(self.db,ident)
            return "b"*40,"Second"
        with self.assertRaises(Conflict): github_sources.sync(self.db,ident,fetch=remote)
        self.assertFalse(github_sources.list_files(self.db)["items"])
        self.assertEqual(self.db.status()["documents"],1)

    def test_existing_v1_database_and_full_erase(self):
        self.db.remember("old","I live in Prague.","old",role="user")
        with closing(sqlite3.connect(self.db.path)) as db:
            db.execute("DROP TABLE github_files")
            db.execute("PRAGMA user_version=1")
        migrated=Ledger(self.db.path)
        self.assertEqual(migrated.lookup("home.city")["value"],"Prague")
        github_sources.connect(migrated,**self.cfg,fetch=lambda c:("c"*40,"Contents"))
        migrated.erase()
        self.assertFalse(github_sources.list_files(migrated)["items"])

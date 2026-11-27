"""Shared service contracts exercised through real HTTP, SQLite, SDKs and stdio."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from gravity_memory.agent_cli import config, smoke
from gravity_memory.client import GravityClient
from gravity_memory.store import Ledger
from gravity_memory.team_cli import exclusive, transfer
from gravity_memory.team_registry import Registry
from gravity_memory.team_server import team_server

ROOT = Path(__file__).resolve().parents[1]


class TeamTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.reg = Registry(self.root/"active")
        self.reg.initialize("owner", "disposable-test-password")
        self.server = team_server(self.reg, port=0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join()
        self.addCleanup(stop)
        self.url = "http://127.0.0.1:"+str(self.server.server_port)
        self.cookie = self.login("owner","disposable-test-password")
        self.send("/admin/create_namespace", {"id":"acme","name":"Acme"}, self.cookie)
        self.key = self.issue("work", ["read","write","feedback"])["key"]
        self.client = GravityClient(self.url, "acme", self.key)

    def send(self, route, args=None, cookie=None, headers=None):
        request = Request(self.url+route, data=json.dumps(args).encode() if args is not None else None,
                          headers={"Content-Type":"application/json", **({"Cookie":cookie} if cookie else {}), **(headers or {})})
        with urlopen(request, timeout=15) as response:
            return json.load(response)

    def login(self, user, password):
        req = Request(self.url+"/auth/login", data=json.dumps({"user":user,"password":password}).encode(),
                      headers={"Content-Type":"application/json"})
        with urlopen(req, timeout=10) as response:
            return response.headers["Set-Cookie"].split(";",1)[0]

    def issue(self,name,permissions):
        return self.send("/admin/issue_key", {"namespace":"acme","name":name,"permissions":permissions}, self.cookie)

    def test_isolation_permissions_logout_and_trace(self):
        self.assertFalse(self.send("/session")["authenticated"])
        user = {"user":"tester","password":"another-sample-password"}
        self.send("/admin/create_user",user,self.cookie)
        viewer = self.login(**user)
        self.assertEqual(self.send("/session",cookie=viewer)["namespaces"], [])
        grant = {"user":"tester","namespace":"acme","role":"viewer"}
        self.send("/admin/grant",grant,self.cookie)
        self.assertEqual([r["id"] for r in self.send("/session",cookie=viewer)["namespaces"]], ["acme"])
        with self.assertRaises(HTTPError) as denied:
            self.send("/studio/start_run", {"request_id":"no","title":"denied"}, viewer, {"X-Workspace":"acme"})
        self.assertEqual(denied.exception.code,403)
        with self.assertRaisesRegex(ValueError,"403"):
            GravityClient(self.url,"personal",self.key).call("activity")
        self.client.call("remember", {"request_id":"x","source":"Test","text":"The region is Tokyo."})
        activity = self.send("/session",cookie=viewer)["namespace_activity"]
        self.assertEqual(activity[0]["requests"], 1)
        id_key = self.issue("observer", ["read"])
        reader = GravityClient(self.url, "acme", id_key["key"])
        self.assertEqual(reader.call("source",{"request_id":"x"})["text"],"The region is Tokyo.")
        self.assertEqual(reader.recall(None, "region")["items"][0]["source_id"], "x")
        self.assertNotIn("remember",[item["name"] for item in reader.call("tools")["tools"]])
        with self.assertRaisesRegex(ValueError,"403"):
            reader.call("policy", {"auto_types":["preference"]})
        self.send("/admin/revoke_key",{"id":id_key["id"]},self.cookie)
        with self.assertRaisesRegex(ValueError,"401"):
            reader.call("status")
        with self.assertRaises(HTTPError) as foreign:
            self.send("/admin/list_keys",{"namespace":"acme"}, self.cookie, {"Origin":"https://another.invalid"})
        self.assertEqual(foreign.exception.code,403)
        self.send("/auth/logout",{},viewer)
        self.assertFalse(self.send("/session",cookie=viewer)["authenticated"])

    def test_restore_migrate_and_retry(self):
        old = Ledger(self.root/"local.sqlite")
        old.remember("r","The owner is Dani.","Original")
        self.reg.create_space("imported","Private import",old.path)
        self.assertEqual(self.reg.ledger("imported").source("r")["text"],"The owner is Dani.")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _:self.client.start_run("Same run",request_id="repeat"),range(4)))
        self.assertEqual(len({r["run_id"] for r in results}),1)
        with self.assertRaisesRegex(ValueError,"409"):
            self.client.start_run("Conflicting retry",request_id="repeat")
        backup = self.root/"backup"
        transfer(self.reg.root, backup)
        restored = self.root/"restored"
        transfer(backup,restored,restore=True)
        copy = Registry(restored)
        self.assertEqual(copy.ledger("imported").source("r")["source"],"Original")
        self.assertTrue(copy.authenticate(self.key,agent=True)["agent"])
        with copy.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sessions").fetchone()[0],0)
        with exclusive(self.root/"lock"):
            with self.assertRaisesRegex(ValueError,"stop the team"):
                with exclusive(self.root/"lock"):
                    pass
        with (backup/"registry.sqlite").open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ValueError,"checksum"):
            transfer(backup,self.root/"invalid",restore=True)
        self.assertFalse((self.root/"invalid").exists())

    def test_sdk_examples_and_four_stdio_configurations(self):
        verified = smoke(self.client)
        self.assertTrue(verified["verified"])
        events = self.client.call("run_detail", {"run_id": verified["run_id"]})["events"]
        self.assertIn("remember", [event["op"] for event in events])
        with self.assertRaisesRegex(ValueError, "closed"):
            self.client.call("remember", {"run_id": verified["run_id"], "request_id": "closed", "source": "Test", "text": "Late event"})
        env = {**os.environ, "GRAVITY_URL":self.url,"GRAVITY_NAMESPACE":"acme","GRAVITY_KEY":self.key,
               "GRAVITY_PEER_KEY":self.issue("peer",["read","write","feedback"])["key"],"PYTHONPATH":str(ROOT)}
        for name in ("claude","codex","cursor","opencode"):
            settings = config(name)
            if name == "codex":
                block = tomllib.loads(settings)["mcp_servers"]["gravity-memory"]
            elif name == "opencode":
                block = settings["mcp"]["servers"]["gravity-memory"]
            else:
                block = settings["mcpServers"]["gravity-memory"]
            args = block["command"] if name == "opencode" else [block["command"], *block["args"]]
            frames = [("initialize", {"protocolVersion":"2025-11-25"}), ("tools/list",{}),
                      ("tools/call",{"name":"start_run","arguments":{"title":name,"request_id":"mcp-"+name}})]
            wire = "".join(json.dumps({"jsonrpc":"2.0","id":i,"method":m,"params":p})+"\n" for i,(m,p) in enumerate(frames,1))
            proc = subprocess.run(args, cwd=self.root, env=env, input=wire, text=True, capture_output=True, timeout=40, check=True)
            handshake, tools, run = map(json.loads, proc.stdout.splitlines())
            self.assertEqual(handshake["result"]["serverInfo"]["version"],"0.4.0")
            self.assertIn("recall", [tool["name"] for tool in tools["result"]["tools"]])
            self.assertTrue(json.loads(run["result"]["content"][0]["text"])["run_id"])
        subprocess.run([sys.executable, str(ROOT/"examples"/"customer_correction.py")],cwd=self.root,env=env,
                       timeout=30, capture_output=True, check=True)
        if shutil.which("node"):
            subprocess.run(["node",str(ROOT/"examples"/"coding-handoff.mjs")],cwd=self.root,env=env,
                           timeout=30, capture_output=True, check=True)

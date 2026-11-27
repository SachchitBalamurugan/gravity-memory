import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

from gravity_memory import Ledger
from gravity_memory import connector_providers as providers
from gravity_memory import connectors

ROOT_PAGE = "11111111-1111-4111-8111-111111111111"
CHILD_PAGE = "22222222-2222-4222-8222-222222222222"


class Providers(unittest.TestCase):
    def test_drive_desktop_consent_pkce_and_loopback_state(self):
        from scripts import drive_oauth
        seen = {}
        pending = []
        def visit(url):
            seen.update(parse_qs(urlsplit(url).query))
            def consent():
                callback = seen["redirect_uri"][0]
                try:
                    urlopen(callback + "?code=fake&state=wrong", timeout=3).read()
                except HTTPError as exc:
                    seen["invalid_status"] = exc.code
                urlopen(callback + "?" + urlencode({"code":"okay","state":seen["state"][0]}), timeout=3).read()
            thread = threading.Thread(target=consent,daemon=True)
            thread.start()
            pending.append(thread)
            return True
        def exchange(request, timeout):
            fields = parse_qs(request.data.decode())
            verifier = fields["code_verifier"][0].encode()
            self.assertEqual(base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).decode().rstrip("="),
                             seen["code_challenge"][0])
            self.assertEqual(fields["redirect_uri"],seen["redirect_uri"])
            self.assertEqual(fields["code"],["okay"])
            return io.BytesIO(b'{"refresh_token":"offline-credential"}')
        with patch.object(drive_oauth.webbrowser,"open",side_effect=visit), patch.object(drive_oauth,"urlopen",side_effect=exchange), patch("builtins.print"):
            refresh = drive_oauth.authorize({"client_id":"test","client_secret":"issuer"}, wait_seconds=5)
        for thread in pending:
            thread.join(5)
        self.assertEqual(refresh,"offline-credential")
        self.assertEqual(seen["invalid_status"],400)
        self.assertEqual(seen["access_type"],["offline"])

    def test_github_app_jwt_signature_and_installation_exchange(self):
        openssl = shutil.which("openssl")
        if not openssl and os.name == "nt":
            candidate = Path(os.environ.get("ProgramFiles", "")) / "Git/usr/bin/openssl.exe"
            openssl = str(candidate) if candidate.is_file() else None
        if not openssl:
            self.skipTest("OpenSSL is needed to verify the GitHub App signing path")
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "app.pem"
            subprocess.run([openssl, "genpkey", "-algorithm", "RSA","-pkeyopt", "rsa_keygen_bits:2048",
                            "-out", str(key)], check=True, capture_output=True)
            def b64(s):
                return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
            class Net:
                def request(_, url, headers, body):
                    self.assertTrue(url.endswith("/app/installations/42/access_tokens"))
                    self.assertEqual(body, {"permissions":{"contents":"read"}})
                    token = headers["Authorization"].removeprefix("Bearer ")
                    h, p, s = token.split(".")
                    self.assertEqual(json.loads(b64(h))["alg"],"RS256")
                    self.assertEqual(json.loads(b64(p))["iss"],"17")
                    signature = Path(tmp) / "signature"
                    signature.write_bytes(b64(s))
                    subprocess.run([openssl, "dgst", "-sha256", "-prverify", str(key), "-signature",str(signature)],
                                   input=f"{h}.{p}".encode(),capture_output=True,check=True)
                    return {"token":"mock-installation-token"}
            token = providers._github_token({"app_id":"17","installation_id":"42","private_key_file":str(key)}, Net())
            self.assertEqual(token,"mock-installation-token")

    def test_identifier_validation(self):
        self.assertEqual(providers.target_id("github", "Org/Repo/Docs"), "org/repo/Docs")
        self.assertEqual(providers.target_id("notion", "https://www.notion.so/Work-" + ROOT_PAGE.replace("-", "")), ROOT_PAGE)
        self.assertEqual(providers.target_id("drive", "https://docs.google.com/document/d/file1/edit"), "file1")
        for provider, url in [("github", "org/repo/../secrets"), ("notion", "https://random.org/" + ROOT_PAGE),
                              ("drive", "https://random.org/drive/folders/root"), ("drive", "root/file")]:
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                providers.target_id(provider, url)

    def test_github_path_immutable_blob_and_incremental_download(self):
        body = b"Store routing decisions in policy.md"
        sha = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
        calls = []
        class Net:
            def request(self, url, headers, **kwargs):
                calls.append(url)
                if url.endswith("/trees/HEAD"):
                    return {"sha": "root", "tree": [
                        {"path": "docs", "type": "tree", "sha": "docs123"},
                        {"path": "private", "type": "tree", "sha": "forbidden"}]}
                if url.endswith("/trees/docs123"):
                    return {"sha": "docs123", "tree": [{"type": "blob", "path": "policy.md", "mode": "100644",
                                                       "size": len(body), "sha": sha}]}
                if url.endswith(sha):
                    return {"content": base64.b64encode(body).decode(), "encoding": "base64"}
                raise AssertionError(url)
        with patch.object(providers, "_github_token", return_value="fake-install-token"):
            before = providers.github("org/repo/docs", {}, {}, "", Net())
            self.assertEqual(before.items["docs/policy.md"][2], body.decode())
            calls.clear()
            after = providers.github("org/repo/docs", {}, {k:v[:2] for k,v in before.items.items()}, before.cursor, Net())
            self.assertIsNone(after.items["docs/policy.md"][2])
            self.assertFalse(any("/blobs/" in url for url in calls))
            self.assertFalse(any("forbidden" in url for url in calls))

    def test_notion_child_removed_nested_blocks_text_and_pagination(self):
        visited = []
        class Net:
            revoked = False
            def request(self, url, headers, query=None):
                visited.append((url, query))
                if url.endswith("/pages/" + ROOT_PAGE) or url.endswith("/pages/" + CHILD_PAGE):
                    if self.revoked and url.endswith(CHILD_PAGE):
                        raise providers.AccessLost("removed")
                    return {"properties": {"Page": {"type": "title", "title": [{"plain_text": "Spec"}]}}}
                if ROOT_PAGE + "/children" in url:
                    if query.get("start_cursor"):
                        return {"results": [{"id": CHILD_PAGE, "type": "child_page"}], "has_more": False}
                    return {"results": [{"id":"block1","type":"toggle","has_children":True}],
                            "has_more":True,"next_cursor":"second-page"}
                if "block1/children" in url:
                    return {"results":[{"type":"paragraph","paragraph":{"rich_text":[{"plain_text":"Nested text"}]}}],
                            "has_more":False}
                if CHILD_PAGE + "/children" in url:
                    return {"results":[{"type":"paragraph","paragraph":{"rich_text":[{"plain_text":"Child text"}]}}],
                            "has_more":False}
                raise AssertionError(url)
        net = Net()
        first = providers.notion(ROOT_PAGE, {"token":"secret"}, {}, "", net)
        self.assertIn("Nested text", first.items[ROOT_PAGE][2])
        self.assertIn("Child text", first.items[CHILD_PAGE][2])
        net.revoked = True
        updated = providers.notion(ROOT_PAGE, {"token":"secret"}, {k:v[:2] for k,v in first.items.items()}, first.cursor, net)
        self.assertEqual(set(updated.items), {ROOT_PAGE})
        self.assertIsNone(updated.items[ROOT_PAGE][2])

    def test_drive_folder_membership_and_changed_payload_scope(self):
        def item(ident, mime, name):
            return {"id":ident, "name":name, "mimeType":mime, "version":"12","driveId":"shared",
                    "capabilities":{"canDownload":True}}
        root = item("root", providers.FOLDER, "folder")
        child = item("doc1", "application/vnd.google-apps.document", "Decision")
        class Net:
            files = [child]
            changes = []
            downloads = []
            def request(self, url, headers, **args):
                query = args.get("query", {})
                if url == providers.TOKEN_URL:
                    assert args["form"] and args["body"]["grant_type"] == "refresh_token"
                    return {"access_token":"token"}
                if url.endswith("/files/root"):
                    return root
                if url.endswith("/changes/startPageToken"):
                    assert query["driveId"] == "shared"
                    return {"startPageToken":"cursor1"}
                if url.endswith("/changes"):
                    return {"newStartPageToken":"cursor2","changes":self.changes}
                if url.endswith("/files"):
                    assert "'root' in parents" in query["q"] and query["corpora"] == "drive"
                    return {"files":self.files}
                if url.endswith("/files/doc1/export"):
                    self.downloads.append(url)
                    return b"Use two replicas."
                raise AssertionError(url)
        net = Net()
        creds = dict(refresh_token="refresh",client_id="id",client_secret="secret")
        first = providers.drive("root", creds, {}, "", net)
        self.assertEqual(first.items["doc1"][2], "Use two replicas.")
        net.changes = [{"fileId":"outside-selected-folder"}]
        old = {k:v[:2] for k,v in first.items.items()}
        second = providers.drive("root", creds, old, first.cursor, net)
        self.assertIsNone(second.items["doc1"][2])
        self.assertEqual(len(net.downloads), 1)
        net.files = []
        third = providers.drive("root", creds, old, second.cursor, net)
        self.assertEqual(third.items, {})

    def test_transport_distinguishes_rate_and_provider_access(self):
        for code, headers, body, error in [
            (403, {"X-RateLimit-Remaining":"0"}, {}, providers.SyncError),
            (403, {}, {"error":{"errors":[{"reason":"userRateLimitExceeded"}]}}, providers.SyncError),
            (403, {}, {}, providers.AccessLost), (404, {}, {}, providers.AccessLost),
            (400, {}, {"error":"invalid_grant"}, providers.AccessLost), (410, {}, {}, providers.CursorExpired),
            (500, {}, {}, providers.SyncError)]:
            with self.subTest(code=code, body=body):
                net = providers.Transport()
                failure = HTTPError("https://api.notion.com", code, "detail", headers, io.BytesIO(json.dumps(body).encode()))
                with patch.object(net.opener, "open", side_effect=failure), self.assertRaises(providers.SyncError) as thrown:
                    net.request("https://api.notion.com", {})
                self.assertIs(type(thrown.exception), error)


class Reconciliation(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        self.ledger = Ledger(folder / "namespace.sqlite")
        self.ledger.connector_namespace = "team"
        self.secret_file = folder / "server.json"
        self.registry = {"engineering": {"provider":"github","namespaces":["team"],"targets":["acme/repo/docs"],
                                         "installation_token_file": str(folder / "token")}}
        self.secret_file.write_text(json.dumps(self.registry))
        config = patch.dict(os.environ, {"GRAVITY_CONNECTOR_SECRETS":str(self.secret_file)})
        config.start()
        self.addCleanup(config.stop)
        self.files = {"notes.md": ("abc","Policy","Quokka cutoff is 2pm.")}
        self.failure = None
        def provider(root, credential, previous, cursor, net):
            if self.failure:
                raise self.failure
            result = providers.Snapshot(cursor="cursor12")
            for key, (rev, label, body) in self.files.items():
                unchanged = previous.get(key) == (rev, label)
                result.put(key, rev, label, None if unchanged else body)
            return result
        fake = patch.dict(connectors.PROVIDERS, {"github":provider})
        fake.start()
        self.addCleanup(fake.stop)
        self.args = dict(provider="github",name="Project docs",target="acme/repo/docs",credential="engineering")
        self.id = connectors.call(self.ledger, "connector_add", self.args)["id"]

    def test_selected_namespace_and_target_allowlist(self):
        other = Ledger(self.ledger.path.parent / "other.sqlite")
        other.connector_namespace = "other"
        with self.assertRaises(providers.SyncError):
            connectors.add(other, **self.args)
        with self.assertRaises(providers.SyncError):
            connectors.add(self.ledger, **{**self.args, "target":"acme/repo"})
        self.assertEqual(connectors.list_connections(other), {"items":[]})
        self.assertEqual(connectors.add(self.ledger, **self.args)["id"], self.id)

    def test_initial_repeat_incremental_removal_and_annotation_erasure(self):
        c = lambda: connectors.sync(self.ledger, self.id)
        self.assertEqual(c()["added"], 1)
        self.assertEqual(c()["unchanged"], 1)
        req = self.ledger.sources()["items"][0]["request_id"]
        self.ledger.annotate("approved", req, [{"key":"desk.cutoff","value":"2pm","quote":"Quokka cutoff is 2pm."}])
        self.files["notes.md"] = ("def","Policy","Quokka cutoff is 4pm.")
        self.assertEqual(c()["updated"],1)
        self.assertIsNone(self.ledger.source(req))
        self.assertIsNone(self.ledger.source("approved"))
        self.assertEqual(self.ledger.lookup("desk.cutoff")["status"], "unknown")
        self.assertEqual(self.ledger.search("Quokka",view="archive")["results"][0]["quote"], "Quokka cutoff is 4pm.")
        self.files = {}
        self.assertEqual(c()["removed"], 1)
        self.assertEqual(self.ledger.search("Quokka",view="archive")["results"], [])

    def test_transient_and_definitive_revocation(self):
        self.assertEqual(connectors.run_due(self.ledger)[0]["added"], 1)
        self.assertEqual(connectors.run_due(self.ledger), [])
        self.failure = providers.SyncError("Provider unavailable. The sync will retry.")
        with self.assertRaises(providers.SyncError):
            connectors.sync(self.ledger, self.id)
        self.assertEqual(self.ledger.status()["documents"], 1)
        current = connectors.list_connections(self.ledger)["items"][0]
        self.assertEqual((current["status"],current["cursor"]), ("error","cursor12"))
        self.failure = providers.AccessLost("Revoked")
        result = connectors.sync(self.ledger, self.id)
        self.assertEqual((result["status"],result["removed"]), ("revoked",1))
        self.assertEqual(self.ledger.status()["documents"], 0)

    def test_erased_source_stays_suppressed_after_upstream_edit(self):
        from gravity_memory.runs import erase_source_tx
        connectors.sync(self.ledger, self.id)
        req = self.ledger.sources()["items"][0]["request_id"]
        with self.ledger._tx(write=True) as db:
            erase_source_tx(db,req)
        self.files["notes.md"] = ("changed","New title","Revised Quokka cutoff.")
        self.assertEqual(connectors.sync(self.ledger,self.id)["added"], 0)
        self.assertEqual(self.ledger.status()["documents"], 0)
        with self.ledger._tx() as db:
            row = db.execute("SELECT request_id,revision,source FROM connector_items").fetchone()
            self.assertEqual(tuple(row), (None,"",""))

    def test_secret_scope_change_revokes_existing_copy(self):
        connectors.sync(self.ledger,self.id)
        self.registry["engineering"]["namespaces"] = ["elsewhere"]
        self.secret_file.write_text(json.dumps(self.registry))
        self.assertEqual(connectors.sync(self.ledger,self.id)["status"], "revoked")
        self.assertEqual(self.ledger.status()["documents"], 0)

    def test_disconnect_during_remote_read_cannot_restore_sources(self):
        def competing(root, credential, previous, cursor, net):
            connectors.remove(self.ledger,self.id)
            out = providers.Snapshot()
            out.put("stale","first","Stale","Stale body.")
            return out
        with patch.dict(connectors.PROVIDERS, {"github":competing}), self.assertRaises(providers.SyncError):
            connectors.sync(self.ledger,self.id)
        self.assertEqual(self.ledger.status()["documents"],0)
        self.assertEqual(connectors.list_connections(self.ledger), {"items":[]})

    def test_child_revocation_survives_unrelated_transient_failure(self):
        def seed(root, credential, previous, cursor, net):
            out = providers.Snapshot(cursor="first")
            out.put("child", "a", "Restricted", "Confidential decision.", ("sensitive-folder",))
            out.put("unrelated","b","Open","Unrelated decision.", ("other-folder",))
            return out
        with patch.dict(connectors.PROVIDERS, {"github":seed}):
            connectors.sync(self.ledger,self.id)
        def fail(root, credential, previous, cursor, net):
            net.withdraw("sensitive-folder")
            raise providers.SyncError("Unrelated provider request timed out.")
        with patch.dict(connectors.PROVIDERS, {"github":fail}), self.assertRaises(providers.SyncError):
            connectors.sync(self.ledger,self.id)
        self.assertEqual(self.ledger.search("confidential",view="archive")["results"],[])
        self.assertEqual(len(self.ledger.search("unrelated",view="archive")["results"]),1)
        self.assertEqual(connectors.list_connections(self.ledger)["items"][0]["cursor"],"first")

    def test_revoked_recall_trace_is_erased(self):
        from gravity_memory.runs import call
        connectors.sync(self.ledger,self.id)
        r = call(self.ledger,"start_run",{"request_id":"start","title":"Account handoff"},actor="agent")
        recall = call(self.ledger,"recall",{"request_id":"read","run_id":r["run_id"],"query":"quokka cutoff"},
                      actor="agent")
        self.assertIn("Quokka", json.dumps(recall))
        self.assertIn("Quokka",json.dumps(call(self.ledger,"trace",{"trace_id":recall["trace_id"]},actor="agent")))
        self.failure = providers.AccessLost("revoked")
        connectors.sync(self.ledger,self.id)
        with self.assertRaises(ValueError):
            call(self.ledger,"trace",{"trace_id":recall["trace_id"]},actor="agent")


if __name__ == "__main__":
    unittest.main()

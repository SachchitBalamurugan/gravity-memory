"""Run the production stack on loopback with a disposable namespace and trusted TLS."""
from contextlib import closing
from http.client import HTTPSConnection
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]

def port():
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

def main():
    project = "gravity-verify-" + uuid4().hex[:12]
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        tlsport = port()
        password = secrets.token_urlsafe(32)
        (folder/"owner-password").write_text(password, encoding="utf-8")
        (folder/"owner-password").chmod(0o644)
        env = {**os.environ, "GRAVITY_HOST":"localhost", "GRAVITY_OWNER":"owner", "GRAVITY_SECRETS_DIR":str(folder),
               "GRAVITY_HTTP_PORT":"127.0.0.1:"+str(port()), "GRAVITY_HTTPS_PORT":"127.0.0.1:"+str(tlsport),
               "GRAVITY_PUBLIC_ORIGIN":"https://localhost:"+str(tlsport)}
        def compose(*args, **kwargs):
            return subprocess.run(["docker","compose","-p",project,*args], cwd=ROOT, env=env, **kwargs)
        try:
            compose("up","--build","-d",check=True,stdout=subprocess.DEVNULL)
            cert = str(folder/"authority.crt")
            for attempt in range(40):
                p = compose("cp","gateway:/data/caddy/pki/authorities/local/root.crt",cert,capture_output=True)
                if p.returncode == 0:
                    break
                time.sleep(0.5)
            context = ssl.create_default_context(cafile=cert)
            def request(path, data=None, cookie=None, token=None):
                with closing(HTTPSConnection("localhost", tlsport, context=context, timeout=10)) as conn:
                    headers = {"Content-Type":"application/json"}
                    if cookie:
                        headers["Cookie"] = cookie
                    if token:
                        headers["Authorization"] = "Bearer "+token
                    conn.request("POST" if data is not None else "GET",path,json.dumps(data) if data is not None else None,headers)
                    response = conn.getresponse()
                    payload = json.load(response)
                    if response.status != 200:
                        raise ValueError(f"{path} returned {response.status}: {payload.get('error')}")
                    return payload, response.getheader("Set-Cookie")
            assert request("/health")[0]["status"] == "ready"
            _, cookie = request("/auth/login", {"user":"owner","password":password})
            assert "Secure" in cookie and "HttpOnly" in cookie
            token = request("/admin/issue_key", {"namespace":"personal","name":"compose-test","permissions":["read","write"]},
                            cookie=cookie.split(";",1)[0])[0]["key"]
            def call(op, args):
                return request("/v2/namespaces/personal/"+op, args, token=token)[0]
            body = "The release runner is Atlas."
            call("remember", {"request_id":"deploy","source":"Release decision","text":body,
                              "facts":[{"key":"release.runner","value":"Atlas","quote":body}]})
            run = call("start_run", {"title":"Deployment verification","request_id":"start"})
            recall = call("recall", {"query":"release runner","run_id":run["run_id"],"request_id":"recall"})
            assert recall["items"][0]["source_id"] == "deploy"
            compose("restart","memory",check=True,stdout=subprocess.DEVNULL)
            for attempt in range(45):
                try:
                    assert call("source",{"request_id":"deploy"})["text"] == body
                    break
                except (ValueError, OSError):
                    if attempt == 44:
                        raise
                    time.sleep(0.5)
            print("Compose passed verified HTTPS, sign-in, scoped key, cited recall and persistent restart.")
        finally:
            compose("down","--volumes","--remove-orphans",check=True,stdout=subprocess.DEVNULL)

if __name__ == "__main__":
    main()

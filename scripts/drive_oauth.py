"""Authorize a Google desktop OAuth client for a selected Gravity Memory namespace."""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import webbrowser


def authorize(client, *, wait_seconds=300):
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    selection = {}
    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def do_GET(self):
            path = urlsplit(self.path)
            query = parse_qs(path.query)
            valid = path.path == "/callback" and secrets.compare_digest(query.get("state", [""])[0], state)
            if not valid:
                self.send_error(400, "Invalid callback or state.")
                return
            selection["code"] = query.get("code", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"You may close this window and return to Gravity Memory setup.")

    with HTTPServer(("127.0.0.1", 0), Callback) as listener:
        redirect = f"http://127.0.0.1:{listener.server_port}/callback"
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": client["client_id"], "redirect_uri": redirect, "response_type":"code",
            "scope":"https://www.googleapis.com/auth/drive.readonly", "access_type":"offline", "prompt":"consent",
            "state":state,"code_challenge":challenge,"code_challenge_method":"S256"})
        print("Open this sign-in link on this computer if your browser does not open.", flush=True)
        print(url, flush=True)
        webbrowser.open(url)
        deadline = time.monotonic() + wait_seconds
        while "code" not in selection and time.monotonic() < deadline:
            listener.timeout = min(5, max(0, deadline - time.monotonic()))
            listener.handle_request()
    if not selection.get("code"):
        raise ValueError("Google sign-in was declined or timed out.")
    params = {"client_id":client["client_id"], "client_secret":client["client_secret"], "redirect_uri":redirect,
              "code_verifier":verifier, "code":selection["code"], "grant_type":"authorization_code"}
    with urlopen(Request("https://oauth2.googleapis.com/token", data=urlencode(params).encode()),
                 timeout=20) as response:
        refresh = json.load(response).get("refresh_token")
    if not refresh:
        raise ValueError("Google did not issue an offline refresh token.")
    return refresh


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True, type=Path, help="OAuth desktop client JSON from Google Cloud")
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--target", required=True, help="selected Drive root file or folder ID")
    parser.add_argument("--alias", required=True)
    parser.add_argument("--output", required=True, type=Path, help="new server credential JSON file")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output file. Merge additional aliases privately.")
    try:
        client = json.loads(args.client.read_text(encoding="utf-8-sig"))["installed"]
        refresh = authorize(client)
        entry = {"provider":"drive","namespaces":[args.namespace],"targets":[args.target],
                 "client_id":client["client_id"], "client_secret":client["client_secret"],"refresh_token":refresh}
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as dst:
            json.dump({args.alias: entry}, dst, indent=2)
        print("Saved the credential in", args.output)
    except (KeyError, ValueError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()

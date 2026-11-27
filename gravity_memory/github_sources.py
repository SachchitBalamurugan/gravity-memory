"""Import explicitly selected text files from public GitHub repositories."""
import base64
import binascii
import hashlib
import json
from pathlib import PurePosixPath
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .store import Conflict, encoded, string, utc

MAX_BYTES = 900_000
PUBLIC_FIELDS = "id,repository,path,ref,blob_sha,request_id,source,last_synced_at"


def selection(repository, path, ref):
    string(repository, "repository", 142)
    parts = repository.split("/")
    if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", parts[0]) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", parts[1]) or parts[1] in (".",".."):
        raise ValueError("Enter a public repository as owner/repository.")
    string(path, "path", 250)
    if "\\" in path or any(part in ("", ".", "..") for part in path.split("/")) or any(ord(c) < 32 for c in path):
        raise ValueError("Use a file path relative to the repository root.")
    if PurePosixPath(path).suffix.lower() not in (".md", ".markdown", ".mdx", ".txt", ".rst"):
        raise ValueError("Choose a Markdown, plain text, or reStructuredText file.")
    if ref is None:
        ref = ""
    if not isinstance(ref, str) or len(ref) > 96 or (ref and not re.fullmatch(r"[A-Za-z0-9._/-]+", ref)):
        raise ValueError("Enter a branch, tag, or commit, or leave it blank for the default branch.")
    repository = repository.lower()
    ident = hashlib.sha256(encoded([repository, path, ref]).encode()).hexdigest()
    return {"id": ident, "repository": repository, "path": path, "ref": ref}


def fetch_file(config):
    url = "https://api.github.com/repos/" + config["repository"] + "/contents/" + quote(config["path"], safe="/")
    if config["ref"]:
        url += "?" + urlencode({"ref": config["ref"]})
    req = Request(url, headers={"User-Agent": "GravityMemory", "Accept": "application/vnd.github+json",
                                "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urlopen(req, timeout=12) as response:
            raw = response.read(1_800_001)
    except HTTPError as exc:
        if exc.code == 404:
            raise ValueError("GitHub could not find that public repository, file, or revision.") from exc
        if exc.code in (403, 429):
            raise ValueError("GitHub declined this request. Check public access or try again after its rate limit resets.") from exc
        raise ValueError(f"GitHub returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError("GitHub is unavailable. The previous local copy is still readable.") from exc
    if len(raw) > 1_800_000:
        raise ValueError("The GitHub file exceeds the 900 KB import limit.")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("type") != "file" or data.get("encoding") != "base64":
        raise ValueError("Choose a single text file up to 900 KB.")
    if type(data.get("size")) is not int or not 0 < data["size"] <= MAX_BYTES:
        raise ValueError("Choose a nonempty file up to 900 KB.")
    try:
        body = base64.b64decode("".join(data["content"].split()), validate=True)
        text = body.decode("utf-8-sig")
    except (ValueError, AttributeError, KeyError, binascii.Error) as exc:
        raise ValueError("GitHub content must be readable UTF-8 text.") from exc
    digest = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
    if digest != data.get("sha") or len(body) != data["size"]:
        raise ValueError("GitHub content did not match its declared blob revision.")
    if "\0" in text:
        raise ValueError("Choose a UTF-8 text file.")
    return digest, text


def list_files(ledger):
    with ledger._tx() as db:
        return {"items": [dict(r) for r in db.execute(
            f"SELECT {PUBLIC_FIELDS} FROM github_files ORDER BY repository,path,ref")]}


def connect(ledger, repository, path, ref="", *, fetch=fetch_file):
    config = selection(repository, path, ref)
    with ledger._tx() as db:
        row = db.execute("SELECT version FROM github_files WHERE id=?", (config["id"],)).fetchone()
    return _refresh(ledger, config, row["version"] if row else None, fetch)


def sync(ledger, id, *, fetch=fetch_file):
    string(id, "id", 64)
    with ledger._tx() as db:
        row = db.execute("SELECT * FROM github_files WHERE id=?", (id,)).fetchone()
    if not row:
        raise ValueError("Unknown GitHub connection.")
    return _refresh(ledger, dict(row), row["version"], fetch)


def _refresh(ledger, config, expected_version, fetch):
    sha, text = fetch(config)
    ident = config["id"]
    request_id = "github:" + ident + ":" + sha
    source = config["repository"] + "/" + config["path"] + ("@" + config["ref"] if config["ref"] else "")
    checked = utc()
    with ledger._tx(write=True) as db:
        before = db.execute("SELECT version,blob_sha FROM github_files WHERE id=?", (ident,)).fetchone()
        if (before["version"] if before else None) != expected_version:
            if before and before["blob_sha"] == sha:
                current = db.execute(f"SELECT {PUBLIC_FIELDS} FROM github_files WHERE id=?", (ident,)).fetchone()
                return {"connection": dict(current), "imported": False, "request_id": request_id}
            raise Conflict("This connection changed during sync. Refresh the list and try again.")
        receipt = ledger._remember(db, request_id, text, source, "document", None, [], checked)
        db.execute("""INSERT INTO github_files(id,repository,path,ref,blob_sha,request_id,source,last_synced_at,version)
            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET blob_sha=excluded.blob_sha,
            request_id=excluded.request_id,last_synced_at=excluded.last_synced_at,version=excluded.version""",
            (ident, config["repository"], config["path"], config["ref"], sha, request_id, source, checked, secrets.token_hex(12)))
        current = db.execute(f"SELECT {PUBLIC_FIELDS} FROM github_files WHERE id=?", (ident,)).fetchone()
    return {"connection": dict(current), "imported": not receipt["replayed"], "request_id": request_id}


def disconnect(ledger, id):
    string(id, "id", 64)
    with ledger._tx(write=True) as db:
        removed = db.execute("DELETE FROM github_files WHERE id=?", (id,)).rowcount
    return {"disconnected": bool(removed)}

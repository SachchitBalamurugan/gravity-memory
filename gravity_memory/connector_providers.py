"""Read-only provider adapters. Providers enumerate only the operator's selected root."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

from .store import encoded

MAX_DOC = 1_000_000
MAX_RESPONSE = 8_000_000
MAX_OBJECTS = 10_000
MAX_REQUESTS = 2_000
MAX_CHANGED_BYTES = 48_000_000
GITHUB = "https://api.github.com"
NOTION = "https://api.notion.com/v1"
DRIVE = "https://www.googleapis.com/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TEXT_EXT = {".md", ".markdown", ".mdx", ".txt", ".rst", ".adoc", ".csv", ".tsv"}
FOLDER = "application/vnd.google-apps.folder"
EXPORT = {"application/vnd.google-apps.document": "text/plain",
          "application/vnd.google-apps.spreadsheet": "text/csv",
          "application/vnd.google-apps.presentation": "text/plain"}
DRIVE_FIELDS = "id,name,mimeType,version,modifiedTime,trashed,size,driveId,capabilities(canDownload)"


class SyncError(ValueError):
    """A retriable configuration, network or incomplete-enumeration failure."""


class AccessLost(SyncError):
    """The provider denied access to this specific selection or object."""


class CursorExpired(SyncError):
    pass


class EmptyRepository(SyncError):
    pass


class UnsupportedExport(SyncError):
    pass


def target_id(provider, target):
    if not isinstance(target, str) or not 1 <= len(target) <= 360 or any(ord(c) < 32 for c in target):
        raise ValueError("Enter a selected resource identifier.")
    target = target.strip()
    if provider == "github":
        if target.startswith("https://github.com/"):
            target = target[len("https://github.com/"):]
        pieces = target.strip("/").split("/")
        if (len(pieces) < 2 or not re.fullmatch(r"[A-Za-z0-9-]{1,39}", pieces[0])
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", pieces[1])
                or any(p in ("", ".", "..") or "\\" in p or "?" in p or "#" in p for p in pieces)):
            raise ValueError("Use owner/repository followed by the optional file or folder path.")
        return "/".join([pieces[0].lower(), pieces[1].lower(), *pieces[2:]])
    if provider == "notion":
        if target.startswith("https://"):
            info = urlsplit(target)
            if not any(info.hostname == host or (info.hostname or "").endswith("." + host)
                       for host in ("notion.so", "notion.site")):
                raise ValueError("Enter a Notion page link or page ID.")
            match = re.search(r"([0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$",
                              info.path.rstrip("/"))
            target = match.group(1) if match else ""
        try:
            return str(uuid.UUID(target))
        except ValueError:
            raise ValueError("Enter a Notion page link or page ID.") from None
    if provider == "drive":
        if target.startswith("https://"):
            info = urlsplit(target)
            if info.hostname not in ("drive.google.com", "docs.google.com"):
                raise ValueError("Enter a Google Drive file or folder link or ID.")
            match = re.search(r"/(?:folders|d)/([A-Za-z0-9_-]+)", info.path)
            target = match.group(1) if match else parse_qs(info.query).get("id", [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,150}", target):
            raise ValueError("Enter a Google Drive file or folder link or ID.")
        return target
    raise ValueError("Choose github, notion, or drive.")


def load_credential(alias, provider, namespace, target):
    path = os.environ.get("GRAVITY_CONNECTOR_SECRETS")
    if not path:
        raise SyncError("Configure GRAVITY_CONNECTOR_SECRETS on the server.")
    try:
        registry = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(registry, dict):
            raise ValueError()
        credential = registry.get(alias)
        if credential is None:
            raise AccessLost("Credential alias is unavailable for this namespace and selection.")
        if (credential["provider"] != provider or not isinstance(credential["namespaces"], list)
                or namespace not in credential["namespaces"] or not isinstance(credential["targets"], list)):
            raise AccessLost("Credential alias is unavailable for this namespace and selection.")
        selections = [target_id(provider, t) for t in credential["targets"]]
        if target not in selections:
            raise AccessLost("Credential alias is unavailable for this namespace and selection.")
        return credential
    except AccessLost:
        raise
    except (KeyError, ValueError, OSError, TypeError) as exc:
        raise SyncError("Credential alias is unavailable for this namespace and selection.") from exc


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Transport:
    def __init__(self, heartbeat=None, withdraw=None):
        self.count = 0
        self.opener = build_opener(NoRedirect())
        self.heartbeat = heartbeat
        self.withdraw = withdraw

    def request(self, url, headers, *, query=None, body=None, raw=False, form=False):
        if self.heartbeat:
            self.heartbeat()
        self.count += 1
        if self.count > MAX_REQUESTS:
            raise SyncError("The selection exceeds the per-sync request limit. Select a smaller root.")
        if query:
            url += "?" + urlencode(query)
        content = None
        headers = {"User-Agent": "GravityMemory/0.4", **headers}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
            content = (urlencode(body) if form else encoded(body)).encode()
        try:
            with self.opener.open(Request(url, data=content, headers=headers), timeout=18) as response:
                data = response.read(MAX_RESPONSE + 1)
        except HTTPError as exc:
            # Read only the provider error code/message for classification. Never log the response.
            try:
                reason = json.loads(exc.read(16_384))
            except (ValueError, OSError):
                reason = {}
            tokens = str(reason).lower()
            throttle = (exc.headers.get("X-RateLimit-Remaining") == "0"
                        or exc.headers.get("Retry-After") or any(s in tokens for s in
                        ("ratelimit", "rate limit", "quota", "dailylimit", "rate_limited", "usagelimits")))
            if exc.code == 410:
                raise CursorExpired("Provider cursor expired.") from None
            if exc.code == 409 and url.startswith(GITHUB + "/repos/") and "repository is empty" in tokens:
                raise EmptyRepository("The selected repository is empty.") from None
            if exc.code == 403 and url.startswith(DRIVE + "/files/") and "exportsizelimitexceeded" in tokens:
                raise UnsupportedExport("Google cannot export this file within its size limit.") from None
            if exc.code == 429 or throttle:
                raise SyncError("Provider rate limit reached. The sync will retry.") from None
            if exc.code in (401, 403, 404) or "invalid_grant" in tokens:
                raise AccessLost("The provider removed access to the selected resource.") from None
            raise SyncError(f"Provider returned HTTP {exc.code}. The sync will retry.") from None
        except (OSError, TimeoutError, URLError) as exc:
            raise SyncError("Provider unavailable. The sync will retry.") from exc
        if len(data) > MAX_RESPONSE:
            if raw:
                return b""
            raise SyncError("Provider response exceeded the size limit. Select a smaller root.")
        if raw:
            return data
        try:
            parsed = json.loads(data)
            if not isinstance(parsed, dict):
                raise ValueError()
            return parsed
        except ValueError:
            raise SyncError("The provider returned an invalid JSON object.") from None


def _key(credential, field):
    value = credential.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SyncError("A required provider credential field is missing.")
    return value.strip()


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _github_token(credential, net):
    if "installation_token_file" in credential:
        try:
            value = Path(_key(credential, "installation_token_file")).read_text().strip()
        except OSError as exc:
            raise SyncError("The installation token file is unavailable.") from exc
        if not value:
            raise SyncError("The installation token file is empty.")
        return value
    installation = str(credential.get("installation_id", ""))
    if not installation.isdigit():
        raise SyncError("Configure a GitHub App installation ID.")
    now = int(time.time())
    header = _b64(b'{"alg":"RS256","typ":"JWT"}')
    payload = _b64(encoded({"iat": now - 60, "exp": now + 480, "iss": _key(credential, "app_id")}).encode())
    claims = f"{header}.{payload}".encode()
    openssl = shutil.which("openssl")
    if not openssl and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "")) / "Git/usr/bin/openssl.exe"
        openssl = str(candidate) if candidate.is_file() else None
    if not openssl:
        raise SyncError("Install OpenSSL for GitHub App signing or provision a rotating installation token file.")
    try:
        signed = subprocess.run([openssl, "dgst", "-sha256", "-sign", _key(credential, "private_key_file")],
                                input=claims, capture_output=True, timeout=8, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SyncError("GitHub App signing failed. Check the configured private key.") from exc
    token = claims.decode() + "." + _b64(signed.stdout)
    result = net.request(f"{GITHUB}/app/installations/{installation}/access_tokens",
                         {"Authorization": "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28"},
                         body={"permissions": {"contents": "read"}})
    return _key(result, "token")


@dataclass
class Snapshot:
    cursor: str = ""
    # Each entry is (revision, source label, changed body or None to reuse previous).
    items: dict = field(default_factory=dict)
    ancestors: dict = field(default_factory=dict)
    changed_bytes: int = 0
    skipped: int = 0

    def put(self, item, version, source, text=None, parents=()):
        if text is not None:
            size = len(text.encode())
            if not text.strip() or "\0" in text or size > MAX_DOC:
                self.skipped += 1
                return
            self.changed_bytes += size
        if self.changed_bytes > MAX_CHANGED_BYTES or len(self.items) >= MAX_OBJECTS:
            raise SyncError("The selection exceeds the sync limit. Select a smaller root.")
        self.items[item] = (version, source[:512], text)
        self.ancestors[item] = parents


def _text(data):
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ""


def _suppressed(previous, item):
    return hasattr(previous, "suppressed") and previous.suppressed(item)


def _withdraw(net, item):
    callback = getattr(net, "withdraw", None)
    if callback:
        callback(item)


def _github_entries(base, headers, parts, net):
    def tree(ref):
        listing = net.request(base + "/git/trees/" + quote(ref, safe=""), headers)
        if listing.get("truncated") or not isinstance(listing.get("tree"), list):
            raise SyncError("GitHub returned an incomplete tree. Select a smaller source.")
        return listing
    root = tree("HEAD")
    cursor, current, path = root["sha"], root["tree"], ""
    for idx, name in enumerate(parts):
        found = next((e for e in current if e["path"] == name), None)
        if not found:
            return cursor, []
        path += name
        if found["type"] != "tree":
            return cursor, [dict(found, path=path)] if idx == len(parts) - 1 else []
        path += "/"
        current = tree(found["sha"])["tree"]
    pending, entries = [(path, current)], []
    while pending:
        prefix, children = pending.pop()
        for entry in children:
            name = prefix + entry["path"]
            if entry["type"] == "tree":
                pending.append((name + "/", tree(entry["sha"])["tree"]))
            else:
                entries.append(dict(entry, path=name))
            if len(entries) + len(pending) > MAX_OBJECTS:
                raise SyncError("Select a smaller GitHub subtree.")
    return cursor, entries


def github(root, credential, previous, cursor, net):
    repo = "/".join(root.split("/")[:2])
    headers = {"Authorization": "Bearer " + _github_token(credential, net),
               "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    base = f"{GITHUB}/repos/{repo}"
    result = Snapshot()
    # HEAD selects the default branch and pins the listing + blob reads to its tree.
    try:
        result.cursor, entries = _github_entries(base, headers, root.split("/")[2:], net)
    except EmptyRepository:
        return result
    for entry in entries:
        path = entry["path"]
        if _suppressed(previous, path):
            continue
        if entry["type"] != "blob" or entry.get("mode") not in ("100644", "100755"):
            continue
        if PurePosixPath(path).suffix.lower() not in TEXT_EXT or entry.get("size", MAX_DOC + 1) > MAX_DOC:
            result.skipped += 1
            continue
        sha = entry["sha"]
        source = f"github.com/{repo}/{path}"
        if previous.get(path) == (sha, source):
            result.put(path, sha, source)
            continue
        # A blob missing from an already pinned tree may be a transient provider race.
        blob = net.request(base + "/git/blobs/" + quote(sha, safe=""), headers)
        if blob.get("encoding") != "base64":
            raise SyncError("GitHub returned an unsupported blob encoding.")
        data = base64.b64decode("".join(blob["content"].split()), validate=True)
        digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if digest != sha or len(data) != entry["size"]:
            raise SyncError("GitHub returned a blob that did not match the selected revision.")
        result.put(path, sha, source, _text(data))
    return result


def _notion_text(rich):
    return "".join(part.get("plain_text", "") for part in rich if isinstance(part, dict))


def _pages(fetch, key, token_key, flag=None):
    token = None
    seen = set()
    while True:
        page = fetch(token)
        if not isinstance(page.get(key), list):
            raise SyncError("The provider returned an incomplete page.")
        yield from page[key]
        token = page.get(token_key)
        if flag and bool(page.get(flag)) != bool(token):
            raise SyncError("The provider returned an incomplete page cursor.")
        if not token:
            return
        if token in seen or len(seen) >= 200:
            raise SyncError("The provider pagination did not finish. Retry or select a smaller root.")
        seen.add(token)


def notion(root, credential, previous, cursor, net):
    headers = {"Authorization": "Bearer " + _key(credential, "token"), "Notion-Version": "2022-06-28"}
    result = Snapshot()
    pending, visited = [(root, ())], set()
    while pending:
        page_id, parents = pending.pop()
        if page_id in visited:
            continue
        visited.add(page_id)
        if len(visited) > MAX_OBJECTS:
            raise SyncError("Select a smaller Notion page hierarchy.")
        try:
            page = net.request(f"{NOTION}/pages/{page_id}", headers)
        except AccessLost:
            if page_id == root:
                raise
            _withdraw(net, page_id)
            continue
        if page.get("archived") or page.get("in_trash"):
            _withdraw(net, page_id)
            continue
        title = next((_notion_text(v.get("title", [])) for v in page.get("properties", {}).values()
                      if v.get("type") == "title"), "")
        source = "Notion / " + (title or page_id)
        lines = [title] if title else []
        count, size = 0, len(title.encode())
        # Keep each page as its own removable document. Linked pages and databases are not traversed.
        def blocks(parent, depth):
            nonlocal count, size
            if depth > 50:
                raise SyncError("Notion hierarchy is too deep. Select a smaller root.")
            def fetch(token):
                query = {"page_size": 100, **({"start_cursor": token} if token else {})}
                return net.request(f"{NOTION}/blocks/{parent}/children", headers, query=query)
            for block in _pages(fetch, "results", "next_cursor", "has_more"):
                count += 1
                if count > MAX_OBJECTS:
                    raise SyncError("Select a smaller Notion page.")
                if block.get("archived") or block.get("in_trash"):
                    if block["type"] == "child_page":
                        _withdraw(net, target_id("notion", block["id"]))
                    continue
                kind = block["type"]
                if kind == "child_page":
                    pending.append((target_id("notion", block["id"]), parents + (page_id,)))
                    continue
                if kind in ("child_database", "link_to_page", "synced_block"):
                    continue
                info = block.get(kind, {})
                text = _notion_text(info.get("rich_text", []))
                if kind == "table_row":
                    text = "\t".join(_notion_text(cell) for cell in info.get("cells", []))
                if text:
                    size += len(text.encode()) + 1
                    if size > MAX_DOC:
                        raise SyncError("A Notion page exceeds the document size limit.")
                    lines.append(text)
                if block.get("has_children"):
                    blocks(block["id"], depth + 1)
        try:
            blocks(page_id, 0)
        except AccessLost:
            if page_id == root:
                raise
            _withdraw(net, page_id)
            continue
        body = "\n".join(lines)
        if _suppressed(previous, page_id):
            continue
        sha = hashlib.sha256(body.encode()).hexdigest()
        result.put(page_id, sha, source, None if previous.get(page_id) == (sha, source) else body, parents)
    result.cursor = hashlib.sha256(encoded(sorted((k, r[0]) for k, r in result.items.items())).encode()).hexdigest()
    return result


def _drive_body(item, headers, net):
    mime = item["mimeType"]
    path = DRIVE + "/files/" + quote(item["id"], safe="")
    if mime in EXPORT:
        data = net.request(path + "/export", headers, query={"mimeType": EXPORT[mime]}, raw=True)
    else:
        data = net.request(path, headers, query={"alt": "media", "supportsAllDrives": "true"}, raw=True)
    if mime == "application/pdf":
        if len(data) > MAX_DOC:
            return ""
        try:
            from pypdf import PdfReader
        except ImportError:
            raise SyncError("Install pypdf to sync text-based PDF files from this Drive selection.") from None
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted or len(reader.pages) > 200:
                return ""
            pages, size = [], 0
            for page in reader.pages:
                content = page.extract_text() or ""
                size += len(content.encode()) + 1
                if size > MAX_DOC:
                    return ""
                pages.append(content)
            return "\n".join(pages)
        except Exception:
            return ""
    return _text(data)


def drive(root, credential, previous, cursor, net):
    authentication = {key: _key(credential, key) for key in ("refresh_token", "client_id", "client_secret")}
    token = net.request(TOKEN_URL, {}, body={**authentication, "grant_type": "refresh_token"}, form=True)
    headers = {"Authorization": "Bearer " + _key(token, "access_token")}
    shared = {"supportsAllDrives": "true"}
    def meta(fid):
        return net.request(DRIVE + "/files/" + quote(fid, safe=""), headers, query={"fields": DRIVE_FIELDS, **shared})
    first = meta(root)
    result = Snapshot()
    if first.get("trashed"):
        raise AccessLost("The selected Drive item is in the trash.")
    drive_id = first.get("driveId")
    change_params = {**shared, **({"driveId": drive_id} if drive_id else {})}
    touched = set()
    if cursor:
        seen = set()
        try:
            page_token = cursor
            while True:
                if page_token in seen or len(seen) >= 200:
                    raise SyncError("The Drive change feed exceeded its pagination limit.")
                seen.add(page_token)
                changes = net.request(DRIVE + "/changes", headers, query={**change_params,
                    "pageToken": page_token, "pageSize": 1000, "includeItemsFromAllDrives": "true",
                    "fields": "nextPageToken,newStartPageToken,changes(fileId)"})
                touched.update(c["fileId"] for c in changes["changes"])
                if len(touched) > MAX_OBJECTS:
                    raise CursorExpired("Drive needs a new cursor.")
                page_token = changes.get("nextPageToken")
                if not page_token:
                    result.cursor = changes["newStartPageToken"]
                    break
        except CursorExpired:
            touched.clear()
    if not result.cursor:
        result.cursor = net.request(DRIVE + "/changes/startPageToken", headers, query=change_params)["startPageToken"]
    # Always verify current folder membership and capability before reusing local text.
    pending, seen_items = [(first, ())], set()
    while pending:
        item, parents = pending.pop()
        fid = item["id"]
        if fid in seen_items:
            continue
        seen_items.add(fid)
        if len(seen_items) > MAX_OBJECTS:
            raise SyncError("Select a smaller Drive folder.")
        if item.get("trashed"):
            _withdraw(net, fid)
            continue
        mime = item["mimeType"]
        if mime == FOLDER:
            def fetch(token):
                return net.request(DRIVE + "/files", headers, query={**shared, "includeItemsFromAllDrives": "true",
                    **({"corpora": "drive", "driveId": drive_id} if drive_id else {}),
                    "q": f"'{fid}' in parents and trashed = false", "pageSize": 1000,
                    "fields": f"nextPageToken,incompleteSearch,files({DRIVE_FIELDS})",
                    **({"pageToken": token} if token else {})})
            def complete(token):
                listing = fetch(token)
                if listing.get("incompleteSearch"):
                    raise SyncError("Drive returned incomplete search results.")
                return listing
            try:
                children = list(_pages(complete, "files", "nextPageToken"))
                pending.extend((child, parents + (fid,)) for child in children)
            except AccessLost:
                if fid == root:
                    raise
                _withdraw(net, fid)
            continue
        if _suppressed(previous, fid):
            continue
        if item.get("capabilities", {}).get("canDownload") is not True:
            _withdraw(net, fid)
            result.skipped += 1
            continue
        if (not (mime in EXPORT or mime.startswith("text/") or mime == "application/pdf")
                or (mime not in EXPORT and int(item.get("size", 0)) > MAX_DOC)):
            result.skipped += 1
            continue
        version = str(item.get("version") or item["modifiedTime"])
        source = "Drive / " + item["name"]
        if fid not in touched and previous.get(fid) == (version, source):
            result.put(fid, version, source, parents=parents)
            continue
        try:
            body = _drive_body(item, headers, net)
        except UnsupportedExport:
            result.skipped += 1
            continue
        except AccessLost:
            if fid == root:
                raise
            _withdraw(net, fid)
            continue
        result.put(fid, version, source, body, parents)
    return result


PROVIDERS = {"github": github, "notion": notion, "drive": drive}

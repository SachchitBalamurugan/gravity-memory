"""Transactional, source-grounded memory. A database file is one trust boundary."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from itertools import groupby
from pathlib import Path

from .language import KEY, contact_scope, extract, matches_contact_scope, search_terms, subject_scope
from .schema import SCHEMA, UPGRADE_V1


class Conflict(ValueError):
    """The same request identifier was previously used for different content."""


def utc(value=None):
    if value is None:
        moment = datetime.now(timezone.utc)
    else:
        if not isinstance(value, str):
            raise ValueError("timestamp must be an ISO 8601 string with a time zone")
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            raise ValueError("timestamp requires a time zone")
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def string(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{name} requires 1 to {maximum} characters without NUL")
    value.encode("utf-8")
    return value


def valid_key(key):
    if not isinstance(key, str) or len(key) > 160 or not KEY.fullmatch(key):
        raise ValueError("key requires lowercase identifiers separated by dots")
    return key


def limit_rows(limit):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    return limit


def _time_bounds(as_of, known_at):
    now = utc()
    return (now if as_of is None else utc(as_of),
            now if known_at is None else utc(known_at))


def _revision_status(variants, at):
    """Resolve the distinct (value, expiry) pairs at a key's last eligible revision."""
    if not variants:
        return "unknown"
    if len(variants) > 1:
        return "disputed"
    value, expiry = next(iter(variants))
    if value is None:
        return "revoked"
    if expiry and expiry <= at:
        return "expired"
    return "current"


def encoded(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def claim_spans(body, facts, effective):
    if not isinstance(facts, list) or len(facts) > 512:
        raise ValueError("facts requires a list of at most 512 entries")
    for fact in facts:
        if not isinstance(fact, dict) or set(fact) - {"key", "value", "quote", "expires_at", "start"}:
            raise ValueError("unsupported fact fields")
        key = valid_key(fact.get("key"))
        if "value" not in fact:
            raise ValueError("supply value or explicitly set it to null to revoke")
        quote = string(fact.get("quote"), "quote", 4096)
        start = fact.get("start", body.find(quote))
        if type(start) is not int or start < 0 or body[start:start + len(quote)] != quote:
            raise ValueError(f"quote for {key} must occur verbatim at its cited position")
        value = fact["value"]
        if value is not None and string(value, "value", 1024) not in quote:
            raise ValueError(f"value for {key} must appear verbatim in its quote")
        until = fact.get("expires_at")
        if until is not None:
            until = utc(until)
            if until <= effective:
                raise ValueError("expires_at must follow effective_at")
        yield key, value, start, start + len(quote), effective, until


def chunks(body, size=1200, overlap=120):
    start = 0
    while start < len(body):
        end = min(start + size, len(body))
        if end < len(body):
            boundary = max(body.rfind("\n", start + size // 2, end), body.rfind(" ", start + size // 2, end))
            if boundary != -1:
                end = boundary + 1
        yield start, end
        if end == len(body):
            return
        start = end - overlap


FACT_SELECT = """
SELECT f.id AS fact_id, f.key, f.value, f.start, f.stop, f.effective_at, f.expires_at,
       coalesce(json_extract(d.payload,'$.origin'),d.request_id) AS request_id,
       d.request_id AS write_id, d.source, d.recorded_at, substr(d.body, f.start+1, f.stop-f.start) AS quote
FROM facts f JOIN documents d ON f.doc=d.id
"""


class Ledger:
    """Bind this object to an authorized database before accepting any tool arguments."""
    def __init__(self, path, *, embedder=None):
        self.embedder = embedder
        self._snapshot = threading.local()
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4):
                raise ValueError(f"unsupported database version {version}")
            if version == 0:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(SCHEMA)
            elif version == 1:
                db.executescript(UPGRADE_V1)
            if version < 3:
                from .run_schema import UPGRADE
                db.executescript(UPGRADE)
            if version < 4:
                from .run_schema import UPGRADE_V3
                db.executescript(UPGRADE_V3)

    def _connect(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA secure_delete=ON")
        return db

    @contextmanager
    def _tx(self, write=False, *, snapshot=False):
        # A composite read may call the existing retrieval paths. Bind those reads
        # to one SQLite snapshot even if another connection imports or erases data.
        current = getattr(self._snapshot, "connection", None)
        if current is not None and (not write or getattr(self._snapshot, "writable", False)):
            yield current
            return
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with db:
                if snapshot:
                    self._snapshot.connection = db
                    self._snapshot.writable = write
                try:
                    yield db
                finally:
                    if snapshot:
                        del self._snapshot.connection
                        del self._snapshot.writable

    def remember(self, request_id, text, source, *, role="document", effective_at=None, facts=None, metadata=None):
        """Persist an original and both indexes atomically. Only user turns enable the parser."""
        with self._tx(write=True) as db:
            return self._remember(db, request_id, text, source, role, effective_at, facts, utc(), metadata=metadata)

    def _remember(self, db, request_id, text, source, role, effective_at, facts, recorded, origin=None, frozen=None, metadata=None):
        string(request_id, "request_id", 256)
        string(text, "text", 2_000_000)
        string(source, "source", 512)
        if role not in ("document", "user", "assistant"):
            raise ValueError("role must be document, user or assistant")
        if role == "assistant" and facts:
            raise ValueError("assistant turns are archive-only")
        if effective_at is not None:
            effective_at = utc(effective_at)
        options = {"role": role, "effective_at": effective_at, "facts": facts}
        if metadata is not None:
            from .scoping import validate_metadata
            options["metadata"] = validate_metadata(metadata)
        if origin is not None:
            options["origin"] = origin
        fingerprint = hashlib.sha256(encoded([source, text, options]).encode()).hexdigest()
        previous = db.execute("SELECT id, fingerprint FROM documents WHERE request_id=?", (request_id,)).fetchone()
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise Conflict("request_id already belongs to different input")
            return self._receipt(db, previous["id"], True)
        effective = effective_at or recorded
        if frozen is not None:
            derived = frozen
        elif role == "user" and facts is None:
            derived = extract(text)
        else:
            derived = [] if facts is None else facts
        spans = list(claim_spans(text, derived, effective))
        doc = db.execute("""
            INSERT INTO documents(request_id,fingerprint,payload,source,body,effective_at,recorded_at)
            VALUES(?,?,?,?,?,?,?)
        """, (request_id, fingerprint, encoded(options), source, text, effective, recorded)).lastrowid
        for key, value, start, stop, at, until in spans:
            fact_id = db.execute("INSERT INTO facts(doc,key,value,start,stop,effective_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                                 (doc, key, value, start, stop, at, until)).lastrowid
            db.execute("INSERT INTO fact_search(rowid,key,value) VALUES(?,?,?)", (fact_id, key.replace(".", " "), value))
        for start, stop in (chunks(text) if origin is None else ()):
            chunk = db.execute("INSERT INTO chunks(doc,start,stop) VALUES(?,?,?)", (doc, start, stop)).lastrowid
            db.execute("INSERT INTO passage_search(rowid,text) VALUES(?,?)", (chunk, text[start:stop]))
        return self._receipt(db, doc, False)

    @staticmethod
    def _receipt(db, doc, replayed):
        receipt = dict(db.execute("SELECT request_id,source,recorded_at,effective_at FROM documents WHERE id=?",
                                  (doc,)).fetchone())
        keys = [row[0] for row in db.execute("SELECT DISTINCT key FROM facts WHERE doc=? ORDER BY key", (doc,))]
        count = db.execute("SELECT count(*) FROM chunks WHERE doc=?", (doc,)).fetchone()[0]
        return {**receipt, "status": "indexed", "keys": keys, "passages": count, "replayed": replayed}

    @staticmethod
    def _unclassified_turns(db, since, at, known):
        return db.execute("""
            SELECT d.request_id,d.effective_at FROM documents d
            WHERE json_extract(d.payload,'$.role')='user' AND json_type(d.payload,'$.facts')='null'
              AND d.effective_at BETWEEN ? AND ? AND d.recorded_at<=?
              AND NOT EXISTS(SELECT 1 FROM facts f WHERE f.doc=d.id)
              AND NOT EXISTS(SELECT 1 FROM documents r
                  WHERE json_extract(r.payload,'$.origin')=d.request_id AND r.recorded_at<=?)
            ORDER BY d.effective_at DESC,d.id DESC LIMIT 3
        """, (since, at, known, known)).fetchall()

    @staticmethod
    def _resolve(db, key, at, known, require_review=False):
        rows = db.execute(FACT_SELECT + """
            WHERE f.key=? AND f.effective_at=(
                SELECT max(s.effective_at) FROM facts s JOIN documents t ON t.id=s.doc
                WHERE s.key=? AND s.effective_at<=? AND t.recorded_at<=?
            ) AND d.recorded_at<=? ORDER BY f.id DESC
        """, (key, key, at, known, known)).fetchall()
        result = {"key": key, "status": "unknown", "value": None, "evidence": []}
        if not rows:
            return result
        variants = {(r["value"], r["expires_at"]) for r in rows}
        # Keep one citation per alternative. Full repetitions are accessible via history.
        seen = set()
        for row in rows:
            pair = row["value"], row["expires_at"]
            if pair not in seen:
                result["evidence"].append(dict(row))
                seen.add(pair)
        result["status"] = _revision_status(variants, at)
        if result["status"] == "current":
            result["value"] = rows[0]["value"]
            if require_review:
                pending = Ledger._unclassified_turns(db, rows[0]["effective_at"], at, known)
                outstanding = [p["request_id"] for p in pending]
                if outstanding:
                    result.update(status="pending_review", value=None, pending_sources=outstanding)
        return result

    def lookup(self, key, *, as_of=None, known_at=None, require_review=False):
        """Resolve valid time and knowledge time independently. Stop at the last version even if expired."""
        with self._tx() as db:
            if type(require_review) is not bool:
                raise ValueError("require_review must be a boolean")
            return self._resolve(db, valid_key(key), utc(as_of), utc(known_at), require_review)

    def profile(self, *, limit=24, offset=0, as_of=None, known_at=None, subject=None, require_review=False, filter=None):
        """Read the workspace's current, cited facts, grouped by canonical key prefix."""
        limit_rows(limit)
        if type(offset) is not int or not 0 <= offset < 2**63:
            raise ValueError("offset must be a nonnegative integer")
        if type(require_review) is not bool:
            raise ValueError("require_review must be a boolean")
        at, known = _time_bounds(as_of, known_at)
        parameters = {"at": at, "known": known}
        subject_filter = ""
        if subject == "self":
            subject_filter = "AND f.key NOT GLOB 'relation.*'"
        elif subject is not None:
            if not valid_key(subject).startswith("relation.") or subject.count(".") != 1:
                raise ValueError("subject must be self or relation.<name>")
            subject_filter = "AND f.key GLOB :subject"
            parameters["subject"] = subject + ".*"

        groups = {}
        excluded = {}
        total = 0
        from .scoping import scoped
        with scoped(self, filter), self._tx() as db:
            pending = self._unclassified_turns(db, "", at, known) if require_review else []
            variants = db.execute(f"""
                WITH latest AS (
                    SELECT f.key, max(f.effective_at) AS effective_at
                    FROM facts f JOIN documents d ON d.id=f.doc
                    WHERE f.effective_at<=:at AND d.recorded_at<=:known {subject_filter}
                    GROUP BY f.key
                )
                SELECT DISTINCT f.key,f.value,f.expires_at,f.effective_at
                FROM latest JOIN facts f USING(key,effective_at) JOIN documents d ON d.id=f.doc
                WHERE d.recorded_at<=:known
                ORDER BY f.key='profile.name' DESC, f.effective_at DESC, f.key
            """, parameters)
            for (key, revision), rows in groupby(variants, key=lambda row: (row["key"], row["effective_at"])):
                status = _revision_status({(r["value"], r["expires_at"]) for r in rows}, at)
                if status == "current" and pending and revision <= pending[0]["effective_at"]:
                    status = "pending_review"
                if status != "current":
                    excluded[status] = excluded.get(status, 0) + 1
                    continue
                if offset <= total < offset + limit:
                    topic = ".".join(key.split(".")[:2]) if key.startswith("relation.") else key.split(".", 1)[0]
                    groups.setdefault(topic, []).append(self._resolve(db, key, at, known))
                total += 1
        return {"groups": [{"topic": topic, "items": items} for topic, items in groups.items()],
                "total": total, "more": offset + limit < total, "excluded": excluded,
                "as_of": at, "known_at": known}

    def context(self, query, *, limit=5, retrieval="auto", profile_limit=6, as_of=None, known_at=None, require_review=False, filter=None):
        """Read a scoped profile, matching current facts and historical passages in one snapshot."""
        string(query, "query", 2048)
        limit_rows(limit)
        limit_rows(profile_limit)
        if retrieval not in ("auto", "full_text", "facts_only"):
            raise ValueError("retrieval must be auto, full_text, or facts_only")
        at, known = _time_bounds(as_of, known_at)
        scope = subject_scope(query)
        from .scoping import scoped
        with scoped(self, filter), self._tx(snapshot=True):
            profile = self.profile(limit=profile_limit, as_of=at, known_at=known, require_review=require_review,
                                   subject=scope.rstrip(".") if scope else None)
            facts = self.search(query, view="facts", limit=limit, as_of=at, known_at=known,
                                require_review=require_review)["results"]
            archive = {"results": [], "route": "facts_only", "reason": "archive retrieval disabled"}
            if retrieval == "auto":
                archive = self.search(query, view="auto_archive", limit=limit, as_of=at, known_at=known)
            elif retrieval == "full_text":
                archive = self.search(query, view="archive", limit=limit, as_of=at, known_at=known)
                archive.update(route="full_text", reason="explicit full-text retrieval")
        return {"query": query, "profile": profile, "facts": facts, "passages": archive["results"],
                "route": archive["route"], "reason": archive["reason"], "as_of": at, "known_at": known,
                **{key: archive[key] for key in ("model", "indexed_passages", "eligible_passages") if key in archive}}

    def history(self, key, *, limit=50, offset=0, as_of=None, known_at=None):
        if type(offset) is not int or not 0 <= offset < 2**63:
            raise ValueError("offset must be a nonnegative integer")
        conditions, parameters = ["f.key=?"], [valid_key(key)]
        if as_of is not None:
            conditions.append("f.effective_at<=?")
            parameters.append(utc(as_of))
        if known_at is not None:
            conditions.append("d.recorded_at<=?")
            parameters.append(utc(known_at))
        with self._tx() as db:
            return [dict(r) for r in db.execute(FACT_SELECT + " WHERE " + " AND ".join(conditions) +
                    " ORDER BY f.effective_at DESC, f.id DESC LIMIT ? OFFSET ?",
                    (*parameters, limit_rows(limit), offset))]

    def source(self, request_id):
        with self._tx() as db:
            row = db.execute("SELECT request_id,source,body AS text,recorded_at,effective_at,json_extract(payload,'$.role') AS role,json_extract(payload,'$.origin') AS original_id FROM documents WHERE request_id=?",
                             (string(request_id, "request_id", 256),)).fetchone()
            return dict(row) if row else None

    def search(self, query, *, view="facts", limit=8, as_of=None, known_at=None, require_review=False, filter=None):
        """Return lexical matches in the requested view. Archive hits are explicitly labeled historical evidence."""
        if filter is not None:
            from .scoping import scoped
            with scoped(self, filter):
                return self.search(query, view=view, limit=limit, as_of=as_of, known_at=known_at, require_review=require_review)
        if type(require_review) is not bool or (require_review and view != "facts"):
            raise ValueError("require_review is a boolean option for fact search")
        if view == "auto_archive":
            from .runtime import archive_search
            return archive_search(self, query, limit, as_of, known_at)
        if view == "hybrid_archive":
            from .semantic import hybrid
            return hybrid(self, query, limit, as_of, known_at)
        if view not in ("facts", "archive"):
            raise ValueError("view must be facts, archive or hybrid_archive")
        limit_rows(limit)
        at, known = utc(as_of), utc(known_at)
        terms = search_terms(query, expand=view == "facts")
        result = {"view": view, "terms": terms, "results": []}
        if not terms:
            return result
        match = " OR ".join('"' + term + '"' for term in terms)
        with self._tx() as db:
            if view == "archive":
                for row in db.execute("""
                    SELECT d.request_id, d.source, c.start, c.stop, passage_search.text AS quote,
                           d.effective_at,d.recorded_at,bm25(passage_search) AS rank
                    FROM passage_search JOIN chunks c ON c.id=passage_search.rowid JOIN documents d ON d.id=c.doc
                    WHERE passage_search MATCH ? AND d.effective_at<=? AND d.recorded_at<=?
                    ORDER BY rank,c.id DESC LIMIT ?
                """, (match, at, known, limit)):
                    result["results"].append(dict(row))
                return result
            # Filter past/future versions before LIMIT. A dense group of stale matches
            # must never crowd out the only current fact or leak the old value.
            matches = db.execute("""
                SELECT f.key,f.value,bm25(fact_search,2.0,1.0) AS rank
                FROM fact_search JOIN facts f ON f.id=fact_search.rowid JOIN documents d ON d.id=f.doc
                WHERE fact_search MATCH ? AND f.effective_at<=? AND d.recorded_at<=?
                  AND NOT EXISTS (
                    SELECT 1 FROM facts s JOIN documents t ON t.id=s.doc
                    WHERE s.key=f.key AND s.effective_at>f.effective_at AND s.effective_at<=? AND t.recorded_at<=?
                  )
                ORDER BY rank, f.id DESC
            """, (match, at, known, at, known))
            subject = subject_scope(query)
            contact = contact_scope(query)
            seen = set()
            for row in matches:
                key = row["key"]
                if subject == "self" and key.startswith("relation."):
                    continue
                if subject and subject != "self" and not key.startswith(subject):
                    continue
                if not matches_contact_scope(key, contact):
                    continue
                if key in seen:
                    continue
                seen.add(key)
                state = self._resolve(db, key, at, known, require_review)
                if state["status"] == "current":
                    result["results"].append({**state, "rank": row["rank"]})
                if len(result["results"]) == limit:
                    break
        return result

    _UNREVIEWED = """json_extract(d.payload,'$.origin') IS NULL
        AND NOT EXISTS(SELECT 1 FROM facts f WHERE f.doc=d.id)
        AND NOT EXISTS(SELECT 1 FROM documents r WHERE json_extract(r.payload,'$.origin')=d.request_id)"""

    def pending_count(self):
        with self._tx() as db:
            return db.execute(f"""SELECT COUNT(*) FROM documents d
                WHERE json_extract(d.payload,'$.role') IN ('user','document') AND {self._UNREVIEWED}""").fetchone()[0]

    def pending(self, *, role="user", limit=50, offset=0):
        """List original turns or documents with no promoted facts that still need a decision."""
        if role not in ("user", "document"):
            raise ValueError("review role must be user or document")
        if type(offset) is not int or not 0 <= offset < 2**63:
            raise ValueError("offset must be a nonnegative integer")
        with self._tx() as db:
            return [dict(row) for row in db.execute(f"""
                SELECT d.request_id,d.source,d.effective_at,substr(d.body,1,240) AS preview
                FROM documents d WHERE json_extract(d.payload,'$.role')=? AND {self._UNREVIEWED}
                ORDER BY d.id LIMIT ? OFFSET ?
            """, (role, limit_rows(limit), offset))]

    def annotate(self, request_id, original_id, facts):
        """Review a stored original and attach cited facts, or dismiss with an empty array."""
        with self._tx(write=True) as db:
            return self._annotate(db, request_id, original_id, facts, utc())

    def _annotate(self, db, request_id, original_id, facts, recorded, effective_at=None):
        row = db.execute("SELECT * FROM documents WHERE request_id=?",
                         (string(original_id, "original_id", 256),)).fetchone()
        if row is None:
            raise ValueError("original source is missing")
        options = json.loads(row["payload"])
        if options.get("origin") or options["role"] == "assistant":
            raise ValueError("select an original document or user turn for review")
        if not isinstance(facts, list):
            raise ValueError("review requires a facts array")
        return self._remember(db, request_id, row["body"], row["source"], "document",
                              row["effective_at"] if effective_at is None else effective_at,
                              facts, recorded, origin=original_id)

    def library(self, *, limit=40, offset=0):
        limit_rows(limit)
        if type(offset) is not int or not 0 <= offset < 2**63:
            raise ValueError("invalid offset")
        with self._tx() as db:
            rows = db.execute("SELECT key,max(doc) AS newest FROM facts GROUP BY key ORDER BY newest DESC,key LIMIT ? OFFSET ?",
                              (limit + 1, offset)).fetchall()
            now = utc()
            return {"items": [self._resolve(db, r["key"], now, now) for r in rows[:limit]], "more": len(rows) > limit}

    def sources(self, *, limit=40, offset=0):
        limit_rows(limit)
        if type(offset) is not int or not 0 <= offset < 2**63:
            raise ValueError("invalid offset")
        with self._tx() as db:
            rows = db.execute("""
                SELECT request_id,source,substr(body,1,180) AS preview,effective_at,recorded_at,
                       json_extract(payload,'$.role') AS role
                FROM documents WHERE json_extract(payload,'$.origin') IS NULL
                ORDER BY id DESC LIMIT ? OFFSET ?
            """, (limit + 1, offset)).fetchall()
            return {"items": [dict(r) for r in rows[:limit]], "more": len(rows) > limit}

    def status(self):
        with self._tx() as db:
            return dict(db.execute("""
                SELECT (SELECT count(*) FROM documents WHERE json_extract(payload,'$.origin') IS NULL) AS documents,
                       (SELECT count(*) FROM documents WHERE json_extract(payload,'$.origin') IS NOT NULL) AS reviews,(SELECT count(*) FROM chunks) AS passages,
                       (SELECT count(*) FROM facts) AS assertions,(SELECT count(DISTINCT key) FROM facts) AS keys
            """).fetchone())

    def export(self):
        """Capture exact extracted evidence and seal the stream against truncation."""
        with self._tx() as db:
            yield {"format": "gravity-memory/2"}
            digest = hashlib.sha256()
            count = 0
            for row in db.execute("SELECT * FROM documents ORDER BY id"):
                options = json.loads(row["payload"])
                if "origin" in options:
                    entry = {"request_id": row["request_id"], "original_id": options["origin"],
                             "facts": options["facts"], "recorded_at": row["recorded_at"]}
                    entry["effective_at"] = row["effective_at"]
                else:
                    stored = [dict(f) for f in db.execute("""
                        SELECT key,value,start,substr(?,start+1,stop-start) AS quote,expires_at
                        FROM facts WHERE doc=? ORDER BY id
                    """, (row["body"], row["id"]))]
                    entry = {"request_id": row["request_id"], "text": row["body"], "source": row["source"],
                             "recorded_at": row["recorded_at"], "assertions": stored, **options}
                digest.update((encoded(entry) + "\n").encode())
                count += 1
                yield entry
            yield {"entries": count, "sha256": digest.hexdigest()}

    def restore(self, records):
        stream = iter(records)
        header = next(stream, None)
        if header not in ({"format": "recall-ledger/1"}, {"format": "recall-ledger/2"}, {"format": "gravity-memory/2"}):
            raise ValueError("unsupported export header")
        with self._tx(write=True) as db:
            if db.execute("SELECT 1 FROM documents LIMIT 1").fetchone():
                raise Conflict("restore requires an empty workspace")
            count = 0
            digest = hashlib.sha256()
            sealed = header["format"].endswith("/2")
            footer = False
            for entry in stream:
                if footer:
                    raise ValueError("data after export footer")
                if sealed and isinstance(entry, dict) and set(entry) == {"entries", "sha256"}:
                    if entry != {"entries": count, "sha256": digest.hexdigest()}:
                        raise ValueError("export count or checksum mismatch")
                    footer = True
                    continue
                fields = {"request_id", "text", "source", "role", "effective_at", "facts", "recorded_at"}
                if sealed:
                    fields.add("assertions")
                    digest.update((encoded(entry) + "\n").encode())
                review_fields = {"request_id", "original_id", "facts", "recorded_at"}
                if not isinstance(entry, dict) or set(entry) not in (
                    fields, fields | {"metadata"}, review_fields, review_fields | {"effective_at"}
                ) or not isinstance(entry["recorded_at"], str):
                    raise ValueError("invalid export entry")
                row = dict(entry)
                recorded = utc(row.pop("recorded_at"))
                if "original_id" in row:
                    receipt = self._annotate(db, **row, recorded=recorded)
                else:
                    frozen = row.pop("assertions", None)
                    if sealed and not isinstance(frozen, list):
                        raise ValueError("missing stored assertions")
                    receipt = self._remember(db, **row, recorded=recorded, frozen=frozen)
                if receipt["replayed"]:
                    raise Conflict("duplicate request_id in export")
                count += 1
            if sealed and not footer:
                raise ValueError("incomplete export: missing footer")
            return {"restored": count}

    def capture_conversation(self, request_id, source, messages):
        from .ingestion import capture_conversation
        return capture_conversation(self, request_id, source, messages)

    def import_documents(self, files):
        from .ingestion import import_documents
        return import_documents(self, files)

    def import_provider(self, provider, scope, payload):
        from .importers import import_records
        return import_records(self, provider, scope, payload)

    def retire(self, key, reason, request_id, *, effective_at=None):
        key = valid_key(key)
        reason = string(reason, "reason", 2048)
        with self._tx(write=True) as db:
            if db.execute("SELECT 1 FROM facts WHERE key=? LIMIT 1", (key,)).fetchone() is None:
                raise ValueError("Choose an existing memory key to retire.")
            return self._remember(db, request_id, reason, "Operator decision", "document", effective_at,
                                  [{"key": key, "value": None, "quote": reason}], utc())

    def connections(self, key, *, limit=12, filter=None):
        from .connections import connections
        from .scoping import scoped
        with scoped(self, filter):
            return connections(self, key, limit=limit)

    def erase(self):
        """Logical whole-workspace erase, including sources, history, receipts and searchable content."""
        with self._tx(write=True) as db:
            count = db.execute("SELECT count(*) FROM documents WHERE json_extract(payload,'$.origin') IS NULL").fetchone()[0]
            db.execute("DELETE FROM documents")
            from .runs import clear_run_data
            clear_run_data(db)
        return {"erased_documents": count}

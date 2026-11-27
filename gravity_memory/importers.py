"""Ingest a scoped JSON API export without treating provider summaries as verified facts."""
import hashlib

from .store import encoded, string, utc


def import_records(ledger, provider, scope, payload):
    string(scope, "scope", 256)
    if provider not in ("mem0", "supermemory"):
        raise ValueError("provider must be mem0 or supermemory")
    if isinstance(payload, dict):
        payload = payload.get("results" if provider == "mem0" else "documents")
    if not isinstance(payload, list):
        raise ValueError("expected a JSON array or a provider results/documents page")
    counts = {"imported": 0, "replayed": 0, "outside_scope": 0}
    with ledger._tx(write=True) as db:
        for index, row in enumerate(payload):
            if not isinstance(row, dict):
                raise ValueError(f"row {index} must be an object")
            if provider == "mem0":
                scopes = [row.get("user_id")]
                body = row.get("memory")
                when = row.get("updated_at") or row.get("created_at")
            else:
                scopes = row.get("containerTags") or [row.get("containerTag")]
                body = row.get("content")
                when = row.get("updatedAt") or row.get("createdAt")
            if not isinstance(scopes, list) or not scopes or any(not isinstance(s, str) or not s for s in scopes):
                raise ValueError(f"row {index} lacks a valid provider scope")
            if scope not in scopes:
                counts["outside_scope"] += 1
                continue
            source = f"{provider}/{string(row.get('id'), 'provider id', 256)}"
            string(body, f"row {index} content", 2_000_000)
            if not when:
                raise ValueError(f"row {index} lacks an upstream timestamp")
            when = utc(when)
            version = hashlib.sha256(encoded([scope, source, when, body]).encode()).hexdigest()
            receipt = ledger._remember(db, version, body, source, "document", when, [], utc())
            counts["replayed" if receipt["replayed"] else "imported"] += 1
    return counts

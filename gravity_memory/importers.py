"""Ingest a scoped JSON API export without treating provider summaries as verified facts."""
import hashlib

from .store import encoded, string, utc


def _in_scope(row, provider, scope, page_scope, index):
    """Every explicit scope on the page or row must allow the selected tenant."""
    scopes = [] if page_scope is None else [[page_scope]]
    field = "user_id" if provider == "mem0" else "namespace"
    if field in row:
        scopes.append([row[field]])
    if provider == "supermemory" and ("containerTags" in row or "containerTag" in row):
        tags = row.get("containerTags") or [row.get("containerTag")]
        scopes.append(tags)
        if row.get("containerTag") and row.get("containerTags"):
            scopes.append([row["containerTag"]])
    if not scopes:
        raise ValueError(f"row {index} has no provider scope; save the request's {field} on the page")
    for allowed in scopes:
        if not isinstance(allowed, list) or not allowed:
            raise ValueError(f"row {index} lacks a valid provider scope")
        for item in allowed:
            string(item, f"row {index} provider scope", 256)
    return all(scope in allowed for allowed in scopes)


def import_records(ledger, provider, scope, payload):
    string(scope, "scope", 256)
    if provider not in ("mem0", "supermemory"):
        raise ValueError("provider must be mem0 or supermemory")
    page_scope = None
    if isinstance(payload, dict):
        # List responses may omit the tenant carried by the upstream request.
        # Require the exporter to preserve it in the file for untagged rows.
        field = "user_id" if provider == "mem0" else "namespace"
        if field in payload:
            page_scope = string(payload[field], "page " + field, 256)
        if provider == "mem0":
            payload = payload.get("results")
        elif "documents" in payload:
            if payload.get("memories") or payload.get("chunks"):
                raise ValueError("import a Supermemory documents page with full content")
            payload = payload["documents"]
        else:
            # Supermemory v3 calls documents on a list page "memories".
            payload = payload.get("memories")
    if not isinstance(payload, list):
        raise ValueError("expected a JSON array or a provider results/documents page")
    counts = {"imported": 0, "replayed": 0, "outside_scope": 0}
    with ledger._tx(write=True) as db:
        recorded = utc()
        for index, row in enumerate(payload):
            if not isinstance(row, dict):
                raise ValueError(f"row {index} must be an object")
            if not _in_scope(row, provider, scope, page_scope, index):
                counts["outside_scope"] += 1
                continue
            if provider == "mem0":
                body = row.get("memory")
                when = row.get("updated_at") or row.get("created_at")
            else:
                body = row.get("content")
                if body is None:
                    raise ValueError(f"row {index} lacks full content; include content when exporting documents")
                dates = row.get("system", row)
                if not isinstance(dates, dict):
                    raise ValueError(f"row {index} system must be an object")
                when = dates.get("updatedAt") or dates.get("createdAt")
            source = f"{provider}/{string(row.get('id'), 'provider id', 256)}"
            string(body, f"row {index} content", 2_000_000)
            if not when:
                raise ValueError(f"row {index} lacks an upstream timestamp")
            when = utc(when)
            version = hashlib.sha256(encoded([scope, source, when, body]).encode()).hexdigest()
            receipt = ledger._remember(db, version, body, source, "document", when, [], recorded)
            counts["replayed" if receipt["replayed"] else "imported"] += 1
    return counts

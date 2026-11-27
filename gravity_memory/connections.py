"""Trace relationships between memory keys through their cited original documents."""
import json

from .store import limit_rows, valid_key


def connections(ledger, key, *, limit=12):
    """Return up to limit original sources and limit related keys, across all revisions.

    Rank originals by effective time. Rank other keys by the number of displayed
    originals they share with the requested key. A link records a historical
    citation, including a revocation or superseded assertion.
    """
    valid_key(key)
    limit_rows(limit)
    graph = {"key": key, "nodes": [], "edges": [], "truncated": False}
    with ledger._tx() as db:
        sources = db.execute("""
            SELECT original.request_id, original.source
            FROM facts f JOIN documents writer ON writer.id=f.doc
            JOIN documents original ON original.request_id=coalesce(
                json_extract(writer.payload,'$.origin'), writer.request_id)
            WHERE f.key=?
            GROUP BY original.id
            ORDER BY original.effective_at DESC, original.request_id
            LIMIT ?
        """, (key, limit + 1)).fetchall()
        if not sources:
            return graph
        graph["truncated"] = len(sources) > limit
        sources = sources[:limit]
        # Include direct assertions and every review of each selected original.
        # DISTINCT counts one witness per source even when many reviews cite it.
        related = db.execute("""
            WITH originals(request_id) AS (SELECT value FROM json_each(?)),
            writes(request_id, doc) AS (
                SELECT o.request_id, d.id FROM originals o
                JOIN documents d ON d.request_id=o.request_id
                UNION ALL
                SELECT o.request_id, d.id FROM originals o
                JOIN documents d ON json_extract(d.payload,'$.origin')=o.request_id
            ), witnesses AS (
                SELECT DISTINCT f.key, w.request_id FROM writes w
                JOIN facts f ON f.doc=w.doc
                WHERE f.key<>?
            )
            SELECT key, count(*) AS shared, json_group_array(request_id) AS requests
            FROM witnesses GROUP BY key ORDER BY shared DESC, key
            LIMIT ?
        """, (json.dumps([r["request_id"] for r in sources]), key, limit + 1)).fetchall()

    def add_memory(name):
        node = {"id": "memory:" + name, "kind": "memory", "key": name, "label": name}
        graph["nodes"].append(node)
        return node["id"]

    def cite(request_id, memory):
        graph["edges"].append({"from": "source:" + request_id, "to": memory,
                               "type": "supports", "label": "Cited in revision history"})

    center = add_memory(key)
    for row in sources:
        request_id = row["request_id"]
        graph["nodes"].append({"id": "source:" + request_id, "kind": "source",
                               "request_id": request_id, "label": row["source"]})
        cite(request_id, center)
    for row in related[:limit]:
        neighbor = add_memory(row["key"])
        for request_id in sorted(json.loads(row["requests"])):
            cite(request_id, neighbor)
        count = row["shared"]
        shared = "1 shared source" if count == 1 else f"{count} shared sources"
        graph["edges"].append({"from": center, "to": neighbor, "type": "co_occurs",
                               "label": shared + " in this view"})
    graph["truncated"] |= len(related) > limit
    return graph

"""Construct a bounded agent response and retain selection details for the run inspector."""
import json
from contextlib import contextmanager

from .language import tokens
from .scoping import scoped
from .store import _time_bounds, encoded, limit_rows, string


@contextmanager
def current_passages(db, at, known):
    # The archive remains available separately. For action-oriented recall, a chunk
    # that repeats a displaced or disputed fact cannot reintroduce it as a passage.
    db.execute("""CREATE TEMP TABLE omitted_spans AS
        WITH eligible AS (
            SELECT f.*,coalesce(json_extract(w.payload,'$.origin'),w.request_id) AS source_id
            FROM facts f JOIN documents w ON w.id=f.doc
            WHERE f.effective_at<=? AND w.recorded_at<=?
        ), latest AS (SELECT key,max(effective_at) AS revision FROM eligible GROUP BY key),
        conflicts AS (
            SELECT f.key FROM eligible f JOIN latest l ON l.key=f.key AND f.effective_at=l.revision
            GROUP BY f.key HAVING count(DISTINCT json_array(f.value,f.expires_at))>1
        )
        SELECT o.id AS doc, f.source_id,f.start,f.stop,f.key,
          CASE WHEN f.effective_at<l.revision THEN 'superseded'
               WHEN c.key IS NOT NULL THEN 'disputed'
               WHEN f.value IS NULL THEN 'revoked' ELSE 'expired' END AS reason
        FROM eligible f JOIN latest l ON f.key=l.key JOIN documents o ON o.request_id=f.source_id
        LEFT JOIN conflicts c ON c.key=f.key WHERE f.effective_at<l.revision OR c.key IS NOT NULL
          OR f.value IS NULL OR f.expires_at<=?""", (at, known, at))
    try:
        db.execute("""INSERT INTO omitted_spans SELECT d.id,d.request_id,0,length(d.body),l.key,l.status
            FROM lesson_support s JOIN lesson_queue l USING(lesson_id) JOIN documents d ON d.request_id=s.source_id
            WHERE l.status IN ('rejected','retracted') AND l.decision_at<=? AND l.decision_at<=?""",(at,known))
        db.execute("CREATE INDEX temp.omitted_by_doc ON omitted_spans(doc,start,stop)")
        db.execute("""CREATE TEMP VIEW chunks AS SELECT c.* FROM main.chunks c
            JOIN documents d ON d.id=c.doc WHERE json_extract(d.payload,'$.role')!='assistant'
            AND NOT EXISTS(SELECT 1 FROM omitted_spans s WHERE s.doc=c.doc AND s.start<c.stop AND c.start<s.stop)""")
        yield
    finally:
        db.execute("DROP VIEW IF EXISTS temp.chunks")
        db.execute("DROP TABLE temp.omitted_spans")


def _fit(response, budget):
    """Count the complete canonical UTF-8 envelope, including its own size field."""
    response["bytes"] = 0
    while True:
        size = len(encoded(response).encode())
        if response["bytes"] == size:
            break
        response["bytes"] = size
    # Also fits common Python HTTP serializers with ASCII escape sequences and spaces.
    return max(size, len(json.dumps(response).encode())) <= budget


def question_fields(query):
    """Match an explicitly requested attribute before lexical terms in its qualifying clause."""
    words = tokens(query)
    if not words or words.pop(0) not in ("which", "what"):
        return set()
    while words and words[0] in {"is","are","was","were","the","a","an","my","our","your","current"}:
        words.pop(0)
    head = words[:2]
    return (set(head) | {"_".join(head)}) if head else set()


def build(ledger, db, args, trace_id):
    query = string(args["query"], "query", 2048)
    budget = args.get("budget_bytes", 8192)
    if type(budget) is not int or not 256 <= budget <= 262144:
        raise ValueError("budget_bytes must be an integer from 256 to 262144")
    limit = limit_rows(args.get("limit", 8))
    route = args.get("retrieval", "auto")
    if route not in ("auto", "facts_only", "full_text"):
        raise ValueError("invalid retrieval route")
    at, known = _time_bounds(args.get("as_of"), args.get("known_at"))
    include_profile = args.get("include_profile", False)
    if type(include_profile) is not bool:
        raise ValueError("include_profile must be a boolean")
    fields = question_fields(query)
    with scoped(ledger, args.get("filter")), current_passages(db, at, known):
        candidate_limit = min(100, max(limit, 12)) if fields else limit
        ctx = ledger.context(query, limit=candidate_limit, profile_limit=limit, retrieval=route, as_of=at, known_at=known)
        suppressed = dict(db.execute("SELECT reason,count(*) FROM omitted_spans GROUP BY reason"))
        from .language import search_terms
        matches = search_terms(query, expand=True)
        excluded = []
        if matches:
            match = " OR ".join('"' + t + '"' for t in matches)
            excluded = [dict(row) for row in db.execute("""
                SELECT DISTINCT s.source_id,s.key,s.reason FROM omitted_spans s WHERE s.key IN
                    (SELECT f.key FROM facts f JOIN fact_search ON fact_search.rowid=f.id
                     JOIN documents d ON d.id=f.doc WHERE fact_search MATCH ?)
                OR s.doc IN (SELECT c.doc FROM main.chunks c JOIN passage_search ON passage_search.rowid=c.id
                             WHERE passage_search MATCH ?)
                ORDER BY s.key,s.source_id LIMIT 30""", (match,match))]
        candidates = []
        facts = sorted(ctx["facts"], key=lambda row: row["key"].split(".")[-1] not in fields)
        for kind, rows in (("fact", facts), ("profile", [
                item for g in ctx["profile"]["groups"] for item in g["items"]] if include_profile else [])):
            for state in rows:
                ev = state["evidence"][0]
                candidates.append({"kind": kind, "key": state["key"], "value": state["value"],
                                   "source_id": ev["request_id"], **{k: ev[k] for k in
                                   ("source", "quote", "start", "stop", "effective_at", "recorded_at")}})
        for p in ctx["passages"]:
            candidates.append({"kind": "passage", "source_id": p["request_id"], **{k: p[k] for k in
                               ("source", "quote", "start", "stop", "effective_at", "recorded_at")}})
    result = {"trace_id": trace_id, "status": "abstained", "items": [], "route": ctx["route"],
              "budget_bytes": budget}
    diagnostics = {"as_of": at, "known_at": known, "route": ctx["route"], "reason": ctx["reason"],
                   "filter": args.get("filter"), "excluded": excluded, "namespace_exclusions": suppressed,
                   "candidates": [], "question_fields": sorted(fields), "version": "0.4.0", **{k: ctx[k] for k in
                   ("model", "indexed_passages", "eligible_passages") if k in ctx}}
    fact_sources, fact_keys = set(), set()
    for rank, item in enumerate(candidates, 1):
        decision = {"source_id": item["source_id"], "kind": item["kind"], "rank": rank}
        diagnostics["candidates"].append(decision)
        key = item.get("key")
        if key in fact_keys or (item["kind"] == "passage" and item["source_id"] in fact_sources):
            decision["reason"] = "same evidence already included"
            continue
        if len(result["items"]) >= limit:
            decision["reason"] = "item limit"
            continue
        old_status = result["status"]
        result["status"] = ("grounded" if item["kind"] in ("fact", "profile") or old_status == "grounded"
                            else "evidence_only")
        result["items"].append(item)
        if not _fit(result, budget):
            result["items"].pop()
            result["status"] = old_status
            decision["reason"] = "byte budget"
            continue
        decision["reason"] = "included"
        if key:
            fact_keys.add(key)
            fact_sources.add(item["source_id"])
    if not result["items"]:
        result["reason"] = ("budget" if any(d["reason"] == "byte budget" for d in diagnostics["candidates"])
                            else "conflicting_evidence" if any(d["reason"] == "disputed" for d in excluded)
                            else "no_current_evidence")
    _fit(result, budget)
    diagnostics["response_bytes"] = result["bytes"]
    return result, diagnostics

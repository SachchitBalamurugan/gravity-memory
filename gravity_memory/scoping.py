"""Validated document metadata predicates shared by all retrieval routes."""
import math
import re
from contextlib import contextmanager

FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
COMPARISONS = {"eq": "=", "ne": "!=", "gte": ">=", "lte": "<=", "gt": ">", "lt": "<"}


def scalar(value):
    if isinstance(value, str):
        return len(value) <= 1024 and "\x00" not in value
    if type(value) is int:
        return -(2**63) <= value < 2**63
    if type(value) is float:
        return math.isfinite(value)
    return isinstance(value, bool) or value is None


def validate_metadata(value):
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError("metadata must be an object with at most 32 scalar fields")
    if any(not isinstance(k, str) or not FIELD.fullmatch(k) or not scalar(v) for k, v in value.items()):
        raise ValueError("metadata keys must be identifiers and values must be bounded JSON scalars")
    return value


def predicate(node, params, depth=0):
    if not isinstance(node, dict) or depth > 6 or len(params) > 128:
        raise ValueError("filter must be a bounded predicate tree")
    op = node.get("operator")
    if op in ("and", "or"):
        children = node.get("operands")
        if set(node) != {"operator", "operands"} or not isinstance(children, list) or not 1 <= len(children) <= 16:
            raise ValueError("and/or require 1 to 16 operands")
        return "(" + (" " + op.upper() + " ").join(predicate(c, params, depth+1) for c in children) + ")"
    if set(node) != {"field", "operator", "value"} or not isinstance(op, str) or op not in COMPARISONS:
        raise ValueError("unsupported filter predicate")
    field, value = node["field"], node["value"]
    if not isinstance(field, str) or not scalar(value):
        raise ValueError("invalid filter field or value")
    if field == "source":
        if not isinstance(value, str):
            raise ValueError("source filters require text")
        params.append(value)
        return "d.source " + COMPARISONS[op] + " ?"
    if field == "role":
        path = "$.role"
    elif field.startswith("metadata.") and FIELD.fullmatch(field[9:]):
        path = "$.metadata." + field[9:]
    else:
        raise ValueError("filter field must be source, role or metadata.<name>")
    kind = ("null" if value is None else "bool" if isinstance(value, bool) else
            "number" if type(value) in (int, float) else "text")
    types = {"number": "('integer','real')", "bool": "('true','false')", "text": "('text')", "null": "('null')"}
    params.append(path)
    type_check = "json_type(d.payload,?) IN " + types[kind]
    if value is None:
        if op not in ("eq", "ne"):
            raise ValueError("null supports only equality predicates")
        return type_check if op == "eq" else "(json_type(d.payload,?) != 'null')"
    params.extend((path, value))
    return "(" + type_check + " AND json_extract(d.payload,?) " + COMPARISONS[op] + " ?)"


@contextmanager
def scoped(ledger, where):
    """A connection-local view applies the same eligible originals to FTS, vectors, profile and graph."""
    if where is None:
        yield
        return
    params = []
    condition = predicate(where, params)
    with ledger._tx(snapshot=True) as db:
        db.execute("CREATE TEMP TABLE eligible_originals(request_id TEXT PRIMARY KEY)")
        try:
            db.execute("INSERT INTO eligible_originals SELECT request_id FROM main.documents d "
                       "WHERE json_extract(d.payload,'$.origin') IS NULL AND " + condition, params)
            db.execute("""CREATE TEMP VIEW documents AS SELECT d.* FROM main.documents d
                JOIN eligible_originals e ON e.request_id =
                coalesce(json_extract(d.payload,'$.origin'),d.request_id)""")
            yield
        finally:
            db.execute("DROP VIEW IF EXISTS temp.documents")
            db.execute("DROP TABLE temp.eligible_originals")

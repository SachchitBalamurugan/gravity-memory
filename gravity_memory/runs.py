"""Namespace-local agent runs, evidence traces, learning decisions and erasure."""
import hashlib
import json
import math
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from . import agent_recall
from .store import Conflict, encoded, limit_rows, string, utc, valid_key

TEXT = {"type": "string"}
INT = {"type": "integer"}
BOOL = {"type": "boolean"}
OBJ = {"type": "object"}
TEXTS = {"type": "array", "items": TEXT}
METRIC_TEXT = {"tool", "model", "route", "fallback_reason"}
METRIC_COUNTS = {"input_tokens", "output_tokens"}
METRIC_FLOATS = {"latency_ms", "cost_usd"}
TELEMETRY = {"type":"object","additionalProperties":False,"properties":{
    **{k:TEXT for k in sorted(METRIC_TEXT)}, **{k:INT for k in sorted(METRIC_COUNTS)},
    **{k:{"type":"number"} for k in sorted(METRIC_FLOATS)}}}
RUN = {"request_id": TEXT, "run_id": TEXT}
RUN_OPS = {
    "start_run": ("Start a run, optionally inheriting a completed handoff.",
                  ["request_id","title"], {"request_id":TEXT,"title":TEXT,"parent_run_id":TEXT}),
    "run_note": ("Record a working note. Supply source_ids for provenance.",
                 [*RUN,"text"], {**RUN,"text":TEXT,"source_ids":TEXTS,"telemetry":TELEMETRY}),
    "recall": ("Retrieve current, cited evidence in a hard UTF-8 byte budget. Omit run_id for a standalone traced read.",
               ["request_id","query"], {**RUN,"run_id":{"type":["string","null"]},"query":TEXT,
               "budget_bytes":INT,"limit":INT,"as_of":TEXT,"known_at":TEXT,
               "filter":OBJ,"include_profile":BOOL,"retrieval":{"enum":["auto","facts_only","full_text"]}}),
    "outcome": ("Report an observed result. Optional cited lesson has type, key, value and source_id.",
                [*RUN,"success","detail"], {**RUN,"success":BOOL,"detail":TEXT,"lesson":OBJ,"source_ids":TEXTS,"telemetry":TELEMETRY}),
    "handoff": ("Close a run with a summary for a different agent.", [*RUN,"summary"],
                {**RUN,"summary":TEXT,"source_ids":TEXTS}),
    "runs": ("List recent agent runs by UTC date range and actor.", [],
             {"agent":TEXT,"since":TEXT,"until":TEXT,"limit":INT,"offset":INT}),
    "run_detail": ("Read run lineage, handoff and timeline.", ["run_id"], {"run_id":TEXT}),
    "trace": ("Inspect a recorded memory request, response and selection evidence.", ["trace_id"], {"trace_id":TEXT}),
    "compare_trace": ("Compare recorded recall with current evidence under the same search settings.",
                      ["trace_id"], {"trace_id":TEXT}),
    "activity": ("Inspect real request counts and recent searches.", [], {"days":INT,"agent":TEXT}),
    "lessons": ("List learned or proposed lessons and their supporting outcomes.", [], {"status":TEXT,"limit":INT,"offset":INT}),
    "review_lesson": ("Approve, reject or retract a lesson as a namespace administrator.", ["lesson_id","decision"],
                      {"lesson_id":TEXT,"decision":{"enum":["approve","reject","retract"]}}),
    "get_policy": ("Read namespace learning, storage and trace retention settings.", [], {}),
    "policy": ("Set namespace retention in days or null for unlimited. Configure allowlisted lesson types.", [],
               {"retention_days":{"type":["integer","null"]},"auto_types":TEXTS}),
    "erase_source": ("Erase an original, derived facts and affected trace content.", ["request_id"], {"request_id":TEXT}),
}
SESSION_OPS = {"start_run", "run_note", "recall", "outcome", "handoff"}
ADMIN_OPS = {"erase_source", "review_lesson", "policy"}
WRITES = {"remember", "annotate", "capture_conversation", "import_documents", "import_provider", "retire",
          "start_run", "run_note", "outcome", "handoff", "review_lesson", "policy"}
TYPE = re.compile(r"[a-z][a-z0-9_-]{0,39}")
ID_FIELDS = {"request_id", "source_id", "write_id", "original_id", "source_ids", "pending_sources"}

class ErasedReceipt(Conflict):
    """A completed command cannot replay its deliberately removed payload."""


def validated_telemetry(metrics):
    if not isinstance(metrics, dict) or not metrics or set(metrics) - TELEMETRY["properties"].keys():
        raise ValueError("telemetry must contain only supported measurement fields")
    for key,value in metrics.items():
        if key in METRIC_TEXT:
            string(value,key,512 if key == "fallback_reason" else 160)
        elif key in METRIC_COUNTS:
            if type(value) is not int or not 0 <= value <= 10**12:
                raise ValueError(key+" must be a nonnegative bounded integer")
        elif type(value) not in (int,float) or not 0 <= value <= 10**12 or not math.isfinite(value):
            raise ValueError(key+" must be a nonnegative finite measurement")
    return metrics


def ident(prefix):
    return prefix + "_" + uuid.uuid4().hex


def row_or_error(row, what):
    if row is None:
        raise ValueError(f"Unknown {what}.")
    return dict(row)


def check_sources(db, ids):
    if not isinstance(ids, list) or len(ids) > 64:
        raise ValueError("source_ids must be a list of up to 64 originals")
    for item in ids:
        string(item, "source_id", 256)
        if not db.execute("SELECT 1 FROM documents WHERE request_id=? AND json_type(payload,'$.origin') IS NULL",
                          (item,)).fetchone():
            raise ValueError("unknown original source")


def references(value):
    refs = set()
    if isinstance(value, list):
        for item in value:
            refs.update(references(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in ID_FIELDS:
                ids = item if isinstance(item, list) else [item]
                refs.update(v for v in ids if isinstance(v, str))
            if isinstance(item, (list,dict)):
                refs.update(references(item))
    return refs


def record(db, trace_id, run_id, op, actor, args, result, diagnostics, start):
    stamp = utc()
    db.execute("INSERT INTO traces VALUES(?,?,?,?,?,?,?,?,?,?)",
               (trace_id,run_id,op,actor,stamp,round((time.perf_counter()-start)*1000,3),
                encoded(args),encoded(result),encoded(diagnostics),int(op in WRITES)))
    db.executemany("INSERT INTO trace_refs VALUES(?,?)",
                   [(ref,trace_id) for ref in references([args,result,diagnostics])])
    if run_id:
        db.execute("INSERT OR IGNORE INTO run_refs SELECT source_id,? FROM trace_refs WHERE trace_id=?",
                   (run_id,trace_id))
        db.execute("UPDATE agent_runs SET updated_at=? WHERE run_id=?", (stamp,run_id))


def expire(db):
    days = db.execute("SELECT retention_days FROM run_policy WHERE singleton=1").fetchone()[0]
    if days is not None:
        cutoff = (datetime.now(timezone.utc)-timedelta(days=days)).isoformat(timespec="microseconds")
        db.execute("DELETE FROM traces WHERE at<?", (cutoff,))


def tracked(ledger, op, args, *, actor, invoke):
    """Commit an ordinary operation and its complete trace in the same transaction."""
    start = time.perf_counter()
    with ledger._tx(write=True, snapshot=True) as db:
        expire(db)
        run_id = args.get("run_id")
        if "run_id" in args:
            row = db.execute("SELECT actor,status FROM agent_runs WHERE run_id=?",
                             (string(run_id, "run_id", 120),)).fetchone()
            if not row or row["actor"] != actor:
                raise ValueError("choose a run owned by this caller")
            if row["status"] != "open":
                raise Conflict("run is closed; start a child run")
        db.execute("SAVEPOINT memory_operation")
        try:
            result = invoke()
        except (ValueError, OverflowError) as exc:
            db.execute("ROLLBACK TO memory_operation")
            error = exc
            result = {"error": str(exc)}
        else:
            error = None
        db.execute("RELEASE memory_operation")
        record(db,ident("tr"),run_id,op,actor,args,result,{"failed":bool(error)},start)
    if error:
        raise error
    return result


def clear_run_data(db):
    for table in ("agent_requests", "trace_refs", "lesson_support", "lesson_queue", "traces", "agent_runs"):
        db.execute("DELETE FROM " + table)


def _remove_traces(db, source_ids):
    # A free-form note or handoff can repeat evidence from an earlier read.
    # Remove payloads for the entire affected run and its handoff descendants.
    sources = encoded(list(source_ids))
    db.execute("""CREATE TEMP TABLE affected_runs AS WITH RECURSIVE affected(id) AS (
        SELECT run_id FROM run_refs WHERE source_id IN (SELECT value FROM json_each(?))
        UNION SELECT run_id FROM agent_runs a JOIN affected p ON a.parent_run_id=p.id
    ) SELECT id FROM affected""", (sources,))
    try:
        db.execute("""DELETE FROM traces WHERE run_id IN (SELECT id FROM affected_runs) OR trace_id IN (
            SELECT trace_id FROM trace_refs WHERE source_id IN (SELECT value FROM json_each(?)))""", (sources,))
        db.execute("UPDATE agent_runs SET title='Erased run',summary=NULL WHERE run_id IN (SELECT id FROM affected_runs)")
        db.execute("DELETE FROM run_refs WHERE run_id IN (SELECT id FROM affected_runs)")
    finally:
        db.execute("DROP TABLE temp.affected_runs")


def erase_source_tx(db, request_id):
    """Erase an original and any linked trace or derived document inside the caller's transaction."""
    string(request_id,"request_id",256)
    if not db.execute("SELECT 1 FROM documents WHERE request_id=?", (request_id,)).fetchone():
        return 0
    ids = {r[0] for r in db.execute("""WITH RECURSIVE
        edges(parent,child) AS (
            SELECT json_extract(payload,'$.origin'),request_id FROM documents WHERE json_type(payload,'$.origin') IS NOT NULL
            UNION ALL SELECT s.source_id,d.write_id FROM lesson_support s JOIN lesson_derivations d USING(lesson_id)
        ), affected(id) AS (VALUES(?) UNION SELECT e.child FROM affected p JOIN edges e ON p.id=e.parent)
        SELECT id FROM affected""", (request_id,))}
    markers = encoded(sorted(ids))
    db.execute("""DELETE FROM lesson_queue WHERE lesson_id IN (
        SELECT lesson_id FROM lesson_support WHERE source_id IN (SELECT value FROM json_each(?))
        UNION SELECT lesson_id FROM lesson_derivations WHERE write_id IN (SELECT value FROM json_each(?)))""",
        (markers,markers))
    _remove_traces(db,ids)
    db.executemany("DELETE FROM documents WHERE request_id=?", [(doc,) for doc in ids])
    return len(ids)


def get_policy(db):
    row = dict(db.execute("SELECT retention_days,auto_types FROM run_policy").fetchone())
    row["auto_types"] = json.loads(row["auto_types"])
    size, count = db.execute("""SELECT coalesce(sum(length(CAST(input AS BLOB))+length(CAST(output AS BLOB))+
        length(CAST(diagnostics AS BLOB))),0),count(*) FROM traces""").fetchone()
    return {**row, "trace_bytes":size, "trace_count":count}


def set_policy(db, args):
    if "retention_days" in args:
        days = args["retention_days"]
        if days is not None and (type(days) is not int or not 1 <= days <= 36500):
            raise ValueError("retention_days must be null or 1 to 36500")
        db.execute("UPDATE run_policy SET retention_days=?", (days,))
        expire(db)
    if "auto_types" in args:
        types = args["auto_types"]
        if not isinstance(types,list) or len(types)>32 or any(not isinstance(t,str) or not TYPE.fullmatch(t) for t in types):
            raise ValueError("auto_types must contain up to 32 lowercase type identifiers")
        db.execute("UPDATE run_policy SET auto_types=?", (encoded(sorted(set(types))),))
    return get_policy(db)


def _source(db, source_id):
    result = row_or_error(db.execute("""SELECT * FROM documents WHERE request_id=?
        AND json_type(payload,'$.origin') IS NULL AND json_extract(payload,'$.role')!='assistant'""",
        (string(source_id,"source_id",256),)).fetchone(),"eligible lesson source")
    return result


def supports(db, lesson_id):
    return [dict(r) for r in db.execute("""SELECT trace_id,run_id,source_id,actor,success,trusted FROM lesson_support
        WHERE lesson_id=? ORDER BY rowid""", (lesson_id,))]


def lesson_row(db, lesson_id):
    lesson = row_or_error(db.execute("SELECT * FROM lesson_queue WHERE lesson_id=?", (lesson_id,)).fetchone(),"lesson")
    lesson["supports"] = supports(db,lesson_id)
    lesson["source_id"] = lesson["supports"][0]["source_id"] if lesson["supports"] else None
    return lesson


def decision(ledger, db, lesson_id, choice, actor):
    lesson = lesson_row(db, lesson_id)
    expected = {"approve":"approved", "reject":"rejected", "retract":"retracted"}.get(choice)
    if not expected:
        raise ValueError("unknown review decision")
    if lesson["status"] == expected:
        return lesson
    if choice in ("approve","reject") and lesson["status"] != "pending":
        raise Conflict("only pending lessons can be reviewed")
    if choice == "retract" and lesson["status"] != "approved":
        raise Conflict("only approved lessons can be retracted")
    write_id = None
    stamp = utc()
    if choice == "approve":
        source = _source(db,lesson["source_id"])
        write_id = ident("learned")
        # Citation offsets remain in the original. The effective date of the decision
        # is when it was made, enabling historical replay before and after promotion.
        ledger._remember(db, write_id, source["body"], source["source"], "document", stamp,
                         [{"key":lesson["key"],"value":lesson["value"],"quote":lesson["value"]}],
                         stamp, origin=source["request_id"])
        db.execute("INSERT INTO lesson_derivations VALUES(?,?)",(lesson_id,write_id))
    elif choice == "retract":
        # Keep the historical decision inspectable. A null revision prevents fallback
        # to the retracted fact, except if a later independent revision already won.
        current = ledger._resolve(db,lesson["key"],stamp,stamp)
        if any(e["write_id"] == lesson["write_id"] for e in current["evidence"]):
            original = _source(db,lesson["source_id"])
            withdrawal_id = ident("withdrawn")
            ledger._remember(db,withdrawal_id,original["body"],original["source"],"document",stamp,
                             [{"key":lesson["key"],"value":None,"quote":lesson["value"]}],stamp,origin=original["request_id"])
            db.execute("INSERT INTO lesson_derivations VALUES(?,?)",(lesson_id,withdrawal_id))
        write_id = lesson["write_id"]
    db.execute("UPDATE lesson_queue SET status=?,decision_at=?,decision_by=?,write_id=? WHERE lesson_id=?",
               (expected,stamp,actor,write_id,lesson_id))
    return lesson_row(db,lesson_id)

def date_bound(value, end=False):
    if isinstance(value,str) and len(value)==10:
        day = date.fromisoformat(value)
        if end:
            return (datetime.combine(day,datetime.max.time(),timezone.utc)).isoformat(timespec="microseconds")
        return datetime.combine(day,datetime.min.time(),timezone.utc).isoformat(timespec="microseconds")
    return utc(value)


def page(args, default=50):
    off = args.get("offset",0)
    if type(off) is not int or not 0<=off<=2**63-1:
        raise ValueError("offset must be a nonnegative integer")
    return limit_rows(args.get("limit",default)),off


def list_runs(db, args):
    filters, parameters = [], []
    if "agent" in args:
        filters.append("actor=?")
        parameters.append(string(args["agent"],"agent",256))
    if "since" in args or "until" in args:
        # Activity charts drill down into runs that actually had an event that day,
        # including a long-lived run that started earlier.
        since = date_bound(args["since"]) if "since" in args else ""
        until = date_bound(args["until"],True) if "until" in args else "9999"
        if until < since:
            raise ValueError("until must follow since")
        filters.append("EXISTS(SELECT 1 FROM traces t WHERE t.run_id=r.run_id AND t.at BETWEEN ? AND ?)")
        parameters.extend((since,until))
    where = " WHERE " + " AND ".join(filters) if filters else ""
    limit, offset = page(args)
    rows = db.execute("""SELECT r.*, (SELECT count(*) FROM traces t WHERE t.run_id=r.run_id) AS requests
        FROM agent_runs r""" + where + " ORDER BY updated_at DESC LIMIT ? OFFSET ?", (*parameters,limit+1,offset)).fetchall()
    return {"items":[dict(r) for r in rows[:limit]],"more":len(rows)>limit}


def activity(db, args):
    days = args.get("days",30)
    if type(days) is not int or days not in (7,30,90):
        raise ValueError("days must be 7, 30 or 90")
    today = datetime.now(timezone.utc).date()
    start = today-timedelta(days=days-1)
    where, params = "at>=?", [start.isoformat()]
    if "agent" in args:
        where += " AND actor=?"
        params.append(string(args["agent"],"agent",256))
    counts = {r[0]: r[1:] for r in db.execute("""SELECT substr(at,1,10),sum(1-write),sum(write)
        FROM traces WHERE """ + where + " GROUP BY substr(at,1,10)",params)}
    series = []
    for n in range(days):
        stamp = (start+timedelta(days=n)).isoformat()
        reads, writes = counts.get(stamp,(0,0))
        series.append({"date":stamp,"reads":reads,"writes":writes})
    recent = [dict(r) for r in db.execute("""SELECT trace_id,run_id,at,actor,json_extract(input,'$.query') AS query
        FROM traces WHERE op IN ('recall','context','search') AND """+where+" ORDER BY at DESC LIMIT 8",params)]
    return {"days":series,"reads":sum(v[0] for v in counts.values()),"writes":sum(v[1] for v in counts.values()),
            "runs":db.execute("SELECT count(DISTINCT run_id) FROM traces WHERE "+where,params).fetchone()[0],
            "latest_searches":recent, "agents":[r[0] for r in db.execute("SELECT DISTINCT actor FROM traces ORDER BY actor")]}


def get_trace(db, trace_id):
    t = row_or_error(db.execute("SELECT * FROM traces WHERE trace_id=?", (string(trace_id,"trace_id",120),)).fetchone(),"trace")
    for field in ("input","output","diagnostics"):
        t[field] = json.loads(t[field])
    return t


def session(ledger, db, op, args, actor, trusted):
    start = time.perf_counter()
    supplied = string(args["request_id"],"request_id",256)
    request_id = hashlib.sha256(supplied.encode()).hexdigest()
    fingerprint = hashlib.sha256(encoded([op,args]).encode()).hexdigest()
    previous = db.execute("SELECT fingerprint,trace_id FROM agent_requests WHERE actor=? AND request_id=?",
                          (actor,request_id)).fetchone()
    if previous:
        if previous["fingerprint"] != fingerprint:
            raise Conflict("request_id already belongs to different input")
        if not previous["trace_id"]:
            raise ErasedReceipt("previous request completed and its response has been erased or expired")
        return get_trace(db,previous["trace_id"])["output"]
    trace_id = ident("tr")
    diagnostic = ({"reported_by_agent": args["telemetry"]} if "telemetry" in args else {})
    if op == "start_run":
        run_id = ident("run")
        parent = args.get("parent_run_id")
        summary = None
        if parent is not None:
            previous_run = row_or_error(db.execute("SELECT * FROM agent_runs WHERE run_id=?",
                                         (string(parent,"parent_run_id",120),)).fetchone(),"parent run")
            if previous_run["status"] != "closed":
                raise Conflict("close the parent with a handoff first")
            summary = previous_run["summary"]
        now = utc()
        title = string(args["title"],"title",256)
        db.execute("""INSERT INTO agent_runs(run_id,title,actor,parent_run_id,started_at,updated_at)
            VALUES(?,?,?,?,?,?)""", (run_id,title,actor,parent,now,now))
        result = {"run_id":run_id,"title":title,"status":"open","parent_run_id":parent,"handoff":summary,"trace_id":trace_id}
    elif op == "recall" and args.get("run_id") is None:
        run_id = None
        result, diagnostic = agent_recall.build(ledger,db,args,trace_id)
    else:
        run_id = string(args["run_id"],"run_id",120)
        run = row_or_error(db.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,)).fetchone(),"run")
        if run["actor"] != actor:
            raise ValueError("start a run under your identity to record new events")
        if run["status"] != "open":
            raise Conflict("run is closed; start a child run for a handoff")
        check_sources(db,args.get("source_ids",[]))
        result = {"run_id":run_id,"trace_id":trace_id}
        if op == "recall":
            result, diagnostic = agent_recall.build(ledger,db,args,trace_id)
        elif op == "run_note":
            result["text"] = string(args["text"],"text",32000)
        elif op == "handoff":
            summary = string(args["summary"],"summary",16000)
            db.execute("UPDATE agent_runs SET status='closed',summary=? WHERE run_id=?", (summary,run_id))
            result.update(status="closed",summary=summary)
        elif op == "outcome":
            success = args["success"]
            if type(success) is not bool:
                raise ValueError("success must be a boolean")
            result.update(success=success,detail=string(args["detail"],"detail",32000))
            if "lesson" in args:
                result["lesson"] = propose(ledger,db,args["lesson"],run_id,trace_id,actor,success,trusted)
    record(db,trace_id,run_id,op,actor,args,result,diagnostic,start)
    db.execute("INSERT INTO agent_requests VALUES(?,?,?,?)", (actor,request_id,fingerprint,trace_id))
    return result


def call(ledger, op, args, *, actor, admin=False, trusted=False):
    """The transport supplies verified identity and namespace authority."""
    if not isinstance(op,str) or op not in RUN_OPS or not isinstance(args,dict):
        raise ValueError("unknown run operation or invalid arguments")
    string(actor,"actor",256)
    _, required, fields = RUN_OPS[op]
    if set(args)-fields.keys() or set(required)-args.keys():
        raise ValueError("unknown or missing arguments for "+op)
    if op in ADMIN_OPS and not admin:
        raise PermissionError("namespace administrator permission required")
    if "telemetry" in args:
        validated_telemetry(args["telemetry"])
    if op in SESSION_OPS:
        start = time.perf_counter()
        with ledger._tx(write=True,snapshot=True) as db:
            expire(db)
            db.execute("SAVEPOINT run_operation")
            try:
                result = session(ledger,db,op,args,actor,trusted)
            except (ValueError, OverflowError) as exc:
                db.execute("ROLLBACK TO run_operation")
                error = exc
                result = {"error": str(exc)}
                owned = db.execute("SELECT run_id FROM agent_runs WHERE run_id=? AND actor=?",
                                   (str(args.get("run_id","")),actor)).fetchone()
                if not isinstance(error, ErasedReceipt):
                    record(db,ident("tr"),owned[0] if owned else None,op,actor,args,result,{"failed":True},start)
            else:
                error = None
            db.execute("RELEASE run_operation")
        if error:
            raise error
        return result
    writes = op in ADMIN_OPS or op == "compare_trace"
    if not writes:
        with ledger._tx() as probe:
            writes = bool(probe.execute("SELECT 1 FROM run_policy WHERE retention_days IS NOT NULL").fetchone())
    with ledger._tx(write=writes,snapshot=True) as db:
        if writes:
            expire(db)
        if op == "runs":
            return list_runs(db,args)
        if op == "activity":
            return activity(db,args)
        if op == "trace":
            return get_trace(db,args["trace_id"])
        if op == "compare_trace":
            start = time.perf_counter()
            saved = get_trace(db,args["trace_id"])
            if saved["op"] != "recall":
                raise ValueError("only recall traces can be compared")
            options = {k:v for k,v in saved["input"].items() if k not in ("as_of","known_at")}
            current, diag = agent_recall.build(ledger,db,options,ident("tr"))
            diag["compared_to"] = saved["trace_id"]
            record(db,current["trace_id"],saved["run_id"],"recall",actor,options,current,diag,start)
            return {"recorded":saved["output"],"current":current}
        if op == "run_detail":
            run_id = string(args["run_id"],"run_id",120)
            run = row_or_error(db.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,)).fetchone(),"run")
            events = [dict(r) for r in db.execute("""SELECT trace_id,op,actor,at,latency_ms
                FROM traces WHERE run_id=? ORDER BY at""", (run_id,))]
            return {"run":run,"events":events}
        if op == "lessons":
            status = args.get("status")
            if status is not None and status not in ("pending","approved","rejected","retracted"):
                raise ValueError("unknown lesson status")
            limit,offset = page(args)
            rows = db.execute("""SELECT lesson_id FROM lesson_queue WHERE (? IS NULL OR status=?)
                ORDER BY created_at DESC LIMIT ? OFFSET ?""", (status,status,limit+1,offset)).fetchall()
            return {"items":[lesson_row(db,r[0]) for r in rows[:limit]],"more":len(rows)>limit}
        if op == "get_policy":
            return get_policy(db)
        if op == "erase_source":
            return {"erased_documents":erase_source_tx(db,args["request_id"])}
        start = time.perf_counter()
        if op == "review_lesson":
            result = decision(ledger,db,string(args["lesson_id"],"lesson_id",120),args["decision"],actor)
        else:
            result = set_policy(db,args)
        record(db,ident("tr"),None,op,actor,args,result,{},start)
        return result


def propose(ledger, db, spec, run_id, trace_id, actor, success, trusted):
    if not isinstance(spec,dict) or set(spec) != {"type","key","value","source_id"}:
        raise ValueError("lesson requires type, key, value and source_id")
    kind, key, value = spec["type"],valid_key(spec["key"]),string(spec["value"],"value",1024)
    if not isinstance(kind,str) or not TYPE.fullmatch(kind):
        raise ValueError("lesson type must be a lowercase identifier")
    source = _source(db,spec["source_id"])
    if value not in source["body"]:
        raise ValueError("lesson value must occur in the cited original")
    db.execute("INSERT OR IGNORE INTO lesson_queue(lesson_id,type,key,value,created_at) VALUES(?,?,?,?,?)",
               (ident("ls"),kind,key,value,utc()))
    lesson_id, status = db.execute("SELECT lesson_id,status FROM lesson_queue WHERE type=? AND key=? AND value=?",
                                   (kind,key,value)).fetchone()
    db.execute("INSERT INTO lesson_support VALUES(?,?,?,?,?,?,?)",
               (lesson_id,trace_id,run_id,source["request_id"],actor,int(success),int(trusted)))
    types = json.loads(db.execute("SELECT auto_types FROM run_policy").fetchone()[0])
    if status == "pending" and kind in types:
        votes = supports(db,lesson_id)
        corroborated = any(a["run_id"] != b["run_id"] and a["actor"] != b["actor"] and
                           a["source_id"] != b["source_id"] and
                           _source(db,a["source_id"])["body"] != _source(db,b["source_id"])["body"]
                           for i,a in enumerate(votes) for b in votes[:i])
        current = ledger._resolve(db,key,utc(),utc())
        unopposed = current["status"] == "unknown" or (current["status"] == "current" and current["value"] == value)
        conflicting = db.execute("""SELECT 1 FROM lesson_queue WHERE key=? AND value!=?
            AND status IN ('pending','approved') LIMIT 1""",(key,value)).fetchone()
        if unopposed and not conflicting and all(v["success"] for v in votes) and (trusted or corroborated):
            return decision(ledger,db,lesson_id,"approve","policy")
    return lesson_row(db,lesson_id)

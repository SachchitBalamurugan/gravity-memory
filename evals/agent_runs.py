"""Compare a pinned Gravity 0.3 checkout and the working 0.4 release on two-session tasks."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name("agent-cases.json")
BASE = "c317fb4"


def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()


def baseline_bundle(ctx, budget):
    # Adapt published 0.3 context to the same evidence fields and bytes. Keep its
    # original result order. Never discard a passage on a freshness or role signal.
    candidates = []
    for fact in ctx["facts"]:
        ev = fact["evidence"][0]
        candidates.append({"kind":"fact","key":fact["key"],"value":fact["value"],"source_id":ev["request_id"],
                           **{k:ev[k] for k in ("source","quote","start","stop","effective_at","recorded_at")}})
    for p in ctx["passages"]:
        candidates.append({"kind":"passage","source_id":p["request_id"],
                           **{k:p[k] for k in ("source","quote","start","stop","effective_at","recorded_at")}})
    reply = {"items":[],"route":ctx["route"]}
    for item in candidates:
        reply["items"].append(item)
        if len(json.dumps(reply).encode()) > budget:
            reply["items"].pop()
    return reply


def source_write(ledger, row, day):
    options = {"role":row.get("role","document"),"effective_at":f"2024-01-{row.get('at',day):02d}T00:00:00Z"}
    if "key" in row:
        fact = {"key":row["key"],"value":row["value"],"quote":row["text"]}
        if "expires_at" in row:
            fact["expires_at"] = row["expires_at"]
        options["facts"] = [fact]
    ledger.remember(row["id"],row["text"],row["id"],**options)


def worker(engine, root, temp):
    sys.path.insert(0,str(root))
    from gravity_memory import Ledger
    if engine != "0.3":
        from gravity_memory.runs import call
        if engine == "0.4-first-pass":
            from unittest.mock import patch
            patch("gravity_memory.agent_recall.question_fields",return_value=set()).start()
    manifest=json.loads(MANIFEST.read_text(encoding="utf-8"))
    budget=manifest["budget_bytes"]
    results=[]
    for group in ("application","coding"):
        for idx, task in enumerate(manifest[group]):
            path=temp/f"{group}-{idx}.sqlite"
            writer = Ledger(path)
            if engine != "0.3":
                run_id=call(writer,"start_run",{"request_id":"w","title":"Intake"},actor="first")["run_id"]
            for day,row in enumerate(task["writes"],1):
                source_write(writer,row,day)
            if "lesson" in task:
                lesson = task["lesson"]
                if engine != "0.3":
                    proposal=call(writer,"outcome",{"request_id":"o","run_id":run_id,"success":True,
                        "detail":"Observed trial result","lesson":{k:v for k,v in lesson.items() if k!="decision"}},
                        actor="first")["lesson"]["lesson_id"]
                    call(writer,"review_lesson",{"lesson_id":proposal,"decision":"approve"},actor="operator",admin=True)
                    if lesson["decision"]=="retract":
                        call(writer,"review_lesson",{"lesson_id":proposal,"decision":"retract"},actor="operator",admin=True)
                else:
                    writer.annotate("approve",lesson["source_id"],[{"key":lesson["key"],"value":lesson["value"],"quote":lesson["value"]}])
                    if lesson["decision"]=="retract":
                        writer.retire(lesson["key"],"The recorded trial was withdrawn.","retract")
            if engine != "0.3":
                call(writer,"handoff",{"request_id":"h","run_id":run_id,"summary":"Intake complete."},actor="first")
            reader=Ledger(path)
            query={"query":task["query"],**{k:task[k] for k in ("as_of","known_at") if k in task}}
            if engine != "0.3":
                new_run=call(reader,"start_run",{"request_id":"r","title":"Follow-up","parent_run_id":run_id},actor="second")["run_id"]
            start=time.perf_counter()
            if engine!="0.3":
                reply=call(reader,"recall",{**query,"run_id":new_run,"request_id":"q","budget_bytes":budget,
                           "retrieval":"full_text","limit":8},actor="second")
            else:
                reply=baseline_bundle(reader.context(**query,retrieval="full_text",limit=8),budget)
            ms=round((time.perf_counter()-start)*1000,3)
            items=reply["items"]
            top=items[0] if items else {}
            action=top.get("value",top.get("quote"))
            stale=[s for s in task["obsolete"] if any(s.casefold() in r["quote"].casefold() for r in items)]
            results.append({"group":group,"name":task["name"],"expected":task["expected"],"gold_source":task["source"],
                            "action":action,"source":top.get("source_id"),"correct":action==task["expected"] and
                            top.get("source_id")==task["source"],"stale_exposure":stale,
                            "latency_ms":ms,"response_bytes":len(canonical(reply))})
    applicable=sum(bool(t["obsolete"]) for g in ("application","coding") for t in manifest[g])
    unknown=[r for r in results if r["expected"] is None]
    ms=sorted(r["latency_ms"] for r in results)
    sizes=[r["response_bytes"] for r in results]
    return {"version":engine,"tasks":len(results),"correct":sum(r["correct"] for r in results),
            "correct_application":sum(r["correct"] for r in results if r["group"]=="application"),
            "correct_coding":sum(r["correct"] for r in results if r["group"]=="coding"),
            "abstention_tasks":len(unknown),"correct_abstentions":sum(r["correct"] for r in unknown),
            "obsolete_probes":applicable,"stale_exposures":sum(bool(r["stale_exposure"]) for r in results),
            "median_ms":round(statistics.median(ms),3),"p95_ms":ms[math.ceil(.95*len(ms))-1],
            "median_bytes":statistics.median(sizes),"max_bytes":max(sizes),"cases":results}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,default=Path(__file__).with_name("agent-results.json"))
    p.add_argument("--worker",choices=["0.3","0.4-first-pass","0.4"])
    p.add_argument("--root",type=Path)
    p.add_argument("--temp",type=Path)
    args=p.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker,args.root,args.temp)))
        return
    with tempfile.TemporaryDirectory(prefix="gravity-comparison-") as folder:
        stage=Path(folder)
        archive=stage/"old.zip"
        subprocess.run(["git","archive","--format=zip","--output",str(archive),BASE],cwd=ROOT,check=True)
        with ZipFile(archive) as old:
            old.extractall(stage/"old")
        env={**os.environ,"PYTHONUTF8":"1","GRAVITY_MODEL_DIR":str(stage/"models")}
        reports=[]
        for version,home in [("0.3",stage/"old"),("0.4-first-pass",ROOT),("0.4",ROOT)]:
            databases=stage/version
            databases.mkdir()
            child=subprocess.run([sys.executable,str(Path(__file__).resolve()),"--worker",version,
                "--root",str(home),"--temp",str(databases)],capture_output=True,text=True,encoding="utf-8",env=env,check=True)
            reports.append(json.loads(child.stdout))
    report={"fixture_sha256":hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),"old_commit":BASE,
            "python":platform.python_version(),"sqlite":sqlite3.sqlite_version,"system":platform.system(),
            "budget_bytes":json.loads(MANIFEST.read_text())["budget_bytes"],"reports":reports}
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for r in reports:
        print({k:v for k,v in r.items() if k!="cases"})

if __name__=="__main__":
    main()

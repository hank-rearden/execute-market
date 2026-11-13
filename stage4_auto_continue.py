#!/usr/bin/env python3
"""One-dispatch Stage 4 continuation, with durable per-batch commits.

Runs after the already-published archive; never modifies production history.
"""
import csv,json,subprocess,sys
from datetime import date,timedelta,datetime,timezone
from pathlib import Path

BATCH_DAYS=180
ARCHIVE=Path("data/stage4_udiff_history.csv")
STATUS=Path("reports/stage4_archive_status.json")
AUDIT=Path("reports/stage4_corporate_action_audit.json")

def run(*args):
    subprocess.run(list(args),check=True)

def validate():
    s=json.loads(STATUS.read_text())
    a=json.loads(AUDIT.read_text())
    if s["archive_sessions"]<1 or s["corporate_action_adjusted"] or s["historical_membership_verified"] or a["corporate_actions_verified"]:
        raise RuntimeError("Archive safety flags invalid")
    with ARCHIVE.open(newline="",encoding="utf-8") as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=["date","symbol","open","high","low","close","volume"]:
            raise RuntimeError("Archive schema invalid")
        seen=set();count=0
        for row in reader:
            key=(row["date"],row["symbol"])
            if key in seen:raise RuntimeError("Duplicate "+str(key))
            seen.add(key);count+=1
    if count!=s["archive_records"]:raise RuntimeError("Record count mismatch")
    return s

def main():
    if not ARCHIVE.exists() or not STATUS.exists():
        raise SystemExit("Missing existing archive/status; no automatic start possible")
    old=json.loads(STATUS.read_text())
    start=date.fromisoformat(old["last_date"])+timedelta(days=1)
    # Today's bhavcopy may not yet be published; end at yesterday.
    end=datetime.now(timezone.utc).date()-timedelta(days=1)
    print("AUTO CONTINUATION",start,"through",end,flush=True)
    if start>end:
        print("Already up to date");return
    run("git","config","user.name","github-actions[bot]")
    run("git","config","user.email","41898282+github-actions[bot]@users.noreply.github.com")
    batch=0
    while start<=end:
        length=min(BATCH_DAYS,(end-start).days+1)
        print("BATCH",batch+1,"start",start,"days",length,flush=True)
        run(sys.executable,"stage4_archive.py","--start",start.isoformat(),"--days",str(length))
        run(sys.executable,"stage4_audit.py")
        status=validate()
        run("git","add",str(ARCHIVE),str(STATUS),str(AUDIT))
        if subprocess.run(["git","diff","--cached","--quiet"]).returncode!=0:
            run("git","commit","-m",f"Stage 4 auto archive batch {batch+1} through {(start+timedelta(days=length-1)).isoformat()}")
            # Preserve each validated batch on remote before continuing.
            run("git","pull","--rebase","origin","main")
            run("git","push","origin","HEAD:main")
        print("SAVED BATCH",batch+1,"sessions",status["archive_sessions"],"records",status["archive_records"],flush=True)
        start+=timedelta(days=length)
        batch+=1
    print("AUTOMATIC CONTINUATION COMPLETE",batch,"batches",flush=True)
if __name__=="__main__":main()

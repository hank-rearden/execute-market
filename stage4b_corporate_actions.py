#!/usr/bin/env python3
"""Stage 4B: fail-closed corporate-action review and independently adjusted research prices.

Only explicit, source-documented, approved events can alter historical OHLCV.
Never modify the original UDiFF archive. Not a substitute for full NSE event reconciliation.
"""
import csv,json,math
from collections import defaultdict
from datetime import datetime,timezone,date
from pathlib import Path

RAW=Path("data/stage4_udiff_history.csv")
FLAGS=Path("reports/stage4_corporate_action_audit.json")
EVENTS=Path("data/stage4b_verified_actions.csv")
REVIEW=Path("reports/stage4b_review.csv")
OUT=Path("data/stage4b_adjusted_history.csv")
REPORT=Path("reports/stage4b_status.json")
FIELDS=["date","symbol","open","high","low","close","volume"]
EVENT_FIELDS=["symbol","ex_date","action_type","price_divisor","source_url","source_description","verified_by","verification_date","approved"]
def readcsv(path):
    with path.open(newline="",encoding="utf-8") as f:return list(csv.DictReader(f))
def writecsv(path,fields,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def events():
    approved=[]
    if not EVENTS.exists():raise RuntimeError("Missing verification ledger")
    for e in readcsv(EVENTS):
        if not any(e.values()):continue
        if e["approved"].strip().lower()!="yes":continue
        if not all(e.get(k,"").strip() for k in ("symbol","ex_date","action_type","price_divisor","source_url","source_description","verified_by","verification_date")):
            raise RuntimeError("Approved event lacks documented evidence: "+str(e))
        date.fromisoformat(e["ex_date"]);date.fromisoformat(e["verification_date"])
        if not e["source_url"].startswith("https://"):raise RuntimeError("Invalid source URL")
        factor=float(e["price_divisor"])
        if not math.isfinite(factor) or factor<=0 or factor==1:raise RuntimeError("Invalid adjustment factor")
        if e["action_type"].lower() not in ("split","bonus","consolidation","split_bonus"):
            raise RuntimeError("Complex actions need separately validated adjustment methodology")
        approved.append((e["symbol"],e["ex_date"],factor,e))
    if len({(s,d) for s,d,_,_ in approved})!=len(approved):
        raise RuntimeError("Duplicate approved events")
    return approved
def main():
    raw=readcsv(RAW)
    flags=json.loads(FLAGS.read_text())["flags"]
    ledger=events()
    bysym=defaultdict(list)
    for s,d,f,e in ledger:bysym[s].append((d,f))
    for s in bysym:bysym[s].sort()
    reviewed={(s,d) for s,d,_,_ in ledger}
    review=[]
    for f in flags:
        key=(f["symbol"],f["date"])
        review.append({"symbol":key[0],"event_date":key[1],"previous_date":f["previous_date"],"previous_close":f["previous_close"],"close":f["close"],"change_pct":f["change_pct"],"status":"APPROVED_LEDGER_EVENT" if key in reviewed else "UNVERIFIED_REQUIRES_NSE_EVIDENCE","source":"NSE corporate filings: https://www.nseindia.com/companies-listing/corporate-filings-actions"})
    writecsv(REVIEW,["symbol","event_date","previous_date","previous_close","close","change_pct","status","source"],review)
    # Prices back-adjusted onto the last-date share basis; volume inversely adjusted.
    # No adjustment at all unless there is an approved ledger entry.
    adjusted=[]
    for r in raw:
        factor=1.0
        for ex,div in bysym.get(r["symbol"],[]):
            if r["date"]<ex:factor*=div
        row=dict(r)
        for field in ("open","high","low","close"):
            row[field]=format(float(r[field])/factor,".10g")
        row["volume"]=format(float(r["volume"])*factor,".12g")
        row["cumulative_price_divisor"]=format(factor,".10g")
        adjusted.append(row)
    writecsv(OUT,FIELDS+["cumulative_price_divisor"],adjusted)
    report={"generated_utc":datetime.now(timezone.utc).isoformat(),"raw_rows":len(raw),"adjusted_rows":len(adjusted),"flags":len(flags),"approved_events":len(ledger),"unverified_flags":sum(x["status"].startswith("UNVERIFIED") for x in review),"raw_archive_unchanged":True,"fully_corporate_action_verified":False,"historical_membership_verified":False,"eligible_for_real_money":False,"status":"RESEARCH ONLY — VERIFIED EVENTS PARTIAL; DO NOT BACKTEST AS FULLY ADJUSTED"}
    REPORT.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":main()

#!/usr/bin/env python3
"""Append-only, no-lookahead EOD signal journal for prospective paper evaluation.

Records candidates only after daily screener; never treats a signal as a filled trade.
Repeated workflow runs on the same data date are idempotent.
"""
import csv,json
from datetime import datetime,timezone,date
from pathlib import Path
SRC=Path("reports/screening.json")
ENH=Path("reports/enhanced_stage2.json")
OUT=Path("data/paper_signal_journal.csv")
FIELDS=["signal_date","symbol","setup","baseline_status","enhanced_score","entry_trigger","stop","target","quantity","estimated_net_risk","estimated_net_rr","signal_generated_utc","execution_status","notes"]
def main():
    base=json.loads(SRC.read_text());enh=json.loads(ENH.read_text())
    if not base.get("paper_only") or not enh.get("paper_only"):raise SystemExit("Paper-only inputs required")
    scores={r["symbol"]:r for r in enh["results"]}
    records=[]
    for r in base["results"]:
        if r["status"]!="WATCHLIST ONLY - UNCONFIRMED":continue
        if r["symbol"] not in scores or not all(r.get(k) is not None for k in ("entry","stop","target","net_risk","net_rr")):raise SystemExit("Missing signal risk data")
        if not (0<r["stop"]<r["entry"]<r["target"]) or r["net_risk"]>500 or r["quantity"]<=0:raise SystemExit("Risk gate failed")
        if date.fromisoformat(r["data_date"])>datetime.now(timezone.utc).date():raise SystemExit("Future data")
        records.append(dict(signal_date=r["data_date"],symbol=r["symbol"],setup=r["setup"],baseline_status=r["status"],enhanced_score=scores[r["symbol"]]["enhanced_score"],entry_trigger=r["entry"],stop=r["stop"],target=r["target"],quantity=r["quantity"],estimated_net_risk=r["net_risk"],estimated_net_rr=r["net_rr"],signal_generated_utc=base["generated_utc"],execution_status="NOT_EXECUTED_UNCONFIRMED",notes="Prospective signal only; no order or fill inferred"))
    old=[]
    if OUT.exists():
        with OUT.open(newline="",encoding="utf-8") as f:old=list(csv.DictReader(f))
    keys={(r["signal_date"],r["symbol"]) for r in old}
    new=[r for r in records if (r["signal_date"],r["symbol"]) not in keys]
    OUT.parent.mkdir(parents=True,exist_ok=True)
    with OUT.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(old+new)
    print(json.dumps(dict(existing=len(old),added=len(new),total=len(old)+len(new),paper_only=True,no_fills_assumed=True)))
if __name__=="__main__":main()

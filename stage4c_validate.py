#!/usr/bin/env python3
"""Stage 4C independent data-quality and point-in-time membership audit.

Never certify membership from today's list. Historical membership is an optional
locally supplied, provenance-documented CSV with symbol,valid_from,valid_to
(half-open intervals). Missing membership fails closed.
"""
import csv,json,math,collections
from pathlib import Path
from datetime import datetime,timezone,date
RAW=Path("data/stage4_udiff_history.csv")
ADJ=Path("data/stage4b_backtest_eligible_history.csv")
MEM=Path("data/stage4c_membership_intervals.csv")
MEM_EVIDENCE=Path("data/stage4c_membership_sources.csv")
REPORT=Path("reports/stage4c_readiness.json")
ISSUES=Path("reports/stage4c_issues.csv")
PIT=Path("data/stage4c_point_in_time_history.csv")
def read(p):
    with p.open(newline="",encoding="utf-8") as f:return list(csv.DictReader(f))
def write(p,fields,rows):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def main():
    raw=read(RAW);adjusted=read(ADJ)
    issues=[]
    def issue(kind,symbol,day,detail):
        issues.append(dict(kind=kind,symbol=symbol,date=day,detail=detail))
    seen=set();days=collections.defaultdict(set);by_symbol=collections.defaultdict(list)
    for r in raw:
        key=(r["date"],r["symbol"])
        if key in seen:issue("DUPLICATE",r["symbol"],r["date"],"duplicate date and symbol")
        seen.add(key);days[r["date"]].add(r["symbol"]);by_symbol[r["symbol"]].append(r)
        try:
            o,h,l,c,v=[float(r[k]) for k in ("open","high","low","close","volume")]
            if not all(math.isfinite(x) for x in (o,h,l,c,v)) or min(o,h,l,c)<=0 or v<0 or h<max(o,c,l) or l>min(o,c,h):
                issue("INVALID_OHLCV",r["symbol"],r["date"],"nonfinite, negative, or inconsistent OHLCV")
        except (ValueError,KeyError):issue("INVALID_OHLCV",r.get("symbol",""),r.get("date",""),"unparseable OHLCV")
    for s,rows in by_symbol.items():
        rows.sort(key=lambda x:x["date"])
        for a,b in zip(rows,rows[1:]):
            try:
                pct=abs(float(b["close"])/float(a["close"])-1)*100
                if pct>=10 and pct<25:issue("UNRECONCILED_10_TO_25_PERCENT_MOVE",s,b["date"],"raw close move %.3f%%; not proof of corporate action"%pct)
            except (ValueError,ZeroDivisionError):pass
    membership=read(MEM) if MEM.exists() else []
    evidence=read(MEM_EVIDENCE) if MEM_EVIDENCE.exists() else []
    verified_sources={e.get("source_id") for e in evidence if e.get("verified","").lower()=="yes" and e.get("official_url","").startswith("https://")}
    valid_intervals=[];bad_intervals=0
    for m in membership:
        try:
            start=date.fromisoformat(m["valid_from"]);end=date.fromisoformat(m["valid_to"]) if m.get("valid_to") else date.max
            if not m["symbol"] or start>=end or m.get("source_id") not in verified_sources:raise ValueError("missing verified provenance")
            valid_intervals.append((m["symbol"],start,end))
        except (ValueError,KeyError):bad_intervals+=1
    # Require a complete, date-specific 100-stock universe before using PIT output.
    pit=[]
    incomplete_dates=[]
    if valid_intervals and not bad_intervals:
        for day,symbols in sorted(days.items()):
            d=date.fromisoformat(day)
            members={s for s,start,end in valid_intervals if start<=d<end}
            if len(members)!=100:
                incomplete_dates.append(dict(date=day,expected=100,observed=len(members),reason="Membership intervals not exactly 100"))
                continue
            available={r["symbol"] for r in adjusted if r["date"]==day}
            missing=members-available
            if missing:
                incomplete_dates.append(dict(date=day,expected=100,observed=len(members&available),reason="Historical members missing from price archive: "+",".join(sorted(missing)[:10])))
                continue
            pit.extend(r for r in adjusted if r["date"]==day and r["symbol"] in members)
    else:
        incomplete_dates=[dict(date=d,expected=100,observed=0,reason="No fully sourced historical membership intervals") for d in days]
    write(PIT,list(adjusted[0]) if adjusted else ["date","symbol","open","high","low","close","volume","cumulative_price_divisor"],pit)
    write(ISSUES,["kind","symbol","date","detail"],issues)
    report=dict(generated_utc=datetime.now(timezone.utc).isoformat(),raw_rows=len(raw),stage4b_conservative_rows=len(adjusted),trading_sessions=len(days),symbols=len(by_symbol),data_quality_issue_count=sum(i["kind"] in ("DUPLICATE","INVALID_OHLCV") for i in issues),unreconciled_10_to_25_percent_moves=sum(i["kind"]=="UNRECONCILED_10_TO_25_PERCENT_MOVE" for i in issues),membership_intervals=len(valid_intervals),membership_bad_or_unsourced_intervals=bad_intervals,membership_verified_source_count=len(verified_sources),complete_point_in_time_sessions=len(days)-len(incomplete_dates),incomplete_point_in_time_sessions=len(incomplete_dates),point_in_time_rows=len(pit),historical_membership_verified=len(incomplete_dates)==0 and bool(days),corporate_action_coverage_complete=False,dividend_total_return_verified=False,backtest_ready=False,real_money_ready=False,readiness="BLOCKED — historical membership, complete corporate actions, and dividends not certified",limitations=["Current-universe-only archive cannot recover prices of historical constituents absent from archive","10-25 percent close jumps are screening flags, not confirmed corporate actions","Dividends and sub-10-percent corporate actions not reconciled","No independent historical constituent list verified across every date","No official Nifty 100 TRI comparison"],sample_incomplete_dates=incomplete_dates[:10])
    REPORT.parent.mkdir(parents=True,exist_ok=True);REPORT.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
    if report["data_quality_issue_count"]:raise SystemExit("Critical OHLCV quality issues; inspect stage4c_issues.csv")
if __name__=="__main__":main()

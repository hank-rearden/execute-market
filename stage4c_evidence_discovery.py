#!/usr/bin/env python3
"""Stage 4C evidence discovery: independent historical membership candidate and move triage.
Third-party memberships are NEVER promoted to certified PIT data.
"""
import csv,io,json,urllib.request,collections
from pathlib import Path
from datetime import datetime,timezone
URL="https://raw.githubusercontent.com/aditya-jha/nse-historical-membership/main/index_history/data/index_membership_history.csv"
OUT=Path("reports/stage4c_membership_candidate_audit.json")
GAPS=Path("reports/stage4c_historical_membership_gaps.csv")
MOVES=Path("reports/stage4c_price_move_triage.csv")
RAW=Path("data/stage4_udiff_history.csv")
ISSUES=Path("reports/stage4c_issues.csv")
def read(p):
    with p.open(newline="",encoding="utf-8") as f:return list(csv.DictReader(f))
def write(p,fields,rows):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def main():
    raw=read(RAW)
    days=sorted({r["date"] for r in raw})
    byday=collections.defaultdict(set)
    for r in raw:byday[r["date"]].add(r["symbol"])
    moves=[]
    for r in read(ISSUES):
        if r["kind"]=="UNRECONCILED_10_TO_25_PERCENT_MOVE":
            moves.append(dict(symbol=r["symbol"],date=r["date"],description=r["detail"],status="UNVERIFIED_MOVE_NOT_AUTOMATICALLY_CORPORATE_ACTION",official_source_url="",approved_adjustment_factor=""))
    write(MOVES,["symbol","date","description","status","official_source_url","approved_adjustment_factor"],moves)
    report=dict(generated_utc=datetime.now(timezone.utc).isoformat(),source_url=URL,source_type="UNOFFICIAL_THIRD_PARTY_RECONSTRUCTION",certified=False,archive_sessions=len(days),price_move_flags=len(moves),candidate_schema=None,candidate_intervals=0,missing_historical_member_symbol_days=0,missing_historical_member_symbols=[],source_fetch_error=None,limitations=["Candidate membership must be checked against NSE circulars and effective dates","Missing historical securities require their own historical OHLCV","No dividend or corporate-action completeness claim"])
    gaps=[]
    try:
        req=urllib.request.Request(URL,headers={"User-Agent":"execute-market-research/1.0"})
        with urllib.request.urlopen(req,timeout=30) as response:
            body=response.read(12000000).decode("utf-8-sig")
        records=list(csv.DictReader(io.StringIO(body)))
        fields=list(records[0]) if records else []
        report["candidate_schema"]=fields
        # Inspect schema rather than guessing; unsupported schemas never produce PIT membership.
        lower={x.lower().strip():x for x in fields}
        symbol=next((lower[x] for x in ("symbol","ticker") if x in lower),None)
        start=next((lower[x] for x in ("valid_from","start_date","date_from") if x in lower),None)
        end=next((lower[x] for x in ("valid_to","end_date","date_to") if x in lower),None)
        index=next((lower[x] for x in ("index_name","index","index_id","index_code") if x in lower),None)
        if symbol and start and index:
            intervals=[]
            for r in records:
                name=r[index].strip().upper().replace(" ","")
                if name not in ("NIFTY100","219","NIFTY_100"):continue
                a=r[start].strip()[:10];b=r[end].strip()[:10] if end and r[end] else "9999-12-31"
                if len(a)==10 and a[4]=="-" and b[4]=="-":intervals.append((r[symbol].strip(),a,b))
            report["candidate_intervals"]=len(intervals)
            absent=set()
            for d in days:
                members={s for s,a,b in intervals if a<=d<b}
                missing=members-byday[d]
                for s in sorted(missing):
                    gaps.append(dict(date=d,symbol=s,source_type="THIRD_PARTY_CANDIDATE",status="MISSING_FROM_CURRENT_UNIVERSE_ARCHIVE"))
                    absent.add(s)
            report["missing_historical_member_symbol_days"]=len(gaps)
            report["missing_historical_member_symbols"]=sorted(absent)
            report["candidate_membership_coverage_sessions"]=sum(1 for d in days if len({s for s,a,b in intervals if a<=d<b})==100)
        else:report["source_fetch_error"]="Unsupported candidate schema; no guessed parsing"
    except Exception as e:report["source_fetch_error"]=str(e)[:500]
    write(GAPS,["date","symbol","source_type","status"],gaps)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":main()

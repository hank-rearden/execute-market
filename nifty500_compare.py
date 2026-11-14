#!/usr/bin/env python3
"""Nifty 100 vs Nifty 500 research comparison. No portfolio execution or live orders."""
import csv,json,math,statistics
from collections import defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
from screener import analyze,charges,SLIPPAGE_RATE
from enhanced_stage2 import rank_stock
BASE=Path("data/stage4_udiff_history.csv")
EXPANDED=Path("data/nifty500_research_history.csv")
OUT=Path("reports/nifty500_comparison.json")
MIN_DAILY_VALUE=5e7
HOLD=10
def load(path):
    data=defaultdict(list);bad=set();seen=set()
    with path.open(newline="",encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            s=r["symbol"];d=r["date"];k=(s,d)
            if k in seen:bad.add(s);continue
            seen.add(k)
            try:
                row={"date":d,"open":float(r["open"]),"high":float(r["high"]),"low":float(r["low"]),"close":float(r["close"]),"volume":int(float(r["volume"]))}
                if not(0<row["low"]<=min(row["open"],row["close"])<=max(row["open"],row["close"])<=row["high"] and row["volume"]>=0):bad.add(s);continue
                data[s].append(row)
            except (ValueError,OverflowError):bad.add(s)
    for s,rows in data.items():
        rows.sort(key=lambda x:x["date"])
        if any(abs(b["close"]/a["close"]-1)>.20 for a,b in zip(rows,rows[1:])):bad.add(s)
    # Global exclusions determined before performance inspection; not selected by profitability.
    return data,bad
def simulate(rows,idx,b):
    trigger=b["entry"];stop=b["stop"];target=b["target"]
    if idx+HOLD>=len(rows):return None
    nextbar=rows[idx+1]
    if nextbar["high"]<trigger:return None
    entry=max(nextbar["open"],trigger)*(1+SLIPPAGE_RATE)
    if nextbar["low"]<=stop:
        raw=min(stop,nextbar["open"]);end=idx+1
    else:
        raw=None;end=None
        for j in range(idx+2,idx+HOLD+1):
            bar=rows[j]
            if bar["open"]<=stop:raw=bar["open"]
            elif bar["low"]<=stop:raw=stop
            elif bar["open"]>=target or bar["high"]>=target:raw=target
            if raw is not None:end=j;break
        if raw is None:end=idx+HOLD;raw=rows[end]["close"]
    exitprice=raw*(1-SLIPPAGE_RATE)
    qty=max(1,int(10000/entry))
    net=(exitprice-entry)*qty-charges(entry,exitprice,qty)
    return round(net/(entry*qty)*100,4)
def main():
    if not BASE.exists() or not EXPANDED.exists():raise SystemExit("Both archives required")
    a,bad100=load(BASE);b,bad500=load(EXPANDED)
    audit=json.loads(Path("reports/stage4_corporate_action_audit.json").read_text())
    bad=bad100|bad500|{r["symbol"] for r in audit["flags"]}
    dates100={d for rows in a.values() for d in [r["date"] for r in rows]}
    dates500={d for rows in b.values() for d in [r["date"] for r in rows]}
    common=sorted(dates100&dates500)
    if len(common)<250:raise SystemExit("Need at least 250 overlapping trading sessions for comparative research; collected "+str(len(common)))
    cutoff=common[int(len(common)*.70)]
    def evaluate(data):
        picks=[];reject=defaultdict(int);eligible=set()
        for symbol,rows in data.items():
            if symbol in bad:reject["anomalous_symbol"]+=1;continue
            lookup={r["date"]:i for i,r in enumerate(rows)}
            for day in common[200:-HOLD:HOLD]:
                i=lookup.get(day)
                if i is None or i<200 or i+HOLD>=len(rows):continue
                hist=rows[:i+1]
                daily=sum(x["close"]*x["volume"] for x in hist[-20:])/20
                if daily<MIN_DAILY_VALUE:reject["liquidity"]+=1;continue
                eligible.add(symbol)
                baseline=analyze(symbol,[{**x,"date":date.fromisoformat(x["date"])} for x in hist],date.fromisoformat(day))
                if baseline["status"]!="WATCHLIST ONLY - UNCONFIRMED":reject["no_valid_setup"]+=1;continue
                enhanced=rank_stock(symbol,hist.copy(),baseline,asof_date=date.fromisoformat(day))
                if enhanced["enhanced_score"] is None:continue
                # Relative strength: trailing 63-session stock return versus the same-day
                # equal-weight current-universe proxy; computed only from observed past bars.
                if len(hist)<64:continue
                stock63=hist[-1]["close"]/hist[-64]["close"]-1
                peers=[]
                for other,otherrows in data.items():
                    if other in bad:continue
                    prior=[x for x in otherrows if x["date"]<=day]
                    if len(prior)>=64 and prior[-1]["date"]==day:
                        peers.append(prior[-1]["close"]/prior[-64]["close"]-1)
                if len(peers)<30:continue
                relative=stock63-statistics.mean(peers)
                pnl=simulate(rows,i,baseline)
                if pnl is None:reject["entry_not_triggered"]+=1;continue
                picks.append({"date":day,"symbol":symbol,"enhanced":enhanced["enhanced_score"],"relative_strength_pp":round(relative*100,3),"net_return_pct":pnl})
        return picks,dict(reject),len(eligible)
    # Identical common dates, identical execution rules; each model selects max one
    # trade per signal date. 500 proxy is a current-universe proxy, not official index.
    def report(picks):
        def summary(items):
            vals=[x["net_return_pct"] for x in items]
            gp=sum(v for v in vals if v>0);gl=-sum(v for v in vals if v<0)
            return {"trades":len(vals),"mean_net_return_pct":round(statistics.mean(vals),3) if vals else None,"win_rate_pct":round(sum(v>0 for v in vals)/len(vals)*100,2) if vals else None,"profit_factor":round(gp/gl,3) if gl else None}
        result={}
        for name,condition in [("enhanced",lambda x:True),("relative_strength_positive",lambda x:x["relative_strength_pp"]>0),("relative_strength_5pp",lambda x:x["relative_strength_pp"]>5)]:
            selected={}
            for p in picks:
                if condition(p):
                    k=p["date"]
                    if k not in selected or (p["enhanced"],p["relative_strength_pp"],p["symbol"])>(selected[k]["enhanced"],selected[k]["relative_strength_pp"],selected[k]["symbol"]):selected[k]=p
            rows=list(selected.values())
            result[name]={"development":summary([x for x in rows if x["date"]<cutoff]),"holdout":summary([x for x in rows if x["date"]>=cutoff])}
        return result
    p100,r100,e100=evaluate(a);p500,r500,e500=evaluate(b)
    result={"version":"nifty500-research-comparison-v1","generated_utc":datetime.now(timezone.utc).isoformat(),
      "paper_only":True,"ready_for_real_money":False,"common_sessions":len(common),"start":common[0],"end":common[-1],"holdout_cutoff":cutoff,
      "filters":{"min_20d_average_daily_traded_value_inr":MIN_DAILY_VALUE,"relative_strength_lookback_sessions":63,"min_history":201,"max_hold_sessions":HOLD,"anomaly_excluded_symbols":sorted(bad)},
      "nifty100":{"eligible_symbols":e100,"candidate_fills":len(p100),"rejections":r100,"strategies":report(p100)},
      "nifty500":{"eligible_symbols":e500,"candidate_fills":len(p500),"rejections":r500,"strategies":report(p500)},
      "limitations":["Current constituent membership, not historical membership","Unadjusted corporate actions; exclude suspicious symbols globally","Historical NSE turnover approximated by close times volume","Relative strength benchmark is equal-weight current-universe proxy, not official Nifty500 index","Selection and testing are exploratory; no frozen walk-forward model selected","One trade per ten-session signal date; not a portfolio NAV","Daily OHLC entry ordering uncertain; conservative stop-first","Fees and slippage estimated; capital-gains tax excluded","No real-money authorization"]}
    OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(result,indent=2))
    print(json.dumps({"common_sessions":len(common),"100":result["nifty100"]["strategies"],"500":result["nifty500"]["strategies"]},indent=2))
if __name__=="__main__":main()

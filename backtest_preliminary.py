#!/usr/bin/env python3
"""Preliminary point-in-time ranking study; NOT an execution backtest."""
import csv,json,statistics
from collections import defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
from screener import analyze
from enhanced_stage2 import rank_stock

HORIZONS=(5,10)
MIN_HISTORY=201
TOP=15
OUT=Path("reports")
def avg(xs):
    return round(statistics.mean(xs),4) if xs else None
def main():
    stocks=defaultdict(list)
    with Path("data/nifty100_history.csv").open(newline="",encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            stocks[r["symbol"]].append({"date":date.fromisoformat(r["date"]),
                "open":float(r["open"]),"high":float(r["high"]),
                "low":float(r["low"]),"close":float(r["close"]),"volume":int(r["volume"])})
    for rows in stocks.values(): rows.sort(key=lambda r:r["date"])
    days=sorted(set(r["date"] for rows in stocks.values() for r in rows))
    by_symbol={symbol:{r["date"]:i for i,r in enumerate(rows)} for symbol,rows in stocks.items()}
    observations=[]
    counts=defaultdict(int)
    # At each date, use only historical data up to that date. Return is close-to-close
    # from the signal close, not an executable entry or after-cost P&L.
    for day in days[MIN_HISTORY-1:-max(HORIZONS)]:
        base=[]
        enhanced=[]
        for symbol,rows in stocks.items():
            idx=by_symbol[symbol].get(day)
            if idx is None or idx+1<MIN_HISTORY: continue
            history=rows[:idx+1]
            baseline=analyze(symbol,history,day)
            base.append(baseline)
            erows=[{**r,"date":r["date"].isoformat()} for r in history]
            score=rank_stock(symbol,erows,baseline,asof_date=day)
            if score["enhanced_score"] is not None:
                enhanced.append(score)
        # Mirror original category priority (not just raw technical score).
        def category(r):
            if r["status"]=="WATCHLIST ONLY - UNCONFIRMED":return 0
            if r["setup"]!="NO SETUP":return 1
            if r["sessions"]>=MIN_HISTORY and r["close"]>r["ema20"]>r["ema50"]>r["ema200"] and 45<=r["rsi14"]<=75:return 2
            return 3
        baseline_top=sorted((r for r in base if category(r)<3),key=lambda r:(category(r),-r["score"],r["symbol"]))[:TOP]
        enhanced_top=sorted(enhanced,key=lambda r:(-r["enhanced_score"],r["symbol"]))[:TOP]
        for model,chosen in (("baseline",baseline_top),("enhanced",enhanced_top)):
            counts[model]+=1
            for rank,r in enumerate(chosen,1):
                symbol=r["symbol"]
                idx=by_symbol[symbol][day]
                series=stocks[symbol]
                for horizon in HORIZONS:
                    if idx+horizon>=len(series):continue
                    exit_date=series[idx+horizon]["date"]
                    # Only evaluate if enough trading sessions exist within the dataset.
                    pct=(series[idx+horizon]["close"]/series[idx]["close"]-1)*100
                    observations.append({"model":model,"signal_date":day.isoformat(),
                        "symbol":symbol,"rank":rank,"horizon_sessions":horizon,
                        "exit_date":exit_date.isoformat(),"return_pct":round(pct,5),
                        "positive":pct>0})
    results={}
    for horizon in HORIZONS:
        results[str(horizon)]={}
        for model in ("baseline","enhanced"):
            subset=[r for r in observations if r["model"]==model and r["horizon_sessions"]==horizon]
            by_date=defaultdict(list)
            for r in subset:by_date[r["signal_date"]].append(r["return_pct"])
            daily=[statistics.mean(v) for v in by_date.values()]
            results[str(horizon)][model]={
                "observations":len(subset),"signal_dates":len(by_date),
                "average_forward_return_pct":avg([r["return_pct"] for r in subset]),
                "median_forward_return_pct":round(statistics.median([r["return_pct"] for r in subset]),4) if subset else None,
                "positive_observation_rate_pct":round(100*sum(r["positive"] for r in subset)/len(subset),2) if subset else None,
                "average_equal_weight_daily_cohort_return_pct":avg(daily)}
    report={"version":"1.0-preliminary","generated_utc":datetime.now(timezone.utc).isoformat(),
        "data_sessions":len(days),"first_session":days[0].isoformat(),"last_session":days[-1].isoformat(),
        "models":list(counts),"max_stocks_per_cohort":TOP,
        "horizons_trading_sessions":list(HORIZONS),"results":results,
        "limitations":[
            "Not a trade execution backtest: close-to-close forward returns only; no entries, stops, costs, slippage or taxes.",
            "Current Nifty 100 constituent universe creates survivorship and point-in-time membership bias.",
            "Historical OHLCV is not independently corporate-action adjusted.",
            "Only ~250 sessions; EMA200 warm-up leaves a short evaluation period.",
            "Overlapping 5/10-session cohorts are statistically dependent; observations are not independent.",
            "Enhanced scoring thresholds were designed with knowledge of this period; results are in-sample.",
            "No index-relative returns, sector regime or out-of-sample validation.",
            "No actual portfolio equity curve or maximum drawdown can be inferred.",
            "Different numbers of selected stocks and dates can affect model comparisons."
        ],"paper_only":True}
    OUT.mkdir(exist_ok=True)
    (OUT/"backtest_preliminary.json").write_text(json.dumps(report,indent=2))
    with (OUT/"backtest_observations.csv").open("w",newline="") as f:
        fields=["model","signal_date","symbol","rank","horizon_sessions","exit_date","return_pct","positive"]
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(observations)
    print(json.dumps(report,indent=2))
if __name__=="__main__":main()

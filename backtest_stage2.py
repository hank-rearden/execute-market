#!/usr/bin/env python3
"""Stage 2: matched-date, matched-count ranking comparison against equal-weight universe proxy."""
import csv,json,statistics
from collections import defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
from screener import analyze
from enhanced_stage2 import rank_stock

HORIZONS=(5,10)
MIN_HISTORY=201
MAX_SELECTIONS=15
OUT=Path("reports")
def mean(xs): return statistics.mean(xs) if xs else None
def rnd(x): return round(x,4) if x is not None else None
def category(r):
    if r["status"]=="WATCHLIST ONLY - UNCONFIRMED": return 0
    if r["status"] in ("STALE DATA","INSUFFICIENT HISTORY"): return 3
    if r["setup"]!="NO SETUP": return 1
    if (r["sessions"]>=MIN_HISTORY and r["close"]>r["ema20"]>r["ema50"]>r["ema200"] and 45<=r["rsi14"]<=75):return 2
    return 3
def main():
    stocks=defaultdict(list)
    with Path("data/nifty100_history.csv").open(newline="",encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            stocks[r["symbol"]].append({"date":date.fromisoformat(r["date"]),"open":float(r["open"]),"high":float(r["high"]),"low":float(r["low"]),"close":float(r["close"]),"volume":int(r["volume"])})
    for rows in stocks.values():rows.sort(key=lambda r:r["date"])
    days=sorted({r["date"] for rows in stocks.values() for r in rows})
    by_symbol={s:{r["date"]:i for i,r in enumerate(rows)} for s,rows in stocks.items()}
    observations=[]
    cohorts=[]
    for day in days[MIN_HISTORY-1:]:
        base=[];enhanced=[]
        for symbol,rows in stocks.items():
            idx=by_symbol[symbol].get(day)
            if idx is None or idx+1<MIN_HISTORY:continue
            history=rows[:idx+1]
            b=analyze(symbol,history,day)
            if category(b)<3:base.append(b)
            e=rank_stock(symbol,[{**r,"date":r["date"].isoformat()} for r in history],b,asof_date=day)
            if e["enhanced_score"] is not None:enhanced.append(e)
        baseline_ranked=sorted(base,key=lambda r:(category(r),-r["score"],r["symbol"]))
        enhanced_ranked=sorted(enhanced,key=lambda r:(-r["enhanced_score"],r["symbol"]))
        for horizon in HORIZONS:
            # Match both models and proxy to exactly the same calendar exit date.
            exit_day_idx=days.index(day)+horizon
            if exit_day_idx>=len(days):continue
            exit_day=days[exit_day_idx]
            def forward(symbol):
                index=by_symbol[symbol].get(day)
                end=by_symbol[symbol].get(exit_day)
                if index is None or end is None or end<=index:return None
                return (stocks[symbol][end]["close"]/stocks[symbol][index]["close"]-1)*100
            eligible_base=[r for r in baseline_ranked if forward(r["symbol"]) is not None]
            eligible_enhanced=[r for r in enhanced_ranked if forward(r["symbol"]) is not None]
            n=min(MAX_SELECTIONS,len(eligible_base),len(eligible_enhanced))
            if n==0:continue
            proxy_returns=[v for symbol in stocks if (v:=forward(symbol)) is not None and by_symbol[symbol].get(day) is not None and by_symbol[symbol][day]+1>=MIN_HISTORY]
            if len(proxy_returns)<50:continue
            benchmark=mean(proxy_returns)
            cohort={"signal_date":day.isoformat(),"exit_date":exit_day.isoformat(),"horizon_sessions":horizon,
                    "matched_count":n,"proxy_universe_size":len(proxy_returns),
                    "proxy_return_pct":rnd(benchmark),"proxy_type":"equal_weight_current_constituents"}
            for model,ranked in (("baseline",eligible_base),("enhanced",eligible_enhanced)):
                picks=ranked[:n]
                values=[forward(r["symbol"]) for r in picks]
                cohort[model+"_return_pct"]=rnd(mean(values))
                cohort[model+"_excess_pct_points"]=rnd(mean(values)-benchmark)
                for rank,(r,value) in enumerate(zip(picks,values),1):
                    observations.append({"signal_date":day.isoformat(),"exit_date":exit_day.isoformat(),
                        "horizon_sessions":horizon,"model":model,"symbol":r["symbol"],"rank":rank,
                        "matched_count":n,"forward_return_pct":rnd(value),"proxy_return_pct":rnd(benchmark),
                        "excess_vs_proxy_pct_points":rnd(value-benchmark),"positive":value>0})
            cohort["enhanced_minus_baseline_pct_points"]=rnd(cohort["enhanced_return_pct"]-cohort["baseline_return_pct"])
            cohorts.append(cohort)
    if not cohorts:raise SystemExit("No matched cohorts")
    summary={}
    for h in HORIZONS:
        subset=[c for c in cohorts if c["horizon_sessions"]==h]
        if not subset:raise SystemExit("Missing horizon "+str(h))
        diff=[c["enhanced_minus_baseline_pct_points"] for c in subset]
        summary[str(h)]={
            "matched_dates":len(subset),"matched_stock_observations_per_model":sum(c["matched_count"] for c in subset),
            "average_stocks_per_date":rnd(mean([c["matched_count"] for c in subset])),
            "min_stocks_per_date":min(c["matched_count"] for c in subset),
            "max_stocks_per_date":max(c["matched_count"] for c in subset),
            "baseline_average_cohort_return_pct":rnd(mean([c["baseline_return_pct"] for c in subset])),
            "enhanced_average_cohort_return_pct":rnd(mean([c["enhanced_return_pct"] for c in subset])),
            "proxy_average_cohort_return_pct":rnd(mean([c["proxy_return_pct"] for c in subset])),
            "enhanced_minus_baseline_pct_points":rnd(mean(diff)),
            "enhanced_outperformed_baseline_dates":sum(d>0 for d in diff),
            "enhanced_outperformance_date_rate_pct":rnd(100*sum(d>0 for d in diff)/len(diff)),
            "baseline_average_excess_vs_proxy_pct_points":rnd(mean([c["baseline_excess_pct_points"] for c in subset])),
            "enhanced_average_excess_vs_proxy_pct_points":rnd(mean([c["enhanced_excess_pct_points"] for c in subset]))
        }
    report={"version":"2.0-matched","generated_utc":datetime.now(timezone.utc).isoformat(),
        "data_first_session":days[0].isoformat(),"data_last_session":days[-1].isoformat(),
        "horizons_trading_sessions":list(HORIZONS),"maximum_picks_per_date":MAX_SELECTIONS,
        "benchmark":"Equal-weight return of available eligible stocks from current Nifty 100 universe, NOT the official Nifty 100 index",
        "comparison":"Same dates, same number of stocks for each model and same calendar exit dates",
        "summary":summary,"paper_only":True,
        "limitations":[
            "Benchmark is a current-constituent equal-weight proxy, not the official float-weighted Nifty 100 index.",
            "Current constituent membership causes survivorship and historical universe bias.",
            "OHLCV not independently corporate-action adjusted; splits/dividends may distort returns.",
            "Close-to-close returns are not executable trade returns; no stop/target/cost/slippage/tax modeling.",
            "Overlapping cohorts and short 250-session dataset reduce statistical reliability.",
            "Selection count is matched but the enhanced model can include no-setup research stocks; strategy eligibility differs.",
            "In-sample comparison only; no out-of-sample validation or risk-adjusted portfolio equity curve."
        ]}
    OUT.mkdir(exist_ok=True)
    (OUT/"backtest_stage2.json").write_text(json.dumps(report,indent=2))
    for filename,rows in (("backtest_stage2_cohorts.csv",cohorts),("backtest_stage2_observations.csv",observations)):
        with (OUT/filename).open("w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]))
            writer.writeheader();writer.writerows(rows)
    print(json.dumps(report,indent=2))
if __name__=="__main__":main()

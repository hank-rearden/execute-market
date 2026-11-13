#!/usr/bin/env python3
"""Conservative Stage 3 paper-trade execution backtest. No live orders."""
import csv,json,statistics
from collections import defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
from screener import analyze,charges,SLIPPAGE_RATE,CAPITAL,MAX_RISK,MAX_POSITION
from enhanced_stage2 import rank_stock
OUT=Path("reports")
HOLD=10
MAX_OPEN=3

def main():
    stocks=defaultdict(list)
    with Path("data/nifty100_history.csv").open(newline="",encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            stocks[r["symbol"]].append({"date":r["date"],"open":float(r["open"]),"high":float(r["high"]),
                                        "low":float(r["low"]),"close":float(r["close"]),"volume":int(r["volume"])})
    for rows in stocks.values():rows.sort(key=lambda r:r["date"])
    days=sorted({r["date"] for rows in stocks.values() for r in rows})
    index={s:{r["date"]:i for i,r in enumerate(rows)} for s,rows in stocks.items()}
    signals={}
    for day in days[:-1]:
        picks=[]
        for symbol,rows in stocks.items():
            i=index[symbol].get(day)
            if i is None or i<200:continue
            history=rows[:i+1]
            b=analyze(symbol,[{**r,"date":date.fromisoformat(r["date"])} for r in history],date.fromisoformat(day))
            if b["status"]!="WATCHLIST ONLY - UNCONFIRMED":continue
            e=rank_stock(symbol,history,b,asof_date=date.fromisoformat(day))
            if e["enhanced_score"] is None:continue
            picks.append({"symbol":symbol,"signal_date":day,"entry":b["entry"],"stop":b["stop"],
                          "target":b["target"],"baseline_score":b["score"],"enhanced_score":e["enhanced_score"]})
        signals[day]=picks
    all_trades=[];all_equity=[];summaries={}
    for model in ("baseline","enhanced"):
        cash=float(CAPITAL);held={};trades=[];curve=[];rejects=defaultdict(int);previous=None
        def finish(p,day,raw,reason):
            nonlocal cash
            fill=raw*(1-SLIPPAGE_RATE)
            gross=(fill-p["entry_fill"])*p["quantity"]
            fees=charges(p["entry_fill"],fill,p["quantity"])
            pnl=gross-fees
            cash+=fill*p["quantity"]-fees
            trades.append({**p,"exit_date":day,"exit_reason":reason,"exit_fill":round(fill,4),
                           "gross_pnl":round(gross,2),"estimated_fees":round(fees,2),
                           "net_pnl":round(pnl,2)})
        for day in days:
            bars={s:rows[index[s][day]] for s,rows in stocks.items() if day in index[s]}
            for symbol,p in list(held.items()):
                bar=bars.get(symbol)
                if bar is None:continue
                p["holding_sessions"]+=1
                reason=None;price=None
                if bar["open"]<=p["stop"]:reason,price="STOP_GAP",bar["open"]
                elif bar["open"]>=p["target"]:reason,price="TARGET_GAP",p["target"]
                elif bar["low"]<=p["stop"]:reason,price="STOP",p["stop"]
                elif bar["high"]>=p["target"]:reason,price="TARGET",p["target"]
                elif p["holding_sessions"]>=HOLD:reason,price="TIME",bar["close"]
                if reason:
                    finish(p,day,price,reason);del held[symbol]
            if previous:
                candidates=signals.get(previous,[])
                if model=="baseline":
                    candidates=sorted(candidates,key=lambda c:(-c["baseline_score"],c["symbol"]))
                else:
                    candidates=sorted(candidates,key=lambda c:(-c["enhanced_score"],-c["baseline_score"],c["symbol"]))
                for c in candidates:
                    if len(held)>=MAX_OPEN:
                        rejects["slots_full"]+=1
                        break
                    s=c["symbol"]
                    if s in held:
                        rejects["already_held"]+=1;continue
                    bar=bars.get(s)
                    if bar is None or bar["high"]<c["entry"]:
                        rejects["not_triggered"]+=1;continue
                    raw=max(c["entry"],bar["open"])
                    if raw>=c["target"]:
                        rejects["gap_past_target"]+=1;continue
                    buy=raw*(1+SLIPPAGE_RATE)
                    stopfill=c["stop"]*(1-SLIPPAGE_RATE)
                    risk_per=buy-stopfill
                    if risk_per<=0:continue
                    limit=min(int(MAX_POSITION/buy),int(cash/buy),int(MAX_RISK/risk_per))
                    qty=0
                    for n in range(limit,0,-1):
                        risk=n*risk_per+charges(buy,stopfill,n)
                        if risk<=MAX_RISK and n*buy+charges(buy,stopfill,n)<=cash:
                            qty=n;break
                    if not qty:
                        rejects["cash_or_risk"]+=1;continue
                    p={"model":model,"symbol":s,"signal_date":c["signal_date"],
                       "entry_date":day,"entry_trigger":c["entry"],"entry_fill":round(buy,4),
                       "stop":c["stop"],"target":c["target"],"quantity":qty,
                       "baseline_score":c["baseline_score"],"enhanced_score":c["enhanced_score"],
                       "holding_sessions":0}
                    cash-=buy*qty
                    # Intraday ordering unknown: assume adverse stop if both entry and stop touch.
                    if bar["low"]<=c["stop"]:
                        p["holding_sessions"]=1
                        finish(p,day,min(c["stop"],bar["open"]),"ENTRY_DAY_STOP_ASSUMED")
                    else:
                        # Do not credit a target touched on entry bar: ordering unknown.
                        held[s]=p
            marked=cash
            for s,p in held.items():
                prior=next((r["close"] for r in reversed(stocks[s]) if r["date"]<=day),p["entry_fill"])
                marked+=prior*p["quantity"]
            curve.append({"model":model,"date":day,"equity":round(marked,2),
                          "cash":round(cash,2),"open_positions":len(held)})
            previous=day
        peak=CAPITAL;dd=0
        for row in curve:
            peak=max(peak,row["equity"])
            dd=max(dd,100*(peak-row["equity"])/peak)
        pnls=[t["net_pnl"] for t in trades]
        profits=sum(p for p in pnls if p>0);losses=sum(p for p in pnls if p<=0)
        summaries[model]={"starting_capital":CAPITAL,"ending_marked_equity":curve[-1]["equity"],
            "marked_return_pct":round(100*(curve[-1]["equity"]/CAPITAL-1),3),
            "realized_net_pnl":round(sum(pnls),2),"closed_trades":len(trades),
            "win_rate_pct":round(100*sum(p>0 for p in pnls)/len(pnls),2) if pnls else None,
            "average_net_pnl_per_trade":round(statistics.mean(pnls),2) if pnls else None,
            "gross_profit":round(profits,2),"gross_loss":round(losses,2),
            "profit_factor":round(profits/abs(losses),3) if losses<0 else None,
            "max_marked_drawdown_pct":round(dd,3),"open_positions_at_end":len(held),
            "rejections":dict(rejects)}
        all_trades.extend(trades);all_equity.extend(curve)
    OUT.mkdir(exist_ok=True)
    report={"version":"3.0-paper","generated_utc":datetime.now(timezone.utc).isoformat(),
            "first_session":days[0],"last_session":days[-1],"sessions":len(days),
            "maximum_holding_sessions":HOLD,"max_open_positions":MAX_OPEN,
            "same_bar_policy":"Adverse stop assumed when entry and stop both touched; no entry-bar target credit",
            "paper_only":True,"strategies":summaries,
            "limitations":["Daily OHLC cannot establish intraday order; stop-first policy is conservative, not exact.",
                "Only about 50 sessions remain after EMA200 warm-up; sample may contain few qualifying trades.",
                "Current constituents introduce survivorship bias; prices are not verified corporate-action adjusted.",
                "Slippage and fees are estimates; capital gains taxes excluded.",
                "Stop gaps can exceed the intended ₹500 risk.",
                "Open positions marked at last close without liquidation fees.",
                "Both strategies use identical qualifying signals; enhanced score changes order only.",
                "In-sample paper simulation, not proof of future profitability."]}
    (OUT/"backtest_stage3.json").write_text(json.dumps(report,indent=2))
    for filename,rows in (("backtest_stage3_trades.csv",all_trades),("backtest_stage3_equity.csv",all_equity)):
        with (OUT/filename).open("w",newline="") as f:
            if rows:
                writer=csv.DictWriter(f,fieldnames=list(rows[0]))
                writer.writeheader();writer.writerows(rows)
            else:f.write("model\n")
    print(json.dumps(report,indent=2))
if __name__=="__main__":main()

#!/usr/bin/env python3
"""EXECUTE Stage 5: prospective EOD paper fills, never broker orders.

Only bars dated AFTER a recorded signal are eligible. Runs idempotently.
Daily OHLC intrabar sequence is unknowable; adverse stop wins ambiguity.
"""
import csv,json
from pathlib import Path
from datetime import datetime,timezone
from screener import charges,SLIPPAGE_RATE,CAPITAL,MAX_RISK,MAX_POSITION
ROOT=Path("data");REPORT=Path("reports")
STATE=ROOT/"paper_portfolio_state.json";TRADES=ROOT/"paper_trades.csv";EQUITY=ROOT/"paper_equity.csv"
TF=["symbol","signal_date","entry_date","exit_date","entry_fill","exit_fill","quantity","stop","target","exit_reason","net_pnl","fees_estimate"]
EF=["date","cash","marked_equity","open_positions","realized_net_pnl","drawdown_pct"]
def load_csv(path):
    if not path.exists():return []
    with path.open(newline="",encoding="utf-8") as f:return list(csv.DictReader(f))
def write_csv(path,fields,rows):
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def main():
    hist={}
    for r in load_csv(ROOT/"nifty100_history.csv"):
        hist.setdefault(r["date"],{})[r["symbol"]]={k:float(r[k]) for k in ("open","high","low","close")}
    dates=sorted(hist)
    if not dates:raise SystemExit("No prices")
    signals=load_csv(ROOT/"paper_signal_journal.csv")
    if not STATE.exists():
        # Anchor at the most recent signal date, never replay old price bars.
        anchor=max((r["signal_date"] for r in signals),default=dates[-1])
        state={"version":1,"paper_only":True,"cash":float(CAPITAL),"open":{},"last_processed_date":anchor,"starting_date":anchor,"peak_equity":float(CAPITAL),"realized_net_pnl":0.0,"processed_signal_keys":[]}
    else:state=json.loads(STATE.read_text())
    if not state.get("paper_only") or state.get("version")!=1:raise SystemExit("Invalid paper state")
    trades=load_csv(TRADES);curve=load_csv(EQUITY)
    processed=set(state["processed_signal_keys"])
    def close_position(s,p,d,raw,reason):
        sell=raw*(1-SLIPPAGE_RATE)
        fee=charges(p["entry_fill"],sell,p["quantity"])
        pnl=(sell-p["entry_fill"])*p["quantity"]-fee
        state["cash"]+=sell*p["quantity"]-fee
        state["realized_net_pnl"]+=pnl
        trades.append(dict(symbol=s,signal_date=p["signal_date"],entry_date=p["entry_date"],exit_date=d,entry_fill=round(p["entry_fill"],4),exit_fill=round(sell,4),quantity=p["quantity"],stop=p["stop"],target=p["target"],exit_reason=reason,net_pnl=round(pnl,2),fees_estimate=round(fee,2)))
        del state["open"][s]
    for d in dates:
        if d<=state["last_processed_date"]:continue
        bars=hist[d]
        # Resolve existing positions before admitting new entries.
        for s,p in list(state["open"].items()):
            b=bars.get(s)
            if b is None:continue
            p["holding_sessions"]+=1
            if b["open"]<=p["stop"]:close_position(s,p,d,b["open"],"STOP_GAP")
            elif b["open"]>=p["target"]:close_position(s,p,d,p["target"],"TARGET_GAP_CAPPED")
            elif b["low"]<=p["stop"]:close_position(s,p,d,p["stop"],"STOP_FIRST")
            elif b["high"]>=p["target"]:close_position(s,p,d,p["target"],"TARGET")
            elif p["holding_sessions"]>=10:close_position(s,p,d,b["close"],"TIME_10_SESSIONS")
        candidates=sorted((r for r in signals if r["signal_date"]<d and (r["signal_date"],r["symbol"]) not in processed),key=lambda r:(r["signal_date"],-float(r["enhanced_score"] or 0),r["symbol"]))
        for r in candidates:
            key=(r["signal_date"],r["symbol"])
            # One next available trading session only; never keep an unfilled trigger alive.
            first=next((day for day in dates if day>r["signal_date"]),None)
            if first!=d:
                if first is not None and first<d:processed.add(key)
                continue
            processed.add(key)
            s=r["symbol"];b=bars.get(s)
            if s in state["open"] or len(state["open"])>=3 or b is None:continue
            trigger=float(r["entry_trigger"]);stop=float(r["stop"]);target=float(r["target"])
            if b["high"]<trigger:continue
            raw=max(trigger,b["open"])
            if raw>=target:continue
            buy=raw*(1+SLIPPAGE_RATE)
            if buy<=stop:continue
            stopfill=stop*(1-SLIPPAGE_RATE)
            limit=min(int(MAX_POSITION/buy),int(state["cash"]/buy),int(MAX_RISK/(buy-stopfill)))
            qty=0
            for n in range(limit,0,-1):
                if (buy-stopfill)*n+charges(buy,stopfill,n)<=MAX_RISK and buy*n+charges(buy,stopfill,n)<=state["cash"]:
                    qty=n;break
            if qty==0:continue
            state["cash"]-=buy*qty
            state["open"][s]=dict(signal_date=r["signal_date"],entry_date=d,entry_fill=buy,quantity=qty,stop=stop,target=target,holding_sessions=0)
            if b["low"]<=stop:
                close_position(s,state["open"][s],d,min(stop,b["open"]),"ENTRY_DAY_STOP_ASSUMED")
            # Never credit an entry-day target: ordering unknown.
        marked=state["cash"]+sum((bars[s]["close"] if s in bars else p["entry_fill"])*p["quantity"] for s,p in state["open"].items())
        state["peak_equity"]=max(state["peak_equity"],marked)
        drawdown=100*(state["peak_equity"]-marked)/state["peak_equity"]
        curve.append(dict(date=d,cash=round(state["cash"],2),marked_equity=round(marked,2),open_positions=len(state["open"]),realized_net_pnl=round(state["realized_net_pnl"],2),drawdown_pct=round(drawdown,3)))
        state["last_processed_date"]=d
    state["processed_signal_keys"]=[list(k) for k in sorted(processed)]
    ROOT.mkdir(exist_ok=True);REPORT.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state,indent=2)+"\n")
    write_csv(TRADES,TF,trades);write_csv(EQUITY,EF,curve)
    wins=sum(float(t["net_pnl"])>0 for t in trades)
    pnl=[float(t["net_pnl"]) for t in trades]
    profit=sum(x for x in pnl if x>0);loss=-sum(x for x in pnl if x<0)
    summary=dict(version="5.0-forward-paper",generated_utc=datetime.now(timezone.utc).isoformat(),paper_only=True,live_quotes=False,orders_placed=False,starting_capital=CAPITAL,last_processed_date=state["last_processed_date"],closed_trades=len(trades),open_positions=len(state["open"]),realized_net_pnl=round(state["realized_net_pnl"],2),win_rate_pct=round(100*wins/len(trades),2) if trades else None,average_net_pnl=round(sum(pnl)/len(pnl),2) if trades else None,profit_factor=round(profit/loss,3) if loss else None,marked_equity=curve[-1]["marked_equity"] if curve else round(state["cash"]+sum(p["entry_fill"]*p["quantity"] for p in state["open"].values()),2),equity_observations=len(curve),limitations=["Only next-session OHLC-based simulated fills, not actual executions","Intraday ordering unknowable; entry-day stops assumed adverse; entry-day targets not credited","Unadjusted NSE historical prices; corporate actions may distort results","Fees and slippage estimated; taxes excluded","No live data; stops can gap past planned loss","New forward account starts at journal anchor; prior backtest not mixed with forward results"])
    (REPORT/"paper_forward_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(json.dumps(summary,indent=2))
if __name__=="__main__":main()

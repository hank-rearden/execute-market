#!/usr/bin/env python3
"""EXECUTE vNext A-E research experiment. PAPER ONLY; no order APIs.
Point-in-time indicators, predetermined data exclusions, chronological model
selection, and future-only prospective journal. Not a validated investment model.
"""
import csv,json,math,statistics
from collections import defaultdict,Counter
from datetime import datetime,timezone
from pathlib import Path
from screener import analyze,charges,SLIPPAGE_RATE
from enhanced_stage2 import rank_stock

DATA=Path("data/stage4_udiff_history.csv")
AUDIT=Path("reports/stage4_corporate_action_audit.json")
OUT=Path("reports/vnext_research.json")
TRADES=Path("reports/vnext_test_trades.csv")
JOURNAL=Path("data/vnext_forward_journal.csv")
FIELDS=["signal_date","symbol","strategy","entry_date","exit_date","entry_fill","exit_fill","net_return_pct","net_pnl_10000","reason"]
HORIZON=10
WARMUP=201

def load():
    data=defaultdict(list); seen=set(); anomalies=defaultdict(list)
    with DATA.open(newline="",encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            s=r["symbol"];d=r["date"];k=(s,d)
            if k in seen:anomalies[s].append("duplicate date");continue
            seen.add(k)
            try:
                row={"date":d,"open":float(r["open"]),"high":float(r["high"]),"low":float(r["low"]),"close":float(r["close"]),"volume":int(float(r["volume"]))}
                if not(0<row["low"]<=min(row["open"],row["close"])<=max(row["open"],row["close"])<=row["high"] and row["volume"]>=0):raise ValueError()
                data[s].append(row)
            except (ValueError,OverflowError):anomalies[s].append("invalid OHLCV")
    for s,rows in data.items():
        rows.sort(key=lambda x:x["date"])
        for a,b in zip(rows,rows[1:]):
            if abs(b["close"]/a["close"]-1)>0.20:anomalies[s].append("price jump >20%")
            if (datetime.fromisoformat(b["date"])-datetime.fromisoformat(a["date"])).days>10:anomalies[s].append("missing >10 calendar days")
    audit=json.loads(AUDIT.read_text())
    excluded={x["symbol"] for x in audit["flags"]}|set(anomalies)
    clean={s:r for s,r in data.items() if s not in excluded and len(r)>=WARMUP+HORIZON}
    return clean,excluded,anomalies,len(data)

def generate(data):
    days=sorted({r["date"] for rows in data.values() for r in rows})
    # Use one evaluation day every ten sessions: fewer overlapping outcome windows.
    candidates=[]
    for day_idx in range(WARMUP-1,len(days)-HORIZON,HORIZON):
        day=days[day_idx]; market=[]
        for s,rows in data.items():
            past=[r for r in rows if r["date"]<=day]
            if len(past)<WARMUP or past[-1]["date"]!=day:continue
            idx=len(past)-1
            if idx+HORIZON>=len(rows):continue
            # Point-in-time: no future OHLC enters signal computation.
            b=analyze(s,[{**r,"date":datetime.fromisoformat(r["date"]).date()} for r in past],datetime.fromisoformat(day).date())
            e=rank_stock(s,past.copy(),b,asof_date=datetime.fromisoformat(day).date())
            if e["enhanced_score"] is None:continue
            # No setup, no entry: hard Stage A gate.
            if b["status"]!="WATCHLIST ONLY - UNCONFIRMED" or b.get("entry") is None or b.get("stop") is None:continue
            relvol=e.get("relative_volume")
            if relvol is None:continue
            market.append((s,rows,idx,b,e))
        for s,rows,idx,b,e in market:
            # Execute only if next session touches entry; conservative stop-first.
            entry=b["entry"];stop=b["stop"];target=b["target"]
            nxt=rows[idx+1]
            if nxt["high"]<entry:continue
            raw=max(nxt["open"],entry)
            buy=raw*(1+SLIPPAGE_RATE)
            if nxt["low"]<=stop:
                exit_raw=min(stop,nxt["open"]);exit_idx=idx+1;reason="entry_day_stop"
            else:
                exit_raw=None;exit_idx=None;reason=None
                for j in range(idx+2,idx+HORIZON+1):
                    bar=rows[j]
                    if bar["open"]<=stop:exit_raw=bar["open"];reason="stop_gap"
                    elif bar["low"]<=stop:exit_raw=stop;reason="stop"
                    elif bar["open"]>=target:exit_raw=target;reason="target_gap"
                    elif bar["high"]>=target:exit_raw=target;reason="target"
                    if exit_raw is not None:exit_idx=j;break
                if exit_raw is None:exit_idx=idx+HORIZON;exit_raw=rows[exit_idx]["close"];reason="time"
            sell=exit_raw*(1-SLIPPAGE_RATE)
            # Normalize to hypothetical 10k notional, whole shares; no compounding.
            qty=max(1,int(10000/buy))
            pnl=(sell-buy)*qty-charges(buy,sell,qty)
            candidates.append({"signal_date":day,"symbol":s,"baseline_score":b["score"],"enhanced_score":e["enhanced_score"],"relative_volume":relvol,
              "rsi":b["rsi14"],"entry_date":nxt["date"],"exit_date":rows[exit_idx]["date"],"entry_fill":round(buy,4),"exit_fill":round(sell,4),
              "net_return_pct":round(pnl/(buy*qty)*100,4),"net_pnl_10000":round(pnl,2),"reason":reason})
    return candidates,days

def choose(candidates,days):
    # Chronological 65% development / 35% untouched test.
    split=days[int(len(days)*.65)]
    training=[c for c in candidates if c["signal_date"]<split]
    testing=[c for c in candidates if c["signal_date"]>=split]
    configs=[
      ("baseline",lambda c:True,lambda c:c["baseline_score"]),
      ("enhanced",lambda c:True,lambda c:c["enhanced_score"]),
      ("volume_1_2",lambda c:c["relative_volume"]>=1.2,lambda c:c["enhanced_score"]),
      ("volume_1_5",lambda c:c["relative_volume"]>=1.5,lambda c:c["enhanced_score"]),
      ("volume_1_2_score70",lambda c:c["relative_volume"]>=1.2 and c["enhanced_score"]>=70,lambda c:c["enhanced_score"]),
    ]
    def select(pool,pred,score):
        grouped=defaultdict(list)
        for c in pool:
            if pred(c):grouped[c["signal_date"]].append(c)
        return [sorted(group,key=lambda x:(-score(x),x["symbol"]))[0] for group in grouped.values()]
    def metrics(rows):
        p=[r["net_return_pct"] for r in rows]
        wins=sum(x>0 for x in p)
        gp=sum(x for x in p if x>0);gl=-sum(x for x in p if x<0)
        equity=1.;peak=1.;dd=0.
        for x in sorted(rows,key=lambda z:z["signal_date"]):
            equity*=1+x["net_return_pct"]/100
            peak=max(peak,equity);dd=max(dd,(peak-equity)/peak*100)
        return {"trades":len(p),"win_rate_pct":round(wins/len(p)*100,2) if p else None,
            "mean_net_return_pct":round(statistics.mean(p),3) if p else None,
            "median_net_return_pct":round(statistics.median(p),3) if p else None,
            "profit_factor":round(gp/gl,3) if gl else None,
            "illustrative_sequential_return_pct":round((equity-1)*100,3) if p else None,
            "illustrative_max_drawdown_pct":round(dd,3) if p else None}
    train={}
    for name,pred,score in configs:
        chosen=select(training,pred,score)
        train[name]=metrics(chosen)
    # Predeclared minimum sample threshold. No winner if data insufficient.
    eligible=[name for name,v in train.items() if v["trades"]>=20 and v["mean_net_return_pct"] is not None]
    selected=max(eligible,key=lambda n:(train[n]["mean_net_return_pct"],train[n]["trades"])) if eligible else None
    test={}
    for name,pred,score in configs:
        test[name]=metrics(select(testing,pred,score))
    selected_trades=[]
    if selected:
        _,pred,score=next(x for x in configs if x[0]==selected)
        selected_trades=select(testing,pred,score)
    return split,train,test,selected,selected_trades

def main():
    data,excluded,anomalies,total=load()
    candidates,days=generate(data)
    split,train,test,selected,trades=choose(candidates,days)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    with TRADES.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader()
        for t in trades:w.writerow({k:(selected if k=="strategy" else t.get(k,"")) for k in FIELDS})
    # Stage E: initialize a prospective journal, never backfill future outcomes.
    JOURNAL.parent.mkdir(parents=True,exist_ok=True)
    if not JOURNAL.exists():
        with JOURNAL.open("w",newline="") as f:
            w=csv.writer(f);w.writerow(["observed_utc","signal_date","symbol","model","status","entry","stop","target","outcome"])
    v=test.get(selected,{}) if selected else {}
    passed=bool(selected and v.get("trades",0)>=20 and (v.get("mean_net_return_pct") or 0)>0 and (v.get("profit_factor") or 0)>1.2)
    report={"version":"vNext-A-E-experimental","generated_utc":datetime.now(timezone.utc).isoformat(),
      "stage_a":"Research ranking never authorizes trades; valid baseline entry/stop/target mandatory",
      "stage_b_lite":{"raw_symbols":total,"excluded_symbols":sorted(excluded),"excluded_count":len(excluded),"clean_symbols":len(data),
                      "anomaly_symbols":dict(anomalies),"historical_membership_verified":False,"corporate_actions_adjusted":False},
      "stage_c":"Predeclared variants: baseline, enhanced, enhanced+volume1.2, enhanced+volume1.5, enhanced+volume1.2+score70",
      "stage_d":{"train_test_cutoff":split,"train":train,"untouched_test":test,"train_selected_model":selected,
                 "selected_test":v,"candidate_trades_before_selection":len(candidates),"independent_trade_windows":False,
                 "note":"One selected trade per 10-session signal date; exit can overlap next entry. Returns are not a portfolio NAV."},
      "stage_e":{"forward_journal":str(JOURNAL),"future_observations":0,"ready_for_real_money":False,
                 "research_gate_passed":passed,"forward_validation_complete":False},
      "limitations":["Present-day Nifty100 constituents cause survivorship bias","Corporate actions not independently verified; excluded symbols are heuristic",
        "Excluding full symbols can bias the sample","No official index benchmark or sector membership","Daily OHLC cannot resolve intraday order",
        "Model selection on short development period risks overfitting","Research gate is necessary, not sufficient for live trading",
        "10-session sampling reduces but does not eliminate dependent outcomes","Fees estimated; taxes excluded","No real orders; no live quotes"]}
    OUT.write_text(json.dumps(report,indent=2))
    print("VNEXT RESEARCH",json.dumps({"clean_symbols":len(data),"excluded":len(excluded),"selected":selected,"test":v,"research_gate_passed":passed}))
if __name__=="__main__":main()

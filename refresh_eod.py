#!/usr/bin/env python3
"""Incremental Nifty 100 EOD refresh; fail closed on missing recent data."""
import csv, io, json, math, time, urllib.request, zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE="https://nsearchives.nseindia.com"
FIELDS=["date","symbol","open","high","low","close","volume"]
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"*/*","Referer":"https://www.nseindia.com/"}
def fetch(url, attempts=3):
    err=None
    for attempt in range(attempts):
        try:
            req=urllib.request.Request(url,headers=HEADERS)
            with urllib.request.urlopen(req,timeout=25) as response:
                return response.read()
        except Exception as exc:
            err=exc
            time.sleep(2*(attempt+1))
    raise RuntimeError(str(err))

def parse(raw):
    return csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))

def validate(row):
    op,hi,lo,cl=[float(row[k]) for k in ("open","high","low","close")]
    vol=int(float(row["volume"]))
    if not all(math.isfinite(v) for v in (op,hi,lo,cl)) or not (0<lo<=op<=hi and lo<=cl<=hi and vol>=0):
        raise ValueError("Invalid OHLCV")
    return {**{k:row[k] for k in ("date","symbol")},"open":op,"high":hi,"low":lo,"close":cl,"volume":vol}

def main():
    path=Path("data/nifty100_history.csv")
    if not path.exists():
        raise SystemExit("Missing committed history")
    with path.open(newline="",encoding="utf-8-sig") as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=FIELDS:
            raise SystemExit("Unexpected history columns")
        old=[validate(r) for r in reader]
    old_keys={(r["date"],r["symbol"]) for r in old}
    if len(old_keys)!=len(old) or len(set(r["symbol"] for r in old))!=100:
        raise SystemExit("Invalid existing history")
    symbols=None
    universe_source=None
    for url in (BASE+"/content/indices/ind_nifty100list.csv","https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv"):
        try:
            values=[r["Symbol"].strip() for r in parse(fetch(url)) if r.get("Symbol","").strip()]
            if len(values)==100 and len(set(values))==100:
                symbols=set(values)
                universe_source=url
                break
        except Exception as exc:
            print("Universe source unavailable:",exc)
    if symbols is None:
        raise SystemExit("Could not validate Nifty 100 constituents")
    records={(r["date"],r["symbol"]):r for r in old if r["symbol"] in symbols}
    today=datetime.now(timezone.utc).date()
    previous_latest=max(r["date"] for r in old)
    successes=[]
    failures=[]
    # Scan a short recent window; avoid downloading all 250 sessions each day.
    for offset in range(0,12):
        day=today-timedelta(days=offset)
        if day.weekday()>=5:
            continue
        ds=day.isoformat()
        # Re-fetch recent dates to ensure an incomplete previous download is not carried forward.
        url=BASE+"/content/cm/BhavCopy_NSE_CM_0_0_0_"+day.strftime("%Y%m%d")+"_F_0000.csv.zip"
        try:
            raw=fetch(url,attempts=2)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names=[n for n in archive.namelist() if n.lower().endswith(".csv")]
                if len(names)!=1: raise ValueError("Expected one CSV")
                reader=parse(archive.read(names[0]))
                required={"Sgmt","FinInstrmTp","SctySrs","TckrSymb","OpnPric","HghPric","LwPric","ClsPric","TtlTradgVol"}
                if not required.issubset(reader.fieldnames or []): raise ValueError("Missing UDiFF columns")
                daily={}
                for r in reader:
                    symbol=r["TckrSymb"].strip()
                    if symbol not in symbols or r["Sgmt"].strip()!="CM" or r["FinInstrmTp"].strip()!="STK" or r["SctySrs"].strip()!="EQ":
                        continue
                    if symbol in daily: raise ValueError("Duplicate EQ symbol "+symbol)
                    daily[symbol]=validate({"date":ds,"symbol":symbol,"open":r["OpnPric"],"high":r["HghPric"],"low":r["LwPric"],"close":r["ClsPric"],"volume":r["TtlTradgVol"]})
                if len(daily)<80:
                    raise ValueError("Too few constituents in session: "+str(len(daily)))
                for symbol,r in daily.items(): records[(ds,symbol)]=r
                successes.append(ds)
                print("Fetched",ds,len(daily))
        except Exception as exc:
            failures.append({"date":ds,"reason":str(exc)})
            print("No valid bhavcopy for",ds,str(exc)[:130])
    days=sorted({d for d,s in records},reverse=True)[:250]
    if len(days)<250:
        raise SystemExit("History would have fewer than 250 sessions; refusing to overwrite")
    newest=days[0]
    if newest<previous_latest:
        raise SystemExit("Data regression")
    if (today-datetime.fromisoformat(newest).date()).days>5:
        raise SystemExit("No sufficiently recent data; refusing to publish stale report")
    # If the last business day is newer than available, do not publish old data as a fresh update.
    prior=today-timedelta(days=1)
    while prior.weekday()>=5:
        prior-=timedelta(days=1)
    if newest<prior.isoformat():
        raise SystemExit("Latest bhavcopy predates previous weekday; possible NSE holiday or delay. No publish.")
    selected=set(days)
    output=sorted((r for (d,s),r in records.items() if d in selected),key=lambda r:(r["date"],r["symbol"]))
    if len({(r["date"],r["symbol"]) for r in output})!=len(output):
        raise SystemExit("Duplicate records")
    counts=Counter(r["date"] for r in output)
    if any(c<60 or c>100 for c in counts.values()):
        raise SystemExit("Bad session coverage")
    if len({r["symbol"] for r in output})!=100:
        raise SystemExit("Current universe not fully represented in rolling window")
    # Atomic replacement only after validation.
    temp=path.with_suffix(".tmp")
    with temp.open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output)
    temp.replace(path)
    report={"generated_utc":datetime.now(timezone.utc).isoformat(),
        "universe_source":universe_source,"universe_size":100,
        "target_sessions":250,"sessions":250,"records":len(output),
        "latest_date":newest,"earliest_date":days[-1],
        "symbols_below_200_sessions":[s for s in sorted(symbols) if sum(r["symbol"]==s for r in output)<200],
        "recent_fetch_successes":successes,"recent_fetch_failures":failures,
        "corporate_action_adjusted":False,"paper_trading_only":True}
    Path("data/history100_report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print("EOD HISTORY VALIDATED",newest,len(output),"records")
if __name__=="__main__":
    main()

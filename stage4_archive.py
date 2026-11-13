#!/usr/bin/env python3
"""Stage 4 archival extension: resumable, bounded NSE UDiFF-era batches.

Separate from production 250-session file. Never silently adjusts prices or
claims historical constituent membership. Start date is 2024-07-08.
"""
import argparse,csv,io,json,math,time,urllib.request,zipfile
from collections import Counter
from datetime import date,timedelta,datetime,timezone
from pathlib import Path

START=date(2024,7,8)
FIELDS=["date","symbol","open","high","low","close","volume"]
BASE="https://nsearchives.nseindia.com"
HEADERS={"User-Agent":"Mozilla/5.0","Accept":"*/*","Referer":"https://www.nseindia.com/"}
ARCHIVE=Path("data/stage4_udiff_history.csv")
REPORT=Path("reports/stage4_archive_status.json")

def get(url):
    req=urllib.request.Request(url,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=18) as response:return response.read()

def universe():
    for url in (BASE+"/content/indices/ind_nifty100list.csv","https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv"):
        try:
            rows=list(csv.DictReader(io.StringIO(get(url).decode("utf-8-sig"))))
            symbols=[r["Symbol"].strip() for r in rows if r.get("Symbol","").strip()]
            if len(symbols)==100 and len(set(symbols))==100:return set(symbols)
        except Exception as e:print("Universe lookup:",str(e)[:120])
    raise RuntimeError("Cannot verify current Nifty 100 universe")

def check(r):
    values=[float(r[k]) for k in ("open","high","low","close")]
    v=int(float(r["volume"]))
    op,hi,lo,cl=values
    if not all(math.isfinite(x) for x in values) or not (0<lo<=op<=hi and lo<=cl<=hi and v>=0):
        raise ValueError("Invalid OHLCV")
    return {"date":r["date"],"symbol":r["symbol"],"open":op,"high":hi,"low":lo,"close":cl,"volume":v}

def load(path):
    records={}
    if not path.exists():return records
    with path.open(newline="",encoding="utf-8-sig") as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=FIELDS:raise RuntimeError("Invalid existing archive schema")
        for r in reader:
            v=check(r);key=(v["date"],v["symbol"])
            if key in records:raise RuntimeError("Duplicate archive key "+str(key))
            records[key]=v
    return records

def fetch_day(day,symbols):
    url=BASE+"/content/cm/BhavCopy_NSE_CM_0_0_0_"+day.strftime("%Y%m%d")+"_F_0000.csv.zip"
    raw=get(url)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names)!=1:raise ValueError("Unexpected ZIP contents")
        reader=csv.DictReader(io.StringIO(z.read(names[0]).decode("utf-8-sig")))
        required={"Sgmt","FinInstrmTp","SctySrs","TckrSymb","OpnPric","HghPric","LwPric","ClsPric","TtlTradgVol"}
        if not required.issubset(reader.fieldnames or []):raise ValueError("Missing UDiFF fields")
        rows={}
        for r in reader:
            symbol=r["TckrSymb"].strip()
            if symbol not in symbols or r["Sgmt"].strip()!="CM" or r["FinInstrmTp"].strip()!="STK" or r["SctySrs"].strip()!="EQ":continue
            if symbol in rows:raise ValueError("Duplicate EQ ticker "+symbol)
            rows[symbol]=check({"date":day.isoformat(),"symbol":symbol,"open":r["OpnPric"],
                "high":r["HghPric"],"low":r["LwPric"],"close":r["ClsPric"],"volume":r["TtlTradgVol"]})
        if len(rows)<60:raise ValueError("Only "+str(len(rows))+" current constituents")
        return rows

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--start",default="2024-07-08")
    p.add_argument("--days",type=int,default=45)
    args=p.parse_args()
    start=date.fromisoformat(args.start)
    if start<START:raise SystemExit("Pre-UDiFF data needs separately verified legacy parser; no silent fallback")
    if not 1<=args.days<=65:raise SystemExit("Use 1–65 calendar days per batch")
    if start+timedelta(days=args.days)>datetime.now(timezone.utc).date()+timedelta(days=1):
        raise SystemExit("Cannot request future dates")
    symbols=universe()
    records=load(ARCHIVE)
    existing_sessions={d for d,c in Counter(d for d,s in records).items() if c>=60}
    failures=[];new_dates=[];skipped=0
    for offset in range(args.days):
        day=start+timedelta(days=offset)
        if day.weekday()>=5 or day.isoformat() in existing_sessions:
            skipped+=1;continue
        try:
            rows=fetch_day(day,symbols)
            for symbol,r in rows.items():records[(r["date"],symbol)]=r
            new_dates.append(day.isoformat())
            print("OK",day,len(rows),flush=True)
        except Exception as e:
            failures.append({"date":day.isoformat(),"error":str(e)[:160]})
            print("SKIP",day,str(e)[:100],flush=True)
        time.sleep(0.2)
    if not records:raise SystemExit("No archive records; refusing empty write")
    ARCHIVE.parent.mkdir(exist_ok=True)
    temp=ARCHIVE.with_suffix(".tmp")
    with temp.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader()
        w.writerows(sorted(records.values(),key=lambda r:(r["date"],r["symbol"])))
    temp.replace(ARCHIVE)
    session_counts=Counter(d for d,s in records)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps({"generated_utc":datetime.now(timezone.utc).isoformat(),
        "requested_start":args.start,"requested_days":args.days,
        "new_sessions":len(new_dates),"skipped_dates":skipped,
        "failures":failures[:100],"archive_sessions":len(session_counts),
        "archive_records":len(records),"first_date":min(session_counts),
        "last_date":max(session_counts),"current_universe_only":True,
        "corporate_action_adjusted":False,"historical_membership_verified":False,
        "eligible_for_real_money":False,"status":"RESEARCH DATA ONLY — NOT VALIDATED FIVE-YEAR HISTORY"},indent=2))
    print("ARCHIVE",len(session_counts),"sessions",len(records),"records")
if __name__=="__main__":main()

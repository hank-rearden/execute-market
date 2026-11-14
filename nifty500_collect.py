#!/usr/bin/env python3
"""Isolated Nifty 500 historical research archive; never touches production Nifty100.
Collect bounded UDiFF date batches, keep only EQ/STK/CM, fail closed on universe errors.
"""
import argparse,csv,io,json,time,urllib.request,zipfile,math
from pathlib import Path
from datetime import date,timedelta,datetime,timezone
from collections import Counter
from stage4_archive import BASE,HEADERS,START,check
ARCH=Path("data/nifty500_research_history.csv")
STATUS=Path("reports/nifty500_collection_status.json")
FIELDS=["date","symbol","open","high","low","close","volume"]
URLS=["https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv","https://archives.nseindia.com/content/indices/ind_nifty500list.csv","https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv","https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"]
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url,headers=HEADERS),timeout=30) as f:return f.read()
def universe():
    errors=[]
    for url in URLS:
        try:
            raw=get(url)
            if not raw or len(raw)>2000000:raise ValueError("Unexpected constituent file size")
            reader=csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
            if not reader.fieldnames:raise ValueError("No CSV headers")
            names={k.strip().lower():k for k in reader.fieldnames}
            if "symbol" not in names:raise ValueError("Missing Symbol header: "+str(reader.fieldnames[:8]))
            col=names["symbol"]
            syms={r[col].strip() for r in reader if r.get(col,"").strip()}
            print("Constituent source:",url,"symbols:",len(syms),flush=True)
            # Index files may temporarily contain an additional line/constituent.
            # Reject obviously incomplete or wrong-universe downloads.
            if 480<=len(syms)<=520:
                return syms
            errors.append(url+": unexpected count "+str(len(syms)))
        except Exception as e:
            errors.append(url+": "+str(e)[:160])
            print("Universe source failed:",url,str(e)[:160],flush=True)
    raise RuntimeError("No verified Nifty500 source (480-520 symbols): "+"; ".join(errors))
def read_existing():
    records={}
    if not ARCH.exists():return records
    with ARCH.open(newline="",encoding="utf-8-sig") as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=FIELDS:raise RuntimeError("Unexpected archive columns")
        for r in reader:
            v=check(r);key=(v["date"],v["symbol"])
            if key in records:raise RuntimeError("Duplicate archive key "+str(key))
            records[key]=v
    return records
def fetch(day,symbols):
    url=BASE+"/content/cm/BhavCopy_NSE_CM_0_0_0_"+day.strftime("%Y%m%d")+"_F_0000.csv.zip"
    with zipfile.ZipFile(io.BytesIO(get(url))) as z:
        names=[x for x in z.namelist() if x.lower().endswith(".csv")]
        if len(names)!=1:raise ValueError("Unexpected ZIP")
        reader=csv.DictReader(io.StringIO(z.read(names[0]).decode("utf-8-sig")))
        required={"Sgmt","FinInstrmTp","SctySrs","TckrSymb","OpnPric","HghPric","LwPric","ClsPric","TtlTradgVol"}
        if not required.issubset(reader.fieldnames or []):raise ValueError("UDiFF columns missing")
        rows={}
        for r in reader:
            s=r["TckrSymb"].strip()
            if s not in symbols or r["Sgmt"].strip()!="CM" or r["FinInstrmTp"].strip()!="STK" or r["SctySrs"].strip()!="EQ":continue
            if s in rows:raise ValueError("Duplicate symbol "+s)
            rows[s]=check({"date":day.isoformat(),"symbol":s,"open":r["OpnPric"],"high":r["HghPric"],"low":r["LwPric"],"close":r["ClsPric"],"volume":r["TtlTradgVol"]})
        if len(rows)<300:raise ValueError("Only "+str(len(rows))+" current constituents in bhavcopy")
        return rows
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--start",default="2024-07-08");ap.add_argument("--days",type=int,default=90);args=ap.parse_args()
    begin=date.fromisoformat(args.start)
    today=datetime.now(timezone.utc).date()
    if begin<START or not 1<=args.days<=180 or begin>today:
        raise SystemExit("Invalid date window: start must be between "+str(START)+" and "+str(today)+", days 1-180")
    requested_end=begin+timedelta(days=args.days)
    effective_end=min(requested_end,today+timedelta(days=1))
    if effective_end<requested_end:
        print("Requested window extends into future; capping end to",effective_end.isoformat(),flush=True)
    effective_days=(effective_end-begin).days
    symbols=universe();records=read_existing()
    # A previous day's partial universe is never silently deemed complete.
    complete={d for d,n in Counter(d for d,s in records).items() if n>=max(300,int(len(symbols)*0.9))}
    errors=[];new=0
    for offset in range(effective_days):
        day=begin+timedelta(days=offset)
        if day.weekday()>=5 or day.isoformat() in complete:continue
        try:
            rows=fetch(day,symbols)
            for s,r in rows.items():records[(r["date"],s)]=r
            new+=1;print("OK",day,len(rows),flush=True)
        except Exception as e:
            errors.append({"date":day.isoformat(),"error":str(e)[:120]});print("SKIP",day,str(e)[:120],flush=True)
        time.sleep(.15)
    if not records:raise RuntimeError("No archive rows: refusing to publish empty research")
    ARCH.parent.mkdir(exist_ok=True)
    tmp=ARCH.with_suffix(".tmp")
    with tmp.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(sorted(records.values(),key=lambda x:(x["date"],x["symbol"])))
    tmp.replace(ARCH)
    days=Counter(d for d,s in records)
    STATUS.parent.mkdir(exist_ok=True)
    STATUS.write_text(json.dumps({"generated_utc":datetime.now(timezone.utc).isoformat(),"universe":"current Nifty 500","universe_size":len(symbols),"requested_start":args.start,"requested_days":args.days,"effective_days":effective_days,"new_sessions":new,"sessions":len(days),"records":len(records),"first_date":min(days),"last_date":max(days),"errors":errors,"historical_constituents_verified":False,"corporate_action_adjusted":False,"paper_only":True},indent=2))
    print("ARCHIVE",len(days),"sessions",len(records),"records")
if __name__=="__main__":main()

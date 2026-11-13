#!/usr/bin/env python3
"""Flag suspicious overnight price ratios; not a corporate-action verifier."""
import csv,json,math
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
p=Path("data/stage4_udiff_history.csv")
stocks=defaultdict(list)
with p.open(newline="",encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):stocks[r["symbol"]].append(r)
flags=[]
for symbol,rows in stocks.items():
    rows.sort(key=lambda r:r["date"])
    for a,b in zip(rows,rows[1:]):
        old=float(a["close"]);new=float(b["close"])
        if old<=0 or new<=0:raise SystemExit("Invalid close")
        change=(new/old-1)*100
        if abs(change)>=25:
            flags.append({"symbol":symbol,"previous_date":a["date"],"date":b["date"],
                          "previous_close":old,"close":new,"change_pct":round(change,3),
                          "reason":"Large move; independently check split/bonus/demerger/symbol history"})
report={"generated_utc":datetime.now(timezone.utc).isoformat(),"flags_count":len(flags),
        "flags":flags[:500],"corporate_actions_verified":False,
        "note":"Heuristic only. No automatic price adjustment; absence of flags does not establish clean prices."}
out=Path("reports/stage4_corporate_action_audit.json")
out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(report,indent=2))
print("Potential adjustment issues:",len(flags))

import csv
import json
from pathlib import Path

folder = Path("reports")
report = json.loads((folder / "screening.json").read_text())
rows = report["results"]
if len(rows) != 100 or not report.get("paper_only"):
    raise SystemExit("Expected 100 paper-only results")

def classify(r):
    status = r["status"]
    if status == "WATCHLIST ONLY - UNCONFIRMED":
        return "QUALIFIED"
    if status in ("STALE DATA", "INSUFFICIENT HISTORY"):
        return "EXCLUDED"
    if r["setup"] != "NO SETUP":
        return "NEAR-QUALIFIED"
    if (r["sessions"] >= 201 and all(r.get(k) is not None for k in
            ("ema20", "ema50", "ema200", "rsi14")) and
            r["close"] > r["ema20"] > r["ema50"] > r["ema200"] and
            45 <= r["rsi14"] <= 75):
        return "EMERGING"
    return "NO SETUP"

for row in rows:
    row["category"] = classify(row)
    row["reason"] = (row["status"] if row["category"] == "NEAR-QUALIFIED"
        else "All model filters passed; unconfirmed signal" if row["category"] == "QUALIFIED"
        else "Strong EMA trend and moderate RSI; no setup trigger" if row["category"] == "EMERGING"
        else row["status"])

order = {"QUALIFIED": 0, "NEAR-QUALIFIED": 1, "EMERGING": 2}
shortlist = sorted(
    (r for r in rows if r["category"] in order),
    key=lambda r: (order[r["category"]], -r["score"], r["symbol"])
)[:15]
summary = {
    "version": "4.1",
    "generated_utc": report["generated_utc"],
    "paper_only": True,
    "corporate_actions_verified": False,
    "signals_confirmed": False,
    "shortlist_count": len(shortlist),
    "shortlist": shortlist
}
(folder / "opportunities.json").write_text(json.dumps(summary, indent=2))
fields = list(dict.fromkeys(k for row in rows for k in row))
with (folder / "opportunities.csv").open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(shortlist)
print("Phase 4.1 shortlist:", len(shortlist))
for row in shortlist:
    print(row["symbol"], row["category"], row["score"], row["reason"])

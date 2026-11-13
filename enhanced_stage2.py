#!/usr/bin/env python3
"""Enhanced Stage 2: independent research ranking, NOT a trading signal."""
import csv
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path

DATA = Path("data/nifty100_history.csv")
OUT = Path("reports")
MIN_SESSIONS = 201

def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0

def rank_stock(symbol, rows, baseline):
    rows.sort(key=lambda r: r["date"])
    n = len(rows)
    result = {
        "symbol": symbol, "data_date": rows[-1]["date"] if rows else None,
        "sessions": n, "baseline_category": baseline.get("category", "UNKNOWN"),
        "baseline_status": baseline.get("status", "UNKNOWN"),
        "enhanced_score": None, "enhanced_status": "INSUFFICIENT HISTORY",
        "trend_points": 0, "structure_points": 0,
        "momentum_points": 0, "participation_points": 0,
        "setup_quality_points": 0, "volatility_points": 0,
        "reasons": [], "cautions": []
    }
    if n < MIN_SESSIONS:
        result["cautions"].append("Requires at least 201 sessions")
        return result
    close = rows[-1]["close"]
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    lows = [r["low"] for r in rows]
    vols = [r["volume"] for r in rows]
    ema20, ema50, ema200 = [baseline.get(k) for k in ("ema20", "ema50", "ema200")]
    rsi = baseline.get("rsi14")
    atr = baseline.get("atr14")
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (ema20, ema50, ema200, rsi, atr)):
        result["enhanced_status"] = "INVALID INDICATORS"
        return result
    # Trend: alignment, positive 20-session slope, and price not excessively extended.
    if close > ema20 > ema50 > ema200:
        result["trend_points"] += 15
        result["reasons"].append("Bullish EMA alignment")
    else:
        result["cautions"].append("EMA trend not fully aligned")
    old20 = mean(closes[-40:-20])
    new20 = mean(closes[-20:])
    if new20 > old20:
        result["trend_points"] += 10
        result["reasons"].append("Recent average price rising")
    if close / ema20 <= 1.06 and close >= ema20:
        result["trend_points"] += 5
    else:
        result["cautions"].append("Price extended or below EMA20")
    # Price structure: higher highs and lows comparing non-overlapping 20-session windows.
    if max(highs[-20:]) > max(highs[-40:-20]):
        result["structure_points"] += 10
    if min(lows[-20:]) > min(lows[-40:-20]):
        result["structure_points"] += 10
    if result["structure_points"] == 20:
        result["reasons"].append("Higher 20-session high and low")
    else:
        result["cautions"].append("Higher-high/higher-low structure incomplete")
    # Momentum: deliberately reward a middle band, not an overbought extreme.
    if 50 <= rsi <= 65:
        result["momentum_points"] = 15
    elif 45 <= rsi <= 70:
        result["momentum_points"] = 10
    elif 40 <= rsi <= 75:
        result["momentum_points"] = 5
    else:
        result["cautions"].append("RSI outside preferred momentum band")
    # Volume participation: volume vs prior 20 sessions, only if nonzero.
    avgvol = mean(vols[-21:-1])
    relative_volume = vols[-1] / avgvol if avgvol > 0 else None
    result["relative_volume"] = round(relative_volume, 2) if relative_volume is not None else None
    if relative_volume is not None and relative_volume >= 1.5 and close > rows[-1]["open"]:
        result["participation_points"] = 10
        result["reasons"].append("Strong up-day relative volume")
    elif relative_volume is not None and relative_volume >= 1 and close > rows[-1]["open"]:
        result["participation_points"] = 5
    else:
        result["cautions"].append("No strong positive volume confirmation")
    # Quality of actual baseline setups, no invented trade triggers.
    setup = baseline.get("setup", "NO SETUP")
    candle_range = rows[-1]["high"] - rows[-1]["low"]
    close_location = ((close - rows[-1]["low"]) / candle_range) if candle_range > 0 else 0.5
    result["close_location"] = round(close_location, 2)
    if setup == "BREAKOUT":
        if close_location >= 0.75:
            result["setup_quality_points"] = 15
            result["reasons"].append("Breakout closes in upper quarter of candle")
        elif close_location >= 0.6:
            result["setup_quality_points"] = 8
        else:
            result["cautions"].append("Breakout closes away from daily high")
    elif setup == "PULLBACK":
        prior_peak = max(highs[-21:-1])
        retracement = (prior_peak - close) / prior_peak if prior_peak > 0 else 0
        if 0.02 <= retracement <= 0.12 and close_location >= 0.6:
            result["setup_quality_points"] = 15
            result["reasons"].append("Measured retracement and constructive close")
        elif close_location >= 0.6:
            result["setup_quality_points"] = 7
            result["cautions"].append("Pullback depth not in preferred band")
        else:
            result["cautions"].append("Pullback recovery candle not convincing")
    else:
        result["cautions"].append("No baseline breakout or pullback trigger")
    # ATR as percentage of price, not raw rupees.
    atr_pct = atr / close * 100 if close > 0 else None
    result["atr_percent"] = round(atr_pct, 2) if atr_pct is not None else None
    if atr_pct is not None and 1 <= atr_pct <= 4:
        result["volatility_points"] = 10
    elif atr_pct is not None and 0.5 <= atr_pct <= 6:
        result["volatility_points"] = 5
    else:
        result["cautions"].append("Volatility outside preferred band")
    result["enhanced_score"] = sum(result[k] for k in (
        "trend_points", "structure_points", "momentum_points",
        "participation_points", "setup_quality_points", "volatility_points"))
    result["enhanced_status"] = ("RESEARCH CANDIDATE" if result["enhanced_score"] >= 65
        else "MONITOR" if result["enhanced_score"] >= 45 else "LOW PRIORITY")
    if baseline.get("status") != "WATCHLIST ONLY - UNCONFIRMED":
        result["cautions"].append("Not baseline risk-qualified; no trade entry authorized")
    return result

def main():
    history = defaultdict(list)
    with DATA.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            history[row["symbol"]].append({
                "date": row["date"], "open": float(row["open"]),
                "high": float(row["high"]), "low": float(row["low"]),
                "close": float(row["close"]), "volume": int(row["volume"])
            })
    with (OUT / "screening.json").open(encoding="utf-8") as f:
        baseline = json.load(f)
    if not baseline.get("paper_only") or len(baseline["results"]) != 100 or len(history) != 100:
        raise SystemExit("Expected 100 paper-only baseline results and 100 history symbols")
    indexed = {r["symbol"]: r for r in baseline["results"]}
    if set(indexed) != set(history):
        raise SystemExit("Baseline/history universe mismatch")
    result = [rank_stock(symbol, history[symbol], indexed[symbol]) for symbol in sorted(history)]
    result.sort(key=lambda r: (r["enhanced_score"] is None, -(r["enhanced_score"] or 0), r["symbol"]))
    payload = {
        "version": "enhanced-stage2-1.0", "generated_utc": baseline["generated_utc"],
        "paper_only": True, "validated_by_backtest": False,
        "relative_strength_vs_index_available": False,
        "sector_and_market_regime_available": False,
        "weekly_confirmation_available": False,
        "corporate_actions_verified": False,
        "notes": "Research-only 100-point heuristic. No index, sector or weekly data; no backtested predictive power. Baseline risk rules unchanged.",
        "results": result, "top15": result[:15]
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "enhanced_stage2.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    fields = ["symbol", "data_date", "sessions", "baseline_category", "baseline_status",
        "enhanced_score", "enhanced_status", "trend_points", "structure_points",
        "momentum_points", "participation_points", "setup_quality_points",
        "volatility_points", "relative_volume", "close_location", "atr_percent",
        "reasons", "cautions"]
    with (OUT / "enhanced_stage2.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in result:
            w.writerow({**row, "reasons": "; ".join(row["reasons"]), "cautions": "; ".join(row["cautions"])})
    print("Enhanced Stage 2 evaluated", len(result), "stocks; top 15 generated")

if __name__ == "__main__":
    main()

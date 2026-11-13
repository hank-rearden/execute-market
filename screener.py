
#!/usr/bin/env python3
"""EXECUTE Market Phase 4 — Nifty 100 paper-trading screener."""

import csv
import json
import math
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

INPUT = Path("data/nifty100_history.csv")
OUTPUT = Path("reports")
MIN_SESSIONS = 201

CAPITAL = 50000
MAX_RISK = 500
MAX_POSITION = 20000
MAX_STOP_PERCENT = 8
MIN_NET_RR = 1.8
MAX_AGE_DAYS = 5

# Illustrative NSE equity-delivery charges.
# Verify against current Zerodha contract notes.
STT = 0.001
STAMP_BUY = 0.00015
EXCHANGE = 0.0000297
SEBI = 0.000001
GST = 0.18
DP_SELL = 15.34
BROKERAGE = 0
SLIPPAGE_RATE = 0.001


def ema(values, n):
    if len(values) < n:
        return None
    result = sum(values[:n]) / n
    k = 2 / (n + 1)
    for value in values[n:]:
        result = value * k + result * (1 - k)
    return result


def rsi(values, n=14):
    if len(values) <= n:
        return None
    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]
    gain = sum(max(x, 0) for x in changes[:n]) / n
    loss = sum(max(-x, 0) for x in changes[:n]) / n
    for change in changes[n:]:
        gain = (gain * (n - 1) + max(change, 0)) / n
        loss = (loss * (n - 1) + max(-change, 0)) / n
    if loss == 0:
        return 100 if gain > 0 else 50
    return 100 - 100 / (1 + gain / loss)


def atr(rows, n=14):
    if len(rows) <= n:
        return None
    ranges = []
    for i in range(1, len(rows)):
        row = rows[i]
        prev = rows[i - 1]["close"]
        ranges.append(max(
            row["high"] - row["low"],
            abs(row["high"] - prev),
            abs(row["low"] - prev)
        ))
    result = sum(ranges[:n]) / n
    for value in ranges[n:]:
        result = (result * (n - 1) + value) / n
    return result


def charges(buy, sell, qty):
    """Estimated complete NSE delivery round trip."""
    buy_value = buy * qty
    sell_value = sell * qty
    turnover = buy_value + sell_value

    stt = turnover * STT
    stamp = buy_value * STAMP_BUY
    exchange = turnover * EXCHANGE
    sebi = turnover * SEBI
    gst = (exchange + sebi + BROKERAGE) * GST

    return (
        stt + stamp + exchange + sebi
        + gst + DP_SELL + BROKERAGE
    )


def trade_metrics(entry, stop, target, qty):
    if qty <= 0:
        return None

    buy_fill = entry * (1 + SLIPPAGE_RATE)
    stop_fill = stop * (1 - SLIPPAGE_RATE)
    target_fill = target * (1 - SLIPPAGE_RATE)

    stop_cost = charges(buy_fill, stop_fill, qty)
    target_cost = charges(buy_fill, target_fill, qty)

    net_loss = (
        (buy_fill - stop_fill) * qty + stop_cost
    )
    net_profit = (
        (target_fill - buy_fill) * qty - target_cost
    )

    return {
        "quantity": qty,
        "position_value": round(entry * qty, 2),
        "estimated_stop_cost": round(stop_cost, 2),
        "estimated_target_cost": round(target_cost, 2),
        "net_risk": round(net_loss, 2),
        "net_profit": round(net_profit, 2),
        "net_rr": round(
            net_profit / net_loss, 2
        ) if net_loss > 0 else 0
    }


def load_data():
    data = defaultdict(list)
    seen = set()

    if not INPUT.exists():
        raise SystemExit("Historical CSV not found")

    with INPUT.open(
        newline="", encoding="utf-8-sig"
    ) as f:
        reader = csv.DictReader(f)
        required = {
            "date", "symbol", "open",
            "high", "low", "close", "volume"
        }
        if not required.issubset(reader.fieldnames or []):
            raise SystemExit("Invalid CSV columns")

        for raw in reader:
            symbol = raw["symbol"].strip()
            d = date.fromisoformat(raw["date"])
            key = (symbol, d)
            if key in seen:
                raise SystemExit(f"Duplicate: {key}")
            seen.add(key)

            row = {
                "date": d,
                "volume": int(raw["volume"])
            }
            for col in ("open", "high", "low", "close"):
                row[col] = float(raw[col])

            prices = [
                row[x] for x in
                ("open", "high", "low", "close")
            ]
            if (
                not all(math.isfinite(x) and x > 0
                        for x in prices)
                or row["low"] > min(prices)
                or row["high"] < max(prices)
                or row["volume"] < 0
            ):
                raise SystemExit(f"Bad OHLCV: {key}")

            data[symbol].append(row)

    if len(data) != 100:
        raise SystemExit(f"Expected 100 Nifty symbols, found {len(data)}")
    for symbol in data:
        data[symbol].sort(key=lambda x: x["date"])

    return data


def analyze(symbol, rows, today):
    last = rows[-1]
    close = last["close"]
    closes = [x["close"] for x in rows]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    momentum = rsi(closes)
    volatility = atr(rows)

    age = (today - last["date"]).days
    if age < 0:
        raise SystemExit("Future-dated data")

    fresh = age <= MAX_AGE_DAYS
    enough = len(rows) >= MIN_SESSIONS

    trend = bool(
        enough and close > e20 > e50 > e200
    )

    setup = "NO SETUP"
    score = 0

    if trend:
        score += 30

    if len(rows) >= 21 and enough:
        previous = rows[-21:-1]
        prior_high = max(x["high"] for x in previous)
        avg_volume = sum(
            x["volume"] for x in previous
        ) / 20

        breakout = (
            trend
            and close > prior_high
            and last["volume"] >= 1.5 * avg_volume
            and momentum < 80
        )

        pullback = (
            trend
            and abs(close / e20 - 1) <= 0.025
            and 45 <= momentum <= 70
            and close > last["open"]
        )

        if breakout:
            setup = "BREAKOUT"
            score += 35
        elif pullback:
            setup = "PULLBACK"
            score += 25

    if momentum is not None and 50 <= momentum <= 70:
        score += 10

    result = {
        "symbol": symbol,
        "data_date": last["date"].isoformat(),
        "sessions": len(rows),
        "close": round(close, 2),
        "ema20": round(e20, 2) if e20 else None,
        "ema50": round(e50, 2) if e50 else None,
        "ema200": round(e200, 2) if e200 else None,
        "rsi14": round(momentum, 2) if momentum is not None else None,
        "atr14": round(volatility, 2) if volatility else None,
        "setup": setup,
        "score": score,
        "entry": None,
        "stop": None,
        "target": None,
        "quantity": 0,
        "position_value": None,
        "net_risk": None,
        "net_profit": None,
        "net_rr": None,
        "status": "NO SETUP"
    }

    if not fresh:
        result["status"] = "STALE DATA"
        return result

    if not enough:
        result["status"] = "INSUFFICIENT HISTORY"
        return result

    if setup == "NO SETUP":
        return result

    # Entry above signal candle.
    entry = round(last["high"] * 1.001, 2)

    # Candidate stop using ATR and recent support.
    atr_stop = entry - 2 * volatility
    support = min(x["low"] for x in rows[-10:])
    support_stop = support - 0.25 * volatility

    # Choose the more conservative (lower) stop.
    stop = round(min(atr_stop, support_stop), 2)

    if stop <= 0 or stop >= entry:
        result["status"] = "INVALID STOP"
        return result

    stop_pct = (entry - stop) / entry * 100

    result["entry"] = entry
    result["stop"] = stop

    if stop_pct > MAX_STOP_PERCENT:
        result["status"] = "REJECTED - WIDE STOP"
        return result

    unit_risk = entry - stop
    target = round(entry + 2.5 * unit_risk, 2)
    result["target"] = target

    max_qty = min(
        int(MAX_POSITION / entry),
        int(CAPITAL / entry),
        int(MAX_RISK / unit_risk)
    )

    chosen = None

    # Include estimated costs and slippage in risk.
    for qty in range(max_qty, 0, -1):
        metrics = trade_metrics(
            entry, stop, target, qty
        )
        if metrics["net_risk"] <= MAX_RISK:
            chosen = metrics
            break

    if chosen is None:
        result["status"] = "REJECTED - RISK LIMIT"
        return result

    result.update(chosen)

    if chosen["net_rr"] < MIN_NET_RR:
        result["status"] = "REJECTED - LOW NET RR"
    else:
        result["status"] = (
            "WATCHLIST ONLY - UNCONFIRMED"
        )

    return result


def main():
    today = datetime.now(timezone.utc).date()
    data = load_data()

    results = [
        analyze(symbol, data[symbol], today)
        for symbol in sorted(data)
    ]

    results.sort(
        key=lambda x: (-x["score"], x["symbol"])
    )

    OUTPUT.mkdir(parents=True, exist_ok=True)

    report = {
        "version": "4.0",
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "universe_size": len(data),
        "eligible_history_count": sum(len(rows) >= MIN_SESSIONS for rows in data.values()),
        "capital": CAPITAL,
        "risk_limit": MAX_RISK,
        "paper_only": True,
        "corporate_actions_verified": False,
        "charges_verified": False,
        "signals_confirmed": False,
        "notes": (
            "All candidates require independent "
            "corporate-action and signal validation. "
            "Charges and slippage are estimates. "
            "Taxes on capital gains are excluded. "
            "Stops cannot guarantee maximum losses."
        ),
        "results": results
    }

    with (OUTPUT / "screening.json").open(
        "w", encoding="utf-8"
    ) as f:
        json.dump(report, f, indent=2)

    with (OUTPUT / "screening.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=list(dict.fromkeys(
                key for result in results for key in result
            ))
        )
        writer.writeheader()
        writer.writerows(results)

    print("EXECUTE MARKET PHASE 4")
    print("PAPER TRADING ONLY")
    for r in results:
        print(
            r["symbol"],
            "|", r["setup"],
            "|", r["status"],
            "| Net R:R", r["net_rr"]
        )
    print("Symbols screened:", len(results))
    print("Reports generated successfully")


if __name__ == "__main__":
    main()

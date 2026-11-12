
#!/usr/bin/env python3
"""EXECUTE Market Phase 3: NSE swing screener. Paper trading only."""

import csv
import json
import math
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

INPUT = Path("data/nse_history.csv")
OUTPUT = Path("reports")

CAPITAL = 50000
RISK_PER_TRADE = 500
MAX_POSITION_VALUE = 20000
MAX_DATA_AGE_DAYS = 7

SYMBOLS = ("DLF", "HDFCLIFE", "TMCV")


def ema(values, period):
    if len(values) < period:
        return None

    result = sum(values[:period]) / period
    multiplier = 2 / (period + 1)

    for value in values[period:]:
        result = value * multiplier + result * (1 - multiplier)

    return result


def rsi(values, period=14):
    if len(values) <= period:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gain = sum(max(x, 0) for x in changes[:period]) / period
    loss = sum(max(-x, 0) for x in changes[:period]) / period

    for change in changes[period:]:
        gain = (gain * (period - 1) + max(change, 0)) / period
        loss = (loss * (period - 1) + max(-change, 0)) / period

    if loss == 0:
        return 100.0 if gain > 0 else 50.0

    return 100 - 100 / (1 + gain / loss)


def atr(rows, period=14):
    if len(rows) <= period:
        return None

    true_ranges = []

    for i in range(1, len(rows)):
        current = rows[i]
        previous = rows[i - 1]

        high = current["high"]
        low = current["low"]
        previous_close = previous["close"]

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        true_ranges.append(tr)

    result = sum(true_ranges[:period]) / period

    for tr in true_ranges[period:]:
        result = (
            result * (period - 1) + tr
        ) / period

    return result


def read_data():
    if not INPUT.exists():
        raise SystemExit(
            "Missing data/nse_history.csv"
        )

    by_symbol = defaultdict(list)
    seen = set()

    required = {
        "date", "symbol", "series",
        "open", "high", "low",
        "close", "volume"
    }

    with INPUT.open(
        newline="", encoding="utf-8-sig"
    ) as file:

        reader = csv.DictReader(file)

        if not required.issubset(reader.fieldnames or []):
            raise SystemExit("Missing required CSV columns")

        for raw in reader:
            symbol = raw["symbol"].strip()

            if symbol not in SYMBOLS:
                continue

            trading_date = date.fromisoformat(raw["date"])
            key = (trading_date, symbol)

            if key in seen:
                raise SystemExit(
                    f"Duplicate record: {key}"
                )

            seen.add(key)

            series = raw["series"].strip()

            if series not in ("EQ", "BE"):
                raise SystemExit(
                    f"Unexpected trading series: {key}"
                )

            if series == "BE" and symbol != "TMCV":
                raise SystemExit(
                    f"Unexpected BE series: {key}"
                )

            row = {
                "date": trading_date,
                "symbol": symbol,
                "series": series
            }

            for field in (
                "open", "high", "low", "close"
            ):
                row[field] = float(raw[field])

            row["volume"] = int(raw["volume"])

            prices = [
                row["open"],
                row["high"],
                row["low"],
                row["close"]
            ]

            if (
                not all(
                    math.isfinite(p) and p > 0
                    for p in prices
                )
                or row["low"] > min(prices)
                or row["high"] < max(prices)
                or row["volume"] < 0
            ):
                raise SystemExit(
                    f"Invalid OHLCV: {key}"
                )

            by_symbol[symbol].append(row)

    for symbol in SYMBOLS:
        by_symbol[symbol].sort(
            key=lambda x: x["date"]
        )

        if not by_symbol[symbol]:
            raise SystemExit(
                f"No data available for {symbol}"
            )

    return by_symbol


def analyze(symbol, rows, today):
    last = rows[-1]

    closes = [
        row["close"] for row in rows
    ]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)

    momentum = rsi(closes, 14)
    volatility = atr(rows, 14)

    age = (today - last["date"]).days

    if age < 0:
        raise SystemExit(
            "Future data date detected"
        )

    fresh = age <= MAX_DATA_AGE_DAYS

    sufficient_history = (
        len(rows) >= 201
        and all(
            x is not None
            for x in (
                ema20, ema50, ema200,
                momentum, volatility
            )
        )
    )

    trend_up = bool(
        sufficient_history
        and last["close"] > ema20 > ema50 > ema200
    )

    avg_volume = None
    prior_high = None

    if len(rows) >= 21:
        previous = rows[-21:-1]

        avg_volume = sum(
            row["volume"] for row in previous
        ) / 20

        prior_high = max(
            row["high"] for row in previous
        )

    breakout = bool(
        trend_up
        and prior_high is not None
        and last["close"] > prior_high
        and avg_volume is not None
        and avg_volume > 0
        and last["volume"] >= 1.5 * avg_volume
        and momentum < 80
    )

    pullback = bool(
        trend_up
        and abs(last["close"] / ema20 - 1) <= 0.025
        and 45 <= momentum <= 70
        and last["close"] > last["open"]
    )

    if breakout:
        setup = "BREAKOUT"
    elif pullback:
        setup = "PULLBACK"
    else:
        setup = "NO SETUP"

    entry = None
    stop = None
    target = None
    quantity = None
    planned_risk = None
    position_value = None

    if setup != "NO SETUP" and volatility:
        entry = round(
            last["high"] * 1.001, 2
        )

        stop = round(
            min(last["low"], ema20)
            - 0.5 * volatility, 2
        )

        if 0 < stop < entry:
            unit_risk = entry - stop

            quantity = min(
                int(RISK_PER_TRADE / unit_risk),
                int(MAX_POSITION_VALUE / entry),
                int(CAPITAL / entry)
            )

            if quantity >= 1:
                target = round(
                    entry + 2 * unit_risk, 2
                )

                planned_risk = round(
                    quantity * unit_risk, 2
                )

                position_value = round(
                    quantity * entry, 2
                )
            else:
                quantity = 0

    score = (
        int(trend_up) * 30
        + int(breakout) * 35
        + int(pullback) * 25
        + (
            10 if momentum is not None
            and 50 <= momentum <= 70
            else 0
        )
    )

    if not fresh:
        status = "STALE DATA"
    elif not sufficient_history:
        status = "INSUFFICIENT HISTORY"
    elif setup != "NO SETUP":
        status = "UNCONFIRMED - EOD / UNADJUSTED"
    else:
        status = "NO CONFIRMED SIGNAL"

    return {
        "symbol": symbol,
        "data_date": last["date"].isoformat(),
        "sessions": len(rows),
        "close": round(last["close"], 2),
        "ema20": round(ema20, 2) if ema20 is not None else None,
        "ema50": round(ema50, 2) if ema50 is not None else None,
        "ema200": round(ema200, 2) if ema200 is not None else None,
        "rsi14": round(momentum, 2) if momentum is not None else None,
        "atr14": round(volatility, 2) if volatility is not None else None,
        "volume": last["volume"],
        "avg_volume20": round(avg_volume) if avg_volume is not None else None,
        "trend_up": trend_up,
        "setup": setup,
        "score": score,
        "entry_trigger": entry,
        "stop": stop,
        "target_2r": target,
        "quantity": quantity,
        "planned_risk_inr": planned_risk,
        "position_value_inr": position_value,
        "fresh": fresh,
        "history_sufficient": sufficient_history,
        "status": status,
        "notes": (
            "Paper research only. "
            "Corporate actions unverified. "
            "Charges, taxes and slippage excluded."
        )
    }


def main():
    data = read_data()

    today = datetime.now(timezone.utc).date()

    results = [
        analyze(symbol, data[symbol], today)
        for symbol in SYMBOLS
    ]

    results.sort(
        key=lambda r: (-r["score"], r["symbol"])
    )

    OUTPUT.mkdir(
        parents=True, exist_ok=True
    )

    report = {
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "capital_inr": CAPITAL,
        "max_risk_per_trade_inr": RISK_PER_TRADE,
        "max_position_value_inr": MAX_POSITION_VALUE,
        "results": results,
        "disclaimer": (
            "Unadjusted NSE historical data. "
            "Signals are unconfirmed. "
            "Paper trading only."
        )
    }

    with (
        OUTPUT / "screening.json"
    ).open("w", encoding="utf-8") as file:
        json.dump(
            report, file, indent=2
        )

    with (
        OUTPUT / "screening.csv"
    ).open(
        "w", newline="", encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=list(results[0].keys())
        )

        writer.writeheader()
        writer.writerows(results)

    print("EXECUTE MARKET - PHASE 3")
    print("PAPER TRADING ONLY")

    for result in results:
        print(
            result["symbol"],
            "Close:", result["close"],
            "RSI:", result["rsi14"],
            "ATR:", result["atr14"],
            "Setup:", result["setup"],
            "Status:", result["status"]
        )

    print("Reports generated successfully")


if __name__ == "__main__":
    main()

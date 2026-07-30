"""Fetch ~25 years of daily S&P 500 (^GSPC) history from Yahoo Finance and save as CSV."""

from __future__ import annotations

import csv
import json
import time
import urllib.request
from pathlib import Path

SYMBOL = "%5EGSPC"  # ^GSPC url-encoded
YEARS = 25
OUT_PATH = Path(__file__).parent / "data" / "sp500_daily.csv"


def fetch_history() -> dict:
    period2 = int(time.time())
    period1 = period2 - YEARS * 365 * 24 * 3600 - 86400 * 30
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_history(payload: dict) -> list[dict]:
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    adjclose = result["indicators"].get("adjclose", [{}])[0].get("adjclose")

    rows = []
    for i, ts in enumerate(timestamps):
        o, h, l, c, v = (
            quote["open"][i],
            quote["high"][i],
            quote["low"][i],
            quote["close"][i],
            quote["volume"][i],
        )
        if c is None:
            continue
        date = time.strftime("%Y-%m-%d", time.gmtime(ts))
        rows.append(
            {
                "date": date,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "adj_close": adjclose[i] if adjclose else c,
                "volume": v,
            }
        )
    return rows


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = fetch_history()
    rows = parse_history(payload)
    rows.sort(key=lambda r: r["date"])

    with OUT_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "adj_close", "volume"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows spanning {rows[0]['date']} to {rows[-1]['date']} -> {OUT_PATH}")


if __name__ == "__main__":
    main()

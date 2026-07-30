"""Fetch ~25 years of daily history for the whole symbol universe from Yahoo Finance.

Writes one tidy CSV (`data/prices.csv`) with a `symbol` column, plus a small
`data/symbols.csv` of names and classifications. build_db.py loads both.

Resumable by design. Yahoo rate-limits bursts with HTTP 429, and a 50-symbol run
is a burst. Each symbol's rows are cached under `data/cache/` as soon as they
arrive, so a throttled run loses no completed work: re-run and it fetches only
what's still missing. `--force` re-fetches everything.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import universe

YEARS = 25
DATA_DIR = Path(__file__).parent / "data"
CACHE_DIR = DATA_DIR / "cache"
# Gzipped: the plain CSV is ~33 MB of mostly-repetitive numeric text, which
# compresses to ~8 MB and is what gets committed so a fresh deploy can build
# its own database.
PRICES_PATH = DATA_DIR / "prices.csv.gz"
SYMBOLS_PATH = DATA_DIR / "symbols.csv"

PRICE_FIELDS = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"]

# Deliberately a bare UA. Measured: this endpoint serves `Mozilla/5.0` fine but
# answers 429 to a full Chrome UA + Referer, which apparently routes into a
# stricter path that expects a real browser session/crumb. Don't "improve" this
# into a realistic browser string -- it breaks the fetch.
UA = "Mozilla/5.0"

# Pacing. Politeness here is cheaper than a throttled run: 429 backoff costs far
# more wall-clock than spacing requests out in the first place.
REQUEST_GAP = 0.6
BACKOFF_429 = 20.0
BACKOFF_OTHER = 2.0


def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol.replace('^', '_idx_').replace('/', '_')}.csv"


def fetch_symbol(symbol: str, period1: int, period2: int, retries: int = 4) -> dict:
    quoted = urllib.parse.quote(symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quoted}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history"
    )
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_err = exc
            # 429 needs a real cool-off, not the usual short retry.
            wait = BACKOFF_429 * (attempt + 1) if exc.code == 429 else BACKOFF_OTHER * (attempt + 1)
            if attempt < retries - 1:
                print(f"       {symbol}: HTTP {exc.code}, waiting {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_err = exc
            time.sleep(BACKOFF_OTHER * (attempt + 1))
    raise RuntimeError(f"giving up after {retries} attempts ({last_err})")


def parse_rows(symbol: str, payload: dict) -> list[dict]:
    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    adj = (result["indicators"].get("adjclose") or [{}])[0].get("adjclose")

    rows = []
    for i, ts in enumerate(timestamps):
        close = quote["close"][i]
        if close is None:
            continue  # holiday/halted session with no print
        rows.append({
            "symbol": symbol,
            "date": time.strftime("%Y-%m-%d", time.gmtime(ts)),
            "open": quote["open"][i],
            "high": quote["high"][i],
            "low": quote["low"][i],
            "close": close,
            "adj_close": adj[i] if adj and adj[i] is not None else close,
            "volume": quote["volume"][i],
        })
    return rows


def _write_cache(symbol: str, rows: list[dict]) -> None:
    with _cache_path(symbol).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PRICE_FIELDS)
        w.writeheader()
        w.writerows(rows)


def _read_cache(symbol: str) -> list[dict]:
    p = _cache_path(symbol)
    if not p.exists():
        return []
    with p.open() as f:
        return list(csv.DictReader(f))


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch daily history for the symbol universe.")
    ap.add_argument("--force", action="store_true", help="re-fetch symbols already cached")
    ap.add_argument("--only", default=None, help="comma-separated symbols to fetch")
    args = ap.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    period2 = int(time.time())
    period1 = period2 - YEARS * 365 * 24 * 3600 - 86400 * 30

    symbols = universe.all_symbols()
    if args.only:
        wanted = {s.strip().upper() for s in args.only.split(",") if s.strip()}
        symbols = [s for s in symbols if s.upper() in wanted]

    todo = [s for s in symbols if args.force or not _cache_path(s).exists()]
    cached = len(symbols) - len(todo)
    print(f"{len(symbols)} symbols: {cached} already cached, {len(todo)} to fetch")

    failed: list[str] = []
    for i, sym in enumerate(todo, 1):
        try:
            rows = parse_rows(sym, fetch_symbol(sym, period1, period2))
            if not rows:
                raise RuntimeError("no usable rows")
            _write_cache(sym, rows)  # persist immediately so a later 429 can't undo it
            print(f"[{i:>2}/{len(todo)}] {sym:<6} {len(rows):>5} rows  {rows[0]['date']} -> {rows[-1]['date']}")
        except Exception as exc:  # one bad symbol shouldn't sink the run
            failed.append(sym)
            print(f"[{i:>2}/{len(todo)}] {sym:<6} FAILED: {exc}", file=sys.stderr)
        time.sleep(REQUEST_GAP)

    # Assemble from cache, so partial progress across runs still produces a CSV.
    all_rows: list[dict] = []
    for sym in symbols:
        all_rows.extend(_read_cache(sym))

    if not all_rows:
        raise SystemExit("no data cached at all - refusing to write an empty CSV")

    all_rows.sort(key=lambda r: (r["symbol"], r["date"]))
    with gzip.open(PRICES_PATH, "wt", newline="", compresslevel=9) as f:
        w = csv.DictWriter(f, fieldnames=PRICE_FIELDS)
        w.writeheader()
        w.writerows(all_rows)

    fetched = {r["symbol"] for r in all_rows}
    with SYMBOLS_PATH.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "name", "sector", "industry", "is_index"])
        w.writerows([r for r in universe.symbol_rows() if r[0] in fetched])

    print(f"\nWrote {len(all_rows):,} rows for {len(fetched)} symbols -> {PRICES_PATH}")
    missing = [s for s in symbols if s not in fetched]
    if missing:
        print(f"Still missing {len(missing)}: {', '.join(missing)}", file=sys.stderr)
        print("Re-run to top up (cached symbols are skipped).", file=sys.stderr)


if __name__ == "__main__":
    main()

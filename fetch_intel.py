"""Build the Market Intelligence universe: pick it, price it, and fetch its facts.

Separate from `fetch_data.py` on purpose, and it writes to separate tables.

`fetch_data.py` serves the app's original core -- 50 curated symbols with 25
years of history, hand-classified in `universe.py`. This script serves the
ranking engine: ~500 liquid US names with 5 years of history and SEC-derived
classification. **They must not share a table.** Every existing page reads
`prices`/`symbols` through `symbol_stats`, `leaderboard`, `sector_performance`
and the period movers; dropping 500 five-year names into `prices` would change
the leaderboard, the sector medians and every "biggest mover" answer on pages
that were explicitly not in scope to change. So this writes `intel_prices`,
`intel_symbols` and `intel_fundamentals`, and the original tables are untouched.

Three stages, each resumable and each safe to run alone:

    --universe   SEC frames pre-screen  -> data/intel_universe.csv
    --prices     Yahoo chart, 5y daily  -> data/intel_prices.csv.gz
    --facts      SEC companyfacts       -> data/intel_fundamentals.csv.gz

Universe selection is deliberately two-stage. SEC's `frames` API ranks every
filer on one concept in a single request, which is what makes a wide universe
affordable at all -- but it can only see reported fundamentals, and a company
with large revenue can still be untradeable. So frames pre-screens on revenue
and assets (assets catches banks and insurers, whose revenue understates their
size), and the final cut is by realized dollar turnover from the prices this
script just fetched. Liquidity is the screen that matters for a page proposing
opportunities: a rank you cannot act on at the screen price is not one.
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

import sec
import universe

DATA_DIR = Path(__file__).parent / "data"
CACHE_DIR = DATA_DIR / "intel_cache"
UNIVERSE_CSV = DATA_DIR / "intel_universe.csv"
PRICES_GZ = DATA_DIR / "intel_prices.csv.gz"
FUNDAMENTALS_GZ = DATA_DIR / "intel_fundamentals.csv.gz"

YEARS = 5
TARGET_UNIVERSE = 500
CANDIDATE_POOL = 1100

UA = "Mozilla/5.0"          # see fetch_data.py -- a realistic UA gets 429'd
REQUEST_GAP = 0.6
BACKOFF_429 = 20.0
BACKOFF_OTHER = 2.0

PRICE_FIELDS = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"]
UNIVERSE_FIELDS = ["symbol", "cik", "name", "sector", "exchange"]

FUNDAMENTAL_FIELDS = [
    "symbol", "cik", "asof",
    "revenue", "revenue_prior", "net_income", "net_income_prior",
    "gross_profit", "gross_profit_prior", "operating_income", "operating_income_prior",
    "interest_expense", "interest_expense_prior", "ocf", "ocf_prior",
    "capex", "capex_prior",
    "assets", "liabilities", "equity", "assets_current", "liabilities_current",
    "long_term_debt", "short_term_debt", "shares_outstanding", "earnings_stability",
]

# Concepts and periods for the frames pre-screen. Two concepts because revenue
# alone mis-ranks financials: a bank's revenue is a fraction of its balance
# sheet, so ranking on revenue would drop most of the sector out of the universe.
FRAME_SCREENS = (("Revenues", "2025"), ("Assets", "2025Q4I"), ("Assets", "2024Q4I"))


# ---------------------------------------------------------------------------
# Stage 1 — universe
# ---------------------------------------------------------------------------
def build_universe() -> list[dict]:
    """Pick the candidate universe and write it to CSV.

    The curated 50 from `universe.py` are force-included regardless of where the
    frames screen puts them, so the intelligence page always covers at least the
    names the rest of the app already discusses -- a stock on the Companies page
    that is missing from the ranking board reads as a bug.
    """
    print("Fetching SEC ticker map...")
    tickers = sec.ticker_map()
    if not tickers:
        raise SystemExit("could not fetch the SEC ticker map")
    print(f"  {len(tickers):,} US-listed lines (NYSE / Nasdaq / NYSE American)")

    # One CIK owns every line the company has listed -- common stock, each
    # preferred series, warrants, units, structured notes. Mapping CIK -> one
    # ticker by taking whichever arrived last picked JPMorgan's `VYLD` note over
    # `JPM` and Bank of America's `MER-PK` preferred over `BAC`, which then
    # carried the parent's fundamentals under a ticker nobody researches.
    #
    # Obvious non-common lines are dropped by pattern here; the rest are all
    # kept and the primary line is chosen EMPIRICALLY in `_assemble_prices`, by
    # traded dollar volume per CIK. Pattern-matching alone cannot do it (`VYLD`
    # looks like ordinary common stock), and liquidity is the definition that
    # actually matters: the primary line is the one the market trades.
    by_cik: dict[int, list[tuple[str, dict]]] = {}
    for ticker, meta in tickers.items():
        if _is_derivative_line(ticker):
            continue
        by_cik.setdefault(meta["cik"], []).append((ticker, meta))

    scored: dict[int, float] = {}
    for tag, period in FRAME_SCREENS:
        rows = sec.largest_by(tag, period, top=CANDIDATE_POOL)
        print(f"  frames {tag} CY{period}: {len(rows)} filers")
        for rank, (cik, _) in enumerate(rows):
            # Rank-based, not value-based: revenue and assets are different units
            # and summing them would let one dominate purely by scale.
            scored[cik] = scored.get(cik, 0.0) + (len(rows) - rank) / len(rows)
        time.sleep(0.2)

    ranked = sorted(scored.items(), key=lambda kv: -kv[1])
    picked: dict[str, dict] = {}
    ciks_taken = 0
    for cik, _ in ranked:
        lines = by_cik.get(cik)
        if not lines:
            continue
        for symbol, meta in lines:
            picked[symbol] = {"symbol": symbol, "cik": cik, "name": meta["name"],
                              "exchange": meta["exchange"], "sector": ""}
        ciks_taken += 1
        if ciks_taken >= CANDIDATE_POOL:
            break

    for symbol in universe.EQUITIES:
        if symbol not in picked and symbol in tickers:
            meta = tickers[symbol]
            picked[symbol] = {"symbol": symbol, "cik": meta["cik"],
                              "name": meta["name"], "exchange": meta["exchange"],
                              "sector": ""}

    print(f"  {len(picked)} candidates; resolving sectors from SIC...")
    rows = _attach_sectors(list(picked.values()))
    _write_universe(rows)
    print(f"Wrote {len(rows)} candidates -> {UNIVERSE_CSV}")
    return rows


def _is_derivative_line(ticker: str) -> bool:
    """True for preferred series, warrants, units and rights.

    A cheap pre-filter so the price fetch is not spent on lines that cannot win
    the liquidity pick anyway. Deliberately conservative: a genuine dual-class
    suffix is a single letter (`BRK-A`, `BRK-B`, `HEI-A`) and is kept, while a
    preferred series is `P` plus a series letter (`MER-PK`, `BAC-PL`). Anything
    this misses is caught by the per-CIK liquidity pick, so a false negative
    costs one wasted fetch rather than a wrong universe.
    """
    if "-" not in ticker:
        return False
    suffix = ticker.split("-", 1)[1]
    if len(suffix) == 1 and suffix.isalpha():
        return False                      # dual-class common
    return suffix.startswith(("P", "W", "R", "U"))


def _attach_sectors(rows: list[dict]) -> list[dict]:
    """Fill each row's sector from its SIC code (one submissions request each).

    Cached on disk like the price fetch, because this is the slow part of the
    universe stage and a re-run should not repeat it.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "sic.json"
    cache: dict[str, str] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except json.JSONDecodeError:
            cache = {}

    for i, row in enumerate(rows, 1):
        key = str(row["cik"])
        if key not in cache:
            payload = sec._get_json(
                f"https://data.sec.gov/submissions/CIK{row['cik']:010d}.json")
            cache[key] = sec.sector_for_sic((payload or {}).get("sic"))
            time.sleep(sec.GAP)
            if i % 50 == 0:
                cache_path.write_text(json.dumps(cache))
                print(f"    {i}/{len(rows)} sectors")
        row["sector"] = cache[key]

    cache_path.write_text(json.dumps(cache))
    return rows


def _write_universe(rows: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with UNIVERSE_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=UNIVERSE_FIELDS)
        w.writeheader()
        w.writerows([{k: r.get(k, "") for k in UNIVERSE_FIELDS} for r in rows])


def read_universe() -> list[dict]:
    if not UNIVERSE_CSV.exists():
        raise SystemExit(f"Missing {UNIVERSE_CSV}. Run with --universe first.")
    with UNIVERSE_CSV.open() as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Stage 2 — prices
# ---------------------------------------------------------------------------
def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol.replace('^', '_idx_').replace('/', '_')}.csv"


def _fetch_chart(symbol: str, period1: int, period2: int, retries: int = 3) -> dict:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol, safe='')}"
           f"?period1={period1}&period2={period2}&interval=1d&events=history")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (404, 401):
                raise RuntimeError(f"HTTP {exc.code}") from exc
            wait = BACKOFF_429 * (attempt + 1) if exc.code == 429 else BACKOFF_OTHER * (attempt + 1)
            if attempt < retries - 1:
                time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(BACKOFF_OTHER * (attempt + 1))
    raise RuntimeError(f"giving up ({last})")


def _parse_rows(symbol: str, payload: dict) -> list[dict]:
    result = payload["chart"]["result"][0]
    stamps = result.get("timestamp") or []
    q = result["indicators"]["quote"][0]
    adj = (result["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    rows = []
    for i, ts in enumerate(stamps):
        close = q["close"][i]
        if close is None:
            continue
        rows.append({
            "symbol": symbol,
            "date": time.strftime("%Y-%m-%d", time.gmtime(ts)),
            "open": q["open"][i], "high": q["high"][i], "low": q["low"][i],
            "close": close,
            "adj_close": adj[i] if adj and adj[i] is not None else close,
            "volume": q["volume"][i],
        })
    return rows


def fetch_prices(force: bool = False) -> None:
    """Fetch 5y daily history for every universe candidate, then cut to the most
    liquid `TARGET_UNIVERSE` and write both artifacts."""
    rows = read_universe()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    period2 = int(time.time())
    period1 = period2 - YEARS * 365 * 24 * 3600 - 86400 * 10

    symbols = [r["symbol"] for r in rows]
    todo = [s for s in symbols if force or not _cache_path(s).exists()]
    print(f"{len(symbols)} candidates: {len(symbols) - len(todo)} cached, {len(todo)} to fetch")

    failed: list[str] = []
    for i, sym in enumerate(todo, 1):
        try:
            got = _parse_rows(sym, _fetch_chart(sym, period1, period2))
            if not got:
                raise RuntimeError("no usable rows")
            with _cache_path(sym).open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=PRICE_FIELDS)
                w.writeheader()
                w.writerows(got)
            if i % 25 == 0 or i == len(todo):
                print(f"  [{i}/{len(todo)}] {sym} ({len(got)} rows)")
        except Exception as exc:
            failed.append(sym)
            print(f"  [{i}/{len(todo)}] {sym} FAILED: {exc}", file=sys.stderr)
        time.sleep(REQUEST_GAP)

    _assemble_prices(rows)
    if failed:
        print(f"{len(failed)} symbols failed: {', '.join(failed[:15])}", file=sys.stderr)


def _assemble_prices(universe_rows: list[dict]) -> None:
    """Read every cached symbol, keep the most liquid, and write the gzip.

    The liquidity cut happens here rather than in SQL so the committed artifact
    stays the size of the universe we actually rank, not of everything screened.
    """
    per_symbol: dict[str, list[dict]] = {}
    turnover: dict[str, float] = {}
    for row in universe_rows:
        sym = row["symbol"]
        path = _cache_path(sym)
        if not path.exists():
            continue
        with path.open() as f:
            got = list(csv.DictReader(f))
        # A name with less than a year of prints cannot support the 12-1
        # momentum or 1-year volatility the engine scores, so it is dropped
        # here rather than ranked on a partial record.
        if len(got) < 252:
            continue
        recent = got[-252:]
        vals = []
        for r in recent:
            try:
                vals.append(float(r["close"]) * float(r["volume"]))
            except (TypeError, ValueError):
                continue
        if not vals:
            continue
        per_symbol[sym] = got
        turnover[sym] = sum(vals) / len(vals)

    # One line per company. Among the tickers sharing a CIK, the one the market
    # actually trades wins -- see the `by_cik` comment in `build_universe`.
    # Without this, a company's fundamentals would be attached to a preferred
    # series or a structured note, and the same issuer could occupy several
    # slots on the board as if it were several opportunities.
    cik_of = {r["symbol"]: r.get("cik") for r in universe_rows}
    primary: dict[str, str] = {}
    for sym in sorted(turnover, key=lambda s: -turnover[s]):
        cik = cik_of.get(sym)
        if cik in (None, ""):
            primary[sym] = sym
            continue
        if cik not in primary:
            primary[cik] = sym
    chosen = {s for k, s in primary.items()}

    dropped = [s for s in turnover if s not in chosen]
    if dropped:
        print(f"  dropped {len(dropped)} secondary lines "
              f"(kept the most-traded ticker per company)")

    ordered = sorted(chosen, key=lambda s: -turnover[s])
    keep = ordered[:TARGET_UNIVERSE]
    forced = [s for s in universe.EQUITIES if s in chosen and s not in keep]
    keep = sorted(set(keep) | set(forced))

    all_rows: list[dict] = []
    for sym in keep:
        all_rows.extend(per_symbol[sym])
    all_rows.sort(key=lambda r: (r["symbol"], r["date"]))

    with gzip.open(PRICES_GZ, "wt", newline="", compresslevel=9) as f:
        w = csv.DictWriter(f, fieldnames=PRICE_FIELDS)
        w.writeheader()
        w.writerows(all_rows)

    kept = {r["symbol"]: r for r in universe_rows if r["symbol"] in set(keep)}
    _write_universe(list(kept.values()))
    print(f"Wrote {len(all_rows):,} price rows for {len(keep)} symbols -> {PRICES_GZ}")
    print(f"Universe trimmed to the {len(kept)} most liquid -> {UNIVERSE_CSV}")


# ---------------------------------------------------------------------------
# Stage 3 — fundamentals
# ---------------------------------------------------------------------------
def fetch_facts(force: bool = False) -> None:
    rows = read_universe()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    facts_cache = CACHE_DIR / "facts"
    facts_cache.mkdir(exist_ok=True)

    out: list[dict] = []
    empty = 0
    for i, row in enumerate(rows, 1):
        symbol, cik = row["symbol"], int(row["cik"])
        cached = facts_cache / f"{cik}.json"
        if cached.exists() and not force:
            try:
                extracted = json.loads(cached.read_text())
            except json.JSONDecodeError:
                extracted = None
        else:
            payload = sec.company_facts(cik)
            extracted = sec.extract(payload) if payload else None
            if extracted is not None:
                cached.write_text(json.dumps(extracted))
            time.sleep(sec.GAP)

        if not extracted or extracted.get("asof") is None:
            # Usually a reorganized issuer: the ticker now points at a successor
            # CIK that has filed no XBRL yet (ExxonMobil Holdings is the live
            # example). Recorded as absent so coverage renormalization can
            # exclude it honestly instead of scoring it as cheap and unlevered.
            empty += 1
            continue

        extracted["symbol"] = symbol
        extracted["cik"] = cik
        out.append({k: extracted.get(k) for k in FUNDAMENTAL_FIELDS})
        if i % 50 == 0:
            print(f"  {i}/{len(rows)} facts")

    with gzip.open(FUNDAMENTALS_GZ, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FUNDAMENTAL_FIELDS)
        w.writeheader()
        w.writerows(out)
    print(f"Wrote fundamentals for {len(out)}/{len(rows)} symbols -> {FUNDAMENTALS_GZ}")
    if empty:
        print(f"  {empty} had no usable XBRL facts (successor CIK or foreign filer)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--universe", action="store_true", help="pick the candidate universe")
    ap.add_argument("--prices", action="store_true", help="fetch 5y daily prices")
    ap.add_argument("--facts", action="store_true", help="fetch SEC fundamentals")
    ap.add_argument("--all", action="store_true", help="all three stages in order")
    ap.add_argument("--force", action="store_true", help="ignore caches")
    args = ap.parse_args()

    if not any((args.universe, args.prices, args.facts, args.all)):
        ap.error("choose at least one stage (--universe / --prices / --facts / --all)")

    if args.universe or args.all:
        build_universe()
    if args.prices or args.all:
        fetch_prices(force=args.force)
    if args.facts or args.all:
        fetch_facts(force=args.force)


if __name__ == "__main__":
    main()

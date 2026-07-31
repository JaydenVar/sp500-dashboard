"""Load the price/symbol CSVs into SQLite and derive every analysis table in SQL.

The analysis lives in views, not in Python. Views are partitioned by symbol so
the same SQL serves the index and every equity.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
import os
import sqlite3
from pathlib import Path

import events
from db import get_connection

DATA_DIR = Path(__file__).parent / "data"
PRICES_GZ = DATA_DIR / "prices.csv.gz"
PRICES_CSV = DATA_DIR / "prices.csv"  # accepted if present, but not what ships
SYMBOLS_CSV = DATA_DIR / "symbols.csv"
COMPANY_EVENTS_JSON = DATA_DIR / "company_events.json"
DB_PATH = DATA_DIR / "sp500.db"

# Bump when the schema changes. A deployed container keeps its disk between
# restarts, so a database built by an OLDER version of this file survives and
# would otherwise be reused forever -- which is exactly how a single-symbol
# database (no `symbols` table) kept breaking the multi-symbol app. The stamp
# plus the object check below make a stale database rebuild itself.
SCHEMA_VERSION = 3

REQUIRED_OBJECTS = frozenset({
    "prices", "symbols", "symbol_stats", "latest_quote",
    "daily_returns", "drawdowns", "moving_averages", "rolling_volatility",
    "yearly_summary", "monthly_returns",
    "company_events", "event_categories", "market_events",
})


def _open_prices():
    """Prefer the committed gzip; fall back to a plain CSV if one is lying around."""
    if PRICES_GZ.exists():
        return gzip.open(PRICES_GZ, "rt", newline="")
    if PRICES_CSV.exists():
        return PRICES_CSV.open(newline="")
    raise FileNotFoundError(f"Missing {PRICES_GZ} (or {PRICES_CSV}). Run fetch_data.py first.")

SCHEMA_SQL = """
DROP TABLE IF EXISTS prices;
CREATE TABLE prices (
    symbol    TEXT NOT NULL,
    date      TEXT NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    adj_close REAL,
    volume    INTEGER,
    PRIMARY KEY (symbol, date)
);

DROP TABLE IF EXISTS symbols;
CREATE TABLE symbols (
    symbol   TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    sector   TEXT,
    industry TEXT,
    is_index INTEGER NOT NULL DEFAULT 0
);

-- Curated company history, loaded from data/company_events.json. Kept as a
-- table rather than a Python literal so adding events is a data edit: the
-- Journey timeline reads it through SQL like every other figure in the app.
DROP TABLE IF EXISTS company_events;
CREATE TABLE company_events (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL,
    category    TEXT NOT NULL,
    source      TEXT NOT NULL
);

-- The category registry from the same file. A new category is a row here, not
-- a branch in the UI: `tone` is what the palette maps to a color.
DROP TABLE IF EXISTS event_categories;
CREATE TABLE event_categories (
    name  TEXT PRIMARY KEY,
    tone  TEXT NOT NULL,
    label TEXT NOT NULL
);

-- Market-wide sessions, loaded from events.py so that module stays the single
-- source for them (the existing chart overlays still import it directly). In
-- SQL they can be joined against prices, which is how the Journey decides
-- whether a market event actually moved the company being viewed.
DROP TABLE IF EXISTS market_events;
CREATE TABLE market_events (
    date        TEXT NOT NULL PRIMARY KEY,
    title       TEXT NOT NULL,
    category    TEXT NOT NULL,
    description TEXT NOT NULL
);
"""

INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_prices_symbol_date ON prices(symbol, date);
CREATE INDEX IF NOT EXISTS idx_prices_date        ON prices(date);
CREATE INDEX IF NOT EXISTS idx_company_events     ON company_events(symbol, date);
"""

VIEWS_SQL = """
-- Per-symbol daily return.
DROP VIEW IF EXISTS daily_returns;
CREATE VIEW daily_returns AS
SELECT
    symbol,
    date,
    close,
    volume,
    (close - LAG(close) OVER w) / LAG(close) OVER w AS daily_return
FROM prices
WINDOW w AS (PARTITION BY symbol ORDER BY date);

-- Running all-time high per symbol, and distance below it.
DROP VIEW IF EXISTS drawdowns;
CREATE VIEW drawdowns AS
WITH peaks AS (
    SELECT
        symbol, date, close,
        MAX(close) OVER (PARTITION BY symbol ORDER BY date
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak_close
    FROM prices
)
SELECT symbol, date, close, peak_close,
       (close - peak_close) / peak_close AS drawdown
FROM peaks;

-- Moving averages and 21-session annualized volatility.
DROP VIEW IF EXISTS moving_averages;
CREATE VIEW moving_averages AS
SELECT
    symbol, date, close,
    CASE WHEN COUNT(*)  OVER w50  = 50  THEN AVG(close) OVER w50  END AS ma_50,
    CASE WHEN COUNT(*)  OVER w200 = 200 THEN AVG(close) OVER w200 END AS ma_200
FROM prices
WINDOW
    w50  AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN 49  PRECEDING AND CURRENT ROW),
    w200 AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW);

DROP VIEW IF EXISTS rolling_volatility;
CREATE VIEW rolling_volatility AS
WITH r AS (
    SELECT symbol, date, daily_return
    FROM daily_returns
    WHERE daily_return IS NOT NULL
),
stats AS (
    SELECT
        symbol, date,
        AVG(daily_return) OVER w              AS avg_ret,
        AVG(daily_return * daily_return) OVER w AS avg_sq,
        COUNT(*) OVER w                       AS n
    FROM r
    WINDOW w AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN 20 PRECEDING AND CURRENT ROW)
)
SELECT symbol, date,
       CASE WHEN n = 21
            THEN SQRT(MAX(avg_sq - avg_ret * avg_ret, 0)) * SQRT(252)
       END AS ann_volatility_21d
FROM stats;

-- Calendar-year summary per symbol, with a partial-year flag: a US equity year
-- runs ~250 sessions, so fewer means the window only covers part of it and the
-- return is not a calendar-year figure.
DROP VIEW IF EXISTS yearly_summary;
CREATE VIEW yearly_summary AS
WITH bounds AS (
    SELECT symbol, substr(date, 1, 4) AS year,
           MIN(date) AS first_date, MAX(date) AS last_date, COUNT(*) AS trading_days
    FROM prices
    GROUP BY symbol, substr(date, 1, 4)
),
agg AS (
    SELECT symbol, substr(date, 1, 4) AS year,
           MAX(close) AS year_high, MIN(close) AS year_low, AVG(volume) AS avg_volume
    FROM prices
    GROUP BY symbol, substr(date, 1, 4)
)
SELECT
    b.symbol, b.year,
    fo.close AS open_close,
    lc.close AS close_close,
    a.year_high, a.year_low, a.avg_volume, b.trading_days,
    (lc.close - fo.close) / fo.close AS year_return,
    CASE WHEN b.trading_days < 240 THEN 1 ELSE 0 END AS is_partial
FROM bounds b
JOIN agg a  ON a.symbol = b.symbol AND a.year = b.year
JOIN prices fo ON fo.symbol = b.symbol AND fo.date = b.first_date
JOIN prices lc ON lc.symbol = b.symbol AND lc.date = b.last_date;

DROP VIEW IF EXISTS monthly_returns;
CREATE VIEW monthly_returns AS
WITH bounds AS (
    SELECT symbol, substr(date, 1, 7) AS year_month,
           MIN(date) AS first_date, MAX(date) AS last_date
    FROM prices
    GROUP BY symbol, substr(date, 1, 7)
)
SELECT
    b.symbol, b.year_month,
    substr(b.year_month, 1, 4) AS year,
    substr(b.year_month, 6, 2) AS month,
    (lc.close - fo.close) / fo.close AS month_return
FROM bounds b
JOIN prices fo ON fo.symbol = b.symbol AND fo.date = b.first_date
JOIN prices lc ON lc.symbol = b.symbol AND lc.date = b.last_date;

-- One row per symbol: the leaderboard/screener source. Period figures are over
-- each symbol's OWN available history, so `first_date`/`years` are exposed --
-- a 2012-listed name has not had the same run as a 2001-listed one.
--
-- Defined as a view for readability, then MATERIALIZED into a table below: it
-- rolls up every window function over all ~300k rows, which measured ~2.1s as a
-- live view versus ~1ms as a table. The inputs only change when the DB is
-- rebuilt, so there is nothing to invalidate.
DROP VIEW IF EXISTS symbol_stats_v;
CREATE VIEW symbol_stats_v AS
WITH bounds AS (
    SELECT symbol, MIN(date) AS first_date, MAX(date) AS last_date, COUNT(*) AS trading_days
    FROM prices GROUP BY symbol
),
ret AS (
    SELECT symbol,
           AVG(daily_return) AS mean_ret,
           AVG(daily_return * daily_return) AS mean_sq
    FROM daily_returns WHERE daily_return IS NOT NULL GROUP BY symbol
),
extremes AS (
    SELECT symbol, MAX(close) AS highest_close, MIN(close) AS lowest_close,
           AVG(volume) AS avg_volume, AVG(close * volume) AS avg_dollar_volume
    FROM prices GROUP BY symbol
),
worst AS (
    SELECT symbol, MIN(drawdown) AS max_drawdown FROM drawdowns GROUP BY symbol
),
latest_vol AS (
    SELECT symbol, ann_volatility_21d FROM (
        SELECT symbol, ann_volatility_21d,
               ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
        FROM rolling_volatility WHERE ann_volatility_21d IS NOT NULL
    ) WHERE rn = 1
),
prev AS (
    SELECT symbol, daily_return FROM (
        SELECT symbol, daily_return,
               ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
        FROM daily_returns WHERE daily_return IS NOT NULL
    ) WHERE rn = 1
)
SELECT
    s.symbol, s.name, s.sector, s.industry, s.is_index,
    b.first_date, b.last_date, b.trading_days,
    fo.close AS first_close,
    lc.close AS last_close,
    (lc.close - fo.close) / fo.close AS total_return,
    (julianday(b.last_date) - julianday(b.first_date)) / 365.25 AS years,
    POWER(lc.close / fo.close,
          1.0 / MAX((julianday(b.last_date) - julianday(b.first_date)) / 365.25, 0.01)) - 1 AS cagr,
    SQRT(MAX(r.mean_sq - r.mean_ret * r.mean_ret, 0)) * SQRT(252) AS ann_volatility,
    w.max_drawdown,
    lv.ann_volatility_21d AS current_volatility,
    p.daily_return AS last_daily_return,
    e.highest_close, e.lowest_close, e.avg_volume, e.avg_dollar_volume
FROM symbols s
JOIN bounds b  ON b.symbol = s.symbol
JOIN prices fo ON fo.symbol = s.symbol AND fo.date = b.first_date
JOIN prices lc ON lc.symbol = s.symbol AND lc.date = b.last_date
JOIN ret r     ON r.symbol = s.symbol
JOIN extremes e ON e.symbol = s.symbol
LEFT JOIN worst w      ON w.symbol = s.symbol
LEFT JOIN latest_vol lv ON lv.symbol = s.symbol
LEFT JOIN prev p        ON p.symbol = s.symbol;
"""

# Materialize the expensive rollup. Run after VIEWS_SQL.
MATERIALIZE_SQL = """
DROP TABLE IF EXISTS symbol_stats;
CREATE TABLE symbol_stats AS SELECT * FROM symbol_stats_v;
CREATE UNIQUE INDEX IF NOT EXISTS idx_symbol_stats ON symbol_stats(symbol);

-- Latest session per symbol plus its prior close, for the quote strip. Reading
-- this instead of joining the daily_returns view took that lookup 286ms -> ~1ms.
DROP TABLE IF EXISTS latest_quote;
CREATE TABLE latest_quote AS
WITH ranked AS (
    SELECT symbol, date, open, high, low, close, volume,
           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn,
           LAG(close) OVER (PARTITION BY symbol ORDER BY date DESC) AS next_close
    FROM prices
),
w52 AS (
    SELECT p.symbol, MAX(p.high) AS w52_high, MIN(p.low) AS w52_low
    FROM prices p
    JOIN (SELECT symbol, MAX(date) AS d FROM prices GROUP BY symbol) l
      ON l.symbol = p.symbol
    WHERE p.date >= date(l.d, '-1 year')
    GROUP BY p.symbol
),
prevclose AS (
    SELECT symbol, close AS prev_close FROM ranked WHERE rn = 2
)
SELECT
    r.symbol, r.date, r.open, r.high, r.low, r.close, r.volume,
    pc.prev_close,
    CASE WHEN pc.prev_close IS NOT NULL AND pc.prev_close <> 0
         THEN (r.close - pc.prev_close) / pc.prev_close END AS daily_return,
    w.w52_high, w.w52_low
FROM ranked r
LEFT JOIN prevclose pc ON pc.symbol = r.symbol
LEFT JOIN w52 w        ON w.symbol = r.symbol
WHERE r.rn = 1;
CREATE UNIQUE INDEX IF NOT EXISTS idx_latest_quote ON latest_quote(symbol);
"""


def _load_prices(conn: sqlite3.Connection) -> int:
    with _open_prices() as f:
        rows = [
            (r["symbol"], r["date"], r["open"], r["high"], r["low"],
             r["close"], r["adj_close"], r["volume"])
            for r in csv.DictReader(f)
        ]
    conn.executemany(
        "INSERT OR REPLACE INTO prices"
        " (symbol, date, open, high, low, close, adj_close, volume)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def _load_symbols(conn: sqlite3.Connection) -> int:
    with SYMBOLS_CSV.open() as f:
        reader = csv.DictReader(f)
        rows = [
            (r["symbol"], r["name"], r["sector"], r["industry"], int(r["is_index"]))
            for r in reader
        ]
    conn.executemany(
        "INSERT OR REPLACE INTO symbols (symbol, name, sector, industry, is_index)"
        " VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


EVENT_FIELDS = ("symbol", "date", "title", "description", "category", "source")


def _validate_event(row: dict, i: int, known_symbols: set[str],
                    known_categories: set[str]) -> tuple:
    """Check one curated event and return it as an insertable tuple.

    Every failure raises rather than skipping the row. A dropped event is
    invisible in the UI -- it looks exactly like a company that has no history
    curated yet -- so a typo would otherwise sit in the file indefinitely.
    """
    where = f"company_events.json event #{i}"
    for field in EVENT_FIELDS:
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{where}: '{field}' must be a non-empty string")

    if row["symbol"] not in known_symbols:
        raise ValueError(f"{where}: unknown symbol {row['symbol']!r}")
    if row["category"] not in known_categories:
        raise ValueError(f"{where}: unknown category {row['category']!r}")
    try:
        dt.date.fromisoformat(row["date"])
    except ValueError as exc:
        raise ValueError(f"{where}: date must be ISO YYYY-MM-DD, got {row['date']!r}") from exc

    return tuple(row[f].strip() for f in EVENT_FIELDS)


def _load_company_events(conn: sqlite3.Connection) -> tuple[int, int]:
    """Load the curated events and their category registry.

    Symbols are validated against the `symbols` table, so this must run after
    `_load_symbols`.
    """
    if not COMPANY_EVENTS_JSON.exists():
        raise FileNotFoundError(f"Missing {COMPANY_EVENTS_JSON}.")

    with COMPANY_EVENTS_JSON.open() as f:
        payload = json.load(f)

    categories = payload.get("categories") or []
    if not categories:
        raise ValueError("company_events.json: 'categories' is empty")
    cat_rows = [(c["name"], c["tone"], c["label"]) for c in categories]
    conn.executemany(
        "INSERT OR REPLACE INTO event_categories (name, tone, label) VALUES (?, ?, ?)",
        cat_rows,
    )

    known_symbols = {r[0] for r in conn.execute("SELECT symbol FROM symbols")}
    known_categories = {c[0] for c in cat_rows}
    rows = [
        _validate_event(row, i, known_symbols, known_categories)
        for i, row in enumerate(payload.get("events") or [], start=1)
    ]
    conn.executemany(
        "INSERT INTO company_events"
        " (symbol, date, title, description, category, source)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows), len(cat_rows)


def _load_market_events(conn: sqlite3.Connection) -> int:
    """Mirror `events.EVENTS` into SQL. That module stays the source of truth."""
    rows = [(date, short, cat, note) for date, short, cat, note in events.EVENTS]
    conn.executemany(
        "INSERT OR REPLACE INTO market_events (date, title, category, description)"
        " VALUES (?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def db_is_current(path: Path = DB_PATH) -> bool:
    """True only if `path` is a database this version of the code built.

    Existence alone is not enough: a deploy container reuses its disk, so a
    database from an older schema (or a build that died halfway) can be sitting
    there. Checking the stamp AND the object list means either case rebuilds
    instead of failing at query time with `no such table`.
    """
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if not row or row[0] != str(SCHEMA_VERSION):
            return False
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        return REQUIRED_OBJECTS.issubset(names)
    except sqlite3.Error:
        return False  # missing meta table, corrupt file, etc.
    finally:
        conn.close()


def main() -> None:
    if not SYMBOLS_CSV.exists():
        raise FileNotFoundError(f"Missing {SYMBOLS_CSV}. Run fetch_data.py first.")

    # Build to a temp file and swap it in, so a crash mid-build leaves the old
    # database in place rather than a half-populated one that looks valid.
    tmp_path = DB_PATH.with_suffix(".db.building")
    if tmp_path.exists():
        tmp_path.unlink()

    conn = get_connection(tmp_path)
    try:
        conn.executescript(SCHEMA_SQL)
        n_prices = _load_prices(conn)
        n_symbols = _load_symbols(conn)
        n_events, n_cats = _load_company_events(conn)  # validates against `symbols`
        n_market = _load_market_events(conn)
        conn.executescript(INDEXES_SQL)
        conn.executescript(VIEWS_SQL)
        conn.executescript(MATERIALIZE_SQL)
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.execute("ANALYZE")
        conn.commit()
    except BaseException:
        conn.close()
        tmp_path.unlink(missing_ok=True)
        raise
    else:
        conn.close()

    os.replace(tmp_path, DB_PATH)  # atomic on the same filesystem
    print(f"Loaded {n_prices:,} price rows across {n_symbols} symbols -> {DB_PATH}")
    print(f"       {n_events} company events in {n_cats} categories, {n_market} market events")


if __name__ == "__main__":
    main()

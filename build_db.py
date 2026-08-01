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

# Market Intelligence artifacts, written by fetch_intel.py. Deliberately loaded
# into their OWN tables rather than into `prices`/`symbols`: those feed the
# leaderboard, sector medians and period movers on pages that must not change,
# and 500 five-year names would silently rewrite every one of those figures.
INTEL_UNIVERSE_CSV = DATA_DIR / "intel_universe.csv"
INTEL_PRICES_GZ = DATA_DIR / "intel_prices.csv.gz"
INTEL_FUNDAMENTALS_GZ = DATA_DIR / "intel_fundamentals.csv.gz"
INTEL_ANALYST_CSV = DATA_DIR / "intel_analyst.csv"  # optional; provider-key only

# Bump when the schema changes. A deployed container keeps its disk between
# restarts, so a database built by an OLDER version of this file survives and
# would otherwise be reused forever -- which is exactly how a single-symbol
# database (no `symbols` table) kept breaking the multi-symbol app. The stamp
# plus the object check below make a stale database rebuild itself.
#
# v3 -> v4: the Market Intelligence tables (intel_*) and the metric_panel view
# the ranking engine scores over.
SCHEMA_VERSION = 4

REQUIRED_OBJECTS = frozenset({
    "prices", "symbols", "symbol_stats", "latest_quote",
    "daily_returns", "drawdowns", "moving_averages", "rolling_volatility",
    "yearly_summary", "monthly_returns",
    "company_events", "event_categories", "market_events",
    "intel_symbols", "intel_prices", "intel_fundamentals", "intel_analyst",
    "metric_panel",
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


# ---------------------------------------------------------------------------
# Market Intelligence
# ---------------------------------------------------------------------------
INTEL_SCHEMA_SQL = """
DROP TABLE IF EXISTS intel_symbols;
CREATE TABLE intel_symbols (
    symbol   TEXT PRIMARY KEY,
    cik      INTEGER,
    name     TEXT NOT NULL,
    sector   TEXT,
    exchange TEXT
);

DROP TABLE IF EXISTS intel_prices;
CREATE TABLE intel_prices (
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

-- One row per company, straight from SEC XBRL. Raw reported figures only: every
-- ratio is derived in the view below, so a change to how (say) free cash flow is
-- defined is a change to one SQL expression rather than a re-fetch.
DROP TABLE IF EXISTS intel_fundamentals;
CREATE TABLE intel_fundamentals (
    symbol              TEXT PRIMARY KEY,
    cik                 INTEGER,
    asof                TEXT,
    revenue             REAL, revenue_prior          REAL,
    net_income          REAL, net_income_prior       REAL,
    gross_profit        REAL, gross_profit_prior     REAL,
    operating_income    REAL, operating_income_prior REAL,
    interest_expense    REAL, interest_expense_prior REAL,
    ocf                 REAL, ocf_prior              REAL,
    capex               REAL, capex_prior            REAL,
    assets              REAL,
    liabilities         REAL,
    equity              REAL,
    assets_current      REAL,
    liabilities_current REAL,
    long_term_debt      REAL,
    short_term_debt     REAL,
    shares_outstanding  REAL,
    earnings_stability  REAL
);

-- Analyst consensus. Always created, usually EMPTY: no free keyless provider
-- serves it (Yahoo's endpoints answer 401), so it is populated only when an
-- optional provider key is configured. The table exists unconditionally so the
-- metric_panel view has something to LEFT JOIN -- a view that only compiles
-- when a secret is set would fail the build on the public deploy.
DROP TABLE IF EXISTS intel_analyst;
CREATE TABLE intel_analyst (
    symbol        TEXT PRIMARY KEY,
    analyst_score REAL,
    target_upside REAL,
    eps_revision  REAL,
    n_analysts    INTEGER,
    fetched       TEXT
);
"""

INTEL_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_intel_prices_sym_date ON intel_prices(symbol, date);
CREATE INDEX IF NOT EXISTS idx_intel_prices_date     ON intel_prices(date);
"""

INTEL_VIEWS_SQL = """
-- Sessions numbered backwards from the latest, so "22 sessions ago" is a join
-- key rather than a date offset. Markets do not trade every calendar day, so a
-- 30-day date offset lands on a weekend for a large share of symbols and
-- silently drops them -- the same trap `ROLLING_RETURNS` documents.
DROP VIEW IF EXISTS intel_seq;
CREATE VIEW intel_seq AS
SELECT symbol, date, close, high, low, volume,
       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
FROM intel_prices;

DROP VIEW IF EXISTS intel_returns;
CREATE VIEW intel_returns AS
SELECT symbol, date, r,
       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
FROM (
    SELECT symbol, date,
           (close - LAG(close) OVER w) / LAG(close) OVER w AS r
    FROM intel_prices
    WINDOW w AS (PARTITION BY symbol ORDER BY date)
)
WHERE r IS NOT NULL;

-- Every price-derived metric the engine scores, one row per symbol.
DROP VIEW IF EXISTS intel_price_metrics;
CREATE VIEW intel_price_metrics AS
WITH bounds AS (
    SELECT symbol, MIN(date) AS first_date, MAX(date) AS last_date, COUNT(*) AS sessions
    FROM intel_prices GROUP BY symbol
),
ends AS (
    SELECT b.symbol, b.first_date, b.last_date, b.sessions,
           f.close AS first_close, l.close AS last_close
    FROM bounds b
    JOIN intel_prices f ON f.symbol = b.symbol AND f.date = b.first_date
    JOIN intel_prices l ON l.symbol = b.symbol AND l.date = b.last_date
),
anchors AS (
    SELECT symbol,
           MAX(CASE WHEN rn = 22  THEN close END) AS c_1m,
           MAX(CASE WHEN rn = 64  THEN close END) AS c_3m,
           MAX(CASE WHEN rn = 253 THEN close END) AS c_12m
    FROM intel_seq WHERE rn IN (22, 64, 253) GROUP BY symbol
),
w252 AS (
    SELECT symbol, AVG(close * volume) AS dollar_volume,
           MAX(high) AS high_252, AVG(volume) AS vol_252
    FROM intel_seq WHERE rn <= 252 GROUP BY symbol
),
w21 AS (
    SELECT symbol, AVG(volume) AS vol_21 FROM intel_seq WHERE rn <= 21 GROUP BY symbol
),
w50 AS (
    SELECT symbol, AVG(close) AS ma_50 FROM intel_seq WHERE rn <= 50 GROUP BY symbol
),
w200 AS (
    SELECT symbol, AVG(close) AS ma_200 FROM intel_seq WHERE rn <= 200 GROUP BY symbol
),
rstats AS (
    SELECT symbol, AVG(r) AS mean_r, AVG(r * r) AS mean_sq, COUNT(*) AS n
    FROM intel_returns WHERE rn <= 252 GROUP BY symbol
),
downside AS (
    SELECT symbol, AVG(r * r) AS mean_sq_neg
    FROM intel_returns WHERE rn <= 252 AND r < 0 GROUP BY symbol
),
rsi AS (
    SELECT symbol,
           AVG(CASE WHEN r > 0 THEN r  ELSE 0 END) AS avg_gain,
           AVG(CASE WHEN r < 0 THEN -r ELSE 0 END) AS avg_loss
    FROM intel_returns WHERE rn <= 14 GROUP BY symbol
),
dd AS (
    SELECT symbol, MIN((close - peak) / peak) AS max_dd_1y
    FROM (
        SELECT symbol, close,
               MAX(close) OVER (PARTITION BY symbol ORDER BY date
                                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak
        FROM (SELECT symbol, date, close FROM intel_seq WHERE rn <= 252)
    )
    GROUP BY symbol
),
-- Beta against the index. `^GSPC` lives in the ORIGINAL prices table, which this
-- only reads -- the intel universe carries no index row of its own.
idx AS (
    SELECT date, (close - LAG(close) OVER (ORDER BY date))
                 / LAG(close) OVER (ORDER BY date) AS ir
    FROM prices WHERE symbol = '^GSPC'
),
beta AS (
    SELECT s.symbol,
           (AVG(s.r * i.ir) - AVG(s.r) * AVG(i.ir))
           / NULLIF(AVG(i.ir * i.ir) - AVG(i.ir) * AVG(i.ir), 0) AS beta_1y
    FROM intel_returns s
    JOIN idx i ON i.date = s.date AND i.ir IS NOT NULL
    WHERE s.rn <= 252
    GROUP BY s.symbol
)
SELECT
    e.symbol, e.first_date, e.last_date, e.sessions, e.last_close,
    (e.last_close - a.c_1m)  / a.c_1m  AS ret_1m,
    (e.last_close - a.c_3m)  / a.c_3m  AS ret_3m,
    -- 12-1 momentum: t-252 to t-21, SKIPPING the most recent month. The skip is
    -- the construction, not an approximation -- see ranking.MOMENTUM_NOTE.
    (a.c_1m - a.c_12m) / a.c_12m       AS mom_12_1,
    CASE WHEN e.sessions >= 1200
         THEN (e.last_close - e.first_close) / e.first_close END AS ret_5y,
    e.last_close / NULLIF(w50.ma_50, 0)   - 1 AS ma50_pos,
    e.last_close / NULLIF(w200.ma_200, 0) - 1 AS ma200_pos,
    e.last_close / NULLIF(w252.high_252, 0) - 1 AS pct_52w_high,
    CASE WHEN rsi.avg_loss = 0 AND rsi.avg_gain > 0 THEN 100.0
         WHEN rsi.avg_loss = 0 THEN 50.0
         ELSE 100.0 - (100.0 / (1.0 + rsi.avg_gain / rsi.avg_loss)) END AS rsi_14,
    w21.vol_21 / NULLIF(w252.vol_252, 0) AS volume_trend,
    CASE WHEN rstats.n >= 200
         THEN SQRT(MAX(rstats.mean_sq - rstats.mean_r * rstats.mean_r, 0)) * SQRT(252)
    END AS vol_1y,
    dd.max_dd_1y,
    beta.beta_1y,
    SQRT(COALESCE(downside.mean_sq_neg, 0)) * SQRT(252) AS downside_dev,
    w252.dollar_volume
FROM ends e
LEFT JOIN anchors  a    ON a.symbol = e.symbol
LEFT JOIN w252          ON w252.symbol = e.symbol
LEFT JOIN w21           ON w21.symbol = e.symbol
LEFT JOIN w50           ON w50.symbol = e.symbol
LEFT JOIN w200          ON w200.symbol = e.symbol
LEFT JOIN rstats        ON rstats.symbol = e.symbol
LEFT JOIN downside      ON downside.symbol = e.symbol
LEFT JOIN rsi           ON rsi.symbol = e.symbol
LEFT JOIN dd            ON dd.symbol = e.symbol
LEFT JOIN beta          ON beta.symbol = e.symbol;

-- Fundamental ratios, derived from the raw SEC figures.
--
-- Every ratio whose sign flips its meaning is NULLed rather than computed. A
-- negative P/E is not a cheap stock and a negative book value is not a bargain,
-- but both rank at the top of a "lower is better" sort -- which would put the
-- most distressed companies in the universe at the head of the value board.
-- NULL means "no valuation signal", which the coverage renormalization handles.
DROP VIEW IF EXISTS intel_fundamental_metrics;
CREATE VIEW intel_fundamental_metrics AS
WITH base AS (
    SELECT f.*, p.last_close,
           f.shares_outstanding * p.last_close AS market_cap,
           f.ocf - COALESCE(f.capex, 0)             AS fcf,
           f.ocf_prior - COALESCE(f.capex_prior, 0) AS fcf_prior
    FROM intel_fundamentals f
    JOIN intel_price_metrics p ON p.symbol = f.symbol
)
SELECT
    symbol, asof, market_cap, shares_outstanding, earnings_stability,
    revenue, net_income, assets, equity,
    CASE WHEN net_income > 0 THEN market_cap / net_income END AS pe,
    CASE WHEN revenue    > 0 THEN market_cap / revenue    END AS ps,
    CASE WHEN equity     > 0 THEN market_cap / equity     END AS pb,
    CASE WHEN market_cap > 0 THEN fcf / market_cap        END AS fcf_yield,
    CASE WHEN equity     > 0
         THEN (COALESCE(long_term_debt, 0) + COALESCE(short_term_debt, 0)) / equity END AS debt_to_equity,
    CASE WHEN liabilities_current > 0 THEN assets_current / liabilities_current END AS current_ratio,
    CASE WHEN interest_expense    > 0 THEN operating_income / interest_expense  END AS interest_coverage,
    CASE WHEN equity  > 0 THEN net_income   / equity  END AS roe,
    CASE WHEN assets  > 0 THEN net_income   / assets  END AS roa,
    CASE WHEN revenue > 0 THEN gross_profit / revenue END AS gross_margin,
    CASE WHEN revenue > 0 THEN net_income   / revenue END AS net_margin,
    CASE WHEN assets  > 0 THEN gross_profit / assets  END AS gross_profitability,
    CASE WHEN revenue_prior > 0 THEN revenue / revenue_prior - 1 END AS revenue_growth,
    -- Growth off a negative base is arithmetically defined and financially
    -- meaningless: a swing from -100 to -10 computes as -90% growth while the
    -- business improved. Only a positive prior period yields a growth figure.
    CASE WHEN net_income_prior > 0 THEN net_income / net_income_prior - 1 END AS earnings_growth,
    CASE WHEN fcf_prior        > 0 THEN fcf / fcf_prior - 1 END AS fcf_growth
FROM base;

-- The single table the ranking engine scores over. One row per symbol, one
-- column per registered metric in `ranking.METRICS`, plus the columns the UI
-- filters on. Adding a metric = a column here + a registry entry, nothing else.
DROP VIEW IF EXISTS metric_panel_v;
CREATE VIEW metric_panel_v AS
SELECT
    s.symbol, s.name, s.sector, s.exchange, 0 AS is_index,
    p.last_date, p.last_close, p.sessions,
    p.ret_1m, p.ret_3m, p.mom_12_1, p.ret_5y,
    p.ma50_pos, p.ma200_pos, p.pct_52w_high, p.rsi_14, p.volume_trend,
    p.vol_1y, p.max_dd_1y, p.beta_1y, p.downside_dev, p.dollar_volume,
    f.market_cap, f.asof AS fundamentals_asof,
    f.pe, f.ps, f.pb, f.fcf_yield,
    f.debt_to_equity, f.current_ratio, f.interest_coverage, f.earnings_stability,
    f.roe, f.roa, f.gross_margin, f.net_margin, f.gross_profitability,
    f.revenue_growth, f.earnings_growth, f.fcf_growth,
    f.revenue, f.net_income, f.assets, f.equity, f.shares_outstanding,
    a.analyst_score, a.target_upside, a.eps_revision, a.n_analysts
FROM intel_symbols s
JOIN intel_price_metrics p        ON p.symbol = s.symbol
LEFT JOIN intel_fundamental_metrics f ON f.symbol = s.symbol
LEFT JOIN intel_analyst a         ON a.symbol = s.symbol;
"""

INTEL_MATERIALIZE_SQL = """
-- Materialized for the same reason `symbol_stats` is: the view rolls a dozen
-- window aggregates over every session of every symbol, and the ranking board
-- re-reads it on every filter change. Inputs only move on a rebuild.
DROP TABLE IF EXISTS metric_panel;
CREATE TABLE metric_panel AS SELECT * FROM metric_panel_v;
CREATE UNIQUE INDEX IF NOT EXISTS idx_metric_panel ON metric_panel(symbol);
CREATE INDEX IF NOT EXISTS idx_metric_panel_sector ON metric_panel(sector);
"""


def _load_intel(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Load the Market Intelligence artifacts if they are present.

    All three are optional. A clone that has never run `fetch_intel.py` still
    builds a working database -- the intel tables are simply empty, the ranking
    board renders an empty state telling the reader to run the fetch, and every
    pre-existing section is unaffected. Making these required would mean a
    missing 20MB artifact breaks the whole app.
    """
    n_sym = n_px = n_fun = 0

    if INTEL_UNIVERSE_CSV.exists():
        with INTEL_UNIVERSE_CSV.open() as f:
            rows = [(r["symbol"], int(r["cik"]) if r.get("cik") else None,
                     r["name"], r.get("sector") or "Unclassified", r.get("exchange") or "")
                    for r in csv.DictReader(f)]
        conn.executemany(
            "INSERT OR REPLACE INTO intel_symbols (symbol, cik, name, sector, exchange)"
            " VALUES (?, ?, ?, ?, ?)", rows)
        n_sym = len(rows)

    if INTEL_PRICES_GZ.exists():
        with gzip.open(INTEL_PRICES_GZ, "rt", newline="") as f:
            rows = [(r["symbol"], r["date"], r["open"], r["high"], r["low"],
                     r["close"], r["adj_close"], r["volume"]) for r in csv.DictReader(f)]
        conn.executemany(
            "INSERT OR REPLACE INTO intel_prices"
            " (symbol, date, open, high, low, close, adj_close, volume)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        n_px = len(rows)

    if INTEL_FUNDAMENTALS_GZ.exists():
        cols = [c for c in _INTEL_FUNDAMENTAL_COLUMNS]
        placeholders = ", ".join("?" * len(cols))
        with gzip.open(INTEL_FUNDAMENTALS_GZ, "rt", newline="") as f:
            rows = []
            for r in csv.DictReader(f):
                rows.append(tuple(_num(r.get(c)) if c not in ("symbol", "asof") else r.get(c)
                                  for c in cols))
        conn.executemany(
            f"INSERT OR REPLACE INTO intel_fundamentals ({', '.join(cols)})"
            f" VALUES ({placeholders})", rows)
        n_fun = len(rows)

    if INTEL_ANALYST_CSV.exists():
        with INTEL_ANALYST_CSV.open() as f:
            rows = [(r["symbol"], _num(r.get("analyst_score")), _num(r.get("target_upside")),
                     _num(r.get("eps_revision")), _num(r.get("n_analysts")), r.get("fetched"))
                    for r in csv.DictReader(f)]
        conn.executemany(
            "INSERT OR REPLACE INTO intel_analyst"
            " (symbol, analyst_score, target_upside, eps_revision, n_analysts, fetched)"
            " VALUES (?, ?, ?, ?, ?, ?)", rows)

    return n_sym, n_px, n_fun


_INTEL_FUNDAMENTAL_COLUMNS = (
    "symbol", "cik", "asof",
    "revenue", "revenue_prior", "net_income", "net_income_prior",
    "gross_profit", "gross_profit_prior", "operating_income", "operating_income_prior",
    "interest_expense", "interest_expense_prior", "ocf", "ocf_prior",
    "capex", "capex_prior",
    "assets", "liabilities", "equity", "assets_current", "liabilities_current",
    "long_term_debt", "short_term_debt", "shares_outstanding", "earnings_stability",
)


def _num(value):
    """CSV cell -> float or None. Empty strings and 'None' both mean absent, and
    an absent fundamental must stay NULL rather than become 0.0 -- zero equity
    would compute an infinite ROE, zero debt would rank as pristine."""
    if value is None or value == "" or value == "None":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _data_version(conn: sqlite3.Connection) -> str:
    """A fingerprint of the market data the rankings were computed from.

    This is what makes rankings refresh automatically. Every cached ranking read
    in `data_access` takes this string as an argument, so when a new price or
    fundamental load changes it, every memoized board is a cache MISS and
    recomputes — without a TTL guess, and without anyone remembering to clear a
    cache after a data refresh. A TTL alone cannot do this: it either expires
    while the data is unchanged (recomputing for nothing) or holds a stale board
    after a refresh, and which one you get is a race with the clock.

    Deliberately derived from the data itself rather than from a build
    timestamp: rebuilding the database from identical CSVs produces the same
    version, so a redeploy that changed no data does not invalidate anything.
    """
    parts: list[str] = []
    for sql in (
        "SELECT MAX(date), COUNT(*) FROM intel_prices",
        "SELECT MAX(asof), COUNT(*) FROM intel_fundamentals",
        "SELECT COUNT(*) FROM intel_analyst",
        "SELECT MAX(date), COUNT(*) FROM prices",
    ):
        parts.extend(str(v) for v in conn.execute(sql).fetchone())
    return "|".join(parts)


def data_version(path: Path = DB_PATH) -> str:
    """Read the stored fingerprint. Empty string if unreadable — which makes the
    caller cache under a distinct key rather than sharing one with real data."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return ""
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'data_version'").fetchone()
        return row[0] if row else ""
    except sqlite3.Error:
        return ""
    finally:
        conn.close()


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
        conn.executescript(INTEL_SCHEMA_SQL)
        n_prices = _load_prices(conn)
        n_symbols = _load_symbols(conn)
        n_events, n_cats = _load_company_events(conn)  # validates against `symbols`
        n_market = _load_market_events(conn)
        n_isym, n_ipx, n_ifun = _load_intel(conn)
        conn.executescript(INDEXES_SQL)
        conn.executescript(INTEL_INDEXES_SQL)
        conn.executescript(VIEWS_SQL)
        conn.executescript(INTEL_VIEWS_SQL)   # reads `prices` for the index beta
        conn.executescript(MATERIALIZE_SQL)
        conn.executescript(INTEL_MATERIALIZE_SQL)
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.execute("INSERT INTO meta (key, value) VALUES ('data_version', ?)",
                     (_data_version(conn),))
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
    print(f"       intel: {n_isym} symbols, {n_ipx:,} price rows, {n_ifun} fundamental rows")


if __name__ == "__main__":
    main()

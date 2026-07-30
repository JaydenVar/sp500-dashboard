"""Load the S&P 500 CSV into SQLite and derive analysis tables entirely in SQL."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from db import get_connection

DATA_DIR = Path(__file__).parent / "data"
CSV_PATH = DATA_DIR / "sp500_daily.csv"
DB_PATH = DATA_DIR / "sp500.db"

SCHEMA_SQL = """
DROP TABLE IF EXISTS prices;
CREATE TABLE prices (
    date TEXT PRIMARY KEY,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    adj_close REAL,
    volume INTEGER
);
"""

# Everything below is plain SQL (views), computed once when the DB is built.
VIEWS_SQL = """
DROP VIEW IF EXISTS daily_returns;
CREATE VIEW daily_returns AS
SELECT
    date,
    close,
    volume,
    (close - LAG(close) OVER (ORDER BY date)) / LAG(close) OVER (ORDER BY date) AS daily_return
FROM prices;

DROP VIEW IF EXISTS running_peak;
CREATE VIEW running_peak AS
SELECT
    date,
    close,
    MAX(close) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak_close
FROM prices;

DROP VIEW IF EXISTS drawdowns;
CREATE VIEW drawdowns AS
SELECT
    date,
    close,
    peak_close,
    (close - peak_close) / peak_close AS drawdown
FROM running_peak;

DROP VIEW IF EXISTS yearly_summary;
CREATE VIEW yearly_summary AS
WITH bounds AS (
    SELECT
        substr(date, 1, 4) AS year,
        MIN(date) AS first_date,
        MAX(date) AS last_date
    FROM prices
    GROUP BY substr(date, 1, 4)
),
year_prices AS (
    SELECT
        b.year,
        (SELECT close FROM prices WHERE date = b.first_date) AS open_close,
        (SELECT close FROM prices WHERE date = b.last_date) AS close_close,
        (SELECT MAX(close) FROM prices WHERE substr(date,1,4) = b.year) AS year_high,
        (SELECT MIN(close) FROM prices WHERE substr(date,1,4) = b.year) AS year_low,
        (SELECT AVG(volume) FROM prices WHERE substr(date,1,4) = b.year) AS avg_volume,
        (SELECT COUNT(*) FROM prices WHERE substr(date,1,4) = b.year) AS trading_days
    FROM bounds b
)
SELECT
    year,
    open_close,
    close_close,
    year_high,
    year_low,
    avg_volume,
    trading_days,
    (close_close - open_close) / open_close AS year_return,
    -- A US equity year has ~250 sessions. Fewer means the dataset only covers
    -- part of it, so the return is not a calendar-year figure.
    CASE WHEN trading_days < 240 THEN 1 ELSE 0 END AS is_partial
FROM year_prices
ORDER BY year;

DROP VIEW IF EXISTS monthly_returns;
CREATE VIEW monthly_returns AS
WITH bounds AS (
    SELECT
        substr(date, 1, 7) AS year_month,
        MIN(date) AS first_date,
        MAX(date) AS last_date
    FROM prices
    GROUP BY substr(date, 1, 7)
)
SELECT
    b.year_month,
    substr(b.year_month, 1, 4) AS year,
    substr(b.year_month, 6, 2) AS month,
    (SELECT close FROM prices WHERE date = b.first_date) AS open_close,
    (SELECT close FROM prices WHERE date = b.last_date) AS close_close,
    ((SELECT close FROM prices WHERE date = b.last_date) - (SELECT close FROM prices WHERE date = b.first_date))
        / (SELECT close FROM prices WHERE date = b.first_date) AS month_return
FROM bounds b
ORDER BY b.year_month;

DROP VIEW IF EXISTS rolling_volatility;
CREATE VIEW rolling_volatility AS
WITH r AS (
    SELECT date, daily_return FROM daily_returns WHERE daily_return IS NOT NULL
),
stats AS (
    SELECT
        date,
        AVG(daily_return) OVER w AS avg_ret,
        AVG(daily_return * daily_return) OVER w AS avg_sq_ret
    FROM r
    WINDOW w AS (ORDER BY date ROWS BETWEEN 20 PRECEDING AND CURRENT ROW)
)
SELECT
    date,
    -- annualized volatility from a 21-day rolling window of daily returns
    SQRT(MAX(avg_sq_ret - avg_ret * avg_ret, 0)) * SQRT(252) AS ann_volatility_21d
FROM stats;
"""


def load_csv(conn: sqlite3.Connection) -> int:
    with CSV_PATH.open() as f:
        reader = csv.DictReader(f)
        rows = [
            (r["date"], r["open"], r["high"], r["low"], r["close"], r["adj_close"], r["volume"])
            for r in reader
        ]
    conn.executemany(
        "INSERT INTO prices (date, open, high, low, close, adj_close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def main() -> None:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Missing {CSV_PATH}. Run fetch_data.py first.")

    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = get_connection(DB_PATH)
    try:
        conn.executescript(SCHEMA_SQL)
        n = load_csv(conn)
        conn.executescript(VIEWS_SQL)
        conn.commit()
        print(f"Loaded {n} rows into {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

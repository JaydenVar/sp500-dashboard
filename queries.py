"""Every SQL statement the dashboard runs.

Each entry pairs the SQL with a plain-English explanation; the SQL Explorer page
renders both alongside the returned rows and the measured execution time. Keeping
them together is what makes the SQL layer inspectable rather than buried.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Reference / lookup
# --------------------------------------------------------------------------
DATE_BOUNDS = """
SELECT MIN(date) AS min_date, MAX(date) AS max_date
FROM prices
WHERE symbol = :symbol;
"""

SYMBOL_DIRECTORY = """
SELECT symbol, name, sector, industry, is_index
FROM symbols
ORDER BY is_index DESC, symbol;
"""

# --------------------------------------------------------------------------
# Time series (scoped by symbol + date range)
# --------------------------------------------------------------------------
PRICES_IN_RANGE = """
SELECT date, open, high, low, close, volume
FROM prices
WHERE symbol = :symbol
  AND date BETWEEN :start AND :end
ORDER BY date;
"""

MOVING_AVERAGES_IN_RANGE = """
SELECT date, close, ma_50, ma_200
FROM moving_averages
WHERE symbol = :symbol
  AND date BETWEEN :start AND :end
ORDER BY date;
"""

DAILY_RETURNS_IN_RANGE = """
SELECT date, daily_return
FROM daily_returns
WHERE symbol = :symbol
  AND date BETWEEN :start AND :end
  AND daily_return IS NOT NULL
ORDER BY date;
"""

DRAWDOWN_IN_RANGE = """
SELECT date, close, peak_close, drawdown
FROM drawdowns
WHERE symbol = :symbol
  AND date BETWEEN :start AND :end
ORDER BY date;
"""

VOLATILITY_IN_RANGE = """
SELECT date, ann_volatility_21d
FROM rolling_volatility
WHERE symbol = :symbol
  AND date BETWEEN :start AND :end
  AND ann_volatility_21d IS NOT NULL
ORDER BY date;
"""

# Cumulative growth of 1 unit invested at the start of the window, compounded
# from daily returns inside the window (so it always starts at 0%).
CUMULATIVE_RETURN_IN_RANGE = """
WITH r AS (
    SELECT date, daily_return
    FROM daily_returns
    WHERE symbol = :symbol
      AND date BETWEEN :start AND :end
      AND daily_return IS NOT NULL
)
SELECT
    date,
    EXP(SUM(LN(1 + daily_return)) OVER (ORDER BY date)) - 1 AS cumulative_return
FROM r
ORDER BY date;
"""

# Comparison mode: every series rebased to 100 at the window's first close, so
# names at different price levels sit on ONE axis (never a second y-scale).
INDEXED_COMPARISON = """
WITH base AS (
    SELECT p.symbol, p.close AS base_close
    FROM prices p
    JOIN (
        SELECT symbol, MIN(date) AS d
        FROM prices
        WHERE date BETWEEN :start AND :end
        GROUP BY symbol
    ) f ON f.symbol = p.symbol AND f.d = p.date
)
SELECT p.symbol, p.date, 100.0 * p.close / b.base_close AS indexed_close
FROM prices p
JOIN base b ON b.symbol = p.symbol
WHERE p.date BETWEEN :start AND :end
ORDER BY p.symbol, p.date;
"""

# --------------------------------------------------------------------------
# Aggregates
# --------------------------------------------------------------------------
YEARLY_SUMMARY = """
SELECT year, open_close, close_close, year_high, year_low,
       avg_volume, trading_days, year_return, is_partial
FROM yearly_summary
WHERE symbol = :symbol
ORDER BY year;
"""

MONTHLY_RETURNS = """
SELECT year, month, month_return
FROM monthly_returns
WHERE symbol = :symbol
ORDER BY year, month;
"""

# Headline figures for the selected symbol over the SELECTED WINDOW, so the KPI
# row always agrees with the charts beneath it.
WINDOW_STATS = """
WITH win AS (
    SELECT * FROM prices
    WHERE symbol = :symbol AND date BETWEEN :start AND :end
),
bounds AS (
    SELECT MIN(date) AS first_date, MAX(date) AS last_date, COUNT(*) AS trading_days
    FROM win
),
r AS (
    SELECT AVG(daily_return) AS mean_ret,
           AVG(daily_return * daily_return) AS mean_sq,
           COUNT(*) AS n
    FROM daily_returns
    WHERE symbol = :symbol AND date BETWEEN :start AND :end
      AND daily_return IS NOT NULL
),
dd AS (
    SELECT MIN(drawdown) AS max_drawdown
    FROM drawdowns
    WHERE symbol = :symbol AND date BETWEEN :start AND :end
)
SELECT
    b.first_date, b.last_date, b.trading_days,
    fo.close AS first_close,
    lc.close AS last_close,
    (lc.close - fo.close) / fo.close AS period_return,
    (SELECT MAX(high)   FROM win) AS period_high,
    (SELECT MIN(low)    FROM win) AS period_low,
    (SELECT MAX(close)  FROM win) AS highest_close,
    (SELECT MIN(close)  FROM win) AS lowest_close,
    (SELECT AVG(volume) FROM win) AS avg_volume,
    (SELECT AVG(close * volume) FROM win) AS avg_dollar_volume,
    SQRT(MAX(r.mean_sq - r.mean_ret * r.mean_ret, 0)) * SQRT(252) AS ann_volatility,
    dd.max_drawdown,
    POWER(lc.close / fo.close,
          1.0 / MAX((julianday(b.last_date) - julianday(b.first_date)) / 365.25, 0.01)) - 1 AS cagr
FROM bounds b, r, dd,
     prices fo, prices lc
WHERE fo.symbol = :symbol AND fo.date = b.first_date
  AND lc.symbol = :symbol AND lc.date = b.last_date;
"""

# Trailing 52-week high/low and the latest session's move — the quote-strip
# figures, always as-of the newest data regardless of the selected window.
# Reads the materialized `latest_quote` table (built in build_db.py) rather than
# joining the daily_returns view, which had to LAG across every row: 286ms -> ~1ms.
QUOTE_SNAPSHOT = """
SELECT date, open, high, low, close, volume,
       prev_close, daily_return, w52_high, w52_low
FROM latest_quote
WHERE symbol = :symbol;
"""

# --------------------------------------------------------------------------
# Leaderboards / screener — one row per symbol over each symbol's own history
# --------------------------------------------------------------------------
LEADERBOARD = """
SELECT
    symbol, name, sector, industry,
    first_date, last_date, years,
    last_close, last_daily_return,
    total_return, cagr,
    ann_volatility, current_volatility, max_drawdown,
    highest_close, lowest_close, avg_volume, avg_dollar_volume
FROM symbol_stats
WHERE is_index = 0
ORDER BY cagr DESC;
"""

# Period movers computed inside the selected window, so the leaderboard responds
# to the date filter rather than only ever showing all-time figures.
PERIOD_MOVERS = """
WITH win AS (
    SELECT symbol, date, close
    FROM prices
    WHERE date BETWEEN :start AND :end
),
edges AS (
    SELECT symbol, MIN(date) AS first_date, MAX(date) AS last_date, COUNT(*) AS sessions
    FROM win GROUP BY symbol
)
SELECT
    s.symbol, s.name, s.sector,
    e.first_date, e.last_date, e.sessions,
    fo.close AS start_close,
    lc.close AS end_close,
    (lc.close - fo.close) / fo.close AS period_return,
    (SELECT AVG(close * volume) FROM prices p
      WHERE p.symbol = s.symbol AND p.date BETWEEN :start AND :end) AS avg_dollar_volume
FROM symbols s
JOIN edges e ON e.symbol = s.symbol
JOIN win fo  ON fo.symbol = s.symbol AND fo.date = e.first_date
JOIN win lc  ON lc.symbol = s.symbol AND lc.date = e.last_date
WHERE s.is_index = 0
ORDER BY period_return DESC;
"""

SECTOR_PERFORMANCE = """
WITH win AS (
    SELECT symbol, date, close FROM prices WHERE date BETWEEN :start AND :end
),
edges AS (
    SELECT symbol, MIN(date) AS fd, MAX(date) AS ld FROM win GROUP BY symbol
),
per_symbol AS (
    SELECT s.sector, (lc.close - fo.close) / fo.close AS ret
    FROM symbols s
    JOIN edges e ON e.symbol = s.symbol
    JOIN win fo ON fo.symbol = s.symbol AND fo.date = e.fd
    JOIN win lc ON lc.symbol = s.symbol AND lc.date = e.ld
    WHERE s.is_index = 0
)
SELECT sector,
       COUNT(*) AS n_symbols,
       MEDIAN(ret) AS median_return,
       AVG(ret) AS avg_return,
       MIN(ret) AS worst_return,
       MAX(ret) AS best_return
FROM per_symbol
GROUP BY sector
ORDER BY median_return DESC;
"""

# --------------------------------------------------------------------------
# SQL Explorer registry: what gets showcased, with an explanation each.
# --------------------------------------------------------------------------
EXPLORER: dict[str, dict[str, object]] = {
    "Leaderboard (symbol_stats)": {
        "sql": LEADERBOARD,
        "params": [],
        "explain": (
            "Reads the `symbol_stats` view — one row per symbol. That view stitches "
            "together six CTEs (period bounds, return moments, price extremes, worst "
            "drawdown, latest rolling volatility, last daily move) and derives CAGR with "
            "`POWER(last/first, 1/years) - 1`. Because each symbol's window is its own "
            "listed history, `first_date` and `years` are returned alongside so unequal "
            "periods stay visible instead of being compared silently."
        ),
    },
    "Period movers (date-scoped)": {
        "sql": PERIOD_MOVERS,
        "params": ["start", "end"],
        "explain": (
            "Ranks every symbol by return inside the selected window. It finds each "
            "symbol's first and last session in range (`GROUP BY` + `MIN/MAX(date)`), "
            "self-joins back to get the closes at those two dates, and divides. This is "
            "why the gainers/losers tables respond to the date filter rather than being "
            "fixed all-time lists."
        ),
    },
    "Sector performance": {
        "sql": SECTOR_PERFORMANCE,
        "params": ["start", "end"],
        "explain": (
            "Computes a per-symbol window return, then aggregates to sector. The "
            "headline figure is the MEDIAN, not the mean: over long windows the mean "
            "of total returns is dominated by a single outlier (one +85,000% name "
            "drags a sector 'average' into five figures), which describes that stock "
            "rather than the sector. MEDIAN is a custom aggregate registered in "
            "db.py, since SQLite has no built-in one. Mean/min/max are returned "
            "alongside for comparison. Members are equal-weighted, not "
            "market-cap-weighted — share counts aren't available from the free source."
        ),
    },
    "Indexed comparison (rebase to 100)": {
        "sql": INDEXED_COMPARISON,
        "params": ["start", "end"],
        "explain": (
            "Rebases every series to 100 at its first close in the window, so symbols at "
            "very different price levels are comparable on a single shared axis. This is "
            "deliberately used instead of a second y-axis: two independent y-scales let "
            "you manufacture any apparent correlation by choosing the scales."
        ),
    },
    "Window stats (KPI row)": {
        "sql": WINDOW_STATS,
        "params": ["symbol", "start", "end"],
        "explain": (
            "One query behind the whole KPI row. Volatility comes from the variance "
            "identity E[r²] − E[r]², annualized by √252; max drawdown comes from the "
            "`drawdowns` view; CAGR is geometric over actual elapsed calendar time via "
            "`julianday`. Everything is scoped to the selected window so the cards can "
            "never disagree with the charts below them."
        ),
    },
    "Moving averages (50 / 200 session)": {
        "sql": MOVING_AVERAGES_IN_RANGE,
        "params": ["symbol", "start", "end"],
        "explain": (
            "Rolling means over trailing 50- and 200-session frames. The `CASE WHEN "
            "COUNT(*) OVER w = N` guard returns NULL until the frame is actually full, "
            "so the early part of a series shows no line rather than a misleading "
            "average computed from fewer sessions than the label claims."
        ),
    },
    "Cumulative return (log-sum compounding)": {
        "sql": CUMULATIVE_RETURN_IN_RANGE,
        "params": ["symbol", "start", "end"],
        "explain": (
            "Compounds daily returns with `EXP(SUM(LN(1+r)) OVER (ORDER BY date)) - 1`. "
            "Summing logs then exponentiating is the running product of (1+r) — true "
            "compounding, not a cumulative sum of percentages, which would drift."
        ),
    },
    "Rolling volatility (21 sessions)": {
        "sql": VOLATILITY_IN_RANGE,
        "params": ["symbol", "start", "end"],
        "explain": (
            "Standard deviation of daily returns over a trailing 21-session window "
            "(about one trading month), annualized by √252. Computed in SQL from the "
            "same variance identity, and NULL until the window is full."
        ),
    },
    "Calendar-year summary": {
        "sql": YEARLY_SUMMARY,
        "params": ["symbol"],
        "explain": (
            "Per-year open/close/high/low, average volume and return. A US equity year "
            "runs ~250 sessions, so any year with fewer is flagged `is_partial` — the "
            "first and last years of a 25-year window are partial, and the UI fades them "
            "because a part-year figure is not a calendar-year return."
        ),
    },
}

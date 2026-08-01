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
# SQL Explorer registry.
#
# Each entry is a *story*, not a code dump: the business question it answers, a
# plain-English explanation, what it costs and touches, and where its output
# actually appears in the product. The SQL is one field among several rather
# than the headline.
# --------------------------------------------------------------------------
EXPLORER: dict[str, dict[str, object]] = {
    "Long-run performance leaderboard": {
        "question": "Which companies compounded fastest over their listed history?",
        "sql": LEADERBOARD,
        "params": [],
        "read_path": "Rollup table",
        "read_path_note": "Precomputed at build time",
        "indexes": ["idx_symbol_stats (symbol)"],
        "objects": ["symbol_stats (materialized)"],
        "powers": [
            "Markets → Performance → Long-run performance table",
            "Risk & Portfolio → Risk → Risk vs return scatter",
            "Developer Center → Performance benchmarks",
        ],
        "explain": (
            "**What it does.** Returns one row per company with its total return, "
            "compound annual growth rate (CAGR), annualized volatility and worst "
            "drawdown, over that company's own listed history.\n\n"
            "**Why it exists.** \"Who won?\" is the first question anyone asks of "
            "market data. Total return alone is misleading — it rewards whoever has "
            "been listed longest — so CAGR normalizes for time and volatility and "
            "drawdown show what an investor endured to earn it.\n\n"
            "**How it works.** It reads `symbol_stats`, a rollup built from six CTEs: "
            "period bounds, return moments, price extremes, worst drawdown, latest "
            "rolling volatility and last daily move. CAGR is geometric — "
            "`POWER(last/first, 1/years) - 1` over actual elapsed calendar time from "
            "`julianday`, not a simple average.\n\n"
            "**The honesty check.** Each company's window is its own listed history, so "
            "the periods are *not* equal. `first_date` and `years` are returned "
            "alongside so an unequal comparison can't hide — a 2012 listing has not "
            "had the same run as a 2001 one."
        ),
    },
    "Best and worst performers in a period": {
        "question": "Who were the biggest winners and losers over a specific date range?",
        "sql": PERIOD_MOVERS,
        "params": ["start", "end"],
        "read_path": "Indexed scan",
        "read_path_note": "Range-scanned per symbol",
        "indexes": ["idx_prices_symbol_date (symbol, date)", "idx_prices_date (date)"],
        "objects": ["prices", "symbols"],
        "powers": [
            "Markets → Sectors & Movers → Advancing / declining breadth",
            "Markets → Sectors & Movers → Top gainer and worst decliner cards",
            "Markets → Sectors & Movers → Movers table",
        ],
        "explain": (
            "**What it does.** Ranks every company by its return *inside the selected "
            "window*, rather than over all time.\n\n"
            "**Why it exists.** All-time winners are a fixed list that never changes. "
            "The useful question is 'who moved during the period I'm looking at' — "
            "which is what makes the date range meaningful rather than decorative.\n\n"
            "**How it works.** For each symbol it finds the first and last trading "
            "session inside the range (`GROUP BY symbol` with `MIN/MAX(date)`), joins "
            "back to `prices` to fetch the closes on exactly those two dates, and "
            "divides. Joining back is what makes it correct across symbols with "
            "different trading calendars — you can't assume every symbol traded on the "
            "window's first day."
        ),
    },
    "Sector performance": {
        "question": "Which sectors led and lagged, without one mega-cap distorting the answer?",
        "sql": SECTOR_PERFORMANCE,
        "params": ["start", "end"],
        "read_path": "Indexed scan + aggregate",
        "read_path_note": "Custom MEDIAN aggregate",
        "indexes": ["idx_prices_date (date)"],
        "objects": ["prices", "symbols"],
        "powers": ["Markets → Sectors & Movers → Sector performance chart"],
        "explain": (
            "**What it does.** Computes each company's return over the window, then "
            "aggregates to sector — reporting the **median** member return.\n\n"
            "**Why median rather than mean.** This is the most important decision in "
            "the query. Over a 25-year window the mean showed Information Technology at "
            "**+13,553%** — a figure that was almost entirely Apple's +85,867%. That "
            "describes one stock, not a sector. The median gives ~+1,082%, which "
            "actually characterizes a typical member.\n\n"
            "**How it works.** A CTE computes per-symbol window returns, then "
            "`GROUP BY sector` applies `MEDIAN`, a custom aggregate registered in "
            "`db.py` because SQLite has no built-in one. Mean, best and worst are "
            "returned alongside so the skew stays visible on hover.\n\n"
            "**Limitation, stated.** Members are equal-weighted, not "
            "market-cap-weighted — share counts aren't available from the free source."
        ),
    },
    "Comparing companies fairly": {
        "question": "How do I compare companies trading at completely different prices?",
        "sql": INDEXED_COMPARISON,
        "params": ["start", "end"],
        "read_path": "Indexed scan",
        "read_path_note": "One pass, rebased in SQL",
        "indexes": ["idx_prices_date (date)", "idx_prices_symbol_date (symbol, date)"],
        "objects": ["prices"],
        "powers": [
            "Research → History → Comparison chart",
            "Research → History → Indexed comparison table",
        ],
        "explain": (
            "**What it does.** Rebases every series to 100 at its first close inside the "
            "window, so a $30 stock and a $600 stock are directly comparable.\n\n"
            "**Why it exists.** Raw prices can't be compared — a $5 move means something "
            "different at $30 than at $600. Rebasing converts levels into *growth*, "
            "which is the comparable quantity.\n\n"
            "**Why not a second y-axis.** A dual-axis chart is the obvious alternative "
            "and it's the wrong one: two independent scales can be aligned to imply any "
            "correlation you like. Rebasing puts everything on **one** axis, so the "
            "comparison is honest by construction.\n\n"
            "**How it works.** A CTE finds each symbol's first in-window close as its "
            "base, then the main query returns `100 * close / base` per row. The "
            "arithmetic stays in SQL; Python only pivots the tidy result into "
            "date × symbol for plotting."
        ),
    },
    "Headline metrics for one company": {
        "question": "What are the key statistics for one company over a chosen window?",
        "sql": WINDOW_STATS,
        "params": ["symbol", "start", "end"],
        "read_path": "Indexed scan",
        "read_path_note": "Single symbol, single pass",
        "indexes": ["idx_prices_symbol_date (symbol, date)"],
        "objects": ["prices", "daily_returns", "drawdowns"],
        "powers": [
            "Markets → Index → KPI row",
            "Research → History → Company metric cards",
        ],
        "explain": (
            "**What it does.** Returns the whole KPI row — period return, CAGR, high and "
            "low, average volume and dollar turnover, annualized volatility and worst "
            "drawdown — in a single query.\n\n"
            "**Why one query.** If the cards were computed separately they could "
            "disagree with each other or with the charts beneath them. One query over "
            "one window makes that impossible.\n\n"
            "**How it works.** Volatility uses the variance identity "
            "`E[r²] − E[r]²`, annualized by `√252` (the approximate number of trading "
            "days in a year). CAGR is geometric over actual elapsed time via "
            "`julianday`. Max drawdown comes from the `drawdowns` view.\n\n"
            "**Why investors care.** Return alone is half the picture. Volatility and "
            "drawdown describe the ride — a 30% CAGR through a 90% drawdown is a very "
            "different proposition from the same return earned smoothly."
        ),
    },
    "Trend indicators": {
        "question": "Is a company trading above or below its long-term trend?",
        "sql": MOVING_AVERAGES_IN_RANGE,
        "params": ["symbol", "start", "end"],
        "read_path": "View over window functions",
        "read_path_note": "Rolling frames, computed on read",
        "indexes": ["idx_prices_symbol_date (symbol, date)"],
        "objects": ["moving_averages (view)", "prices"],
        "powers": ["Research → History → Moving averages chart"],
        "explain": (
            "**What it does.** Returns the closing price alongside its trailing 50- and "
            "200-session moving averages.\n\n"
            "**Why investors care.** These two are the most watched trend indicators in "
            "markets. Price above the 200-day is broadly read as an uptrend, and the "
            "50-day crossing the 200-day has its own well-known names.\n\n"
            "**How it works.** `AVG(close) OVER (PARTITION BY symbol ORDER BY date ROWS "
            "BETWEEN 49 PRECEDING AND CURRENT ROW)` — the frame clause defines the "
            "window; no self-join needed.\n\n"
            "**The detail that matters.** Each average is wrapped in "
            "`CASE WHEN COUNT(*) OVER w = N`, returning NULL until the frame is "
            "genuinely full. Without that guard a '200-day average' would silently be a "
            "30-day average for the first 200 rows — a label claiming more than the "
            "arithmetic supports."
        ),
    },
    "Growth of an investment": {
        "question": "What would an investment have grown to over this period?",
        "sql": CUMULATIVE_RETURN_IN_RANGE,
        "params": ["symbol", "start", "end"],
        "read_path": "View + window function",
        "read_path_note": "Log-sum compounding",
        "indexes": ["idx_prices_symbol_date (symbol, date)"],
        "objects": ["daily_returns (view)"],
        "powers": ["Research → History → Cumulative return chart"],
        "explain": (
            "**What it does.** Compounds daily returns into a running growth curve "
            "starting at 0%.\n\n"
            "**How it works.** "
            "`EXP(SUM(LN(1 + daily_return)) OVER (ORDER BY date)) - 1`. Summing "
            "logarithms and then exponentiating is the running *product* of "
            "`(1 + r)` — which is what compounding is.\n\n"
            "**The bug this avoids.** The intuitive version is a cumulative *sum* of "
            "daily percentages. It's wrong, and it drifts further the longer the window: "
            "+10% then −10% is −1%, not 0%. Over 6,000 trading days the error is "
            "enormous. Doing it in log space is both correct and numerically stable."
        ),
    },
    "Risk over time": {
        "question": "How volatile has a company been, and when did that change?",
        "sql": VOLATILITY_IN_RANGE,
        "params": ["symbol", "start", "end"],
        "read_path": "View over window functions",
        "read_path_note": "21-session rolling frame",
        "indexes": ["idx_prices_symbol_date (symbol, date)"],
        "objects": ["rolling_volatility (view)", "daily_returns (view)"],
        "powers": [
            "Research → History → Rolling volatility chart",
        ],
        "explain": (
            "**What it does.** Standard deviation of daily returns over a trailing "
            "21-session window — about one trading month — annualized by `√252`.\n\n"
            "**Why investors care.** A single volatility number for 25 years hides "
            "everything interesting. The rolling version shows *when* risk arrived: the "
            "2008 crisis and March 2020 both appear as unmistakable spikes.\n\n"
            "**How it works.** Same variance identity as the KPI query, but inside a "
            "moving frame, and NULL until the window is full so early values aren't "
            "computed from fewer observations than the label implies.\n\n"
            "**Why √252.** Variance scales with time, so standard deviation scales with "
            "its square root; 252 is the conventional count of trading days per year."
        ),
    },
    "Yearly track record": {
        "question": "How has the market performed year by year?",
        "sql": YEARLY_SUMMARY,
        "params": ["symbol"],
        "read_path": "View + grouped joins",
        "read_path_note": "Per-year bounds and aggregates",
        "indexes": ["idx_prices_symbol_date (symbol, date)"],
        "objects": ["yearly_summary (view)", "prices"],
        "powers": [
            "Markets → Performance → Calendar-year returns chart",
            "Markets → Performance → Partial-year flagging",
        ],
        "explain": (
            "**What it does.** Per-year open, close, high, low, average volume and "
            "return, plus a flag marking years the dataset only partly covers.\n\n"
            "**Why the flag matters.** A 25-year window starts and ends mid-year, so the "
            "first and last calendar years are incomplete. Showing a July-to-December "
            "figure beside twelve full years, both labelled 'annual return', is simply "
            "wrong. The view counts sessions and flags any year under 240 — a US equity "
            "year has ~250 — and the UI fades and asterisks those bars.\n\n"
            "**How it works.** A bounds CTE finds each year's first and last session, an "
            "aggregate CTE computes high/low/volume, and the two join back to `prices` "
            "for the closes on those exact dates."
        ),
    },
}


# --------------------------------------------------------------------------
# Portfolio simulation
# --------------------------------------------------------------------------
# A weighted portfolio's DAILY return is the weighted average of its holdings'
# daily returns. That models a portfolio rebalanced back to target weights every
# day -- the standard simple assumption, and stated as such in the UI, because a
# buy-and-hold portfolio drifts away from its target weights as winners grow.
#
# Only sessions where EVERY holding traded are used, so a symbol that listed
# mid-window cannot silently change the portfolio's composition partway through.
PORTFOLIO_SERIES = """
WITH picks(symbol, weight) AS (VALUES {weight_rows}),
-- The money goes in on the first session where EVERY holding has a price. That
-- is the investable date; returns accrue from the following session. Defining
-- it from returns instead would start a day late and drop a real trading day
-- whenever a holding listed mid-window.
priced AS (
    SELECT p.date
    FROM prices p JOIN picks k ON k.symbol = p.symbol
    WHERE p.date BETWEEN :start AND :end AND p.close IS NOT NULL
    GROUP BY p.date
    HAVING COUNT(DISTINCT p.symbol) = (SELECT COUNT(*) FROM picks)
),
invested AS (SELECT MIN(date) AS d0 FROM priced),
port AS (
    SELECT d.date, SUM(d.daily_return * k.weight) AS port_return
    FROM daily_returns d
    JOIN picks k ON k.symbol = d.symbol
    JOIN priced pr ON pr.date = d.date
    WHERE d.date > (SELECT d0 FROM invested)
      AND d.daily_return IS NOT NULL
    GROUP BY d.date
    HAVING COUNT(*) = (SELECT COUNT(*) FROM picks)
)
SELECT
    date,
    port_return,
    EXP(SUM(LN(1 + port_return)) OVER (ORDER BY date)) - 1 AS cumulative_return
FROM port
ORDER BY date;
"""


# Headline portfolio statistics, computed from the same weighted daily series.
PORTFOLIO_STATS = """
WITH picks(symbol, weight) AS (VALUES {weight_rows}),
priced AS (
    SELECT p.date
    FROM prices p JOIN picks k ON k.symbol = p.symbol
    WHERE p.date BETWEEN :start AND :end AND p.close IS NOT NULL
    GROUP BY p.date
    HAVING COUNT(DISTINCT p.symbol) = (SELECT COUNT(*) FROM picks)
),
invested AS (SELECT MIN(date) AS d0 FROM priced),
port AS (
    SELECT d.date, SUM(d.daily_return * k.weight) AS pr
    FROM daily_returns d
    JOIN picks k ON k.symbol = d.symbol
    JOIN priced p ON p.date = d.date
    WHERE d.date > (SELECT d0 FROM invested)
      AND d.daily_return IS NOT NULL
    GROUP BY d.date
    HAVING COUNT(*) = (SELECT COUNT(*) FROM picks)
),
growth AS (
    SELECT date, pr, EXP(SUM(LN(1 + pr)) OVER (ORDER BY date)) AS wealth
    FROM port
),
peaks AS (
    SELECT date, wealth,
           MAX(wealth) OVER (ORDER BY date
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak
    FROM growth
),
moments AS (
    SELECT AVG(pr) AS mean_r, AVG(pr * pr) AS mean_sq, COUNT(*) AS n, MAX(date) AS d1
    FROM port
)
SELECT
    m.n AS sessions,
    (SELECT d0 FROM invested) AS first_date,
    m.d1 AS last_date,
    (SELECT wealth FROM growth ORDER BY date DESC LIMIT 1) - 1 AS total_return,
    -- Elapsed time runs from the investment date, not the first return date.
    POWER((SELECT wealth FROM growth ORDER BY date DESC LIMIT 1),
          1.0 / MAX((julianday(m.d1) - julianday((SELECT d0 FROM invested))) / 365.25, 0.01)) - 1 AS cagr,
    SQRT(MAX(m.mean_sq - m.mean_r * m.mean_r, 0)) * SQRT(252) AS ann_volatility,
    (SELECT MIN((wealth - peak) / peak) FROM peaks) AS max_drawdown,
    (SELECT MAX(pr) FROM port) AS best_day,
    (SELECT MIN(pr) FROM port) AS worst_day
FROM moments m;
"""


# ---------------------------------------------------------------------------
# Schema card for model-generated SQL
# ---------------------------------------------------------------------------
# Handed to the model when no template can answer a question. It lists only the
# objects `sqlguard.ALLOWED_OBJECTS` permits, so the model is never told about a
# table the validator would then reject -- a mismatch there produces confident
# SQL that always fails validation, which reads as the feature being broken.
#
# Kept here rather than in the prompt module because it describes the SQL layer,
# and this file is where the SQL layer is documented.
SCHEMA_CARD = """\
Tables:
  symbols(symbol, name, sector, industry, is_index)
      One row per company. is_index = 1 marks the S&P 500 index itself (^GSPC);
      equities are is_index = 0. Filter on it unless the question is about the index.
  prices(symbol, date, open, high, low, close, adj_close, volume)
      One row per symbol per trading session. date is TEXT 'YYYY-MM-DD'.
      ~300k rows -- always constrain by symbol, date, or both.

Views (computed on read):
  daily_returns(symbol, date, close, volume, daily_return)
      daily_return is a fraction: 0.0125 means +1.25%.
  drawdowns(symbol, date, close, peak_close, drawdown)
      drawdown is negative or zero, measured from the running all-time high.
  moving_averages(symbol, date, close, ma_50, ma_200)
      NULL until the trailing window is full.
  rolling_volatility(symbol, date, ann_volatility_21d)
      21-session annualized standard deviation.
  yearly_summary(symbol, year, open_close, close_close, year_high, year_low,
                 avg_volume, trading_days, year_return, is_partial)
      is_partial = 1 when the year is incomplete at either end of the data.
  monthly_returns(symbol, year_month, year, month, month_return)

Materialized rollups (fast -- prefer these for cross-company questions):
  symbol_stats(symbol, name, sector, industry, is_index, first_date, last_date,
               trading_days, first_close, last_close, total_return, years, cagr,
               ann_volatility, max_drawdown, current_volatility, last_daily_return,
               highest_close, lowest_close, avg_volume, avg_dollar_volume)
      One row per symbol over its own full listed history.
  latest_quote(symbol, date, open, high, low, close, volume, prev_close,
               daily_return, w52_high, w52_low)
      Newest session per symbol.

Conventions:
  * Returns, CAGR, volatility and drawdown are fractions, not percentages.
  * Histories differ -- META lists from 2012, TSLA from 2010. Return `years` or
    `first_date` alongside any all-time ranking so unequal periods stay visible.
  * Dividends are excluded; these are price returns.
  * SQLite dialect. SQRT, POWER, LN, EXP and MEDIAN are registered.
  * Sector figures use MEDIAN, not AVG -- one outlier otherwise stands in for
    a whole sector.
"""


# ---------------------------------------------------------------------------
# Parameterized variants for Ask the Market
# ---------------------------------------------------------------------------
# The plain LEADERBOARD and PERIOD_MOVERS above return everything and let the
# caller slice. These take the row count, the sector filter and the sort column
# as query parameters instead, so a question's "top 5 technology companies"
# becomes a LIMIT and a WHERE rather than a pandas `.head()` after the fact --
# the parameters reach the database, which is where a reader of the SQL Explorer
# would expect to find them.
#
# `{order_by}` and `{not_null}` are format slots, not bind parameters: SQLite
# cannot bind a column name. Both are filled from a whitelist in data_access, so
# no user-supplied text ever reaches them. `:limit` of -1 means no limit.

PERIOD_MOVERS_RANKED = """
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
  AND (:sector = '' OR s.sector = :sector)
ORDER BY {order_by}
LIMIT :limit;
"""

LEADERBOARD_RANKED = """
SELECT
    symbol, name, sector, industry,
    first_date, last_date, years,
    last_close, last_daily_return,
    total_return, cagr,
    ann_volatility, current_volatility, max_drawdown,
    highest_close, lowest_close, avg_volume, avg_dollar_volume
FROM symbol_stats
WHERE is_index = 0
  AND (:sector = '' OR sector = :sector)
  AND {not_null} IS NOT NULL
ORDER BY {order_by}
LIMIT :limit;
"""


# --------------------------------------------------------------------------
# Rolling returns — every holding period of a fixed length, not one window
# --------------------------------------------------------------------------
# A single window return answers "what did it do since 2015?", which is one
# draw from one starting date. Rolling returns answer the question an investor
# actually has: "if I had bought at ANY point and held N years, what range of
# outcomes would I have seen?" The spread between the best and worst bar is the
# real risk of the horizon, and it is invisible in a single cumulative line.
#
# The lookback is a SESSION count, not a date offset: markets don't trade every
# calendar day, so a 365-day offset lands on a holiday or weekend for a large
# share of rows and silently drops them. Pairing row N with row N-:sessions is
# exact. `LAG` would read more naturally but SQLite requires a literal offset,
# and the horizon is chosen at runtime — hence the self-join on ROW_NUMBER.
ROLLING_RETURNS = """
WITH s AS (
    SELECT date, close, ROW_NUMBER() OVER (ORDER BY date) AS rn
    FROM prices
    WHERE symbol = :symbol AND close IS NOT NULL AND close > 0
)
SELECT
    a.date,
    b.date AS start_date,
    a.close / b.close - 1 AS window_return,
    -- Annualized so horizons are comparable: a 10-year total return and a
    -- 1-year total return are not the same unit and must not share an axis.
    POWER(a.close / b.close, 252.0 / :sessions) - 1 AS annualized_return
FROM s a
JOIN s b ON b.rn = a.rn - :sessions
ORDER BY a.date;
"""

# The distribution behind the line above. Computed in SQL rather than from the
# returned frame, so the summary and the chart cannot drift apart and neither is
# a Python-side calculation.
ROLLING_RETURN_SUMMARY = """
WITH s AS (
    SELECT date, close, ROW_NUMBER() OVER (ORDER BY date) AS rn
    FROM prices
    WHERE symbol = :symbol AND close IS NOT NULL AND close > 0
),
roll AS (
    SELECT a.date,
           POWER(a.close / b.close, 252.0 / :sessions) - 1 AS ann_return
    FROM s a
    JOIN s b ON b.rn = a.rn - :sessions
)
SELECT
    COUNT(*) AS periods,
    MIN(ann_return) AS worst,
    MAX(ann_return) AS best,
    AVG(ann_return) AS mean_return,
    MEDIAN(ann_return) AS median_return,
    -- Share of holding periods that finished above water. The single most
    -- useful figure here: it turns "stocks go up over time" into a number.
    AVG(CASE WHEN ann_return > 0 THEN 1.0 ELSE 0.0 END) AS share_positive,
    (SELECT date FROM roll ORDER BY ann_return ASC  LIMIT 1) AS worst_end_date,
    (SELECT date FROM roll ORDER BY ann_return DESC LIMIT 1) AS best_end_date
FROM roll;
"""


# --------------------------------------------------------------------------
# Correlation matrix — how a set of names move together
# --------------------------------------------------------------------------
# Pearson correlation of DAILY RETURNS, not of prices. Two rising price series
# correlate near 1.0 whatever they actually did, because both trend; returns are
# what a diversification claim rests on.
#
# Expanded from the covariance identity so it runs as one grouped pass:
#     r = (n*Sxy - Sx*Sy) / (sqrt(n*Sxx - Sx^2) * sqrt(n*Syy - Sy^2))
# The self-join on `date` is what enforces pairwise-complete observations — a
# session where either name did not trade is absent from that pair's sums, so a
# mid-window listing narrows its own pairs rather than corrupting every cell.
#
# `{symbol_rows}` is a format slot: the VALUES list is built in data_access from
# symbols validated against the universe, never interpolated from user text.
CORRELATION_MATRIX = """
WITH picks(symbol) AS (VALUES {symbol_rows}),
r AS (
    SELECT d.symbol, d.date, d.daily_return
    FROM daily_returns d
    JOIN picks k ON k.symbol = d.symbol
    WHERE d.date BETWEEN :start AND :end
      AND d.daily_return IS NOT NULL
),
pairs AS (
    SELECT
        a.symbol AS sym_a,
        b.symbol AS sym_b,
        COUNT(*)                                   AS n,
        SUM(a.daily_return)                        AS sx,
        SUM(b.daily_return)                        AS sy,
        SUM(a.daily_return * b.daily_return)       AS sxy,
        SUM(a.daily_return * a.daily_return)       AS sxx,
        SUM(b.daily_return * b.daily_return)       AS syy
    FROM r a
    JOIN r b ON b.date = a.date
    GROUP BY a.symbol, b.symbol
)
SELECT
    sym_a, sym_b, n,
    -- Clamped to [-1, 1]. The expanded identity is algebraically incapable of
    -- leaving that range, but in floating point a symbol against ITSELF returns
    -- 1.0000000000000002, and a coefficient above 1.0 on screen reads as a bug
    -- in the statistic rather than as the last bit of a double.
    CASE WHEN n < 3 THEN NULL ELSE
        MAX(-1.0, MIN(1.0,
            (n * sxy - sx * sy) / NULLIF(
                SQRT(MAX(n * sxx - sx * sx, 0.0)) * SQRT(MAX(n * syy - sy * sy, 0.0)), 0.0)))
    END AS correlation
FROM pairs
ORDER BY sym_a, sym_b;
"""


# --------------------------------------------------------------------------
# Return contribution — which holding actually produced the portfolio's return
# --------------------------------------------------------------------------
# Weight x holding-return is the intuitive answer and it is wrong: the parts do
# not sum to the whole once returns compound. The exact decomposition for a
# daily-rebalanced portfolio falls out of the wealth recursion:
#
#     W_T - 1 = SUM_t W_{t-1} * pr_t              (telescoping the compounding)
#             = SUM_t W_{t-1} * SUM_i w_i r_it
#             = SUM_i [ SUM_t w_i * r_it * W_{t-1} ]   <- one term per holding
#
# So a holding's contribution is its weighted daily return scaled by the
# portfolio's wealth going INTO that session. These terms sum to the portfolio's
# total return exactly, which `tests_portfolio.py` asserts to 1e-9.
#
# `w_begin` is the wealth *before* the session — the frame therefore ends one row
# early (`1 PRECEDING`), and the first session's COALESCE supplies the opening
# wealth of 1.0 that an empty window frame returns as NULL.
PORTFOLIO_CONTRIBUTION = """
WITH picks(symbol, weight) AS (VALUES {weight_rows}),
priced AS (
    SELECT p.date
    FROM prices p JOIN picks k ON k.symbol = p.symbol
    WHERE p.date BETWEEN :start AND :end AND p.close IS NOT NULL
    GROUP BY p.date
    HAVING COUNT(DISTINCT p.symbol) = (SELECT COUNT(*) FROM picks)
),
invested AS (SELECT MIN(date) AS d0 FROM priced),
port AS (
    SELECT d.date, SUM(d.daily_return * k.weight) AS pr
    FROM daily_returns d
    JOIN picks k ON k.symbol = d.symbol
    JOIN priced p ON p.date = d.date
    WHERE d.date > (SELECT d0 FROM invested)
      AND d.daily_return IS NOT NULL
    GROUP BY d.date
    HAVING COUNT(*) = (SELECT COUNT(*) FROM picks)
),
wealth AS (
    SELECT date,
           EXP(COALESCE(
               SUM(LN(1 + pr)) OVER (ORDER BY date
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
               0.0)) AS w_begin
    FROM port
)
SELECT
    k.symbol,
    s.name,
    s.sector,
    k.weight,
    SUM(d.daily_return * k.weight * w.w_begin) AS contribution,
    -- The holding's own compounded return over exactly the sessions the
    -- portfolio was invested, so the two columns describe the same period.
    EXP(SUM(LN(1 + d.daily_return))) - 1 AS holding_return,
    COUNT(*) AS sessions
FROM daily_returns d
JOIN picks k   ON k.symbol = d.symbol
JOIN wealth w  ON w.date = d.date
JOIN symbols s ON s.symbol = k.symbol
WHERE d.daily_return IS NOT NULL
GROUP BY k.symbol, s.name, s.sector, k.weight
ORDER BY contribution DESC;
"""


# --------------------------------------------------------------------------
# Explorer entries for the queries defined above
# --------------------------------------------------------------------------
# Registered here rather than inside the EXPLORER literal because these
# statements are defined further down the file than the registry is. The
# alternative -- hoisting four long queries above it -- would put the SQL in
# an order that has nothing to do with how it reads.
EXPLORER.update({
    "Every holding period of a fixed length": {
        "question": "If I had bought at any point and held five years, what would I have earned?",
        "sql": ROLLING_RETURNS,
        "params": ["symbol", "sessions"],
        "read_path": "Indexed scan + self-join",
        "read_path_note": "Row-numbered, paired N sessions apart",
        "indexes": ["idx_prices_symbol_date (symbol, date)"],
        "objects": ["prices"],
        "powers": [
            "Markets → Performance → Rolling returns chart",
            "Markets → Performance → Ended-positive / best / worst / median cards",
        ],
        "explain": (
            "**What it does.** Returns the annualized return of *every* holding period "
            "of a chosen length in the record — roughly 3,800 overlapping ten-year "
            "periods, not one.\n\n"
            "**Why it exists.** A single cumulative line answers \"what did it do since "
            "2015?\", which is one draw from one starting date, and starting dates are "
            "the thing an investor does not control. The spread between the best and "
            "worst period is the real risk of the horizon, and it is completely "
            "invisible in a cumulative chart.\n\n"
            "**How it works.** `ROW_NUMBER()` numbers the sessions and the table joins "
            "to itself paired N rows apart. The lookback is a **session** count, not a "
            "date offset: markets don't trade every calendar day, so a 365-day offset "
            "lands on a weekend or holiday for a large share of rows and silently drops "
            "them. `LAG` would read more naturally but SQLite requires a literal offset "
            "and the horizon is picked at runtime.\n\n"
            "**The honesty check.** Returns are annualized so the four horizons share an "
            "axis, and the UI states that overlapping periods share most of their "
            "history — neighbouring points are not independent observations."
        ),
    },
    "How closely two companies move together": {
        "question": "Which of these companies actually diversify each other?",
        # Pre-filled with a representative set. The live query builds its
        # VALUES list from the user's selection, but the Explorer executes
        # what it displays, and an unfilled `{symbol_rows}` slot is not
        # valid SQL -- so the showcased statement is a real, runnable one.
        "sql": CORRELATION_MATRIX.format(
            symbol_rows="('AAPL'), ('JPM'), ('XOM'), ('JNJ')"),
        "params": ["start", "end"],
        "read_path": "View + pairwise self-join",
        "read_path_note": "One grouped pass over the pairs",
        "indexes": ["idx_prices_symbol_date (symbol, date)"],
        "objects": ["daily_returns (view)", "prices"],
        "powers": [
            "Risk & Portfolio → Risk → Correlation matrix",
            "Risk & Portfolio → Risk → Most / least correlated pair cards",
        ],
        "explain": (
            "**What it does.** Pearson correlation between every pair in a chosen set, "
            "over the selected window.\n\n"
            "**Why it is returns and not prices.** Two rising price series correlate "
            "near 1.0 whatever they actually did, because both trend. Correlation of "
            "*daily returns* is what a diversification claim rests on — it asks whether "
            "they move together day to day, not whether both went up over a decade.\n\n"
            "**How it works.** Expanded from the covariance identity — "
            "`r = (n*Sxy - Sx*Sy) / (sqrt(n*Sxx - Sx^2) * sqrt(n*Syy - Sy^2))` — so the "
            "whole matrix is one grouped pass instead of a query per pair. The result "
            "is clamped to [-1, 1]: the algebra cannot leave that range, but in floating "
            "point a symbol against itself returns 1.0000000000000002, and a coefficient "
            "above 1.0 on screen reads as a broken statistic.\n\n"
            "**The honesty check.** The self-join on `date` enforces pairwise-complete "
            "observations, so a company listed mid-window is measured over its own "
            "shorter overlap rather than corrupting every other cell. Below three shared "
            "sessions the coefficient is NULL, not a fabricated 1.0 from a two-point fit."
        ),
    },
    "Which holding actually produced the return": {
        "question": "My portfolio returned 40% — where did that come from?",
        # Pre-filled with an equal-weighted four-name basket, for the same
        # reason as the correlation entry above.
        "sql": PORTFOLIO_CONTRIBUTION.format(
            weight_rows="('AAPL', 0.25), ('MSFT', 0.25), ('JPM', 0.25), ('XOM', 0.25)"),
        "params": ["start", "end"],
        "read_path": "View + window functions",
        "read_path_note": "Wealth recursion, then grouped per holding",
        "indexes": ["idx_prices_symbol_date (symbol, date)"],
        "objects": ["daily_returns (view)", "prices", "symbols"],
        "powers": [
            "Portfolio → Return contribution chart",
            "Portfolio → Contribution table and its sum check",
        ],
        "explain": (
            "**What it does.** Splits the portfolio's total return into one figure per "
            "holding, in percentage points.\n\n"
            "**Why the obvious answer is wrong.** Weight x holding-return is what most "
            "people reach for, and the parts do not sum to the whole — once returns "
            "compound, the gap grows with the window. On a 25-year basket it misses by "
            "more than the total return itself.\n\n"
            "**How it works.** The exact decomposition falls out of the wealth "
            "recursion: `W_T - 1 = SUM_t W_{t-1} * pr_t`, and since `pr_t` is itself a "
            "weighted sum across holdings, the order of summation swaps to give one "
            "term per holding. So a holding's contribution is its weighted daily return "
            "scaled by the portfolio's wealth going *into* that session. `w_begin` uses "
            "a window frame ending `1 PRECEDING`, with a COALESCE supplying the opening "
            "wealth of 1.0 that an empty frame returns as NULL.\n\n"
            "**The honesty check.** These terms sum to the portfolio's total return "
            "exactly; `tests_portfolio.py` asserts it to 1e-9 across five baskets, and "
            "the UI prints the sum beside the total so a reader can check it too."
        ),
    },
})


# ==========================================================================
# Stock Journey — a company's history as a sequence of events
# ==========================================================================
# The Journey section replays one company's record forward in time. Every fact
# it narrates is one of these queries; `journey.py` selects and phrases them and
# computes nothing, so a figure on the Journey page is the same kind of object
# as a figure anywhere else in the app.
#
# All of these are ALL-TIME by construction, like Performance: a journey is the
# company's whole record, and the shared date window is a comparison frame for
# cross-section reading rather than a filter on a narrative. What moves is the
# `:asof` cursor, which selects a POINT inside that record, not a sub-window.

# Where the company stood on a given date -- the query behind the playhead.
#
# `:asof` is matched with `date <= :asof` rather than equality: the cursor is a
# calendar date and most calendar dates are not trading sessions, so an equality
# match would blank the whole panel on every weekend and holiday.
JOURNEY_SNAPSHOT = """
WITH s AS (
    SELECT date, close,
           MAX(close) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
               AS peak_close,
           ROW_NUMBER() OVER (ORDER BY date) AS rn
    FROM prices
    WHERE symbol = :symbol AND close IS NOT NULL AND close > 0
),
asof AS (
    SELECT * FROM s WHERE date <= :asof ORDER BY date DESC LIMIT 1
),
first AS (
    SELECT date AS first_date, close AS first_close FROM s ORDER BY date LIMIT 1
)
SELECT
    a.date,
    a.close,
    a.peak_close,
    a.rn AS sessions_elapsed,
    f.first_date,
    f.first_close,
    a.close / f.first_close - 1 AS return_to_date,
    (a.close - a.peak_close) / a.peak_close AS drawdown,
    -- A record high is a close at (not merely near) the running maximum, which
    -- includes the current row -- so the day it sets the record reads as 1.
    CASE WHEN a.close >= a.peak_close THEN 1 ELSE 0 END AS at_record_high,
    (julianday(a.date) - julianday(f.first_date)) / 365.25 AS years_elapsed,
    POWER(a.close / f.first_close,
          1.0 / MAX((julianday(a.date) - julianday(f.first_date)) / 365.25, 0.01)) - 1
        AS cagr_to_date
FROM asof a CROSS JOIN first f;
"""

# Every drawdown episode: peak, trough, and whether it ever recovered.
#
# Gaps-and-islands over the running maximum. A session that closes at the
# running peak opens a new episode, so a cumulative SUM of that flag labels each
# episode; the peak day is its first row and the recovery day is the first row
# of the NEXT episode. An episode with no next one is still underwater today,
# which is why `recovery_date` is nullable and must stay that way -- coalescing
# it to the last date would report an ongoing drawdown as recovered.
#
# `:asof` bounds the SOURCE rows, not the output. That distinction is the whole
# point: filtering the finished episodes instead would let a cursor sitting in
# 2003 report that the crash it is living through recovered in 2012. Cutting the
# series first means an episode still open at the cursor comes back with a NULL
# recovery -- which is what was actually known at that moment.
JOURNEY_DRAWDOWN_EPISODES = """
WITH s AS (
    SELECT date, close,
           MAX(close) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
               AS peak_close
    FROM prices
    WHERE symbol = :symbol AND close IS NOT NULL AND close > 0 AND date <= :asof
),
flagged AS (
    SELECT date, close, peak_close,
           CASE WHEN close >= peak_close THEN 1 ELSE 0 END AS is_peak
    FROM s
),
grouped AS (
    SELECT date, close, peak_close,
           SUM(is_peak) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
               AS episode
    FROM flagged
),
episodes AS (
    SELECT
        episode,
        MIN(date)  AS peak_date,
        MAX(date)  AS last_date,
        MAX(peak_close) AS peak_close,
        MIN(close) AS trough_close,
        COUNT(*)   AS sessions
    FROM grouped
    GROUP BY episode
),
troughs AS (
    -- The trough's DATE, which a MIN() over close cannot give.
    SELECT g.episode, MIN(g.date) AS trough_date
    FROM grouped g
    JOIN episodes e ON e.episode = g.episode AND g.close = e.trough_close
    GROUP BY g.episode
)
SELECT
    e.peak_date,
    t.trough_date,
    LEAD(e.peak_date) OVER (ORDER BY e.episode) AS recovery_date,
    e.peak_close,
    e.trough_close,
    e.trough_close / e.peak_close - 1 AS depth,
    e.sessions,
    julianday(t.trough_date) - julianday(e.peak_date) AS days_to_trough,
    julianday(LEAD(e.peak_date) OVER (ORDER BY e.episode)) - julianday(t.trough_date)
        AS days_to_recover
FROM episodes e
JOIN troughs t ON t.episode = e.episode
WHERE e.trough_close / e.peak_close - 1 <= :min_depth
ORDER BY e.peak_date;
"""

# Largest single-session moves, both directions in one result.
#
# UNION ALL of two ordered halves rather than one query sorted by ABS(): the
# extremes are asymmetric (the worst day is usually larger than the best) and a
# single ABS ranking would return five losses and no gains for most companies.
JOURNEY_EXTREME_DAYS = """
WITH r AS (
    SELECT date, close, daily_return
    FROM daily_returns
    WHERE symbol = :symbol AND daily_return IS NOT NULL AND date <= :asof
)
SELECT * FROM (
    SELECT 'gain' AS direction, date, close, daily_return
    FROM r ORDER BY daily_return DESC LIMIT :limit
)
UNION ALL
SELECT * FROM (
    SELECT 'loss' AS direction, date, close, daily_return
    FROM r ORDER BY daily_return ASC LIMIT :limit
)
ORDER BY daily_return DESC;
"""

# Longest unbroken runs of up or down sessions.
#
# Classic gaps-and-islands: the difference between a global row number and a
# per-direction row number is constant within a run, so it labels the run.
# Flat sessions (a return of exactly zero) end a run rather than extending
# either one -- a flat day is not an up day, and treating it as one would
# silently merge two separate streaks into a longer fictitious one.
#
# The run's return comes from the CLOSES at its boundaries, not from compounding
# the daily returns: a product of daily returns has to be reconstructed with
# EXP(SUM(LN(...))) and accumulates float error over a long run, while the two
# closes give the same figure exactly.
JOURNEY_STREAKS = """
WITH r AS (
    SELECT date, close, daily_return,
           CASE WHEN daily_return > 0 THEN 1
                WHEN daily_return < 0 THEN -1
                ELSE 0 END AS direction
    FROM daily_returns
    WHERE symbol = :symbol AND daily_return IS NOT NULL AND date <= :asof
),
marked AS (
    SELECT *,
           ROW_NUMBER() OVER (ORDER BY date)
         - ROW_NUMBER() OVER (PARTITION BY direction ORDER BY date) AS run_id
    FROM r
    WHERE direction <> 0
),
runs AS (
    SELECT direction, run_id,
           COUNT(*)  AS length,
           MIN(date) AS start_date,
           MAX(date) AS end_date
    FROM marked
    GROUP BY direction, run_id
),
priced AS (
    SELECT
        runs.direction, runs.length, runs.start_date, runs.end_date,
        (SELECT close FROM prices
          WHERE symbol = :symbol AND date < runs.start_date
          ORDER BY date DESC LIMIT 1) AS close_before,
        (SELECT close FROM prices
          WHERE symbol = :symbol AND date = runs.end_date) AS close_after
    FROM runs
)
SELECT direction, length, start_date, end_date,
       close_after / close_before - 1 AS run_return
FROM priced
WHERE direction = :direction
ORDER BY length DESC, start_date ASC
LIMIT :limit;
"""

# Best and worst calendar months and years, as one labelled list.
#
# Partial periods are excluded for years (the view already flags them) and for
# months at the record's edges: a company listed on the 28th has a "month"
# of two sessions, and it would otherwise win or lose the ranking outright.
JOURNEY_BEST_WORST_PERIODS = """
WITH m AS (
    SELECT year_month, month_return,
           ROW_NUMBER() OVER (ORDER BY year_month)      AS rn_asc,
           ROW_NUMBER() OVER (ORDER BY year_month DESC) AS rn_desc
    FROM monthly_returns
    WHERE symbol = :symbol AND month_return IS NOT NULL AND year_month <= :asof_month
),
full_months AS (
    SELECT year_month, month_return FROM m WHERE rn_asc > 1 AND rn_desc > 1
),
y AS (
    SELECT year, year_return
    FROM yearly_summary
    WHERE symbol = :symbol AND is_partial = 0 AND year_return IS NOT NULL
      AND year <= :asof_year
)
-- Each branch is wrapped in its own subquery: in a compound SELECT, SQLite
-- applies a trailing ORDER BY/LIMIT to the WHOLE result, so an unparenthesized
-- `... UNION ALL ... ORDER BY x LIMIT 1` would return one row overall instead
-- of one row per branch.
SELECT * FROM (
    SELECT 'month' AS period_type, 'best' AS extreme,
           year_month AS period, month_return AS period_return
    FROM full_months ORDER BY month_return DESC LIMIT 1)
UNION ALL
SELECT * FROM (
    SELECT 'month', 'worst', year_month, month_return
    FROM full_months ORDER BY month_return ASC LIMIT 1)
UNION ALL
SELECT * FROM (
    SELECT 'year', 'best', year, year_return FROM y ORDER BY year_return DESC LIMIT 1)
UNION ALL
SELECT * FROM (
    SELECT 'year', 'worst', year, year_return FROM y ORDER BY year_return ASC LIMIT 1);
"""

# Trend changes: 50/200 moving-average crossovers.
#
# The crossing session is the one where the SIGN of (ma_50 - ma_200) differs
# from the previous session's, which is why both lagged values are carried. The
# `moving_averages` view returns NULL until each window is full, so the first
# 200 sessions produce no signal at all rather than a spurious one from a
# partial average.
JOURNEY_TREND_CHANGES = """
WITH m AS (
    SELECT date, close, ma_50, ma_200,
           LAG(ma_50)  OVER (ORDER BY date) AS prev_50,
           LAG(ma_200) OVER (ORDER BY date) AS prev_200
    FROM moving_averages
    WHERE symbol = :symbol AND ma_50 IS NOT NULL AND ma_200 IS NOT NULL
)
SELECT date, close,
       CASE WHEN ma_50 > ma_200 THEN 'golden' ELSE 'death' END AS cross_type
FROM m
WHERE prev_50 IS NOT NULL AND prev_200 IS NOT NULL
  AND (ma_50 > ma_200) <> (prev_50 > prev_200)
  AND date <= :asof
ORDER BY date;
"""

# Record-high behaviour over the whole record, as one row.
#
# `longest_dry_spell` is the largest gap in calendar days between consecutive
# record highs -- the single most quotable "how long did it take to get back"
# figure, and one that a reader cannot get by eye from a 25-year chart.
JOURNEY_RECORD_SUMMARY = """
WITH s AS (
    SELECT date, close,
           MAX(close) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
               AS peak_close
    FROM prices
    WHERE symbol = :symbol AND close IS NOT NULL AND close > 0 AND date <= :asof
),
highs AS (
    SELECT date, close,
           LAG(date) OVER (ORDER BY date) AS prev_high_date
    FROM s WHERE close >= peak_close
)
SELECT
    COUNT(*) AS record_days,
    MAX(close) AS record_close,
    (SELECT date FROM highs ORDER BY close DESC, date ASC LIMIT 1) AS record_date,
    (SELECT MAX(julianday(date) - julianday(prev_high_date)) FROM highs) AS longest_dry_spell,
    (SELECT date FROM highs
      WHERE julianday(date) - julianday(prev_high_date)
            = (SELECT MAX(julianday(date) - julianday(prev_high_date)) FROM highs)
      LIMIT 1) AS dry_spell_end_date
FROM highs;
"""

# Curated company events, with the price move the market actually made around
# each one attached.
#
# The join to prices is `date >= e.date` on the FIRST matching session: events
# are dated to the day the news was actionable, and a good fraction of those
# land on a weekend or a holiday. `forward_return` is measured over the next
# `:sessions` sessions from the session before the event, so an event that broke
# before the open is not credited with a move that had already happened.
#
# LEFT JOINs throughout: an event dated before the company's first price row
# (an IPO in 1980, say) must still return its row so the Did You Know panel can
# use it. Only the price columns come back NULL.
JOURNEY_COMPANY_EVENTS = """
WITH e AS (
    SELECT ce.date, ce.title, ce.description, ce.category, ce.source,
           ec.tone, ec.label
    FROM company_events ce
    JOIN event_categories ec ON ec.name = ce.category
    WHERE ce.symbol = :symbol
),
anchored AS (
    SELECT e.*,
           (SELECT p.date FROM prices p
             WHERE p.symbol = :symbol AND p.date >= e.date
             ORDER BY p.date ASC LIMIT 1) AS session_date,
           (SELECT p.close FROM prices p
             WHERE p.symbol = :symbol AND p.date < e.date
             ORDER BY p.date DESC LIMIT 1) AS close_before
    FROM e
)
SELECT
    a.date, a.title, a.description, a.category, a.source, a.tone, a.label,
    a.session_date,
    (SELECT close FROM prices
      WHERE symbol = :symbol AND date = a.session_date) AS close_on,
    a.close_before,
    (SELECT close FROM prices
      WHERE symbol = :symbol AND date >= a.session_date
      ORDER BY date ASC LIMIT 1 OFFSET :sessions) / a.close_before - 1
        AS forward_return
FROM anchored a
WHERE a.date <= :asof
ORDER BY a.date;
"""

# Market-wide events, kept only where THIS company actually moved.
#
# A 25-year chart of any company can be papered over with the same 12 crash
# markers, which teaches nothing about the company: the point of showing the
# 2008 bottom on one name and not another is that one of them fell 80% and the
# other fell 12%. The filter is therefore the company's own realized move across
# the event, not the event's importance to the index. `:min_move` is a bind
# parameter so the threshold is visible in the SQL rather than applied to the
# frame afterwards.
JOURNEY_MARKET_EVENT_IMPACT = """
WITH m AS (
    SELECT date, title, category, description FROM market_events WHERE date <= :asof
),
anchored AS (
    SELECT m.*,
           (SELECT p.close FROM prices p
             WHERE p.symbol = :symbol AND p.date < m.date
             ORDER BY p.date DESC LIMIT 1) AS close_before,
           (SELECT p.close FROM prices p
             WHERE p.symbol = :symbol AND p.date >= m.date
             ORDER BY p.date ASC LIMIT 1 OFFSET :sessions) AS close_after
    FROM m
)
SELECT date, title, category, description,
       close_before, close_after,
       close_after / close_before - 1 AS company_move
FROM anchored
WHERE close_before IS NOT NULL AND close_after IS NOT NULL
  AND ABS(close_after / close_before - 1) >= :min_move
ORDER BY date;
"""

# The price line the Journey draws, thinned to a drawable number of points.
#
# 6,300 sessions is more points than a 380px-tall chart can resolve, and the
# Journey redraws on every playback tick rather than once per page load. Every
# `:stride`-th session is kept, plus the running peak so the all-time-high band
# stays exact -- a thinned series that dropped the peaks would show the record
# line stepping below prices it is supposed to bound.
JOURNEY_PRICE_PATH = """
WITH s AS (
    SELECT date, close,
           MAX(close) OVER (ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
               AS peak_close,
           ROW_NUMBER() OVER (ORDER BY date) AS rn,
           COUNT(*)    OVER () AS total
    FROM prices
    WHERE symbol = :symbol AND close IS NOT NULL AND close > 0
)
SELECT date, close, peak_close,
       (close - peak_close) / peak_close AS drawdown
FROM s
WHERE rn % :stride = 0 OR rn = 1 OR rn = total OR close >= peak_close
ORDER BY date;
"""


# Registered here rather than in the EXPLORER literal above for the same reason
# as the block before it: these statements are defined after that dict.
EXPLORER.update({
    "A company's position at a point in its history": {
        "question": "Where did this company stand on a given date?",
        "sql": JOURNEY_SNAPSHOT,
        "params": ["symbol", "asof"],
        "read_path": "Indexed scan",
        "read_path_note": "Range-scanned per symbol",
        "indexes": ["idx_prices_symbol_date (symbol, date)"],
        "objects": ["prices"],
        "powers": [
            "Research → Journey → the playhead KPI row",
            "Research → Journey → the record-high state chip",
        ],
        "explain": (
            "**What it does.** Returns one row describing where a company stood on "
            "`:asof`: its close, its return and CAGR since its first session, how far "
            "below its running record it was, and whether that day set a new one.\n\n"
            "**Why it exists.** The Journey replays a company forward in time, and "
            "every figure on the page has to describe the SAME instant. Computing them "
            "in one query rather than slicing a cached frame per card is what "
            "guarantees they cannot disagree with each other.\n\n"
            "**How it works.** A running `MAX(close)` over an unbounded preceding frame "
            "gives the all-time high as of each row. The `:asof` row is selected with "
            "`date <= :asof ... LIMIT 1` rather than by equality, because the cursor is "
            "a calendar date and most calendar dates are not trading sessions — an "
            "equality match would blank the panel on every weekend and holiday.\n\n"
            "**The honesty check.** `return_to_date` is measured from the company's "
            "first session in this dataset, which is not its IPO — the record starts "
            "around 2001 whatever the company's actual age. The UI labels it as such."
        ),
    },
    "Every drawdown a company has had": {
        "question": "How far did it fall, how long was it down, and did it recover?",
        "sql": JOURNEY_DRAWDOWN_EPISODES,
        "params": ["symbol", "asof", "min_depth"],
        "read_path": "Window scan",
        "read_path_note": "Gaps-and-islands over the running peak",
        "indexes": ["idx_prices_symbol_date (symbol, date)"],
        "objects": ["prices"],
        "powers": [
            "Research → Journey → drawdown bands on the price path",
            "Research → Journey → recovery periods in Did You Know",
        ],
        "explain": (
            "**What it does.** Returns one row per drawdown episode deeper than "
            "`:min_depth`: the peak it fell from, the trough, the depth, how long it "
            "took to bottom, and the date it made a new high again.\n\n"
            "**Why it exists.** A single 'max drawdown' number says a company once fell "
            "57%; it does not say whether that took three weeks or three years, or "
            "whether the recovery took a decade. The time axis is most of what a "
            "drawdown actually costs an investor.\n\n"
            "**How it works.** Gaps-and-islands. A session closing at the running "
            "maximum opens a new episode, so a cumulative `SUM` of that flag labels "
            "each one; the peak is the episode's first row and the recovery is the "
            "first row of the *next* episode, reached with `LEAD`. The trough's date "
            "needs a join back — `MIN(close)` gives the price but not the day.\n\n"
            "**The honesty check.** `recovery_date` is NULL for an episode that has not "
            "recovered, and it stays NULL. Coalescing it to the last date in the record "
            "would report a company still 40% underwater as fully recovered.\n\n"
            "**Why `:asof` cuts the source rows and not the results.** The Journey "
            "replays history forward, so a cursor in 2003 must not be told that the "
            "crash it is in the middle of recovered in 2012. Bounding the series before "
            "the episodes are built returns a NULL recovery for anything still open at "
            "the cursor — what was actually known at that moment. A filter applied to "
            "finished episodes would leak the future, and `tests_journey.py` asserts "
            "against exactly that."
        ),
    },
    "Longest winning and losing streaks": {
        "question": "What is the longest run of up or down days this company has had?",
        "sql": JOURNEY_STREAKS,
        "params": ["symbol", "asof", "direction", "limit"],
        "read_path": "Window scan",
        "read_path_note": "Gaps-and-islands over daily direction",
        "indexes": ["idx_prices_symbol_date (symbol, date)"],
        "objects": ["daily_returns (view)", "prices"],
        "powers": ["Research → Journey → Did You Know streak facts"],
        "explain": (
            "**What it does.** Finds unbroken runs of up sessions (`:direction` = 1) or "
            "down sessions (-1), longest first, with the return earned across each run."
            "\n\n**How it works.** The difference between a row's position in the whole "
            "series and its position among rows of the same direction is constant "
            "within a run, so that difference labels the run — the standard "
            "gaps-and-islands identity. Flat sessions are dropped *before* the labels "
            "are computed, so an exactly-zero day ends a streak instead of merging two "
            "separate ones into a longer fictitious one.\n\n"
            "**Why the return comes from closes.** Compounding a run's daily returns "
            "needs `EXP(SUM(LN(1+r)))` in SQLite and accumulates float error over a "
            "long run. The close before the run and the close on its last day give the "
            "same figure exactly, in one subquery each."
        ),
    },
    "Company events, with the market's reaction": {
        "question": "What happened at this company, and did the stock move on it?",
        "sql": JOURNEY_COMPANY_EVENTS,
        "params": ["symbol", "asof", "sessions"],
        "read_path": "Indexed scan",
        "read_path_note": "Curated events joined to the nearest session",
        "indexes": ["idx_company_events (symbol, date)", "idx_prices_symbol_date (symbol, date)"],
        "objects": ["company_events", "event_categories", "prices"],
        "powers": [
            "Research → Journey → the historical timeline",
            "Research → Journey → chart annotations",
        ],
        "explain": (
            "**What it does.** Returns the curated events for a company up to `:asof`, "
            "each with the close before it and the return over the following "
            "`:sessions` sessions.\n\n"
            "**Why it exists.** A 25-year chart has craters and spikes in it, and "
            "without labels the reader has to already know what they were. Attaching "
            "the realized move to each event is what lets the page connect a story to a "
            "shape rather than just placing a marker near one.\n\n"
            "**How it works.** Events are dated to the day the news was actionable, and "
            "many of those are weekends or holidays, so each is anchored to the first "
            "session on or after its date. The forward return is measured from the "
            "close *before* the event, so news that broke pre-open is not credited with "
            "a move that had already happened.\n\n"
            "**The honesty check.** The join is a LEFT JOIN and events before the "
            "company's first price row still return, with NULL prices — an IPO in 1980 "
            "is a true fact about the company even though this dataset starts in 2001. "
            "`source` travels with every row so any claim can be re-verified.\n\n"
            "**Extending it.** The events are rows in `company_events`, loaded from "
            "`data/company_events.json` at build time and validated against `symbols` "
            "and the category registry. Adding an event, a company or a whole category "
            "is a data edit plus a rebuild — no application code changes."
        ),
    },
    "Market events that actually moved this company": {
        "question": "Which market-wide crises materially hit this specific company?",
        "sql": JOURNEY_MARKET_EVENT_IMPACT,
        "params": ["symbol", "asof", "sessions", "min_move"],
        "read_path": "Indexed scan",
        "read_path_note": "12 events, two correlated lookups each",
        "indexes": ["idx_prices_symbol_date (symbol, date)"],
        "objects": ["market_events", "prices"],
        "powers": ["Research → Journey → market context on the timeline"],
        "explain": (
            "**What it does.** Takes the 12 market-defining sessions and keeps only "
            "those where this company itself moved at least `:min_move` across the "
            "event window.\n\n"
            "**Why it exists.** Every company's 25-year chart can be papered over with "
            "the same 12 crash markers, which teaches nothing about the company. The "
            "informative fact is that 2008 took one name down 80% and another down 12% "
            "— so the filter is the company's own realized move, not the event's "
            "importance to the index.\n\n"
            "**How it works.** Each event is anchored to the close before it and the "
            "close `:sessions` sessions later, both by correlated subquery; the "
            "threshold is applied in the `WHERE` clause as a bind parameter, so a "
            "reader of the SQL can see exactly what 'materially affected' means. "
            "Applying it to the returned frame instead would hide it."
        ),
    },
})


# ---------------------------------------------------------------------------
# Market Intelligence
# ---------------------------------------------------------------------------
# The scoring statement itself is NOT here: it is generated from the metric
# registry by `ranking.score_sql`, because a hand-written copy would have to be
# re-edited every time a weight moved and would drift out of step with the
# registry silently. These are the fixed reads around it.

INTEL_STATUS = """
SELECT
    (SELECT COUNT(*) FROM intel_symbols)                                   AS n_symbols,
    (SELECT COUNT(DISTINCT symbol) FROM intel_prices)                      AS n_priced,
    (SELECT COUNT(*) FROM intel_fundamentals WHERE asof IS NOT NULL)       AS n_fundamentals,
    (SELECT COUNT(*) FROM intel_analyst)                                   AS n_analyst,
    (SELECT MAX(date) FROM intel_prices)                                   AS last_date,
    (SELECT MAX(asof) FROM intel_fundamentals)                             AS fundamentals_asof
"""
INTEL_STATUS_NOTE = (
    "What the intelligence universe currently holds: how many symbols are "
    "priced, how many carry SEC fundamentals, and the newest session and filing "
    "date behind the rankings."
)

INTEL_SECTORS = """
SELECT sector, COUNT(*) AS n
FROM metric_panel
WHERE sector IS NOT NULL AND sector <> ''
GROUP BY sector
HAVING COUNT(*) >= 3
ORDER BY sector
"""
INTEL_SECTORS_NOTE = (
    "Sectors available to filter on. A sector with fewer than three companies is "
    "withheld: valuation metrics are ranked WITHIN sector, and a percentile "
    "drawn from two peers is arithmetic rather than information."
)

INTEL_PANEL_ROW = """
SELECT * FROM metric_panel WHERE symbol = :symbol
"""
INTEL_PANEL_ROW_NOTE = (
    "Every stored metric for one company, as the ranking engine sees it."
)

INTEL_UNIVERSE = """
SELECT symbol, name, sector, exchange, last_close, market_cap, dollar_volume,
       vol_1y, last_date
FROM metric_panel
ORDER BY market_cap DESC NULLS LAST
"""
INTEL_UNIVERSE_NOTE = (
    "The ranked universe with its size and liquidity, largest first."
)

# --------------------------------------------------------------------------
# One company's recorded history from the WIDE universe.
#
# The core 50 have 25 years of daily bars in `prices`, and every derived series
# the Research page draws comes from a view over that table. The other ~450
# ranked companies have five years in `intel_prices` and no such views, so
# without these two statements a reader who searched one of them saw an empty
# state on Research -> History -- the app holding real data and declining to
# draw it.
#
# The derived columns are computed here rather than in new views ON PURPOSE: the
# ranking engine's schema is not something a presentation change should extend,
# and one statement per read keeps the intel tables exactly as `fetch_intel.py`
# and `build_db.py` leave them.
#
# Every expression is copied from the corresponding core view so the two records
# MEAN the same thing: the 21-session volatility uses the same 21-row frame and
# the same 252-session annualization, and the drawdown is measured from the
# running high with the same unbounded frame. The one honest difference is the
# record's LENGTH -- a "running high" over five years is not an all-time high --
# which is why the page labels a fallback history rather than presenting it as
# equivalent.
# --------------------------------------------------------------------------
INTEL_SYMBOL_HISTORY = """
WITH base AS (
    SELECT
        date, open, high, low, close, volume,
        (close - LAG(close) OVER w) / LAG(close) OVER w AS daily_return,
        MAX(close) OVER (ORDER BY date
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak_close,
        CASE WHEN COUNT(*) OVER w50  = 50  THEN AVG(close) OVER w50  END AS ma_50,
        CASE WHEN COUNT(*) OVER w200 = 200 THEN AVG(close) OVER w200 END AS ma_200
    FROM intel_prices
    WHERE symbol = :symbol
    WINDOW
        w    AS (ORDER BY date),
        w50  AS (ORDER BY date ROWS BETWEEN 49  PRECEDING AND CURRENT ROW),
        w200 AS (ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW)
),
vol AS (
    SELECT date,
           AVG(daily_return) OVER v                AS avg_ret,
           AVG(daily_return * daily_return) OVER v AS avg_sq,
           COUNT(*) OVER v                         AS n
    FROM base
    WHERE daily_return IS NOT NULL
    WINDOW v AS (ORDER BY date ROWS BETWEEN 20 PRECEDING AND CURRENT ROW)
),
win AS (
    SELECT b.date, b.open, b.high, b.low, b.close, b.volume, b.daily_return,
           b.peak_close, b.ma_50, b.ma_200,
           CASE WHEN v.n = 21
                THEN SQRT(MAX(v.avg_sq - v.avg_ret * v.avg_ret, 0)) * SQRT(252)
           END AS ann_volatility_21d
    FROM base b
    LEFT JOIN vol v ON v.date = b.date
    WHERE b.date BETWEEN :start AND :end
)
SELECT date, open, high, low, close, volume, daily_return,
       ma_50, ma_200, ann_volatility_21d,
       (close - peak_close) / peak_close AS drawdown,
       close / (SELECT close FROM win ORDER BY date LIMIT 1) - 1.0 AS cumulative_return
FROM win
ORDER BY date
"""
INTEL_SYMBOL_HISTORY_NOTE = (
    "Five years of daily bars for one company in the wide ranking universe, with "
    "the same derived series the core universe gets from its views: daily return, "
    "50- and 200-session moving averages, 21-session annualized volatility, "
    "drawdown from the running high, and cumulative return rebased to the "
    "window's first session."
)

INTEL_SYMBOL_WINDOW_STATS = """
WITH win AS (
    SELECT * FROM intel_prices
    WHERE symbol = :symbol AND date BETWEEN :start AND :end
),
bounds AS (
    SELECT MIN(date) AS first_date, MAX(date) AS last_date, COUNT(*) AS trading_days
    FROM win
),
r AS (
    SELECT AVG(dr) AS mean_ret, AVG(dr * dr) AS mean_sq, COUNT(*) AS n
    FROM (
        SELECT date,
               (close - LAG(close) OVER (ORDER BY date))
                   / LAG(close) OVER (ORDER BY date) AS dr
        FROM intel_prices WHERE symbol = :symbol
    )
    WHERE dr IS NOT NULL AND date BETWEEN :start AND :end
),
dd AS (
    SELECT MIN((close - peak_close) / peak_close) AS max_drawdown
    FROM (
        SELECT date, close,
               MAX(close) OVER (ORDER BY date
                                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS peak_close
        FROM intel_prices WHERE symbol = :symbol
    )
    WHERE date BETWEEN :start AND :end
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
FROM bounds b, r, dd, win fo, win lc
WHERE fo.date = b.first_date AND lc.date = b.last_date
"""
INTEL_SYMBOL_WINDOW_STATS_NOTE = (
    "The same window summary the core universe gets from WINDOW_STATS -- return, "
    "CAGR, high, low, volatility, max drawdown -- computed over intel_prices for "
    "a company outside the 50-symbol core. Returns no row when the window holds "
    "no session, which is what the page tests before it draws anything."
)

INTEL_SYMBOL_BOUNDS = """
SELECT MIN(date) AS first_date, MAX(date) AS last_date, COUNT(*) AS sessions
FROM intel_prices WHERE symbol = :symbol
"""
INTEL_SYMBOL_BOUNDS_NOTE = (
    "How much recorded history the wide universe actually holds for one company. "
    "The page states the span rather than implying the five-year record is the "
    "company's whole life."
)


# Registered here rather than in the EXPLORER literal above for the same reason
# the Journey entries are: these read the intel_* tables, which are built from a
# separate artifact and are empty on a clone that has not run fetch_intel.py.
EXPLORER.update({
    "Intelligence universe coverage": {
        "question": "What does the ranking engine actually have data for?",
        "sql": INTEL_STATUS,
        "params": [],
        "read_path": "Four correlated aggregates",
        "read_path_note": "Counts only; no scan of the price rows",
        "indexes": [],
        "objects": ["intel_symbols", "intel_prices", "intel_fundamentals", "intel_analyst"],
        "powers": ["Intelligence → the universe line under Methodology"],
        "explain": (
            "**What it does.** Reports how much of the intelligence universe is "
            "priced, how much carries SEC fundamentals, and how fresh both are.\n\n"
            "**Why it exists.** The ranking board is meaningless without knowing "
            "what it ranked. A page that shows fifty confident scores while forty "
            "of the universe's names silently lack fundamentals is reporting data "
            "availability as if it were opportunity — so the coverage figures are "
            "queried and shown rather than assumed."
        ),
    },
    "Intelligence sector coverage": {
        "question": "Which sectors have enough companies to rank within?",
        "sql": INTEL_SECTORS,
        "params": [],
        "read_path": "Indexed group-by",
        "read_path_note": "idx_metric_panel_sector",
        "indexes": ["idx_metric_panel_sector (sector)"],
        "objects": ["metric_panel"],
        "powers": ["Intelligence → the Sector filter"],
        "explain": (
            "**What it does.** Lists the sectors offered as filters, with their "
            "company counts.\n\n"
            "**Why the HAVING clause.** Valuation metrics are ranked *within* "
            "sector, so a sector with two members produces percentiles of 0% and "
            "100% whatever the two companies are actually worth. Below three "
            "members the sector is withheld from the filter rather than offered "
            "with a percentile that is arithmetic instead of information."
        ),
    },
    "Intelligence metric panel for one company": {
        "question": "What does the ranking engine know about a single company?",
        "sql": INTEL_PANEL_ROW,
        "params": ["symbol"],
        "read_path": "Primary-key lookup",
        "read_path_note": "idx_metric_panel (symbol)",
        "indexes": ["idx_metric_panel (symbol)"],
        "objects": ["metric_panel"],
        "powers": ["Intelligence → the metric breakdown panel"],
        "explain": (
            "**What it does.** Returns every stored metric for one symbol — the "
            "exact row the scoring SQL reads.\n\n"
            "**Why it is a materialized table.** `metric_panel` rolls a dozen "
            "window aggregates over every session of every symbol in the "
            "universe. As a live view the ranking board would recompute all of "
            "it on every filter change; the inputs only move when the database "
            "is rebuilt, so there is nothing to invalidate. Same reasoning as "
            "`symbol_stats` and `latest_quote`.\n\n"
            "**Note.** The symbol picker lists the 50-symbol core universe. The "
            "intelligence universe is wider, and every core symbol is forced "
            "into it, so every option here resolves."
        ),
    },
})

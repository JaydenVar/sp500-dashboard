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
            "Performance → Long-run performance table",
            "Risk → Risk vs return scatter",
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
            "Market → Advancing / declining breadth",
            "Market → Top gainer and worst decliner cards",
            "Market → Movers table",
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
        "powers": ["Market → Sector performance chart"],
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
            "Companies → Comparison chart",
            "Companies → Indexed comparison table",
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
            "Overview → KPI row",
            "Companies → Company metric cards",
            "Risk → Risk metric cards",
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
        "powers": ["Companies → Moving averages chart"],
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
        "powers": ["Companies → Cumulative return chart"],
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
            "Companies → Rolling volatility chart",
            "Risk → Rolling volatility chart",
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
            "Performance → Calendar-year returns chart",
            "Performance → Partial-year flagging",
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

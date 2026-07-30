"""All analysis in this project happens in SQL. Python only runs these queries and plots results."""

PRICES_IN_RANGE = """
SELECT date, close, volume
FROM prices
WHERE date BETWEEN :start AND :end
ORDER BY date;
"""

DRAWDOWN_IN_RANGE = """
SELECT date, close, peak_close, drawdown
FROM drawdowns
WHERE date BETWEEN :start AND :end
ORDER BY date;
"""

VOLATILITY_IN_RANGE = """
SELECT date, ann_volatility_21d
FROM rolling_volatility
WHERE date BETWEEN :start AND :end
ORDER BY date;
"""

YEARLY_SUMMARY = """
SELECT year, open_close, close_close, year_high, year_low, avg_volume,
       trading_days, year_return, is_partial
FROM yearly_summary
ORDER BY year;
"""

MONTHLY_RETURNS = """
SELECT year, month, month_return
FROM monthly_returns
ORDER BY year, month;
"""

HEADLINE_STATS = """
WITH bounds AS (
    SELECT MIN(date) AS first_date, MAX(date) AS last_date FROM prices
),
first_last AS (
    SELECT
        (SELECT close FROM prices WHERE date = b.first_date) AS first_close,
        (SELECT close FROM prices WHERE date = b.last_date) AS last_close,
        b.first_date,
        b.last_date,
        (SELECT COUNT(*) FROM prices) AS trading_days
    FROM bounds b
),
peak AS (
    SELECT MAX(close) AS all_time_high FROM prices
),
current_dd AS (
    SELECT drawdown FROM drawdowns ORDER BY date DESC LIMIT 1
),
worst_dd AS (
    SELECT MIN(drawdown) AS max_drawdown FROM drawdowns
),
vol AS (
    SELECT STDEV_daily AS daily_vol FROM (
        SELECT
            SQRT(AVG(daily_return * daily_return) - AVG(daily_return) * AVG(daily_return)) AS STDEV_daily
        FROM daily_returns
        WHERE daily_return IS NOT NULL
    )
)
SELECT
    fl.first_date,
    fl.last_date,
    fl.first_close,
    fl.last_close,
    fl.trading_days,
    p.all_time_high,
    cd.drawdown AS current_drawdown,
    wd.max_drawdown,
    v.daily_vol * SQRT(252) AS ann_volatility_full_period,
    POWER(fl.last_close / fl.first_close, 1.0 / ((julianday(fl.last_date) - julianday(fl.first_date)) / 365.25)) - 1 AS cagr
FROM first_last fl, peak p, current_dd cd, worst_dd wd, vol v;
"""

YTD_RETURN = """
WITH this_year AS (
    SELECT strftime('%Y', date) AS yr FROM prices ORDER BY date DESC LIMIT 1
),
year_start AS (
    SELECT MIN(date) AS d FROM prices WHERE strftime('%Y', date) = (SELECT yr FROM this_year)
),
latest AS (
    SELECT MAX(date) AS d FROM prices
)
SELECT
    (SELECT close FROM prices WHERE date = (SELECT d FROM year_start)) AS start_close,
    (SELECT close FROM prices WHERE date = (SELECT d FROM latest)) AS end_close,
    ((SELECT close FROM prices WHERE date = (SELECT d FROM latest)) -
     (SELECT close FROM prices WHERE date = (SELECT d FROM year_start))) /
     (SELECT close FROM prices WHERE date = (SELECT d FROM year_start)) AS ytd_return;
"""

DATE_BOUNDS = """
SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM prices;
"""

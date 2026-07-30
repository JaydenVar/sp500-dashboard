# S&P 500 — 25-Year Dashboard

A SQL-first dashboard over ~25 years of daily S&P 500 (`^GSPC`) history.

All statistics are computed **in SQL** against a local SQLite database. Python
runs the queries and draws the results; it does no analysis of its own.

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install streamlit plotly pandas
```

## Run

```bash
./.venv/bin/python fetch_data.py    # download daily history -> data/sp500_daily.csv
./.venv/bin/python build_db.py      # load CSV + create SQL views -> data/sp500.db
./.venv/bin/streamlit run app.py    # open the dashboard
```

`fetch_data.py` and `build_db.py` only need re-running to refresh the data.

## Files

| File | Role |
|---|---|
| `fetch_data.py` | Pulls ~25y of daily OHLCV from Yahoo Finance into `data/sp500_daily.csv` |
| `build_db.py` | Loads the CSV into SQLite and defines every analysis view in SQL |
| `queries.py` | The SQL the dashboard runs — one place to read all the analysis |
| `db.py` | Connection helper; registers `SQRT`/`POWER` (stock SQLite lacks them) |
| `theme.py` | Light/dark palette values and page CSS |
| `app.py` | Streamlit UI — runs the queries, draws the charts |

## The SQL layer

`build_db.py` creates one table and five views. The views are where the analysis
lives:

| View | What it computes |
|---|---|
| `prices` | base table — one row per trading day |
| `daily_returns` | day-over-day return via `LAG(close) OVER (ORDER BY date)` |
| `running_peak` | running all-time high via `MAX(close) OVER (…UNBOUNDED PRECEDING…)` |
| `drawdowns` | `(close − peak) / peak` — how far below the all-time high |
| `yearly_summary` | per-year open/close/high/low, avg volume, return, partial-year flag |
| `monthly_returns` | per-month return, used for the seasonality heatmap |
| `rolling_volatility` | annualized volatility over a 21-session rolling window |

Headline figures (CAGR, max drawdown, full-period volatility) come from
`queries.HEADLINE_STATS`, a single query. The **Data & SQL** tab in the app shows
every query verbatim next to the numbers it produced.

## Dashboard

A filter row (date-range presets, light/dark) scopes everything below it, then a
KPI row and four tabs:

- **Overview** — index level (linear/log) and daily volume
- **Returns** — calendar-year return bars and a month-by-year seasonality heatmap
- **Risk** — drawdown-from-peak and rolling volatility
- **Data & SQL** — yearly table, CSV export, and the SQL behind every figure

## Notes on the data

- Source is Yahoo Finance's chart API (no API key). It returns index price data;
  returns here are **price returns, not total returns** — dividends are excluded.
- The window is the last ~25 years, so the **first and last calendar years are
  partial**. Their bars are faded and marked `*`, and the yearly table flags them,
  because a part-year figure is not a calendar-year return.
- KPI labels state their own scope: `25Y CAGR` and `Max drawdown (25Y)` are
  full-period and do not change with the date filter; `Current close`,
  `Current drawdown` and `Ann. volatility (21d)` are as-of the latest session.

## Visual design

Colors come from a validated palette (blue/red — the documented diverging pair)
and were checked against the six data-viz color rules in both light and dark mode:
OKLCH lightness band, chroma floor, colorblind separation (Machado 2009 protan/
deutan simulation), normal-vision separation floor, and WCAG contrast against each
mode's surface. All pass — worst-case colorblind ΔE 19.2 against a floor of 8.

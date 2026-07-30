# Market Analytics — a SQL-first equity dashboard

25 years of daily history for the S&P 500 index and 49 large-cap US equities.
**Every figure on screen is the output of a SQL query** — the analysis lives in
SQLite views and materialized rollups, and Python only queries and draws. The
built-in **SQL Explorer** page proves it: pick any query and see the SQL, an
explanation of what it does, the rows it returns, and its measured runtime.

## Setup

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## Run

```bash
./.venv/bin/streamlit run app.py
```

That's it — on first launch the app builds its own database from the committed
CSVs. To refresh the market data:

```bash
./.venv/bin/python fetch_data.py    # download daily history (resumable)
./.venv/bin/python build_db.py      # rebuild the SQLite database
```

## Sections

| Section | What's in it |
|---|---|
| **Overview** | S&P 500 quote strip, KPI row, index level (linear/log), volume |
| **Market** | Breadth (advancing/declining), sector performance, full movers table |
| **Companies** | Ticker/name search, company overview, price + MAs + volume + returns + cumulative return + rolling volatility, and multi-symbol comparison |
| **Performance** | Long-run leaderboard, calendar-year returns, monthly seasonality |
| **Risk** | Drawdown curve, rolling volatility, risk-vs-return scatter |
| **SQL Explorer** | Every query, explained, with live timings and the schema |
| **About** | Architecture and an honest account of the data's limits |

Controls sit in one row above everything they scope: date presets
(1M / 3M / 6M / YTD / 1Y / 3Y / 5Y / 10Y / MAX), a light/dark toggle, and Reset view.

## Architecture

```
fetch_data.py   Yahoo Finance chart API  ->  data/prices.csv + data/symbols.csv
build_db.py     CSVs  ->  SQLite: tables, views, materialized rollups
queries.py      every SQL statement, each paired with an explanation
data_access.py  cached query layer (st.cache_data) — the only place SQL is run
charts.py       Plotly builders + one shared styling function
components.py   header, KPI cards, quote strip, sortable/paginated table
theme.py        validated palettes and the whole stylesheet
app.py          layout and interaction only — no analysis
```

### The SQL layer

| Object | Kind | What it computes |
|---|---|---|
| `prices` | table | one row per symbol per session |
| `symbols` | table | name, sector, industry |
| `daily_returns` | view | `LAG(close) OVER (PARTITION BY symbol …)` |
| `drawdowns` | view | running peak via `MAX(…) OVER (… UNBOUNDED PRECEDING)`, and distance below it |
| `moving_averages` | view | trailing 50/200-session means, NULL until the frame is full |
| `rolling_volatility` | view | 21-session annualized σ from `E[r²] − E[r]²` |
| `yearly_summary` | view | per-year OHLC, return, partial-year flag |
| `monthly_returns` | view | per-month return (seasonality grid) |
| `symbol_stats` | **materialized** | one row per symbol: CAGR, volatility, max drawdown, liquidity |
| `latest_quote` | **materialized** | newest session, prior close, trailing 52-week range |

### Performance

Two rollups are materialized at build time rather than computed per request,
because both aggregate window functions over all ~300k rows:

| Query | As a live view | Materialized |
|---|---|---|
| Leaderboard | ~2,100 ms | **~1 ms** |
| Quote snapshot | ~286 ms | **~0.4 ms** |

On top of that, every read is memoized with `st.cache_data`, and the app renders
only the active section — so switching sections doesn't re-run the other six
sections' queries.

`SQRT`, `POWER`, `LN`, `EXP` and a custom `MEDIAN` aggregate are registered in
`db.py`, since Python's bundled SQLite ships without the math functions.

## Design decisions worth knowing

**No dual-axis charts, ever.** Comparing symbols at different price levels
rebases every series to 100 on one shared axis. Two independent y-scales can be
aligned to imply any correlation you like — it's the most common way a finance
chart misleads. When series end more than 20x apart the axis switches to log, so
equal ratios get equal vertical space instead of the smaller series flattening
onto the baseline.

**Median, not mean, for sector performance.** Over long windows a mean of total
returns is dominated by one outlier: a single +85,000% name drags a sector
"average" into five figures, describing that stock rather than the sector.

**Colors are validated, not chosen by eye.** Series colors pass, per theme, an
OKLCH lightness band, a chroma floor, colorblind separation (Machado 2009
protan/deutan at full severity), a normal-vision separation floor, and WCAG
contrast against that theme's surface. Identity never rests on color alone —
multi-series charts carry a legend, direction chips pair an arrow with the color,
and every chart has a table view or CSV export.

## Honest limits

Things this data genuinely cannot support, stated rather than papered over:

- **Price returns, not total returns.** Dividends are excluded, so long-run
  figures understate what a shareholder actually earned.
- **No market cap.** It needs share counts, and every free endpoint that serves
  them is auth-gated (401/404). Rather than hardcode a figure that goes stale,
  the app shows average dollar turnover, computed from data actually present.
- **Unequal histories.** Symbols listed later (META 2012, TSLA 2010) have shorter
  records, so all-time leaderboards show `Years` and `From` instead of silently
  ranking unequal periods. Use the Market tab for like-for-like window returns.
- **Sectors are equal-weighted**, not market-cap-weighted, for the same reason.
- **Survivorship bias.** The universe is a fixed list of companies that exist
  today, omitting firms that failed or were acquired — which flatters long-run
  returns.
- **Market status is schedule-based** (weekday 09:30–16:00 ET); exchange
  holidays are not modelled, and the label says so.
- **Partial calendar years** at each end of the window are flagged and faded.

## Notes on fetching

Yahoo's chart API rate-limits bursts with HTTP 429. Two things matter:

- `fetch_data.py` caches each symbol under `data/cache/` the moment it arrives,
  so a throttled run loses no completed work — re-run and it fetches only what's
  missing.
- The request sends a bare `User-Agent: Mozilla/5.0`. Measured: a *full* Chrome
  UA plus a `Referer` gets 429'd, apparently routing into a stricter path that
  expects a real browser session. Don't "improve" it into a realistic browser
  string.

## Data source

Yahoo Finance chart API (unauthenticated), daily closes. This project is for
analysis and portfolio demonstration — it is not investment advice.

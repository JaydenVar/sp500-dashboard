# Market Analytics — a SQL-first equity platform

25 years of daily history for the S&P 500 index and 49 large-cap US equities.
**Every figure on screen is the output of a SQL query** — the analysis lives in
SQLite views and materialized rollups, and Python only queries and draws.

The app ships as **two separate experiences**: a *User Mode* that behaves like a
financial product, and a *Developer Center* documenting the engineering behind
it. The **SQL Explorer** there presents each query as the business question it
answers, with the code one click away — and **Interview Mode** answers the
questions an interviewer actually asks.

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

## Two modes

The app deliberately separates the product from the engineering. Toggle in the
top-right.

**User Mode** — a financial application. No SQL, schemas or query timings appear
anywhere.

| Section | What's in it |
|---|---|
| **Overview** | *Ask the Market* search, index quote, KPIs, price with market events, volume |
| **Market** | Breadth, sector performance, full movers table |
| **Companies** | Ticker/name search, company page, price, moving averages, volume, returns, cumulative return, volatility, drawdown, and multi-metric comparison |
| **Journey** | *Stock Journey* — travel through one company's whole history with playback, a live timeline and "Did you know?" facts |
| **Performance** | Long-run leaderboard, calendar-year returns, monthly seasonality |
| **Risk** | Drawdown curve, rolling volatility, risk-vs-return scatter |
| **Portfolio** | Weighted basket simulator with return, CAGR, volatility and drawdown |
| **About** | What the metrics mean and the limitations that affect reading them |

**Developer Center** — everything technical, organized for someone evaluating
the engineering: Overview, Project Architecture, Database Schema, SQL Explorer,
Performance, Technology Stack, **Interview Mode**, About the Project.

### Ask the Market

A natural-language search bar answering questions like *"What stock had the
highest trading volume?"*, *"Compare Apple and Microsoft"*, or *"What company had
the biggest drawdown?"*.

No LLM: eleven intents are keyword-scored and answered by the same queries the
rest of the app runs, so an answer can never disagree with the page beside it.
Intents are scored rather than first-matched, symbols resolve by ticker *or*
company name, and unmatched input says so instead of guessing.

### Portfolio simulator

Build a weighted basket and see what it would have done. The arithmetic is SQL:
a weighted portfolio's daily return is the weighted average of its holdings',
wealth compounds by log-sum, and drawdown comes from a running peak.

Verified to 1e-9 against an independent pandas recomputation across four
scenarios — including one mixing 2001, 2010 and 2012 listings, which exposed a
real bug: the investable date is the first session where every holding has a
*price*, not a *return*.

Modelling assumptions are stated in the UI: rebalanced to target weights daily,
only sessions where every holding traded, dividends excluded.

### Market timeline

Twelve market-defining sessions — the dot-com bottom, Lehman, the 2009 trough,
the COVID crash and recovery, the inflation cycle — overlay the charts with hover
explanations, so a 25-year chart explains its own craters.

### Stock Journey

Pick a company and travel through its history. A cursor — drag it, press play, or
click the chart, a timeline entry or a fact card — selects a point in the record,
and every panel re-queries at that instant: where the company stood, how far below
its record it was, and what had happened to it so far.

The chart reveals as you travel: solid where you have been, faint where you have
not, on a log axis so a 2008 crash stays visible on a name that has since gone up
a hundredfold. Beneath it, a strip showing the distance below the running
all-time high at every point.

The timeline merges three sources:

1. **Curated company events** — IPOs, splits, acquisitions, CEO changes, product
   launches, crises. 256 of them across all 50 companies, each with a source.
2. **Computed milestones** — crash troughs with their recovery dates, and the
   largest single sessions, derived from the price data.
3. **Market-wide events, filtered to the ones that actually hit this company.**
   Any company's chart could be papered with the same twelve crash markers; the
   informative fact is that 2008 took one name down 80% and another down 12%. So
   the test is the company's own realized move, applied in the SQL.

**Did you know?** updates as you travel: record highs, the longest stretch without
one, best and worst days, months and years, the longest winning and losing streaks,
the deepest crash and how long the round trip took.

#### Adding company events

`data/company_events.json` is data, not code. Add a row, run `build_db.py`, and it
appears — no application changes:

```json
{
  "symbol": "AAPL", "date": "2007-01-09", "title": "iPhone unveiled",
  "description": "Steve Jobs introduced the iPhone at Macworld…",
  "category": "Product Launch", "source": "Apple Newsroom, 9 Jan 2007"
}
```

The loader validates every row against `symbols` and the category registry and
**fails the build** on a bad one, rather than skipping it — a dropped event is
invisible in the UI and looks identical to a company with no history curated yet.
New categories are a row in the same file's `categories` list; their `tone` is what
the palette maps to a color.

## Architecture

```
fetch_data.py   Yahoo Finance chart API  ->  data/prices.csv + data/symbols.csv
build_db.py     CSVs  ->  SQLite: tables, views, materialized rollups
queries.py      every SQL statement, each paired with an explanation
data_access.py  cached query layer (st.cache_data) — the only place SQL is run
charts.py       Plotly builders + one shared styling function
components.py   header, KPI cards, quote strip, chart + table helpers
events.py       market timeline events with hover explanations
journey.py      Stock Journey narrative layer — picks and phrases facts, no math
ask.py          natural-language intent routing (no LLM)
answers.py      renders an answer per intent from existing queries
devcenter.py    the Developer Center, incl. SQL Explorer and Interview Mode
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
| `company_events` | table | curated company history, loaded from `data/company_events.json` |
| `event_categories` | table | the category registry that drives event color and labelling |
| `market_events` | table | the market timeline, mirrored from `events.py` so it can join to prices |

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
- **IPO dates and stock splits are curated, not derived.** The price record
  starts around 2001, so a company's first row is not its listing date — Apple's
  is 2001, not 1980. And Yahoo's `close` is already split-adjusted while
  `adj_close` adjusts for splits *and* dividends, so a split leaves no trace in
  either column. Both therefore come from `data/company_events.json` with a
  source attached, or are not claimed at all. The Journey says "first session in
  this dataset" and never calls it an IPO.
- **Curated events are a best effort, and dated.** Each carries a `source` so it
  can be re-verified, and coverage is deeper for large, well-documented companies
  than for the rest.

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

# MarketLens — a SQL-first equity platform

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

Five pages, each answering one question. Every page carries its own
sub-navigation.

| Page | What's in it |
|---|---|
| **Research** *(landing)* | One search box for **any** US-listed stock, *Ask the Market* directly beneath it, and **Today's Opportunities** — the ranking engine's top names, clickable straight into the panel below. Then: **Snapshot** (live quote, interactive chart, profile, SEC financials, valuation, analyst consensus, trailing performance, news) · **History** (price, moving averages, volume, daily returns, cumulative return, rolling volatility, drawdown, multi-metric peer comparison) · **Journey** (travel through one company's whole history with playback, a live timeline and "Did you know?" facts) |
| **Intelligence** | The ranking engine: ~500 US stocks scored on 33 objective metrics over three horizons, filterable by risk, objective, sector and market cap, with category scores, strengths, risks, a full metric breakdown and the generated SQL |
| **Markets** | **Index** (index quote, KPIs, price with market events, volume) · **Sectors & Movers** (breadth, sector performance, full movers table) · **Performance** (long-run leaderboard, calendar-year returns, rolling returns, monthly seasonality) |
| **Risk & Portfolio** | **Risk** (correlation matrix and risk-vs-return scatter — cross-sectional, no picker) · **Portfolio** (weighted basket simulator with return, CAGR, volatility, drawdown, allocation and return contribution) |
| **About** | What the metrics mean, which of the two universes a figure came from, and the limitations that affect reading them |

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
fetch_intel.py  the wide universe: pick it, price it, fetch its SEC facts
sec.py          SEC EDGAR XBRL client — fundamentals and share counts
live_data.py    Yahoo quote/search/news — the only request-time network calls
build_db.py     CSVs  ->  SQLite: tables, views, materialized rollups
queries.py      every SQL statement, each paired with an explanation
ranking.py      the metric registry and the scoring SQL it generates
data_access.py  cached query layer (st.cache_data) — the only place SQL is run
charts.py       Plotly builders + one shared styling function
components.py   header, KPI cards, quote strip, chart + table helpers
events.py       market timeline events with hover explanations
journey.py      Stock Journey narrative layer — picks and phrases facts, no math
market_intel.py the ranking board, and the top picks the landing page shows
pagectx.py      the frozen per-run context every page module receives
research.py     PAGE: search + Ask, then Snapshot / History / Journey
markets.py      PAGE: Index / Sectors & Movers / Performance
riskfolio.py    PAGE: Risk / Portfolio
about.py        PAGE: what the numbers mean
ask.py          natural-language intent routing (no LLM)
answers.py      renders an answer per intent from existing queries
devcenter.py    the Developer Center, incl. SQL Explorer and Interview Mode
theme.py        validated palettes and the whole stylesheet
app.py          chrome only — theme, navigation, the shared window, five calls
```

**One page, one module.** `app.py` used to hold every section inline at 1,538
lines, which meant a change to one page could break any other. It is now ~320
lines that resolve the theme, the company directory and the shared date window,
build the navigation and dispatch. Each page module declares its own `VIEWS` and
its own `WINDOWED` tuple — the views that actually read the shared window — so
the window control appears exactly where it applies and cannot fall out of date.

### Research — any US-listed company

One search box resolves any stock on NYSE, Nasdaq or NYSE American — not just
the 50 in the core universe. It checks the local catalog first (the core 50 plus
the ~500 ranked names), so the common case answers from SQLite in under a
millisecond rather than a network round trip per keystroke, and falls through to
the provider for anything else.

**Snapshot** is live: quote, an interactive chart across eight spans, the company
profile, key financials from SEC filings, valuation, trailing performance and
recent headlines.

**History** is the recorded daily record. The core 50 carry 25 years; the rest of
the ranked universe carries five, computed with the same metric definitions, and
the shorter record is labelled with its actual span rather than presented as
equivalent — a "max drawdown" over five years is a different statement from one
over twenty-five.

**Journey** replays a company's whole record forward in time under a cursor.

### Market Intelligence

**The intelligence engine** ranks the wide universe on objective metrics across
three horizons, filterable by risk tolerance, investment objective, sector and
market cap. Every result carries its overall score, per-category scores, the
metrics it is strongest and weakest on, a full metric breakdown, and an
explanation of why it landed there.

The scoring is deliberately auditable:

- **Percentile rank within the filtered universe**, not z-scores. Financial
  cross-sections are heavily skewed, and one 400x P/E moves a mean and a standard
  deviation enough to compress everything else toward the middle. A percentile
  cannot be moved by an outlier at all.
- **Valuation ranks within sector.** Pooling P/E across utilities and software
  ranks sectors, not companies.
- **Missing data renormalizes; it is never zero-filled.** A stock with no
  fundamentals would otherwise score 0 on every valuation metric and rank as
  expensive rather than as unknown — which turns the board into a ranking of data
  availability. Below 60% metric coverage a stock is excluded and told why.
- **Horizon weights follow the published anomaly literature.** One-month return
  is scored *inverted* (Jegadeesh 1990, short-horizon reversal); medium-term is
  anchored on 12-1 momentum, which skips the most recent month precisely to avoid
  that reversal (Jegadeesh & Titman 1993); five-year return is inverted again on
  the long board (De Bondt & Thaler 1985).
- **The AI never ranks.** Scores come from SQL over reported data. The model is
  handed the finished result and asked to explain it — and the page works
  without a key, falling back to an explanation assembled from the same figures.

Weights and metrics live in two registries in `ranking.py`, which *generates* the
scoring SQL. Retuning a weight or adding a metric is a registry edit plus one
column in the `metric_panel` view — the scoring logic is never touched. The
generated statement for the current board is shown under **Methodology**.

Rankings refresh automatically when market data does: `build_db` stamps a
`data_version` derived from the data itself, every cached ranking read takes it
as an argument, and a new load is therefore a cache miss. A TTL cannot do this
job — it expires on a clock rather than on the data, so it both recomputes
unchanged boards and serves stale ones after a refresh.

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
| `intel_symbols` | table | the wide universe: ticker, CIK, SIC-derived sector, exchange |
| `intel_prices` | table | 5 years of daily bars for that universe |
| `intel_fundamentals` | table | raw SEC XBRL figures, as filed — no ratios |
| `intel_analyst` | table | consensus data; empty unless a provider key is set |
| `intel_seq` | view | sessions numbered backwards from the latest, so "22 sessions ago" is a join key rather than a date offset |
| `intel_returns` | view | daily returns for the wide universe, likewise numbered |
| `intel_price_metrics` | view | every price-derived metric: momentum, trend, RSI, volatility, beta, drawdown, liquidity |
| `intel_fundamental_metrics` | view | every ratio, derived from the raw figures — negative-denominator cases NULLed rather than computed |
| `metric_panel` | **materialized** | one row per symbol, one column per registered metric — what the ranking engine scores |

The intel tables are deliberately **separate from `prices`/`symbols`**. Those
feed the leaderboard, sector medians and period movers; dropping 500 five-year
names into them would silently change every one of those figures on pages that
have nothing to do with this feature.

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

**One date window for the whole app, and its control appears only where it
applies.** A single window means moving between pages keeps every figure
describing the same period, which is what makes reading across them trustworthy.
Independent per-page pickers were considered and rejected: they turn each
navigation into a silent re-baselining. What the control must not do is claim a
reach it lacks, so it is hidden on views that set their own period — Snapshot has
its own span control, a Journey is the company's whole record by construction,
Performance is all-time, and the ranking engine scores fixed trailing windows.
The banner names the view it is scoping right now, or says plainly that this one
sets its own.

**Navigation is a radio, never tabs** — at both levels. `st.tabs` renders every
tab's body on every rerun, so it would run all of a page's queries when one view
is visible and make Plotly measure charts inside hidden containers, rendering
them at a fraction of the container width.

## Honest limits

Things this data genuinely cannot support, stated rather than papered over:

- **Price returns, not total returns.** Dividends are excluded, so long-run
  figures understate what a shareholder actually earned.
- **Market cap comes from the ranked universe only.** It needs share counts, and
  every *Yahoo* endpoint serving them is auth-gated (401). SEC EDGAR serves them
  from the filings themselves, so the intelligence universe computes cap as
  shares outstanding × latest close on every rebuild. The core 50-symbol pages
  still show average dollar turnover instead, because they are built from the
  price artifact alone and a figure that appears on one page and not another is
  less confusing than one that is stale on half of them.
- **Fundamentals lag the market.** SEC figures are as-filed, so a ratio can be up
  to a quarter behind the price it is divided by.
- **Intelligence sectors are SIC-derived, not GICS.** SEC classifies filers with
  a 1987 scheme mapped here to modern sector names — close, not official. The
  core 50 keep their hand-curated GICS-style labels.
  This has a real consequence, because valuation is ranked *within* sector: Lam
  Research (`LRCX`) files under SIC 3559 "Special Industry Machinery" and lands
  in **Industrials**, while its direct peers Applied Materials and KLA land in
  Information Technology. Its valuation percentile is therefore drawn against
  industrial machinery rather than semiconductor equipment. Every SIC-derived
  sector carries this class of error; the fix is a real classification source,
  not a longer override table. Named here rather than buried because a reader
  comparing `LRCX` to `AMAT` on the board will notice, and should know why.
- **Some issuers carry no fundamentals at all.** A company that reorganized has a
  successor CIK with no XBRL history behind its ticker (ExxonMobil is the live
  case). Those names rank on price metrics and are excluded from any horizon
  whose coverage they cannot meet, rather than being scored as if unlevered.
- **Unequal histories.** Symbols listed later (META 2012, TSLA 2010) have shorter
  records, so all-time leaderboards show `Years` and `From` instead of silently
  ranking unequal periods. Use Markets → Sectors & Movers for like-for-like window returns.
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

### Fetching the intelligence universe

Three resumable stages, each safe to run alone:

```bash
python fetch_intel.py --universe   # SEC frames pre-screen -> candidate list
python fetch_intel.py --prices     # 5y daily history, then a liquidity cut
python fetch_intel.py --facts      # SEC companyfacts -> fundamentals
python build_db.py                 # load everything, rebuild metric_panel
```

Universe selection is two-stage on purpose. SEC's `frames` API ranks every filer
on one concept in a single request, which is what makes a wide universe
affordable at all — but it can only see reported fundamentals, and a company
with large revenue can still be untradeable. So frames pre-screens on revenue
*and* assets (assets catches banks and insurers, whose revenue understates their
size), and the final cut is by realized dollar turnover.

One line per company. A single CIK owns every listed line an issuer has — common
stock, each preferred series, warrants, structured notes — so the primary ticker
is chosen by traded volume rather than by pattern. Without that, JPMorgan enters
the universe as `VYLD` and Bank of America as `MER-PK`, each carrying the
parent's fundamentals under a ticker nobody researches.

`fetch_intel.py` is optional: a clone that has never run it builds a working
database with empty intelligence tables, and the page renders an empty state
naming the command. Every other section is unaffected.

## Data source

Yahoo Finance chart API (unauthenticated) for prices, quotes, symbol search and
headlines; SEC EDGAR XBRL (unauthenticated) for fundamentals, share counts and
classification. Both are keyless, which is what keeps the public deploy working
with no secrets set. This project is for analysis and portfolio demonstration —
it is not investment advice.

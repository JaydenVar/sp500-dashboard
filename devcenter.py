"""Developer Center — everything technical, kept out of the user-facing app.

User Mode is a financial product: it never shows SQL, schemas, timings or
implementation detail. Everything of that kind lives here, organized for a
reader who is evaluating the engineering rather than the markets.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import components as ui
import data_access as dal
import queries
from build_db import REQUIRED_OBJECTS, SCHEMA_VERSION

SECTIONS = [
    "Overview",
    "Project Architecture",
    "Database Schema",
    "SQL Explorer",
    "Query Router",
    "Performance",
    "Technology Stack",
    "Interview Mode",
    "About the Project",
]


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def _overview(directory: pd.DataFrame) -> None:
    ui.section("Developer Center", "How this application is built")
    ui.note(
        "User Mode is the product; this is the engineering behind it. Nothing here "
        "is shown to a normal user — no SQL, no schema, no timings."
    )

    n_symbols = len(directory)
    rows = dal.row_count()
    ui.kpi_cards([
        {"icon": "🗄", "label": "Database engine", "value": "SQLite", "small": True,
         "foot": "Portable file; Postgres-ready SQL"},
        {"icon": "📊", "label": "Price rows", "value": f"{rows:,}",
         "foot": f"{n_symbols} symbols, daily bars"},
        {"icon": "🧱", "label": "SQL objects", "value": f"{len(REQUIRED_OBJECTS)}",
         "foot": "Tables, views and rollups"},
        {"icon": "🏷", "label": "Schema version", "value": f"v{SCHEMA_VERSION}",
         "foot": "Stamped; stale builds self-heal"},
    ])

    st.markdown(
        """
#### What this project demonstrates

| Area | Where to look |
|---|---|
| **SQL** — window functions, CTEs, custom aggregates | *SQL Explorer* |
| **Database design** — normalization, indexing, materialization | *Database Schema* |
| **Data engineering** — resumable ingestion, idempotent builds | *Project Architecture* |
| **Query optimization** — measured 2,100 ms → 1 ms | *Performance* |
| **Data visualization** — chart-form selection, accessible color | *User Mode* |
| **Financial analysis** — CAGR, drawdown, volatility, portfolios | *User Mode* |

The guiding rule for the whole codebase: **every number on screen is the output
of a SQL query.** Python runs the queries and draws the results; it performs no
analysis of its own. That constraint is what makes the SQL worth reading.
"""
    )


# ---------------------------------------------------------------------------
# Architecture
# ---------------------------------------------------------------------------
def _architecture() -> None:
    ui.section("Project Architecture", "How data flows from source to screen")

    st.markdown(
        """
```
Yahoo Finance chart API
        │  fetch_data.py — resumable, rate-limit aware
        ▼
data/cache/<symbol>.csv        (per-symbol, written on arrival)
        │
        ▼
data/prices.csv.gz + data/symbols.csv     (committed to git, 7.6 MB)
        │  build_db.py — schema, views, materialized rollups, ANALYZE
        ▼
data/sp500.db          SQLite: 2 tables · 6 views · 2 rollups
        │  queries.py  — every SQL statement, each with an explanation
        │  data_access.py — the only module that opens a connection
        ▼
app.py + charts.py + components.py       layout and drawing only
```

#### Layer responsibilities

| Module | Owns | Deliberately does **not** |
|---|---|---|
| `fetch_data.py` | Network I/O, retries, caching | Any analysis |
| `build_db.py` | Schema, views, rollups, versioning | Serving queries |
| `queries.py` | Every SQL statement + its explanation | Executing anything |
| `data_access.py` | Connections, caching, timing | Formatting or drawing |
| `charts.py` | Plotly construction and styling | Querying |
| `app.py` | Layout, routing, interaction | **Computing any metric** |

#### Three design decisions worth defending

**1. Analysis lives in SQL views, not Python.**
Daily returns, running-peak drawdowns, moving averages and rolling volatility are
all window functions in the database. The alternative — pulling rows into pandas
and computing there — would have been faster to write and would have made the
SQL layer decorative. Keeping it in SQL means the logic ports to Postgres
unchanged and the *SQL Explorer* has something real to show.

**2. Ingestion is resumable, because the source rate-limits.**
Yahoo returns HTTP 429 to bursts. Each symbol's rows are written to a cache file
the moment they arrive, so a throttled run loses no completed work — re-running
fetches only what is missing. A naive loop lost all 50 symbols to one 429.

**3. The build is idempotent and self-healing.**
`build_db.py` stamps a `schema_version` and writes to a temp file that is
atomically swapped in. The app validates that stamp before trusting an existing
database. This was not theoretical: a deployed container reused a database from
an older schema and every query failed with `no such table`. Checking existence
alone is not enough — the schema has to be verified.
"""
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def _schema() -> None:
    ui.section("Database Schema", "Tables, views and materialized rollups")

    st.markdown(
        """
#### Base tables

```sql
prices (                          symbols (
  symbol   TEXT NOT NULL,           symbol   TEXT PRIMARY KEY,
  date     TEXT NOT NULL,           name     TEXT NOT NULL,
  open     REAL,                    sector   TEXT,
  high     REAL,                    industry TEXT,
  low      REAL,                    is_index INTEGER NOT NULL
  close    REAL,                  )
  adj_close REAL,
  volume   INTEGER,
  PRIMARY KEY (symbol, date)      -- composite: one bar per symbol per day
)
```

The composite primary key is the integrity constraint that matters here: it makes
a duplicate bar impossible, and it lets the ingest use `INSERT OR REPLACE` so a
re-run is idempotent rather than duplicating history.

#### Indexes

| Index | Columns | Serves |
|---|---|---|
| *(implicit PK)* | `(symbol, date)` | Every per-symbol time-series query |
| `idx_prices_symbol_date` | `(symbol, date)` | Range scans within one symbol |
| `idx_prices_date` | `(date)` | Cross-sectional queries (all symbols on a date) |
| `idx_symbol_stats` | `(symbol)` | Leaderboard lookups |

The two access patterns are genuinely different — *one symbol across time* and
*all symbols at one time* — which is why both index shapes exist.
"""
    )

    st.markdown("#### Live schema, read from the database")
    schema, _ = dal.timed_read(
        "SELECT type, name FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY "
        "CASE type WHEN 'table' THEN 0 WHEN 'view' THEN 1 ELSE 2 END, name;"
    )
    st.dataframe(schema, use_container_width=True, hide_index=True, height=320)

    st.markdown(
        """
#### Views vs materialized rollups

`daily_returns`, `drawdowns`, `moving_averages`, `rolling_volatility`,
`yearly_summary` and `monthly_returns` are **views** — always consistent with the
base table, computed on read.

`symbol_stats` and `latest_quote` are **materialized tables** built once at load
time. They aggregate window functions over every row, which is far too expensive
to repeat per request. Their inputs only change when the database is rebuilt, so
there is nothing to invalidate. See *Performance* for the measurements.
"""
    )


# ---------------------------------------------------------------------------
# SQL Explorer — one query, told as a story
# ---------------------------------------------------------------------------
def _sql_explorer(directory: pd.DataFrame, start: str, end: str) -> None:
    ui.section("SQL Explorer", "Each query as a business question, not a code dump")
    ui.note(
        "Every query below powers something a user actually sees. Pick one to read "
        "the question it answers, how it works, what it costs, and what it returns. "
        "The SQL itself is one click away rather than in your face."
    )
    st.write("")

    names = list(queries.EXPLORER)
    labels = [queries.EXPLORER[n]["question"] for n in names]
    idx = st.selectbox(
        "Business question", range(len(names)), format_func=lambda i: labels[i], key="sql_pick",
    )
    name = names[idx]
    spec = queries.EXPLORER[name]

    params: dict[str, str] = {}
    if spec["params"]:
        cols = st.columns(len(spec["params"]))
        for c, p in zip(cols, spec["params"]):
            with c:
                if p == "symbol":
                    params[p] = st.selectbox("Symbol", list(directory["symbol"]), key="sql_sym")
                elif p == "start":
                    params[p] = st.text_input("Window start", start, key="sql_start")
                elif p == "end":
                    params[p] = st.text_input("Window end", end, key="sql_end")

    try:
        df, ms = dal.timed_read(str(spec["sql"]), params)
        err = None
    except Exception as exc:  # surfaced in the UI rather than crashing the page
        df, ms, err = pd.DataFrame(), 0.0, str(exc)

    # ---- the question ----
    st.markdown(
        f'<div class="qbox"><div class="qbox-k">Business question</div>'
        f'<div class="qbox-q">{ui.esc(spec["question"])}</div></div>',
        unsafe_allow_html=True,
    )

    # ---- plain english ----
    st.markdown("##### How it works")
    st.markdown(str(spec["explain"]))

    # ---- technical summary ----
    st.markdown("##### Technical summary")
    ui.kpi_cards([
        {"icon": "⏱", "label": "Execution time", "value": f"{ms:,.1f} ms",
         "foot": "Uncached run, measured just now"},
        {"icon": "🧾", "label": "Rows returned", "value": f"{len(df):,}",
         "foot": f"{len(df.columns)} columns"},
        {"icon": "🗄", "label": "Engine", "value": "SQLite", "small": True,
         "foot": "Portable; SQL is Postgres-ready"},
        {"icon": "⚡", "label": "Read path", "value": str(spec["read_path"]), "small": True,
         "foot": str(spec["read_path_note"])},
    ])

    tech = st.columns(2)
    with tech[0]:
        st.markdown(
            "**Indexes used**\n\n"
            + "\n".join(f"- `{i}`" for i in spec["indexes"])
            if spec["indexes"] else "**Indexes used**\n\n- *(full scan of a small rollup)*"
        )
    with tech[1]:
        st.markdown(
            "**Views / rollups touched**\n\n"
            + "\n".join(f"- `{v}`" for v in spec["objects"])
        )

    # ---- SQL, collapsed ----
    # Collapsed by default: the SQL is available on demand, not the headline.
    # (No leading arrow glyph -- Streamlit's expander draws its own chevron.)
    with st.expander("Show SQL"):
        st.code(str(spec["sql"]).strip(), language="sql")

    # ---- results ----
    st.markdown("##### Results")
    if err:
        st.error(f"Query failed: {err}")
    elif df.empty:
        st.info("This query returned no rows for those parameters.")
    else:
        ui.data_table(
            df, key=f"sqlres_{idx}",
            search_cols=tuple(c for c in df.columns if df[c].dtype == object)[:3],
            page_size_options=(10, 25, 50, 100),
            csv_name=f"{name.lower().replace(' ', '_')}.csv",
            height=360,
        )

    # ---- where it's used ----
    st.markdown("##### Where this runs in the product")
    st.markdown(
        "".join(f'<div class="uses">✔ {ui.esc(u)}</div>' for u in spec["powers"]),
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------
def _performance() -> None:
    ui.section("Performance", "What was slow, what was done, what it measures now")

    ui.kpi_cards([
        {"icon": "🏁", "label": "Leaderboard", "value": "~1 ms",
         "change": "was 2,100 ms", "change_dir": "up", "foot": "Materialized rollup"},
        {"icon": "💬", "label": "Quote lookup", "value": "~0.4 ms",
         "change": "was 286 ms", "change_dir": "up", "foot": "Precomputed latest bar"},
        {"icon": "📈", "label": "Time series", "value": "~10 ms",
         "foot": "6,300 rows via composite index"},
        {"icon": "🧠", "label": "Repeat reads", "value": "cached",
         "small": True, "foot": "st.cache_data, 1-hour TTL"},
    ])

    st.markdown(
        """
#### 1. Materializing two expensive rollups

`symbol_stats` aggregates six CTEs of window functions across every row. As a
live view the leaderboard took **~2,100 ms**; as a table built once at load time
it is **~1 ms**. `latest_quote` went from **286 ms to ~0.4 ms** the same way —
it previously joined a view that had to `LAG` across all 300k rows just to find
one symbol's last bar.

The trade-off is honest: materialized data can go stale. It doesn't here, because
the inputs only change when the database is rebuilt from source.

#### 2. Indexing for two different access patterns

A composite `(symbol, date)` index serves *one symbol across time*; a separate
`(date)` index serves *all symbols on one date*. Cross-sectional queries were
scanning without the second one.

#### 3. Rendering only the active section

The app originally used tabs. Streamlit renders **every** tab's body on each
rerun, so all seven sections' queries ran when only one was visible. Switching to
conditional rendering cut per-interaction query work by roughly 7×.

It also fixed a rendering bug: Plotly measured charts inside hidden tabs and drew
them at a fraction of the container width.

#### 4. Caching with the right granularity

Every read goes through `data_access.py` and is memoized with `st.cache_data`.
Connections are *not* cached — SQLite rejects a connection shared across
Streamlit's threads — so each run opens its own, which is cheap. Only the
*results* are cached.

The SQL Explorer deliberately bypasses the cache, because a cache hit would
misreport the query cost it exists to demonstrate.

#### 5. Guarding against a stale database

The build stamps a `schema_version` and swaps the file in atomically. The app
verifies that stamp instead of merely checking the file exists — a deploy
container had reused a database from an older schema and every query failed.
"""
    )


# ---------------------------------------------------------------------------
# Stack
# ---------------------------------------------------------------------------
def _stack() -> None:
    ui.section("Technology Stack", "What was used, and why it was chosen")

    st.markdown(
        """
| Layer | Choice | Why this one |
|---|---|---|
| **Database** | SQLite (Postgres-ready SQL) | Zero-config and file-portable, so the repo clones and runs. The SQL — CTEs, window functions, `PARTITION BY` — is standard and moves to Postgres with little change. |
| **Data access** | `sqlite3` + `pandas.read_sql` | No ORM on purpose. An ORM would hide the SQL, which is the thing this project is meant to show. |
| **Application** | Streamlit | Pure-Python UI with real interactivity. The cost is less layout control, paid down with a hand-written stylesheet. |
| **Charts** | Plotly | Zoom, pan, crosshair and PNG export for free, with full control over axes, hover and color. |
| **Data handling** | pandas | Reshaping query results for charts only — never for computing metrics. |
| **Ingestion** | `urllib` (stdlib) | One HTTP call against a documented JSON endpoint; a dependency would earn nothing. |
| **Timezones** | `tzdata` | `zoneinfo` reads the *system* tz database, which slim Linux images omit. |

#### Why Postgres is the migration target

Real reasons, not resume decoration:

- **Concurrency.** SQLite serializes writers. A multi-user deployment with live
  ingestion needs proper MVCC.
- **Native math and statistics.** `SQRT`, `POWER`, `LN`, `EXP` and `MEDIAN` all
  had to be registered as Python functions here because SQLite ships without
  them. Postgres has them built in, plus `percentile_cont`, and would run them
  in-engine rather than round-tripping to Python per row.
- **Materialized views as a first-class feature.** The two rollups are hand-built
  tables; Postgres has `MATERIALIZED VIEW` with `REFRESH`.
- **Partitioning.** Time-series data partitions naturally by date range once it
  outgrows a single file.

What would *not* change: the schema, and nearly all of the query text. That is
the point of keeping the analysis in portable SQL.
"""
    )


# ---------------------------------------------------------------------------
# Interview Mode
# ---------------------------------------------------------------------------
def _interview() -> None:
    ui.section("Interview Mode", "The project explained the way an interviewer will probe it")
    ui.note(
        "Written as answers to the questions actually asked in Data Analyst, BI, "
        "and Software Engineering interviews — including the ones about mistakes."
    )

    with st.expander("**1. Walk me through this project in 60 seconds.**", expanded=True):
        st.markdown(
            """
It's a market analytics platform over 25 years of daily data for the S&P 500 and
49 large-cap equities — about 300,000 rows.

The architecture rule is that **every number on screen is the output of a SQL
query**. The analysis lives in SQLite views and materialized rollups; Python runs
the queries and draws the results but computes no metrics itself.

There are two experiences deliberately kept apart: a **User Mode** that behaves
like a financial product, and a **Developer Center** — this — where the
engineering is documented. Mixing SQL into an end-user screen is what makes a
project read as a class assignment.
"""
        )

    with st.expander("**2. How does data flow through the application?**"):
        st.markdown(
            """
Four stages, each with one job:

1. **Ingest** — `fetch_data.py` pulls daily OHLCV per symbol from Yahoo's chart
   API. It caches each symbol on arrival, so a rate-limited run resumes instead
   of restarting.
2. **Build** — `build_db.py` loads the CSVs and defines the analysis as views,
   then materializes the two expensive rollups and runs `ANALYZE`.
3. **Serve** — `data_access.py` is the only module that opens a connection. Every
   read is cached there.
4. **Render** — `app.py` and `charts.py` lay out and draw.

The strict direction of dependency is what makes it testable: I can rebuild the
database and run every query without starting the UI.
"""
        )

    with st.expander("**3. Why SQL for the analysis instead of pandas?**"):
        st.markdown(
            """
It would genuinely have been faster to write in pandas. Three reasons not to:

- **It's the skill being demonstrated.** Analysis in pandas makes the database a
  file store and the SQL decorative.
- **Portability.** Window functions and CTEs move to Postgres essentially
  unchanged. Pandas logic would need rewriting.
- **The computation belongs near the data.** Rolling volatility over 300k rows
  is a windowed aggregate — exactly what a database is built for.

Where pandas *is* used: reshaping a result set for a chart, e.g. pivoting a tidy
frame into date × symbol. Never for computing a metric.
"""
        )

    with st.expander("**4. Show me the most interesting SQL you wrote.**"):
        st.markdown(
            """
Two I'd point at:

**Running-peak drawdown** — how far below its all-time high a symbol sits, on
every date:

```sql
MAX(close) OVER (PARTITION BY symbol ORDER BY date
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
```

The frame clause is the whole trick: an expanding window gives the running
maximum, so drawdown is one subtraction. Doing this with a self-join would be
O(n²).

**Cumulative return by log-sum:**

```sql
EXP(SUM(LN(1 + daily_return)) OVER (ORDER BY date)) - 1
```

Summing logs and exponentiating is the running *product* of `(1 + r)` — true
compounding. A cumulative **sum** of percentages is the classic bug: it drifts,
and the longer the window the worse it gets.

Also worth mentioning: moving averages guard with
`CASE WHEN COUNT(*) OVER w = 200`, so a "200-day average" is NULL until 200 days
actually exist rather than quietly averaging 30.
"""
        )

    with st.expander("**5. What did you optimize, and how do you know it worked?**"):
        st.markdown(
            """
Measured before and after, not guessed:

| Change | Before | After |
|---|---|---|
| Materialize `symbol_stats` | 2,100 ms | ~1 ms |
| Precompute `latest_quote` | 286 ms | ~0.4 ms |
| Render only the active section | 7 sections' queries per rerun | 1 |

The leaderboard was slow because it rolled up window functions over all 300k rows
on every request. Its inputs only change at build time, so it became a table.

The tab fix was the interesting one: Streamlit renders every tab body on each
rerun, so all seven sections queried on every interaction. It also caused a
*visual* bug — Plotly measured charts in hidden tabs and drew them at ~55% width.
One change fixed both.
"""
        )

    with st.expander("**6. Why Postgres next? What would actually change?**"):
        st.markdown(
            """
- **Concurrency** — SQLite serializes writers; multi-user with live ingestion
  needs MVCC.
- **Built-in math** — I had to register `SQRT`, `POWER`, `LN`, `EXP` and a custom
  `MEDIAN` aggregate as Python callbacks, which means a Python call per row.
  Postgres runs them in-engine and has `percentile_cont`.
- **`MATERIALIZED VIEW` with `REFRESH`** instead of hand-built rollup tables.
- **Range partitioning** by date as the data grows.

The schema and nearly all query text carry over unchanged — which was the reason
for keeping the analysis in portable SQL.
"""
        )

    with st.expander("**7. What broke, and what did you learn?**"):
        st.markdown(
            """
Four worth telling, because each changed how I write code:

**A stale database silently broke every query.** The app builds its database on
first run if one isn't present — but I checked only whether the *file existed*. A
deployed container kept its disk across restarts, so a database from an older
single-symbol schema was reused forever and every query failed with
`no such table: symbols`. Now the build stamps a `schema_version` that the app
verifies, and writes atomically via a temp file so a half-finished build can't
masquerade as a good one. **Existence is not validity.**

**Percentages were wrong by 100×.** A total return of +18,630% displayed as
+186%. Streamlit's `"%%"` number format appends a percent sign — it does not
multiply. Caught by reading the rendered table against a figure I already knew.

**Rate limiting cost an entire ingest run.** 50 symbols, all lost to HTTP 429.
Two fixes: cache each symbol as it arrives so a run is resumable, and — counter
to instinct — send a *bare* `User-Agent`. A full browser UA plus a Referer was
what triggered the throttling.

**A mean was the wrong statistic.** Sector performance showed Information
Technology at +13,553%, which was almost entirely one Apple. Over long windows a
mean of total returns describes the outlier, not the group. Switched to median.
"""
        )

    with st.expander("**8. How did you decide what the charts should look like?**"):
        st.markdown(
            """
Form follows the question, and two rules are non-negotiable:

**No dual-axis charts.** Comparing companies at different price levels rebases
everything to 100 on one shared axis. Two independent y-scales can be aligned to
imply any correlation you want — it's the most common way a finance chart lies.
When series diverge more than 20×, the axis switches to log so the smaller ones
don't flatten onto the baseline.

**Color is validated, not chosen by eye.** Series colors are checked per theme
for colorblind separation (Machado 2009 protanopia/deuteranopia simulation) and
WCAG contrast against that theme's surface. Identity never rests on color alone —
multi-series charts carry legends, and direction indicators pair an arrow with
the color.

Partial calendar years at the edges of a window are faded and asterisked, because
a part-year figure is not a calendar-year return.
"""
        )

    with st.expander("**9. What are the limitations of your data?**"):
        st.markdown(
            """
I'd rather state these than be caught by them:

- **Price returns, not total returns.** Dividends are excluded, so long-run
  figures understate what a shareholder earned.
- **Survivorship bias.** The universe is companies that exist *today*, omitting
  those that failed or were acquired — which flatters long-run returns. This is
  the most serious limitation and it affects every leaderboard.
- **No market cap.** It needs share counts, which no reachable free endpoint
  provides. Rather than hardcode a figure that goes stale, the app shows average
  dollar turnover, computed from data actually present.
- **Unequal histories.** META lists in 2012, TSLA in 2010. All-time leaderboards
  show `Years` and `From` rather than silently ranking unequal periods.
- **Sectors are equal-weighted**, not market-cap-weighted, for the same reason.
"""
        )

    with st.expander("**10. What would you build next?**"):
        st.markdown(
            """
In priority order, with the reasoning:

1. **Postgres migration** — unblocks concurrency and removes the Python math
   callbacks.
2. **Total-return series** — the single biggest accuracy win; every long-horizon
   number is currently understated.
3. **A point-in-time universe** — index membership by date, which is the only
   real fix for survivorship bias.
4. **Incremental loads** — append the latest bars instead of rebuilding, so the
   dataset can update daily.
5. **Tests over the SQL** — assert known values (2008 ≈ −38%, the March 2009
   trough) so a refactor can't silently change a number.
"""
        )


# ---------------------------------------------------------------------------
# About
# ---------------------------------------------------------------------------
def _about_project() -> None:
    ui.section("About the Project", "Scope, provenance and honest limits")
    st.markdown(
        """
#### What it is

A market analytics platform over ~25 years of daily history for the S&P 500 index
and 49 large-cap US equities — roughly 300,000 rows — built to demonstrate SQL,
database design, data engineering and data visualization in one place.

#### Data provenance

Daily OHLCV from Yahoo Finance's public chart endpoint, unauthenticated. Sector
and industry classifications are hand-maintained: the endpoints that serve them
are auth-gated, and these are stable facts rather than market data.

#### Honest limits

- Price returns, not total returns — dividends excluded.
- Survivorship bias — a fixed list of companies that exist today.
- No market cap — share counts aren't available; dollar turnover shown instead.
- Unequal listing histories — surfaced rather than hidden.
- Sector aggregates are equal-weighted and use the median, not the mean.
- Market status is schedule-based; exchange holidays aren't modelled.

#### Not investment advice

This is an analysis and portfolio-demonstration project.
"""
    )


# ---------------------------------------------------------------------------
# Query Router — how a question became a query
# ---------------------------------------------------------------------------
def _query_router() -> None:
    import nlq
    import router
    import sqlguard

    ui.section("Query Router", "How a natural-language question becomes a query")
    ui.note(
        "Every question in Ask the Market enters here. The router prefers an existing "
        "SQL template and only generates SQL when no template can answer — so the "
        "common path runs the same tuned queries as the rest of the app."
    )

    live = nlq.available()
    ui.kpi_cards([
        {"icon": "🧠", "label": "Intent extraction",
         "value": "Model" if live else "Keywords",
         "foot": nlq.MODEL if live else "No API key — deterministic fallback"},
        {"icon": "📚", "label": "Templates", "value": str(len(router.TEMPLATES)),
         "foot": "Preferred over generated SQL"},
        {"icon": "🛡", "label": "Readable objects", "value": str(len(sqlguard.ALLOWED_OBJECTS)),
         "foot": "Everything else is rejected"},
        {"icon": "🎟", "label": "Generated this session",
         "value": f"{router.generated_used()}/{router.MAX_GENERATED_PER_SESSION}",
         "foot": "Per-session cap"},
    ])

    st.markdown("**Route log** — the path each question took this session.")
    log = st.session_state.get("route_log", [])
    if not log:
        st.caption("No questions asked yet this session. Ask one in User Mode → Overview.")
    else:
        st.dataframe(
            pd.DataFrame(log[::-1]).rename(columns={
                "question": "Question", "path": "Path", "intent": "Intent",
                "source": "Parsed by", "detail": "Detail",
                "params": "Resolved parameters", "ms": "ms"}),
            width="stretch", hide_index=True,
        )
        st.caption(
            "Each parameter is tagged with the tier that won it: `question` beat the "
            "sidebar, `ui` inherited the current window, `default` had neither."
        )

    # The generated statement, disclosed here and nowhere else. User Mode shows
    # the answer; the SQL behind it belongs to whoever is evaluating the build.
    last = st.session_state.get("last_generated_sql", "")
    if last:
        with st.expander("Last generated SQL", expanded=False):
            st.code(last, language="sql")

    with st.expander("Validation rules applied to generated SQL", expanded=False):
        st.markdown(
            "Checked in order, cheapest first, before anything reaches SQLite:\n\n"
            "1. **Comments stripped**, then the stripped text is what runs — "
            "`SELECT/**/DROP` and trailing `--` are the standard ways to hide a token.\n"
            "2. **One statement only** — checked before the SELECT test, because "
            "`SELECT 1; DROP TABLE prices` passes a prefix check.\n"
            "3. **Must begin with SELECT or WITH.**\n"
            "4. **No write keywords** — INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, "
            "PRAGMA, ATTACH and the rest, matched on word boundaries so `created_at` "
            "is not a false positive.\n"
            "5. **Table allowlist** — only the objects below; CTE names defined by the "
            "query itself are exempt. `sqlite_master` and `meta` are not readable.\n"
            "6. **EXPLAIN against the real schema** — compiles the statement without "
            "running it, so a hallucinated column fails here rather than mid-render.\n\n"
            "Execution then happens on a `mode=ro` connection, where SQLite itself "
            "refuses writes. String analysis can be fooled; the engine cannot."
        )
        st.code("\n".join(sorted(sqlguard.ALLOWED_OBJECTS)), language="text")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
def render(section: str, directory: pd.DataFrame, start: str, end: str) -> None:
    if section == "Overview":
        _overview(directory)
    elif section == "Project Architecture":
        _architecture()
    elif section == "Database Schema":
        _schema()
    elif section == "SQL Explorer":
        _sql_explorer(directory, start, end)
    elif section == "Query Router":
        _query_router()
    elif section == "Performance":
        _performance()
    elif section == "Technology Stack":
        _stack()
    elif section == "Interview Mode":
        _interview()
    elif section == "About the Project":
        _about_project()

"""Cached query layer between Streamlit and SQL.

Every read goes through here so caching, timing and connection handling live in
one place. A sqlite3 connection can't be shared across Streamlit's threads, so
each call opens its own (cheap, read-only) and the *results* are what get cached.
"""

from __future__ import annotations

import datetime as dt
import time

import pandas as pd
import streamlit as st

import build_db
import queries
from db import DB_PATH, get_connection

TTL = 3600  # data is a static daily snapshot; an hour is plenty


@st.cache_resource(show_spinner=False)
def ensure_db() -> str:
    """Build the DB from the committed CSVs unless a CURRENT one already exists.

    Checks the schema stamp rather than mere file existence: a deploy container
    keeps its disk across restarts, so a database left by an older schema would
    otherwise be reused forever and every query would fail with `no such table`.
    """
    if not build_db.db_is_current(DB_PATH):
        build_db.main()
    return str(DB_PATH)


def _read(sql: str, params: dict | None = None) -> pd.DataFrame:
    ensure_db()
    conn = get_connection()
    try:
        return pd.read_sql(sql, conn, params=params or {})
    finally:
        conn.close()


def timed_read(sql: str, params: dict | None = None) -> tuple[pd.DataFrame, float]:
    """Uncached read that also reports elapsed ms — for the SQL Explorer, where
    the measured time is the point and a cache hit would misreport it."""
    ensure_db()
    conn = get_connection()
    try:
        t0 = time.perf_counter()
        df = pd.read_sql(sql, conn, params=params or {})
        return df, (time.perf_counter() - t0) * 1000
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reference
# ---------------------------------------------------------------------------
@st.cache_data(ttl=TTL, show_spinner=False)
def directory() -> pd.DataFrame:
    return _read(queries.SYMBOL_DIRECTORY)


@st.cache_data(ttl=TTL, show_spinner=False)
def date_bounds(symbol: str) -> tuple[dt.date, dt.date]:
    row = _read(queries.DATE_BOUNDS, {"symbol": symbol}).iloc[0]
    return dt.date.fromisoformat(row["min_date"]), dt.date.fromisoformat(row["max_date"])


@st.cache_data(ttl=TTL, show_spinner=False)
def global_last_date() -> dt.date:
    df = _read("SELECT MAX(date) AS d FROM prices;")
    return dt.date.fromisoformat(df.iloc[0]["d"])


@st.cache_data(ttl=TTL, show_spinner=False)
def row_count() -> int:
    return int(_read("SELECT COUNT(*) AS n FROM prices;").iloc[0]["n"])


# ---------------------------------------------------------------------------
# Per-symbol series
# ---------------------------------------------------------------------------
def _dated(df: pd.DataFrame) -> pd.DataFrame:
    if "date" in df.columns:
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=TTL, show_spinner=False)
def prices(symbol: str, start: str, end: str) -> pd.DataFrame:
    return _dated(_read(queries.PRICES_IN_RANGE, {"symbol": symbol, "start": start, "end": end}))


@st.cache_data(ttl=TTL, show_spinner=False)
def moving_averages(symbol: str, start: str, end: str) -> pd.DataFrame:
    return _dated(_read(queries.MOVING_AVERAGES_IN_RANGE, {"symbol": symbol, "start": start, "end": end}))


@st.cache_data(ttl=TTL, show_spinner=False)
def daily_returns(symbol: str, start: str, end: str) -> pd.DataFrame:
    return _dated(_read(queries.DAILY_RETURNS_IN_RANGE, {"symbol": symbol, "start": start, "end": end}))


@st.cache_data(ttl=TTL, show_spinner=False)
def drawdowns(symbol: str, start: str, end: str) -> pd.DataFrame:
    return _dated(_read(queries.DRAWDOWN_IN_RANGE, {"symbol": symbol, "start": start, "end": end}))


@st.cache_data(ttl=TTL, show_spinner=False)
def volatility(symbol: str, start: str, end: str) -> pd.DataFrame:
    return _dated(_read(queries.VOLATILITY_IN_RANGE, {"symbol": symbol, "start": start, "end": end}))


@st.cache_data(ttl=TTL, show_spinner=False)
def cumulative_return(symbol: str, start: str, end: str) -> pd.DataFrame:
    return _dated(_read(queries.CUMULATIVE_RETURN_IN_RANGE, {"symbol": symbol, "start": start, "end": end}))


@st.cache_data(ttl=TTL, show_spinner=False)
def window_stats(symbol: str, start: str, end: str) -> pd.Series | None:
    df = _read(queries.WINDOW_STATS, {"symbol": symbol, "start": start, "end": end})
    return None if df.empty else df.iloc[0]


@st.cache_data(ttl=TTL, show_spinner=False)
def quote(symbol: str) -> pd.Series | None:
    df = _read(queries.QUOTE_SNAPSHOT, {"symbol": symbol})
    return None if df.empty else df.iloc[0]


@st.cache_data(ttl=TTL, show_spinner=False)
def yearly(symbol: str) -> pd.DataFrame:
    return _read(queries.YEARLY_SUMMARY, {"symbol": symbol})


@st.cache_data(ttl=TTL, show_spinner=False)
def monthly(symbol: str) -> pd.DataFrame:
    return _read(queries.MONTHLY_RETURNS, {"symbol": symbol})


# ---------------------------------------------------------------------------
# Cross-symbol
# ---------------------------------------------------------------------------
@st.cache_data(ttl=TTL, show_spinner=False)
def leaderboard() -> pd.DataFrame:
    return _read(queries.LEADERBOARD)


@st.cache_data(ttl=TTL, show_spinner=False)
def period_movers(start: str, end: str) -> pd.DataFrame:
    return _read(queries.PERIOD_MOVERS, {"start": start, "end": end})


@st.cache_data(ttl=TTL, show_spinner=False)
def sector_performance(start: str, end: str) -> pd.DataFrame:
    return _read(queries.SECTOR_PERFORMANCE, {"start": start, "end": end})


# ---------------------------------------------------------------------------
# Parameterized variants — used by Ask the Market
# ---------------------------------------------------------------------------
# Sort column, sector and row count are pushed into the query rather than applied
# to its result, so a question's parameters are visible in the SQL. The column
# name cannot be a bind parameter in SQLite, so it is resolved through these
# whitelists -- a key that isn't here raises rather than reaching the statement.
MOVERS_SORTS = {
    "return": "period_return",
    "volume": "avg_dollar_volume",
    "turnover": "avg_dollar_volume",
    "liquidity": "avg_dollar_volume",
}
STATS_SORTS = {
    "cagr": "cagr",
    "return": "total_return",
    "volatility": "ann_volatility",
    "risk": "ann_volatility",
    "drawdown": "max_drawdown",
    "years": "years",
    "volume": "avg_dollar_volume",
    "turnover": "avg_dollar_volume",
}


def _order(column: str, ascending: bool) -> str:
    return f"{column} {'ASC' if ascending else 'DESC'}"


@st.cache_data(ttl=TTL, show_spinner=False)
def movers_ranked(start: str, end: str, *, sort: str = "return",
                  ascending: bool = False, sector: str = "", limit: int = -1) -> pd.DataFrame:
    """Period movers, filtered and ranked in SQL. `limit=-1` means every row."""
    column = MOVERS_SORTS.get(sort, MOVERS_SORTS["return"])
    sql = queries.PERIOD_MOVERS_RANKED.format(order_by=_order(column, ascending))
    return _read(sql, {"start": start, "end": end, "sector": sector, "limit": int(limit)})


@st.cache_data(ttl=TTL, show_spinner=False)
def leaderboard_ranked(*, sort: str = "cagr", ascending: bool = False,
                       sector: str = "", limit: int = -1) -> pd.DataFrame:
    """All-time stats, filtered and ranked in SQL.

    Rows where the sort column is NULL are excluded in the query. SQLite orders
    NULLs first on an ascending sort, so a "steadiest company" question would
    otherwise return companies with no volatility figure at all.
    """
    column = STATS_SORTS.get(sort, STATS_SORTS["cagr"])
    sql = queries.LEADERBOARD_RANKED.format(
        order_by=_order(column, ascending), not_null=column)
    return _read(sql, {"sector": sector, "limit": int(limit)})


@st.cache_data(ttl=TTL, show_spinner=False)
def rolling_returns(symbol: str, sessions: int) -> pd.DataFrame:
    """Annualized return of every `sessions`-long holding period, in date order."""
    return _dated(_read(queries.ROLLING_RETURNS,
                        {"symbol": symbol, "sessions": int(sessions)}))


@st.cache_data(ttl=TTL, show_spinner=False)
def rolling_return_summary(symbol: str, sessions: int) -> pd.Series | None:
    df = _read(queries.ROLLING_RETURN_SUMMARY,
               {"symbol": symbol, "sessions": int(sessions)})
    return None if df.empty or not df.iloc[0]["periods"] else df.iloc[0]


def _symbol_rows(symbols: tuple[str, ...]) -> str:
    """Build the VALUES list for a multi-symbol query.

    Same rule as `_weight_rows`: symbols reach this from a multiselect, but a
    value spliced into SQL is validated against the universe regardless.
    """
    from universe import all_symbols

    known = set(all_symbols())
    for sym in symbols:
        if sym not in known:
            raise ValueError(f"unknown symbol: {sym!r}")
    return ", ".join(f"('{sym}')" for sym in symbols)


@st.cache_data(ttl=TTL, show_spinner=False)
def correlation_matrix(symbols: tuple[str, ...], start: str, end: str) -> pd.DataFrame:
    """Pairwise daily-return correlation, pivoted into a square matrix.

    The pivot is a reshape of the query's rows, not a calculation — every
    coefficient is computed in SQL. Rows and columns are returned in the
    caller's order so a symbol keeps its position when the selection grows.
    """
    if len(symbols) < 2:
        return pd.DataFrame()
    sql = queries.CORRELATION_MATRIX.format(symbol_rows=_symbol_rows(symbols))
    df = _read(sql, {"start": start, "end": end})
    if df.empty:
        return pd.DataFrame()
    matrix = df.pivot(index="sym_a", columns="sym_b", values="correlation")
    ordered = [s for s in symbols if s in matrix.index and s in matrix.columns]
    return matrix.loc[ordered, ordered]


@st.cache_data(ttl=TTL, show_spinner=False)
def correlation_pairs(symbols: tuple[str, ...], start: str, end: str) -> pd.DataFrame:
    """The same query's rows as a long list, with self-pairs and mirrors dropped.

    Feeds the table view and the most/least-correlated callouts, where the
    square matrix's duplicate halves would double-count every pair.
    """
    if len(symbols) < 2:
        return pd.DataFrame()
    sql = queries.CORRELATION_MATRIX.format(symbol_rows=_symbol_rows(symbols))
    df = _read(sql, {"start": start, "end": end})
    return df[df["sym_a"] < df["sym_b"]].reset_index(drop=True) if not df.empty else df


def _weight_rows(weights: tuple[tuple[str, float], ...]) -> str:
    """Build the VALUES list for a portfolio query.

    Symbols are validated against the universe rather than interpolated blindly:
    they reach this from a multiselect, but a table name spliced into SQL is a
    habit worth never forming. Weights are floats and formatted as such.
    """
    from universe import all_symbols

    known = set(all_symbols())
    rows = []
    for sym, w in weights:
        if sym not in known:
            raise ValueError(f"unknown symbol: {sym!r}")
        rows.append(f"('{sym}', {float(w):.10f})")
    return ", ".join(rows)


@st.cache_data(ttl=TTL, show_spinner=False)
def portfolio_series(weights: tuple[tuple[str, float], ...], start: str, end: str) -> pd.DataFrame:
    sql = queries.PORTFOLIO_SERIES.format(weight_rows=_weight_rows(weights))
    return _dated(_read(sql, {"start": start, "end": end}))


@st.cache_data(ttl=TTL, show_spinner=False)
def portfolio_stats(weights: tuple[tuple[str, float], ...], start: str, end: str) -> pd.Series | None:
    sql = queries.PORTFOLIO_STATS.format(weight_rows=_weight_rows(weights))
    df = _read(sql, {"start": start, "end": end})
    return None if df.empty or pd.isna(df.iloc[0]["sessions"]) else df.iloc[0]


@st.cache_data(ttl=TTL, show_spinner=False)
def portfolio_contribution(weights: tuple[tuple[str, float], ...],
                           start: str, end: str) -> pd.DataFrame:
    """Per-holding contribution to the portfolio's total return.

    The contributions sum to the portfolio's total return exactly; see the
    derivation above `PORTFOLIO_CONTRIBUTION` in queries.py.
    """
    sql = queries.PORTFOLIO_CONTRIBUTION.format(weight_rows=_weight_rows(weights))
    return _read(sql, {"start": start, "end": end})


@st.cache_data(ttl=TTL, show_spinner=False)
def indexed_comparison(symbols: tuple[str, ...], start: str, end: str) -> pd.DataFrame:
    """Rebased-to-100 series for the given symbols, pivoted date x symbol.

    Filtering in pandas after one SQL pass beats issuing N queries; the SQL still
    does the rebasing so the arithmetic stays in the query layer.
    """
    df = _read(queries.INDEXED_COMPARISON, {"start": start, "end": end})
    if df.empty:
        return pd.DataFrame()
    df = df[df["symbol"].isin(symbols)]
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    pivot = df.pivot(index="date", columns="symbol", values="indexed_close")
    # Preserve the caller's order so colors follow the entity, not sort order.
    ordered = [s for s in symbols if s in pivot.columns]
    return pivot[ordered]


@st.cache_data(ttl=TTL, show_spinner=False)
def comparison_frames(symbols: tuple[str, ...], start: str, end: str, metric: str) -> dict:
    """One frame per symbol for a comparable metric, keyed by symbol.

    Kept in the data layer so the page stays layout-only, and cached as a unit
    because a comparison redraws whenever the selection changes.
    """
    loaders = {
        "Cumulative return": (cumulative_return, "cumulative_return"),
        "Drawdown": (drawdowns, "drawdown"),
        "Rolling volatility": (volatility, "ann_volatility_21d"),
        "Daily returns": (daily_returns, "daily_return"),
        "Volume": (prices, "volume"),
    }
    loader, col = loaders[metric]
    out = {}
    for sym in symbols:
        df = loader(sym, start, end)
        if df is not None and not df.empty:
            out[sym] = df[["date", col]]
    return out


# ---------------------------------------------------------------------------
# Stock Journey
# ---------------------------------------------------------------------------
# All all-time by construction, like the Performance readers: a journey is the
# company's whole record. `asof` moves a cursor INSIDE that record rather than
# narrowing it, so it is a cache key like any other parameter -- and because the
# cursor lands on a small number of distinct dates during playback, the cache
# hit rate through a replay is high.

@st.cache_data(ttl=TTL, show_spinner=False)
def journey_snapshot(symbol: str, asof: str) -> pd.Series | None:
    df = _read(queries.JOURNEY_SNAPSHOT, {"symbol": symbol, "asof": asof})
    return None if df.empty else df.iloc[0]


@st.cache_data(ttl=TTL, show_spinner=False)
def journey_price_path(symbol: str, stride: int) -> pd.DataFrame:
    """The drawable price line. `stride` thins it; peaks are always kept."""
    return _dated(_read(queries.JOURNEY_PRICE_PATH,
                        {"symbol": symbol, "stride": max(int(stride), 1)}))


@st.cache_data(ttl=TTL, show_spinner=False)
def journey_drawdowns(symbol: str, asof: str, min_depth: float = -0.20) -> pd.DataFrame:
    """Drawdown episodes deeper than `min_depth` (a negative fraction).

    `asof` bounds the price series the episodes are built from, so an episode
    still open at the cursor returns a NULL recovery instead of one dated in the
    cursor's future. Filtering the returned frame instead would leak it.
    """
    return _read(queries.JOURNEY_DRAWDOWN_EPISODES,
                 {"symbol": symbol, "asof": asof, "min_depth": float(min_depth)})


@st.cache_data(ttl=TTL, show_spinner=False)
def journey_extremes(symbol: str, asof: str, limit: int = 5) -> pd.DataFrame:
    return _read(queries.JOURNEY_EXTREME_DAYS,
                 {"symbol": symbol, "asof": asof, "limit": int(limit)})


@st.cache_data(ttl=TTL, show_spinner=False)
def journey_streaks(symbol: str, asof: str, direction: int, limit: int = 3) -> pd.DataFrame:
    """Longest runs in one direction: 1 for up sessions, -1 for down."""
    if direction not in (1, -1):
        raise ValueError(f"direction must be 1 or -1, got {direction!r}")
    return _read(queries.JOURNEY_STREAKS,
                 {"symbol": symbol, "asof": asof,
                  "direction": int(direction), "limit": int(limit)})


@st.cache_data(ttl=TTL, show_spinner=False)
def journey_best_worst(symbol: str, asof: str) -> pd.DataFrame:
    """Best/worst full calendar month and year up to the cursor.

    The month and year keys are derived from `asof` here rather than in SQL
    because SQLite's date functions would need the same substring anyway, and
    the query reads more clearly taking them as parameters.
    """
    return _read(queries.JOURNEY_BEST_WORST_PERIODS,
                 {"symbol": symbol, "asof_month": asof[:7], "asof_year": asof[:4]})


@st.cache_data(ttl=TTL, show_spinner=False)
def journey_trend_changes(symbol: str, asof: str) -> pd.DataFrame:
    return _dated(_read(queries.JOURNEY_TREND_CHANGES, {"symbol": symbol, "asof": asof}))


@st.cache_data(ttl=TTL, show_spinner=False)
def journey_records(symbol: str, asof: str) -> pd.Series | None:
    df = _read(queries.JOURNEY_RECORD_SUMMARY, {"symbol": symbol, "asof": asof})
    return None if df.empty or not df.iloc[0]["record_days"] else df.iloc[0]


@st.cache_data(ttl=TTL, show_spinner=False)
def journey_company_events(symbol: str, asof: str, sessions: int = 5) -> pd.DataFrame:
    return _read(queries.JOURNEY_COMPANY_EVENTS,
                 {"symbol": symbol, "asof": asof, "sessions": int(sessions)})


@st.cache_data(ttl=TTL, show_spinner=False)
def journey_market_events(symbol: str, asof: str, sessions: int = 5,
                          min_move: float = 0.05) -> pd.DataFrame:
    """Market-wide events, kept only where this company itself moved.

    `min_move` is an absolute fraction and is applied in the query's WHERE
    clause, so what counts as "materially affected" is visible in the SQL.
    """
    return _read(queries.JOURNEY_MARKET_EVENT_IMPACT,
                 {"symbol": symbol, "asof": asof,
                  "sessions": int(sessions), "min_move": abs(float(min_move))})

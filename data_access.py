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

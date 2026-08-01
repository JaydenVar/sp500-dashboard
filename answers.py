"""Render answers for Ask the Market.

Each handler turns a matched intent into a headline sentence plus supporting
evidence -- figures, a chart, or a table. It runs real queries against the
selected window, so the answer always agrees with the rest of the app.

The user never sees SQL here: this is User Mode.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import charts
import components as ui
import data_access as dal
import events
from universe import INDEX_SYMBOL


def _headline(text: str) -> None:
    st.markdown(f'<div class="answer">{text}</div>', unsafe_allow_html=True)


def _name(directory: pd.DataFrame, sym: str) -> str:
    row = directory[directory["symbol"] == sym]
    return str(row.iloc[0]["name"]) if len(row) else sym


def _scope(p) -> str:
    """How the answer describes the slice it actually used.

    The window and any sector filter came from the question or from the sidebar,
    and the headline has to say which -- an answer that silently narrows to one
    sector reads as a wrong answer about the whole market.
    """
    where = f" in {ui.esc(p.sector)}" if p.sector else ""
    return f"{where} over {ui.esc(p.preset)}"


def _movers_sort(p) -> str:
    """Map a stated metric onto a sortable column, defaulting to return."""
    return p.metric if p.metric in dal.MOVERS_SORTS else "return"


def _stats_sort(p, default: str) -> str:
    return p.metric if p.metric in dal.STATS_SORTS else default


def _descending(p, default: bool) -> bool:
    """Explicit ordering in the question overrides the intent's natural direction."""
    if p.ordering == "asc":
        return False
    if p.ordering == "desc":
        return True
    return default


def _empty(p) -> None:
    if p.sector:
        st.warning(f"No companies in {ui.esc(p.sector)} have data over {ui.esc(p.preset)}.")
    else:
        st.warning("No data in this window.")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def top_volume(directory, pal, p):
    top = dal.movers_ranked(p.start, p.end, sort="volume", ascending=False,
                            sector=p.sector, limit=p.limit)
    if top.empty:
        return _empty(p)
    w = top.iloc[0]
    _headline(
        f"<b>{ui.esc(w['name'])} ({ui.esc(w['symbol'])})</b> traded the most{_scope(p)}, averaging "
        f"<b>{ui.fmt_dollar_compact(w['avg_dollar_volume'])}</b> of turnover a day."
    )
    ui.note(
        "Measured as dollar turnover (price × shares), which compares fairly across "
        "companies — a cheap stock can trade many more shares for the same money."
    )
    tbl = top.assign(Turnover=top["avg_dollar_volume"],
                     Return=top["period_return"] * 100)[
        ["symbol", "name", "sector", "Turnover", "Return"]]
    tbl.columns = ["Ticker", "Company", "Sector", "Avg daily turnover", "Return"]
    ui.data_table(tbl, key="ans_vol", search_cols=("Ticker", "Company"),
                  csv_name="highest_turnover.csv", height=340,
                  column_config={
                      "Avg daily turnover": st.column_config.NumberColumn(format="compact"),
                      "Return": st.column_config.NumberColumn(format="%+.1f%%")})


def _gainers_losers(directory, pal, p, best: bool, key: str):
    sort = _movers_sort(p)
    top = dal.movers_ranked(p.start, p.end, sort=sort, ascending=not _descending(p, best),
                            sector=p.sector, limit=p.limit)
    if top.empty:
        return _empty(p)
    # Breadth is a different question from the ranking, so it gets its own
    # unlimited query rather than being derived from the truncated one.
    df = dal.movers_ranked(p.start, p.end, sort=sort, sector=p.sector, limit=-1)
    w = top.iloc[0]
    verb = "gained" if best else "lost"
    _headline(
        f"<b>{ui.esc(w['name'])} ({ui.esc(w['symbol'])})</b> {verb} the most{_scope(p)}, "
        f"at <b>{ui.fmt_pct(w['period_return'], 1)}</b>."
    )
    adv = int((df["period_return"] > 0).sum())
    ui.kpi_cards([
        {"icon": "🥇" if best else "🔻", "label": "Leader" if best else "Laggard",
         "value": str(w["symbol"]), "small": True,
         "change": ui.fmt_pct(w["period_return"], 1),
         "change_dir": "up" if best else "down", "foot": str(w["name"])[:28]},
        {"icon": "📊", "label": "Advancing", "value": f"{adv}",
         "foot": f"of {len(df)} companies"},
        {"icon": "🎯", "label": "Median return",
         "value": ui.fmt_pct(float(df["period_return"].median()), 1),
         "foot": "Middle company"},
    ])
    tbl = top.assign(Return=top["period_return"] * 100)[
        ["symbol", "name", "sector", "Return"]]
    tbl.columns = ["Ticker", "Company", "Sector", "Return"]
    ui.data_table(tbl, key=key, search_cols=("Ticker", "Company"),
                  csv_name=f"{'winners' if best else 'losers'}.csv", height=340,
                  column_config={"Return": st.column_config.NumberColumn(format="%+.1f%%")})


def top_gainers(directory, pal, p):
    _gainers_losers(directory, pal, p, True, "ans_win")


def top_losers(directory, pal, p):
    _gainers_losers(directory, pal, p, False, "ans_lose")


def biggest_drawdown(directory, pal, p):
    lb = dal.leaderboard_ranked(sort=_stats_sort(p, "drawdown"), ascending=True,
                                sector=p.sector, limit=p.limit)
    if lb.empty:
        return _empty(p)
    w = lb.iloc[0]
    _headline(
        f"<b>{ui.esc(w['name'])} ({ui.esc(w['symbol'])})</b> suffered the deepest fall: "
        f"<b>{ui.fmt_pct(w['max_drawdown'], 1)}</b> from a previous high."
    )
    ui.note(
        "Drawdown is measured from each company's running all-time high over its "
        "full listed history — it is the loss an investor would have had to sit "
        "through, not a single-day move."
    )
    dd = dal.drawdowns(str(w["symbol"]), p.start, p.end)
    if not dd.empty:
        fig = charts.area_series(dd, "drawdown", pal, color_key="red",
                                 y_title="Drawdown", height=300, tickformat=".0%")
        charts.add_events(fig, pal, events.in_range(p.start, p.end))
        ui.chart(fig, key="ans_dd",
                 caption=f"{w['symbol']} drawdown over {p.preset}")

    out = lb.assign(MaxDD=lb["max_drawdown"] * 100, Vol=lb["ann_volatility"] * 100)[
        ["symbol", "name", "sector", "MaxDD", "Vol"]]
    out.columns = ["Ticker", "Company", "Sector", "Max drawdown", "Volatility"]
    ui.data_table(out, key="ans_ddt", search_cols=("Ticker", "Company"),
                  csv_name="deepest_drawdowns.csv", height=340,
                  column_config={
                      "Max drawdown": st.column_config.NumberColumn(format="%.1f%%"),
                      "Volatility": st.column_config.NumberColumn(format="%.1f%%")})


def _by_volatility(directory, pal, p, most: bool, key: str):
    # NULL volatility is excluded in SQL -- on an ascending sort SQLite would
    # otherwise return companies with no figure at all as the "steadiest".
    top = dal.leaderboard_ranked(sort=_stats_sort(p, "volatility"),
                                 ascending=not _descending(p, most),
                                 sector=p.sector, limit=p.limit)
    if top.empty:
        return _empty(p)
    w = top.iloc[0]
    _headline(
        f"<b>{ui.esc(w['name'])} ({ui.esc(w['symbol'])})</b> has been the "
        f"{'most volatile' if most else 'steadiest'}"
        f"{' in ' + ui.esc(p.sector) if p.sector else ''}, at "
        f"<b>{ui.fmt_pct(w['ann_volatility'], 1, signed=False)}</b> annualized."
    )
    ui.note(
        "Volatility is the annualized standard deviation of daily moves over each "
        "company's full history. Higher means a rougher ride — not necessarily a "
        "worse outcome; check the return and drawdown alongside it."
    )
    out = top.assign(Vol=top["ann_volatility"] * 100, CAGR=top["cagr"] * 100,
                     MaxDD=top["max_drawdown"] * 100)[
        ["symbol", "name", "sector", "Vol", "CAGR", "MaxDD"]]
    out.columns = ["Ticker", "Company", "Sector", "Volatility", "CAGR", "Max drawdown"]
    ui.data_table(out, key=key, search_cols=("Ticker", "Company"),
                  csv_name="volatility.csv", height=340,
                  column_config={
                      "Volatility": st.column_config.NumberColumn(format="%.1f%%"),
                      "CAGR": st.column_config.NumberColumn(format="%+.1f%%"),
                      "Max drawdown": st.column_config.NumberColumn(format="%.1f%%")})


def most_volatile(directory, pal, p):
    _by_volatility(directory, pal, p, True, "ans_vola")


def least_volatile(directory, pal, p):
    _by_volatility(directory, pal, p, False, "ans_stable")


def best_cagr(directory, pal, p):
    top = dal.leaderboard_ranked(sort=_stats_sort(p, "cagr"),
                                 ascending=not _descending(p, True),
                                 sector=p.sector, limit=p.limit)
    if top.empty:
        return _empty(p)
    w = top.iloc[0]
    _headline(
        f"<b>{ui.esc(w['name'])} ({ui.esc(w['symbol'])})</b> compounded fastest"
        f"{' in ' + ui.esc(p.sector) if p.sector else ''} at "
        f"<b>{ui.fmt_pct(w['cagr'], 1)}</b> a year over {w['years']:.0f} years."
    )
    ui.note(
        "CAGR is each company's own listed history, so the periods differ — "
        "'Years' is shown so a shorter, luckier run isn't mistaken for a longer one."
    )
    out = top.assign(CAGR=top["cagr"] * 100, Total=top["total_return"] * 100,
                     Years=top["years"].round(1))[
        ["symbol", "name", "sector", "Years", "CAGR", "Total"]]
    out.columns = ["Ticker", "Company", "Sector", "Years", "CAGR", "Total return"]
    ui.data_table(out, key="ans_cagr", search_cols=("Ticker", "Company"),
                  csv_name="best_cagr.csv", height=340,
                  column_config={
                      "CAGR": st.column_config.NumberColumn(format="%+.2f%%"),
                      "Total return": st.column_config.NumberColumn(format="%+.1f%%"),
                      "Years": st.column_config.NumberColumn(format="%.1f")})


def compare(directory, pal, p):
    syms = list(p.symbols)[:4]
    if len(syms) < 2:
        return st.info("Name two companies to compare, e.g. “Compare Apple and Microsoft”.")

    pivot = dal.indexed_comparison(tuple(syms), p.start, p.end)
    if pivot.empty:
        return st.warning("No overlapping history for those companies in this window.")

    finals = pivot.ffill().iloc[-1]
    rets = (finals / 100.0 - 1.0).sort_values(ascending=False)
    lead, lag = rets.index[0], rets.index[-1]
    _headline(
        f"Over {p.preset}, <b>{ui.esc(_name(directory, lead))}</b> returned "
        f"<b>{ui.fmt_pct(rets.iloc[0], 1)}</b> versus "
        f"<b>{ui.fmt_pct(rets.iloc[-1], 1)}</b> for "
        f"<b>{ui.esc(_name(directory, lag))}</b>."
    )

    cards = []
    for sym in syms:
        ws = dal.window_stats(sym, p.start, p.end)
        if ws is None:
            continue
        cards.append({
            "icon": "📈", "label": sym, "value": ui.fmt_pct(ws["period_return"], 1),
            "change": f"{ui.fmt_pct(ws['ann_volatility'], 0, signed=False)} vol",
            "change_dir": "flat",
            "foot": f"CAGR {ui.fmt_pct(ws['cagr'], 1)} · DD {ui.fmt_pct(ws['max_drawdown'], 0)}",
        })
    if cards:
        ui.kpi_cards(cards)

    wide = charts.comparison_needs_log(pivot)
    fig = charts.indexed_comparison(pivot, pal, log=wide)
    charts.add_events(fig, pal, events.in_range(p.start, p.end), label=False)
    ui.chart(fig, key="ans_cmp",
             caption="Rebased to 100 at the window start so different price levels are comparable")
    ui.note(
        "Both series share one axis — a second y-scale can be aligned to imply any "
        "relationship you like, so it is never used here."
        + (" Log scale is on because the series end far apart." if wide else "")
    )


def company_detail(directory, pal, p):
    if not p.symbols:
        return st.info("Name a company, e.g. “How did Nvidia perform?”")
    sym = p.symbols[0]
    s_min, _ = dal.date_bounds(sym)
    cs = max(pd.to_datetime(p.start).date(), s_min).isoformat()

    ws = dal.window_stats(sym, cs, p.end)
    q = dal.quote(sym)
    if ws is None or q is None:
        return st.warning(f"No data for {sym} in this window.")

    _headline(
        f"<b>{ui.esc(_name(directory, sym))} ({ui.esc(sym)})</b> returned "
        f"<b>{ui.fmt_pct(ws['period_return'], 1)}</b> over {p.preset}, "
        f"with a worst drawdown of <b>{ui.fmt_pct(ws['max_drawdown'], 1)}</b>."
    )
    ui.kpi_cards([
        {"icon": "💵", "label": "Last price", "value": ui.fmt_price(q["close"]),
         "change": ui.fmt_pct(q["daily_return"], 2) if pd.notna(q["daily_return"]) else None,
         "change_dir": "up" if (q["daily_return"] or 0) >= 0 else "down",
         "foot": "Most recent close"},
        {"icon": "📈", "label": "CAGR", "value": ui.fmt_pct(ws["cagr"], 1),
         "foot": "Annualized over the window"},
        {"icon": "〰", "label": "Volatility",
         "value": ui.fmt_pct(ws["ann_volatility"], 1, signed=False),
         "foot": "Annualized"},
        {"icon": "📉", "label": "Max drawdown", "value": ui.fmt_pct(ws["max_drawdown"], 1),
         "change_dir": "down", "change": "peak to trough", "foot": "Deepest fall"},
        {"icon": "🔊", "label": "Avg volume", "value": ui.fmt_compact(ws["avg_volume"]),
         "foot": f"{ui.fmt_dollar_compact(ws['avg_dollar_volume'])} turnover"},
    ])

    px = dal.prices(sym, cs, p.end)
    if not px.empty:
        fig = charts.price_line(px, pal, label=ui.fmt_price(px["close"].iloc[-1]))
        charts.add_events(fig, pal, events.in_range(cs, p.end))
        ui.chart(fig, key="ans_co",
                 caption=f"{sym} price over {p.preset}")


def sector(directory, pal, p):
    df = dal.sector_performance(p.start, p.end)
    if df.empty:
        return st.warning("No data in this window.")
    df = df.sort_values("median_return", ascending=not _descending(p, True))
    top, bottom = df.iloc[0], df.iloc[-1]
    _headline(
        f"<b>{ui.esc(top['sector'])}</b> led over {ui.esc(p.preset)} with a median member "
        f"return of <b>{ui.fmt_pct(top['median_return'], 1)}</b>; "
        f"<b>{ui.esc(bottom['sector'])}</b> lagged at "
        f"<b>{ui.fmt_pct(bottom['median_return'], 1)}</b>."
    )
    # A stated count trims the chart; the default leaves every sector visible,
    # since the comparison between them is the point of this answer.
    shown = df.head(p.limit) if p.source("limit") == "question" else df
    ui.chart(charts.sector_bars(shown, pal), key="ans_sector")
    ui.note(
        "Median member return, not the mean — over long windows one very large "
        "company can otherwise stand in for its whole sector."
    )


def market_summary(directory, pal, p):
    ws = dal.window_stats(INDEX_SYMBOL, p.start, p.end)
    movers = dal.movers_ranked(p.start, p.end, sector=p.sector, limit=-1)
    if ws is None:
        return st.warning("No data in this window.")
    adv = int((movers["period_return"] > 0).sum()) if not movers.empty else 0
    _headline(
        f"The S&P 500 returned <b>{ui.fmt_pct(ws['period_return'], 1)}</b> over {p.preset}, "
        f"with <b>{adv} of {len(movers)}</b> companies advancing."
    )
    ui.kpi_cards([
        {"icon": "📅", "label": f"Return · {p.preset}", "value": ui.fmt_pct(ws["period_return"], 1),
         "foot": f"{int(ws['trading_days']):,} sessions"},
        {"icon": "📈", "label": "CAGR", "value": ui.fmt_pct(ws["cagr"], 1),
         "foot": "Annualized"},
        {"icon": "〰", "label": "Volatility",
         "value": ui.fmt_pct(ws["ann_volatility"], 1, signed=False), "foot": "Annualized"},
        {"icon": "📉", "label": "Max drawdown", "value": ui.fmt_pct(ws["max_drawdown"], 1),
         "change_dir": "down", "change": "peak to trough", "foot": "Deepest fall"},
    ])
    px = dal.prices(INDEX_SYMBOL, p.start, p.end)
    if not px.empty:
        fig = charts.price_line(px, pal, label=ui.fmt_price(px["close"].iloc[-1], 0))
        charts.add_events(fig, pal, events.in_range(p.start, p.end))
        ui.chart(fig, key="ans_mkt",
                 caption="Index level with market events marked")


HANDLERS = {
    "top_volume": top_volume,
    "top_gainers": top_gainers,
    "top_losers": top_losers,
    "biggest_drawdown": biggest_drawdown,
    "most_volatile": most_volatile,
    "least_volatile": least_volatile,
    "best_cagr": best_cagr,
    "compare": compare,
    "company_detail": company_detail,
    "sector": sector,
    "market_summary": market_summary,
}


# ---------------------------------------------------------------------------
# Generated-SQL results
# ---------------------------------------------------------------------------
# Everything above answers a question the app was built to answer, with a chart
# chosen for that question. Below is the other case: a result set whose shape is
# only known at runtime. The chart is therefore inferred, and inferred
# conservatively -- a table is a correct answer, a misleading chart is not.

_DATE_COLS = ("date", "day", "session", "year_month")
_LABEL_COLS = ("symbol", "name", "sector", "industry", "year", "month", "ticker", "company")
_RATE_HINTS = ("return", "cagr", "volatility", "drawdown", "yield", "pct", "ratio")


def _numeric_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _looks_like_rate(col: str, series: pd.Series) -> bool:
    """A fraction to render as a percentage, rather than a price or a count.

    The name alone isn't enough -- a column called `total_return` holding 8500
    is already in percent, and formatting it as one again would read as 850,000%.
    So the magnitude has to agree with the name.
    """
    if not any(h in col.lower() for h in _RATE_HINTS):
        return False
    peak = pd.to_numeric(series, errors="coerce").abs().max()
    return bool(pd.notna(peak) and peak <= 10)


def generated(question: str, df: pd.DataFrame, pal, *, insight_text: str = "",
              key: str = "ans_gen") -> None:
    """Render a result set produced by generated SQL: insight, chart, table.

    No SQL appears here -- this is User Mode. The statement is disclosed in the
    Developer Center, collapsed, alongside the route that produced it.
    """
    if df is None or df.empty:
        return st.warning("That returned no rows.")

    if insight_text:
        _headline(ui.esc(insight_text))

    numeric = _numeric_cols(df)
    lowered = {c.lower(): c for c in df.columns}
    date_col = next((lowered[c] for c in _DATE_COLS if c in lowered), None)
    label_col = next((lowered[c] for c in _LABEL_COLS if c in lowered), None)

    # One chart, only when the shape clearly supports one. Two or more numeric
    # columns against a date would need a shared-scale decision this path has no
    # basis to make, so it stays a table.
    value_col = numeric[0] if numeric else None
    if value_col is not None and len(df) > 1:
        tickfmt = ".0%" if _looks_like_rate(value_col, df[value_col]) else None
        fig = None
        if date_col and len(numeric) == 1 and len(df) >= 5:
            # area_series builds its hovertemplate by concatenation, so the format
            # has to be a real string -- None would raise rather than default.
            fig = charts.area_series(
                df.rename(columns={date_col: "date"}).assign(
                    **{value_col: pd.to_numeric(df[value_col], errors="coerce")}),
                value_col, pal, y_title=value_col.replace("_", " ").title(),
                height=300, tickformat=tickfmt or ",.2f")
        elif label_col and label_col != value_col and len(df) <= 25:
            fig = charts.generic_bars(df, pal, label_col=label_col,
                                      value_col=value_col, tickformat=tickfmt)
        if fig is not None:
            ui.chart(fig, key=f"{key}_chart",
                     caption=ui.esc(question))

    pretty = df.copy()
    pretty.columns = [str(c).replace("_", " ").strip().title() for c in pretty.columns]
    ui.data_table(pretty, key=f"{key}_tbl", csv_name="answer.csv",
                  height=min(340, 60 + 35 * len(pretty)))
    ui.note(
        "Figures come from the same database as every other page. Returns are price "
        "returns — dividends are excluded — and companies listed later have shorter "
        "histories than those listed in 2001."
    )

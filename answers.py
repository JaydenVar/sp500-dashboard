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
from charts import PLOT_CONFIG
from universe import INDEX_SYMBOL


def _headline(text: str) -> None:
    st.markdown(f'<div class="answer">{text}</div>', unsafe_allow_html=True)


def _movers(start: str, end: str) -> pd.DataFrame:
    return dal.period_movers(start, end)


def _name(directory: pd.DataFrame, sym: str) -> str:
    row = directory[directory["symbol"] == sym]
    return str(row.iloc[0]["name"]) if len(row) else sym


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def top_volume(directory, pal, start, end, symbols, preset):
    df = _movers(start, end)
    if df.empty:
        return st.warning("No data in this window.")
    top = df.sort_values("avg_dollar_volume", ascending=False).head(10)
    w = top.iloc[0]
    _headline(
        f"<b>{ui.esc(w['name'])} ({ui.esc(w['symbol'])})</b> traded the most, averaging "
        f"<b>{ui.fmt_dollar_compact(w['avg_dollar_volume'])}</b> of turnover a day over {preset}."
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


def _gainers_losers(directory, pal, start, end, preset, best: bool, key: str):
    df = _movers(start, end)
    if df.empty:
        return st.warning("No data in this window.")
    df = df.sort_values("period_return", ascending=not best)
    top = df.head(10)
    w = top.iloc[0]
    verb = "gained" if best else "lost"
    _headline(
        f"<b>{ui.esc(w['name'])} ({ui.esc(w['symbol'])})</b> {verb} the most over {preset}, "
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


def top_gainers(directory, pal, start, end, symbols, preset):
    _gainers_losers(directory, pal, start, end, preset, True, "ans_win")


def top_losers(directory, pal, start, end, symbols, preset):
    _gainers_losers(directory, pal, start, end, preset, False, "ans_lose")


def biggest_drawdown(directory, pal, start, end, symbols, preset):
    lb = dal.leaderboard()
    if lb.empty:
        return st.warning("No data available.")
    w = lb.sort_values("max_drawdown").iloc[0]
    _headline(
        f"<b>{ui.esc(w['name'])} ({ui.esc(w['symbol'])})</b> suffered the deepest fall: "
        f"<b>{ui.fmt_pct(w['max_drawdown'], 1)}</b> from a previous high."
    )
    ui.note(
        "Drawdown is measured from each company's running all-time high over its "
        "full listed history — it is the loss an investor would have had to sit "
        "through, not a single-day move."
    )
    dd = dal.drawdowns(str(w["symbol"]), start, end)
    if not dd.empty:
        fig = charts.area_series(dd, "drawdown", pal, color_key="red",
                                 y_title="Drawdown", height=300, tickformat=".0%")
        charts.add_events(fig, pal, events.in_range(start, end))
        ui.chart(fig, key="ans_dd", config=PLOT_CONFIG, controls=False,
                 caption=f"{w['symbol']} drawdown over {preset}")

    tbl = lb.sort_values("max_drawdown").head(10)
    out = tbl.assign(MaxDD=tbl["max_drawdown"] * 100, Vol=tbl["ann_volatility"] * 100)[
        ["symbol", "name", "sector", "MaxDD", "Vol"]]
    out.columns = ["Ticker", "Company", "Sector", "Max drawdown", "Volatility"]
    ui.data_table(out, key="ans_ddt", search_cols=("Ticker", "Company"),
                  csv_name="deepest_drawdowns.csv", height=340,
                  column_config={
                      "Max drawdown": st.column_config.NumberColumn(format="%.1f%%"),
                      "Volatility": st.column_config.NumberColumn(format="%.1f%%")})


def _by_volatility(directory, pal, most: bool, key: str):
    lb = dal.leaderboard().dropna(subset=["ann_volatility"])
    if lb.empty:
        return st.warning("No data available.")
    df = lb.sort_values("ann_volatility", ascending=not most)
    w = df.iloc[0]
    _headline(
        f"<b>{ui.esc(w['name'])} ({ui.esc(w['symbol'])})</b> has been the "
        f"{'most volatile' if most else 'steadiest'}, at "
        f"<b>{ui.fmt_pct(w['ann_volatility'], 1, signed=False)}</b> annualized."
    )
    ui.note(
        "Volatility is the annualized standard deviation of daily moves over each "
        "company's full history. Higher means a rougher ride — not necessarily a "
        "worse outcome; check the return and drawdown alongside it."
    )
    top = df.head(10)
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


def most_volatile(directory, pal, start, end, symbols, preset):
    _by_volatility(directory, pal, True, "ans_vola")


def least_volatile(directory, pal, start, end, symbols, preset):
    _by_volatility(directory, pal, False, "ans_stable")


def best_cagr(directory, pal, start, end, symbols, preset):
    lb = dal.leaderboard().sort_values("cagr", ascending=False)
    if lb.empty:
        return st.warning("No data available.")
    w = lb.iloc[0]
    _headline(
        f"<b>{ui.esc(w['name'])} ({ui.esc(w['symbol'])})</b> compounded fastest at "
        f"<b>{ui.fmt_pct(w['cagr'], 1)}</b> a year over {w['years']:.0f} years."
    )
    ui.note(
        "CAGR is each company's own listed history, so the periods differ — "
        "'Years' is shown so a shorter, luckier run isn't mistaken for a longer one."
    )
    top = lb.head(10)
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


def compare(directory, pal, start, end, symbols, preset):
    syms = symbols[:4]
    if len(syms) < 2:
        return st.info("Name two companies to compare, e.g. “Compare Apple and Microsoft”.")

    pivot = dal.indexed_comparison(tuple(syms), start, end)
    if pivot.empty:
        return st.warning("No overlapping history for those companies in this window.")

    finals = pivot.ffill().iloc[-1]
    rets = (finals / 100.0 - 1.0).sort_values(ascending=False)
    lead, lag = rets.index[0], rets.index[-1]
    _headline(
        f"Over {preset}, <b>{ui.esc(_name(directory, lead))}</b> returned "
        f"<b>{ui.fmt_pct(rets.iloc[0], 1)}</b> versus "
        f"<b>{ui.fmt_pct(rets.iloc[-1], 1)}</b> for "
        f"<b>{ui.esc(_name(directory, lag))}</b>."
    )

    cards = []
    for sym in syms:
        ws = dal.window_stats(sym, start, end)
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
    charts.add_events(fig, pal, events.in_range(start, end), label=False)
    ui.chart(fig, key="ans_cmp", config=PLOT_CONFIG,
             caption="Rebased to 100 at the window start so different price levels are comparable")
    ui.note(
        "Both series share one axis — a second y-scale can be aligned to imply any "
        "relationship you like, so it is never used here."
        + (" Log scale is on because the series end far apart." if wide else "")
    )


def company_detail(directory, pal, start, end, symbols, preset):
    if not symbols:
        return st.info("Name a company, e.g. “How did Nvidia perform?”")
    sym = symbols[0]
    s_min, _ = dal.date_bounds(sym)
    cs = max(pd.to_datetime(start).date(), s_min).isoformat()

    ws = dal.window_stats(sym, cs, end)
    q = dal.quote(sym)
    if ws is None or q is None:
        return st.warning(f"No data for {sym} in this window.")

    _headline(
        f"<b>{ui.esc(_name(directory, sym))} ({ui.esc(sym)})</b> returned "
        f"<b>{ui.fmt_pct(ws['period_return'], 1)}</b> over {preset}, "
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

    px = dal.prices(sym, cs, end)
    if not px.empty:
        fig = charts.price_line(px, pal, label=ui.fmt_price(px["close"].iloc[-1]))
        charts.add_events(fig, pal, events.in_range(cs, end))
        ui.chart(fig, key="ans_co", config=PLOT_CONFIG,
                 caption=f"{sym} price over {preset}")


def sector(directory, pal, start, end, symbols, preset):
    df = dal.sector_performance(start, end)
    if df.empty:
        return st.warning("No data in this window.")
    df = df.sort_values("median_return", ascending=False)
    top, bottom = df.iloc[0], df.iloc[-1]
    _headline(
        f"<b>{ui.esc(top['sector'])}</b> led over {preset} with a median member return of "
        f"<b>{ui.fmt_pct(top['median_return'], 1)}</b>; "
        f"<b>{ui.esc(bottom['sector'])}</b> lagged at "
        f"<b>{ui.fmt_pct(bottom['median_return'], 1)}</b>."
    )
    ui.chart(charts.sector_bars(df, pal), key="ans_sector", config=PLOT_CONFIG,
             controls=False)
    ui.note(
        "Median member return, not the mean — over long windows one very large "
        "company can otherwise stand in for its whole sector."
    )


def market_summary(directory, pal, start, end, symbols, preset):
    ws = dal.window_stats(INDEX_SYMBOL, start, end)
    movers = _movers(start, end)
    if ws is None:
        return st.warning("No data in this window.")
    adv = int((movers["period_return"] > 0).sum()) if not movers.empty else 0
    _headline(
        f"The S&P 500 returned <b>{ui.fmt_pct(ws['period_return'], 1)}</b> over {preset}, "
        f"with <b>{adv} of {len(movers)}</b> companies advancing."
    )
    ui.kpi_cards([
        {"icon": "📅", "label": f"Return · {preset}", "value": ui.fmt_pct(ws["period_return"], 1),
         "foot": f"{int(ws['trading_days']):,} sessions"},
        {"icon": "📈", "label": "CAGR", "value": ui.fmt_pct(ws["cagr"], 1),
         "foot": "Annualized"},
        {"icon": "〰", "label": "Volatility",
         "value": ui.fmt_pct(ws["ann_volatility"], 1, signed=False), "foot": "Annualized"},
        {"icon": "📉", "label": "Max drawdown", "value": ui.fmt_pct(ws["max_drawdown"], 1),
         "change_dir": "down", "change": "peak to trough", "foot": "Deepest fall"},
    ])
    px = dal.prices(INDEX_SYMBOL, start, end)
    if not px.empty:
        fig = charts.price_line(px, pal, label=ui.fmt_price(px["close"].iloc[-1], 0))
        charts.add_events(fig, pal, events.in_range(start, end))
        ui.chart(fig, key="ans_mkt", config=PLOT_CONFIG,
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

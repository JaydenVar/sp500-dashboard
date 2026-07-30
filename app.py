"""Market Analytics — a SQL-first equity platform.

Two deliberately separate experiences share this entry point:

* **User Mode** (default) is the financial product. It shows markets, companies,
  performance and risk, and never surfaces SQL, schemas, timings or any other
  implementation detail -- mixing those into an end-user screen is what makes an
  application read as a class project rather than a product.
* **Developer Center** (see devcenter.py) is where all of that technical material
  lives, organized for someone evaluating the engineering.

Underneath both: every figure on screen is the output of a SQL query
(see queries.py) against a SQLite database whose analysis lives in views and
materialized rollups (see build_db.py). This module lays out and draws; it
computes no metrics of its own.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

import answers
import ask
import charts
import components as ui
import data_access as dal
import devcenter
import events
import queries
from charts import PLOT_CONFIG
from theme import PALETTES, app_css
from universe import INDEX_SYMBOL

st.set_page_config(
    page_title="Market Analytics — SQL Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Date presets. Value is (label -> lookback in days | sentinel).
PRESETS: dict[str, object] = {
    "1M": 30, "3M": 91, "6M": 182, "YTD": "ytd",
    "1Y": 365, "3Y": 1095, "5Y": 1826, "10Y": 3653, "MAX": "max",
}
DEFAULT_PRESET = "MAX"


def resolve_range(preset: str, min_d: dt.date, max_d: dt.date) -> tuple[dt.date, dt.date]:
    if preset == "max":
        return min_d, max_d
    if preset == "ytd":
        return max(dt.date(max_d.year, 1, 1), min_d), max_d
    spec = PRESETS.get(preset, "max")
    if spec == "max":
        return min_d, max_d
    if spec == "ytd":
        return max(dt.date(max_d.year, 1, 1), min_d), max_d
    return max(max_d - dt.timedelta(days=int(spec)), min_d), max_d


# ---------------------------------------------------------------------------
# Theme + chrome
# ---------------------------------------------------------------------------
if "theme" not in st.session_state:
    st.session_state.theme = "Light"

pal = PALETTES[st.session_state.theme]
st.markdown(app_css(pal), unsafe_allow_html=True)

directory = dal.directory()
equities = directory[directory["is_index"] == 0].reset_index(drop=True)
last_date = dal.global_last_date()

ui.header(
    "Market Analytics",
    "S&P 500 index and large-cap equities · 25 years of daily history, analyzed in SQL",
    last_date,
    len(directory),
)

# ---------------------------------------------------------------------------
# Control row — one row, above everything it scopes
# ---------------------------------------------------------------------------
ctl = st.columns([4.0, 1.5, 1.9])
with ctl[0]:
    preset = st.radio(
        "Date range", list(PRESETS), index=list(PRESETS).index(DEFAULT_PRESET),
        horizontal=True, key="preset", label_visibility="collapsed",
    )
with ctl[1]:
    theme_choice = st.radio(
        "Theme", ["Light", "Dark"], horizontal=True, label_visibility="collapsed",
        index=0 if st.session_state.theme == "Light" else 1, key="theme_pick",
    )
    if theme_choice != st.session_state.theme:
        st.session_state.theme = theme_choice
        st.rerun()
with ctl[2]:
    mode = st.radio(
        "Mode", ["User", "Developer"], horizontal=True, key="mode",
        label_visibility="collapsed",
        help="User Mode is the financial product. Developer Center documents how it's built.",
    )

DEV = mode == "Developer"

index_min, index_max = dal.date_bounds(INDEX_SYMBOL)
start_d, end_d = resolve_range(preset, index_min, index_max)
START, END = start_d.isoformat(), end_d.isoformat()

st.markdown(
    f'<div class="note">'
    f'<span class="modepill{" dev" if DEV else ""}">'
    f'{"◆ Developer Center" if DEV else "● User Mode"}</span>'
    f'&nbsp;&nbsp;Window <b>{start_d:%b %d, %Y}</b> → <b>{end_d:%b %d, %Y}</b>'
    f' · {preset} · scopes every section below</div>',
    unsafe_allow_html=True,
)

# Two entirely separate experiences. User Mode never shows SQL, schemas, timings
# or implementation detail -- mixing those into an end-user screen is what makes
# an app read as a class project rather than a product.
USER_SECTIONS = ["Overview", "Market", "Companies", "Performance", "Risk", "Portfolio", "About"]

# Deliberately a radio, not st.tabs. st.tabs renders EVERY tab's body on every
# rerun, which (a) runs all sections' queries when only one is visible and
# (b) makes Plotly measure charts inside hidden tabs, so they render at a
# fraction of the container width. Rendering only the active section fixes both.
section = st.radio(
    "Section", devcenter.SECTIONS if DEV else USER_SECTIONS,
    horizontal=True, key="dev_section" if DEV else "section",
    label_visibility="collapsed",
)

if DEV:
    devcenter.render(section, directory, START, END)


# ---------------------------------------------------------------------------
# Overview — the index
# ---------------------------------------------------------------------------
if not DEV and section == "Overview":
    # Ask the Market -- natural language routed to real queries. No LLM: intents
    # are keyword-scored and answered from the same SQL the rest of the app uses,
    # so an answer can never disagree with the page beside it.
    asked = st.text_input(
        "Ask the Market", key="ask_q", label_visibility="collapsed",
        placeholder="Ask the Market…  e.g. “What stock had the highest trading volume?”",
    )
    ex_cols = st.columns(len(ask.EXAMPLES))
    for i_e, ex in enumerate(ask.EXAMPLES):
        with ex_cols[i_e]:
            if st.button(ex, key=f"ask_ex_{i_e}", use_container_width=True):
                st.session_state.ask_q = ex
                st.rerun()

    if asked and asked.strip():
        intent, syms, yr = ask.match(asked, directory)
        if intent is None:
            st.info(
                "I couldn't match that to a question I can answer yet. Try one of "
                "the suggestions above, or name a company."
            )
        else:
            a_start, a_end = START, END
            a_preset = preset
            if yr:  # "since 2020" overrides the window for this answer
                y_start = dt.date(yr, 1, 1)
                if index_min <= y_start <= end_d:
                    a_start, a_preset = y_start.isoformat(), f"since {yr}"
            answers.HANDLERS[intent.handler](directory, pal, a_start, a_end, syms, a_preset)
        st.divider()

    q = dal.quote(INDEX_SYMBOL)
    ws = dal.window_stats(INDEX_SYMBOL, START, END)
    px = dal.prices(INDEX_SYMBOL, START, END)

    if ws is None or px.empty or q is None:
        st.warning("No data in the selected window.")
    else:
        ui.quote_strip(
            "S&P 500 Index", INDEX_SYMBOL, q,
            f"Index · latest session {pd.to_datetime(q['date']):%b %d, %Y}", pal,
        )

        ui.kpi_cards([
            {"icon": "📅", "label": f"Return · {preset}", "value": ui.fmt_pct(ws["period_return"], 1),
             "change": f"{ws['trading_days']:,} sessions", "change_dir": "flat",
             "foot": "Close-to-close over the window"},
            {"icon": "📈", "label": "CAGR", "value": ui.fmt_pct(ws["cagr"], 1),
             "foot": "Annualized over the window"},
            {"icon": "🏔", "label": "Window high", "value": ui.fmt_price(ws["period_high"], 0),
             "foot": f"Low {ui.fmt_price(ws['period_low'], 0)}"},
            {"icon": "📉", "label": "Max drawdown", "value": ui.fmt_pct(ws["max_drawdown"], 1),
             "change": "peak to trough", "change_dir": "down",
             "foot": "Deepest fall from a running high"},
            {"icon": "〰", "label": "Volatility", "value": ui.fmt_pct(ws["ann_volatility"], 1, signed=False),
             "foot": "Annualized, from daily returns"},
            {"icon": "🔁", "label": "52-week range", "value": ui.fmt_price(q["w52_low"], 0), "small": True,
             "foot": f"to {ui.fmt_price(q['w52_high'], 0)} · trailing 1y"},
        ])

        ui.section("Index level", f"{preset} · hover for the crosshair readout")
        c1, c2 = st.columns([1.4, 1.4])
        with c1:
            scale = st.radio("Scale", ["Linear", "Log"], horizontal=True,
                             key="ov_scale", label_visibility="collapsed")
        with c2:
            show_events = st.toggle("Market events", value=True, key="ov_events",
                                    help="Mark the crashes, bottoms and turning points")

        fig = charts.price_line(px, pal, log=(scale == "Log"),
                                label=ui.fmt_price(px["close"].iloc[-1], 0))
        evts = events.in_range(START, END) if show_events else []
        charts.add_events(fig, pal, evts)
        ui.chart(fig, key="ov_price", config=PLOT_CONFIG,
                 caption="Drag to pan · drag-select to zoom · toolbar (on hover) for autoscale and PNG export")

        if evts:
            ui.note(
                f"{len(evts)} market events marked — hover a diamond for what happened "
                "and why the chart moves there."
            )

        ui.section("Trading volume", "Daily share volume")
        ui.chart(charts.volume_bars(px, pal), key="ov_vol", config=PLOT_CONFIG)


# ---------------------------------------------------------------------------
# Market — breadth, sectors, movers
# ---------------------------------------------------------------------------
if not DEV and section == "Market":
    movers = dal.period_movers(START, END)
    sectors = dal.sector_performance(START, END)

    if movers.empty:
        st.warning("No symbols have data in this window.")
    else:
        adv = int((movers["period_return"] > 0).sum())
        dec = int((movers["period_return"] < 0).sum())
        med = float(movers["period_return"].median())
        best, worst = movers.iloc[0], movers.iloc[-1]

        ui.kpi_cards([
            {"icon": "📊", "label": "Advancing", "value": f"{adv}",
             "change": f"of {len(movers)}", "change_dir": "up", "foot": "Positive over the window"},
            {"icon": "📉", "label": "Declining", "value": f"{dec}",
             "change": f"of {len(movers)}", "change_dir": "down", "foot": "Negative over the window"},
            {"icon": "🎯", "label": "Median return", "value": ui.fmt_pct(med, 1),
             "foot": "Middle symbol, equal-weighted"},
            {"icon": "🚀", "label": "Top gainer", "value": ui.esc(best["symbol"]), "small": True,
             "change": ui.fmt_pct(best["period_return"], 1), "change_dir": "up",
             "foot": str(best["name"])[:30]},
            {"icon": "🔻", "label": "Worst decliner", "value": ui.esc(worst["symbol"]), "small": True,
             "change": ui.fmt_pct(worst["period_return"], 1), "change_dir": "down",
             "foot": str(worst["name"])[:30]},
        ])

        ui.section("Sector performance", f"Median member return · {preset}")
        ui.chart(charts.sector_bars(sectors, pal), key="mkt_sector", config=PLOT_CONFIG, controls=False)
        ui.note(
            "Median member return, not the mean: over long windows a mean of total "
            "returns is dominated by one outlier (a single +85,000% name pulls a "
            "sector 'average' into five figures), describing that stock rather than "
            "the sector. Hover a bar for the mean, best and worst alongside. Members "
            "are equal-weighted, not market-cap-weighted, since share counts aren't "
            "available from the free data source."
        )

        ui.section("Movers", f"Every symbol ranked by {preset} return")
        # Percent columns are scaled to whole percents here. Streamlit's "%%"
        # number format only appends a literal '%' -- it does not multiply -- so
        # passing raw fractions would understate every figure by 100x.
        tbl = movers.assign(
            Return=movers["period_return"] * 100,
            Start=movers["start_close"].round(2),
            End=movers["end_close"].round(2),
            Liquidity=movers["avg_dollar_volume"],
        )[["symbol", "name", "sector", "Start", "End", "Return", "Liquidity", "sessions"]]
        tbl.columns = ["Ticker", "Company", "Sector", "Start", "End", "Return", "Avg $ vol", "Sessions"]
        ui.data_table(
            tbl, key="movers",
            search_cols=("Ticker", "Company", "Sector"),
            sort_options={"Return": "Return", "Avg $ volume": "Avg $ vol",
                          "Ticker": "Ticker", "Company": "Company"},
            default_sort="Return",
            csv_name=f"movers_{preset}.csv",
            column_config={
                "Return": st.column_config.NumberColumn("Return", format="%+.2f%%",
                                                        help="Close-to-close over the window"),
                "Avg $ vol": st.column_config.NumberColumn("Avg $ vol", format="compact"),
                "Start": st.column_config.NumberColumn("Start", format="%.2f"),
                "End": st.column_config.NumberColumn("End", format="%.2f"),
            },
        )


# ---------------------------------------------------------------------------
# Companies — search, overview, per-symbol charts, comparison
# ---------------------------------------------------------------------------
if not DEV and section == "Companies":
    # Autocomplete: one option per symbol, searchable by ticker OR company name
    # because the label contains both and Streamlit's selectbox filters substrings.
    label_by_key = {f"{r.symbol} — {r['name']}": r.symbol for _, r in equities.iterrows()}
    keys = list(label_by_key)
    default_key = next((k for k in keys if label_by_key[k] == "AAPL"), keys[0])

    pick = st.columns([2.6, 1.4])
    with pick[0]:
        chosen_label = st.selectbox(
            "Search company or ticker", keys,
            index=keys.index(st.session_state.get("co_symbol", default_key))
            if st.session_state.get("co_symbol") in keys else keys.index(default_key),
            key="co_symbol",
            help="Type a ticker (AAPL) or a company name (Apple)",
        )
    sym = label_by_key[chosen_label]
    meta = equities[equities["symbol"] == sym].iloc[0]

    with pick[1]:
        view_mode = st.radio("View", ["Line", "Candles"], horizontal=True,
                             key="co_view", label_visibility="collapsed")

    s_min, s_max = dal.date_bounds(sym)
    c_start = max(start_d, s_min)
    cs, ce = c_start.isoformat(), end_d.isoformat()

    cq = dal.quote(sym)
    cws = dal.window_stats(sym, cs, ce)
    cpx = dal.prices(sym, cs, ce)

    if cq is None or cws is None or cpx.empty:
        st.warning(f"No data for {sym} in this window.")
    else:
        listed_note = ""
        if s_min > start_d:
            listed_note = f" · history starts {s_min:%b %Y}, after the selected window opens"
        ui.quote_strip(
            str(meta["name"]), sym, cq,
            f"{meta['sector']} · {meta['industry']}{listed_note}", pal,
        )

        ui.kpi_cards([
            {"icon": "📅", "label": f"Return · {preset}", "value": ui.fmt_pct(cws["period_return"], 1),
             "change": f"{cws['trading_days']:,} sessions", "change_dir": "flat",
             "foot": f"{pd.to_datetime(cws['first_date']):%b %Y} → {pd.to_datetime(cws['last_date']):%b %Y}"},
            {"icon": "📈", "label": "CAGR", "value": ui.fmt_pct(cws["cagr"], 1),
             "foot": "Annualized over the window"},
            {"icon": "🏔", "label": "Highest close", "value": ui.fmt_price(cws["highest_close"]),
             "foot": f"Lowest {ui.fmt_price(cws['lowest_close'])}"},
            {"icon": "🔁", "label": "52-week high", "value": ui.fmt_price(cq["w52_high"]),
             "foot": f"Low {ui.fmt_price(cq['w52_low'])} · trailing 1y"},
            {"icon": "🔊", "label": "Avg volume", "value": ui.fmt_compact(cws["avg_volume"]),
             "foot": f"{ui.fmt_dollar_compact(cws['avg_dollar_volume'])} avg turnover"},
            {"icon": "〰", "label": "Volatility", "value": ui.fmt_pct(cws["ann_volatility"], 1, signed=False),
             "change": ui.fmt_pct(cws["max_drawdown"], 0) + " max DD", "change_dir": "down",
             "foot": "Annualized, from daily returns"},
        ])
        ui.note(
            "Market cap is intentionally absent: it needs share-count data that no "
            "reachable free endpoint provides, and a stale hardcoded figure would be "
            "worse than none. Average dollar turnover is shown instead — it is computed "
            "from data actually in the database."
        )

        ui.section("Price history", f"{sym} · {preset}")
        if view_mode == "Candles":
            ui.chart(charts.candlestick(cpx, pal), key="co_candle", config=PLOT_CONFIG)
        else:
            fig_co = charts.price_line(cpx, pal, label=ui.fmt_price(cpx["close"].iloc[-1]))
            charts.add_events(fig_co, pal, events.in_range(cs, ce))
            ui.chart(fig_co, key="co_price", config=PLOT_CONFIG)

        ui.section("Moving averages", "Close with trailing 50- and 200-session means")
        ma = dal.moving_averages(sym, cs, ce)
        ui.chart(charts.moving_average_chart(ma, pal), key="co_ma", config=PLOT_CONFIG)
        ui.note(
            "A moving-average line only begins once its full window exists, so the "
            "200-session average is absent for the first 200 sessions rather than "
            "being averaged from fewer points than its label claims."
        )

        left, right = st.columns(2)
        with left:
            ui.section("Trading volume")
            ui.chart(charts.volume_bars(cpx, pal, height=240), key="co_vol", config=PLOT_CONFIG, controls=False)
        with right:
            ui.section("Daily returns")
            dr = dal.daily_returns(sym, cs, ce)
            ui.chart(charts.returns_bars(dr, pal, height=240), key="co_ret", config=PLOT_CONFIG, controls=False)

        l2, r2 = st.columns(2)
        with l2:
            ui.section("Cumulative return", "Compounded from daily returns")
            cr = dal.cumulative_return(sym, cs, ce)
            if not cr.empty:
                ui.chart(
                    charts.area_series(cr, "cumulative_return", pal, color_key="blue",
                                       y_title="Cumulative", height=280,
                                       label=ui.fmt_pct(cr["cumulative_return"].iloc[-1], 0)),
                    key="co_cum", config=PLOT_CONFIG, controls=False,
                )
        with r2:
            ui.section("Rolling volatility", "21-session, annualized")
            cv = dal.volatility(sym, cs, ce)
            if not cv.empty:
                ui.chart(
                    charts.area_series(cv, "ann_volatility_21d", pal, color_key="red",
                                       y_title="Ann. volatility", height=280,
                                       label=ui.fmt_pct(cv["ann_volatility_21d"].iloc[-1], 0, signed=False),
                                       zero_line=False),
                    key="co_vola", config=PLOT_CONFIG, controls=False,
                )

        # ---- comparison ----
        ui.section("Comparison", "Rebased to 100 at the window start — one shared axis")
        default_cmp = [s for s in (sym, "MSFT", INDEX_SYMBOL) if s in set(directory["symbol"])]
        cmp_syms = st.multiselect(
            "Compare symbols", options=list(directory["symbol"]),
            default=st.session_state.get("cmp_syms", default_cmp[:3]),
            key="cmp_syms", max_selections=8,
            help="Up to 8. Each keeps its own color as the selection changes.",
        )
        if len(cmp_syms) < 2:
            ui.note("Pick at least two symbols to compare.")
        else:
            pivot = dal.indexed_comparison(tuple(cmp_syms), START, END)
            if pivot.empty:
                ui.note("No overlapping data for that selection in this window.")
            else:
                # Default to log when the spread is wide enough that a linear axis
                # would flatten the smaller series onto the baseline.
                wide = charts.comparison_needs_log(pivot)
                cscale = st.radio(
                    "Comparison scale", ["Log", "Linear"] if wide else ["Linear", "Log"],
                    horizontal=True, key="cmp_scale", label_visibility="collapsed",
                )
                ui.chart(
                    charts.indexed_comparison(pivot, pal, log=(cscale == "Log")),
                    key="co_cmp", config=PLOT_CONFIG,
                )
                if wide and cscale == "Log":
                    ui.note(
                        "Log scale is on by default here because these series end more "
                        "than 20x apart — on a linear axis the smaller ones would flatten "
                        "onto the baseline. On a log axis equal ratios take equal vertical "
                        "space, so each series stays readable. Still one shared axis."
                    )
                finals = pivot.ffill().iloc[-1].sort_values(ascending=False)
                rows = pd.DataFrame({
                    "Ticker": finals.index,
                    "Indexed (start=100)": finals.values.round(1),
                    "Window return": (finals.values - 100.0),  # already indexed to 100 = whole percents
                    "First data": [pivot[c].first_valid_index().date() for c in finals.index],
                })
                st.dataframe(
                    rows, use_container_width=True, hide_index=True,
                    column_config={
                        "Window return": st.column_config.NumberColumn("Window return", format="%+.2f%%"),
                    },
                )
                ui.note(
                    "Rebasing to 100 puts every symbol on one axis. A second y-axis is "
                    "deliberately never used: two independent scales can be aligned to "
                    "suggest any correlation you like. Where a symbol listed after the "
                    "window opened, its series starts later — see 'First data'."
                )


# ---------------------------------------------------------------------------
# Performance — leaderboards over each symbol's own history
# ---------------------------------------------------------------------------
if not DEV and section == "Performance":
    lb = dal.leaderboard()
    ui.section("Long-run performance", "Each symbol over its own listed history")
    ui.note(
        "These are all-time figures per symbol, so the periods are NOT equal — a name "
        "listed in 2012 has not had the same run as one listed in 2001. 'Years' and "
        "'From' are shown so the comparison stays honest. Use the Market tab for "
        "like-for-like returns inside a single window."
    )

    # As above: scale fractions to whole percents, because the "%%" format only
    # appends a percent sign rather than multiplying.
    disp = lb.assign(
        Ticker=lb["symbol"], Company=lb["name"], Sector=lb["sector"],
        From=pd.to_datetime(lb["first_date"]).dt.date,
        Years=lb["years"].round(1),
        Last=lb["last_close"].round(2),
        Total=lb["total_return"] * 100, CAGR=lb["cagr"] * 100,
        Vol=lb["ann_volatility"] * 100, MaxDD=lb["max_drawdown"] * 100,
    )[["Ticker", "Company", "Sector", "From", "Years", "Last", "Total", "CAGR", "Vol", "MaxDD"]]

    ui.data_table(
        disp, key="perf",
        search_cols=("Ticker", "Company", "Sector"),
        sort_options={
            "CAGR": "CAGR", "Total return": "Total", "Volatility": "Vol",
            "Max drawdown": "MaxDD", "Years": "Years", "Ticker": "Ticker",
        },
        default_sort="CAGR",
        csv_name="performance.csv",
        height=460,
        column_config={
            "Total": st.column_config.NumberColumn("Total return", format="%+.1f%%"),
            "CAGR": st.column_config.NumberColumn("CAGR", format="%+.2f%%"),
            "Vol": st.column_config.NumberColumn("Volatility", format="%.2f%%"),
            "MaxDD": st.column_config.NumberColumn("Max DD", format="%.1f%%"),
            "Last": st.column_config.NumberColumn("Last", format="%.2f"),
            "Years": st.column_config.NumberColumn("Years", format="%.1f"),
        },
    )

    ui.section("Calendar-year returns", "S&P 500 index, per year")
    y = dal.yearly(INDEX_SYMBOL).copy()
    if not y.empty:
        y["partial"] = y["is_partial"] == 1
        y["tick"] = y.apply(lambda r: f"{r['year']}*" if r["partial"] else r["year"], axis=1)
        y["label"] = ""
        full = y[~y["partial"]]
        if not full.empty:
            for idx in (full["year_return"].idxmax(), full["year_return"].idxmin()):
                y.loc[idx, "label"] = ui.fmt_pct(y.loc[idx, "year_return"], 1)
        ui.chart(charts.yearly_return_bars(y, pal), key="perf_yearly", config=PLOT_CONFIG, controls=False)
        partials = y.loc[y["partial"], "year"].tolist()
        if partials:
            ui.note(
                f"* {', '.join(partials)} are faded: the dataset covers only part of those "
                "years, so they are period returns, not calendar-year returns."
            )

    ui.section("Monthly seasonality", "S&P 500 index · month by year")
    m = dal.monthly(INDEX_SYMBOL)
    if not m.empty:
        piv = m.pivot(index="year", columns="month", values="month_return")
        piv = piv.reindex(columns=[f"{i:02d}" for i in range(1, 13)])
        ui.chart(charts.seasonality_heatmap(piv, pal, MONTHS), key="perf_season", config=PLOT_CONFIG, controls=False)
        with st.expander("Monthly returns — table view"):
            st.dataframe(piv.style.format("{:+.2%}", na_rep="—"), use_container_width=True)


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------
if not DEV and section == "Risk":
    rsym = st.selectbox(
        "Symbol", list(directory["symbol"]), key="risk_sym",
        format_func=lambda s: f"{s} — {directory.loc[directory['symbol'] == s, 'name'].iloc[0]}",
    )
    r_min, _ = dal.date_bounds(rsym)
    rs = max(start_d, r_min).isoformat()

    dd = dal.drawdowns(rsym, rs, END)
    vol = dal.volatility(rsym, rs, END)
    rws = dal.window_stats(rsym, rs, END)

    if dd.empty or rws is None:
        st.warning("No data in the selected window.")
    else:
        worst = dd.loc[dd["drawdown"].idxmin()]
        cur = dd["drawdown"].iloc[-1]
        ui.kpi_cards([
            {"icon": "📉", "label": "Max drawdown", "value": ui.fmt_pct(rws["max_drawdown"], 1),
             "change": f"{pd.to_datetime(worst['date']):%b %Y}", "change_dir": "down",
             "foot": "Deepest fall from a running high"},
            {"icon": "📍", "label": "Current drawdown", "value": ui.fmt_pct(cur, 1),
             "change_dir": "down" if cur < -0.001 else "flat",
             "change": "below peak" if cur < -0.001 else "at peak",
             "foot": "Distance below the running high"},
            {"icon": "〰", "label": "Volatility (window)",
             "value": ui.fmt_pct(rws["ann_volatility"], 1, signed=False),
             "foot": "Annualized, from daily returns"},
            {"icon": "⚡", "label": "Volatility (21d)",
             "value": ui.fmt_pct(vol["ann_volatility_21d"].iloc[-1], 1, signed=False) if not vol.empty else "—",
             "foot": "Most recent 21-session reading"},
        ])

        ui.section("Drawdown from running high", f"{rsym} · underwater curve")
        fig = charts.area_series(dd, "drawdown", pal, color_key="red",
                                 y_title="Drawdown", height=340, tickformat=".0%")
        fig.add_annotation(
            x=worst["date"], y=worst["drawdown"],
            text=f"{worst['drawdown'] * 100:.1f}% · {pd.to_datetime(worst['date']):%b %Y}",
            showarrow=True, arrowhead=0, arrowcolor=pal["muted"], ax=0, ay=-26,
            font=dict(color=pal["text_primary"], size=11.5),
        )
        charts.add_events(fig, pal, events.in_range(rs, END))
        ui.chart(fig, key="risk_dd", config=PLOT_CONFIG,
                 caption="Event markers show what drove each decline — hover a diamond for detail")

        ui.section("Rolling volatility", "21-session, annualized")
        if not vol.empty:
            ui.chart(
                charts.area_series(vol, "ann_volatility_21d", pal, color_key="blue",
                                   y_title="Ann. volatility", height=300,
                                   label=ui.fmt_pct(vol["ann_volatility_21d"].iloc[-1], 0, signed=False),
                                   zero_line=False),
                key="risk_vol", config=PLOT_CONFIG, controls=False,
            )

        ui.section("Risk vs return", "All symbols over their own full history")
        lb = dal.leaderboard()
        scat = lb.dropna(subset=["ann_volatility", "cagr"])
        if not scat.empty:
            import plotly.graph_objects as go
            f = go.Figure()
            f.add_trace(go.Scatter(
                x=scat["ann_volatility"], y=scat["cagr"], mode="markers+text",
                text=scat["symbol"], textposition="top center",
                textfont=dict(color=pal["text_secondary"], size=9),
                marker=dict(size=10, color=pal["series"][0],
                            line=dict(width=2, color=pal["surface"])),
                customdata=scat[["name"]].values,
                hovertemplate="<b>%{text}</b><br>Vol %{x:.1%} · CAGR %{y:.1%}<extra></extra>",
            ))
            f = charts.style(f, pal, y_title="CAGR", height=440, crosshair=False,
                             y_tickformat=".0%")
            f.update_xaxes(title=dict(text="Annualized volatility",
                                      font=dict(color=pal["muted"], size=11.5)),
                           tickformat=".0%", showgrid=True, gridcolor=pal["gridline"])
            ui.chart(f, key="risk_scatter", config=PLOT_CONFIG, controls=False)
            ui.note(
                "One point per symbol, over each symbol's own listed history — so the "
                "horizon differs between points. Labels are drawn for every point here "
                "because there are only ~49 and they are the identity channel."
            )

# ---------------------------------------------------------------------------
# Portfolio — build a weighted basket and see what it would have done
# ---------------------------------------------------------------------------
if not DEV and section == "Portfolio":
    ui.section("Portfolio simulator", "Build a basket and see how it would have performed")
    ui.note(
        "Pick holdings and set their weights. Every figure is computed from the "
        "actual daily history of those companies over the selected window."
    )

    all_syms = list(directory["symbol"])
    name_of = dict(zip(directory["symbol"], directory["name"]))
    default = [s for s in ("AAPL", "MSFT", "NVDA", "AMZN") if s in all_syms]

    holdings = st.multiselect(
        "Holdings", all_syms, default=st.session_state.get("pf_syms", default),
        key="pf_syms", max_selections=8,
        format_func=lambda s: f"{s} — {name_of.get(s, s)}",
        help="Up to 8 holdings",
    )

    if not holdings:
        ui.note("Add at least one holding to run a simulation.")
    else:
        st.markdown('<div class="note" style="margin-top:6px;">Weights (%)</div>',
                    unsafe_allow_html=True)
        cols = st.columns(min(len(holdings), 4))
        raw_w: dict[str, float] = {}
        for i_h, sym in enumerate(holdings):
            with cols[i_h % len(cols)]:
                raw_w[sym] = st.number_input(
                    sym, min_value=0.0, max_value=100.0,
                    value=float(round(100 / len(holdings), 1)), step=5.0,
                    key=f"pf_w_{sym}",
                )

        total_w = sum(raw_w.values())
        if total_w <= 0:
            st.warning("Total weight must be greater than zero.")
        else:
            # Normalize so the basket always sums to 100%. Showing the raw total
            # keeps that honest rather than silently rescaling behind the reader.
            weights = tuple((s, w / total_w) for s, w in raw_w.items())
            if abs(total_w - 100) > 0.05:
                ui.note(
                    f"Weights total {total_w:.1f}% — normalized to 100% for the "
                    "simulation. Set them to sum to 100 to control it exactly."
                )

            stats = dal.portfolio_stats(weights, START, END)
            pser = dal.portfolio_series(weights, START, END)

            if stats is None or pser.empty:
                st.warning(
                    "Not enough overlapping history for that combination in this "
                    "window. Try a longer range or different holdings."
                )
            else:
                bench = dal.window_stats(INDEX_SYMBOL, START, END)
                bench_ret = bench["period_return"] if bench is not None else None
                diff = (stats["total_return"] - bench_ret) if bench_ret is not None else None

                ui.kpi_cards([
                    {"icon": "\U0001F4B0", "label": "Total return",
                     "value": ui.fmt_pct(stats["total_return"], 1),
                     "change": (f"{diff*100:+.1f} pts vs S&P 500" if diff is not None else None),
                     "change_dir": ("up" if (diff or 0) >= 0 else "down"),
                     "foot": f"{int(stats['sessions']):,} sessions"},
                    {"icon": "\U0001F4C8", "label": "CAGR", "value": ui.fmt_pct(stats["cagr"], 1),
                     "foot": "Annualized growth rate"},
                    {"icon": "\u3030", "label": "Volatility",
                     "value": ui.fmt_pct(stats["ann_volatility"], 1, signed=False),
                     "foot": "Annualized, from daily moves"},
                    {"icon": "\U0001F4C9", "label": "Max drawdown",
                     "value": ui.fmt_pct(stats["max_drawdown"], 1),
                     "change": "peak to trough", "change_dir": "down",
                     "foot": "Deepest fall you'd have sat through"},
                    {"icon": "\U0001F53A", "label": "Best day",
                     "value": ui.fmt_pct(stats["best_day"], 1),
                     "foot": f"Worst {ui.fmt_pct(stats['worst_day'], 1)}"},
                ])

                ui.section("Growth of the portfolio",
                           f"Invested {pd.to_datetime(stats['first_date']):%b %d, %Y}")
                fig_pf = charts.area_series(
                    pser, "cumulative_return", pal, color_key="blue",
                    y_title="Cumulative return", height=340,
                    label=ui.fmt_pct(pser["cumulative_return"].iloc[-1], 0),
                )
                charts.add_events(fig_pf, pal, events.in_range(stats["first_date"], END))
                ui.chart(fig_pf, key="pf_growth", config=PLOT_CONFIG)

                mix = pd.DataFrame({
                    "Ticker": [s for s, _ in weights],
                    "Company": [name_of.get(s, s) for s, _ in weights],
                    "Weight": [w * 100 for _, w in weights],
                })
                st.dataframe(
                    mix, use_container_width=True, hide_index=True,
                    column_config={"Weight": st.column_config.NumberColumn(
                        "Weight", format="%.1f%%")},
                )

                ui.note_md(
                    "**How this is modelled.** The portfolio is rebalanced back to "
                    "target weights every day — the standard simple assumption. A "
                    "real buy-and-hold basket drifts as winners grow, so its result "
                    "would differ. Only sessions where every holding traded are "
                    "counted, and the money goes in on the first such session, so a "
                    "recently-listed holding sets the start date for the whole "
                    "basket. Dividends are excluded, as everywhere else here."
                )



# ---------------------------------------------------------------------------
# About — the USER-facing one: what the numbers mean, never how it is built.
# Architecture, SQL and performance all live in the Developer Center.
# ---------------------------------------------------------------------------
if not DEV and section == "About":
    ui.section("About this data", "What these numbers mean, and what they don't")

    ui.kpi_cards([
        {"icon": "\U0001F4C5", "label": "History", "value": "25 years",
         "foot": "Daily bars since 2001"},
        {"icon": "\U0001F3E2", "label": "Companies", "value": "49",
         "foot": "Large-cap US equities"},
        {"icon": "\U0001F4C8", "label": "Benchmark", "value": "S&P 500", "small": True,
         "foot": "Index used throughout"},
        {"icon": "\U0001F553", "label": "Updated", "value": last_date.strftime("%b %d, %Y"),
         "small": True, "foot": "Latest close in the dataset"},
    ])

    st.markdown(
        """
#### How to read the metrics

| Metric | What it tells you |
|---|---|
| **Return** | Simple price change between the first and last day of your window. |
| **CAGR** | The annual growth rate that compounds to that return. It makes windows of different lengths comparable. |
| **Volatility** | How much daily prices swing, annualized. Higher means a rougher ride, not necessarily a worse outcome. |
| **Max drawdown** | The deepest fall from a previous high. This is the loss you would have had to sit through. |
| **52-week range** | The highest and lowest price of the past year — context for where the price sits today. |
| **Moving average** | The average price over the last 50 or 200 sessions, used to read trend rather than noise. |

**Why return alone isn't enough.** Two companies can post the same CAGR while
one delivered it smoothly and the other through a 90% collapse and recovery.
Volatility and drawdown describe that difference, which is why they sit beside
every return figure here.

#### Important limitations

These genuinely affect how the numbers should be read:

- **Dividends are excluded.** All figures are price returns, so long-run results
  understate what a shareholder actually earned — meaningfully so for
  high-dividend companies.
- **Only companies that exist today are included.** Firms that failed or were
  acquired are absent, which flatters long-run averages. This is called
  survivorship bias and it affects every ranking on the site.
- **Company histories differ in length.** Meta lists in 2012, Tesla in 2010.
  All-time rankings show each company's start date and number of years so
  unequal periods are visible rather than hidden.
- **Sector figures are equal-weighted** and reported as the median member, so one
  very large company cannot stand in for its whole sector.
- **Market status follows the regular schedule** (weekdays, 09:30-16:00 ET).
  Exchange holidays are not modelled.
- **Partial years are marked.** The first and last calendar years of the window
  are incomplete, so their bars are faded and asterisked.

#### Not investment advice

This is an analytical tool built to explore historical market data. Nothing here
is a recommendation to buy or sell any security. Past performance does not
predict future results.
"""
    )

    ui.section("Data source")
    ui.note(
        "Daily open, high, low, close and volume from Yahoo Finance. Sector and "
        "industry classifications are hand-maintained. Curious how it is built? "
        "Switch to Developer Center in the top-right."
    )

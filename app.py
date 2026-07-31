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
import nlq
import queries
import router
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
    "1Y": 365, "3Y": 1095, "5Y": 1826, "10Y": 3653,
    "All Time": "all", "Custom Range": "custom",
}
DEFAULT_PRESET = "All Time"
CUSTOM_PRESET = "Custom Range"

# The sections that actually read the window. Performance is all-time by
# construction (each symbol over its own listed history, calendar-year returns,
# seasonality) and About states no figures at all -- so the control row must not
# claim to scope them. The window is deliberately shared by the rest: moving
# between Market and Companies keeps every figure describing the same period,
# which is what makes reading across sections trustworthy.
WINDOWED_SECTIONS = ("Overview", "Market", "Companies", "Risk", "Portfolio")

# Rolling-return horizons, as SESSION counts (~252 trading days a year). A
# holding-period length, not a date filter: it selects how long each period is,
# while the record swept stays the symbol's full history.
ROLLING_HORIZONS: dict[str, int] = {"1Y": 252, "3Y": 756, "5Y": 1260, "10Y": 2520}

# Opening set for the correlation matrix: four names from four different sectors,
# so the first render shows a range of coefficients rather than one cluster near
# 1.0 that makes the chart look broken.
CORR_DEFAULT = ("AAPL", "JPM", "XOM", "JNJ")


def resolve_range(preset: str, min_d: dt.date, max_d: dt.date) -> tuple[dt.date, dt.date]:
    """Preset label (or bare sentinel) -> window, clamped to the available data.

    Relative windows run back from the newest session in the data rather than
    from today, so "1Y" is the last year the market traded -- the two differ
    every weekend and after every holiday.
    """
    spec = PRESETS.get(preset, preset)
    # "max" is the old label for All Time. A browser session that survived a
    # Streamlit Cloud hot-update can still be holding it (see the deploy hazard
    # in PROJECT_STATE), so it stays an accepted alias rather than falling
    # through to an empty window.
    if spec in ("all", "max", "custom"):
        return min_d, max_d
    if spec == "ytd":
        return max(dt.date(max_d.year, 1, 1), min_d), max_d
    try:
        days = int(spec)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return min_d, max_d
    return max(max_d - dt.timedelta(days=days), min_d), max_d


def custom_window(min_d: dt.date, max_d: dt.date) -> tuple[dt.date, dt.date]:
    """Start/end pickers for Custom Range, validated before anything queries it.

    Both fields are bounded by the data, so the failure a reader can actually
    produce is a reversed pair -- and SQLite answers `BETWEEN '2020' AND '2010'`
    with zero rows, which would surface as "No data in the selected window" on
    every section at once: an input error wearing the costume of a data gap. It
    is caught here instead, and the last valid window is held.
    """
    last_ok = st.session_state.get("custom_window", (min_d, max_d))
    cols = st.columns([1.3, 1.3, 3.4])
    with cols[0]:
        picked_s = st.date_input(
            "Start date", value=last_ok[0], min_value=min_d, max_value=max_d,
            key="custom_start", format="YYYY-MM-DD",
        )
    with cols[1]:
        picked_e = st.date_input(
            "End date", value=last_ok[1], min_value=min_d, max_value=max_d,
            key="custom_end", format="YYYY-MM-DD",
        )

    # A cleared field yields None; hold the last good window rather than
    # comparing None against a date.
    if picked_s is None or picked_e is None:
        return last_ok
    if picked_s > picked_e:
        st.error(
            f"Start date ({picked_s:%b %d, %Y}) is after the end date "
            f"({picked_e:%b %d, %Y}). Showing the previous range."
        )
        return last_ok

    st.session_state["custom_window"] = (picked_s, picked_e)
    return picked_s, picked_e


def use_example(text: str) -> None:
    """Load a suggestion chip into the Ask box.

    Streamlit refuses writes to a widget's key once that widget has been
    instantiated this run, so this has to run as the button's on_click --
    callbacks fire before the rerun, while the text_input does not yet exist.
    """
    st.session_state.ask_q = text


# ---------------------------------------------------------------------------
# Theme + chrome
# ---------------------------------------------------------------------------
if "theme" not in st.session_state:
    st.session_state.theme = "Dark"

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
    # Drop a stored preset that is no longer offered before the widget is built.
    # st.radio raises on a session_state value outside its options, and a session
    # that survived a hot-update can still hold the old "MAX" label. Writing a
    # widget key is only forbidden AFTER the widget exists -- here it does not yet.
    if "preset" in st.session_state and st.session_state["preset"] not in PRESETS:
        del st.session_state["preset"]
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
if preset == CUSTOM_PRESET:
    start_d, end_d = custom_window(index_min, index_max)
else:
    start_d, end_d = resolve_range(preset, index_min, index_max)
START, END = start_d.isoformat(), end_d.isoformat()

# Name the sections this window reaches. It previously read "scopes every section
# below", which was false for two of the seven: Performance is all-time and About
# has no figures to scope. A control that overstates its reach makes a reader
# distrust the ones it does govern.
_named = f"{', '.join(WINDOWED_SECTIONS[:-1])} and {WINDOWED_SECTIONS[-1]}"
scope_note = (
    "seeds the SQL Explorer window" if DEV
    else f"applies to {_named} · Performance and About are always all-time"
)
st.markdown(
    f'<div class="note">'
    f'<span class="modepill{" dev" if DEV else ""}">'
    f'{"◆ Developer Center" if DEV else "● User Mode"}</span>'
    f'&nbsp;&nbsp;Window <b>{start_d:%b %d, %Y}</b> → <b>{end_d:%b %d, %Y}</b>'
    f' · {preset} · {scope_note}</div>',
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
    # Ask the Market -- every question goes through the Query Router, which
    # prefers an existing SQL template and only generates SQL when none fits. A
    # template answer is therefore the same query the rest of the app runs, so an
    # answer can never disagree with the page beside it.
    asked = st.text_input(
        "Ask the Market", key="ask_q", label_visibility="collapsed",
        placeholder="Ask the Market…  e.g. “What stock had the highest trading volume?”",
    )
    ex_cols = st.columns(len(ask.EXAMPLES))
    for i_e, ex in enumerate(ask.EXAMPLES):
        with ex_cols[i_e]:
            st.button(
                ex, key=f"ask_ex_{i_e}", width="stretch",
                on_click=use_example, args=(ex,),
            )

    if asked and asked.strip():
        # The current window goes in as the second precedence tier: a question
        # that names its own window overrides it, one that doesn't inherits it.
        with st.spinner("Reading the question…"):
            routed = router.route(
                asked, directory,
                ui_start=START, ui_end=END, ui_preset=preset,
                data_min=index_min, data_max=end_d,
            )

        if routed.path == router.TEMPLATE_PATH:
            answers.HANDLERS[routed.template.handler](directory, pal, routed.params)

        elif routed.path == router.GENERATED_PATH:
            # No template covered this one, so the answer came from generated SQL.
            # The insight is written from the returned rows, never from the model's
            # own recollection -- it can only describe what the query actually found.
            with st.spinner("Reading the result…"):
                said = nlq.insight(asked, routed.df.head(30).to_csv(index=False))
            answers.generated(asked, routed.df, pal, insight_text=said or "")

        else:
            ui.empty_state(routed.error,
                           "Try naming a company, a sector or a time period — "
                           "or pick one of the suggestions above.", kind="info")
        st.divider()

    q = dal.quote(INDEX_SYMBOL)
    ws = dal.window_stats(INDEX_SYMBOL, START, END)
    px = dal.prices(INDEX_SYMBOL, START, END)

    if ws is None or px.empty or q is None:
        ui.empty_state("No index data in the selected window.",
                       "Widen the date range above — the shortest presets can fall "
                       "entirely inside a market closure.", kind="warn")
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
        ui.empty_state("No symbols traded in this window.",
                       "Widen the date range above to cover at least one session.",
                       kind="warn")
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
        filter_cols=("Sector",),
            sort_options={"Return": "Return", "Avg $ volume": "Avg $ vol",
                          "Ticker": "Ticker", "Company": "Company"},
            default_sort="Return",
            csv_name=f"movers_{preset.lower().replace(' ', '_')}.csv",
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
        ui.empty_state(f"{sym} has no sessions in this window.",
                       "This company may have listed after the window ends — try "
                       "All Time to see its full history.", kind="warn")
    else:
        listed_note = ""
        if s_min > start_d:
            listed_note = f" · history starts {s_min:%b %Y}, after the selected window opens"
        ui.quote_strip(
            str(meta["name"]), sym, cq,
            f"{meta['sector']} · {meta['industry']}{listed_note}", pal,
        )

        # Return, CAGR, volatility and max drawdown lead: they are what a company
        # is actually researched on, and the pair of them (a return and the ride
        # it took) is the comparison the About page argues for. Max drawdown was
        # previously a sub-line on the volatility card, which read as a footnote
        # to volatility rather than as the peer metric it is.
        ui.kpi_cards([
            {"icon": "📅", "label": f"Return · {preset}", "value": ui.fmt_pct(cws["period_return"], 1),
             "change": f"{cws['trading_days']:,} sessions", "change_dir": "flat",
             "foot": f"{pd.to_datetime(cws['first_date']):%b %Y} → {pd.to_datetime(cws['last_date']):%b %Y}"},
            {"icon": "📈", "label": "CAGR", "value": ui.fmt_pct(cws["cagr"], 1),
             "foot": "Annualized over the window"},
            {"icon": "〰", "label": "Volatility", "value": ui.fmt_pct(cws["ann_volatility"], 1, signed=False),
             "foot": "Annualized, from daily returns"},
            {"icon": "📉", "label": "Max drawdown", "value": ui.fmt_pct(cws["max_drawdown"], 1),
             "change": "peak to trough", "change_dir": "down",
             "foot": "Deepest fall from a running high"},
            {"icon": "🔁", "label": "52-week high", "value": ui.fmt_price(cq["w52_high"]),
             "foot": f"Low {ui.fmt_price(cq['w52_low'])} · trailing 1y"},
            {"icon": "🔊", "label": "Avg volume", "value": ui.fmt_compact(cws["avg_volume"]),
             "foot": f"{ui.fmt_dollar_compact(cws['avg_dollar_volume'])} avg turnover"},
        ])
        ui.note(
            "Market cap is intentionally absent: it needs share-count data that no "
            "reachable free endpoint provides, and a stale hardcoded figure would be "
            "worse than none. Average dollar turnover is shown instead — it is computed "
            "from data actually in the database."
        )

        ui.section("Price history", f"{sym} · {preset}")
        # The window high/low used to be its own KPI card. It belongs beside the
        # chart that shows it, and moving it there kept the metric row to the four
        # figures a company is researched on plus two of context.
        hi_lo = (f"Window high {ui.fmt_price(cws['highest_close'])} · "
                 f"low {ui.fmt_price(cws['lowest_close'])}")
        if view_mode == "Candles":
            ui.chart(charts.candlestick(cpx, pal), key="co_candle", config=PLOT_CONFIG,
                     caption=hi_lo)
        else:
            fig_co = charts.price_line(cpx, pal, label=ui.fmt_price(cpx["close"].iloc[-1]))
            charts.add_events(fig_co, pal, events.in_range(cs, ce))
            ui.chart(fig_co, key="co_price", config=PLOT_CONFIG, caption=hi_lo)

        # Six charts used to render in sequence here, which buried the comparison
        # below a scroll of secondary analysis. They are now one radio deep, for
        # the same reason st.tabs is refused for section switching: only the
        # selected view renders, so a visit runs one query instead of six and no
        # Plotly figure is measured inside a hidden container. Nothing was
        # removed -- every chart is still reachable, "Hide" is just the default.
        detail = st.radio(
            "More analysis",
            ["Hide", "Moving averages", "Volume & daily returns",
             "Growth & volatility", "Drawdown"],
            horizontal=True, key="co_detail",
            help="Deeper charts for this company, one at a time",
        )

        if detail == "Moving averages":
            ui.section("Moving averages", "Close with trailing 50- and 200-session means")
            ma = dal.moving_averages(sym, cs, ce)
            ui.chart(charts.moving_average_chart(ma, pal), key="co_ma", config=PLOT_CONFIG)
            ui.note(
                "A moving-average line only begins once its full window exists, so the "
                "200-session average is absent for the first 200 sessions rather than "
                "being averaged from fewer points than its label claims."
            )

        elif detail == "Volume & daily returns":
            left, right = st.columns(2)
            with left:
                ui.section("Trading volume")
                ui.chart(charts.volume_bars(cpx, pal), key="co_vol", config=PLOT_CONFIG, controls=False)
            with right:
                ui.section("Daily returns")
                dr = dal.daily_returns(sym, cs, ce)
                ui.chart(charts.returns_bars(dr, pal), key="co_ret", config=PLOT_CONFIG, controls=False)

        elif detail == "Growth & volatility":
            l2, r2 = st.columns(2)
            with l2:
                ui.section("Cumulative return", "Compounded from daily returns")
                cr = dal.cumulative_return(sym, cs, ce)
                if not cr.empty:
                    ui.chart(
                        charts.area_series(cr, "cumulative_return", pal, color_key="blue",
                                           y_title="Cumulative", height=charts.H_COMPACT,
                                           label=ui.fmt_pct(cr["cumulative_return"].iloc[-1], 0)),
                        key="co_cum", config=PLOT_CONFIG, controls=False,
                    )
            with r2:
                ui.section("Rolling volatility", "21-session, annualized")
                cv = dal.volatility(sym, cs, ce)
                if not cv.empty:
                    ui.chart(
                        charts.area_series(cv, "ann_volatility_21d", pal, color_key="red",
                                           y_title="Ann. volatility", height=charts.H_COMPACT,
                                           label=ui.fmt_pct(cv["ann_volatility_21d"].iloc[-1], 0, signed=False),
                                           zero_line=False),
                        key="co_vola", config=PLOT_CONFIG, controls=False,
                    )

        elif detail == "Drawdown":
            ui.section("Drawdown", "How far below its previous high the price sat")
            cdd = dal.drawdowns(sym, cs, ce)
            if not cdd.empty:
                fig_dd = charts.area_series(cdd, "drawdown", pal, color_key="red",
                                            y_title="Drawdown", height=charts.H_COMPACT, tickformat=".0%")
                worst_c = cdd.loc[cdd["drawdown"].idxmin()]
                fig_dd.add_annotation(
                    x=worst_c["date"], y=worst_c["drawdown"],
                    text=f"{worst_c['drawdown'] * 100:.1f}% · {pd.to_datetime(worst_c['date']):%b %Y}",
                    showarrow=True, arrowhead=0, arrowcolor=pal["muted"], ax=0, ay=-26,
                    font=dict(color=pal["text_primary"], size=11.5),
                )
                charts.add_events(fig_dd, pal, events.in_range(cs, ce), label=False)
                ui.chart(fig_dd, key="co_dd", config=PLOT_CONFIG, controls=False,
                         caption="Measured from the running all-time high — the loss an investor sat through")

        # ---- peer comparison ----
        ui.section("Peer comparison", "Rebased to 100 at the window start — one shared axis")

        # Open on a real peer set: the same sector, ranked by the turnover already
        # computed for the Market table (period_movers is cached, so this is a
        # cache hit rather than a new query). The old default paired every company
        # with MSFT, which is a peer for a handful of them and noise for the rest.
        known = set(directory["symbol"])
        pm = dal.period_movers(START, END)
        same_sector = pm[(pm["sector"] == meta["sector"]) & (pm["symbol"] != sym)]
        top_peers = list(
            same_sector.sort_values("avg_dollar_volume", ascending=False)["symbol"].head(2)
        )
        peer_default = [sym] + (top_peers or [s for s in (INDEX_SYMBOL,) if s in known])

        # Follow the selected company. A multiselect with a key keeps its value
        # forever, so without this, switching from Apple to Exxon would leave the
        # comparison showing Apple's peers -- stale in a way that reads as a bug.
        # Writing a widget key is only forbidden AFTER the widget exists.
        if st.session_state.get("co_last_sym") != sym:
            st.session_state["co_last_sym"] = sym
            st.session_state["cmp_syms"] = peer_default
        # The limit used to be 8; a selection stored from then would exceed
        # max_selections and raise on a session that survived a hot-update.
        stored = st.session_state.get("cmp_syms")
        if stored is not None and len(stored) > 3:
            st.session_state["cmp_syms"] = list(stored)[:3]

        cmp_syms = st.multiselect(
            "Compare with", options=list(directory["symbol"]),
            default=peer_default, key="cmp_syms", max_selections=3,
            help="Up to three. Defaults to the biggest companies in the same sector; "
                 "add ^GSPC to compare against the index.",
        )
        cmp_metric = st.radio(
            "Compare on", ["Price (rebased)", "Cumulative return", "Drawdown",
                           "Rolling volatility", "Daily returns", "Volume"],
            horizontal=True, key="cmp_metric", label_visibility="collapsed",
        )

        if len(cmp_syms) < 2:
            ui.note("Pick at least two symbols to compare.")
        elif cmp_metric != "Price (rebased)":
            # Every other metric is already unit-comparable across companies
            # (percentages, or share counts), so it overlays directly -- still on
            # one shared axis, never a second y-scale.
            spec = {
                "Cumulative return": ("cumulative_return", "Cumulative return", ".0%", ".1%", True),
                "Drawdown":          ("drawdown", "Drawdown", ".0%", ".1%", True),
                "Rolling volatility": ("ann_volatility_21d", "Ann. volatility", ".0%", ".1%", False),
                "Daily returns":     ("daily_return", "Daily return", ".1%", ".2%", True),
                "Volume":            ("volume", "Volume", None, ",.0f", False),
            }[cmp_metric]
            col, title, tickfmt, hoverfmt, zline = spec
            frames = dal.comparison_frames(tuple(cmp_syms), START, END, cmp_metric)
            if not frames:
                ui.note("No data for that selection in this window.")
            else:
                fig_m = charts.multi_series(
                    frames, pal, value_col=col, y_title=title, height=charts.H_PRIMARY,
                    tickformat=tickfmt, zero_line=zline, hover_fmt=hoverfmt,
                )
                charts.add_events(fig_m, pal, events.in_range(START, END), label=False)
                ui.chart(fig_m, key=f"co_cmp_{col}", config=PLOT_CONFIG,
                         caption=f"{cmp_metric} for {len(frames)} symbols on one shared axis")
                ui.note(
                    "These metrics are already comparable across companies, so they "
                    "overlay directly. A second y-axis is never used: two independent "
                    "scales can be aligned to imply any relationship you like."
                )
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
                ui.table_view(
                    "Comparison — table view", rows,
                    column_config={
                        "Window return": st.column_config.NumberColumn(
                            "Window return", format="%+.2f%%"),
                        "Indexed (start=100)": st.column_config.NumberColumn(
                            "Indexed (start=100)", format="%.1f",
                            help="Every series starts at 100 on the window's first session"),
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
        filter_cols=("Sector",),
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

    # Rolling returns. Stays all-time like the rest of this section: the point is
    # every holding period in the record, so narrowing to a window would discard
    # the outcomes that make the spread worth showing.
    ui.section("Rolling returns", "S&P 500 index · every holding period of a fixed length")
    horizon = st.radio(
        "Holding period", list(ROLLING_HORIZONS), key="perf_roll",
        horizontal=True, label_visibility="collapsed",
    )
    roll_sessions = ROLLING_HORIZONS[horizon]
    roll = dal.rolling_returns(INDEX_SYMBOL, roll_sessions)
    rsum = dal.rolling_return_summary(INDEX_SYMBOL, roll_sessions)

    if roll.empty or rsum is None:
        ui.empty_state(f"The record is shorter than {horizon}.",
                       "No complete holding period of that length exists yet — pick a "
                       "shorter one.")
    else:
        ui.kpi_cards([
            {"icon": "🟢", "label": "Ended positive",
             "value": ui.fmt_pct(rsum["share_positive"], 0, signed=False),
             "foot": f"of {int(rsum['periods']):,} {horizon} periods"},
            {"icon": "🥇", "label": "Best", "value": ui.fmt_pct(rsum["best"], 1),
             "change": f"to {pd.to_datetime(rsum['best_end_date']):%b %Y}", "change_dir": "up",
             "foot": "Annualized, best end date"},
            {"icon": "🥀", "label": "Worst", "value": ui.fmt_pct(rsum["worst"], 1),
             "change": f"to {pd.to_datetime(rsum['worst_end_date']):%b %Y}", "change_dir": "down",
             "foot": "Annualized, worst end date"},
            {"icon": "🎯", "label": "Median", "value": ui.fmt_pct(rsum["median_return"], 1),
             "foot": f"Typical {horizon} outcome"},
        ])
        fig_roll = charts.area_series(
            roll, "annualized_return", pal, color_key="blue",
            y_title=f"{horizon} annualized return", height=charts.H_PRIMARY, tickformat=".0%",
        )
        charts.add_events(fig_roll, pal, events.in_range(
            roll["date"].iloc[0].date().isoformat(), END))
        ui.chart(fig_roll, key="perf_rolling", config=PLOT_CONFIG,
                 caption="Each point is the annualized return of the period ENDING that day")
        ui.note(
            f"Every {horizon} stretch in the record, not one window — the point plotted "
            f"on a date is what an investor earned per year over the {horizon} ending "
            "there. The spread between the best and worst bars is the real risk of the "
            "horizon, which a single cumulative line hides completely. Returns are "
            "annualized so the four horizons stay comparable, and overlapping periods "
            "share most of their history, so neighbouring points are not independent."
        )

    ui.section("Monthly seasonality", "S&P 500 index · month by year")
    m = dal.monthly(INDEX_SYMBOL)
    if not m.empty:
        piv = m.pivot(index="year", columns="month", values="month_return")
        piv = piv.reindex(columns=[f"{i:02d}" for i in range(1, 13)])
        ui.chart(charts.seasonality_heatmap(piv, pal, MONTHS), key="perf_season", config=PLOT_CONFIG, controls=False)
        ui.table_view("Monthly returns — table view",
                      piv.style.format("{:+.2%}", na_rep="—"), hide_index=False)


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
        ui.empty_state(f"{rsym} has no sessions in this window.",
                       "Widen the date range above, or pick a company with a longer "
                       "listed history.", kind="warn")
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
                                 y_title="Drawdown", height=charts.H_PRIMARY, tickformat=".0%")
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
                                   y_title="Ann. volatility", height=charts.H_COMPACT,
                                   label=ui.fmt_pct(vol["ann_volatility_21d"].iloc[-1], 0, signed=False),
                                   zero_line=False),
                key="risk_vol", config=PLOT_CONFIG, controls=False,
            )

        ui.section("How these move together", f"Daily-return correlation · {preset}")
        corr_syms = st.multiselect(
            "Companies", list(directory["symbol"]),
            default=[s for s in CORR_DEFAULT if s in set(directory["symbol"])],
            key="risk_corr_syms", max_selections=8,
            format_func=lambda s: f"{s} — {directory.loc[directory['symbol'] == s, 'name'].iloc[0]}",
            help="Two to eight companies",
        )

        if len(corr_syms) < 2:
            ui.empty_state("Pick at least two companies.",
                           "Correlation describes a pair, so it needs two names "
                           "to compare.")
        else:
            cm = dal.correlation_matrix(tuple(corr_syms), START, END)
            cp = dal.correlation_pairs(tuple(corr_syms), START, END)
            if cm.empty or cp.empty:
                ui.empty_state("Those companies share no trading sessions in this window.",
                               "Correlation needs days where every name traded — widen "
                               "the window or drop the most recently listed company.",
                               kind="warn")
            else:
                usable = cp.dropna(subset=["correlation"])
                if not usable.empty:
                    hi = usable.loc[usable["correlation"].idxmax()]
                    lo = usable.loc[usable["correlation"].idxmin()]
                    ui.kpi_cards([
                        {"icon": "🔗", "label": "Most correlated",
                         "value": f"{hi['sym_a']} · {hi['sym_b']}", "small": True,
                         "change": f"{hi['correlation']:.2f}", "change_dir": "flat",
                         "foot": "Moves most closely together"},
                        {"icon": "🪢", "label": "Least correlated",
                         "value": f"{lo['sym_a']} · {lo['sym_b']}", "small": True,
                         "change": f"{lo['correlation']:.2f}", "change_dir": "flat",
                         "foot": "The most diversifying pair here"},
                        {"icon": "📐", "label": "Average pair",
                         "value": f"{usable['correlation'].mean():.2f}",
                         "foot": f"Across {len(usable)} pairs"},
                        {"icon": "📆", "label": "Shared sessions",
                         "value": f"{int(usable['n'].min()):,}",
                         "foot": "Fewest for any pair in the window"},
                    ])

                ui.chart(charts.correlation_heatmap(cm, pal), key="risk_corr",
                         config=PLOT_CONFIG, controls=False)
                ui.note(
                    "Correlation of DAILY RETURNS, not of prices: two rising price "
                    "series correlate near 1.0 whatever they actually did, because "
                    "both trend. 1.00 means the two moved in lockstep, 0.00 that they "
                    "moved independently, negative that one tended to rise when the "
                    "other fell — so the lower the number, the more a pair diversifies "
                    "each other. Each pair uses only the sessions where both traded, "
                    "so a company listed mid-window is measured over its own shorter "
                    "overlap rather than dragging the rest down."
                )
                pairs_tbl = usable.assign(
                    Pair=usable["sym_a"] + " · " + usable["sym_b"],
                    Correlation=usable["correlation"].round(3),
                    Sessions=usable["n"].astype(int),
                )[["Pair", "Correlation", "Sessions"]].sort_values(
                    "Correlation", ascending=False)
                ui.table_view(
                    "Correlation pairs — table view", pairs_tbl,
                    column_config={
                        "Correlation": st.column_config.NumberColumn(
                            "Correlation", format="%.3f",
                            help="1.00 moves in lockstep · 0.00 independent · "
                                 "negative moves opposite"),
                        "Sessions": st.column_config.NumberColumn(
                            "Sessions", format="%d",
                            help="Days both companies traded inside the window"),
                    },
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
            f = charts.style(f, pal, y_title="CAGR", height=charts.H_TALL, crosshair=False,
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
        ui.empty_state("No holdings selected.",
                       "Pick up to eight companies above to build a basket.")
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
            ui.empty_state("Every holding is weighted zero.",
                           "Give at least one holding a weight above 0% to run the "
                           "simulation.", kind="warn")
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
                ui.empty_state(
                    "Not enough overlapping history for that combination.",
                    "The basket needs sessions where every holding traded. Widen the "
                    "window, or drop the most recently listed holding.", kind="warn")
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
                    y_title="Cumulative return", height=charts.H_PRIMARY,
                    label=ui.fmt_pct(pser["cumulative_return"].iloc[-1], 0),
                )
                charts.add_events(fig_pf, pal, events.in_range(stats["first_date"], END))
                ui.chart(fig_pf, key="pf_growth", config=PLOT_CONFIG)

                # Benchmark on ONE shared axis (never a second y-scale). Both series
                # are percentages from the same starting session, so they are directly
                # comparable: the index window starts at the portfolio's first RETURN
                # date, not its investable date, so neither series is a session ahead.
                ui.section("Portfolio vs. the S&P 500", f"Both from {pd.to_datetime(pser['date'].iloc[0]):%b %d, %Y}")
                bench_start = pser["date"].iloc[0].date().isoformat()
                bench_ser = dal.cumulative_return(INDEX_SYMBOL, bench_start, END)
                if bench_ser.empty:
                    ui.empty_state("No index history overlaps this portfolio's window.",
                                   "The benchmark comparison needs index sessions "
                                   "inside the basket's own date range.", kind="warn")
                else:
                    ui.chart(
                        charts.multi_series(
                            {"Portfolio": pser.rename(columns={"cumulative_return": "v"}),
                             "S&P 500": bench_ser.rename(columns={"cumulative_return": "v"})},
                            pal, value_col="v", y_title="Cumulative return",
                            height=charts.H_PRIMARY, tickformat=".0%", zero_line=True, hover_fmt=".1%",
                        ),
                        key="pf_vs_bench", config=PLOT_CONFIG, controls=False,
                    )
                    lead = stats["total_return"] - bench_ser["cumulative_return"].iloc[-1]
                    ui.note(
                        f"The basket finished {abs(lead) * 100:.1f} points "
                        f"{'ahead of' if lead >= 0 else 'behind'} the index over this "
                        "period. Both lines start at 0% on the same session and share "
                        "one axis — the comparison is only honest if they do. The index "
                        "is price-only here, as the portfolio is: neither includes "
                        "dividends, so both are understated by a similar order."
                    )

                ui.section("Allocation", "What the basket is made of")
                alloc_col, contrib_col = st.columns([1, 1.35])
                mix = pd.DataFrame({
                    "symbol": [s for s, _ in weights],
                    "name": [name_of.get(s, s) for s, _ in weights],
                    "weight": [w for _, w in weights],
                })
                with alloc_col:
                    ui.chart(charts.allocation_donut(mix, pal), key="pf_alloc",
                             config=PLOT_CONFIG, controls=False)

                contrib = dal.portfolio_contribution(weights, START, END)
                with contrib_col:
                    if contrib.empty:
                        ui.empty_state("No contribution data for this basket.",
                                       "This needs at least one full session after the "
                                       "investment date.", kind="warn")
                    else:
                        ui.chart(charts.contribution_bars(contrib, pal), key="pf_contrib",
                                 config=PLOT_CONFIG, controls=False)

                if not contrib.empty:
                    ui.note(
                        "Left: how the money is split. Right: how much of the "
                        "portfolio's total return each holding actually produced, in "
                        "percentage points. These are NOT weight x return — once "
                        "returns compound, the parts of that product do not sum to the "
                        "whole. Each bar is the holding's weighted daily return scaled "
                        "by the portfolio's value going into that session, which does "
                        "sum to the total exactly. A big weight on a mediocre performer "
                        "and a small weight on a great one can produce the same bar; "
                        "hover to see which."
                    )
                    contrib_tbl = contrib.assign(
                        Ticker=contrib["symbol"], Company=contrib["name"],
                        Weight=contrib["weight"] * 100,
                        Return=contrib["holding_return"] * 100,
                        Contribution=contrib["contribution"] * 100,
                    )[["Ticker", "Company", "Weight", "Return", "Contribution"]]
                    ui.table_view(
                        "Contribution by holding — table view", contrib_tbl,
                        column_config={
                            "Weight": st.column_config.NumberColumn("Weight", format="%.1f%%"),
                            "Return": st.column_config.NumberColumn(
                                "Holding return", format="%+.1f%%",
                                help="The holding's own compounded return over the "
                                     "sessions the portfolio was invested"),
                            "Contribution": st.column_config.NumberColumn(
                                "Contribution", format="%+.2f%%",
                                help="Points of the portfolio's total return; these sum "
                                     "to the total"),
                        },
                        footer=(f"Sums to {contrib['contribution'].sum() * 100:+.2f}% · "
                                f"portfolio total {stats['total_return'] * 100:+.2f}%"),
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

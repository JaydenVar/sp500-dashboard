"""Market Analytics — a SQL-first equity dashboard.

Every figure on screen is the output of a SQL query (see queries.py) against a
local SQLite database whose analysis lives in views and materialized rollups
(see build_db.py). Python queries and draws; it does no analysis of its own.
The SQL Explorer tab shows each query, its explanation, and its measured runtime.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

import charts
import components as ui
import data_access as dal
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
ctl = st.columns([5.2, 1.5, 1.15])
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
    if st.button("↺ Reset view", use_container_width=True,
                 help="Restore the default date range and clear chart zoom/pan"):
        for k in ("preset", "cmp_syms", "co_symbol"):
            st.session_state.pop(k, None)
        st.rerun()

index_min, index_max = dal.date_bounds(INDEX_SYMBOL)
start_d, end_d = resolve_range(preset, index_min, index_max)
START, END = start_d.isoformat(), end_d.isoformat()

st.markdown(
    f'<div class="note">Window <b>{start_d:%b %d, %Y}</b> → <b>{end_d:%b %d, %Y}</b>'
    f' · {preset} · filters scope every tab below</div>',
    unsafe_allow_html=True,
)

SECTIONS = ["Overview", "Market", "Companies", "Performance", "Risk", "SQL Explorer", "About"]

# Deliberately a radio, not st.tabs. st.tabs renders EVERY tab's body on every
# rerun, which (a) runs all seven sections' queries when only one is visible and
# (b) makes Plotly measure charts inside hidden tabs, so they render at a
# fraction of the container width. Rendering only the active section fixes both.
section = st.radio(
    "Section", SECTIONS, horizontal=True, key="section", label_visibility="collapsed",
)


# ---------------------------------------------------------------------------
# Overview — the index
# ---------------------------------------------------------------------------
if section == "Overview":
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
        c1, c2 = st.columns([1, 1])
        with c1:
            scale = st.radio("Scale", ["Linear", "Log"], horizontal=True,
                             key="ov_scale", label_visibility="collapsed")
        fig = charts.price_line(px, pal, log=(scale == "Log"),
                                label=ui.fmt_price(px["close"].iloc[-1], 0))
        st.plotly_chart(fig, use_container_width=True, config=PLOT_CONFIG)

        ui.section("Trading volume", "Daily share volume")
        st.plotly_chart(charts.volume_bars(px, pal), use_container_width=True, config=PLOT_CONFIG)

        ui.note(
            "Drag to pan, drag-select to zoom, and use the toolbar (top-right on hover) to "
            "zoom, autoscale, reset axes, or download the chart as a PNG."
        )


# ---------------------------------------------------------------------------
# Market — breadth, sectors, movers
# ---------------------------------------------------------------------------
if section == "Market":
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
        st.plotly_chart(charts.sector_bars(sectors, pal), use_container_width=True, config=PLOT_CONFIG)
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
if section == "Companies":
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
            st.plotly_chart(charts.candlestick(cpx, pal), use_container_width=True, config=PLOT_CONFIG)
        else:
            st.plotly_chart(
                charts.price_line(cpx, pal, label=ui.fmt_price(cpx["close"].iloc[-1])),
                use_container_width=True, config=PLOT_CONFIG,
            )

        ui.section("Moving averages", "Close with trailing 50- and 200-session means")
        ma = dal.moving_averages(sym, cs, ce)
        st.plotly_chart(charts.moving_average_chart(ma, pal), use_container_width=True, config=PLOT_CONFIG)
        ui.note(
            "A moving-average line only begins once its full window exists, so the "
            "200-session average is absent for the first 200 sessions rather than "
            "being averaged from fewer points than its label claims."
        )

        left, right = st.columns(2)
        with left:
            ui.section("Trading volume")
            st.plotly_chart(charts.volume_bars(cpx, pal, height=240),
                            use_container_width=True, config=PLOT_CONFIG)
        with right:
            ui.section("Daily returns")
            dr = dal.daily_returns(sym, cs, ce)
            st.plotly_chart(charts.returns_bars(dr, pal, height=240),
                            use_container_width=True, config=PLOT_CONFIG)

        l2, r2 = st.columns(2)
        with l2:
            ui.section("Cumulative return", "Compounded from daily returns")
            cr = dal.cumulative_return(sym, cs, ce)
            if not cr.empty:
                st.plotly_chart(
                    charts.area_series(cr, "cumulative_return", pal, color_key="blue",
                                       y_title="Cumulative", height=280,
                                       label=ui.fmt_pct(cr["cumulative_return"].iloc[-1], 0)),
                    use_container_width=True, config=PLOT_CONFIG,
                )
        with r2:
            ui.section("Rolling volatility", "21-session, annualized")
            cv = dal.volatility(sym, cs, ce)
            if not cv.empty:
                st.plotly_chart(
                    charts.area_series(cv, "ann_volatility_21d", pal, color_key="red",
                                       y_title="Ann. volatility", height=280,
                                       label=ui.fmt_pct(cv["ann_volatility_21d"].iloc[-1], 0, signed=False),
                                       zero_line=False),
                    use_container_width=True, config=PLOT_CONFIG,
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
                st.plotly_chart(
                    charts.indexed_comparison(pivot, pal, log=(cscale == "Log")),
                    use_container_width=True, config=PLOT_CONFIG,
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
if section == "Performance":
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
        st.plotly_chart(charts.yearly_return_bars(y, pal), use_container_width=True, config=PLOT_CONFIG)
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
        st.plotly_chart(charts.seasonality_heatmap(piv, pal, MONTHS),
                        use_container_width=True, config=PLOT_CONFIG)
        with st.expander("Monthly returns — table view"):
            st.dataframe(piv.style.format("{:+.2%}", na_rep="—"), use_container_width=True)


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------
if section == "Risk":
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
        st.plotly_chart(fig, use_container_width=True, config=PLOT_CONFIG)

        ui.section("Rolling volatility", "21-session, annualized")
        if not vol.empty:
            st.plotly_chart(
                charts.area_series(vol, "ann_volatility_21d", pal, color_key="blue",
                                   y_title="Ann. volatility", height=300,
                                   label=ui.fmt_pct(vol["ann_volatility_21d"].iloc[-1], 0, signed=False),
                                   zero_line=False),
                use_container_width=True, config=PLOT_CONFIG,
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
            st.plotly_chart(f, use_container_width=True, config=PLOT_CONFIG)
            ui.note(
                "One point per symbol, over each symbol's own listed history — so the "
                "horizon differs between points. Labels are drawn for every point here "
                "because there are only ~49 and they are the identity channel."
            )


# ---------------------------------------------------------------------------
# SQL Explorer
# ---------------------------------------------------------------------------
if section == "SQL Explorer":
    ui.section("SQL Explorer", "Every query behind this dashboard, run live")
    ui.note(
        "Pick a query to see the exact SQL, what it does and why, the rows it returns, "
        "and how long it took. Timings are measured on an uncached run, so they reflect "
        "real query cost rather than a cache hit."
    )
    st.write("")

    names = list(queries.EXPLORER)
    choice = st.selectbox("Query", names, key="sql_pick")
    spec = queries.EXPLORER[choice]

    needed = spec["params"]
    params: dict[str, str] = {}
    if needed:
        cols = st.columns(len(needed))
        for c, p in zip(cols, needed):
            with c:
                if p == "symbol":
                    params[p] = st.selectbox("symbol", list(directory["symbol"]), key="sql_sym")
                elif p == "start":
                    params[p] = st.text_input("start", START, key="sql_start")
                elif p == "end":
                    params[p] = st.text_input("end", END, key="sql_end")

    try:
        df, ms = dal.timed_read(str(spec["sql"]), params)
        err = None
    except Exception as exc:
        df, ms, err = pd.DataFrame(), 0.0, str(exc)

    ui.kpi_cards([
        {"icon": "⏱", "label": "Execution time", "value": f"{ms:,.1f} ms",
         "foot": "Uncached, measured just now"},
        {"icon": "🧾", "label": "Rows returned", "value": f"{len(df):,}",
         "foot": f"{len(df.columns)} columns"},
        {"icon": "🗄", "label": "Engine", "value": "SQLite", "small": True,
         "foot": "Views + materialized rollups"},
    ])

    st.markdown(f'<div class="note" style="margin:10px 0 2px;">{ui.esc(str(spec["explain"]))}</div>',
                unsafe_allow_html=True)
    st.code(str(spec["sql"]).strip(), language="sql")

    if err:
        st.error(f"Query failed: {err}")
    elif df.empty:
        st.info("Query returned no rows for those parameters.")
    else:
        st.markdown('<div class="note">Returned dataset (first 200 rows)</div>', unsafe_allow_html=True)
        st.dataframe(df.head(200), use_container_width=True, hide_index=True, height=360)
        st.download_button("⬇ Export result CSV", data=df.to_csv(index=False),
                           file_name="query_result.csv", mime="text/csv")

    with st.expander("Schema — tables, views and materialized rollups"):
        schema = dal.timed_read(
            "SELECT type, name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE 'idx_%' "
            "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name;"
        )[0]
        st.dataframe(schema, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# About
# ---------------------------------------------------------------------------
if section == "About":
    ui.section("About this project", "How it is built, and what the numbers do and don't mean")
    st.markdown(
        """
**Architecture.** A SQL-first pipeline in four steps:

1. `fetch_data.py` pulls ~25 years of daily OHLCV per symbol from Yahoo Finance's
   chart API into `data/prices.csv`. It caches each symbol as it arrives, so a
   rate-limited run resumes instead of starting over.
2. `build_db.py` loads the CSVs into SQLite and defines the analysis as **views**
   — daily returns, running-peak drawdowns, moving averages, rolling volatility,
   calendar-year and monthly rollups — all partitioned by symbol.
3. The two most expensive rollups are **materialized** into tables at build time.
   The leaderboard went from ~2,100 ms as a live view to ~1 ms as a table; the
   quote snapshot from ~286 ms to under 1 ms.
4. `app.py` queries and draws. It performs no analysis of its own — every number
   on screen comes back from SQL. The **SQL Explorer** tab proves it.

**Why no dual-axis charts.** Comparing symbols at different price levels uses
rebasing to 100 on a single shared axis. Two independent y-scales can be aligned
to imply any correlation you want, which is the most common way a finance chart
misleads.

**Honest limits** — the things this data genuinely cannot support:

- **Price returns, not total returns.** Dividends are excluded, so long-run
  figures understate what a shareholder actually earned.
- **No market cap.** It requires share counts, which no reachable free endpoint
  provides. Rather than hardcode a figure that goes stale, the app shows average
  dollar turnover, computed from data actually in the database.
- **Unequal histories.** Symbols listed later (META 2012, TSLA 2010) have shorter
  records. All-time leaderboards therefore show `Years` and `From` rather than
  silently ranking unequal periods against each other.
- **Sectors are equal-weighted**, not market-cap-weighted, for the same reason.
- **Survivorship bias.** The universe is a fixed list of companies that exist
  today, so it omits firms that failed or were acquired — which flatters
  long-run returns.
- **Market status is schedule-based.** Weekday 09:30–16:00 ET; exchange holidays
  are not modelled.
- **Partial calendar years** at each end of the window are flagged and faded,
  because a part-year figure is not a calendar-year return.

**Accessibility.** Series colors come from a palette validated per theme for
colorblind separation (Machado 2009 protan/deutan simulation) and WCAG contrast
against each mode's surface. Identity never rests on color alone: legends and
direct labels accompany every multi-series chart, and each chart has a table
view or CSV export.
"""
    )
    ui.section("Data source")
    ui.note(
        "Yahoo Finance chart API (unauthenticated). Prices are daily closes. "
        "This project is for analysis and portfolio demonstration — it is not "
        "investment advice."
    )

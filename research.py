"""The Research page: the landing page, and everything about one company.

**One search box for the whole app.** It resolves any US-listed stock: the local
catalog first (the 50-symbol core universe plus the ~500 ranked names, so the
common case needs no network at all) and the provider only for a ticker neither
holds. The app previously carried three separate company pickers -- one on
Companies, one on Journey, one on Live Research -- which is what made a reader
feel they had wandered into a different product on every tab.

Directly beneath it, **Ask the Market**: a reader arriving here either has a
company in mind or a question, and both belong at the same entry point.

Then **Today's Opportunities** -- the top of the ranking engine's board, on the
front page, each name clickable straight into the panel below. The engine's real
output rather than a link to it.

Three sub-views underneath, in the order a reader works through a name:

* **Snapshot** — what the company is doing now: live quote, an interactive
  chart, the profile and SEC financials, valuation, analyst consensus, trailing
  performance and recent headlines. Works for any US-listed stock.
* **History** — the recorded daily history: price, moving averages, volume,
  daily returns, cumulative return, rolling volatility, drawdown and (for the
  core universe) a peer comparison. Twenty-five years for the core 50, five for
  the rest of the ranked universe, and the difference is always labelled.
* **Journey** — that record replayed forward in time under a cursor.

Layout only. Every figure is a column from a SQL query (see queries.py) or a
field straight off the live provider payload; this module computes no metric of
its own.
"""

from __future__ import annotations

import datetime as dt
import re

import pandas as pd
import streamlit as st

import answers
import ask
import charts
import components as ui
import data_access as dal
import events
import journey
import live_data
import market_intel
import nlq
import ranking
import router
from pagectx import Ctx
from universe import INDEX_SYMBOL

VIEWS = ("Snapshot", "History", "Journey")

# Only History reads the shared date window. Snapshot carries its own span
# control (1D through Max, straight from the provider) and a Journey is the
# company's whole record by construction -- the cursor selects a POINT inside it
# rather than narrowing it. The window control is hidden on both rather than
# shown above figures it does not govern.
WINDOWED = ("History",)

DEFAULT_SYMBOL = "AAPL"

# Chart spans offered on Snapshot, in the order a reader scans them.
SPANS = ("1D", "5D", "1M", "6M", "YTD", "1Y", "5Y", "Max")
DEFAULT_SPAN = "1Y"

# How many names the landing strip shows. Four fits one row on a laptop without
# the cards shrinking to the point where the company name is unreadable.
N_PICKS = 4

# Sessions behind each pick's sparkline: one trading quarter. Short enough that
# the shape is about the move the score is describing, long enough that a single
# session cannot define it.
PICK_SPARK_SESSIONS = 60

# Stock Journey playback. `JOURNEY_STEPS` is calendar days advanced per tick --
# a journey is 25 years long, so a step of one session would take 40 minutes to
# play through and nobody would watch it.
JOURNEY_STEPS: dict[str, int] = {"Slow": 20, "Medium": 60, "Fast": 150}
JOURNEY_DEFAULT_SPEED = "Medium"
JOURNEY_TICK = 0.4  # seconds between playback frames

# Keep every 3rd session. 6,300 points is far more than a 380px chart can
# resolve, and the Journey redraws on every playback frame rather than once per
# page load. The query keeps all record highs regardless of the stride, so
# thinning cannot make the all-time-high line cut below the price it bounds.
JOURNEY_STRIDE = 3
# Only drawdowns worth narrating. Below ~15% a long record produces dozens of
# episodes and the timeline becomes noise rather than a story.
JOURNEY_MIN_DRAWDOWN = -0.15
# Sessions over which an event's effect is measured. One session is too tight
# (news after the close lands on the next day) and a month stops being about
# the event at all.
JOURNEY_EVENT_SESSIONS = 5
# How much THIS company must have moved for a market-wide event to appear on
# its timeline. See the JOURNEY_MARKET_EVENT_IMPACT comment in queries.py.
JOURNEY_MIN_MOVE = 0.05


# ---------------------------------------------------------------------------
# History — the deep record, from the local database
# ---------------------------------------------------------------------------
def history_view(ctx: Ctx, sym: str, version: str) -> None:
    """The recorded daily history for the searched company.

    Routes on which universe holds the company. The core 50 have 25 years in
    `prices` and a view per derived series; the rest of the ranked universe has
    five years in `intel_prices` and is read by one statement. A company in
    neither is not an error either -- it simply has no stored record, and the
    live Snapshot is the honest answer.
    """
    if sym in set(ctx.equities["symbol"]):
        _history_core(ctx, sym)
    elif dal.intel_bounds(version, sym) is not None:
        _history_ranked(ctx, sym, version)
    else:
        ui.empty_state(
            f"No daily history is stored for {sym}.",
            "This company is outside both the 25-year core universe and the "
            "five-year ranked universe. Snapshot still shows its live quote, "
            "chart, performance and news straight from the provider.",
        )


def _history_core(ctx: Ctx, sym: str) -> None:
    pal, preset = ctx.pal, ctx.preset
    directory, equities = ctx.directory, ctx.equities
    START, END = ctx.start, ctx.end
    start_d, end_d = ctx.start_d, ctx.end_d

    meta = equities[equities["symbol"] == sym].iloc[0]

    view_options = ["Line", "Candles"]
    ui.select_guard("co_view", view_options)
    view_mode = st.radio("View", view_options, horizontal=True,
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
        return

    listed_note = ""
    if s_min > start_d:
        listed_note = f" · history starts {s_min:%b %Y}, after the selected window opens"
    ui.quote_strip(
        str(meta["name"]), sym, cq,
        f"{meta['sector']} · {meta['industry']}{listed_note}", pal,
    )

    # Return, CAGR, volatility and max drawdown lead: they are what a company
    # is actually researched on, and the pair of them (a return and the ride
    # it took) is the comparison the About page argues for.
    # The sparkline goes on the RETURN card and nowhere else in this row: it is
    # the path the return card's own number summarizes, so it adds the shape of
    # the ride without repeating a figure. On CAGR or volatility the same curve
    # would be decoration attached to a statistic it does not depict.
    closes = cpx["close"].tolist()
    ui.kpi_cards([
        {"icon": "📅", "label": f"Return · {preset}", "value": ui.fmt_pct(cws["period_return"], 1),
         "change": f"{cws['trading_days']:,} sessions", "change_dir": "flat",
         "foot": f"{pd.to_datetime(cws['first_date']):%b %Y} → {pd.to_datetime(cws['last_date']):%b %Y}",
         "spark": ui.sparkline(closes, color=ui.spark_color(closes, pal), uid=f"co{sym}")},
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
        "Market cap and the live quote are on Snapshot, which reads them from the "
        "provider and from SEC filings. This view is the local 25-year record: "
        "average dollar turnover is shown here because it is computed from data "
        "actually in the database."
    )

    ui.section("Price history", f"{sym} · {preset}")
    # The window high/low used to be its own KPI card. It belongs beside the
    # chart that shows it, and moving it there kept the metric row to the four
    # figures a company is researched on plus two of context.
    hi_lo = (f"Window high {ui.fmt_price(cws['highest_close'])} · "
             f"low {ui.fmt_price(cws['lowest_close'])}")
    if view_mode == "Candles":
        ui.chart(charts.candlestick(cpx, pal), key="co_candle",
                 caption=hi_lo)
    else:
        fig_co = charts.price_line(cpx, pal, label=ui.fmt_price(cpx["close"].iloc[-1]))
        charts.add_events(fig_co, pal, events.in_range(cs, ce))
        ui.chart(fig_co, key="co_price", caption=hi_lo)

    # Six charts used to render in sequence here, which buried the comparison
    # below a scroll of secondary analysis. They are now one radio deep, for
    # the same reason st.tabs is refused for section switching: only the
    # selected view renders, so a visit runs one query instead of six and no
    # Plotly figure is measured inside a hidden container. Nothing was
    # removed -- every chart is still reachable, "Hide" is just the default.
    detail_options = ["Hide", "Moving averages", "Volume & daily returns",
                      "Growth & volatility", "Drawdown"]
    ui.select_guard("co_detail", detail_options)
    detail = st.radio(
        "More analysis", detail_options,
        horizontal=True, key="co_detail",
        help="Deeper charts for this company, one at a time",
    )

    if detail == "Moving averages":
        ui.section("Moving averages", "Close with trailing 50- and 200-session means")
        ma = dal.moving_averages(sym, cs, ce)
        ui.chart(charts.moving_average_chart(ma, pal), key="co_ma")
        ui.note(
            "A moving-average line only begins once its full window exists, so the "
            "200-session average is absent for the first 200 sessions rather than "
            "being averaged from fewer points than its label claims."
        )

    elif detail == "Volume & daily returns":
        left, right = st.columns(2)
        with left:
            ui.section("Trading volume")
            ui.chart(charts.volume_bars(cpx, pal), key="co_vol")
        with right:
            ui.section("Daily returns")
            dr = dal.daily_returns(sym, cs, ce)
            ui.chart(charts.returns_bars(dr, pal), key="co_ret")

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
                    key="co_cum"
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
                    key="co_vola"
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
            ui.chart(fig_dd, key="co_dd",
                     caption="Measured from the running all-time high — the loss an investor sat through")

    # ---- peer comparison ----
    ui.section("Peer comparison", "Rebased to 100 at the window start — one shared axis")

    # Open on a real peer set: the same sector, ranked by the turnover already
    # computed for the Movers table (period_movers is cached, so this is a
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
    ui.multiselect_guard("cmp_syms", known)
    stored = st.session_state.get("cmp_syms")
    if stored is not None and len(stored) > 3:
        st.session_state["cmp_syms"] = list(stored)[:3]

    cmp_syms = st.multiselect(
        "Compare with", options=list(directory["symbol"]),
        default=peer_default, key="cmp_syms", max_selections=3,
        help="Up to three. Defaults to the biggest companies in the same sector; "
             "add ^GSPC to compare against the index.",
    )
    cmp_options = ["Price (rebased)", "Cumulative return", "Drawdown",
                   "Rolling volatility", "Daily returns", "Volume"]
    ui.select_guard("cmp_metric", cmp_options)
    cmp_metric = st.radio(
        "Compare on", cmp_options,
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
            ui.chart(fig_m, key=f"co_cmp_{col}",
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
            scale_options = ["Log", "Linear"] if wide else ["Linear", "Log"]
            ui.select_guard("cmp_scale", scale_options)
            cscale = st.radio(
                "Comparison scale", scale_options,
                horizontal=True, key="cmp_scale", label_visibility="collapsed",
            )
            ui.chart(
                charts.indexed_comparison(pivot, pal, log=(cscale == "Log")),
                key="co_cmp"
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
# Journey — one company's whole record, replayed forward in time
# ---------------------------------------------------------------------------
# ALL-TIME by construction, so it does not read the shared window: a journey is
# the company's entire history, and the cursor selects a POINT inside that record
# rather than narrowing it. This is not the per-section date filter that was
# proposed and rejected twice -- nothing here re-baselines a figure that another
# view also shows.
#
# WIDGET-KEY DISCIPLINE, which the whole view is arranged around: Streamlit
# forbids writing a widget's key after that widget has been instantiated in the
# current run. The cursor is moved from four places (playback, a timeline jump,
# a Did You Know jump, a chart click), so the slider is created INSIDE the
# fragment with every cursor write happening above it. Button callbacks are safe
# anywhere because on_click fires before the next run builds any widget.
def journey_view(ctx: Ctx, sym: str) -> None:
    pal, equities = ctx.pal, ctx.equities

    if sym not in set(equities["symbol"]):
        ui.empty_state(
            f"There is no journey for {sym}.",
            "A journey replays a quarter-century of price history against the "
            "curated events that moved it, and that record exists for the 50 "
            "core companies. Search one of those — or read this company on "
            "Snapshot and History instead.",
        )
        return

    jsym = sym
    jmeta = equities[equities["symbol"] == jsym].iloc[0]
    j_min, j_max = dal.date_bounds(jsym)

    ui.section("Stock Journey",
               f"Travel through {jmeta['name']}'s history and watch it happen")

    head = st.columns([2.4, 1.15, 1.15])
    with head[1]:
        ui.select_guard("jrn_speed", list(JOURNEY_STEPS))
        jrn_speed = st.radio("Speed", list(JOURNEY_STEPS), horizontal=True,
                             index=list(JOURNEY_STEPS).index(JOURNEY_DEFAULT_SPEED),
                             key="jrn_speed", label_visibility="collapsed",
                             help="How much history each playback frame advances")

    # A default selection must follow its subject. The cursor is reset whenever
    # the company changes -- otherwise switching from Apple to a company that
    # listed in 2020 leaves the cursor in 2015, before that company has a single
    # price row, and every panel empties at once. Written here, before the
    # slider exists, which is the only point Streamlit allows it.
    if st.session_state.get("jrn_last_symbol") != jsym:
        st.session_state["jrn_last_symbol"] = jsym
        st.session_state["jrn_slider"] = j_max
        st.session_state["jrn_playing"] = False
        st.session_state.pop("jrn_pending_jump", None)

    # Clamp a cursor that survived a company change or a hot-update. st.slider
    # raises on a stored value outside its own min/max, the same failure mode
    # the date preset had.
    stored = st.session_state.get("jrn_slider")
    if isinstance(stored, dt.date) and not (j_min <= stored <= j_max):
        st.session_state["jrn_slider"] = min(max(stored, j_min), j_max)

    def jrn_jump(date_str: str) -> None:
        """Move the cursor. Safe from a button callback: it runs before the rerun."""
        target = dt.date.fromisoformat(str(date_str)[:10])
        st.session_state["jrn_slider"] = min(max(target, j_min), j_max)
        st.session_state["jrn_playing"] = False  # a jump is a deliberate stop

    with head[2]:
        playing = st.session_state.get("jrn_playing", False)
        btn = st.columns(2)
        with btn[0]:
            if st.button("⏸ Pause" if playing else "▶ Play", key="jrn_play",
                         width="stretch", type="secondary" if playing else "primary"):
                # At the end of the record, Play restarts rather than doing
                # nothing -- a button that looks live and is inert reads as broken.
                if not playing and st.session_state.get("jrn_slider", j_max) >= j_max:
                    st.session_state["jrn_slider"] = j_min
                st.session_state["jrn_playing"] = not playing
                st.rerun()
        with btn[1]:
            if st.button("↺ Restart", key="jrn_restart", width="stretch"):
                st.session_state["jrn_slider"] = j_min
                st.session_state["jrn_playing"] = False
                st.rerun()

    step_days = JOURNEY_STEPS[jrn_speed]
    is_playing = st.session_state.get("jrn_playing", False)

    # The fragment is what makes playback affordable. A full script rerun every
    # 0.4s would re-run the control row, the section radio and every query the
    # page above owns; a fragment redraws only this panel.
    @st.fragment(run_every=JOURNEY_TICK if is_playing else None)
    def journey_panel() -> None:
        # --- Every cursor write happens here, ABOVE the slider. ---
        pending = st.session_state.pop("jrn_pending_jump", None)
        if pending is not None:
            st.session_state["jrn_slider"] = min(max(pending, j_min), j_max)

        if st.session_state.get("jrn_playing"):
            nxt = st.session_state.get("jrn_slider", j_min) + dt.timedelta(days=step_days)
            if nxt >= j_max:
                st.session_state["jrn_slider"] = j_max
                st.session_state["jrn_playing"] = False
                # scope="app" so the fragment is re-decorated WITHOUT run_every.
                # A fragment-scoped rerun would leave the timer running against
                # a cursor that can no longer move.
                st.rerun(scope="app")
            st.session_state["jrn_slider"] = nxt

        cursor = st.slider(
            "Journey cursor", min_value=j_min, max_value=j_max,
            value=st.session_state.get("jrn_slider", j_max),
            key="jrn_slider", format="MMM YYYY", label_visibility="collapsed",
            help="Drag to travel through this company's history",
        )
        asof = cursor.isoformat()

        snap = dal.journey_snapshot(jsym, asof)
        path = dal.journey_price_path(jsym, JOURNEY_STRIDE)

        if snap is None or path.empty:
            ui.empty_state(
                f"{jmeta['name']} has no sessions on or before {cursor:%b %d, %Y}.",
                "Drag the cursor forward — this company listed later than the "
                "start of the record.", kind="warn")
            return

        # --- Facts for THIS instant. Every one is a query against `asof`, so
        # nothing on the panel can describe a moment the chart is not showing.
        records = dal.journey_records(jsym, asof)
        extremes = dal.journey_extremes(jsym, asof, limit=3)
        up_runs = dal.journey_streaks(jsym, asof, direction=1, limit=1)
        down_runs = dal.journey_streaks(jsym, asof, direction=-1, limit=1)
        best_worst = dal.journey_best_worst(jsym, asof)
        # The cursor is a query parameter on every one of these, never a filter
        # applied to the result. An episode still open at the cursor must report
        # a NULL recovery rather than one dated in the reader's future.
        crashes = dal.journey_drawdowns(jsym, asof, min_depth=JOURNEY_MIN_DRAWDOWN)
        trends = dal.journey_trend_changes(jsym, asof)
        co_events = dal.journey_company_events(jsym, asof, sessions=JOURNEY_EVENT_SESSIONS)
        mkt_events = dal.journey_market_events(
            jsym, asof, sessions=JOURNEY_EVENT_SESSIONS, min_move=JOURNEY_MIN_MOVE)

        moments = journey.timeline(
            company_events=co_events, market_events=mkt_events,
            drawdowns=crashes, extremes=extremes, asof=asof,
        )

        ui.journey_header(
            cursor.strftime("%B %-d, %Y"), snap["close"],
            journey.chapter_label(snap),
            f"{ui.fmt_pct(snap['return_to_date'])} since {snap['first_date'][:4]}",
            "up" if snap["return_to_date"] >= 0 else "down",
        )

        ui.kpi_cards([
            {"icon": "📅", "label": "Into the journey",
             "value": f"{snap['years_elapsed']:.1f} yrs",
             "foot": f"{int(snap['sessions_elapsed']):,} sessions traded"},
            {"icon": "📈", "label": "Compound annual return",
             "value": ui.fmt_pct(snap["cagr_to_date"], 1),
             "foot": "Annualized, from the first session in the record"},
            {"icon": "🏔️", "label": "All-time high so far",
             "value": ui.fmt_price(snap["peak_close"]),
             "foot": "The highest close reached by this date"},
            {"icon": "🕳️", "label": "Below that high",
             "value": ui.fmt_pct(snap["drawdown"], 1),
             "change_dir": "down" if snap["drawdown"] < -0.001 else "flat",
             "foot": "Distance from the running record"},
        ])

        panes = st.columns([2.45, 1.0])
        with panes[0]:
            fig = charts.journey_path(path, pal, asof=asof, moments=moments,
                                      log=st.session_state.get("jrn_log", True))
            picked = ui.chart(
                fig, key="jrn_path", select=True,
                caption=("Solid is history travelled, faint is still ahead. Diamonds, "
                         "circles and squares are events — hover one, or click "
                         "anywhere on the line to travel there.")
            )
            # A click is stashed and applied on the NEXT fragment run, above the
            # slider. Applying it here would write an already-instantiated
            # widget's key, which Streamlit forbids.
            if picked and picked.get("selection", {}).get("points"):
                clicked = str(picked["selection"]["points"][0].get("x", ""))[:10]
                if clicked and clicked != st.session_state.get("jrn_last_click"):
                    st.session_state["jrn_last_click"] = clicked
                    try:
                        st.session_state["jrn_pending_jump"] = dt.date.fromisoformat(clicked)
                        st.session_state["jrn_playing"] = False
                        st.rerun(scope="fragment")
                    except ValueError:
                        pass  # a click on a non-date axis position; ignore

            ui.chart(charts.journey_drawdown_band(path, pal, asof=asof),
                     key="jrn_dd",
                     caption="How far below its own record the company was, at every point so far.")

        with panes[1]:
            ui.section("Timeline", f"{len(moments)} moments so far")
            with st.container(height=560):
                ui.journey_timeline(moments, on_jump=jrn_jump, key_prefix="jrntl")

        ui.section("Did you know?", f"What the record says about {jmeta['name']} by this date")
        facts = journey.did_you_know(
            name=str(jmeta["name"]), snapshot=snap, records=records, extremes=extremes,
            up_streaks=up_runs, down_streaks=down_runs, best_worst=best_worst,
            drawdowns=crashes, company_events=co_events, trend_changes=trends,
        )
        ui.did_you_know(facts, on_jump=jrn_jump, key_prefix="jrnfact")

        ui.table_view(
            "Timeline data",
            pd.DataFrame([{
                "Date": m.date, "Kind": m.kind, "Event": m.title,
                # Scaled to percentage points here, like every other percent
                # column in the app: the `%%` format string prints the number
                # it is given, so a raw 0.0643 would render as "0.06%".
                "Company move": None if m.move is None else m.move * 100,
                "Source": m.source,
            } for m in moments]),
            column_config={"Company move": st.column_config.NumberColumn(
                "Company move", format="%+.2f%%")},
            footer=("Company events are curated and carry a source. Market events are "
                    "kept only where this company itself moved at least "
                    f"{JOURNEY_MIN_MOVE:.0%}. Milestones are computed from the price data."),
        )

    journey_panel()

    ui.note(
        "The journey is always the company's whole record, so the date filter above "
        "does not apply here. Returns are price-only and exclude dividends, and the "
        "first session shown is where this dataset begins — not the company's IPO, "
        "which appears as a curated event where one is recorded."
    )


# ---------------------------------------------------------------------------
# History for the wider ranked universe
#
# Same charts, same metric definitions, a shorter record. The one thing this
# must never do is present five years as though it were the twenty-five the core
# universe gets: a "max drawdown" measured from a five-year high is a different
# statement from one measured from an all-time high, and a reader comparing the
# two without knowing that is being misled by the layout. Hence the banner, and
# hence the recorded span is printed rather than implied.
# ---------------------------------------------------------------------------
def _history_ranked(ctx: Ctx, sym: str, version: str) -> None:
    pal, preset = ctx.pal, ctx.preset
    start_d, end_d = ctx.start_d, ctx.end_d

    bounds = dal.intel_bounds(version, sym)
    first_rec, last_rec = bounds
    row = dal.panel_row(version, sym)
    name = str(row["name"]) if row is not None else sym
    sector = str(row.get("sector") or "") if row is not None else ""

    cs = max(start_d, first_rec).isoformat()
    ce = end_d.isoformat()

    hist = dal.intel_history(version, sym, cs, ce)
    stats = dal.intel_window_stats(version, sym, cs, ce)

    ui.section(f"{name} · recorded history",
               f"{sector} · {preset}" if sector else preset)
    ui.empty_state(
        "Historical data limited to available history.",
        f"{sym} is in the ranked universe rather than the 25-year core, so the "
        f"record here runs {first_rec:%b %Y} → {last_rec:%b %Y} "
        f"({(last_rec - first_rec).days // 365} years). Every figure below is "
        f"computed the same way as for a core company — a return, a drawdown and "
        f"a volatility over this shorter record, not over a full history.",
        kind="info",
    )

    if hist.empty or stats is None:
        ui.empty_state(f"{sym} has no sessions inside the selected window.",
                       f"Its record starts {first_rec:%b %Y}. Widen the date "
                       "range above, or read the live quote on Snapshot.",
                       kind="warn")
        return

    rk_closes = hist["close"].tolist()
    ui.kpi_cards([
        {"icon": "📅", "label": f"Return · {preset}",
         "value": ui.fmt_pct(stats["period_return"], 1),
         "change": f"{stats['trading_days']:,} sessions", "change_dir": "flat",
         "foot": f"{pd.to_datetime(stats['first_date']):%b %Y} → "
                 f"{pd.to_datetime(stats['last_date']):%b %Y}",
         "spark": ui.sparkline(rk_closes, color=ui.spark_color(rk_closes, pal),
                               uid=f"rk{sym}")},
        {"icon": "📈", "label": "CAGR", "value": ui.fmt_pct(stats["cagr"], 1),
         "foot": "Annualized over the window"},
        {"icon": "〰", "label": "Volatility",
         "value": ui.fmt_pct(stats["ann_volatility"], 1, signed=False),
         "foot": "Annualized, from daily returns"},
        {"icon": "📉", "label": "Max drawdown",
         "value": ui.fmt_pct(stats["max_drawdown"], 1),
         "change": "within this record", "change_dir": "down",
         "foot": f"Deepest fall from a high since {first_rec:%b %Y}"},
        {"icon": "🔊", "label": "Avg volume", "value": ui.fmt_compact(stats["avg_volume"]),
         "foot": f"{ui.fmt_dollar_compact(stats['avg_dollar_volume'])} avg turnover"},
    ])

    view_options = ["Line", "Candles"]
    ui.select_guard("co_view", view_options)
    view_mode = st.radio("View", view_options, horizontal=True,
                         key="co_view", label_visibility="collapsed")

    ui.section("Price history", f"{sym} · {preset}")
    hi_lo = (f"Window high {ui.fmt_price(stats['highest_close'])} · "
             f"low {ui.fmt_price(stats['lowest_close'])}")
    if view_mode == "Candles":
        ui.chart(charts.candlestick(hist, pal), key="rk_candle",
                 caption=hi_lo)
    else:
        fig = charts.price_line(hist, pal, label=ui.fmt_price(hist["close"].iloc[-1]))
        charts.add_events(fig, pal, events.in_range(cs, ce))
        ui.chart(fig, key="rk_price", caption=hi_lo)

    # The same one-at-a-time radio the core view uses, minus the peer comparison:
    # that one rebases several symbols against each other over the shared window
    # and reads the core `prices` table, so offering it here would compare a
    # five-year record against twenty-five-year ones on one axis.
    detail_options = ["Hide", "Moving averages", "Volume & daily returns",
                      "Growth & volatility", "Drawdown"]
    ui.select_guard("co_detail", detail_options)
    detail = st.radio("More analysis", detail_options, horizontal=True,
                      key="co_detail",
                      help="Deeper charts for this company, one at a time")

    if detail == "Moving averages":
        ui.section("Moving averages", "Close with trailing 50- and 200-session means")
        ui.chart(charts.moving_average_chart(hist, pal), key="rk_ma")
        ui.note(
            "A moving-average line only begins once its full window exists, so the "
            "200-session average is absent for the first 200 sessions rather than "
            "being averaged from fewer points than its label claims."
        )

    elif detail == "Volume & daily returns":
        left, right = st.columns(2)
        with left:
            ui.section("Trading volume")
            ui.chart(charts.volume_bars(hist, pal), key="rk_vol")
        with right:
            ui.section("Daily returns")
            ui.chart(charts.returns_bars(hist.dropna(subset=["daily_return"]), pal),
                     key="rk_ret")

    elif detail == "Growth & volatility":
        l2, r2 = st.columns(2)
        with l2:
            ui.section("Cumulative return", "Compounded from the window's first session")
            ui.chart(
                charts.area_series(hist, "cumulative_return", pal, color_key="blue",
                                   y_title="Cumulative", height=charts.H_COMPACT,
                                   label=ui.fmt_pct(hist["cumulative_return"].iloc[-1], 0)),
                key="rk_cum"
            )
        with r2:
            ui.section("Rolling volatility", "21-session, annualized")
            cv = hist.dropna(subset=["ann_volatility_21d"])
            if not cv.empty:
                ui.chart(
                    charts.area_series(cv, "ann_volatility_21d", pal, color_key="red",
                                       y_title="Ann. volatility", height=charts.H_COMPACT,
                                       label=ui.fmt_pct(cv["ann_volatility_21d"].iloc[-1], 0,
                                                        signed=False),
                                       zero_line=False),
                    key="rk_vola"
                )

    elif detail == "Drawdown":
        ui.section("Drawdown", "How far below its previous high the price sat")
        fig_dd = charts.area_series(hist, "drawdown", pal, color_key="red",
                                    y_title="Drawdown", height=charts.H_COMPACT,
                                    tickformat=".0%")
        worst = hist.loc[hist["drawdown"].idxmin()]
        fig_dd.add_annotation(
            x=worst["date"], y=worst["drawdown"],
            text=f"{worst['drawdown'] * 100:.1f}% · {pd.to_datetime(worst['date']):%b %Y}",
            showarrow=True, arrowhead=0, arrowcolor=pal["muted"], ax=0, ay=-26,
            font=dict(color=pal["text_primary"], size=11.5),
        )
        charts.add_events(fig_dd, pal, events.in_range(cs, ce), label=False)
        ui.chart(fig_dd, key="rk_dd",
                 caption=f"Measured from the running high since {first_rec:%b %Y} — "
                         "not an all-time high, because the record does not go back "
                         "that far")

    ui.table_view("Show the daily record behind these charts", hist)


# ---------------------------------------------------------------------------
# Snapshot — live, for any US-listed stock
# ---------------------------------------------------------------------------
def snapshot_view(ctx: Ctx, sym: str, version: str) -> None:
    pal = ctx.pal
    quote = live_data.quote(sym)
    if not quote:
        ui.empty_state(
            f"No live quote came back for {sym}.",
            "The provider may be rate-limiting or the ticker may have been "
            "delisted. Try again shortly, or search for a different company.",
            kind="warn",
        )
        return

    stored = dal.panel_row(version, sym)
    _quote_header(quote, stored, pal)
    _price_chart(sym, quote, pal)

    col_a, col_b = st.columns(2)
    with col_a:
        _profile_card(quote, stored)
        _valuation_card(stored)
    with col_b:
        _financials_card(stored)
        _analyst_card(stored)

    _performance_card(sym, pal)
    _news_panel(sym)


def _quote_header(q: dict, stored, pal: dict) -> None:
    """Name the company before quoting its price.

    The meta line is assembled from what is actually known: the exchange always,
    the sector only when this company is in the ranked universe, and the stamp
    only when the provider sent one. A header that prints "· ·" around missing
    fields reads as broken rather than as incomplete.
    """
    bits = [q.get("exchange"), q.get("currency")]
    if stored is not None and stored.get("sector"):
        bits.insert(1, str(stored["sector"]))
    if q.get("market_time"):
        bits.append(dt.datetime.fromtimestamp(q["market_time"])
                    .strftime("As of %b %d, %Y %H:%M"))
    ui.live_quote_strip(q, " · ".join(str(b) for b in bits if b), pal)

    # Price and its change moved into the header above, so the card row is four
    # figures that are NOT repeated anywhere else on the panel.
    ui.kpi_cards([
        {"icon": "📊", "label": "Day Range",
         "value": f"{ui.fmt_price(q.get('day_low'))} – {ui.fmt_price(q.get('day_high'))}",
         "small": True, "foot": "Session low to high"},
        {"icon": "📈", "label": "52-Week Range",
         "value": f"{ui.fmt_price(q.get('w52_low'))} – {ui.fmt_price(q.get('w52_high'))}",
         "small": True, "foot": "Trailing one year"},
        {"icon": "🔙", "label": "Previous close",
         "value": ui.fmt_price(q.get("prev_close")),
         "small": True, "foot": "What the change is measured from"},
        {"icon": "🔄", "label": "Volume",
         "value": ui.fmt_compact(q.get("volume")),
         "small": True, "foot": "Shares traded this session"},
    ])


def _price_chart(symbol: str, quote: dict, pal: dict) -> None:
    ui.select_guard("mi_span", SPANS)
    span = st.radio("Span", SPANS, horizontal=True, index=SPANS.index(DEFAULT_SPAN),
                    key="mi_span", label_visibility="collapsed")
    df = live_data.history(symbol, span)
    if df.empty:
        ui.empty_state(f"No price history came back for {symbol} over {span}.",
                       "Try a longer span — newly listed companies have no "
                       "five-year record to draw.")
        return

    view_options = ["Line", "Candles"]
    ui.select_guard("mi_view", view_options)
    view = st.radio("View", view_options, horizontal=True, key="mi_view",
                    label_visibility="collapsed")
    if view == "Candles" and {"open", "high", "low"}.issubset(df.columns) \
            and not df[["open", "high", "low"]].isna().all().any():
        fig = charts.candlestick(df, pal)
    else:
        fig = charts.price_line(df, pal, label=ui.fmt_price(quote["price"]))
    ui.chart(fig, key=f"mi_price_{symbol}_{span}",
             config={"displayModeBar": False},
             caption=f"{ui.esc(quote['name'])} · {span} · {len(df):,} sessions")
    ui.table_view("Show the prices behind this chart", df)


def _profile_card(quote: dict, stored) -> None:
    rows = [
        ("Name", quote.get("name") or "—"),
        ("Exchange", quote.get("exchange") or "—"),
        ("Currency", quote.get("currency") or "—"),
    ]
    if stored is not None:
        rows.append(("Sector", str(stored.get("sector") or "—")))
        rows.append(("Market cap", ui.fmt_metric(stored.get("market_cap"), "money")))
        rows.append(("Shares outstanding", ui.fmt_compact(stored.get("shares_outstanding"))))
    if quote.get("first_trade"):
        first = dt.datetime.fromtimestamp(quote["first_trade"]).strftime("%b %d, %Y")
        rows.append(("First traded", first))
    ui.card("Company profile", rows)
    if stored is None:
        st.markdown(
            '<div class="note">Sector, market cap and fundamentals come from SEC '
            'filings and are stored for the ranked universe only. This company '
            'is outside it, so only live price data is shown.</div>',
            unsafe_allow_html=True)


def _valuation_card(stored) -> None:
    if stored is None:
        return
    keys = [("pe", "P/E"), ("ps", "P/S"), ("pb", "P/B"), ("fcf_yield", "FCF yield")]
    rows = [(label, ui.fmt_metric(stored.get(k), ranking.BY_KEY[k].fmt)) for k, label in keys]
    ui.card("Valuation", rows)
    if stored.get("pe") is None and stored.get("net_income") is not None:
        st.markdown('<div class="note">P/E is blank because trailing earnings are '
                    'negative — a negative multiple is not a cheap one.</div>',
                    unsafe_allow_html=True)


def _financials_card(stored) -> None:
    if stored is None:
        return
    rows = [
        ("Revenue (TTM)", ui.fmt_metric(stored.get("revenue"), "money")),
        ("Net income (TTM)", ui.fmt_metric(stored.get("net_income"), "money")),
        ("Total assets", ui.fmt_metric(stored.get("assets"), "money")),
        ("Shareholder equity", ui.fmt_metric(stored.get("equity"), "money")),
        ("Gross margin", ui.fmt_metric(stored.get("gross_margin"), "pct")),
        ("Net margin", ui.fmt_metric(stored.get("net_margin"), "pct")),
        ("Return on equity", ui.fmt_metric(stored.get("roe"), "pct")),
        ("Revenue growth (YoY)", ui.fmt_metric(stored.get("revenue_growth"), "pct")),
    ]
    ui.card("Key financials · SEC filings", rows)
    if stored.get("fundamentals_asof"):
        st.markdown(f'<div class="note">Latest reported period ending '
                    f'{ui.esc(str(stored["fundamentals_asof"]))}. Figures are as '
                    f'filed with the SEC in XBRL.</div>', unsafe_allow_html=True)


def _analyst_card(stored) -> None:
    has = stored is not None and stored.get("analyst_score") is not None
    if has:
        ui.card("Analyst consensus", [
            ("Consensus rating", ui.fmt_metric(stored.get("analyst_score"), "num")),
            ("Price target upside", ui.fmt_metric(stored.get("target_upside"), "pct")),
            ("EPS revision trend", ui.fmt_metric(stored.get("eps_revision"), "pct")),
            ("Analysts covering", ui.fmt_compact(stored.get("n_analysts"))),
        ])
        return
    ui.empty_state(
        "Analyst consensus is not configured.",
        "No keyless provider serves consensus ratings, price targets or earnings "
        "estimates — Yahoo's endpoints return 401. Set an optional provider key "
        "to enable this panel; the rankings run without it and simply drop the "
        "analyst category.",
    )


def _performance_card(symbol: str, pal: dict) -> None:
    perf = live_data.performance(symbol)
    if not perf:
        return
    ui.section("Performance", "Trailing total price return, live from the provider")
    df = pd.DataFrame({"period": list(perf), "return": [perf[k] for k in perf]})
    fig = charts.generic_bars(df, pal, label_col="period", value_col="return",
                              height=charts.H_COMPACT, tickformat=".0%")
    ui.chart(fig, key=f"mi_perf_{symbol}", config={"displayModeBar": False},
             caption="Price return only — dividends are excluded, as everywhere "
                     "else in this app.")


def _news_panel(symbol: str) -> None:
    items = live_data.news(symbol)
    ui.section("Recent news", "Headlines from the provider, linked to the source")
    if not items:
        ui.empty_state("No recent headlines came back for this company.",
                       "Coverage is thinner for smaller companies. Try a larger "
                       "or more widely followed name.")
        return
    out = ['<div class="mi-card">']
    for item in items:
        when = ""
        if item.get("published"):
            when = dt.datetime.fromtimestamp(item["published"]).strftime("%b %d, %Y")
        link = ui.esc(item["link"])
        # Empty string when the story carries no art, so the headline takes the
        # full width rather than indenting past a blank square.
        thumb = ui.img_tile(item.get("thumb", ""), "mi-thumb")
        out.append(
            f'<div class="mi-news">{thumb}<div class="mi-news-txt">'
            f'<a href="{link}" target="_blank" rel="noopener">'
            f'{ui.esc(item["title"])}</a>'
            f'<div class="src">{ui.esc(item["publisher"])} · {ui.esc(when)}</div>'
            f'</div></div>')
    out.append("</div>")
    st.markdown("".join(out), unsafe_allow_html=True)
    st.markdown('<div class="note">Headlines are shown as published and are never '
                'summarized by the model — a paraphrase of an article the model '
                'cannot open is a fabrication risk with no upside.</div>',
                unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Symbol resolution — the one search box
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _catalog(version: str) -> pd.DataFrame:
    """Every company the app holds data for, as one searchable frame.

    Searched BEFORE the provider, which is the whole point: a reader typing
    "COST" or "Costco" gets an answer from the database in under a millisecond
    instead of a network round trip on every keystroke, and the page keeps
    working when the provider is unreachable. The provider is still consulted
    for anything this frame does not hold, because Snapshot is meant to work for
    any US-listed stock -- not only the ones already loaded.
    """
    core = dal.directory()
    core = core[core["is_index"] == 0][["symbol", "name"]].assign(exchange="")
    wide = dal.intel_universe(version)[["symbol", "name", "exchange"]]
    cat = (pd.concat([core, wide], ignore_index=True)
             .dropna(subset=["symbol"])
             .drop_duplicates(subset="symbol", keep="first")
             .reset_index(drop=True))
    cat["u_symbol"] = cat["symbol"].astype(str).str.upper()
    cat["u_name"] = cat["name"].astype(str).str.upper()
    return cat


def _local_matches(text: str, version: str, limit: int = 8) -> list[dict]:
    """Ranked local hits: exact ticker, then prefix, then anywhere in the name."""
    cat = _catalog(version)
    t = text.strip().upper()
    if not t:
        return []
    pat = re.escape(t)
    tiers = [
        cat[cat["u_symbol"] == t],
        cat[cat["u_symbol"].str.startswith(t) | cat["u_name"].str.startswith(t)],
        cat[cat["u_symbol"].str.contains(pat) | cat["u_name"].str.contains(pat)],
    ]
    hits = (pd.concat(tiers, ignore_index=True)
              .drop_duplicates(subset="symbol", keep="first")
              .head(limit))
    return [{"symbol": r["symbol"], "name": r["name"],
             "exchange": r["exchange"] or "local"} for _, r in hits.iterrows()]


def _resolve(typed: str, version: str) -> str | None:
    """Turn what the reader typed into one symbol.

    Returns the stored symbol when the box is empty, so the page keeps showing
    the last company rather than resetting to a default on every rerun.
    """
    current = st.session_state.get("rs_symbol", DEFAULT_SYMBOL)
    if not typed.strip():
        return current

    matches = _local_matches(typed, version) or live_data.search(typed)
    if not matches:
        ui.empty_state(
            f"Nothing US-listed matches “{typed}”.",
            "Try a ticker (AAPL) or a fuller company name. Only NYSE, Nasdaq "
            "and NYSE American equities are searchable — funds, ADR lines on "
            "foreign venues and indices are filtered out.",
        )
        return None

    if len(matches) == 1:
        st.session_state["rs_symbol"] = matches[0]["symbol"]
        return matches[0]["symbol"]

    labels = [f"{m['symbol']} — {m['name']} ({m['exchange']})" for m in matches]
    ui.select_guard("rs_match", labels)
    chosen = st.radio("Match", labels, key="rs_match", label_visibility="collapsed")
    symbol = matches[labels.index(chosen)]["symbol"]
    st.session_state["rs_symbol"] = symbol
    return symbol


def _use_pick(symbol: str) -> None:
    """Load a top-pick card into the research panel.

    A button callback, which is the only place this is safe: `on_click` fires
    BEFORE the next run builds any widget, and both keys written here belong to
    widgets (`rs_query`, `rs_match`) that Streamlit would refuse to let us touch
    once they exist. Clearing the query is deliberate -- a click is a choice, and
    leaving a stale search string in the box would re-resolve over the top of it
    on the very next rerun.
    """
    st.session_state["rs_symbol"] = symbol
    st.session_state["rs_query"] = ""
    st.session_state.pop("rs_match", None)


def _use_example(text: str) -> None:
    """Load a suggestion chip into the Ask box. Same callback rule as above."""
    st.session_state.ask_q = text


# ---------------------------------------------------------------------------
# The landing page
# ---------------------------------------------------------------------------
def _ask_input() -> str:
    """The Ask the Market box, beside the company search rather than beneath it.

    Both are entry points, so they belong on one row -- but stacked they read as
    a single two-line form, and worse, the second box plus its six suggestion
    buttons pushed the searched company's own panel below the fold. Searching a
    company and then having to scroll past a question box to see the answer is
    the opposite of what the search is for.

    The suggestions live in a collapsed expander for the same reason: they are
    useful the first time and cost a row of vertical space on every visit after.
    """
    st.markdown('<div class="rs-lbl">Ask the market</div>', unsafe_allow_html=True)
    asked = st.text_input(
        "Ask the Market", key="ask_q", label_visibility="collapsed",
        placeholder="e.g. “Which sector performed best?”",
    )
    with st.expander("Example questions"):
        # Two across rather than six down. Stacked full sentences made the open
        # expander taller than the panel it sits beside, so opening it shoved the
        # page around; a short label in a 2-up grid is three rows instead of six
        # and each one is scannable at a glance. The sentence that actually gets
        # asked is the tooltip, so nothing is hidden -- only deferred.
        cols = st.columns(2)
        for i_e, (label, question) in enumerate(ask.EXAMPLES):
            with cols[i_e % 2]:
                st.button(label, key=f"ask_ex_{i_e}", width="stretch",
                          help=question, on_click=_use_example, args=(question,))
    return asked


def _ask_answer(ctx: Ctx, asked: str) -> None:
    """Render the answer to a question, if one was asked.

    Split from the input so it can render at full width below the entry row --
    and so it takes NO vertical space when nobody has asked anything, which is
    the common case for a reader who came here to look up a company.

    Every question goes through the Query Router, which prefers an existing SQL
    template and only generates SQL when none fits -- so a template answer is the
    same query the rest of the app runs, and an answer can never disagree with
    the page beside it.
    """
    if not (asked and asked.strip()):
        return

    # The current window goes in as the second precedence tier: a question that
    # names its own window overrides it, one that doesn't inherits it.
    with st.spinner("Reading the question…"):
        routed = router.route(
            asked, ctx.directory,
            ui_start=ctx.start, ui_end=ctx.end, ui_preset=ctx.preset,
            data_min=ctx.index_min, data_max=ctx.end_d,
        )

    if routed.path == router.TEMPLATE_PATH:
        answers.HANDLERS[routed.template.handler](ctx.directory, ctx.pal, routed.params)
    elif routed.path == router.GENERATED_PATH:
        # No template covered this one, so the answer came from generated SQL.
        # The insight is written from the returned rows, never from the model's
        # own recollection -- it can only describe what the query actually found.
        with st.spinner("Reading the result…"):
            said = nlq.insight(asked, routed.df.head(30).to_csv(index=False))
        answers.generated(asked, routed.df, ctx.pal, insight_text=said or "")
    else:
        ui.empty_state(routed.error,
                       "Try naming a company, a sector or a time period — "
                       "or open Example questions above.", kind="info")
    st.divider()


def _pick_name(full: str, limit: int = 22) -> str:
    """A company name short enough to sit on the chip's second line.

    Truncated on a word boundary where one is available, because a hard cut mid
    word ("Internation…") reads as a rendering fault rather than as an
    abbreviation. The full name is still in the chip's tooltip.
    """
    name = " ".join(str(full).split())
    if len(name) <= limit:
        return name
    cut = name[:limit].rsplit(" ", 1)[0]
    return (cut if len(cut) >= limit * 0.6 else name[:limit]).rstrip(" ,.") + "…"


def _opportunities(ctx: Ctx, version: str) -> None:
    """Today's Opportunities: the ranking engine's board, on the front page.

    Fixed to Medium-Term / Balanced (see `market_intel.LANDING_HORIZON`) rather
    than inheriting the Intelligence page's filters, so the landing page says the
    same thing on every visit. Each chip loads that company into the panel below.

    STILL ONE ROW. This began as four cards with a button under each -- about
    250px of the first screen, which pushed the searched company's own panel out
    of view -- and was collapsed to a row of bare chips to win that space back.
    It is now a row of *cards* without being a card grid: the card IS the button.
    Two lines of label (ticker + score, then the company name) and a sparkline
    painted behind them as a CSS background image, which is the only way a
    Streamlit button can carry a graphic -- its label is markdown text and cannot
    hold an <svg>. That costs ~28px over the chip row rather than the ~210px the
    card block cost, so the engine stays above the fold and the reader gets the
    shape of the move as well as the score.
    """
    picks = market_intel.top_picks(version, N_PICKS)
    if picks.empty:
        return

    # Per-symbol styling. Streamlit gives each widget a `st-key-<key>` container
    # class and the key carries the symbol, so this is a generated rule per pick
    # rather than a wrapper element the button could not live inside anyway.
    rules = []
    for _, row in picks.iterrows():
        sym = str(row["symbol"])
        color = market_intel.band_color(float(row["overall_score"]), ctx.pal)
        # A ticker can carry '.' or '-' (BRK-B, BF.B). Both are legal in a class
        # NAME but '.' opens a class SELECTOR, so it has to be escaped here.
        css_sym = sym.replace(".", "\\.")
        closes = dal.intel_sparkline(version, sym, PICK_SPARK_SESSIONS)["close"].tolist()
        # Direction comes from the series itself (`spark_color`), not from the
        # score: the score is a cross-sectional rank and the line is this
        # company's own move, so colouring one by the other would be two
        # different claims wearing one hue.
        spark = ui.sparkline_uri(closes, color=ui.spark_color(closes, ctx.pal),
                                 w=78, h=22, uid=f"p{sym}")
        rules.append(
            f"div.st-key-rs_pick_{css_sym} button:not(:disabled)"
            f" {{ border-left: 3px solid {color} !important; }}")
        if spark:
            rules.append(
                f"div.st-key-rs_pick_{css_sym} button"
                f" {{ background-image: {spark} !important; }}")
    st.markdown("<style>" + "".join(rules) + "</style>", unsafe_allow_html=True)

    current = st.session_state.get("rs_symbol", DEFAULT_SYMBOL)
    cols = st.columns([1.9] + [1.15] * len(picks) + [1.5])
    with cols[0]:
        st.markdown(
            f'<div class="pick-lead">TODAY&#8217;S OPPORTUNITIES'
            f'<span>{ui.esc(market_intel.LANDING_HORIZON)} · '
            f'{ui.esc(market_intel.LANDING_OBJECTIVE)} · ranked in SQL</span></div>',
            unsafe_allow_html=True)

    # The chip that is currently loaded says so, and is disabled. Without it the
    # strip looked identical before and after a click while the panel below
    # silently changed underneath it -- no confirmation that the thing you
    # pressed is the thing you are now reading -- and the pressed button kept its
    # focus styling, which reads as stuck.
    for i, (_, row) in enumerate(picks.iterrows()):
        sym = str(row["symbol"])
        active = sym == current
        with cols[i + 1]:
            st.button(
                f"**{sym}**  ·  {float(row['overall_score']):.0f}  \n"
                f"{_pick_name(row['name'])}",
                key=f"rs_pick_{sym}", width="stretch", disabled=active,
                on_click=_use_pick, args=(sym,),
                help=(f"{row['name']} — shown below" if active else
                      f"{row['name']} · {row.get('sector') or 'sector unknown'} · "
                      f"score {float(row['overall_score']):.0f}/100. "
                      f"The line is the last {PICK_SPARK_SESSIONS} sessions. "
                      f"Load it into the panel below."),
            )
    with cols[-1]:
        st.button("See full rankings →", key="rs_to_engine", width="stretch",
                  on_click=_goto_engine,
                  help="Every horizon, objective, risk band, sector and cap "
                       "filter, with the scoring SQL and a full metric breakdown")

    ui.note(
        "0–100 composites of each stock's percentile ranks on the metrics that "
        "matter over a medium-term horizon, computed in SQL from reported "
        "financials and price history — never by a model. A position within the "
        f"ranked universe, not a forecast, and not advice. The line behind each "
        f"name is that company's last {PICK_SPARK_SESSIONS} closes."
    )


def _goto_engine() -> None:
    """Jump to the Intelligence page. A callback, so it runs before the nav radio
    is rebuilt -- writing `section` after that widget exists would raise."""
    st.session_state["section"] = "Intelligence"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def render(ctx: Ctx) -> None:
    """Reading order, which is the whole point of this page.

    Search and the company it finds must be adjacent -- NOTHING goes between
    them. Everything that is not the answer to "what did I just search for" sits
    either beside the search box (Ask the Market), above it (Today's
    Opportunities), or is absent until it has something to say (the answer
    panel). The earlier stacking put a second text box, six suggestion buttons
    and four cards between the search and its own result, so finding a company
    meant scrolling past the tools you did not use.

    Opportunities was the last thing still breaking that rule: one row, but a row
    that separated the box you typed into from the panel that answered it, which
    read as though the strip belonged to the search. Above the hero it is what it
    actually is -- a standing offer you can take or scroll past on the way in.
    """
    version = dal.intel_version()

    # Opportunities renders HERE but is built further down, because its active
    # chip depends on the symbol `_resolve` returns and that has not happened
    # yet at this point in the script. A container reserves the slot so the
    # order things are drawn in can differ from the order they are computed in;
    # building it here instead would leave the strip one rerun stale, still
    # marking the previous company as loaded.
    picks_slot = st.container()

    # The hero. One panel holding the greeting and both entry points, rather
    # than two loose inputs on the page background -- a landing page needs a
    # place the eye lands, and on a dark surface an unbounded pair of text boxes
    # is not one. `st.container(key=...)` is the hook: raw HTML cannot wrap real
    # Streamlit widgets, and each widget sits in its own DOM wrapper so a
    # sibling selector never reaches them either.
    with st.container(key="rs_hero"):
        st.markdown(
            f'<div class="hero-greet">{ui.esc(ui.greeting())}<em>.</em></div>'
            f'<div class="hero-sub">Search any US-listed company, or ask the '
            f'market a question — both answered from the same SQL.</div>',
            unsafe_allow_html=True)

        find, question = st.columns([1.35, 1.0])
        with find:
            st.markdown('<div class="rs-lbl">Research a company</div>',
                        unsafe_allow_html=True)
            typed = st.text_input(
                "Search", key="rs_query", label_visibility="collapsed",
                placeholder="Ticker or company name — AAPL, Costco, Rocket Lab…",
            )
            # Resolution renders here, under the box that caused it: a
            # disambiguation list or a "nothing matches" belongs beside the
            # input it is about.
            symbol = _resolve(typed, version)
        with question:
            asked = _ask_input()

    with picks_slot:
        _opportunities(ctx, version)
    _ask_answer(ctx, asked)
    if symbol is None:
        return

    view = ui.sub_nav("Research", VIEWS, default=VIEWS[0])
    if view == "Snapshot":
        snapshot_view(ctx, symbol, version)
    elif view == "History":
        history_view(ctx, symbol, version)
    else:
        journey_view(ctx, symbol)


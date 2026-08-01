"""The Markets page: what the market as a whole is doing.

Three sub-views, one question each -- the index itself, how the sectors and
individual names moved inside the window, and long-run performance over each
symbol's own listed history.

Layout only. Every figure here is a column from a SQL query (see queries.py);
this module computes no metric of its own, the same rule the rest of the app
follows.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import charts
import components as ui
import data_access as dal
import events
from charts import PLOT_CONFIG
from pagectx import Ctx
from universe import INDEX_SYMBOL

VIEWS = ("Index", "Sectors & Movers", "Performance")

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Rolling-return horizons, as SESSION counts (~252 trading days a year). A
# holding-period length, not a date filter: it selects how long each period is,
# while the record swept stays the symbol's full history.
ROLLING_HORIZONS: dict[str, int] = {"1Y": 252, "3Y": 756, "5Y": 1260, "10Y": 2520}


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------
def index_view(ctx: Ctx) -> None:
    pal, preset = ctx.pal, ctx.preset
    START, END = ctx.start, ctx.end

    q = dal.quote(INDEX_SYMBOL)
    ws = dal.window_stats(INDEX_SYMBOL, START, END)
    px = dal.prices(INDEX_SYMBOL, START, END)

    if ws is None or px.empty or q is None:
        ui.empty_state("No index data in the selected window.",
                       "Widen the date range above — the shortest presets can fall "
                       "entirely inside a market closure.", kind="warn")
        return

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
# Sectors & Movers — breadth, sectors, movers
# ---------------------------------------------------------------------------
def sectors_view(ctx: Ctx) -> None:
    pal, preset = ctx.pal, ctx.preset
    START, END = ctx.start, ctx.end

    movers = dal.period_movers(START, END)
    sectors = dal.sector_performance(START, END)

    if movers.empty:
        ui.empty_state("No symbols traded in this window.",
                       "Widen the date range above to cover at least one session.",
                       kind="warn")
        return

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
# Performance — leaderboards over each symbol's own history
# ---------------------------------------------------------------------------
def performance_view(ctx: Ctx) -> None:
    pal = ctx.pal
    END = ctx.end

    lb = dal.leaderboard()
    ui.section("Long-run performance", "Each symbol over its own listed history")
    ui.note(
        "These are all-time figures per symbol, so the periods are NOT equal — a name "
        "listed in 2012 has not had the same run as one listed in 2001. 'Years' and "
        "'From' are shown so the comparison stays honest. Use Sectors & Movers for "
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
        ui.chart(charts.seasonality_heatmap(piv, pal, MONTHS), key="perf_season",
                 config=PLOT_CONFIG, controls=False)
        ui.table_view("Monthly returns — table view",
                      piv.style.format("{:+.2%}", na_rep="—"), hide_index=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
RENDERERS = {
    "Index": index_view,
    "Sectors & Movers": sectors_view,
    "Performance": performance_view,
}

# Which sub-views read the shared date window. Performance is all-time by
# construction -- each symbol over its own listed history, calendar-year returns,
# every rolling holding period in the record -- so the window control is hidden
# there rather than displayed above figures it does not govern.
WINDOWED = ("Index", "Sectors & Movers")


def render(ctx: Ctx) -> None:
    view = ui.sub_nav("Markets", VIEWS, default=VIEWS[0])
    RENDERERS[view](ctx)

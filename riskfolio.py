"""The Risk & Portfolio page: how names relate, and what a basket of them does.

Two sub-views. **Risk** is deliberately cross-sectional -- it asks how a set of
companies move relative to each other, and how the whole universe trades off
volatility against return. It carries no company picker: the single-company
drawdown and rolling-volatility curves it used to duplicate live on
Research → History, driven by the same queries, and one screen showing them
behind a second picker was the app's only true duplication.

**Portfolio** builds a weighted basket and reports what it would have done.

Layout only. Every figure is a column from a SQL query (see queries.py).
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import charts
import components as ui
import data_access as dal
import events
from charts import PLOT_CONFIG
from pagectx import Ctx
from universe import INDEX_SYMBOL

VIEWS = ("Risk", "Portfolio")

# Opening set for the correlation matrix: four names from four different sectors,
# so the first render shows a range of coefficients rather than one cluster near
# 1.0 that makes the chart look broken.
CORR_DEFAULT = ("AAPL", "JPM", "XOM", "JNJ")


# ---------------------------------------------------------------------------
# Risk — cross-sectional only
# ---------------------------------------------------------------------------
def risk_view(ctx: Ctx) -> None:
    pal, preset, directory = ctx.pal, ctx.preset, ctx.directory
    START, END = ctx.start, ctx.end

    ui.section("How these move together", f"Daily-return correlation · {preset}")
    known = set(directory["symbol"])
    ui.multiselect_guard("risk_corr_syms", known)
    corr_syms = st.multiselect(
        "Companies", list(directory["symbol"]),
        default=[s for s in CORR_DEFAULT if s in known],
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
            "because there are only ~49 and they are the identity channel. For one "
            "company's own drawdown and rolling-volatility curves, see "
            "Research → History."
        )


# ---------------------------------------------------------------------------
# Portfolio — build a weighted basket and see what it would have done
# ---------------------------------------------------------------------------
def portfolio_view(ctx: Ctx) -> None:
    pal, directory = ctx.pal, ctx.directory
    START, END = ctx.start, ctx.end

    ui.section("Portfolio simulator", "Build a basket and see how it would have performed")
    ui.note(
        "Pick holdings and set their weights. Every figure is computed from the "
        "actual daily history of those companies over the selected window."
    )

    all_syms = list(directory["symbol"])
    name_of = dict(zip(directory["symbol"], directory["name"]))
    default = [s for s in ("AAPL", "MSFT", "NVDA", "AMZN") if s in all_syms]

    ui.multiselect_guard("pf_syms", set(all_syms))
    holdings = st.multiselect(
        "Holdings", all_syms, default=st.session_state.get("pf_syms", default),
        key="pf_syms", max_selections=8,
        format_func=lambda s: f"{s} — {name_of.get(s, s)}",
        help="Up to 8 holdings",
    )

    if not holdings:
        ui.empty_state("No holdings selected.",
                       "Pick up to eight companies above to build a basket.")
        return

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
        return

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
        return

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
        {"icon": "〰", "label": "Volatility",
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
    ui.section("Portfolio vs. the S&P 500",
               f"Both from {pd.to_datetime(pser['date'].iloc[0]):%b %d, %Y}")
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
# Entry point
# ---------------------------------------------------------------------------
RENDERERS = {"Risk": risk_view, "Portfolio": portfolio_view}

# Both sub-views read the shared date window.
WINDOWED = VIEWS


def render(ctx: Ctx) -> None:
    view = ui.sub_nav("Risk & Portfolio", VIEWS, default=VIEWS[0])
    RENDERERS[view](ctx)

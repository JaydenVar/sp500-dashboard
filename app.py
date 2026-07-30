"""S&P 500 — 25-Year Dashboard.

Every number on this page comes from a SQL query (see queries.py) run against a
local SQLite database (see build_db.py) built from real Yahoo Finance history
(see fetch_data.py). Streamlit + Plotly only render what SQL already computed.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import build_db
import queries
from db import DB_PATH, get_connection
from theme import PALETTES, inject_css

st.set_page_config(page_title="S&P 500 — 25-Year Dashboard", layout="wide")


@st.cache_resource
def ensure_db() -> str:
    """Build the DB from the committed CSV if it isn't there yet.

    The .db is a build artifact and isn't in version control, so a fresh deploy
    starts without one. Cached so the build runs once per server, not per rerun.
    Only the build is cached — a sqlite3 connection can't be shared across
    Streamlit's threads, so each script run opens its own (cheap, and read-only).
    """
    if not DB_PATH.exists():
        build_db.main()
    return str(DB_PATH)


ensure_db()
conn = get_connection()

# ---------------------------------------------------------------------------
# Top control row: theme + date range. Filters scope everything below them.
# ---------------------------------------------------------------------------
ctrl_left, ctrl_right = st.columns([3, 1])
with ctrl_right:
    mode_name = st.radio("Theme", list(PALETTES.keys()), horizontal=True, label_visibility="collapsed")
pal = PALETTES[mode_name]
st.markdown(inject_css(pal), unsafe_allow_html=True)

bounds = conn.execute(queries.DATE_BOUNDS).fetchone()
min_date = dt.date.fromisoformat(bounds[0])
max_date = dt.date.fromisoformat(bounds[1])

PRESETS = {
    "YTD": dt.date(max_date.year, 1, 1),
    "1Y": max_date - dt.timedelta(days=365),
    "5Y": max_date - dt.timedelta(days=365 * 5),
    "10Y": max_date - dt.timedelta(days=365 * 10),
    "25Y / All": min_date,
}

with ctrl_left:
    preset = st.radio("Date range", list(PRESETS.keys()), index=4, horizontal=True)

start_date = max(PRESETS[preset], min_date)
end_date = max_date

st.title("S&P 500 — 25-Year Dashboard")
st.markdown(
    f"<span class='sp500-caption'>Data: {min_date.isoformat()} to {max_date.isoformat()} "
    f"&middot; showing {start_date.isoformat()} to {end_date.isoformat()} &middot; "
    "all figures computed in SQLite via SQL views (see the Data &amp; SQL tab)</span>",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Data pulls
# ---------------------------------------------------------------------------
prices = pd.read_sql(queries.PRICES_IN_RANGE, conn, params={"start": start_date.isoformat(), "end": end_date.isoformat()})
drawdowns = pd.read_sql(queries.DRAWDOWN_IN_RANGE, conn, params={"start": start_date.isoformat(), "end": end_date.isoformat()})
volatility = pd.read_sql(queries.VOLATILITY_IN_RANGE, conn, params={"start": start_date.isoformat(), "end": end_date.isoformat()})
yearly = pd.read_sql(queries.YEARLY_SUMMARY, conn)
monthly = pd.read_sql(queries.MONTHLY_RETURNS, conn)
headline = conn.execute(queries.HEADLINE_STATS).fetchone()
ytd = conn.execute(queries.YTD_RETURN).fetchone()

(
    first_date, last_date, first_close, last_close, trading_days,
    all_time_high, current_drawdown, max_drawdown, ann_vol_full, cagr,
) = headline
ytd_return = ytd[2]

for df in (prices, drawdowns, volatility):
    df["date"] = pd.to_datetime(df["date"])

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def wash(hexcolor: str, alpha: float = 0.10) -> str:
    """Series hue at ~10% opacity for an area fill — a wash, never a saturated block."""
    h = hexcolor.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def color_key(items: list[tuple[str, str]]) -> None:
    """items: list of (label, hex). Renders swatch + ink-colored text, never colored text."""
    spans = "".join(
        f"<span style='display:inline-flex;align-items:center;margin-right:16px;'>"
        f"<span style='width:10px;height:10px;border-radius:2px;background:{hexcolor};"
        f"display:inline-block;margin-right:6px;'></span>"
        f"<span style='color:{pal['text_secondary']};font-size:0.85rem;'>{label}</span></span>"
        for label, hexcolor in items
    )
    st.markdown(f"<div style='margin-top:-8px;margin-bottom:8px;'>{spans}</div>", unsafe_allow_html=True)


def base_layout(fig: go.Figure, y_title: str = "") -> go.Figure:
    fig.update_layout(
        template=pal["plotly_template"],
        paper_bgcolor=pal["surface"],
        plot_bgcolor=pal["surface"],
        font=dict(color=pal["text_primary"], family="system-ui, -apple-system, 'Segoe UI', sans-serif"),
        margin=dict(l=10, r=10, t=10, b=10),
        hoverlabel=dict(bgcolor=pal["surface"], font_color=pal["text_primary"], bordercolor=pal["border"]),
        showlegend=False,
    )
    fig.update_xaxes(showgrid=False, showline=True, linecolor=pal["baseline"], linewidth=1, tickfont=dict(color=pal["muted"]))
    fig.update_yaxes(
        title=y_title,
        showgrid=True, gridcolor=pal["gridline"], gridwidth=1, griddash="solid",
        zeroline=False, tickfont=dict(color=pal["muted"]), title_font=dict(color=pal["muted"]),
    )
    return fig


# ---------------------------------------------------------------------------
# KPI row (stat tiles) — deltas use status ink, never as a fill on the mark
# ---------------------------------------------------------------------------
k1, k2, k3, k4, k5, k6 = st.columns(6)
delta_color = pal["good_text"] if ytd_return >= 0 else pal["bad_text"]
k1.metric("Current close", f"{last_close:,.0f}", f"{fmt_pct(ytd_return)} YTD")
k2.metric("25Y CAGR", fmt_pct(cagr))
k3.metric("All-time high", f"{all_time_high:,.0f}")
k4.metric("Current drawdown", fmt_pct(current_drawdown))
k5.metric("Max drawdown (25Y)", fmt_pct(max_drawdown))
k6.metric("Ann. volatility (21d)", f"{volatility['ann_volatility_21d'].iloc[-1] * 100:.1f}%" if not volatility.empty else "—")

st.divider()

tab_overview, tab_returns, tab_risk, tab_data = st.tabs(["Overview", "Returns", "Risk", "Data & SQL"])

# ---------------------------------------------------------------------------
# Overview: price trend
# ---------------------------------------------------------------------------
with tab_overview:
    scale = st.radio("Scale", ["Linear", "Log"], horizontal=True)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=prices["date"], y=prices["close"], mode="lines",
            line=dict(color=pal["blue"], width=2, shape="linear"),
            hovertemplate="%{y:,.0f}<extra></extra>",
            name="S&P 500",
        )
    )
    fig.add_annotation(
        x=prices["date"].iloc[-1], y=prices["close"].iloc[-1],
        text=f"{prices['close'].iloc[-1]:,.0f}", showarrow=False,
        xanchor="left", font=dict(color=pal["text_primary"], size=13), xshift=8,
    )
    if scale == "Log":
        fig.update_yaxes(type="log")
    fig.update_layout(hovermode="x unified")
    fig = base_layout(fig, "Index level")
    # A line needs no zero baseline (only bars do). Padding the data range keeps
    # short ranges legible instead of flattening them against a 0 axis.
    if scale == "Linear":
        span = prices["close"].max() - prices["close"].min()
        pad = span * 0.08 if span else 1
        fig.update_yaxes(range=[prices["close"].min() - pad, prices["close"].max() + pad])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    vol_fig = go.Figure()
    vol_fig.add_trace(
        go.Bar(x=prices["date"], y=prices["volume"], marker_color=pal["blue"], marker_line_width=0,
               hovertemplate="%{y:,.0f}<extra></extra>")
    )
    vol_fig = base_layout(vol_fig, "Volume")
    vol_fig.update_layout(height=180)
    st.caption("Daily trading volume")
    st.plotly_chart(vol_fig, use_container_width=True, config={"displayModeBar": False})

# ---------------------------------------------------------------------------
# Returns: yearly diverging bar + monthly seasonality heatmap
# ---------------------------------------------------------------------------
with tab_returns:
    st.subheader("Calendar-year return")
    y = yearly.copy()
    y["partial"] = y["is_partial"] == 1
    y["color"] = y["year_return"].apply(lambda v: pal["blue"] if v >= 0 else pal["red"])
    # Partial years aren't calendar-year returns, so they must not read as one.
    y["opacity"] = y["partial"].map({True: 0.45, False: 1.0})
    y["tick"] = y.apply(lambda r: f"{r['year']}*" if r["partial"] else r["year"], axis=1)

    key_items = [("Positive year", pal["blue"]), ("Negative year", pal["red"])]
    color_key(key_items)
    partial_years = y.loc[y["partial"], "year"].tolist()
    if partial_years:
        st.markdown(
            f"<div class='sp500-caption' style='margin-top:-4px;margin-bottom:8px;'>"
            f"* {', '.join(partial_years)} shown faded — the dataset covers only part of "
            f"these years, so they are period returns, not full calendar years.</div>",
            unsafe_allow_html=True,
        )

    # Label only the extremes, and only among full years. These ride the bars as
    # trace text: add_annotation() with a string x collapses a category axis's
    # range in this Plotly version.
    y["label"] = ""
    full = y[~y["partial"]]
    for idx in (full["year_return"].idxmax(), full["year_return"].idxmin()):
        y.loc[idx, "label"] = fmt_pct(y.loc[idx, "year_return"])

    bar_fig = go.Figure()
    bar_fig.add_trace(
        go.Bar(
            x=y["tick"], y=y["year_return"], marker_color=y["color"], marker_line_width=0,
            marker_opacity=y["opacity"],
            text=y["label"], textposition="outside", cliponaxis=False,
            textfont=dict(color=pal["text_primary"], size=12),
            hovertemplate="%{x}: %{y:.1%}<extra></extra>",
        )
    )
    bar_fig.add_hline(y=0, line_color=pal["baseline"], line_width=1)
    bar_fig = base_layout(bar_fig, "Return")
    bar_fig.update_xaxes(type="category", tickangle=-45)
    bar_fig.update_yaxes(tickformat=".0%")
    bar_fig.update_layout(bargap=0.25)
    st.plotly_chart(bar_fig, use_container_width=True, config={"displayModeBar": False})

    st.subheader("Monthly seasonality")
    pivot = monthly.pivot(index="year", columns="month", values="month_return")
    pivot = pivot.reindex(columns=[f"{m:02d}" for m in range(1, 13)])
    max_abs = float(pivot.abs().max().max())
    heat_fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=MONTH_NAMES,
            y=pivot.index,
            colorscale=[[0.0, pal["red"]], [0.5, pal["neutral_mid"]], [1.0, pal["blue"]]],
            zmid=0, zmin=-max_abs, zmax=max_abs,
            hovertemplate="%{y} %{x}: %{z:.1%}<extra></extra>",
            colorbar=dict(title="Return", tickformat=".0%", outlinewidth=0, tickfont=dict(color=pal["muted"])),
            xgap=2, ygap=2,
        )
    )
    heat_fig.update_layout(
        template=pal["plotly_template"], paper_bgcolor=pal["surface"], plot_bgcolor=pal["surface"],
        font=dict(color=pal["text_primary"], family="system-ui, -apple-system, 'Segoe UI', sans-serif"),
        margin=dict(l=10, r=10, t=10, b=10),
        hoverlabel=dict(bgcolor=pal["surface"], font_color=pal["text_primary"], bordercolor=pal["border"]),
    )
    heat_fig.update_xaxes(showgrid=False, tickfont=dict(color=pal["muted"]))
    heat_fig.update_yaxes(showgrid=False, autorange="reversed", tickfont=dict(color=pal["muted"]))
    st.plotly_chart(heat_fig, use_container_width=True, config={"displayModeBar": False})

    with st.expander("Monthly returns — table view"):
        st.dataframe(pivot.style.format("{:+.1%}", na_rep="—"), use_container_width=True)

# ---------------------------------------------------------------------------
# Risk: drawdown underwater chart + rolling volatility
# ---------------------------------------------------------------------------
with tab_risk:
    st.subheader("Drawdown from all-time high")
    dd_fig = go.Figure()
    dd_fig.add_trace(
        go.Scatter(
            x=drawdowns["date"], y=drawdowns["drawdown"], mode="lines",
            line=dict(color=pal["red"], width=2), fill="tozeroy", fillcolor=wash(pal["red"]),
            hovertemplate="%{y:.1%}<extra></extra>",
        )
    )
    worst_row = drawdowns.loc[drawdowns["drawdown"].idxmin()]
    dd_fig.add_annotation(
        x=worst_row["date"], y=worst_row["drawdown"],
        text=f"{worst_row['drawdown']*100:.1f}% ({worst_row['date'].date()})",
        showarrow=True, arrowhead=0, ax=0, ay=-24,
        font=dict(color=pal["text_primary"], size=12),
    )
    dd_fig.add_hline(y=0, line_color=pal["baseline"], line_width=1)
    dd_fig.update_layout(hovermode="x unified")
    dd_fig = base_layout(dd_fig, "Drawdown")
    dd_fig.update_yaxes(tickformat=".0%")
    st.plotly_chart(dd_fig, use_container_width=True, config={"displayModeBar": False})

    st.subheader("Rolling volatility (21-trading-day, annualized)")
    vol_fig2 = go.Figure()
    vol_fig2.add_trace(
        go.Scatter(
            x=volatility["date"], y=volatility["ann_volatility_21d"], mode="lines",
            line=dict(color=pal["blue"], width=2),
            hovertemplate="%{y:.1%}<extra></extra>",
        )
    )
    if not volatility.empty:
        vol_fig2.add_annotation(
            x=volatility["date"].iloc[-1], y=volatility["ann_volatility_21d"].iloc[-1],
            text=f"{volatility['ann_volatility_21d'].iloc[-1]*100:.1f}%",
            showarrow=False, xanchor="left", xshift=8, font=dict(color=pal["text_primary"], size=13),
        )
    vol_fig2.update_layout(hovermode="x unified")
    vol_fig2 = base_layout(vol_fig2, "Ann. volatility")
    vol_fig2.update_yaxes(tickformat=".0%")
    st.plotly_chart(vol_fig2, use_container_width=True, config={"displayModeBar": False})

# ---------------------------------------------------------------------------
# Data & SQL: table view + transparency into the queries
# ---------------------------------------------------------------------------
with tab_data:
    st.subheader("Yearly summary — table view")
    display_yearly = yearly.copy()
    display_yearly["partial year"] = display_yearly["is_partial"].map({1: "yes", 0: ""})
    display_yearly = display_yearly.drop(columns=["is_partial"])
    display_yearly["year_return"] = display_yearly["year_return"].map(lambda v: f"{v*100:+.1f}%")
    display_yearly["avg_volume"] = display_yearly["avg_volume"].map(lambda v: f"{v:,.0f}")
    for col in ("open_close", "close_close", "year_high", "year_low"):
        display_yearly[col] = display_yearly[col].map(lambda v: f"{v:,.1f}")
    st.dataframe(display_yearly, use_container_width=True, hide_index=True)

    st.download_button(
        "Download raw daily prices (CSV)",
        data=pd.read_sql(queries.PRICES_IN_RANGE, conn, params={"start": min_date.isoformat(), "end": max_date.isoformat()}).to_csv(index=False),
        file_name="sp500_daily.csv",
        mime="text/csv",
    )

    st.subheader("SQL behind this dashboard")
    st.caption("Every stat and series above is the direct output of one of these queries/views against data/sp500.db.")
    for name, sql in [
        ("yearly_summary (view)", queries.YEARLY_SUMMARY),
        ("monthly_returns (view)", queries.MONTHLY_RETURNS),
        ("drawdowns (view)", queries.DRAWDOWN_IN_RANGE),
        ("rolling_volatility (view)", queries.VOLATILITY_IN_RANGE),
        ("headline stats (CAGR, max drawdown, ann. vol)", queries.HEADLINE_STATS),
    ]:
        with st.expander(name):
            st.code(sql, language="sql")

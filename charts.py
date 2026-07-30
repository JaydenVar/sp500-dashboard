"""Chart builders and the shared Plotly configuration.

One place owns axis/grid/legend/hover styling so every chart in the app reads as
the same system. Mark specs follow the house rules: 2px lines, ~10% area washes,
hairline solid gridlines, recessive axes, selective direct labels, and text in
ink tokens rather than the series color.
"""

from __future__ import annotations

import plotly.graph_objects as go

# Modebar: zoom, pan, box/lasso-free selection removed, image download kept.
# `Reset View` is also offered as a real button in the UI, because the modebar
# is discoverable only on hover.
PLOT_CONFIG = {
    "displayModeBar": True,
    "displaylogo": False,
    "scrollZoom": False,  # would hijack page scrolling
    # Required, not cosmetic: charts inside a Streamlit tab that is not the active
    # one at first paint get measured while hidden and render at a fraction of the
    # container width. `responsive` makes Plotly re-measure when it becomes visible.
    "responsive": True,
    "modeBarButtonsToRemove": ["select2d", "lasso2d", "toggleSpikelines"],
    "toImageButtonOptions": {"format": "png", "scale": 2, "filename": "chart"},
}


def wash(hex_color: str, alpha: float = 0.10) -> str:
    """A series hue as a translucent area fill -- a wash, never a saturated block."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def style(fig: go.Figure, pal: dict, *, y_title: str = "", height: int | None = None,
          crosshair: bool = True, legend: bool = False, y_tickformat: str | None = None,
          zero_line: bool = False) -> go.Figure:
    """Apply the house chart styling. Called by every builder below."""
    fig.update_layout(
        template=pal["plotly_template"],
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=pal["text_primary"], family='system-ui, -apple-system, "Segoe UI", sans-serif', size=12),
        margin=dict(l=8, r=8, t=8, b=8),
        hovermode="x unified" if crosshair else "closest",
        hoverlabel=dict(
            bgcolor=pal["surface"],
            bordercolor=pal["border_strong"],
            font=dict(color=pal["text_primary"], size=12,
                      family='system-ui, -apple-system, "Segoe UI", sans-serif'),
            align="left",
        ),
        showlegend=legend,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0)", borderwidth=0,
            font=dict(color=pal["text_secondary"], size=11.5),
        ),
        dragmode="pan",
        transition=dict(duration=0),
        autosize=True,
    )
    if height:
        fig.update_layout(height=height)

    fig.update_xaxes(
        showgrid=False,
        showline=True, linecolor=pal["baseline"], linewidth=1,
        tickfont=dict(color=pal["muted"], size=11),
        # The crosshair: readers aim at a date, not at a 2px line.
        showspikes=crosshair, spikemode="across", spikethickness=1,
        spikecolor=pal["muted"], spikedash="solid",
        rangeslider=dict(visible=False),
    )
    fig.update_yaxes(
        title=dict(text=y_title, font=dict(color=pal["muted"], size=11.5)) if y_title else None,
        showgrid=True, gridcolor=pal["gridline"], gridwidth=1, griddash="solid",
        zeroline=zero_line, zerolinecolor=pal["baseline"], zerolinewidth=1,
        showline=False,
        tickfont=dict(color=pal["muted"], size=11),
    )
    if y_tickformat:
        fig.update_yaxes(tickformat=y_tickformat)
    return fig


def end_label(fig: go.Figure, x, y, text: str, pal: dict) -> None:
    """Label the final point of a line -- selective labelling, in ink not series color."""
    fig.add_annotation(
        x=x, y=y, text=text, showarrow=False, xanchor="left", xshift=7,
        font=dict(color=pal["text_primary"], size=12),
    )


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def price_line(df, pal: dict, *, log: bool = False, label: str | None = None,
               height: int = 380, color: str | None = None) -> go.Figure:
    color = color or pal["series"][0]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["close"], mode="lines", name="Close",
        line=dict(color=color, width=2, shape="linear"),
        fill="tozeroy", fillcolor=wash(color, 0.08),
        hovertemplate="<b>%{y:,.2f}</b><extra></extra>",
    ))
    fig = style(fig, pal, y_title="Price", height=height)
    if log:
        fig.update_yaxes(type="log")
    else:
        # A line needs no zero baseline; padding keeps short windows legible.
        lo, hi = float(df["close"].min()), float(df["close"].max())
        pad = (hi - lo) * 0.10 or max(hi * 0.01, 1)
        fig.update_yaxes(range=[lo - pad, hi + pad])
    if label and len(df):
        end_label(fig, df["date"].iloc[-1], df["close"].iloc[-1], label, pal)
    return fig


def candlestick(df, pal: dict, *, height: int = 380) -> go.Figure:
    fig = go.Figure(go.Candlestick(
        x=df["date"], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        increasing=dict(line=dict(color=pal["up"], width=1), fillcolor=pal["up"]),
        decreasing=dict(line=dict(color=pal["down"], width=1), fillcolor=pal["down"]),
        name="OHLC", showlegend=False,
    ))
    fig = style(fig, pal, y_title="Price", height=height, crosshair=False)
    fig.update_layout(xaxis_rangeslider_visible=False)
    return fig


def volume_bars(df, pal: dict, *, height: int = 150) -> go.Figure:
    fig = go.Figure(go.Bar(
        x=df["date"], y=df["volume"], name="Volume",
        marker_color=pal["series"][0], marker_line_width=0, opacity=0.75,
        hovertemplate="<b>%{y:,.0f}</b><extra></extra>",
    ))
    return style(fig, pal, y_title="Volume", height=height)


def moving_average_chart(df, pal: dict, *, height: int = 380) -> go.Figure:
    """Close with 50/200-session means. Three series -> legend is mandatory."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["close"], mode="lines", name="Close",
        line=dict(color=pal["series"][0], width=2),
        hovertemplate="<b>%{y:,.2f}</b><extra>Close</extra>",
    ))
    for col, name, slot in (("ma_50", "50-session MA", 1), ("ma_200", "200-session MA", 6)):
        if col in df.columns and df[col].notna().any():
            fig.add_trace(go.Scatter(
                x=df["date"], y=df[col], mode="lines", name=name,
                line=dict(color=pal["series"][slot], width=2),
                connectgaps=False,
                hovertemplate="<b>%{y:,.2f}</b><extra>" + name + "</extra>",
            ))
    return style(fig, pal, y_title="Price", height=height, legend=True)


def comparison_needs_log(pivot, threshold: float = 20.0) -> bool:
    """True when the series' end values span too wide a range for a linear axis.

    Over long windows one big compounder can end 40x above the others, which on a
    linear axis squashes every other series flat onto the baseline. A log axis
    gives equal vertical space to equal *ratios*, so all series stay readable --
    and it is still ONE axis, unlike the dual-scale hack.
    """
    if pivot is None or pivot.empty or pivot.shape[1] < 2:
        return False
    finals = pivot.ffill().iloc[-1].abs()
    finals = finals[finals > 0]
    if len(finals) < 2:
        return False
    return float(finals.max() / finals.min()) > threshold


def indexed_comparison(pivot, pal: dict, *, height: int = 420, log: bool = False) -> go.Figure:
    """Multiple symbols rebased to 100 -- ONE axis, never a second y-scale.

    Series take categorical slots in fixed order so a symbol keeps its color when
    the selection changes; color follows the entity, not its rank.
    """
    fig = go.Figure()
    for i, col in enumerate(pivot.columns):
        s = pivot[col].dropna()
        fig.add_trace(go.Scatter(
            x=s.index, y=s.values, mode="lines", name=str(col),
            line=dict(color=pal["series"][i % len(pal["series"])], width=2),
            hovertemplate="<b>%{y:,.1f}</b><extra>" + str(col) + "</extra>",
        ))
    fig = style(fig, pal, y_title="Indexed (start = 100)", height=height, legend=True)
    fig.add_hline(y=100, line_color=pal["baseline"], line_width=1)
    if log:
        fig.update_yaxes(type="log")
    return fig


def returns_bars(df, pal: dict, *, height: int = 260) -> go.Figure:
    """Daily returns, colored by sign -- diverging polarity, not identity."""
    colors = [pal["up"] if v >= 0 else pal["down"] for v in df["daily_return"]]
    fig = go.Figure(go.Bar(
        x=df["date"], y=df["daily_return"], marker_color=colors, marker_line_width=0,
        hovertemplate="<b>%{y:.2%}</b><extra></extra>", name="Daily return",
    ))
    return style(fig, pal, y_title="Daily return", height=height,
                 y_tickformat=".1%", zero_line=True)


def area_series(df, value_col, pal: dict, *, color_key: str = "blue", y_title: str = "",
                height: int = 300, tickformat: str = ".0%", label: str | None = None,
                zero_line: bool = True) -> go.Figure:
    color = pal[color_key] if color_key in pal else color_key
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df[value_col], mode="lines", name=y_title or value_col,
        line=dict(color=color, width=2), fill="tozeroy", fillcolor=wash(color, 0.10),
        hovertemplate="<b>%{y:" + tickformat + "}</b><extra></extra>",
    ))
    fig = style(fig, pal, y_title=y_title, height=height,
                y_tickformat=tickformat, zero_line=zero_line)
    if label and len(df):
        end_label(fig, df["date"].iloc[-1], df[value_col].iloc[-1], label, pal)
    return fig


def yearly_return_bars(y, pal: dict, *, height: int = 340) -> go.Figure:
    """Calendar-year returns. Partial years are faded and asterisked, because a
    part-year figure is not a calendar-year return."""
    colors = [pal["up"] if v >= 0 else pal["down"] for v in y["year_return"]]
    opac = [0.45 if p else 1.0 for p in y["partial"]]
    fig = go.Figure(go.Bar(
        x=y["tick"], y=y["year_return"], marker_color=colors, marker_opacity=opac,
        marker_line_width=0, text=y["label"], textposition="outside", cliponaxis=False,
        textfont=dict(color=pal["text_primary"], size=11),
        hovertemplate="<b>%{y:.1%}</b><extra>%{x}</extra>", name="Year return",
    ))
    fig = style(fig, pal, y_title="Return", height=height, crosshair=False,
                y_tickformat=".0%")
    fig.add_hline(y=0, line_color=pal["baseline"], line_width=1)
    fig.update_xaxes(type="category", tickangle=-45)
    fig.update_layout(bargap=0.28)
    return fig


def seasonality_heatmap(pivot, pal: dict, month_names, *, height: int = 460) -> go.Figure:
    """Diverging heatmap: two opposite hues with a NEUTRAL midpoint (never a hue
    at zero), symmetric range so equal magnitudes read equally either side."""
    vmax = float(pivot.abs().max().max() or 0.01)
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=month_names, y=pivot.index,
        colorscale=[[0.0, pal["red"]], [0.5, pal["neutral_mid"]], [1.0, pal["blue"]]],
        zmid=0, zmin=-vmax, zmax=vmax, xgap=2, ygap=2,
        hovertemplate="<b>%{z:.1%}</b><extra>%{y} %{x}</extra>",
        colorbar=dict(
            title=dict(text="Return", font=dict(color=pal["muted"], size=11)),
            tickformat=".0%", outlinewidth=0, thickness=11,
            tickfont=dict(color=pal["muted"], size=10.5),
        ),
    ))
    fig = style(fig, pal, height=height, crosshair=False)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(showline=False)
    return fig


def sector_bars(df, pal: dict, *, height: int = 340) -> go.Figure:
    """Median member return per sector. Median rather than mean because a mean of
    long-window total returns is dominated by a single outlier."""
    d = df.sort_values("median_return")
    colors = [pal["up"] if v >= 0 else pal["down"] for v in d["median_return"]]
    fig = go.Figure(go.Bar(
        x=d["median_return"], y=d["sector"], orientation="h",
        marker_color=colors, marker_line_width=0,
        text=[f"{v:+.1%}" for v in d["median_return"]], textposition="outside",
        textfont=dict(color=pal["text_primary"], size=11), cliponaxis=False,
        customdata=d[["n_symbols", "avg_return", "best_return", "worst_return"]].values,
        hovertemplate=(
            "<b>%{x:.1%}</b> median<br>"
            "%{customdata[0]} symbols · mean %{customdata[1]:.1%}<br>"
            "best %{customdata[2]:.1%} · worst %{customdata[3]:.1%}"
            "<extra>%{y}</extra>"
        ),
        name="Sector",
    ))
    fig = style(fig, pal, height=height, crosshair=False)
    fig.update_xaxes(tickformat=".0%", showgrid=True, gridcolor=pal["gridline"], showline=False)
    fig.update_yaxes(showgrid=False, tickfont=dict(color=pal["text_secondary"], size=11.5))
    fig.add_vline(x=0, line_color=pal["baseline"], line_width=1)
    return fig

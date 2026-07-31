"""Chart builders and the shared Plotly configuration.

One place owns axis/grid/legend/hover styling so every chart in the app reads as
the same system. Mark specs follow the house rules: 2px lines, ~10% area washes,
hairline solid gridlines, recessive axes, selective direct labels, and text in
ink tokens rather than the series color.
"""

from __future__ import annotations

import datetime as dt

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

# Chart heights, as a four-step scale rather than a number chosen per call site.
# Every builder's default is one of these, so charts sitting side by side or
# stacked down a page align instead of missing each other by 20px. The step
# encodes the chart's ROLE, which is why they are named for it:
#
#   STRIP   a companion band under a primary chart (volume beneath price)
#   COMPACT a secondary reading — one series, no legend
#   PRIMARY the chart a section exists to show
#   TALL    needs vertical room to stay legible: a matrix, a scatter of ~49
#           labelled points, a category axis with a dozen rows
H_STRIP, H_COMPACT, H_PRIMARY, H_TALL = 150, 300, 380, 460


def add_events(fig: go.Figure, pal: dict, evts, *, y_domain: tuple[float, float] = (0, 1),
               label: bool = True) -> go.Figure:
    """Mark market events on a time axis.

    Drawn as paper-referenced vertical lines so they don't disturb the y-range,
    plus an invisible scatter carrying the hover text -- a shape alone can't have
    a tooltip, and the explanation is the point of the marker.
    """
    if evts is None or len(evts) == 0:
        return fig

    # Events cluster (2020 has a peak and a bottom five weeks apart), and rotated
    # labels sitting at one height overlap into an unreadable smear. Labels are
    # therefore tiered: anything too close to the previously labelled event drops
    # to the next tier down. The diamonds and hover text stay put regardless.
    ordinals = [dt.date.fromisoformat(e[0]).toordinal() for e in evts]
    span = max(ordinals) - min(ordinals) or 1
    TIERS = (1.0, 0.80, 0.60)
    MIN_GAP = 0.055  # fraction of the window below which labels would collide

    xs, texts, colors = [], [], []
    tier, last_labeled = 0, None
    for (date, short, cat, note), o in zip(evts, ordinals):
        color = pal["down"] if cat == "crisis" else pal["up"] if cat == "recovery" else pal["muted"]
        fig.add_vline(x=date, line_width=1, line_dash="dot", line_color=color, opacity=0.5)

        if label:
            if last_labeled is not None and (o - last_labeled) / span < MIN_GAP:
                tier = (tier + 1) % len(TIERS)
            else:
                tier = 0
            last_labeled = o
            fig.add_annotation(
                x=date, y=TIERS[tier], yref="paper", text=short, showarrow=False,
                textangle=-90, xanchor="left", yanchor="top", xshift=3,
                font=dict(color=pal["muted"], size=9), opacity=0.9,
            )

        xs.append(date)
        texts.append(f"<b>{short}</b><br>{date}<br>{_wrap(note, 46)}")
        colors.append(color)

    # The markers ride an invisible secondary axis fixed to 0..1, so they always
    # sit at the top of the plot regardless of the data's scale. (A trace cannot
    # use `yref="paper"` -- that exists only on annotations and shapes.)
    fig.add_trace(go.Scatter(
        x=xs, y=[0.985] * len(xs), yaxis="y2",
        mode="markers", marker=dict(size=9, color=colors, symbol="diamond",
                                    line=dict(width=1.5, color=pal["surface"])),
        hovertemplate="%{text}<extra></extra>", text=texts,
        showlegend=False, name="Market event", hoverlabel=dict(align="left"),
    ))
    fig.update_layout(yaxis2=dict(
        overlaying="y", side="right", range=[0, 1],
        showgrid=False, zeroline=False, showticklabels=False,
        fixedrange=True, visible=False,
    ))
    return fig


def _wrap(text: str, width: int) -> str:
    """Soft-wrap hover text; Plotly tooltips don't wrap on their own."""
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return "<br>".join(out)


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
               height: int = H_PRIMARY, color: str | None = None) -> go.Figure:
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


def candlestick(df, pal: dict, *, height: int = H_PRIMARY) -> go.Figure:
    fig = go.Figure(go.Candlestick(
        x=df["date"], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        increasing=dict(line=dict(color=pal["up"], width=1), fillcolor=pal["up"]),
        decreasing=dict(line=dict(color=pal["down"], width=1), fillcolor=pal["down"]),
        name="OHLC", showlegend=False,
    ))
    fig = style(fig, pal, y_title="Price", height=height, crosshair=False)
    fig.update_layout(xaxis_rangeslider_visible=False)
    return fig


def volume_bars(df, pal: dict, *, height: int = H_STRIP) -> go.Figure:
    fig = go.Figure(go.Bar(
        x=df["date"], y=df["volume"], name="Volume",
        marker_color=pal["series"][0], marker_line_width=0, opacity=0.75,
        hovertemplate="<b>%{y:,.0f}</b><extra></extra>",
    ))
    return style(fig, pal, y_title="Volume", height=height)


def moving_average_chart(df, pal: dict, *, height: int = H_PRIMARY) -> go.Figure:
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


def indexed_comparison(pivot, pal: dict, *, height: int = H_PRIMARY, log: bool = False) -> go.Figure:
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


def returns_bars(df, pal: dict, *, height: int = H_COMPACT) -> go.Figure:
    """Daily returns, colored by sign -- diverging polarity, not identity."""
    colors = [pal["up"] if v >= 0 else pal["down"] for v in df["daily_return"]]
    fig = go.Figure(go.Bar(
        x=df["date"], y=df["daily_return"], marker_color=colors, marker_line_width=0,
        hovertemplate="<b>%{y:.2%}</b><extra></extra>", name="Daily return",
    ))
    return style(fig, pal, y_title="Daily return", height=height,
                 y_tickformat=".1%", zero_line=True)


def area_series(df, value_col, pal: dict, *, color_key: str = "blue", y_title: str = "",
                height: int = H_COMPACT, tickformat: str = ".0%", label: str | None = None,
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


def yearly_return_bars(y, pal: dict, *, height: int = H_PRIMARY) -> go.Figure:
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


def seasonality_heatmap(pivot, pal: dict, month_names, *, height: int = H_TALL) -> go.Figure:
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


def sector_bars(df, pal: dict, *, height: int = H_TALL) -> go.Figure:
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


def correlation_heatmap(matrix, pal: dict, *, height: int = H_TALL) -> go.Figure:
    """Pairwise correlation as a diverging heatmap on a FIXED -1..1 scale.

    Fixed rather than data-driven, unlike the seasonality map: correlation has
    absolute meaning, and rescaling to the observed range would paint a set of
    names that all move together (0.6-0.8) in the same dramatic spread as a
    genuinely diversified one. The midpoint is neutral, never a hue, so zero
    reads as zero.

    Coefficients are printed in every cell — the color is the pattern, the text
    is the value, and the reading never depends on judging a shade.
    """
    z = matrix.values
    fig = go.Figure(go.Heatmap(
        z=z, x=list(matrix.columns), y=list(matrix.index),
        colorscale=[[0.0, pal["red"]], [0.5, pal["neutral_mid"]], [1.0, pal["blue"]]],
        zmid=0, zmin=-1, zmax=1, xgap=2, ygap=2,
        text=[[("—" if v != v else f"{v:.2f}") for v in row] for row in z],
        texttemplate="%{text}",
        textfont=dict(size=11),
        hovertemplate="<b>%{z:.2f}</b><extra>%{y} vs %{x}</extra>",
        colorbar=dict(
            title=dict(text="Correlation", font=dict(color=pal["muted"], size=11)),
            tickformat=".1f", outlinewidth=0, thickness=11,
            tickvals=[-1, -0.5, 0, 0.5, 1],
            tickfont=dict(color=pal["muted"], size=10.5),
        ),
    ))
    fig = style(fig, pal, height=height, crosshair=False)
    fig.update_yaxes(autorange="reversed", showgrid=False,
                     tickfont=dict(color=pal["text_secondary"], size=11.5))
    fig.update_xaxes(showline=False, side="top",
                     tickfont=dict(color=pal["text_secondary"], size=11.5))
    return fig


def allocation_donut(df, pal: dict, *, label_col: str = "symbol",
                     value_col: str = "weight", height: int = H_PRIMARY) -> go.Figure:
    """Portfolio weights as a donut.

    A pie is the one place a part-to-whole chart earns its keep — the weights
    are a composition that sums to exactly 100% by construction. It stays
    readable because the basket is capped at 8 holdings; beyond roughly that
    many slices, angle comparison fails and this should become a bar chart.

    Every slice carries its ticker and percent as text, so the legend and the
    hue are reinforcement rather than the only way to identify a holding.
    """
    d = df.sort_values(value_col, ascending=False)
    colors = [pal["series"][i % len(pal["series"])] for i in range(len(d))]
    fig = go.Figure(go.Pie(
        labels=d[label_col].astype(str), values=d[value_col],
        hole=0.58, sort=False, direction="clockwise",
        marker=dict(colors=colors, line=dict(color=pal["surface"], width=2)),
        texttemplate="%{label}<br>%{percent:.1%}",
        textposition="outside",
        textfont=dict(color=pal["text_primary"], size=11),
        hovertemplate="<b>%{label}</b><br>%{percent:.1%} of the basket<extra></extra>",
    ))
    fig = style(fig, pal, height=height, crosshair=False)
    fig.update_layout(margin=dict(l=8, r=8, t=28, b=28))
    return fig


def contribution_bars(df, pal: dict, *, height: int = H_PRIMARY) -> go.Figure:
    """Per-holding contribution to the portfolio's total return, in points.

    Direction carries both a color and a signed value label, and the bars are
    ordered by contribution so the reading order is the ranking. Hover adds the
    holding's own return and weight, which is what explains a bar: a large
    weight on a mediocre performer and a small weight on a great one can
    contribute the same amount, and only the hover distinguishes them.
    """
    d = df.dropna(subset=["contribution"]).sort_values("contribution")
    colors = [pal["up"] if v >= 0 else pal["down"] for v in d["contribution"]]
    fig = go.Figure(go.Bar(
        x=d["contribution"], y=d["symbol"].astype(str), orientation="h",
        marker_color=colors, marker_line_width=0,
        text=[f"{v:+.1%}" for v in d["contribution"]], textposition="outside",
        textfont=dict(color=pal["text_primary"], size=11), cliponaxis=False,
        customdata=d[["name", "weight", "holding_return"]].values,
        hovertemplate=(
            "<b>%{x:+.1%}</b> of the portfolio's return<br>"
            "weight %{customdata[1]:.1%} · the holding itself returned "
            "%{customdata[2]:+.1%}"
            "<extra>%{customdata[0]}</extra>"
        ),
        name="Contribution",
    ))
    fig = style(fig, pal, height=height, crosshair=False)
    fig.update_xaxes(tickformat=".0%", showgrid=True, gridcolor=pal["gridline"],
                     showline=False)
    fig.update_yaxes(showgrid=False, tickfont=dict(color=pal["text_secondary"], size=11.5))
    fig.add_vline(x=0, line_color=pal["baseline"], line_width=1)
    return fig


def multi_series(frames: dict, pal: dict, *, value_col: str, y_title: str,
                 height: int = H_PRIMARY, tickformat: str | None = None,
                 zero_line: bool = False, hover_fmt: str = ",.2f") -> go.Figure:
    """Overlay one metric across several symbols on ONE shared axis.

    `frames` maps symbol -> DataFrame with `date` and `value_col`. Colors come
    from the categorical slots in the caller's order, so a symbol keeps its color
    when the selection changes -- identity follows the entity, not its rank.
    """
    fig = go.Figure()
    for i, (sym, df) in enumerate(frames.items()):
        if df is None or df.empty or value_col not in df.columns:
            continue
        s = df.dropna(subset=[value_col])
        fig.add_trace(go.Scatter(
            x=s["date"], y=s[value_col], mode="lines", name=str(sym),
            line=dict(color=pal["series"][i % len(pal["series"])], width=2),
            hovertemplate="<b>%{y:" + hover_fmt + "}</b><extra>" + str(sym) + "</extra>",
        ))
    fig = style(fig, pal, y_title=y_title, height=height, legend=True,
                y_tickformat=tickformat, zero_line=zero_line)
    return fig


def generic_bars(df, pal: dict, *, label_col: str, value_col: str,
                 height: int = H_PRIMARY, tickformat: str | None = None) -> go.Figure:
    """Horizontal bars for a result set whose shape isn't known ahead of time.

    Used only by the generated-SQL path, where the columns come back from a query
    nobody wrote by hand. Every other chart in this app is built for one specific
    query and says so; this one is deliberately generic, and the caller decides
    whether the shape earns a chart at all.

    Direction still carries a value label as well as a color, so the reading does
    not depend on distinguishing the two hues.
    """
    d = df.dropna(subset=[value_col]).copy()
    d = d.sort_values(value_col).tail(20)
    signed = bool((d[value_col] < 0).any())
    colors = ([pal["up"] if v >= 0 else pal["down"] for v in d[value_col]]
              if signed else [pal["series"][0]] * len(d))
    fmt = (lambda v: f"{v:+.1%}") if tickformat == ".0%" else (lambda v: f"{v:,.2f}")
    fig = go.Figure(go.Bar(
        x=d[value_col], y=d[label_col].astype(str), orientation="h",
        marker_color=colors, marker_line_width=0,
        text=[fmt(v) for v in d[value_col]], textposition="outside",
        textfont=dict(color=pal["text_primary"], size=11), cliponaxis=False,
        hovertemplate="<b>%{x}</b><extra>%{y}</extra>",
        name=str(value_col),
    ))
    fig = style(fig, pal, height=height, crosshair=False)
    fig.update_xaxes(showgrid=True, gridcolor=pal["gridline"], showline=False,
                     tickformat=tickformat)
    fig.update_yaxes(showgrid=False, tickfont=dict(color=pal["text_secondary"], size=11.5))
    if signed:
        fig.add_vline(x=0, line_color=pal["baseline"], line_width=1)
    return fig


# ---------------------------------------------------------------------------
# Stock Journey
# ---------------------------------------------------------------------------
def journey_path(df, pal: dict, *, asof, moments=None, height: int = H_PRIMARY,
                 log: bool = True, show_peak: bool = True) -> go.Figure:
    """The Journey's primary chart: a company's whole record, revealed to `asof`.

    Progressive reveal is drawn as TWO traces over the same series rather than
    by truncating the frame: the travelled part in full color, the rest as a
    faint ghost. Truncating instead would rescale both axes on every playback
    tick, so the line would writhe as it grew and the reader would lose the
    shape they were following. With the ghost present the axes are fixed by the
    full record from the first frame, and only the reveal moves.

    Log scale is the default here and not elsewhere: a journey covers a
    company's entire record, and over 25 years a linear axis compresses the
    first decade into the baseline -- the 2008 crash becomes invisible on a name
    that has since gone up 100x. On a log axis equal vertical distances are
    equal PERCENTAGE moves, which is what the narrative is about.
    """
    asof = str(asof)[:10]
    fig = go.Figure()
    color = pal["series"][0]

    travelled = df[df["date"].astype(str).str[:10] <= asof]

    # The ghost: the whole record, drawn first so the reveal sits on top of it.
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["close"], mode="lines", name="Not yet reached",
        line=dict(color=pal["muted"], width=1),
        opacity=0.28, hoverinfo="skip", showlegend=False,
    ))

    if show_peak and len(travelled):
        # The running all-time high as a step above the price. It is what makes
        # a drawdown legible as distance rather than as a dip: the gap between
        # the two lines IS the drawdown at every point.
        fig.add_trace(go.Scatter(
            x=travelled["date"], y=travelled["peak_close"], mode="lines",
            name="All-time high",
            line=dict(color=pal["up"], width=1, dash="dot", shape="hv"),
            hoverinfo="skip", showlegend=False,
        ))

    if len(travelled):
        fig.add_trace(go.Scatter(
            x=travelled["date"], y=travelled["close"], mode="lines", name="Close",
            line=dict(color=color, width=2),
            fill="tozeroy", fillcolor=wash(color, 0.10),
            hovertemplate="<b>%{y:,.2f}</b><extra></extra>",
        ))
        # The playhead: a dot at the cursor, so the eye has somewhere to be
        # during playback. A vertical rule alone reads as a gridline.
        last = travelled.iloc[-1]
        fig.add_trace(go.Scatter(
            x=[last["date"]], y=[last["close"]], mode="markers",
            marker=dict(size=11, color=color,
                        line=dict(width=2.5, color=pal["surface"])),
            hovertemplate="<b>%{y:,.2f}</b><extra></extra>",
            showlegend=False, name="Now",
        ))
        fig.add_vline(x=last["date"], line_width=1, line_color=color, opacity=0.35)

    fig = style(fig, pal, y_title="Price (log)" if log else "Price", height=height)
    if log:
        fig.update_yaxes(type="log")

    # Both axes are pinned to the FULL record, not to the revealed part. This is
    # the whole reason the ghost trace exists -- see the docstring.
    if len(df):
        fig.update_xaxes(range=[df["date"].min(), df["date"].max()])

    if moments:
        _annotate_moments(fig, pal, moments)
    return fig


# Marker glyph per timeline kind. A new event category needs no entry here: the
# Journey passes `kind`, which is one of three structural roles, while the
# category's own color comes from its `tone` in the events data.
_MOMENT_SYMBOL = {"company": "circle", "market": "square", "milestone": "diamond"}


def _annotate_moments(fig: go.Figure, pal: dict, moments) -> None:
    """Place clickable-looking markers for timeline entries on the price axis.

    One trace per kind so the shapes stay distinguishable without relying on
    color -- the house rule is that identity never rests on color alone, and a
    reader with a color vision deficiency still has three distinct glyphs.

    The markers ride the same invisible 0..1 overlay axis `add_events` uses, so
    they sit at a fixed height whatever the price scale is doing.
    """
    tone_color = {
        "positive": pal["up"], "negative": pal["down"],
        "milestone": pal["series"][3], "neutral": pal["muted"],
    }
    by_kind: dict[str, list] = {}
    for m in moments:
        by_kind.setdefault(m.kind, []).append(m)

    for kind, group in by_kind.items():
        heights = {"company": 0.97, "market": 0.90, "milestone": 0.83}
        fig.add_trace(go.Scatter(
            x=[m.date for m in group],
            y=[heights.get(kind, 0.95)] * len(group),
            yaxis="y2", mode="markers",
            marker=dict(size=9, symbol=_MOMENT_SYMBOL.get(kind, "circle"),
                        color=[tone_color.get(m.tone, pal["muted"]) for m in group],
                        line=dict(width=1.5, color=pal["surface"])),
            text=[
                f"<b>{m.title}</b><br>{m.date}"
                + (f"<br>Move: {m.move:+.1%}" if m.move is not None else "")
                + f"<br>{_wrap(m.detail, 46)}"
                for m in group
            ],
            hovertemplate="%{text}<extra></extra>",
            hoverlabel=dict(align="left"),
            showlegend=False, name=kind,
        ))

    fig.update_layout(yaxis2=dict(
        overlaying="y", side="right", range=[0, 1],
        showgrid=False, zeroline=False, showticklabels=False,
        fixedrange=True, visible=False,
    ))


def journey_drawdown_band(df, pal: dict, *, asof, height: int = H_STRIP) -> go.Figure:
    """The companion strip: how far below the record the company was, over time.

    Paired with `journey_path` above it and revealed to the same cursor, so the
    two read as one instrument. Drawdown is always <= 0, so the axis is fixed
    from the record's worst point to zero rather than auto-ranging -- an
    auto-ranged strip rescales as the reveal advances and a 5% dip early in the
    record looks identical to a 50% crash later on.
    """
    asof = str(asof)[:10]
    travelled = df[df["date"].astype(str).str[:10] <= asof]
    fig = go.Figure()

    if len(travelled):
        fig.add_trace(go.Scatter(
            x=travelled["date"], y=travelled["drawdown"], mode="lines",
            line=dict(color=pal["down"], width=1.5),
            fill="tozeroy", fillcolor=wash(pal["down"], 0.16),
            hovertemplate="<b>%{y:.1%}</b> below high<extra></extra>",
            showlegend=False,
        ))

    fig = style(fig, pal, y_title="Drawdown", height=height, zero_line=True)
    fig.update_yaxes(tickformat=".0%")
    if len(df):
        fig.update_xaxes(range=[df["date"].min(), df["date"].max()])
        worst = float(df["drawdown"].min())
        fig.update_yaxes(range=[worst * 1.08, 0.02])
    return fig

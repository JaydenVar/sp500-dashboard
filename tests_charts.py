"""Regression tests: the chart interaction model, asserted on the figures themselves.

Every property here is invisible in a screenshot and silent when it breaks. A
chart with the wrong interaction settings still renders correctly, still shows
the right numbers, and simply behaves wrong under the pointer -- which is the
class of bug that survives a manual pass over the app.

* **Zoomability is stamped on the figure, and it is what everything reads.**
  `charts.style(zoomable=...)` writes `layout.meta`, and the Plotly config, the
  reset button and `uirevision` are all derived from it. A new builder that
  forgets to declare itself static gets wheel-capture and a reset button on a
  bar chart; the call sites can no longer catch that, because they deliberately
  no longer decide it. This suite is what catches it instead.

* **`view_signature` resets a zoom for the right reasons and only those.** It is
  the whole contract of preserving a view across a rerun, and both failure modes
  are silent: too sensitive and a reader's zoom evaporates when they toggle an
  overlay; too stable and a zoom into 2008 persists onto a chart of a window
  that does not contain 2008. The Journey case matters most -- its playhead
  advances four times a second, and a signature that moved with it would reset
  the axes on every tick.

* **Unified hover keeps its `hoverdistance` cutoff.** With no cutoff, every
  trace contributes its nearest point to the one tooltip, so the market-event
  markers `add_events` overlays would attach a paragraph about 2008 to a hover
  in 2019. This is the specific reason the two hover modes are configured
  differently, and it reads as a styling detail until it regresses.

* **WebGL is used above the point threshold and not below.** Each WebGL figure
  holds a GPU context and browsers cap how many a page may hold, so "always GL"
  is a real cost on a page of small charts; "never GL" is a 6,300-point SVG path
  re-rendering on every pan frame.

Run: ./.venv/bin/python tests_charts.py
"""

from __future__ import annotations

import inspect
import sys

import pandas as pd
import plotly.graph_objects as go

import charts
import components as ui
import data_access as dal
import theme
from universe import INDEX_SYMBOL

_failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}{f'  {detail}' if detail else ''}")


PAL = theme.PALETTES["Dark"]
dal.ensure_db()

FULL = dal.prices(INDEX_SYMBOL, "2001-01-01", "2100-01-01")
SHORT = FULL.tail(250).reset_index(drop=True)
MA = dal.moving_averages(INDEX_SYMBOL, "2001-01-01", "2100-01-01")


def _risk_scatter() -> go.Figure:
    """The one figure assembled outside charts.py (riskfolio.risk_view)."""
    f = go.Figure(go.Scatter(x=[0.1, 0.25], y=[0.08, 0.19], mode="markers"))
    return charts.style(f, PAL, y_title="CAGR", height=charts.H_TALL, crosshair=False)


# A chart you can traverse: a date axis, or two continuous measures.
ZOOMABLE = {
    "price_line": charts.price_line(FULL, PAL),
    "candlestick": charts.candlestick(FULL, PAL),
    "volume_bars": charts.volume_bars(FULL, PAL),
    "returns_bars": charts.returns_bars(
        FULL.assign(daily_return=FULL["close"].pct_change().fillna(0)), PAL),
    "area_series": charts.area_series(FULL.assign(v=0.1), "v", PAL),
    "indexed_comparison": charts.indexed_comparison(
        FULL.set_index("date")[["close"]].rename(columns={"close": "A"}), PAL),
    "multi_series": charts.multi_series(
        {"A": FULL.rename(columns={"close": "v"})}, PAL, value_col="v", y_title="y"),
    "moving_average_chart": charts.moving_average_chart(MA, PAL),
    "journey_path": charts.journey_path(
        FULL.assign(peak_close=FULL["close"].cummax()), PAL, asof="2015-01-01"),
    "journey_drawdown_band": charts.journey_drawdown_band(
        FULL.assign(drawdown=FULL["close"] / FULL["close"].cummax() - 1), PAL, asof="2015-01-01"),
    "risk_scatter": _risk_scatter(),
}

# A category axis, a matrix or a composition: there is no view to move.
STATIC = {
    "yearly_return_bars": charts.yearly_return_bars(pd.DataFrame({
        "tick": ["2023", "2024"], "year_return": [0.2, -0.1],
        "partial": [False, True], "label": ["", ""]}), PAL),
    "sector_bars": charts.sector_bars(pd.DataFrame({
        "sector": ["Tech", "Energy"], "median_return": [0.3, -0.1], "n_symbols": [5, 4],
        "avg_return": [0.3, -0.1], "best_return": [0.5, 0.1], "worst_return": [0.1, -0.3]}), PAL),
    "correlation_heatmap": charts.correlation_heatmap(
        pd.DataFrame([[1.0, 0.4], [0.4, 1.0]], index=["A", "B"], columns=["A", "B"]), PAL),
    "seasonality_heatmap": charts.seasonality_heatmap(
        pd.DataFrame([[0.1, -0.2]], index=[2024], columns=["01", "02"]), PAL, ["Jan", "Feb"]),
    "allocation_donut": charts.allocation_donut(
        pd.DataFrame({"symbol": ["A", "B"], "weight": [0.6, 0.4]}), PAL),
    "contribution_bars": charts.contribution_bars(pd.DataFrame({
        "symbol": ["A"], "name": ["A Co"], "weight": [1.0],
        "holding_return": [0.2], "contribution": [0.2]}), PAL),
    "generic_bars": charts.generic_bars(
        pd.DataFrame({"k": ["A", "B"], "v": [1.0, 2.0]}), PAL, label_col="k", value_col="v"),
}

print(f"{len(FULL)} sessions in the index record; "
      f"{len(ZOOMABLE)} zoomable builders, {len(STATIC)} static")

print("\n=== every builder declares its kind, and the config follows from it ===")
for name, fig in ZOOMABLE.items():
    check(f"{name}: pan, wheel zoom, free axes",
          charts.is_zoomable(fig)
          and charts.plot_config(fig)["scrollZoom"] is True
          and fig.layout.dragmode == "pan"
          and fig.layout.xaxis.fixedrange is False
          and fig.layout.yaxis.fixedrange is False)
for name, fig in STATIC.items():
    check(f"{name}: no drag, the page keeps the wheel",
          not charts.is_zoomable(fig)
          and charts.plot_config(fig)["scrollZoom"] is False
          and fig.layout.dragmode is False
          and fig.layout.xaxis.fixedrange is True
          and fig.layout.yaxis.fixedrange is True)

print("\n=== the crosshair, and the flicker it used to have ===")
price, candle, sector = ZOOMABLE["price_line"], ZOOMABLE["candlestick"], STATIC["sector_bars"]
check("a zoomable chart carries an x crosshair", price.layout.xaxis.showspikes is True)
check("the crosshair has no distance cutoff", price.layout.spikedistance == -1)
check("a static chart carries none", sector.layout.xaxis.showspikes is False)
check("closest-hover charts gained a crosshair too",
      candle.layout.xaxis.showspikes is True
      and ZOOMABLE["risk_scatter"].layout.xaxis.showspikes is True)
check("closest-hover charts also get a y crosshair",
      candle.layout.yaxis.showspikes is True)
check("unified-hover charts do NOT — the box already prints every y",
      price.layout.yaxis.showspikes in (None, False))
check("unified hover keeps its cutoff, so event markers stay out of the tooltip",
      price.layout.hoverdistance == 20)
check("closest hover drops the cutoff, so the readout never falls between sessions",
      candle.layout.hoverdistance == -1 and ZOOMABLE["risk_scatter"].layout.hoverdistance == -1)
check("series names are never truncated in a tooltip",
      price.layout.hoverlabel.namelength == -1)

print("\n=== the modebar and the double-click contract ===")
check("double-click restores the authored axes", charts.PLOT_CONFIG["doubleClick"] == "reset")
check("zoom tools are offered on zoomable charts only",
      "zoom2d" not in charts.PLOT_CONFIG["modeBarButtonsToRemove"]
      and "zoom2d" in charts.PLOT_CONFIG_STATIC["modeBarButtonsToRemove"])
check("only one reset affordance in the bar (autoscale removed, reset kept)",
      "autoScale2d" in charts.PLOT_CONFIG["modeBarButtonsToRemove"]
      and "resetScale2d" not in charts.PLOT_CONFIG["modeBarButtonsToRemove"])

print("\n=== WebGL above the threshold, SVG below it ===")
check(f"a {len(FULL)}-point line is WebGL",
      type(ZOOMABLE["price_line"].data[0]).__name__ == "Scattergl")
check(f"a {len(SHORT)}-point line stays SVG",
      type(charts.price_line(SHORT, PAL).data[0]).__name__ == "Scatter")
check("a log-axis line stays SVG whatever its length",
      type(charts.price_line(FULL, PAL, log=True).data[0]).__name__ == "Scatter")
check("every trace of a long overlay chart is WebGL",
      all(type(t).__name__ == "Scattergl" for t in ZOOMABLE["moving_average_chart"].data))

print("\n=== view_signature: what resets a zoom, and what must not ===")
base = charts.view_signature(charts.price_line(FULL, PAL))
check("the same data twice is the same token", charts.view_signature(charts.price_line(FULL, PAL)) == base, base)
check("adding moving-average overlays KEEPS the view",
      charts.view_signature(ZOOMABLE["moving_average_chart"]) == base)
check("changing the date window RESETS the view",
      charts.view_signature(charts.price_line(SHORT, PAL)) != base)
check("a price level an order of magnitude away RESETS the view",
      charts.view_signature(charts.price_line(FULL.assign(close=FULL["close"] * 20), PAL)) != base)
_journey = FULL.assign(peak_close=FULL["close"].cummax())
_ticks = {charts.view_signature(charts.journey_path(_journey, PAL, asof=a))
          for a in ("2003-01-01", "2009-06-30", "2015-01-01", FULL["date"].max())}
check("the Journey playhead advancing does NOT reset the view",
      len(_ticks) == 1, f"{len(_ticks)} distinct token(s) across four cursor positions")
check("a static chart carries no view token at all",
      all(charts.view_signature(f) == "static" for f in STATIC.values()))

print("\n=== components.chart takes its behaviour from the figure ===")
sig = inspect.signature(ui.chart)
check("config is derived unless overridden", sig.parameters["config"].default is None)
check("the reset button is derived unless overridden", sig.parameters["controls"].default is None)
check("no page still passes a hardcoded chart config",
      not any("PLOT_CONFIG" in open(f).read()
              for f in ("research.py", "markets.py", "riskfolio.py", "answers.py")))

print()
print("FAILURES:", _failures)
sys.exit(1 if _failures else 0)

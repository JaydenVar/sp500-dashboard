"""Regression tests: the brand layer — palette contrast, the mark, sparklines.

Everything here is a property that is *invisible when it breaks*. A sparkline
drawn from a constant series still renders — as an SVG full of `NaN`
coordinates, which browsers silently drop, so the card shows an empty box rather
than an error. An accent whose contrast slipped under 4.5:1 still paints; it
just stops being readable for some of the people reading it. Neither shows up in
a screenshot taken by someone who already knows what it is supposed to say.

* **The brand ramp is measured, not admired.** `theme.PALETTES[mode]['brand']` is
  the ink of every selected tab label, the Explorer's caption and every link, so
  it is small text and owes 4.5:1 against the surface it is drawn on. That is
  asserted here per mode rather than trusted to the eye that chose it.

* **The gains/losses scale and the categorical series are UNCHANGED.** The
  rebrand's one hard rule: a reader brings green-up/red-down with them, and the
  series order is the colorblind-safety mechanism that was validated. Both are
  pinned to their exact values so a future palette edit that "tidies" them fails
  here instead of on screen.

* **The brand is not a series color.** If the app's own accent were also a slot
  in the categorical scale, "the product's color" and "this company's color"
  would be the same signal on a chart legend.

* **Sparkline projection survives its degenerate inputs.** A flat series (zero
  span), a one-point series, an all-NaN column and a 6,300-point record are all
  real: a newly listed company, a fund that has not moved, a symbol with no
  price rows, and a 25-year core company. Each has a defined answer here.

* **Thinning keeps the last point.** The endpoint dot marks the latest close and
  sits beside a number describing that session; a stride that dropped it would
  put the dot on a different day than the figure next to it.

Run: ./.venv/bin/python tests_brand.py
"""

from __future__ import annotations

import re
import sys

import components as ui
import theme

_failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}{f'  {detail}' if detail else ''}")


# ---------------------------------------------------------------------------
# WCAG relative luminance, from the definition rather than a library, so this
# suite has no dependency the app does not already carry.
# ---------------------------------------------------------------------------
def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    parts = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        parts.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = parts
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    a, b = _luminance(fg), _luminance(bg)
    lo, hi = min(a, b), max(a, b)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------------------
print("=== the brand ramp is readable on its own surface ===")
for mode, pal in theme.PALETTES.items():
    ratio = contrast(pal["brand"], pal["surface"])
    check(f"{mode}: brand {pal['brand']} on surface clears 4.5:1",
          ratio >= 4.5, f"{ratio:.2f}:1")
    # The accent is aliased to the brand on purpose -- it is what lets ~25
    # existing var(--accent) rules adopt the brand with no call-site change.
    check(f"{mode}: accent is the brand", pal["accent"] == pal["brand"])
    check(f"{mode}: brand_2 is the validated series[0]",
          pal["brand_2"] == pal["series"][0])

print("\n=== the market scale and the series slots are untouched ===")
check("dark up/down unchanged",
      (theme.PALETTES["Dark"]["up"], theme.PALETTES["Dark"]["down"]) == ("#3ec46b", "#f2685f"))
check("light up/down unchanged",
      (theme.PALETTES["Light"]["up"], theme.PALETTES["Light"]["down"]) == ("#0b7d3f", "#c62828"))
check("dark series slots unchanged",
      theme.PALETTES["Dark"]["series"] == [
          "#3987e5", "#d95926", "#199e70", "#c98500",
          "#d55181", "#008300", "#9085e9", "#e66767"])
check("light series slots unchanged",
      theme.PALETTES["Light"]["series"] == [
          "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
          "#e87ba4", "#008300", "#4a3aa7", "#e34948"])
for mode, pal in theme.PALETTES.items():
    check(f"{mode}: the brand is NOT a categorical slot",
          pal["brand"].lower() not in [s.lower() for s in pal["series"]])
    check(f"{mode}: the brand is neither the up nor the down color",
          pal["brand"].lower() not in (pal["up"].lower(), pal["down"].lower()))

print("\n=== every rule still resolves against both palettes ===")
for mode, pal in theme.PALETTES.items():
    css = theme.app_css(pal)
    check(f"{mode}: stylesheet builds", "<style>" in css and len(css) > 10_000)
    # A palette key removed in a future edit surfaces as a KeyError above, but a
    # var() referencing a token no rule defines fails SILENTLY -- the property is
    # dropped and the element keeps whatever it inherited.
    declared = set(re.findall(r"^\s*(--[a-z0-9-]+):", css, re.M))
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", css))
    check(f"{mode}: no rule reads an undefined custom property",
          used <= declared, f"missing: {sorted(used - declared)}")

print("\n=== the brandmark ===")
mark = ui.brandmark(38)
check("is a self-contained svg", mark.startswith("<svg") and mark.endswith("</svg>"))
check("carries an accessible name", 'role="img"' in mark and 'aria-label="MarketLens"' in mark)
check("follows the palette via CSS vars, not baked hexes",
      "var(--brand)" in mark and "var(--brand-2)" in mark and "#8B75F0" not in mark)
# SVG ids are document-global: two marks sharing a gradient id would both paint
# from whichever <defs> parsed first.
check("namespaces its gradient id",
      "ml-grad-hdr" in ui.brandmark(uid="hdr") and "ml-grad-x" in ui.brandmark(uid="x"))
check("two marks on one page cannot collide",
      ui.brandmark(uid="a") != ui.brandmark(uid="b"))

print("\n=== the wordmark ===")
check("splits into two inks", ui.wordmark("MarketLens", ("Market", "Lens"))
      == '<div class="hdr-title">Market<b>Lens</b></div>')
check("falls back whole when the split does not compose the name",
      "<b>" not in ui.wordmark("MarketLens", ("Mark", "Lens")))

print("\n=== the greeting ===")
import datetime as dt  # noqa: E402  (local to this section, as the app's is)

for hour, expected in ((0, "Good morning"), (11, "Good morning"),
                       (12, "Good afternoon"), (17, "Good afternoon"),
                       (18, "Good evening"), (23, "Good evening")):
    got = ui.greeting(dt.datetime(2026, 8, 1, hour, 0, tzinfo=ui.MARKET_TZ))
    check(f"{hour:02d}:00 ET -> {expected}", got == expected, got)
check("a naive datetime is read as exchange-local rather than raising",
      ui.greeting(dt.datetime(2026, 8, 1, 9, 0)) == "Good morning")
# The app knows a session, not a person. A name would have to be invented.
check("carries no name placeholder",
      not any(t in ui.greeting() for t in ("{", "%s", "None")))

print("\n=== sparkline projection ===")
RISING = [10, 11, 10.5, 12, 13, 12.5, 14]
FALLING = list(reversed(RISING))
PAL = theme.PALETTES["Dark"]

check("a rising series is the up color", ui.spark_color(RISING, PAL) == PAL["up"])
check("a falling series is the down color", ui.spark_color(FALLING, PAL) == PAL["down"])
check("a flat series is muted", ui.spark_color([5, 5, 5], PAL) == PAL["muted"])
check("direction is first-to-last, not the path between",
      ui.spark_color([10, 99, 11], PAL) == PAL["up"])

svg = ui.sparkline(RISING, color=PAL["up"])
check("renders an svg", svg.startswith("<svg") and "<path" in svg)
check("never emits a NaN coordinate", "nan" not in svg.lower())
check("draws the endpoint marker", "<circle" in svg)

flat = ui.sparkline([5, 5, 5, 5], color=PAL["muted"])
# Zero span: the naive projection divides by it and every y becomes NaN, which
# browsers drop silently -- an empty card rather than a flat line.
check("a flat series draws a line rather than NaNs",
      flat and "nan" not in flat.lower())
check("a flat series is centred vertically",
      flat.count(f"{ui.SPARK_H / 2:.1f}") >= 2)

check("one point is not a line", ui.sparkline([7], color=PAL["up"]) == "")
check("an empty series is not a line", ui.sparkline([], color=PAL["up"]) == "")
check("an all-missing column is not a line",
      ui.sparkline([float("nan"), float("nan")], color=PAL["up"]) == "")
check("missing values are dropped, not projected",
      "nan" not in ui.sparkline([10, float("nan"), 12, 14], color=PAL["up"]).lower())

print("\n=== thinning a long record ===")


def stroke_points(svg: str) -> int:
    """Vertices on the stroked path — NOT the filled area path underneath it.

    The area path is the same line plus two closing corners, so counting ` L`
    across the whole document double-counts every vertex and reports a thinned
    120-point curve as 243.
    """
    stroke = [seg for seg in svg.split('<path d="') if 'fill="none"' in seg][0]
    return stroke.split('"')[0].count(" L") + 1


LONG = [float(i % 97) for i in range(6300)]  # a 25-year core company
long_svg = ui.sparkline(LONG, color=PAL["up"])
n_points = stroke_points(long_svg)
check("a 6,300-point record is thinned", n_points <= ui.SPARK_MAX_POINTS + 1, f"{n_points} points")
check("thinning keeps the path small", len(long_svg) < 4000, f"{len(long_svg)} bytes")
check("a short series is drawn in full", stroke_points(ui.sparkline(RISING, color=PAL["up"]))
      == len(RISING))

# The endpoint dot marks the LATEST close, beside a number describing that
# session. A stride that dropped it would put the dot on a different day.
tail_svg = ui.sparkline([1.0] * 200 + [999.0], color=PAL["up"])
last_circle = tail_svg.split("<circle")[-1]
check("the endpoint circle sits at the right edge",
      f'cx="{ui.SPARK_W - 1:.1f}"' in last_circle, last_circle[:56])
check("the endpoint circle sits on the final value, not a strided neighbour",
      'cy="2.0"' in last_circle, "999 is the series max, so y is the top pad")

print("\n=== the CSS background-image variant ===")
uri = ui.sparkline_uri(RISING, color=PAL["up"])
check("is a css url() value", uri.startswith('url("data:image/svg+xml,') and uri.endswith('")'))
# An unescaped '#' terminates a data URI at the fragment, so a raw hex color
# would truncate the SVG to everything before the stroke color.
check("percent-encodes the '#' in a hex color", "%23" in uri and "#" not in uri)
check("percent-encodes the quotes that would close the url()",
      '"' not in uri[5:-2])
check("an undrawable series yields no background rule",
      ui.sparkline_uri([1], color=PAL["up"]) == "")

print("\n=== pick-chip name truncation ===")
import research  # noqa: E402  (imports streamlit; kept out of the module header)

check("a short name is untouched", research._pick_name("KLA CORP") == "KLA CORP")
check("a long name is cut on a word boundary",
      research._pick_name("SIMON PROPERTY GROUP INC").endswith("…"))
check("truncation stays within budget",
      all(len(research._pick_name(n)) <= 23 for n in
          ("SIMON PROPERTY GROUP INC", "A" * 80, "INTERNATIONAL BUSINESS MACHINES CORP")))
check("a single unbroken word still truncates", research._pick_name("A" * 80).endswith("…"))
check("whitespace is normalized", research._pick_name("  KLA   CORP  ") == "KLA CORP")

print()
print("FAILURES:", _failures)
sys.exit(1 if _failures else 0)

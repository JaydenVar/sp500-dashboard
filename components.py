"""Reusable UI pieces: header, KPI cards, quote strip, section headings, tables.

All user/data-derived text is HTML-escaped before it reaches an f-string, since
these build markup directly.
"""

from __future__ import annotations

import datetime as dt
import html
import urllib.parse
import zoneinfo

import pandas as pd
import streamlit as st

import charts


def _market_tz():
    """Exchange-local timezone, resolved defensively.

    `zoneinfo` reads the *system* tz database, which slim Linux containers often
    omit even though macOS always has it -- so this resolves locally and can fail
    at import time in a deploy. `tzdata` is in requirements.txt to supply it, and
    this fallback means a missing database degrades the market-status label to a
    fixed-offset approximation instead of taking the whole app down.
    """
    try:
        return zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        # Fixed -5h: correct in winter, an hour off during US DST. The status pill
        # already says it is schedule-based, and a slightly-off label beats a crash.
        return dt.timezone(dt.timedelta(hours=-5), "ET (approx)")


# US equity regular session, in exchange-local time.
MARKET_TZ = _market_tz()
TZ_IS_EXACT = isinstance(MARKET_TZ, zoneinfo.ZoneInfo)
OPEN_T = dt.time(9, 30)
CLOSE_T = dt.time(16, 0)


def esc(v) -> str:
    return html.escape(str(v), quote=True)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def fmt_price(v, digits: int = 2) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:,.{digits}f}"


def fmt_pct(v, digits: int = 2, signed: bool = True) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v * 100:{'+' if signed else ''}.{digits}f}%"


def fmt_compact(v) -> str:
    """1_284 -> 1,284 · 12_900 -> 12.9K · 4_200_000 -> 4.2M"""
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    sign = "-" if v < 0 else ""
    a = abs(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{sign}{a / div:.1f}{suf}"
    return f"{sign}{a:,.0f}"


def fmt_dollar_compact(v) -> str:
    return "—" if v is None or pd.isna(v) else "$" + fmt_compact(v)


# ---------------------------------------------------------------------------
# Market status
# ---------------------------------------------------------------------------
def market_status(now: dt.datetime | None = None) -> tuple[bool, str]:
    """(is_open, human label). Weekends and outside 09:30–16:00 ET are closed.

    Exchange holidays are NOT modelled — the label says 'schedule only' so it
    never claims more precision than it has.
    """
    now = now or dt.datetime.now(MARKET_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=MARKET_TZ)
    now = now.astimezone(MARKET_TZ)
    weekday = now.weekday() < 5
    in_hours = OPEN_T <= now.time() < CLOSE_T
    is_open = weekday and in_hours
    # If the tz database was unavailable we're on a fixed offset that ignores DST,
    # so say so rather than presenting an hour-off clock as exact.
    approx = "" if TZ_IS_EXACT else " (approx)"
    if is_open:
        return True, f"Market open · {now:%H:%M} ET{approx}"
    if not weekday:
        return False, "Market closed · weekend"
    if now.time() < OPEN_T:
        return False, f"Pre-market · opens 09:30 ET{approx}"
    return False, f"After hours · closed 16:00 ET{approx}"


# ---------------------------------------------------------------------------
# Brand
# ---------------------------------------------------------------------------
def brandmark(size: int = 38, *, uid: str = "hdr", cls: str = "hdr-mark") -> str:
    """The MarketLens mark, as inline SVG.

    **One glyph carrying both halves of the name.** The white path is a five-point
    line that reads simultaneously as the letter **M** (two apexes over a valley,
    legs falling to the baseline) and as a price chart making a higher high; the
    filled dot sits on that higher apex as the focal point, and the ring behind
    it is the lens barrel. The tile carries the brand gradient, which is the one
    place the two brand colors appear together.

    Inline rather than an asset file for two reasons: it inherits nothing from
    the network (Streamlit Cloud serves no static directory by default, so an
    <img> would need a data URI anyway), and it stays crisp at any DPI.

    `uid` namespaces the gradient's element id. SVG ids are DOCUMENT-global, so
    two marks on one page with the same id would both resolve to whichever
    <defs> the browser parsed first -- fine today, a silent mis-paint the moment
    a second mark is drawn with different colors.
    """
    gid = f"ml-grad-{uid}"
    # The stops carry the brand as `style`, NOT as a stop-color presentation
    # attribute: `var()` is a CSS value function, and browsers disagree about
    # whether it resolves inside a presentation attribute. In `style` it is a
    # plain declaration and always does -- which is what keeps the mark following
    # the Light/Dark palette without this function needing the palette dict.
    return (
        f'<svg class="{esc(cls)}" width="{size}" height="{size}" viewBox="0 0 40 40" '
        f'role="img" aria-label="MarketLens" xmlns="http://www.w3.org/2000/svg">'
        f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" style="stop-color: var(--brand)"/>'
        f'<stop offset="1" style="stop-color: var(--brand-2)"/>'
        f'</linearGradient></defs>'
        f'<rect width="40" height="40" rx="11" fill="url(#{gid})"/>'
        # The lens barrel, sitting behind the path.
        f'<circle cx="20" cy="20" r="12.6" fill="none" stroke="#fff" '
        f'stroke-opacity="0.3" stroke-width="1.4"/>'
        # M / market path: up, dip, higher up, down.
        f'<path d="M10 26.4 L15.4 15.2 L20 20.6 L24.6 12.4 L30 26.4" fill="none" '
        f'stroke="#fff" stroke-width="2.7" stroke-linecap="round" '
        f'stroke-linejoin="round"/>'
        # The focus, on the higher apex.
        f'<circle cx="24.6" cy="12.4" r="2.9" fill="#fff"/>'
        f'</svg>'
    )


def wordmark(name: str, split: tuple[str, str]) -> str:
    """The wordmark: first half in primary ink, second half in the brand gradient.

    `split` comes from `theme.BRAND_SPLIT` rather than being sliced here, so the
    two halves are a property of the brand and not of this renderer.
    """
    head, tail = split
    if head + tail != name:  # a caller passed a name the split doesn't compose
        return f'<div class="hdr-title">{esc(name)}</div>'
    return f'<div class="hdr-title">{esc(head)}<b>{esc(tail)}</b></div>'


def header(title: str, subtitle: str, data_through: dt.date, n_symbols: int,
           *, split: tuple[str, str] | None = None) -> None:
    """The product masthead: brandmark, wordmark, and the session's own facts.

    One bar rather than a title over a subtitle: a platform header is furniture
    a reader stops reading after the first visit, so it should cost one row.
    """
    is_open, label = market_status()
    dot = "dot-open" if is_open else "dot-closed"
    mark = wordmark(title, split) if split else f'<div class="hdr-title">{esc(title)}</div>'
    st.markdown(
        f"""
<div class="hdr">
  <div class="hdr-left">
    {brandmark(38)}
    <div>
      {mark}
      <div class="hdr-sub">{esc(subtitle)}</div>
    </div>
  </div>
  <div class="hdr-right">
    <span class="mkt"><span class="dot {dot}"></span>{esc(label)}</span>
    <div class="hdr-meta">
      <div>Data through <b>{esc(data_through.strftime('%b %d, %Y'))}</b></div>
      <div><b>{n_symbols}</b> symbols · daily close</div>
    </div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def greeting(now: dt.datetime | None = None) -> str:
    """"Good morning" / "Good afternoon" / "Good evening", in exchange-local time.

    **Deliberately carries no name.** The app knows a browser session, not a
    person; a greeting that guesses at a name is worse than one that doesn't try,
    and there is nowhere honest to get one from. Exchange-local rather than the
    reader's own clock because everything else on the page is stated in ET, and
    a header saying "Good evening" beside a market that is still open would be
    the app disagreeing with itself.
    """
    now = now or dt.datetime.now(MARKET_TZ)
    if now.tzinfo is None:  # same defensive step as market_status()
        now = now.replace(tzinfo=MARKET_TZ)
    hour = now.astimezone(MARKET_TZ).hour
    if hour < 12:
        return "Good morning"
    return "Good afternoon" if hour < 18 else "Good evening"


def section(title: str, sub: str = "") -> None:
    st.markdown(
        f'<div class="sec"><span class="sec-t">{esc(title)}</span>'
        f'<span class="sec-s">{esc(sub)}</span></div>',
        unsafe_allow_html=True,
    )


def note(text: str) -> None:
    """Muted caption. Escapes its input -- use note_md() when you need emphasis."""
    st.markdown(f'<div class="note">{esc(text)}</div>', unsafe_allow_html=True)


def note_md(text: str) -> None:
    """Muted caption that renders Markdown (bold, links) instead of escaping it.

    `note()` escapes its input, which is right for anything data-derived but turns
    literal `**` into visible asterisks in hand-written copy.
    """
    st.caption(text)


# ---------------------------------------------------------------------------
# Widget-key hygiene
#
# `st.radio` and `st.selectbox` RAISE when `session_state[key]` holds a value
# that is not in `options`, and `st.multiselect` does the same for any element of
# its stored list. Both happen routinely here for two reasons:
#
# * a browser session survives a Streamlit Cloud hot-update, so it can still hold
#   a section name, sub-view or preset label that the new code no longer offers;
# * several option lists are computed per run (search matches, the sectors that
#   have rows, the companies on the current board) and legitimately shrink.
#
# The result is an app that was working a moment ago raising
# `StreamlitAPIException` on the next click. These clear the stale value first.
#
# BOTH MUST RUN BEFORE THE WIDGET IS INSTANTIATED. Streamlit forbids writing a
# widget's key only *after* that widget exists in the current run -- which is the
# same rule the Ask suggestion chips and the Journey cursor are arranged around.
# ---------------------------------------------------------------------------
def select_guard(key: str, options) -> None:
    """Drop a stored single choice that is no longer offered."""
    if key in st.session_state and st.session_state[key] not in options:
        del st.session_state[key]


def multiselect_guard(key: str, options) -> None:
    """Keep the still-valid choices in a stored multiselect, drop the rest.

    Clearing the whole selection would throw away a reader's other picks because
    one of them stopped being offered.
    """
    stored = st.session_state.get(key)
    if stored is None:
        return
    kept = [v for v in stored if v in options]
    if len(kept) != len(stored):
        st.session_state[key] = kept


def sub_nav(page: str, options, *, default: str | None = None) -> str:
    """The second-level navigation inside a page.

    A radio, never `st.tabs` -- for the same reason the top-level nav refuses
    tabs: `st.tabs` renders EVERY tab body on every rerun, so it would run all of
    a page's queries when one view is visible and make Plotly measure charts
    inside hidden containers. Rendering only the active view fixes both.

    Keyed per page (`sub_<page>`) so each page remembers its own view, and styled
    as a lighter segmented control by `theme.app_css` so the hierarchy against the
    top-level nav is visible at a glance.
    """
    opts = list(options)
    key = f"sub_{page}"
    select_guard(key, opts)
    start = opts.index(default) if default in opts else 0
    return st.radio(page, opts, index=start, horizontal=True, key=key,
                    label_visibility="collapsed")


# ---------------------------------------------------------------------------
# Cards
#
# Promoted out of `market_intel.py`, where they were introduced and where they
# made that one page read tighter than the rest of the app *because* nothing else
# had them. One surface, one border and one spacing scale everywhere is the point
# -- the `.mi-` class names are kept because the palette-derived rules in
# `theme.app_css` already passed the contrast and color-vision validation under
# those names.
# ---------------------------------------------------------------------------
def metric_rows(rows) -> str:
    """Label/value pairs as card body HTML. Both sides are escaped."""
    return "".join(
        f'<div class="mi-metric"><span class="k">{esc(k)}</span>'
        f'<span class="v">{esc(v)}</span></div>' for k, v in rows)


def card(title: str, rows) -> None:
    """A titled card of label/value rows -- the app's standard detail panel."""
    st.markdown(f'<div class="mi-card"><h4>{esc(title)}</h4>'
                f'{metric_rows(rows)}</div>', unsafe_allow_html=True)


def fmt_metric(value, kind: str) -> str:
    """One formatter for every ranking metric, driven by the registry's `fmt`.

    Centralised because the same metric is printed in six places (the board, the
    breakdown, strengths, risks, the valuation card, the financials card) and a
    ratio that renders as a percentage in one of them is the kind of
    inconsistency that makes a reader stop trusting the page.
    """
    if value is None or (isinstance(value, float) and value != value):
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if kind == "pct":
        return f"{v:+.1%}" if abs(v) < 100 else f"{v:,.0f}"
    if kind == "money":
        return fmt_dollar_compact(v)
    if kind == "ratio":
        return f"{v:,.2f}x"
    return f"{v:,.2f}"


# `kind` -> (css modifier, default icon). Severity is carried by the icon and the
# wording as well as the rule color, so it survives a reader who can't separate
# the two hues.
_EMPTY_KINDS = {
    "empty": ("", "○"),
    "warn": ("empty-warn", "⚠"),
    "info": ("empty-info", "ℹ"),
}


def empty_state(message: str, hint: str = "", *, kind: str = "empty", icon: str = "") -> None:
    """The one way this app says "there is nothing to show here".

    Replaces raw `st.warning` / `st.info`, whose default panels are framework
    chrome in colors the palette never validated. `hint` is where the recovery
    goes: an empty state that only reports absence leaves the reader stuck, so
    the second line says what to change (a longer window, a different symbol).
    """
    modifier, default_icon = _EMPTY_KINDS.get(kind, _EMPTY_KINDS["empty"])
    hint_html = f'<div class="empty-hint">{esc(hint)}</div>' if hint else ""
    st.markdown(
        f'<div class="empty {modifier}">'
        f'<span class="empty-ico">{esc(icon or default_icon)}</span>'
        f'<div class="empty-body"><div class="empty-msg">{esc(message)}</div>'
        f'{hint_html}</div></div>',
        unsafe_allow_html=True,
    )


def table_view(label: str, df, *, column_config: dict | None = None,
               height: int | None = None, footer: str = "",
               hide_index: bool = True) -> None:
    """A chart's underlying numbers, in a collapsed expander.

    Every chart in this app is paired with one of these -- the table is the
    accessible channel for readers who can't use the visual, and the fallback
    when a value has to be read exactly rather than estimated off an axis.
    Collapsed by default because it is the secondary reading; note that an
    expander still EXECUTES its body when collapsed, so this takes an
    already-computed frame and never a query.
    """
    with st.expander(label):
        st.dataframe(df, width="stretch", hide_index=hide_index,
                     column_config=column_config or {},
                     **({"height": height} if height else {}))
        if footer:
            st.markdown(f'<div class="note">{esc(footer)}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sparklines
#
# A sparkline is a SHAPE, not an instrument. It has no axes, no hover, no zoom
# and no reset button, so it is inline SVG rather than a Plotly figure: four of
# these on the landing page would otherwise be four more figures for Streamlit to
# serialize and Plotly to measure, to say something a ~200-byte path says.
#
# They are drawn from a column the page ALREADY queried. Nothing here computes a
# statistic -- min, max and the endpoints are used only to map values onto a
# viewBox, which is projection, not analysis.
# ---------------------------------------------------------------------------
SPARK_W = 96
SPARK_H = 26
# A ~96px-wide shape cannot resolve more marks than this, and a 25-year daily
# record is ~6,300 points -- which would be a 60KB path in the page source for a
# curve indistinguishable from one drawn with 120. Thinning is a rendering
# decision, not an analytical one: it never changes which series is drawn.
SPARK_MAX_POINTS = 120


def _spark_points(values, w: float, h: float, pad: float = 2.0) -> list[tuple[float, float]]:
    """Values -> (x, y) in SVG space, with y inverted and a flat series centred.

    A constant series has zero span, and dividing by it would be a NaN in every
    coordinate; it is drawn as a centred straight line instead, which is what a
    flat series actually looks like.
    """
    lo, hi = min(values), max(values)
    span = hi - lo
    inner = h - 2 * pad
    step = w / (len(values) - 1) if len(values) > 1 else 0.0
    if span <= 0:
        return [(i * step, h / 2) for i in range(len(values))]
    return [(i * step, pad + inner * (1 - (v - lo) / span)) for i, v in enumerate(values)]


def sparkline(values, *, color: str, w: int = SPARK_W, h: int = SPARK_H,
              fill: bool = True, width: float = 1.6, uid: str = "s") -> str:
    """A closing-price path as inline SVG. Returns "" when there is nothing to draw.

    Returns markup rather than rendering, so a caller can put it inside a card,
    a table cell, or -- as the pick chips do -- a data URI on a CSS background.
    """
    series = [float(v) for v in values
              if v is not None and not (isinstance(v, float) and v != v)]
    if len(series) < 2:
        return ""
    if len(series) > SPARK_MAX_POINTS:
        # Stride, but always keep the LAST point: the endpoint dot marks the
        # latest close, and a stride that dropped it would put the dot on a
        # session the number beside it is not describing.
        step = len(series) / SPARK_MAX_POINTS
        idx = sorted({int(i * step) for i in range(SPARK_MAX_POINTS)} | {len(series) - 1})
        series = [series[i] for i in idx]

    pts = _spark_points(series, w - 1, h)
    path = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
    gid = f"ml-spark-{uid}"

    area = ""
    if fill:
        area = (
            f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" style="stop-color: {esc(color)}; stop-opacity: .28"/>'
            f'<stop offset="1" style="stop-color: {esc(color)}; stop-opacity: 0"/>'
            f'</linearGradient></defs>'
            f'<path d="{path} L{pts[-1][0]:.1f} {h} L0 {h} Z" fill="url(#{gid})"/>'
        )

    return (
        f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'preserveAspectRatio="none" aria-hidden="true" '
        f'xmlns="http://www.w3.org/2000/svg">{area}'
        f'<path d="{path}" fill="none" stroke="{esc(color)}" stroke-width="{width}" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="{width + .5:.1f}" '
        f'fill="{esc(color)}"/></svg>'
    )


def spark_color(values, pal: dict) -> str:
    """Green when the series ended above where it started, red below, muted flat.

    The direction is the series' own first-to-last move -- the same convention
    the rest of the app uses for a return -- so the color is never a judgement
    this function made up.
    """
    series = [float(v) for v in values
              if v is not None and not (isinstance(v, float) and v != v)]
    if len(series) < 2 or series[0] == 0:
        return pal["muted"]
    if series[-1] > series[0]:
        return pal["up"]
    return pal["down"] if series[-1] < series[0] else pal["muted"]


def sparkline_uri(values, *, color: str, w: int = SPARK_W, h: int = SPARK_H,
                  uid: str = "s") -> str:
    """The same sparkline as a `url(...)` value for a CSS background-image.

    This is how the pick chips get a sparkline: a Streamlit button's label is
    markdown text and cannot hold an <svg>, so the shape is painted behind the
    label instead. Percent-encoded rather than base64 -- the SVG stays readable
    in devtools and the encoding costs fewer bytes at this size.
    """
    svg = sparkline(values, color=color, w=w, h=h, uid=uid)
    if not svg:
        return ""
    encoded = urllib.parse.quote(svg, safe="")
    return f"url(\"data:image/svg+xml,{encoded}\")"


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
def kpi_cards(cards: list[dict]) -> None:
    """cards: [{icon, label, value, small?, change?, change_dir?, foot?, spark?}]

    `change_dir` is 'up' | 'down' | 'flat' and is passed explicitly rather than
    inferred from sign, because for drawdown/volatility a bigger number is worse.

    `spark` is pre-rendered markup from `sparkline()` and is trusted as such --
    every other field on the card is escaped. It goes last in the card because it
    is context for the number above it, never the reading itself.
    """
    out = ['<div class="kpi-grid">']
    for c in cards:
        chg = ""
        if c.get("change"):
            d = c.get("change_dir", "flat")
            arrow = {"up": "▲", "down": "▼", "flat": "•"}[d]
            chg = f'<div class="chg chg-{d}">{arrow} {esc(c["change"])}</div>'
        foot = f'<div class="kpi-foot">{esc(c["foot"])}</div>' if c.get("foot") else ""
        spark = f'<div class="kpi-spark">{c["spark"]}</div>' if c.get("spark") else ""
        out.append(
            f'<div class="kpi">'
            f'<div class="kpi-top"><span class="kpi-ico">{c.get("icon", "")}</span>'
            f'<span class="kpi-label">{esc(c["label"])}</span></div>'
            f'<div class="kpi-val{" sm" if c.get("small") else ""}">{esc(c["value"])}</div>'
            f'{chg}{foot}{spark}</div>'
        )
    out.append("</div>")
    st.markdown("".join(out), unsafe_allow_html=True)


def _quote_html(name: str, symbol: str, price, meta: str,
                change, pct, pal: dict) -> str:
    """The shared markup behind both quote headers.

    One function so the stored-history header and the live header are the same
    object visually. They came from different sources (a `prices` row vs. a
    provider payload) and briefly had different layouts, which made moving
    between Snapshot and History feel like moving between two applications.
    """
    if pct is None or (isinstance(pct, float) and pct != pct):
        color, chg_txt = pal["muted"], "—"
    else:
        up = pct >= 0
        color = pal["up"] if up else pal["down"]
        arrow = "▲" if up else "▼"
        d_txt = "" if change is None or change != change else f"{change:+,.2f} "
        chg_txt = f"{arrow} {d_txt}({fmt_pct(pct)})"

    return f"""
<div class="quote">
  <div style="flex:1 1 260px; min-width:0;">
    <div class="q-name">{esc(name)}<span class="q-tkr">{esc(symbol)}</span></div>
    <div class="q-meta">{esc(meta)}</div>
  </div>
  <div style="text-align:right;">
    <div class="q-price">{fmt_price(price)}</div>
    <div class="q-chg" style="color:{color};">{esc(chg_txt)}</div>
  </div>
</div>
"""


def quote_strip(name: str, symbol: str, q: pd.Series, meta: str, pal: dict) -> None:
    """The Yahoo-style price header for a symbol in the local database."""
    ret = q.get("daily_return")
    delta = None
    if ret is not None and not pd.isna(ret) and not pd.isna(q.get("prev_close")):
        delta = q["close"] - q["prev_close"]
    st.markdown(
        _quote_html(name, symbol, q["close"], meta,
                    delta, None if pd.isna(ret) else ret, pal),
        unsafe_allow_html=True,
    )


def live_quote_strip(q: dict, meta: str, pal: dict) -> None:
    """The same header, from a live provider payload.

    Snapshot previously opened straight onto a row of KPI cards, so the company
    it described was named nowhere above the fold -- a reader who clicked in from
    a pick card saw a price with no idea whose it was. Every quote screen worth
    copying leads with the name and the ticker; this does too.
    """
    st.markdown(
        _quote_html(q.get("name") or q.get("symbol") or "", q.get("symbol") or "",
                    q.get("price"), meta, q.get("change"), q.get("change_pct"), pal),
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def chart(fig, *, key: str, config: dict | None = None, caption: str = "",
          controls: bool | None = None, select: bool = False):
    """Render a Plotly figure with the interaction model the figure asked for.

    The figure decides, not the call site. `charts.style(zoomable=...)` stamps
    `layout.meta`, and this reads it back for all three of: which Plotly config
    to use (wheel zoom and a full modebar, or a fixed frame), whether to draw a
    reset button, and whether the view is worth preserving at all. Passing
    `config` or `controls` explicitly still wins, but nothing needs to.

    **Zoom survives a rerun because of `uirevision`.** Streamlit re-sends the
    whole figure whenever anything on the page changes, and Plotly's default is
    to re-home the axes each time it does -- so before this, every zoom was
    destroyed by an unrelated widget. Holding `uirevision` constant tells Plotly
    to keep the reader's view across a re-render; keying it on
    `charts.view_signature` is what still lets a real data change reset it. See
    that function for exactly which changes count.

    The reset button then has to defeat `uirevision` deliberately, which is what
    the nonce is for: bumping it changes the widget key, Streamlit remounts the
    component, and the client-side view state goes with it. That is a heavier
    hammer than re-keying `uirevision`, and the right one -- it is the single
    action in the app whose entire purpose is to discard that state, and it
    cannot half-work.

    `select=True` makes the chart report clicked points back to Python and
    returns the selection; it is off by default because a chart that reruns the
    script on every stray click is worse than one that does nothing. Only the
    Journey uses it, where clicking an event marker is the point.
    """
    zoomable = charts.is_zoomable(fig)
    if config is None:
        config = charts.plot_config(fig)
    if controls is None:
        controls = zoomable

    nonce_key = f"__nonce_{key}"
    nonce = st.session_state.get(nonce_key, 0)

    if zoomable:
        fig.update_layout(uirevision=f"{key}|{charts.view_signature(fig)}")

    if controls:
        left, right = st.columns([5, 1.15])
        with left:
            if caption:
                st.markdown(f'<div class="note">{esc(caption)}</div>', unsafe_allow_html=True)
        with right:
            if st.button("↺ Reset view", key=f"__reset_{key}", width="stretch",
                         help="Restore the default zoom, pan and axis range"):
                st.session_state[nonce_key] = nonce + 1
                st.rerun()
    elif caption:
        st.markdown(f'<div class="note">{esc(caption)}</div>', unsafe_allow_html=True)

    # `use_container_width`, NOT `width="stretch"` -- unlike st.dataframe and
    # st.button, st.plotly_chart has no `width` parameter in Streamlit 1.50. It
    # would land in **kwargs, which this widget forwards to Plotly, and surface
    # on every chart in the app as "The keyword arguments have been deprecated
    # ... use `config` instead". Revisit only after checking the signature:
    # `inspect.signature(st.plotly_chart)`.
    if select:
        return st.plotly_chart(
            fig, use_container_width=True, config=config, key=f"{key}_{nonce}",
            on_select="rerun", selection_mode="points",
        )
    st.plotly_chart(fig, use_container_width=True, config=config, key=f"{key}_{nonce}")
    return None


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
def data_table(
    df: pd.DataFrame,
    *,
    key: str,
    column_config: dict | None = None,
    search_cols: tuple[str, ...] = (),
    filter_cols: tuple[str, ...] = (),
    sort_options: dict[str, str] | None = None,
    default_sort: str | None = None,
    page_size_options: tuple[int, ...] = (10, 25, 50),
    csv_name: str = "table.csv",
    height: int = 420,
) -> None:
    """Searchable + sortable + paginated table with CSV export.

    Sorting is offered explicitly (as well as via Streamlit's own column-header
    click) because header sort only orders the CURRENT page; sorting the frame
    first means page 1 really is the top of the whole set.
    """
    c1, c2, c3, c4 = st.columns([2.4, 1.7, 1.0, 1.0])
    with c1:
        term = st.text_input(
            "Search", key=f"{key}_q", placeholder="Filter by ticker, name or sector…",
            label_visibility="collapsed",
        ) if search_cols else ""
    with c2:
        sort_key = st.selectbox(
            "Sort", list(sort_options or {}), key=f"{key}_sort",
            index=(list(sort_options).index(default_sort) if sort_options and default_sort in sort_options else 0),
            label_visibility="collapsed",
        ) if sort_options else None
    with c3:
        ascending = st.selectbox(
            "Order", ["High → low", "Low → high"], key=f"{key}_dir", label_visibility="collapsed",
        ) == "Low → high"
    with c4:
        per_page = st.selectbox(
            "Rows", page_size_options, key=f"{key}_n", label_visibility="collapsed",
        )

    view = df
    if term and search_cols:
        t = term.strip().lower()
        mask = pd.Series(False, index=view.index)
        for col in search_cols:
            if col in view.columns:
                mask |= view[col].astype(str).str.lower().str.contains(t, na=False, regex=False)
        view = view[mask]

    # Column filters: multiselect per low-cardinality text column (Sector,
    # Industry...). Only offered where the distinct count is small enough that a
    # picker beats typing -- a filter with 49 options is just a worse search box.
    if filter_cols:
        fcols = [c for c in filter_cols if c in df.columns and 2 <= df[c].nunique() <= 25]
        if fcols:
            with st.expander(f"Filters ({len(fcols)})"):
                cols_ui = st.columns(min(len(fcols), 3))
                for i, col in enumerate(fcols):
                    with cols_ui[i % len(cols_ui)]:
                        opts = sorted(df[col].dropna().astype(str).unique())
                        chosen = st.multiselect(col, opts, key=f"{key}_f_{col}")
                        if chosen:
                            view = view[view[col].astype(str).isin(chosen)]

    if sort_options and sort_key:
        col = sort_options[sort_key]
        if col in view.columns:
            view = view.sort_values(col, ascending=ascending, na_position="last")

    total = len(view)
    pages = max(1, (total + per_page - 1) // per_page)
    page = 1
    if pages > 1:
        page = st.number_input(
            f"Page (1–{pages})", min_value=1, max_value=pages, value=1, step=1, key=f"{key}_p",
        )
    lo = (int(page) - 1) * per_page
    shown = view.iloc[lo:lo + per_page]

    st.dataframe(
        shown, width="stretch", hide_index=True, height=height,
        column_config=column_config or {},
    )

    left, right = st.columns([3, 1])
    with left:
        rng = f"{lo + 1}–{min(lo + per_page, total)} of {total:,}" if total else "no matches"
        st.markdown(f'<div class="note">Showing {rng}</div>', unsafe_allow_html=True)
    with right:
        st.download_button(
            "⬇ Export CSV", data=view.to_csv(index=False), file_name=csv_name,
            mime="text/csv", key=f"{key}_dl", width="stretch",
        )


# ---------------------------------------------------------------------------
# Stock Journey
# ---------------------------------------------------------------------------
def journey_header(date_label: str, price: float, chip: tuple[str, str],
                   ret_label: str, ret_dir: str) -> None:
    """The cursor headline: the one line a reader tracks during playback."""
    chip_text, chip_tone = chip
    st.markdown(
        f'<div class="jrn-head">'
        f'<span class="jrn-date">{esc(date_label)}</span>'
        f'<span class="jrn-price">{esc(fmt_price(price))}</span>'
        f'<span class="chg chg-{esc(ret_dir)}">{esc(ret_label)}</span>'
        f'<span class="jrn-spacer"></span>'
        f'<span class="jrn-chip {esc(chip_tone)}">{esc(chip_text)}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def did_you_know(facts, *, on_jump=None, key_prefix: str = "dyk") -> None:
    """Render Did You Know cards, each optionally jumping the cursor to its date.

    The card body is one HTML block and the jump is a separate small button
    underneath, rather than the whole card being a widget: Streamlit has no
    clickable-container primitive, and faking one with an invisible button
    overlay breaks keyboard focus order and screen-reader labelling. A named
    button is worse-looking and genuinely operable.
    """
    if not facts:
        empty_state("No facts to show yet.",
                    "Move the cursor further into the company's history.")
        return

    cols = st.columns(min(len(facts), 3))
    for i, fact in enumerate(facts):
        with cols[i % len(cols)]:
            st.markdown(
                f'<div class="dyk {esc(fact.tone)}">'
                f'<div class="dyk-top"><span class="dyk-ico">{fact.icon}</span>'
                f'<span class="dyk-head">{esc(fact.headline)}</span></div>'
                f'<div class="dyk-body">{esc(fact.detail)}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            if fact.jump_date and on_jump is not None:
                st.button(
                    "Go to this moment", key=f"{key_prefix}_{i}", width="stretch",
                    on_click=on_jump, args=(fact.jump_date,),
                    help=f"Move the journey cursor to {fact.jump_date}",
                )


def journey_timeline(moments, *, on_jump=None, key_prefix: str = "tl",
                     limit: int = 40) -> None:
    """The historical timeline beside the chart, newest first.

    Newest first because the cursor is usually at the end of what has been
    revealed, so the events nearest the playhead are the ones being read. The
    `limit` keeps a 30-year mega-cap from rendering 80 rows and 80 buttons,
    which is slow and is not a timeline anyone reads to the bottom of.
    """
    if not moments:
        empty_state("Nothing has happened yet on this journey.",
                    "Press play, or drag the cursor forward to reach the "
                    "company's first recorded events.")
        return

    shown = list(reversed(moments))[:limit]
    for i, m in enumerate(shown):
        move_html = ""
        if m.move is not None:
            direction = "up" if m.move >= 0 else "down"
            move_html = (f'<span class="tl-move {direction}">'
                         f'{fmt_pct(m.move, 1)}</span>')
        st.markdown(
            f'<div class="tl-row {esc(m.tone)}">'
            f'<div class="tl-date">{esc(m.date)}'
            f'<span class="tl-kind">{esc(m.kind)}</span></div>'
            f'<div class="tl-title">{esc(m.title)} {move_html}</div>'
            f'<div class="tl-body">{esc(m.detail)}</div>'
            + (f'<div class="tl-src">Source: {esc(m.source)}</div>' if m.source else "")
            + "</div>",
            unsafe_allow_html=True,
        )
        if on_jump is not None:
            st.button("Jump here", key=f"{key_prefix}_{i}_{m.date}_{m.kind}",
                      on_click=on_jump, args=(m.date,),
                      help=f"Move the journey cursor to {m.date}")

    if len(moments) > limit:
        note(f"Showing the {limit} most recent of {len(moments)} moments so far.")

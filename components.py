"""Reusable UI pieces: header, KPI cards, quote strip, section headings, tables.

All user/data-derived text is HTML-escaped before it reaches an f-string, since
these build markup directly.
"""

from __future__ import annotations

import datetime as dt
import html
import zoneinfo

import pandas as pd
import streamlit as st


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


def header(title: str, subtitle: str, data_through: dt.date, n_symbols: int) -> None:
    is_open, label = market_status()
    dot = "dot-open" if is_open else "dot-closed"
    st.markdown(
        f"""
<div class="hdr">
  <div class="hdr-left">
    <div class="hdr-mark">📈</div>
    <div>
      <div class="hdr-title">{esc(title)}</div>
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
# Cards
# ---------------------------------------------------------------------------
def kpi_cards(cards: list[dict]) -> None:
    """cards: [{icon, label, value, small?, change?, change_dir?, foot?}]

    `change_dir` is 'up' | 'down' | 'flat' and is passed explicitly rather than
    inferred from sign, because for drawdown/volatility a bigger number is worse.
    """
    out = ['<div class="kpi-grid">']
    for c in cards:
        chg = ""
        if c.get("change"):
            d = c.get("change_dir", "flat")
            arrow = {"up": "▲", "down": "▼", "flat": "•"}[d]
            chg = f'<div class="chg chg-{d}">{arrow} {esc(c["change"])}</div>'
        foot = f'<div class="kpi-foot">{esc(c["foot"])}</div>' if c.get("foot") else ""
        out.append(
            f'<div class="kpi">'
            f'<div class="kpi-top"><span class="kpi-ico">{c.get("icon", "")}</span>'
            f'<span class="kpi-label">{esc(c["label"])}</span></div>'
            f'<div class="kpi-val{" sm" if c.get("small") else ""}">{esc(c["value"])}</div>'
            f'{chg}{foot}</div>'
        )
    out.append("</div>")
    st.markdown("".join(out), unsafe_allow_html=True)


def quote_strip(name: str, symbol: str, q: pd.Series, meta: str, pal: dict) -> None:
    """The Yahoo-style price header for a selected symbol."""
    ret = q.get("daily_return")
    if ret is None or pd.isna(ret):
        color, arrow, chg_txt = pal["muted"], "", "—"
    else:
        up = ret >= 0
        color = pal["up"] if up else pal["down"]
        arrow = "▲" if up else "▼"
        delta = q["close"] - q["prev_close"] if not pd.isna(q.get("prev_close")) else None
        d_txt = f"{delta:+,.2f} " if delta is not None else ""
        chg_txt = f"{arrow} {d_txt}({fmt_pct(ret)})"

    st.markdown(
        f"""
<div class="quote">
  <div style="flex:1 1 260px; min-width:0;">
    <div class="q-name">{esc(name)}<span class="q-tkr">{esc(symbol)}</span></div>
    <div class="q-meta">{esc(meta)}</div>
  </div>
  <div style="text-align:right;">
    <div class="q-price">{fmt_price(q['close'])}</div>
    <div class="q-chg" style="color:{color};">{esc(chg_txt)}</div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def chart(fig, *, key: str, config: dict, caption: str = "", controls: bool = True) -> None:
    """Render a Plotly figure with a visible reset control.

    Zoom and pan live in the browser, so Python can't read them back. Bumping a
    nonce in the chart's key remounts the component, which is what actually
    discards the client-side view state -- the same thing the toolbar's "reset
    axes" does, but as a control a reader can find without hovering first.
    """
    nonce_key = f"__nonce_{key}"
    nonce = st.session_state.get(nonce_key, 0)

    if controls:
        left, right = st.columns([5, 1.15])
        with left:
            if caption:
                st.markdown(f'<div class="note">{esc(caption)}</div>', unsafe_allow_html=True)
        with right:
            if st.button("↺ Reset view", key=f"__reset_{key}", use_container_width=True,
                         help="Restore the default zoom, pan and axis range"):
                st.session_state[nonce_key] = nonce + 1
                st.rerun()
    elif caption:
        st.markdown(f'<div class="note">{esc(caption)}</div>', unsafe_allow_html=True)

    st.plotly_chart(fig, use_container_width=True, config=config, key=f"{key}_{nonce}")


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
def data_table(
    df: pd.DataFrame,
    *,
    key: str,
    column_config: dict | None = None,
    search_cols: tuple[str, ...] = (),
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
        shown, use_container_width=True, hide_index=True, height=height,
        column_config=column_config or {},
    )

    left, right = st.columns([3, 1])
    with left:
        rng = f"{lo + 1}–{min(lo + per_page, total)} of {total:,}" if total else "no matches"
        st.markdown(f'<div class="note">Showing {rng}</div>', unsafe_allow_html=True)
    with right:
        st.download_button(
            "⬇ Export CSV", data=view.to_csv(index=False), file_name=csv_name,
            mime="text/csv", key=f"{key}_dl", use_container_width=True,
        )

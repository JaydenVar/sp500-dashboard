"""MarketLens — a SQL-first equity platform.

Two deliberately separate experiences share this entry point:

* **User Mode** (default) is the financial product. It shows one company's
  research, the ranking engine, the market, risk and portfolios, and never
  surfaces SQL, schemas, timings or any other implementation detail -- mixing
  those into an end-user screen is what makes an application read as a class
  project rather than a product.
* **Developer Center** (see devcenter.py) is where all of that technical material
  lives, organized for someone evaluating the engineering.

Underneath both: every figure on screen is the output of a SQL query
(see queries.py) against a SQLite database whose analysis lives in views and
materialized rollups (see build_db.py).

**This module lays out chrome and nothing else.** It resolves the theme, the
directory and the shared date window, builds the navigation, and dispatches to
one page module (`research`, `market_intel`, `markets`, `riskfolio`, `about`).
It draws no figure and computes no metric. It used to hold every section inline
at 1,500 lines, which meant a change to one page risked every other one.
"""

from __future__ import annotations

import datetime as dt

import streamlit as st

import about
import components as ui
import data_access as dal
import devcenter
import market_intel
import markets
import research
import riskfolio
from pagectx import Ctx
from theme import BRAND_NAME, BRAND_SPLIT, BRAND_TAGLINE, PALETTES, app_css
from universe import INDEX_SYMBOL

# The tab icon stays an emoji rather than the SVG brandmark: `page_icon` takes an
# emoji or something PIL can open, and SVG is neither -- committing a PNG just to
# theme a favicon would put a binary artifact in a repository whose entire visual
# layer is otherwise generated. The mark itself is inline SVG in the app bar,
# which is where a reader actually looks.
st.set_page_config(
    page_title=f"{BRAND_NAME} — Equity Research & Intelligence",
    page_icon="🔷",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Date presets. Value is (label -> lookback in days | sentinel).
PRESETS: dict[str, object] = {
    "1M": 30, "3M": 91, "6M": 182, "YTD": "ytd",
    "1Y": 365, "3Y": 1095, "5Y": 1826, "10Y": 3653,
    "All Time": "all", "Custom Range": "custom",
}
DEFAULT_PRESET = "All Time"
CUSTOM_PRESET = "Custom Range"

# Five pages, each answering one question. Nine wrapped onto two rows on a
# laptop, and three of them were about the same subject behind three separate
# company pickers.
USER_SECTIONS = ["Research", "Intelligence", "Markets", "Risk & Portfolio", "About"]
DEFAULT_SECTION = "Research"

# ---------------------------------------------------------------------------
# THE SHARED DATE WINDOW.
#
# One value, resolved once, for the whole app: moving between pages keeps every
# figure describing the same period, which is what makes reading across them
# trustworthy. That is a twice-defended decision and it has not changed.
#
# What IS new is that the control only appears where the active view reads it.
# A window control sitting above the Journey, or above Snapshot's live 1D chart,
# claims a reach it does not have -- and a control that overstates its reach
# makes a reader distrust the pages it really does govern.
#
# THIS IS NOT THE PER-SECTION DATE FILTER THAT WAS REJECTED TWICE. That proposal
# was several independent windows, one per page, each free to re-baseline a
# figure another page also showed. This is one window whose control is hidden
# where it would do nothing. The distinction is the whole design, so it is
# stated here rather than left for a future reader to rediscover.
#
# Each page module owns the list, next to the views themselves, so adding a view
# cannot leave this out of date.
# ---------------------------------------------------------------------------
WINDOWED_VIEWS: dict[str, tuple[str, ...]] = {
    "Research": research.WINDOWED,
    "Markets": markets.WINDOWED,
    "Risk & Portfolio": riskfolio.WINDOWED,
}
PAGE_VIEWS: dict[str, tuple[str, ...]] = {
    "Research": research.VIEWS,
    "Markets": markets.VIEWS,
    "Risk & Portfolio": riskfolio.VIEWS,
}


def resolve_range(preset: str, min_d: dt.date, max_d: dt.date) -> tuple[dt.date, dt.date]:
    """Preset label (or bare sentinel) -> window, clamped to the available data.

    Relative windows run back from the newest session in the data rather than
    from today, so "1Y" is the last year the market traded -- the two differ
    every weekend and after every holiday.
    """
    spec = PRESETS.get(preset, preset)
    # "max" is the old label for All Time. A browser session that survived a
    # Streamlit Cloud hot-update can still be holding it (see the deploy hazard
    # in PROJECT_STATE), so it stays an accepted alias rather than falling
    # through to an empty window.
    if spec in ("all", "max", "custom"):
        return min_d, max_d
    if spec == "ytd":
        return max(dt.date(max_d.year, 1, 1), min_d), max_d
    try:
        days = int(spec)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return min_d, max_d
    return max(max_d - dt.timedelta(days=days), min_d), max_d


def custom_window(min_d: dt.date, max_d: dt.date) -> tuple[dt.date, dt.date]:
    """Start/end pickers for Custom Range, validated before anything queries it.

    Both fields are bounded by the data, so the failure a reader can actually
    produce is a reversed pair -- and SQLite answers `BETWEEN '2020' AND '2010'`
    with zero rows, which would surface as "No data in the selected window" on
    every page at once: an input error wearing the costume of a data gap. It
    is caught here instead, and the last valid window is held.
    """
    last_ok = st.session_state.get("custom_window", (min_d, max_d))
    cols = st.columns([1.3, 1.3, 3.4])
    with cols[0]:
        picked_s = st.date_input(
            "Start date", value=last_ok[0], min_value=min_d, max_value=max_d,
            key="custom_start", format="YYYY-MM-DD",
        )
    with cols[1]:
        picked_e = st.date_input(
            "End date", value=last_ok[1], min_value=min_d, max_value=max_d,
            key="custom_end", format="YYYY-MM-DD",
        )

    # A cleared field yields None; hold the last good window rather than
    # comparing None against a date.
    if picked_s is None or picked_e is None:
        return last_ok
    if picked_s > picked_e:
        st.error(
            f"Start date ({picked_s:%b %d, %Y}) is after the end date "
            f"({picked_e:%b %d, %Y}). Showing the previous range."
        )
        return last_ok

    st.session_state["custom_window"] = (picked_s, picked_e)
    return picked_s, picked_e


def active_view(section: str) -> str:
    """The sub-view a page will open on, read BEFORE that page renders.

    The window control has to know whether the view about to be drawn reads the
    window, but the sub-nav radio lives inside the page and is built after this
    row. Reading the stored key is safe -- it is a read, and `ui.sub_nav` falls
    back to the same default when nothing is stored.
    """
    views = PAGE_VIEWS.get(section)
    if not views:
        return ""
    stored = st.session_state.get(f"sub_{section}")
    return stored if stored in views else views[0]


# ---------------------------------------------------------------------------
# Theme + chrome
# ---------------------------------------------------------------------------
if "theme" not in st.session_state:
    st.session_state.theme = "Dark"

pal = PALETTES[st.session_state.theme]
st.markdown(app_css(pal), unsafe_allow_html=True)

directory = dal.directory()
equities = directory[directory["is_index"] == 0].reset_index(drop=True)
last_date = dal.global_last_date()

ui.header(BRAND_NAME, BRAND_TAGLINE, last_date, len(directory), split=BRAND_SPLIT)

# ---------------------------------------------------------------------------
# Navigation — the page radio comes FIRST, because what the control row shows
# depends on which page is active.
#
# Deliberately a radio, not st.tabs. st.tabs renders EVERY tab's body on every
# rerun, which (a) runs all pages' queries when only one is visible and (b) makes
# Plotly measure charts inside hidden tabs, so they render at a fraction of the
# container width. Rendering only the active page fixes both.
# ---------------------------------------------------------------------------
nav = st.columns([4.6, 1.5, 1.9])
with nav[1]:
    theme_choice = st.radio(
        "Theme", ["Light", "Dark"], horizontal=True, label_visibility="collapsed",
        index=0 if st.session_state.theme == "Light" else 1, key="theme_pick",
    )
    if theme_choice != st.session_state.theme:
        st.session_state.theme = theme_choice
        st.rerun()
with nav[2]:
    mode = st.radio(
        "Mode", ["User", "Developer"], horizontal=True, key="mode",
        label_visibility="collapsed",
        help="User Mode is the financial product. Developer Center documents how it's built.",
    )

DEV = mode == "Developer"
options = devcenter.SECTIONS if DEV else USER_SECTIONS
nav_key = "dev_section" if DEV else "section"

# A browser session that survived a hot-update can still be holding a page name
# this build no longer offers ("Companies", "Overview"), and `st.radio` RAISES on
# a stored value outside its options -- so the first load after this ships would
# be a stack trace rather than a page. Cleared before the widget exists, which is
# the only point Streamlit allows a widget key to be written.
ui.select_guard(nav_key, options)
with nav[0]:
    section = st.radio("Section", options, horizontal=True, key=nav_key,
                       label_visibility="collapsed")

view = "" if DEV else active_view(section)

# ---------------------------------------------------------------------------
# Control row — the date window, shown only where it reaches
# ---------------------------------------------------------------------------
# `preset` is a widget key, and Streamlit DISCARDS widget state for a widget that
# did not render this run. Since the control is now conditional, the chosen
# preset is mirrored into a plain key that survives a visit to a page without
# the control -- otherwise navigating to Snapshot and back would silently reset
# a reader's 5Y window to the default.
if st.session_state.get("preset") in PRESETS:
    st.session_state["preset_value"] = st.session_state["preset"]
stored_preset = st.session_state.get("preset_value", DEFAULT_PRESET)
if stored_preset not in PRESETS:
    stored_preset = DEFAULT_PRESET

show_window = DEV or view in WINDOWED_VIEWS.get(section, ())

if show_window:
    ui.select_guard("preset", PRESETS)
    preset = st.radio(
        "Date range", list(PRESETS), index=list(PRESETS).index(stored_preset),
        horizontal=True, key="preset", label_visibility="collapsed",
    )
    st.session_state["preset_value"] = preset
else:
    preset = stored_preset

index_min, index_max = dal.date_bounds(INDEX_SYMBOL)
if preset == CUSTOM_PRESET and show_window:
    start_d, end_d = custom_window(index_min, index_max)
elif preset == CUSTOM_PRESET:
    start_d, end_d = st.session_state.get("custom_window", (index_min, index_max))
else:
    start_d, end_d = resolve_range(preset, index_min, index_max)
START, END = start_d.isoformat(), end_d.isoformat()

# The banner states what the window is scoping RIGHT NOW, by name. It used to
# name a fixed list of sections, which silently became wrong the moment a new
# section was added -- so it is derived, and where the window does not reach it
# says so instead of quoting dates the page beside it ignores.
here = f"{ui.esc(section)}{f' → {ui.esc(view)}' if view else ''}"
if DEV:
    scope = (f'Window <b>{start_d:%b %d, %Y}</b> → <b>{end_d:%b %d, %Y}</b>'
             f' · {ui.esc(preset)} · seeds the SQL Explorer window')
elif show_window:
    scope = (f'Window <b>{start_d:%b %d, %Y}</b> → <b>{end_d:%b %d, %Y}</b>'
             f' · {ui.esc(preset)} · scoping {here}')
else:
    scope = (f'{here} sets its own period — the shared date window does not '
             f'reach it, so its control is hidden rather than shown above '
             f'figures it would not change')
st.markdown(
    f'<div class="note">'
    f'<span class="modepill{" dev" if DEV else ""}">'
    f'{"◆ Developer Center" if DEV else "● User Mode"}</span>'
    f'&nbsp;&nbsp;{scope}</div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Dispatch. One call per page; every body lives in its own module.
# ---------------------------------------------------------------------------
if DEV:
    devcenter.render(section, directory, START, END)
else:
    ctx = Ctx(
        pal=pal, directory=directory, equities=equities, preset=preset,
        start_d=start_d, end_d=end_d, start=START, end=END,
        last_date=last_date, index_min=index_min, index_max=index_max,
    )
    if section == "Research":
        research.render(ctx)
    elif section == "Intelligence":
        # NOT windowed, and not all-time either: the engine scores fixed trailing
        # windows (1-year volatility, 12-1 momentum, trailing-twelve-month
        # fundamentals) that the methodology defines. Letting the shared filter
        # reach it would change what "12-1 momentum" means from one visit to the
        # next -- the scores would stop being comparable to EACH OTHER, which is
        # a different and worse failure than re-baselining a comparison.
        market_intel.render(pal)
    elif section == "Markets":
        markets.render(ctx)
    elif section == "Risk & Portfolio":
        riskfolio.render(ctx)
    else:
        about.render(ctx)

"""Smoke tests: every page and sub-view renders, under every preset, without raising.

This is the failure class the other suites cannot see. `tests_sqlguard`,
`tests_router`, `tests_portfolio` and `tests_ranking` all test layers *below* the
page -- a query can be correct, validated, fast and correctly scored while a page
module still raises a `KeyError` on the frame it returns, and nothing would catch
it before the deploy.

`streamlit.testing.v1.AppTest` runs `app.py` in-process and drives widgets by
key: no browser, no extra dependency (it ships with Streamlit). Widget state is
set through `session_state` before `.run()`, which is how a page, a sub-view and
a preset get selected without clicking anything.

The cases are chosen to be the ones that have actually broken or that render an
edge path:

* every page x every sub-view x four presets, including `1M` -- short windows are
  where a rolling or correlation query legitimately returns nothing and the page
  has to show an empty state rather than index into an empty frame;
* **stale widget keys**: a browser session that survived a hot-update still holds
  the OLD page names, and `st.radio` raises on a stored value outside its
  options. This is the failure the restructure was most likely to ship, so it is
  tested directly rather than assumed away by the guards;
* Research against three kinds of company -- core 50, ranked-universe-only (the
  five-year fallback record), and a ticker in neither;
* each rolling-return horizon, including one longer than some symbols' history;
* correlation selections of 0, 1, 2 and 8 symbols -- below two it must not query;
* portfolio baskets of 0, 1, 2 and 4 holdings;
* every ranking horizon, objective and risk band, since each generates a
  DIFFERENT scoring statement from the metric registry;
* the Developer Center with every SQL Explorer entry selected, which is the only
  thing that actually executes each showcased statement. A templated query left
  with an unfilled `{slot}` fails here and nowhere else.

**Cost:** a full run is several minutes, because each case is a cold app start.
That is why it is a separate script rather than something to run on every edit --
give it its own CI step.

Run: ./.venv/bin/python tests_ui.py
"""

from __future__ import annotations

import ast
import datetime as dt
import pathlib
import sys

from streamlit.testing.v1 import AppTest

import queries
import ranking

APP = str(pathlib.Path(__file__).parent / "app.py")
PRESETS = ["1M", "1Y", "10Y", "All Time"]


def _literal(name: str):
    """Read a module-level constant out of app.py rather than restating it here.

    A hardcoded copy silently stops covering a new page the moment one is added:
    the sweep keeps passing while the new page is never rendered. Parsed with
    `ast` because importing app.py would execute the whole Streamlit script.
    """
    tree = ast.parse(pathlib.Path(APP).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit(f"could not find {name} in app.py")


SECTIONS = _literal("USER_SECTIONS")

# Sub-views are declared by the page modules themselves, which is where the app
# reads them from too -- so a renamed view breaks here rather than silently
# dropping out of the sweep.
import markets  # noqa: E402
import research  # noqa: E402
import riskfolio  # noqa: E402

VIEWS: dict[str, tuple[str, ...]] = {
    "Research": research.VIEWS,
    "Markets": markets.VIEWS,
    "Risk & Portfolio": riskfolio.VIEWS,
}

# Research opens on Snapshot, which fetches from the provider. The preset sweep
# is about SQL paths, so it is pinned to History there and the network paths get
# their own labelled cases below -- otherwise a provider hiccup fails cases that
# have nothing to do with this codebase, and the sweep stops being a signal.
OFFLINE_VIEW = {"Research": "History"}

print(f"Sweeping {len(SECTIONS)} pages read from app.py: {', '.join(SECTIONS)}\n")

_failures = 0


def run(label: str, state: dict) -> None:
    """Start the app with `state` pre-loaded and assert it raised nothing."""
    global _failures
    at = AppTest.from_file(APP, default_timeout=180)
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    if at.exception:
        _failures += 1
        detail = "; ".join(str(e.value)[:200] for e in at.exception)
        print(f"  [FAIL] {label}  {detail}")
    else:
        print(f"  [ok ] {label}")


def page(section: str, view: str | None = None, **extra) -> dict:
    state = {"section": section, **extra}
    if view is not None:
        state[f"sub_{section}"] = view
    return state


print("=== every page, every preset ===")
for section in SECTIONS:
    for preset in PRESETS:
        view = OFFLINE_VIEW.get(section) if section in VIEWS else None
        run(f"{section} · {preset}", page(section, view, preset=preset))

print("=== every sub-view ===")
for section, views in VIEWS.items():
    for view in views:
        # Research → Snapshot is a network case; it is covered under its own
        # heading below rather than here.
        if section == "Research" and view == "Snapshot":
            continue
        run(f"{section} → {view}", page(section, view, preset="5Y"))

print("=== stale widget keys: a session that survived a hot-update ===")
# THE failure this restructure was most likely to ship. `st.radio` raises when
# session_state holds a value outside its options, so an open tab pinned to a
# page name from the previous build would greet its reader with a stack trace.
for stale in ("Companies", "Overview", "Market", "Journey", "Performance", "Risk"):
    run(f"stale section '{stale}'", {"section": stale})
run("stale preset 'MAX'", {"section": "Markets", "preset": "MAX"})
run("stale sub-view", {"section": "Markets", "sub_Markets": "Movers"})
run("stale research sub-view", {"section": "Research", "sub_Research": "Companies"})
# A correlation selection holding a symbol that is no longer in the directory.
run("stale correlation symbol",
    page("Risk & Portfolio", "Risk", risk_corr_syms=["AAPL", "NOT_A_SYMBOL"]))
run("stale portfolio holding",
    page("Risk & Portfolio", "Portfolio", pf_syms=["AAPL", "NOT_A_SYMBOL"]))

print("=== research: the three kinds of company ===")
# MU is in the ranked universe but not the 50-symbol core, so History must render
# the five-year fallback record with its label rather than an empty state. ZZZZ is
# in neither, which is also not an error -- Snapshot is the answer.
for sym, kind in (("AAPL", "core 50"), ("MU", "ranked universe only"),
                  ("ZZZZ", "neither universe")):
    run(f"Research → History · {sym} ({kind})",
        page("Research", "History", rs_symbol=sym, preset="All Time"))
    run(f"Research → Journey · {sym} ({kind})",
        page("Research", "Journey", rs_symbol=sym))

# The fallback record inside a window that starts before the record does, and
# inside one that ends before it begins.
run("Research → History · MU, 10Y window (record is 5Y)",
    page("Research", "History", rs_symbol="MU", preset="10Y"))
run("Research → History · MU, 1M window",
    page("Research", "History", rs_symbol="MU", preset="1M"))

print("=== research: the detail radio, on both kinds of record ===")
for sym in ("AAPL", "MU"):
    for detail in ("Moving averages", "Volume & daily returns",
                   "Growth & volatility", "Drawdown"):
        run(f"Research → History · {sym} · {detail}",
            page("Research", "History", rs_symbol=sym, preset="5Y", co_detail=detail))

print("=== the shared window: hidden where it does not reach ===")
# The preset is mirrored into a plain key because Streamlit discards widget
# state for a widget that did not render. A page whose control is hidden must
# still carry the reader's window through to the next page that uses one.
run("preset survives a page without the control",
    {"section": "Research", "sub_Research": "Journey", "preset_value": "5Y"})
run("custom range on a page without the control",
    {"section": "Research", "sub_Research": "Snapshot", "preset_value": "Custom Range",
     "custom_window": (dt.date(2020, 1, 2), dt.date(2021, 1, 4))})
run("custom range where it does apply",
    page("Markets", "Index", preset="Custom Range",
         custom_window=(dt.date(2020, 1, 2), dt.date(2021, 1, 4))))

print("=== rolling-return horizons ===")
for horizon in ("1Y", "3Y", "5Y", "10Y"):
    run(f"Markets → Performance · rolling {horizon}",
        page("Markets", "Performance", preset="All Time", perf_roll=horizon))

print("=== correlation selections ===")
# Below two symbols the page must show a prompt, not build a 1x1 matrix.
for syms in ([], ["AAPL"], ["AAPL", "MSFT"],
             ["AAPL", "JPM", "XOM", "JNJ", "NVDA", "TSLA", "META", "AMZN"]):
    run(f"Risk · correlation x{len(syms)}",
        page("Risk & Portfolio", "Risk", preset="5Y", risk_corr_syms=syms))

print("=== portfolio baskets ===")
for syms in ([], ["AAPL"], ["META", "TSLA"], ["AAPL", "MSFT", "NVDA", "AMZN"]):
    run(f"Portfolio · basket x{len(syms)}",
        page("Risk & Portfolio", "Portfolio", preset="10Y", pf_syms=syms))

print("=== the shortest window, where queries come back empty ===")
for section, view in (("Markets", "Sectors & Movers"), ("Markets", "Performance"),
                      ("Research", "History"), ("Risk & Portfolio", "Risk"),
                      ("Risk & Portfolio", "Portfolio")):
    run(f"{section} → {view} · 1M", page(section, view, preset="1M"))

print("=== stock journey: cursor positions and companies ===")
# The Journey's own edge is a cursor that is valid for one company and not for
# another. `jrn_slider` survives a company change in a real session, so the
# reset-on-change path has to hold for a 2010 listing viewed at a 2003 cursor.
for sym in ("AAPL", "TSLA", "META"):
    for cursor, when in ((dt.date(2003, 6, 30), "2003"),
                         (dt.date(2013, 1, 2), "2013"),
                         (dt.date(2026, 7, 1), "2026")):
        run(f"Journey · {sym} @ {when}",
            page("Research", "Journey", rs_symbol=sym, jrn_slider=cursor))

for speed in ("Slow", "Medium", "Fast"):
    run(f"Journey · speed {speed}", page("Research", "Journey", jrn_speed=speed))

# Playback mid-flight: the fragment advances the cursor and must not write a
# widget key that already exists this run.
run("Journey · playing", page("Research", "Journey", jrn_playing=True,
                              jrn_slider=dt.date(2010, 1, 4)))
# A cursor at the very first session, where most fact queries return nothing.
run("Journey · at the first session",
    page("Research", "Journey", rs_symbol="TSLA", jrn_slider=dt.date(2010, 6, 29)))

print("=== market intelligence: engine controls ===")
# Every horizon x objective generates a DIFFERENT scoring statement from the
# registry, so this is the only thing that executes them against the real panel.
# A weight edit that produces invalid SQL for one combination fails here.
for horizon in ranking.HORIZONS:
    run(f"Intelligence · {horizon}", page("Intelligence", mi_horizon=horizon))

for objective in ranking.OBJECTIVES:
    run(f"Intelligence · objective {objective}",
        page("Intelligence", mi_objective=objective))

for risk in ranking.RISK_BANDS:
    run(f"Intelligence · risk {risk}", page("Intelligence", mi_risk=risk))

# Filter combinations that legitimately return nothing must render an empty
# state rather than index into an empty board.
run("Intelligence · a filter set nothing can satisfy",
    page("Intelligence", mi_risk="Conservative", mi_sectors=["Energy"],
         mi_caps=["Small (<$2B)"]))
run("Intelligence · every cap band at once",
    page("Intelligence", mi_caps=list(ranking.CAP_BANDS)))
run("Intelligence · a single sector",
    page("Intelligence", mi_sectors=["Information Technology"]))
run("Intelligence · a stale sector filter",
    page("Intelligence", mi_sectors=["Not A Sector"]))

print("=== the landing page, and the network paths it does NOT depend on ===")
# The opportunities strip reads the same `da.rankings` the engine does, so it
# must render on the landing page even when the provider is unreachable. The
# sweep pins History for that reason; these are the cases that do hit the wire.
run("Research → Snapshot · default", page("Research", "Snapshot"))
run("Research → Snapshot · candles, 5Y",
    page("Research", "Snapshot", mi_view="Candles", mi_span="5Y"))
run("Research → Snapshot · intraday span",
    page("Research", "Snapshot", mi_span="1D"))
run("Research → Snapshot · a ticker outside the stored universe",
    page("Research", "Snapshot", rs_symbol="RKLB"))
run("Research · a typed search that resolves locally",
    page("Research", "History", rs_query="Micron"))
run("Research · a typed search that matches nothing",
    page("Research", "History", rs_query="qqqqqzzzz"))

print("=== developer center: every SQL Explorer entry executes ===")
for i, name in enumerate(queries.EXPLORER):
    run(f"Explorer · {name[:46]}", {"mode": "Developer", "sql_pick": i})

print()
print("FAILURES:", _failures)
sys.exit(1 if _failures else 0)

"""Smoke tests: every section renders, under every preset, without raising.

This is the failure class the other three suites cannot see. `tests_sqlguard`,
`tests_router` and `tests_portfolio` all test layers *below* the page — a query
can be correct, validated and fast while `app.py` still raises a `KeyError` on
the frame it returns, and nothing would catch it before the deploy.

`streamlit.testing.v1.AppTest` runs `app.py` in-process and drives widgets by
key: no browser, no extra dependency (it ships with Streamlit). Widget state is
set through `session_state` before `.run()`, which is how a section and a preset
get selected without clicking anything.

The cases are chosen to be the ones that have actually broken or that render an
edge path:

* every section x four presets, including `1M` — short windows are where a
  rolling or correlation query legitimately returns nothing and the page has to
  show an empty state rather than index into an empty frame;
* each rolling-return horizon, including one longer than some symbols' history;
* correlation selections of 0, 1, 2 and 8 symbols — below two it must not query;
* portfolio baskets of 0, 1, 2 and 4 holdings;
* the Developer Center with every SQL Explorer entry selected, which is the only
  thing that actually executes each showcased statement. A templated query left
  with an unfilled `{slot}` fails here and nowhere else.

**Cost:** a full run is a few minutes, because each case is a cold app start.
That is why it is a separate script rather than something to run on every edit —
give it its own CI step.

Run: ./.venv/bin/python tests_ui.py
"""

from __future__ import annotations

import ast
import pathlib
import sys

from streamlit.testing.v1 import AppTest

import queries

APP = str(pathlib.Path(__file__).parent / "app.py")
PRESETS = ["1M", "1Y", "10Y", "All Time"]


def _sections() -> list[str]:
    """Read `USER_SECTIONS` out of app.py rather than restating it here.

    A hardcoded copy silently stops covering a new section the moment one is
    added -- the sweep keeps passing while the new page is never rendered, which
    is the same drift that left the scope banner naming two sections after a
    third had been added. Parsed with `ast` because importing app.py would
    execute the whole Streamlit script.
    """
    tree = ast.parse(pathlib.Path(APP).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "USER_SECTIONS" for t in node.targets):
            return [ast.literal_eval(e) for e in node.value.elts]
    raise SystemExit("could not find USER_SECTIONS in app.py")


SECTIONS = _sections()
print(f"Sweeping {len(SECTIONS)} sections read from app.py: {', '.join(SECTIONS)}\n")

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
        detail = "; ".join(str(e.value)[:160] for e in at.exception)
        print(f"  [FAIL] {label}  {detail}")
    else:
        print(f"  [ok ] {label}")


print("=== every section, every preset ===")
for section in SECTIONS:
    for preset in PRESETS:
        state = {"section": section, "preset": preset}
        # Intelligence opens on Live Research, which fetches from the provider.
        # The preset sweep is about SQL paths, so it is pinned to the engine
        # view here and the network path gets its own labelled cases below --
        # otherwise a provider hiccup fails four cases that have nothing to do
        # with it, and the sweep stops being a signal about this codebase.
        if section == "Intelligence":
            state["mi_view_mode"] = "Intelligence Engine"
        run(f"{section} · {preset}", state)

print("=== rolling-return horizons ===")
for horizon in ("1Y", "3Y", "5Y", "10Y"):
    run(f"Performance · rolling {horizon}",
        {"section": "Performance", "preset": "All Time", "perf_roll": horizon})

print("=== correlation selections ===")
# Below two symbols the page must show a prompt, not build a 1x1 matrix.
for syms in ([], ["AAPL"], ["AAPL", "MSFT"],
             ["AAPL", "JPM", "XOM", "JNJ", "NVDA", "TSLA", "META", "AMZN"]):
    run(f"Risk · correlation x{len(syms)}",
        {"section": "Risk", "preset": "5Y", "risk_corr_syms": syms})

print("=== portfolio baskets ===")
for syms in ([], ["AAPL"], ["META", "TSLA"], ["AAPL", "MSFT", "NVDA", "AMZN"]):
    run(f"Portfolio · basket x{len(syms)}",
        {"section": "Portfolio", "preset": "10Y", "pf_syms": syms})

print("=== the shortest window, where queries come back empty ===")
for section in ("Market", "Companies", "Performance", "Risk", "Portfolio"):
    run(f"{section} · 1M", {"section": section, "preset": "1M"})

print("=== stock journey: cursor positions and companies ===")
# The Journey's own edge is a cursor that is valid for one company and not for
# another. `jrn_slider` survives a company change in a real session, so the
# reset-on-change path has to hold for a 2010 listing viewed at a 2003 cursor.
import datetime as dt  # noqa: E402  (test-local, not part of the app's imports)

for sym, label in (("AAPL — Apple Inc.", "AAPL"),
                   ("TSLA — Tesla, Inc.", "TSLA"),
                   ("META — Meta Platforms, Inc.", "META")):
    for cursor, when in ((dt.date(2003, 6, 30), "2003"),
                         (dt.date(2013, 1, 2), "2013"),
                         (dt.date(2026, 7, 1), "2026")):
        run(f"Journey · {label} @ {when}",
            {"section": "Journey", "jrn_symbol": sym, "jrn_slider": cursor})

for speed in ("Slow", "Medium", "Fast"):
    run(f"Journey · speed {speed}", {"section": "Journey", "jrn_speed": speed})

# Playback mid-flight: the fragment advances the cursor and must not write a
# widget key that already exists this run.
run("Journey · playing", {"section": "Journey", "jrn_playing": True,
                          "jrn_slider": dt.date(2010, 1, 4)})
# A cursor at the very first session, where most fact queries return nothing.
run("Journey · at the first session",
    {"section": "Journey", "jrn_symbol": "TSLA — Tesla, Inc.",
     "jrn_slider": dt.date(2010, 6, 29)})

print("=== market intelligence: engine controls ===")
# Every horizon x objective generates a DIFFERENT scoring statement from the
# registry, so this is the only thing that executes them against the real panel.
# A weight edit that produces invalid SQL for one combination fails here.
import ranking  # noqa: E402  (test-local)

for horizon in ranking.HORIZONS:
    run(f"Intelligence · {horizon}",
        {"section": "Intelligence", "mi_view_mode": "Intelligence Engine",
         "mi_horizon": horizon})

for objective in ranking.OBJECTIVES:
    run(f"Intelligence · objective {objective}",
        {"section": "Intelligence", "mi_view_mode": "Intelligence Engine",
         "mi_objective": objective})

for risk in ranking.RISK_BANDS:
    run(f"Intelligence · risk {risk}",
        {"section": "Intelligence", "mi_view_mode": "Intelligence Engine",
         "mi_risk": risk})

# Filter combinations that legitimately return nothing must render an empty
# state rather than index into an empty board.
run("Intelligence · a filter set nothing can satisfy",
    {"section": "Intelligence", "mi_view_mode": "Intelligence Engine",
     "mi_risk": "Conservative", "mi_sectors": ["Energy"],
     "mi_caps": ["Small (<$2B)"]})
run("Intelligence · every cap band at once",
    {"section": "Intelligence", "mi_view_mode": "Intelligence Engine",
     "mi_caps": list(ranking.CAP_BANDS)})
run("Intelligence · a single sector",
    {"section": "Intelligence", "mi_view_mode": "Intelligence Engine",
     "mi_sectors": ["Information Technology"]})

print("=== market intelligence: live research (hits the provider) ===")
# Isolated and labelled: these are the only cases in the suite that depend on an
# external service being reachable.
run("Intelligence · live research default", {"section": "Intelligence"})
run("Intelligence · live research, candles",
    {"section": "Intelligence", "mi_view": "Candles", "mi_span": "5Y"})
run("Intelligence · live research, intraday span",
    {"section": "Intelligence", "mi_span": "1D"})
run("Intelligence · a ticker outside the stored universe",
    {"section": "Intelligence", "mi_symbol": "RKLB"})

print("=== developer center: every SQL Explorer entry executes ===")
for i, name in enumerate(queries.EXPLORER):
    run(f"Explorer · {name[:46]}", {"mode": "Developer", "sql_pick": i})

print()
print("FAILURES:", _failures)
sys.exit(1 if _failures else 0)

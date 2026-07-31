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

import pathlib
import sys

from streamlit.testing.v1 import AppTest

import queries

APP = str(pathlib.Path(__file__).parent / "app.py")
SECTIONS = ["Overview", "Market", "Companies", "Performance", "Risk", "Portfolio", "About"]
PRESETS = ["1M", "1Y", "10Y", "All Time"]

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
        run(f"{section} · {preset}", {"section": section, "preset": preset})

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

print("=== developer center: every SQL Explorer entry executes ===")
for i, name in enumerate(queries.EXPLORER):
    run(f"Explorer · {name[:46]}", {"mode": "Developer", "sql_pick": i})

print()
print("FAILURES:", _failures)
sys.exit(1 if _failures else 0)

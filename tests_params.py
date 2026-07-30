"""Regression tests: extracted parameters must reach the executed SQL.

The bug these exist to catch is specific. The intent parser filled `limit`,
`sector`, `ordering` and a window, the handlers ignored all of them, and every
answer came back as the default ten rows over whatever the sidebar was showing.
Asserting on the parsed intent would have passed the whole time.

So these tests assert against the **SQL text and bind parameters actually handed
to SQLite**, captured by wrapping `data_access._read`. A test that only checked
the returned DataFrame could still pass if a handler sliced the result in pandas,
which is exactly the shape of the original defect.

Run: ./.venv/bin/python tests_params.py
"""

from __future__ import annotations

import datetime as dt
import re
import sys

import pandas as pd
import streamlit as st

import answers
import data_access as dal
import nlq
import params as prm
import router
from theme import PALETTES

PAL = PALETTES["Light"]
DIRECTORY = dal.directory()
SECTORS = sorted({str(s) for s in DIRECTORY["sector"].dropna().unique()})
DATA_MIN, DATA_MAX = dal.date_bounds("^GSPC")

# A UI window deliberately different from every window the questions ask for, so
# a test can only pass by honouring the question rather than coincidentally
# matching the default.
UI_START, UI_END, UI_PRESET = "2015-01-02", DATA_MAX.isoformat(), "UI-10Y"

_calls: list[tuple[str, dict]] = []
_real_read = dal._read


def _capture(sql, params=None):
    _calls.append((sql, dict(params or {})))
    return _real_read(sql, params)


dal._read = _capture

_failures = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _failures
    if not cond:
        _failures += 1
    print(f"  [{'ok ' if cond else 'FAIL'}] {label}{('  -> ' + detail) if detail else ''}")


def reset() -> None:
    """Clear both caches before a case.

    `data_access` memoizes on its arguments, so a second case asking for the same
    window and limit is served from cache and never calls `_read` -- correct in
    production, invisible to a capture-based test. Without this the assertions
    silently observe nothing and pass on an empty list.
    """
    _calls.clear()
    st.session_state.clear()
    st.cache_data.clear()


def run(question: str, intent: nlq.Intent) -> router.Route:
    """Route a question with the model stubbed to a known intent, then render it."""
    reset()
    nlq.available = lambda: True
    nlq.extract = lambda q, d: intent
    routed = router.route(question, DIRECTORY, ui_start=UI_START, ui_end=UI_END,
                          ui_preset=UI_PRESET, data_min=DATA_MIN, data_max=DATA_MAX)
    if routed.path == router.TEMPLATE_PATH:
        # Rendering is what pushes the parameters into data_access.
        answers.HANDLERS[routed.template.handler](DIRECTORY, PAL, routed.params)
    return routed


def ranked_calls() -> list[tuple[str, dict]]:
    """Only the ranked queries -- the ones parameters are supposed to reach."""
    return [(s, p) for s, p in _calls if "LIMIT :limit" in s]


def limits() -> list[int]:
    return [p["limit"] for _, p in ranked_calls()]


def sectors_bound() -> list[str]:
    return [p["sector"] for _, p in ranked_calls()]


def windows() -> list[tuple[str, str]]:
    return [(p["start"], p["end"]) for _, p in _calls if "start" in p and "end" in p]


def order_clause() -> str:
    for sql, _ in ranked_calls():
        m = re.search(r"ORDER BY\s+([a-z_]+\s+(?:ASC|DESC))", sql)
        if m:
            return m.group(1)
    return ""


def years_between(a: str, b: str) -> float:
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days / 365.25


# ---------------------------------------------------------------------------
print("=== \"Top 5 winners in the last year\" — limit 5, 1y window, UI ignored ===")
r = run("Top 5 winners in the last year",
        nlq.Intent(intent="biggest_winners", limit=5, lookback_days=365))
check("resolved limit is 5", r.params.limit == 5, str(r.params.limit))
check("limit came from the question", r.params.source("limit") == prm.QUESTION)
check("LIMIT 5 reached the SQL", 5 in limits(), str(limits()))
check("window came from the question", r.params.source("window") == prm.QUESTION)
check("window ignores the UI start", r.params.start != UI_START, r.params.start)
check("window spans ~1 year", 0.9 <= years_between(r.params.start, r.params.end) <= 1.1,
      f"{years_between(r.params.start, r.params.end):.2f}y")
check("SQL bound the question's window", (r.params.start, r.params.end) in windows())
check("no UI window reached the SQL", (UI_START, UI_END) not in windows())

print("=== \"Top winners\" — no parameters stated, UI window inherited ===")
r = run("Top winners", nlq.Intent(intent="biggest_winners"))
check("limit falls back to the default", r.params.limit == prm.DEFAULT_LIMIT, str(r.params.limit))
check("limit tagged as default", r.params.source("limit") == prm.DEFAULT)
check("window inherited from the UI", r.params.source("window") == prm.UI)
check("UI window reached the SQL", (UI_START, UI_END) in windows(), str(windows()[:2]))
check("default limit reached the SQL", prm.DEFAULT_LIMIT in limits(), str(limits()))

print("=== \"Top 10 technology companies over the last 3 years\" ===")
r = run("Top 10 technology companies over the last 3 years",
        nlq.Intent(intent="biggest_winners", limit=10, sector="technology", lookback_days=1095))
check("limit is 10", r.params.limit == 10, str(r.params.limit))
check("sector resolved to the data's name", r.params.sector == "Information Technology",
      r.params.sector)
check("sector came from the question", r.params.source("sector") == prm.QUESTION)
check("LIMIT 10 reached the SQL", 10 in limits(), str(limits()))
check("sector bound into the SQL", "Information Technology" in sectors_bound(),
      str(sectors_bound()))
check("window spans ~3 years", 2.9 <= years_between(r.params.start, r.params.end) <= 3.1,
      f"{years_between(r.params.start, r.params.end):.2f}y")
check("every returned row is in that sector",
      set(dal.movers_ranked(r.params.start, r.params.end, sector=r.params.sector,
                            limit=10)["sector"]) == {"Information Technology"})

print("=== parameters survive without a model (keyword tier reads the text) ===")
reset()
nlq.available = lambda: False
nlq.extract = lambda q, d: None
r = router.route("Top 5 winners in the last year", DIRECTORY, ui_start=UI_START,
                 ui_end=UI_END, ui_preset=UI_PRESET, data_min=DATA_MIN, data_max=DATA_MAX)
answers.HANDLERS[r.template.handler](DIRECTORY, PAL, r.params)
check("intent came from keywords", r.intent.source == "keywords", r.intent.source)
check("limit still 5", r.params.limit == 5, str(r.params.limit))
check("window still ~1 year", 0.9 <= years_between(r.params.start, r.params.end) <= 1.1)
check("LIMIT 5 still reached the SQL", bool(limits()) and 5 in limits(), str(limits()))

print("=== explicit dates beat a relative window ===")
r = run("winners between 2020-03-01 and 2021-03-01",
        nlq.Intent(intent="biggest_winners", start_date="2020-03-01", end_date="2021-03-01",
                   lookback_days=3650))
check("start honoured", r.params.start == "2020-03-01", r.params.start)
check("end honoured", r.params.end == "2021-03-01", r.params.end)
check("dates reached the SQL", ("2020-03-01", "2021-03-01") in windows())

print("=== a named year sets the window ===")
r = run("biggest winners since 2020", nlq.Intent(intent="biggest_winners"))
check("start is that January", r.params.start == "2020-01-01", r.params.start)
check("window tagged as from the question", r.params.source("window") == prm.QUESTION)

print("=== ordering reaches ORDER BY ===")
r = run("worst performers", nlq.Intent(intent="biggest_losers"))
check("losers sort ascending", order_clause() == "period_return ASC", order_clause())
r = run("best performers", nlq.Intent(intent="biggest_winners"))
check("winners sort descending", order_clause() == "period_return DESC", order_clause())
r = run("winners, lowest first", nlq.Intent(intent="biggest_winners", ordering="asc"))
check("explicit asc overrides the intent", order_clause() == "period_return ASC", order_clause())

print("=== metric selects the sort column ===")
r = run("most traded companies", nlq.Intent(intent="top_volume", metric="volume"))
check("volume sorts by turnover", order_clause() == "avg_dollar_volume DESC", order_clause())
r = run("top 3 by volume", nlq.Intent(intent="biggest_winners", metric="volume", limit=3))
check("metric overrides the default sort", order_clause() == "avg_dollar_volume DESC",
      order_clause())
check("and the limit still applies", 3 in limits(), str(limits()))

print("=== limit + sector reach the all-time leaderboard queries too ===")
for label, intent, want_order in [
    ("best_cagr", nlq.Intent(intent="best_cagr", limit=4, sector="Health Care"), "cagr DESC"),
    ("most_volatile", nlq.Intent(intent="most_volatile", limit=6), "ann_volatility DESC"),
    ("safest", nlq.Intent(intent="safest", limit=7), "ann_volatility ASC"),
    ("biggest_drawdown", nlq.Intent(intent="biggest_drawdown", limit=8), "max_drawdown ASC"),
]:
    r = run(label, intent)
    check(f"{label}: limit reached SQL", intent.limit in limits(), str(limits()))
    check(f"{label}: ORDER BY correct", order_clause() == want_order, order_clause())
    if intent.sector:
        check(f"{label}: sector bound", "Health Care" in sectors_bound(), str(sectors_bound()))

print("=== NULL sort values are excluded, not ranked first ===")
steady = dal.leaderboard_ranked(sort="volatility", ascending=True, limit=5)
check("no NULL volatility in the steadiest rows", steady["ann_volatility"].notna().all())
check("ascending really is ascending",
      list(steady["ann_volatility"]) == sorted(steady["ann_volatility"]))

print("=== company questions still resolve their symbols ===")
r = run("Compare Apple and Microsoft",
        nlq.Intent(intent="compare", companies=("AAPL", "MSFT")))
check("symbols resolved", r.symbols == ["AAPL", "MSFT"], str(r.symbols))
check("symbols tagged from the question", r.params.source("symbols") == prm.QUESTION)

print("=== the limit is capped ===")
r = run("top 5000 winners", nlq.Intent(intent="biggest_winners", limit=5000))
check(f"clamped to {prm.MAX_LIMIT}", r.params.limit == prm.MAX_LIMIT, str(r.params.limit))

print("=== provenance is recorded for the Developer Center ===")
r = run("Top 10 technology companies over the last 3 years",
        nlq.Intent(intent="biggest_winners", limit=10, sector="technology", lookback_days=1095))
summary = r.params.summary()
check("summary names the winning tier", "limit=10(question)" in summary, summary)
check("summary reaches the route log",
      st.session_state["route_log"][-1]["params"] == summary)

print()
print("FAILURES:", _failures)
sys.exit(1 if _failures else 0)

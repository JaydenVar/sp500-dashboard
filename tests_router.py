"""Router tests with a stubbed model.

These prove the plumbing, not the model: template preference over generated SQL,
rejection of hostile SQL, the per-session budget, and the fallback to the keyword
tier when the model is unavailable or errors. The model itself is stubbed, so
nothing here needs an API key or a network.

Parameter propagation is tested separately in tests_params.py.

Run: ./.venv/bin/python tests_router.py
"""

import sys

import streamlit as st

import data_access as dal
import nlq
import router

d = dal.directory()
DMIN, DMAX = dal.date_bounds('^GSPC')
UI = dict(ui_start='2015-01-02', ui_end=DMAX.isoformat(), ui_preset='UI-10Y', data_min=DMIN, data_max=DMAX)
fails = 0


def check(label, cond, extra=""):
    global fails
    if not cond:
        fails += 1
    print(f"  [{'ok ' if cond else 'FAIL'}] {label}{(' -> ' + str(extra)) if extra else ''}")


def stub(*, intent=None, sql=None, avail=True):
    nlq.available = lambda: avail
    nlq.extract = lambda q, dd: intent
    nlq.generate_sql = lambda q, s, h="": sql


def reset():
    st.session_state.clear()


GOOD_SQL = ("SELECT s.sector, COUNT(*) AS companies, MEDIAN(ss.cagr) AS median_cagr "
            "FROM symbol_stats ss JOIN symbols s ON s.symbol = ss.symbol "
            "WHERE ss.is_index = 0 GROUP BY s.sector ORDER BY median_cagr DESC LIMIT 20")

print("=== LLM intent that maps to a template -> template wins ===")
reset()
stub(intent=nlq.Intent(intent="biggest_winners", limit=5, period="the last 5 years", source="llm"),
     sql=GOOD_SQL)
r = router.route("bigest winers past 5 yrs", d, **UI)
check("path is Template Query", r.path == router.TEMPLATE_PATH, r.path)
check("template preferred over generated SQL", r.sql == "", r.sql[:40])
check("no generated-SQL budget consumed", router.generated_used() == 0)
check("limit carried through", r.intent.limit == 5)

print("=== custom intent -> generated SQL, validated and executed ===")
reset()
stub(intent=nlq.Intent(intent="custom", source="llm"), sql=GOOD_SQL)
r = router.route("median compound growth by sector", d, **UI)
check("path is AI Generated SQL", r.path == router.GENERATED_PATH, r.path)
check("dataframe returned", r.df is not None and not r.df.empty,
      None if r.df is None else f"{len(r.df)} rows")
check("budget consumed", router.generated_used() == 1)
check("sql exposed for Developer Center", bool(st.session_state.get("last_generated_sql")))
check("route logged with path", st.session_state["route_log"][-1]["path"] == router.GENERATED_PATH)
if r.df is not None:
    print("      sample:", r.df.head(3).to_dict("records"))

print("=== hostile generated SQL is refused ===")
for label, bad in [
    ("stacked DROP", "SELECT 1; DROP TABLE prices"),
    ("bare DELETE", "DELETE FROM prices"),
    ("unapproved table", "SELECT * FROM sqlite_master"),
    ("hallucinated column", "SELECT made_up FROM symbols"),
]:
    reset()
    stub(intent=nlq.Intent(intent="custom", source="llm"), sql=bad)
    r = router.route("q", d, **UI)
    check(f"{label} rejected", r.path == router.UNMATCHED_PATH and bool(r.error), r.reason)
    check(f"{label} still shown to devs", bool(st.session_state.get("last_generated_sql")))

print("=== session cap ===")
reset()
stub(intent=nlq.Intent(intent="custom", source="llm"), sql=GOOD_SQL)
for _ in range(router.MAX_GENERATED_PER_SESSION):
    router.route("q", d, **UI)
check("budget exhausted", router.budget_left() == 0, router.generated_used())
r = router.route("one more", d, **UI)
check("further requests refused", r.path == router.UNMATCHED_PATH, r.reason)
check("refusal is friendly", "limit" in r.error.lower(), r.error[:50])

print("=== model unavailable mid-flight -> keyword fallback still routes ===")
reset()
nlq.available = lambda: False
nlq.extract = lambda q, dd: None
r = router.route("Which sector performed best?", d, **UI)
check("fell back to keywords", r.path == router.TEMPLATE_PATH and r.intent.source == "keywords",
      f"{r.path}/{r.intent.source if r.intent else '-'}")

print("=== model returns None (API error) -> keyword fallback ===")
reset()
nlq.available = lambda: True
nlq.extract = lambda q, dd: None
r = router.route("Compare Apple and Microsoft.", d, **UI)
check("degraded to keywords", r.intent is not None and r.intent.source == "keywords")
check("symbols still resolved", r.symbols == ["AAPL", "MSFT"], r.symbols)

print()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)

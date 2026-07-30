"""Query Router — the single entry point for every business question.

One rule governs everything below: **an existing SQL template always wins.** The
templates are the queries the rest of the app runs, already tuned, already
materialized where it matters, already paired with a chart that reads correctly.
Generated SQL is the fallback for questions no template can answer -- never a
substitute for one that can.

    question
      -> intent           (nlq.extract, falling back to ask.match keywords)
      -> TEMPLATES hit?   -> answers.HANDLERS[...]   path: Template Query
      -> no template      -> nlq.generate_sql
                          -> sqlguard.validate       (rejects -> friendly error)
                          -> read-only execution     path: AI Generated SQL

Extending it is a dict entry. A new intent needs a name in `nlq.INTENTS`, a line
in `TEMPLATES`, and a handler in `answers.HANDLERS`; nothing in this module's
logic changes. That is the whole reason the registry is data rather than an
if-chain.

Every route is logged with the path it took, which is what the Developer Center
reads. User Mode never sees any of it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pandas as pd
import streamlit as st

import ask
import nlq
import queries
import sqlguard
from db import get_readonly_connection

# Ceiling on generated-SQL calls per browser session. Template hits and cached
# repeats don't count against it -- this bounds what a single visitor to a public
# deploy can spend, not what they can ask.
MAX_GENERATED_PER_SESSION = 10

TEMPLATE_PATH = "Template Query"
GENERATED_PATH = "AI Generated SQL"
UNMATCHED_PATH = "Unmatched"


@dataclass(frozen=True)
class Template:
    """One answerable question shape backed by existing SQL and an existing chart."""
    handler: str          # key into answers.HANDLERS
    label: str            # shown in the Developer Center route log
    queries: tuple[str, ...] = ()   # queries.py constants the handler reads


# Intent name -> template. Keys match `nlq.INTENTS`; `custom` is deliberately
# absent, which is what sends a question down the generated-SQL path.
TEMPLATES: dict[str, Template] = {
    "top_volume":       Template("top_volume", "Highest turnover", ("PERIOD_MOVERS",)),
    "biggest_winners":  Template("top_gainers", "Best performers", ("PERIOD_MOVERS",)),
    "biggest_losers":   Template("top_losers", "Worst performers", ("PERIOD_MOVERS",)),
    "biggest_drawdown": Template("biggest_drawdown", "Deepest drawdown",
                                 ("LEADERBOARD", "DRAWDOWN_IN_RANGE")),
    "most_volatile":    Template("most_volatile", "Most volatile", ("LEADERBOARD",)),
    "safest":           Template("least_volatile", "Steadiest", ("LEADERBOARD",)),
    "best_cagr":        Template("best_cagr", "Fastest compounder", ("LEADERBOARD",)),
    "compare":          Template("compare", "Company comparison", ("INDEXED_COMPARISON",)),
    "company_detail":   Template("company_detail", "Company detail",
                                 ("WINDOW_STATS", "PRICES_IN_RANGE")),
    "sector":           Template("sector", "Sector performance", ("SECTOR_PERFORMANCE",)),
    "market_summary":   Template("market_summary", "Market summary", ("WINDOW_STATS",)),
}


@dataclass
class Route:
    """What the router decided, and what came back if it executed anything."""
    question: str
    path: str = UNMATCHED_PATH
    intent: nlq.Intent | None = None
    template: Template | None = None
    sql: str = ""
    df: pd.DataFrame | None = None
    error: str = ""            # user-facing
    reason: str = ""           # technical, Developer Center only
    ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.path != UNMATCHED_PATH and not self.error

    @property
    def symbols(self) -> list[str]:
        if self.intent is None:
            return []
        return list(self.intent.companies or self.intent.comparison_targets)


# ---------------------------------------------------------------------------
# Session bookkeeping
# ---------------------------------------------------------------------------
def _log(route: Route) -> None:
    entries = st.session_state.setdefault("route_log", [])
    entries.append({
        "question": route.question,
        "path": route.path,
        "intent": route.intent.intent if route.intent else "-",
        "source": route.intent.source if route.intent else "-",
        "detail": route.template.label if route.template else (route.reason or "generated"),
        "ms": round(route.ms, 1),
    })
    del entries[:-20]


def generated_used() -> int:
    return int(st.session_state.get("generated_sql_calls", 0))


def budget_left() -> int:
    return max(0, MAX_GENERATED_PER_SESSION - generated_used())


# ---------------------------------------------------------------------------
# Intent resolution
# ---------------------------------------------------------------------------
def _from_keywords(question: str, directory) -> nlq.Intent | None:
    """Deterministic fallback. No typo tolerance, but no API key either."""
    matched, symbols, year = ask.match(question, directory)
    if matched is None:
        return None
    return nlq.Intent(
        intent=matched.name,
        companies=tuple(symbols),
        start_date=f"{year}-01-01" if year else "",
        period=f"since {year}" if year else "",
        question_restated=question,
        source="keywords",
    )


def resolve_intent(question: str, directory) -> nlq.Intent | None:
    """LLM first for typos, synonyms and number words; keywords when it can't run."""
    if nlq.available():
        parsed = nlq.extract(question, directory)
        if parsed is not None:
            # A `custom` verdict is a real answer, not a miss -- it routes to SQL.
            return parsed
    return _from_keywords(question, directory)


# ---------------------------------------------------------------------------
# Generated-SQL path
# ---------------------------------------------------------------------------
def _run_generated(route: Route, hint: str) -> None:
    if not nlq.available():
        route.error = (
            "I couldn't match that to a question I can answer yet. Try one of the "
            "suggestions above, or name a company."
        )
        route.reason = "no model available and no template matched"
        return

    if budget_left() <= 0:
        route.error = (
            "You've reached this session's limit for new questions. The suggestions "
            "above still work — or reload to start a fresh session."
        )
        route.reason = f"session cap reached ({MAX_GENERATED_PER_SESSION})"
        return

    sql = nlq.generate_sql(route.question, queries.SCHEMA_CARD, hint)
    st.session_state["generated_sql_calls"] = generated_used() + 1
    if not sql:
        route.error = "I couldn't turn that into a query. Try rephrasing it."
        route.reason = "model returned no SQL"
        return

    route.sql = sql
    # Kept for the Developer Center, which is the only place SQL is ever shown.
    # Stored even when validation fails -- a rejected statement is the more
    # interesting one to read.
    st.session_state["last_generated_sql"] = sql
    verdict = sqlguard.validate(sql)
    if not verdict.ok:
        route.error = verdict.message
        route.reason = verdict.reason
        return

    # Read-only connection: the guard has already refused anything that writes,
    # and this makes sure a write it somehow missed still cannot land.
    route.sql = verdict.sql
    conn = get_readonly_connection()
    try:
        route.df = pd.read_sql(verdict.sql, conn)
    except Exception as exc:
        route.error = "That query didn't run against this dataset. Try rephrasing it."
        route.reason = f"execution failed: {exc}"
        return
    finally:
        conn.close()

    if route.df is None or route.df.empty:
        route.error = "That returned no rows — the data may not cover it."
        route.reason = "empty result"
        return

    route.path = GENERATED_PATH


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def route(question: str, directory, *, window_note: str = "") -> Route:
    """Resolve a question to a template, or to validated generated SQL."""
    started = time.perf_counter()
    result = Route(question=question.strip())

    if not result.question:
        result.ms = (time.perf_counter() - started) * 1000
        return result

    result.intent = resolve_intent(result.question, directory)

    if result.intent is not None:
        template = TEMPLATES.get(result.intent.intent)
        if template is not None:
            result.template = template
            result.path = TEMPLATE_PATH
            result.ms = (time.perf_counter() - started) * 1000
            _log(result)
            return result

    _run_generated(result, window_note)
    result.ms = (time.perf_counter() - started) * 1000
    _log(result)
    return result

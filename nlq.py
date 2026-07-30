"""The Anthropic boundary — the only module that imports the SDK.

Three jobs, each of which returns `None` rather than raising when the model is
unavailable: read a question into a structured intent, write SQL when no template
fits, and summarize a result set into one business sentence.

Isolation is the point. The router below this can run its whole template path with
no API key, no network, and no `anthropic` install -- every function here answers
`None` and the caller falls back to the keyword matcher in `ask.py`. That matters
because this app deploys publicly on Streamlit Cloud, where a missing secret must
degrade the feature, not break the page.

Cost control lives here too: every call is memoized on its inputs, so a repeated
question costs nothing. The per-session cap on SQL generation is the router's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import streamlit as st

MODEL = "claude-opus-5"
_TTL = 3600

# Intent names the router has templates for. `custom` is the explicit escape
# hatch -- the model chooses it when nothing else fits, which is what routes a
# question to generated SQL. Keep in sync with `router.TEMPLATES`.
INTENTS = (
    "top_volume", "biggest_winners", "biggest_losers", "biggest_drawdown",
    "most_volatile", "safest", "best_cagr", "compare", "company_detail",
    "sector", "market_summary", "custom",
)

# Absent values are empty strings / zero / empty lists rather than null: the
# schema is strict (`additionalProperties: false`, everything required), and
# sentinels keep it that way without nullable unions.
_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "metric": {"type": "string", "description": "return, volume, volatility, drawdown, cagr, price, or empty"},
        "limit": {"type": "integer", "description": "How many rows the user asked for; 0 if unstated"},
        "companies": {"type": "array", "items": {"type": "string"},
                      "description": "Tickers from the catalog, in the order mentioned"},
        "sector": {"type": "string"},
        "start_date": {"type": "string", "description": "YYYY-MM-DD or empty"},
        "end_date": {"type": "string", "description": "YYYY-MM-DD or empty"},
        "period": {"type": "string", "description": "Plain-language window, e.g. 'the last 5 years'"},
        "ordering": {"type": "string", "enum": ["desc", "asc", ""]},
        "comparison_targets": {"type": "array", "items": {"type": "string"}},
        "question_restated": {"type": "string", "description": "The question, spelling corrected"},
    },
    "required": ["intent", "metric", "limit", "companies", "sector", "start_date",
                 "end_date", "period", "ordering", "comparison_targets", "question_restated"],
    "additionalProperties": False,
}

_EXTRACT_SYSTEM = """\
You read questions about a stock-market dashboard and return structured intent.

Correct typos and read around them ("bigest winers" is "biggest winners"). Treat \
singular and plural as the same. Convert number words to integers ("five" -> 5). \
Resolve company names, nicknames and misspellings to tickers from the catalog \
below, and use only tickers that appear in it.

Pick the single best `intent`:
  top_volume        most traded / highest turnover / most liquid
  biggest_winners   best performers, top gainers, what went up most
  biggest_losers    worst performers, biggest decliners
  biggest_drawdown  deepest fall from a high, worst crash
  most_volatile     riskiest, largest swings
  safest            steadiest, least volatile, most stable
  best_cagr         fastest compounder, best annualized growth
  compare           two or more named companies set against each other
  company_detail    one named company's performance
  sector            which sector or industry led or lagged
  market_summary    the index or the market overall
  custom            anything none of the above answers

Choose `custom` only when no listed intent fits -- it is more expensive to serve. \
A question that is one of the above but worded oddly is still that intent.

Set `limit` only when a count is actually stated. Leave `start_date`/`end_date` \
empty unless specific dates or a year are given; put relative windows ("past 5 \
years") in `period` instead.

Company catalog:
{catalog}"""

_SQL_SYSTEM = """\
You write a single SQLite SELECT answering a question about equity market data.

{schema}

Rules, all enforced by a validator that will reject your output:
  * Exactly one statement. No semicolon-separated statements.
  * SELECT or WITH only. Never INSERT, UPDATE, DELETE, DROP, ALTER, CREATE,
    PRAGMA, ATTACH or any other write.
  * Read only from the objects listed above. sqlite_master and meta are off limits.
  * Always LIMIT to at most 50 rows.
  * Alias every computed column to a readable name -- the result is shown to a
    non-technical reader as a table.
  * Return returns/volatility/drawdown as the raw fraction, not multiplied by 100.

Return only SQL. No prose, no markdown fence, no explanation."""

_INSIGHT_SYSTEM = """\
You write one short business insight about a result table from a stock dashboard.

Two or three sentences, plain language, for someone who knows markets but not \
this database. Lead with the finding. Quote the specific figures that support it. \
Do not describe the query, the columns, or how the data was produced, and do not \
give investment advice.

Note honestly when the data limits the reading -- unequal listing histories, \
price returns excluding dividends, a short window."""


@dataclass(frozen=True)
class Intent:
    """A question parsed into parameters the router can act on."""
    intent: str = "custom"
    metric: str = ""
    limit: int = 0
    companies: tuple[str, ...] = ()
    sector: str = ""
    start_date: str = ""
    end_date: str = ""
    period: str = ""
    ordering: str = ""
    comparison_targets: tuple[str, ...] = ()
    question_restated: str = ""
    source: str = "llm"          # "llm" or "keywords" -- shown in the Developer Center
    raw: dict = field(default_factory=dict, compare=False)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
def _api_key() -> str:
    """Resolve a key from Streamlit secrets, then the environment.

    `st.secrets` raises rather than returning empty when no secrets file exists at
    all, which is the normal state of a fresh clone -- so this is a try, not a get.
    """
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY", "")
        if key:
            return str(key)
    except Exception:
        pass
    import os
    return os.environ.get("ANTHROPIC_API_KEY", "")


@st.cache_resource(show_spinner=False)
def _client():
    """The SDK client, or None if unusable. Cached so a miss is diagnosed once."""
    if not _api_key():
        return None
    try:
        import anthropic
    except ImportError:
        return None
    try:
        return anthropic.Anthropic(api_key=_api_key(), timeout=30.0, max_retries=1)
    except Exception:
        return None


def available() -> bool:
    """Whether AI routing can run at all. False puts the app on the keyword path."""
    return _client() is not None


def _text(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
# Intent extraction
# ---------------------------------------------------------------------------
@st.cache_data(ttl=_TTL, show_spinner=False)
def _extract_raw(question: str, catalog: str) -> dict | None:
    client = _client()
    if client is None:
        return None
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=_EXTRACT_SYSTEM.format(catalog=catalog),
            # Classification, and it sits in front of a user waiting on a page
            # render -- low effort is the right trade here, not a cost dodge.
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": _INTENT_SCHEMA}},
            messages=[{"role": "user", "content": question}],
        )
        if response.stop_reason == "refusal":
            return None
        return json.loads(_text(response))
    except Exception:
        return None


def extract(question: str, directory) -> Intent | None:
    """Parse a question into an `Intent`, or None if the model is unavailable.

    Tickers the model returns are checked against the directory and dropped if
    unknown, so a hallucinated symbol can never reach a query.
    """
    known = {str(s).upper() for s in directory["symbol"]}
    catalog = "\n".join(
        f"  {r['symbol']} = {r['name']} ({r['sector']})"
        for _, r in directory.iterrows()
    )
    data = _extract_raw(question.strip(), catalog)
    if not data:
        return None

    def syms(key: str) -> tuple[str, ...]:
        out = []
        for v in data.get(key) or []:
            v = str(v).upper().strip()
            if v in known and v not in out:
                out.append(v)
        return tuple(out)

    intent = str(data.get("intent") or "custom")
    return Intent(
        intent=intent if intent in INTENTS else "custom",
        metric=str(data.get("metric") or ""),
        limit=max(0, min(int(data.get("limit") or 0), 50)),
        companies=syms("companies"),
        sector=str(data.get("sector") or ""),
        start_date=str(data.get("start_date") or ""),
        end_date=str(data.get("end_date") or ""),
        period=str(data.get("period") or ""),
        ordering=str(data.get("ordering") or ""),
        comparison_targets=syms("comparison_targets"),
        question_restated=str(data.get("question_restated") or question),
        source="llm",
        raw=data,
    )


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------
@st.cache_data(ttl=_TTL, show_spinner=False)
def generate_sql(question: str, schema_card: str, hint: str = "") -> str | None:
    """Write SQL for a question no template covers. Unvalidated -- the caller must
    pass it through `sqlguard.validate` before it goes anywhere near the database."""
    client = _client()
    if client is None:
        return None
    prompt = question if not hint else f"{question}\n\nContext: {hint}"
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=3000,
            system=_SQL_SYSTEM.format(schema=schema_card),
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            return None
        sql = _text(response)
        # Models fence SQL even when told not to; strip it rather than fail on it.
        if sql.startswith("```"):
            sql = sql.split("```")[1]
            if sql.lower().startswith("sql"):
                sql = sql[3:]
        return sql.strip() or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Insight
# ---------------------------------------------------------------------------
@st.cache_data(ttl=_TTL, show_spinner=False)
def insight(question: str, table_csv: str, note: str = "") -> str | None:
    """One business sentence about a result set, or None if unavailable."""
    client = _client()
    if client is None:
        return None
    body = f"Question: {question}\n\nResult:\n{table_csv}"
    if note:
        body += f"\n\nContext: {note}"
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=_INSIGHT_SYSTEM,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": body}],
        )
        if response.stop_reason == "refusal":
            return None
        return _text(response) or None
    except Exception:
        return None

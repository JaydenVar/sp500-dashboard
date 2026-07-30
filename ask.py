"""Ask the Market — natural-language questions routed to existing SQL.

No LLM. Each intent is a keyword pattern plus a handler that runs a real query
and returns a rendered answer: a headline sentence, supporting figures, and
often a chart or table. The user never sees SQL -- this lives in User Mode.

Design notes worth defending in an interview:

* **Intents are scored, not first-matched.** A question mentioning both "volume"
  and "biggest" should resolve to the volume intent, not whichever rule happened
  to be first in the list.
* **Symbol extraction is explicit.** Tickers and company names are matched
  against the universe, so "Compare Apple and Microsoft" resolves without the
  user knowing that AAPL and MSFT exist.
* **A miss is honest.** Unmatched input returns suggestions rather than guessing
  and returning a confidently wrong answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

# Words that are never useful signal when matching a company name.
_STOP = {
    "the", "a", "an", "and", "or", "vs", "versus", "compare", "comparison",
    "show", "me", "what", "which", "who", "how", "was", "were", "is", "are",
    "had", "has", "have", "biggest", "largest", "highest", "lowest", "best",
    "worst", "most", "least", "since", "from", "in", "of", "for", "to", "stock",
    "stocks", "company", "companies", "with", "that", "did", "over", "on",
}


@dataclass
class Intent:
    """One answerable question shape."""
    name: str
    keywords: tuple[tuple[str, ...], ...]      # each inner tuple = alternatives (OR)
    handler: str                               # dispatch key resolved in app code
    example: str
    needs_symbols: int = 0                     # how many symbols the answer needs
    weight: float = 1.0
    extras: dict = field(default_factory=dict)


INTENTS: list[Intent] = [
    Intent("top_volume",
           (("volume", "traded", "trading", "liquid", "turnover"),
            ("most", "highest", "biggest", "largest", "top")),
           "top_volume", "What stock had the highest trading volume?"),
    Intent("biggest_winners",
           (("winner", "winners", "gainer", "gainers", "best", "grew", "growth",
             "rose", "up", "performer", "performers"),),
           "top_gainers", "Show me the biggest winners since 2020."),
    Intent("biggest_losers",
           (("loser", "losers", "decliner", "decliners", "worst", "fell",
             "dropped", "down"),),
           "top_losers", "Which companies performed worst?"),
    Intent("biggest_drawdown",
           (("drawdown", "crash", "collapse", "fell furthest", "decline"),
            ("biggest", "largest", "worst", "deepest", "most")),
           "biggest_drawdown", "What company had the biggest drawdown?",
           weight=1.4),
    Intent("most_volatile",
           (("volatile", "volatility", "risky", "riskiest", "swing", "swings"),),
           "most_volatile", "Which stock has been the most volatile?"),
    Intent("safest",
           (("safest", "stable", "steadiest", "least volatile", "calmest"),),
           "least_volatile", "Which company was the most stable?"),
    Intent("best_cagr",
           (("cagr", "compound", "compounded", "annualized", "annual growth"),),
           "best_cagr", "Which companies compounded fastest?", weight=1.3),
    Intent("compare",
           (("compare", "versus", "vs", "against", "difference between"),),
           "compare", "Compare Apple and Microsoft.", needs_symbols=2, weight=1.6),
    Intent("company_detail",
           (("how did", "tell me about", "performance of", "about"),),
           "company_detail", "How did Nvidia perform?", needs_symbols=1),
    Intent("sector",
           (("sector", "sectors", "industry group"),),
           "sector", "Which sector performed best?", weight=1.3),
    Intent("market_summary",
           (("market", "overall", "index", "s&p", "sp500", "summary", "breadth"),),
           "market_summary", "How has the market done overall?"),
]

EXAMPLES = [
    "What stock had the highest trading volume?",
    "Compare Apple and Microsoft.",
    "Show me the biggest winners since 2020.",
    "What company had the biggest drawdown?",
    "Which sector performed best?",
    "Which stock has been the most volatile?",
]


def extract_symbols(text: str, directory: pd.DataFrame) -> list[str]:
    """Find symbols named by ticker or company name, in the order mentioned."""
    lowered = f" {text.lower()} "
    found: list[tuple[int, str]] = []

    for _, row in directory.iterrows():
        sym = str(row["symbol"])
        # Ticker as a standalone word ("AAPL", not the "aapl" inside a word).
        m = re.search(rf"\b{re.escape(sym.lower())}\b", lowered)
        pos = m.start() if m else None

        # Company name: match the distinctive leading word ("apple", "nvidia"),
        # skipping corporate suffixes that would match everything.
        name = str(row["name"]).lower()
        for token in re.split(r"[\s,.]+", name):
            token = token.strip()
            if len(token) < 3 or token in {"inc", "corp", "corporation", "the",
                                           "company", "co", "group", "platforms",
                                           "incorporated", "holdings", "index"}:
                continue
            m2 = re.search(rf"\b{re.escape(token)}\b", lowered)
            if m2 and (pos is None or m2.start() < pos):
                pos = m2.start()
            break  # only the first distinctive token

        if pos is not None:
            found.append((pos, sym))

    found.sort()
    out: list[str] = []
    for _, sym in found:
        if sym not in out:
            out.append(sym)
    return out


def extract_year(text: str) -> int | None:
    """A 4-digit year mentioned in the question, e.g. 'since 2020'."""
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return int(m.group(0)) if m else None


def match(text: str, directory: pd.DataFrame) -> tuple[Intent | None, list[str], int | None]:
    """Resolve free text to (intent, symbols, year).

    Every intent is scored and the best wins, rather than taking the first rule
    that matches -- "biggest drawdown" contains both a superlative and a
    drawdown term, and only the second one identifies the right answer.
    """
    lowered = text.lower().strip()
    if not lowered:
        return None, [], None

    symbols = extract_symbols(text, directory)
    year = extract_year(text)

    best, best_score = None, 0.0
    for intent in INTENTS:
        groups_hit = sum(
            1 for group in intent.keywords
            if any(re.search(rf"\b{re.escape(k)}\b", lowered) for k in group)
        )
        if groups_hit == 0:
            continue
        # Require every keyword group to hit for multi-group intents: those are
        # the specific ones ("biggest" AND "drawdown") and a partial match there
        # is usually a different question.
        if groups_hit < len(intent.keywords):
            continue
        score = groups_hit * intent.weight
        if intent.needs_symbols and len(symbols) >= intent.needs_symbols:
            score += 1.5
        elif intent.needs_symbols:
            score -= 1.0
        if score > best_score:
            best, best_score = intent, score

    # A bare company name with no other signal is a company question.
    if best is None and symbols:
        best = next(i for i in INTENTS if i.name == "company_detail")

    return best, symbols, year

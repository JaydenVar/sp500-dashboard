"""Parameter precedence — question over UI over default.

The intent parser produces what the *question* asked for. The sidebar produces
what the *screen* is currently showing. Neither is authoritative on its own, and
the resolution between them is the whole content of this module:

    1. a value stated in the question wins
    2. otherwise the current UI filter
    3. otherwise the application default

Every resolved field records which tier it came from, because "why did that
answer use a five-year window" is the first question anyone asks of a result that
surprises them, and the Developer Center can then answer it exactly.

A deterministic parser backs the model up. It reads counts ("top 5", "top five")
and relative windows ("past 3 years") straight from the text, and fills any field
the model left empty -- so the keyword tier honours parameters too, and a model
that returns a partial intent degrades to a worse answer rather than a wrong one.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

DEFAULT_LIMIT = 10
MAX_LIMIT = 50

QUESTION = "question"
UI = "ui"
DEFAULT = "default"

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "twentyfive": 25, "thirty": 30, "fifty": 50,
}
# Abbreviations included because people type them ("past 5 yrs") and the keyword
# tier has no model to normalize them.
_UNIT_DAYS = {
    "day": 1, "d": 1, "week": 7, "wk": 7, "month": 30, "mo": 30,
    "quarter": 91, "qtr": 91, "year": 365, "yr": 365,
}
_NUM = r"\d{1,3}|" + "|".join(_NUMBER_WORDS)
_SUPERLATIVE = (r"top|best|worst|first|bottom|biggest|largest|highest|lowest|most"
                r"|strongest|weakest|leading")

_COUNT_RE = re.compile(
    # "top 5" / "biggest five" -- superlative then count
    rf"\b(?:{_SUPERLATIVE})\s+({_NUM})\b"
    # "5 best" / "five worst" -- count then superlative
    rf"|\b({_NUM})\s+(?:{_SUPERLATIVE})\b",
    re.IGNORECASE,
)
_LOOKBACK_RE = re.compile(
    rf"\b(?:last|past|previous|trailing|over|in|within|during)\s+"
    rf"(?:the\s+)?(?:last\s+|past\s+|previous\s+)?"
    rf"({_NUM})?\s*"
    rf"({'|'.join(_UNIT_DAYS)})s?\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _as_int(token: str | None) -> int:
    if not token:
        return 0
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token.replace(" ", ""), 0)


def parse_count(text: str) -> int:
    """"top 5" / "five best" -> 5. Zero when the question states no count."""
    m = _COUNT_RE.search(text or "")
    if not m:
        return 0
    return _as_int(m.group(1) or m.group(2))


def parse_lookback_days(text: str) -> int:
    """"past 3 years" -> 1095. "last year" -> 365. Zero when none is stated."""
    m = _LOOKBACK_RE.search(text or "")
    if not m:
        return 0
    count = _as_int(m.group(1)) or 1     # "last year" means one year
    return count * _UNIT_DAYS[m.group(2).lower()]


def parse_year(text: str) -> int:
    m = _YEAR_RE.search(text or "")
    return int(m.group(0)) if m else 0


def match_sector(value: str, known: list[str]) -> str:
    """Resolve a loosely-named sector to the exact name used in the data.

    Users say "technology" and "tech"; the data says "Information Technology".
    Matching is by containment in either direction, longest candidate first, so
    "Consumer Discretionary" is not shadowed by a prefix of "Consumer Staples".
    """
    v = (value or "").strip().lower()
    if not v:
        return ""
    for name in sorted(known, key=len, reverse=True):
        low = name.lower()
        if v == low or v in low or low in v:
            return name
    # "tech" -> "Information Technology": fall back to a word-stem match.
    for name in sorted(known, key=len, reverse=True):
        if any(w.startswith(v) or v.startswith(w) for w in name.lower().split()):
            return name
    return ""


@dataclass(frozen=True)
class Params:
    """What a question actually resolved to, and where each value came from."""
    start: str
    end: str
    preset: str
    limit: int = DEFAULT_LIMIT
    symbols: tuple[str, ...] = ()
    sector: str = ""
    metric: str = ""
    ordering: str = ""
    sources: dict[str, str] = field(default_factory=dict, compare=False)

    def source(self, key: str) -> str:
        return self.sources.get(key, DEFAULT)

    def summary(self) -> str:
        """One line for the Developer Center route log."""
        bits = [f"limit={self.limit}({self.source('limit')})",
                f"window={self.preset}({self.source('window')})"]
        if self.sector:
            bits.append(f"sector={self.sector}({self.source('sector')})")
        if self.symbols:
            bits.append(f"symbols={','.join(self.symbols)}")
        if self.metric:
            bits.append(f"metric={self.metric}")
        if self.ordering:
            bits.append(f"order={self.ordering}")
        return " · ".join(bits)


def resolve(intent, *, question: str, ui_start: str, ui_end: str, ui_preset: str,
            data_min: dt.date, data_max: dt.date, sectors: list[str]) -> Params:
    """Merge a parsed intent with the current UI state under the precedence rules."""
    sources: dict[str, str] = {}
    text = question or ""

    # --- limit -------------------------------------------------------------
    # No UI control sets a row count, so this is question-or-default. The text
    # parser is the backstop for a model that returned the intent but not the count.
    stated = getattr(intent, "limit", 0) or parse_count(text)
    if stated > 0:
        limit, sources["limit"] = min(int(stated), MAX_LIMIT), QUESTION
    else:
        limit, sources["limit"] = DEFAULT_LIMIT, DEFAULT

    # --- window ------------------------------------------------------------
    start, end, preset = ui_start, ui_end, ui_preset
    sources["window"] = UI

    i_start = (getattr(intent, "start_date", "") or "").strip()
    i_end = (getattr(intent, "end_date", "") or "").strip()
    lookback = int(getattr(intent, "lookback_days", 0) or 0) or parse_lookback_days(text)
    year = parse_year(text)

    def clamp(d: dt.date) -> dt.date:
        return max(data_min, min(d, data_max))

    if i_start or i_end:
        # Explicit dates are the strongest signal the question can carry.
        s = _to_date(i_start) or data_min
        e = _to_date(i_end) or data_max
        start, end = clamp(s).isoformat(), clamp(e).isoformat()
        preset = f"{start} to {end}"
        sources["window"] = QUESTION
    elif lookback > 0:
        # Relative windows run back from the newest session in the data, not from
        # today -- "the last year" means the last year the market actually traded.
        s = clamp(data_max - dt.timedelta(days=lookback))
        start, end = s.isoformat(), data_max.isoformat()
        preset = _lookback_label(lookback)
        sources["window"] = QUESTION
    elif year:
        s = clamp(dt.date(year, 1, 1))
        start, end, preset = s.isoformat(), ui_end, f"since {year}"
        sources["window"] = QUESTION

    # --- entities ----------------------------------------------------------
    symbols = tuple(getattr(intent, "companies", ()) or
                    getattr(intent, "comparison_targets", ()) or ())
    sources["symbols"] = QUESTION if symbols else DEFAULT

    sector = match_sector(getattr(intent, "sector", "") or "", sectors)
    if not sector:
        sector = match_sector(_bare_sector_mention(text, sectors), sectors)
    sources["sector"] = QUESTION if sector else DEFAULT

    metric = (getattr(intent, "metric", "") or "").strip().lower()
    sources["metric"] = QUESTION if metric else DEFAULT

    ordering = (getattr(intent, "ordering", "") or "").strip().lower()
    if ordering not in ("asc", "desc"):
        ordering = ""
    sources["ordering"] = QUESTION if ordering else DEFAULT

    return Params(start=start, end=end, preset=preset, limit=limit, symbols=symbols,
                  sector=sector, metric=metric, ordering=ordering, sources=sources)


def _to_date(stamp: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(stamp[:10])
    except (ValueError, TypeError):
        return None


def _lookback_label(days: int) -> str:
    if days % 365 == 0 and days >= 365:
        n = days // 365
        return "the last year" if n == 1 else f"the last {n} years"
    if days % 30 == 0 and days >= 30:
        n = days // 30
        return "the last month" if n == 1 else f"the last {n} months"
    return f"the last {days} days"


def _bare_sector_mention(text: str, sectors: list[str]) -> str:
    """Catch a sector named in the question that the model didn't extract."""
    low = (text or "").lower()
    for name in sorted(sectors, key=len, reverse=True):
        if name.lower() in low:
            return name
    for alias, name in (("tech", "Information Technology"),
                        ("healthcare", "Health Care"), ("health", "Health Care"),
                        ("financial", "Financials"), ("bank", "Financials"),
                        ("energy", "Energy"), ("industrial", "Industrials"),
                        ("utility", "Utilities"), ("utilities", "Utilities"),
                        ("staples", "Consumer Staples"),
                        ("discretionary", "Consumer Discretionary"),
                        ("materials", "Materials"), ("real estate", "Real Estate"),
                        ("communication", "Communication Services")):
        if re.search(rf"\b{re.escape(alias)}", low) and name in sectors:
            return name
    return ""

"""Market timeline: the events that explain the shapes in the charts.

A 25-year price chart has craters in it. Without labels a reader has to already
know what 2008 and March 2020 look like; with them the chart explains itself.

Dates are the market-defining session, not the first headline -- the day the
chart actually turns. Each entry carries a short label for the marker and a
longer note for the hover, so the annotation teaches rather than just points.
"""

from __future__ import annotations

import datetime as dt

# (date, short label, category, hover explanation)
# Categories drive color: "crisis" = drawdown, "recovery" = turning point,
# "policy" = central-bank / macro, "milestone" = market structure.
EVENTS: list[tuple[str, str, str, str]] = [
    ("2002-10-09", "Dot-com bottom", "recovery",
     "The S&P 500 bottomed at 776 after falling 49% from its 2000 peak. It took "
     "seven years to regain the previous high."),
    ("2007-10-09", "Pre-crisis peak", "milestone",
     "The market topped at 1,565 before the financial crisis. Anyone buying here "
     "waited until 2013 to break even."),
    ("2008-09-15", "Lehman collapse", "crisis",
     "Lehman Brothers filed for bankruptcy — the largest in US history. Credit "
     "markets froze and the index fell 46% over the following six months."),
    ("2009-03-09", "Financial crisis bottom", "recovery",
     "The index closed at 676, down 57% from its 2007 peak — the deepest drawdown "
     "in this dataset. It marked the start of an 11-year bull market."),
    ("2010-05-06", "Flash Crash", "milestone",
     "The market fell roughly 9% within minutes and largely recovered the same "
     "day, exposing how fragile automated liquidity had become."),
    ("2011-08-05", "US credit downgrade", "crisis",
     "S&P cut the US sovereign rating from AAA for the first time. The index fell "
     "about 17% over the surrounding weeks."),
    ("2015-08-24", "China devaluation shock", "crisis",
     "A surprise yuan devaluation triggered a global selloff; the index fell more "
     "than 10% in a matter of days."),
    ("2018-12-24", "Rate-hike selloff", "crisis",
     "A tightening cycle plus trade tensions produced the worst December since "
     "1931, taking the index close to a 20% decline."),
    ("2020-02-19", "Pre-COVID peak", "milestone",
     "The last high before the pandemic. What followed was the fastest 30% "
     "decline in market history."),
    ("2020-03-23", "COVID crash bottom", "recovery",
     "The index bottomed 34% below its February high after just 23 trading days "
     "— then recovered the entire loss within five months."),
    ("2022-01-03", "Inflation-era peak", "milestone",
     "The market peaked as inflation hit 40-year highs, before the most "
     "aggressive Federal Reserve tightening cycle since the 1980s."),
    ("2022-10-12", "Inflation bear bottom", "recovery",
     "The index bottomed 25% below its January high as inflation began easing "
     "and rate expectations peaked."),
]


def in_range(start: str | dt.date, end: str | dt.date) -> list[tuple[str, str, str, str]]:
    """Events falling inside a window, oldest first."""
    s = start.isoformat() if isinstance(start, dt.date) else str(start)
    e = end.isoformat() if isinstance(end, dt.date) else str(end)
    return [ev for ev in EVENTS if s <= ev[0] <= e]


def category_color(category: str, pal: dict) -> str:
    return {
        "crisis": pal["down"],
        "recovery": pal["up"],
        "policy": pal["series"][3],
        "milestone": pal["muted"],
    }.get(category, pal["muted"])

"""The USER-facing About page: what the numbers mean, never how it is built.

Architecture, SQL, schemas and timings all live in the Developer Center. Mixing
those into an end-user screen is what makes an application read as a class
project rather than a product.
"""

from __future__ import annotations

import streamlit as st

import components as ui
from pagectx import Ctx


def render(ctx: Ctx) -> None:
    ui.section("About this data", "What these numbers mean, and what they don't")

    ui.kpi_cards([
        {"icon": "\U0001F4C5", "label": "History", "value": "25 years",
         "foot": "Daily bars since 2001"},
        {"icon": "\U0001F3E2", "label": "Companies", "value": "49",
         "foot": "Large-cap US equities"},
        {"icon": "\U0001F4C8", "label": "Benchmark", "value": "S&P 500", "small": True,
         "foot": "Index used throughout"},
        {"icon": "\U0001F553", "label": "Updated", "value": ctx.last_date.strftime("%b %d, %Y"),
         "small": True, "foot": "Latest close in the dataset"},
    ])

    st.markdown(
        """
#### How to read the metrics

| Metric | What it tells you |
|---|---|
| **Return** | Simple price change between the first and last day of your window. |
| **CAGR** | The annual growth rate that compounds to that return. It makes windows of different lengths comparable. |
| **Volatility** | How much daily prices swing, annualized. Higher means a rougher ride, not necessarily a worse outcome. |
| **Max drawdown** | The deepest fall from a previous high. This is the loss you would have had to sit through. |
| **52-week range** | The highest and lowest price of the past year — context for where the price sits today. |
| **Moving average** | The average price over the last 50 or 200 sessions, used to read trend rather than noise. |
| **Intelligence score** | A 0–100 composite of a stock's percentile ranks on the metrics that matter for the chosen horizon. It describes position within the ranked universe, not a forecast. |

**Why return alone isn't enough.** Two companies can post the same CAGR while
one delivered it smoothly and the other through a 90% collapse and recovery.
Volatility and drawdown describe that difference, which is why they sit beside
every return figure here.

#### Two universes

The site draws on two separate sets of companies, and knowing which one you are
looking at explains why some figures are available and others are not.

- **The core 50** — the S&P 500 index plus 49 large-cap US equities, with 25
  years of daily history. Everything on Markets, Risk & Portfolio and
  Research → History and Journey comes from this set.
- **The ranked universe** — roughly 500 liquid US stocks with five years of
  daily bars and fundamentals pulled from SEC XBRL filings. This is what the
  Intelligence engine scores, and what Research → Snapshot reads company
  profiles, valuation and financials from.

Research → Snapshot works for *any* US-listed stock, because the live quote,
chart and news come from the price provider at request time. A company outside
the ranked universe simply shows no stored fundamentals, and one outside the
core 50 has a shorter recorded history — both are labelled where they appear
rather than left to look like missing data.

#### Important limitations

These genuinely affect how the numbers should be read:

- **Dividends are excluded.** All figures are price returns, so long-run results
  understate what a shareholder actually earned — meaningfully so for
  high-dividend companies.
- **Only companies that exist today are included.** Firms that failed or were
  acquired are absent, which flatters long-run averages. This is called
  survivorship bias and it affects every ranking on the site.
- **Company histories differ in length.** Meta lists in 2012, Tesla in 2010.
  All-time rankings show each company's start date and number of years so
  unequal periods are visible rather than hidden.
- **Sector figures are equal-weighted** and reported as the median member, so one
  very large company cannot stand in for its whole sector.
- **Sectors in the ranked universe come from SEC SIC codes**, a 1987
  classification mapped onto modern sector names. Close, but not official — and
  it matters, because valuation metrics are ranked within sector.
- **Fundamentals are as filed** and lag the market by up to a quarter.
- **Market status follows the regular schedule** (weekdays, 09:30-16:00 ET).
  Exchange holidays are not modelled.
- **Partial years are marked.** The first and last calendar years of the window
  are incomplete, so their bars are faded and asterisked.

#### Not investment advice

This is an analytical tool built to explore historical market data. Nothing here
is a recommendation to buy or sell any security. Past performance does not
predict future results.
"""
    )

    ui.section("Data source")
    ui.note(
        "Daily open, high, low, close and volume from Yahoo Finance. Company "
        "fundamentals and share counts come from SEC EDGAR XBRL filings. Sector "
        "and industry classifications for the core 50 are hand-maintained. Curious "
        "how it is built? Switch to Developer Center in the top-right."
    )

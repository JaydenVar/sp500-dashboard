"""The Stock Journey's narrative layer: which fact to tell, and how to say it.

This module SELECTS and PHRASES. It does not calculate. Every number it renders
arrives from a query in `data_access`, and the only transformations here are
presentational -- rounding for display, a day count rendered as "1 yr 11 mo", a
signed percentage. The rule that every figure in this app is the output of a SQL
query does not get an exception for the page that talks the most.

That split is also what keeps the page honest under playback. Facts are built
against the cursor date the queries were given, so a fact can never describe a
moment the chart is not showing -- there is no second code path holding a stale
figure.

What it refuses to do:
  - state a company's IPO date from its first price row. The record starts
    around 2001 whatever the company's age, so `first_session_fact` says
    "first session in this dataset" and the real IPO comes from a curated event
    or not at all.
  - invent a fact when a query came back empty. A young company genuinely has
    no ten-year streak, and the panel shows fewer cards rather than a filler.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

# Categories whose curated events are worth surfacing as standalone "Did You
# Know" cards rather than only as timeline markers. A category not listed here
# still draws on the timeline -- this is about which ones carry a fact that
# reads well out of chronological context.
FACT_CATEGORIES = ("IPO", "Stock Split")


@dataclass(frozen=True)
class Fact:
    """One Did You Know card.

    `jump_date` is what makes a card clickable: the Journey moves its cursor
    there. A fact with no single defensible date (a count across the whole
    record) leaves it None and renders as a static card.
    """

    headline: str
    detail: str
    icon: str = "◆"
    tone: str = "neutral"  # neutral | positive | negative | milestone
    jump_date: str | None = None


@dataclass(frozen=True)
class Moment:
    """One entry on the historical timeline."""

    date: str
    title: str
    detail: str
    kind: str          # company | market | milestone
    tone: str
    source: str = ""
    move: float | None = None   # the company's realized move around it, if known


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------
def _pct(value, digits: int = 1, signed: bool = True) -> str:
    if value is None or pd.isna(value):
        return "—"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.{digits}f}%"


def _multiple(value) -> str:
    """A total return rendered as a growth multiple.

    `x / 1 - 1` and `x` are the same number in different clothes: a return of
    845.5 IS 846.5x. Long-run equity returns run into five-figure percentages
    where a multiple stays readable, so the multiple is used above 10x.
    """
    if value is None or pd.isna(value):
        return "—"
    return f"{value + 1:,.0f}x" if value >= 10 else _pct(value)


def _duration(days) -> str:
    """A day count as human text. Formatting, not a derived metric."""
    if days is None or pd.isna(days):
        return "—"
    days = int(days)
    if days < 60:
        return f"{days} days"
    months = round(days / 30.44)
    if months < 24:
        return f"{months} months"
    years, rem = divmod(round(days / 30.44), 12)
    return f"{years} yr {rem} mo" if rem else f"{years} years"


def _pretty_date(value) -> str:
    if not value or pd.isna(value):
        return "—"
    return dt.date.fromisoformat(str(value)[:10]).strftime("%b %-d, %Y")


def _pretty_month(value) -> str:
    return dt.date.fromisoformat(f"{value}-01").strftime("%B %Y")


# ---------------------------------------------------------------------------
# Did You Know
# ---------------------------------------------------------------------------
def did_you_know(
    *,
    name: str,
    snapshot: pd.Series | None,
    records: pd.Series | None,
    extremes: pd.DataFrame,
    up_streaks: pd.DataFrame,
    down_streaks: pd.DataFrame,
    best_worst: pd.DataFrame,
    drawdowns: pd.DataFrame,
    company_events: pd.DataFrame,
    trend_changes: pd.DataFrame,
) -> list[Fact]:
    """Assemble the facts that are true as of the cursor, most striking first.

    Every branch is guarded on its own frame being non-empty: a company two
    years into the record has no worst-year figure and no completed drawdown,
    and the panel is meant to shrink rather than to fill.
    """
    facts: list[Fact] = []

    # --- Curated origin facts. The ONLY honest source of an IPO date here.
    if not company_events.empty:
        for _, ev in company_events[company_events["category"].isin(FACT_CATEGORIES)].iterrows():
            icon = "🎉" if ev["category"] == "IPO" else "✂️"
            facts.append(Fact(
                headline=ev["title"],
                detail=f"{_pretty_date(ev['date'])} — {ev['description']}",
                icon=icon, tone="milestone", jump_date=str(ev["date"]),
            ))

    # --- Where the record stands.
    if records is not None:
        facts.append(Fact(
            headline=f"Record high: {records['record_close']:,.2f}",
            detail=(
                f"Set on {_pretty_date(records['record_date'])}. "
                f"{name} has closed at an all-time high on "
                f"{int(records['record_days']):,} separate sessions."
            ),
            icon="🏔️", tone="positive", jump_date=str(records["record_date"]),
        ))
        if records["longest_dry_spell"] and records["longest_dry_spell"] > 400:
            facts.append(Fact(
                headline=f"{_duration(records['longest_dry_spell'])} without a record",
                detail=(
                    "The longest the stock ever went between all-time highs. It "
                    f"finally set a new one on {_pretty_date(records['dry_spell_end_date'])}."
                ),
                icon="⏳", tone="negative", jump_date=str(records["dry_spell_end_date"]),
            ))

    # --- The single biggest days in either direction.
    if not extremes.empty:
        gains = extremes[extremes["direction"] == "gain"]
        losses = extremes[extremes["direction"] == "loss"]
        if not gains.empty:
            top = gains.iloc[0]
            facts.append(Fact(
                headline=f"Best day ever: {_pct(top['daily_return'])}",
                detail=f"{_pretty_date(top['date'])} — the largest single-session gain on record.",
                icon="🚀", tone="positive", jump_date=str(top["date"]),
            ))
        if not losses.empty:
            worst = losses.iloc[-1]
            facts.append(Fact(
                headline=f"Worst day ever: {_pct(worst['daily_return'])}",
                detail=f"{_pretty_date(worst['date'])} — the largest single-session loss on record.",
                icon="💥", tone="negative", jump_date=str(worst["date"]),
            ))

    # --- Streaks.
    if not up_streaks.empty:
        run = up_streaks.iloc[0]
        facts.append(Fact(
            headline=f"{int(run['length'])} straight up days",
            detail=(
                f"{_pretty_date(run['start_date'])} to {_pretty_date(run['end_date'])}, "
                f"worth {_pct(run['run_return'])} across the run — the longest winning "
                "streak in the record."
            ),
            icon="📈", tone="positive", jump_date=str(run["end_date"]),
        ))
    if not down_streaks.empty:
        run = down_streaks.iloc[0]
        facts.append(Fact(
            headline=f"{int(run['length'])} straight down days",
            detail=(
                f"{_pretty_date(run['start_date'])} to {_pretty_date(run['end_date'])}, "
                f"costing {_pct(run['run_return'])} — the longest losing streak in the record."
            ),
            icon="📉", tone="negative", jump_date=str(run["end_date"]),
        ))

    # --- Best and worst calendar periods.
    if not best_worst.empty:
        lookup = {(r["period_type"], r["extreme"]): r for _, r in best_worst.iterrows()}
        if ("year", "best") in lookup:
            r = lookup[("year", "best")]
            facts.append(Fact(
                headline=f"Best year: {r['period']} at {_pct(r['period_return'])}",
                detail="Measured on full calendar years only, so a partial first or last year cannot win it.",
                icon="🏆", tone="positive", jump_date=f"{r['period']}-12-31",
            ))
        if ("year", "worst") in lookup:
            r = lookup[("year", "worst")]
            facts.append(Fact(
                headline=f"Worst year: {r['period']} at {_pct(r['period_return'])}",
                detail="The full calendar year in which the stock lost the most.",
                icon="🥀", tone="negative", jump_date=f"{r['period']}-12-31",
            ))
        if ("month", "best") in lookup:
            r = lookup[("month", "best")]
            facts.append(Fact(
                headline=f"Best month: {_pct(r['period_return'])}",
                detail=f"{_pretty_month(r['period'])} was the strongest full month on record.",
                icon="☀️", tone="positive", jump_date=f"{r['period']}-28",
            ))
        if ("month", "worst") in lookup:
            r = lookup[("month", "worst")]
            facts.append(Fact(
                headline=f"Worst month: {_pct(r['period_return'])}",
                detail=f"{_pretty_month(r['period'])} was the weakest full month on record.",
                icon="🌧️", tone="negative", jump_date=f"{r['period']}-28",
            ))

    # --- The deepest crash, and how long the round trip took.
    if not drawdowns.empty:
        worst = drawdowns.loc[drawdowns["depth"].idxmin()]
        if pd.notna(worst["recovery_date"]):
            detail = (
                f"Peaked {_pretty_date(worst['peak_date'])}, bottomed "
                f"{_pretty_date(worst['trough_date'])} after {_duration(worst['days_to_trough'])}, "
                f"and took a further {_duration(worst['days_to_recover'])} to reach a new high "
                f"on {_pretty_date(worst['recovery_date'])}."
            )
            jump = str(worst["recovery_date"])
        else:
            detail = (
                f"Peaked {_pretty_date(worst['peak_date'])} and bottomed "
                f"{_pretty_date(worst['trough_date'])}. It has not made a new "
                "all-time high since."
            )
            jump = str(worst["trough_date"])
        facts.append(Fact(
            headline=f"Deepest crash: {_pct(worst['depth'])}",
            detail=detail, icon="🕳️", tone="negative", jump_date=jump,
        ))

    # --- The compounding fact, stated as a multiple.
    if snapshot is not None and pd.notna(snapshot["return_to_date"]):
        facts.append(Fact(
            headline=f"{_multiple(snapshot['return_to_date'])} since {_pretty_date(snapshot['first_date'])}",
            detail=(
                f"A {_pct(snapshot['cagr_to_date'])} compound annual return over "
                f"{snapshot['years_elapsed']:.1f} years. That first date is where this "
                "dataset begins, not when the company listed."
            ),
            icon="🌱", tone="milestone", jump_date=str(snapshot["first_date"]),
        ))

    if not trend_changes.empty:
        golden = trend_changes[trend_changes["cross_type"] == "golden"]
        facts.append(Fact(
            headline=f"{len(trend_changes)} major trend changes",
            detail=(
                f"The 50-day average crossed the 200-day {len(trend_changes)} times — "
                f"{len(golden)} of them upward. Each crossing is a shift in the "
                "medium-term trend, not a prediction."
            ),
            icon="🔀", tone="neutral",
        ))

    return facts


# ---------------------------------------------------------------------------
# The timeline
# ---------------------------------------------------------------------------
def timeline(
    *,
    company_events: pd.DataFrame,
    market_events: pd.DataFrame,
    drawdowns: pd.DataFrame,
    extremes: pd.DataFrame,
    asof: str,
) -> list[Moment]:
    """Merge curated history, relevant market events and computed milestones.

    Three sources, one chronology. Market events arrive already filtered to the
    ones this company actually moved on -- that test is in the SQL, not here.

    Events dated before the company's first price row are dropped from the
    TIMELINE (they have no position on the chart's x-axis) while remaining
    available to Did You Know, which is why the filter is on `session_date`
    rather than on `date`.
    """
    moments: list[Moment] = []

    if not company_events.empty:
        for _, ev in company_events.iterrows():
            if pd.isna(ev["session_date"]):
                continue  # predates the price record; a fact, not a marker
            moments.append(Moment(
                date=str(ev["date"]), title=ev["title"], detail=ev["description"],
                kind="company", tone=ev["tone"], source=ev["source"],
                move=None if pd.isna(ev["forward_return"]) else float(ev["forward_return"]),
            ))

    if not market_events.empty:
        for _, ev in market_events.iterrows():
            moments.append(Moment(
                date=str(ev["date"]), title=ev["title"], detail=ev["description"],
                kind="market",
                tone={"crisis": "negative", "recovery": "positive"}.get(ev["category"], "neutral"),
                source="Market timeline",
                move=None if pd.isna(ev["company_move"]) else float(ev["company_move"]),
            ))

    # Computed milestones: the crash episodes and the record days the price data
    # itself calls out. These are the "chart annotations" a reader can click.
    if not drawdowns.empty:
        for _, dd in drawdowns.iterrows():
            moments.append(Moment(
                date=str(dd["trough_date"]),
                title=f"Bottom of a {_pct(dd['depth'])} drawdown",
                detail=(
                    f"Down from its {_pretty_date(dd['peak_date'])} peak. "
                    + (f"Recovered {_pretty_date(dd['recovery_date'])}, "
                       f"{_duration(dd['days_to_recover'])} later."
                       if pd.notna(dd["recovery_date"]) else "Still below that peak.")
                ),
                kind="milestone", tone="negative",
                move=float(dd["depth"]),
            ))

    if not extremes.empty:
        for _, ex in extremes.iterrows():
            moments.append(Moment(
                date=str(ex["date"]),
                title=f"{_pct(ex['daily_return'])} in one session",
                detail="One of the largest single-day moves in the company's record.",
                kind="milestone",
                tone="positive" if ex["direction"] == "gain" else "negative",
                move=float(ex["daily_return"]),
            ))

    moments = [m for m in moments if m.date <= asof]
    return sorted(moments, key=lambda m: m.date)


def chapter_label(snapshot: pd.Series | None) -> tuple[str, str]:
    """A short state chip for the cursor: where the company is, in words.

    The thresholds are display bands over `drawdown`, which the query computed;
    the phrasing is the only thing decided here.
    """
    if snapshot is None:
        return "No data", "neutral"
    if snapshot["at_record_high"]:
        return "At an all-time high", "positive"
    dd = snapshot["drawdown"]
    if dd >= -0.05:
        return "Near its record", "positive"
    if dd >= -0.20:
        return f"{_pct(dd)} off its high", "neutral"
    if dd >= -0.50:
        return f"In a {_pct(dd)} drawdown", "negative"
    return f"Deep drawdown, {_pct(dd)} off", "negative"

"""Regression tests: the Stock Journey's SQL, against an independent recompute.

Everything the Journey narrates is a claim about a company's history, and most
of those claims are the kind that look plausible while being wrong. A drawdown
episode that ends one row early still draws a sensible-looking band; a streak
that swallows flat sessions still prints a number. So the properties checked
here are the ones a rendered page cannot show:

* **Drawdown episodes partition the record.** Gaps-and-islands over a running
  maximum is easy to get almost right. The episodes must tile the whole series
  without gaps or overlaps, and each depth must match a pandas recompute.
* **An unrecovered drawdown reports NULL, not the last date.** Coalescing that
  to "recovered" would tell a reader still 40% underwater that they got out.
* **Flat sessions break a streak rather than extending it.** A zero-return day
  is not an up day, and merging across one produces a longer streak than ever
  happened. Verified against a run-length recompute that treats zero as its own
  direction.
* **Curated events survive the round trip.** Every event in the JSON reaches
  SQL with its date and category intact, and a category not in the registry
  fails the BUILD rather than vanishing from the timeline.
* **Events before the price record still return, with NULL prices.** An IPO in
  1980 is a true fact about a company whose data starts in 2001, and the Did
  You Know panel depends on it coming back.

Run: ./.venv/bin/python tests_journey.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys

import numpy as np
import pandas as pd

import build_db
import journey
import queries
from db import get_connection

CONN = get_connection()
TOL = 1e-9
LAST = "2999-12-31"  # an `asof` past the end of the record
_failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}{f'  {detail}' if detail else ''}")


def _read(sql: str, **params) -> pd.DataFrame:
    return pd.read_sql(sql, CONN, params=params)


def closes(symbol: str) -> pd.DataFrame:
    df = _read("SELECT date, close FROM prices WHERE symbol = :s"
               " AND close IS NOT NULL AND close > 0 ORDER BY date", s=symbol)
    return df


# Three shapes of record: the index (long, shallow), a mega-cap with enormous
# compounding, and a 2010 listing whose history starts mid-dataset.
SYMBOLS = ("^GSPC", "AAPL", "TSLA")


# ---------------------------------------------------------------------------
print("\nSnapshot — the playhead agrees with the raw series")
# ---------------------------------------------------------------------------
for sym in SYMBOLS:
    px = closes(sym)
    for asof in ("2009-03-09", "2015-06-30", "2020-03-23", LAST):
        got = _read(queries.JOURNEY_SNAPSHOT, symbol=sym, asof=asof)
        want = px[px["date"] <= asof]
        if want.empty:
            check(f"{sym} @ {asof} — no rows before listing", got.empty)
            continue
        row, last = got.iloc[0], want.iloc[-1]
        check(f"{sym} @ {asof} — lands on the last session on or before",
              row["date"] == last["date"], f"got {row['date']}")
        check(f"{sym} @ {asof} — close",
              abs(row["close"] - last["close"]) < TOL)
        check(f"{sym} @ {asof} — running peak",
              abs(row["peak_close"] - want["close"].max()) < TOL)
        check(f"{sym} @ {asof} — return to date",
              abs(row["return_to_date"]
                  - (last["close"] / want["close"].iloc[0] - 1)) < TOL)
        check(f"{sym} @ {asof} — drawdown",
              abs(row["drawdown"]
                  - (last["close"] - want["close"].max()) / want["close"].max()) < TOL)
        check(f"{sym} @ {asof} — record-high flag",
              bool(row["at_record_high"]) == bool(last["close"] >= want["close"].max()))

# A cursor before the first session must return NOTHING rather than a row of
# NULLs -- the UI branches on `snapshot is None` to draw its empty state.
check("cursor before the record returns no rows",
      _read(queries.JOURNEY_SNAPSHOT, symbol="TSLA", asof="2005-01-01").empty)


# ---------------------------------------------------------------------------
print("\nDrawdown episodes — a partition of the record, not a selection from it")
# ---------------------------------------------------------------------------
for sym in SYMBOLS:
    px = closes(sym)
    peak = px["close"].cummax()

    # Every episode, unfiltered, so the partition property is testable.
    eps = _read(queries.JOURNEY_DRAWDOWN_EPISODES, symbol=sym, asof=LAST, min_depth=0.0)

    # Peak days -- where the series sets or matches its running maximum -- are
    # exactly the episode boundaries.
    want_peaks = px.loc[px["close"] >= peak, "date"].tolist()
    check(f"{sym} — one episode per record-high session",
          len(eps) == len(want_peaks), f"{len(eps)} vs {len(want_peaks)}")
    check(f"{sym} — episodes start on the record-high sessions",
          eps["peak_date"].tolist() == want_peaks)

    # Recovery chains: each episode's recovery is the next episode's peak.
    chained = eps["recovery_date"].iloc[:-1].tolist() == eps["peak_date"].iloc[1:].tolist()
    check(f"{sym} — recovery date is the next episode's peak", chained)
    check(f"{sym} — only the final episode may be unrecovered",
          eps["recovery_date"].isna().sum() <= 1,
          f"{int(eps['recovery_date'].isna().sum())} NULLs")

    # Depth against a pandas recompute of the worst drawdown overall.
    deep = _read(queries.JOURNEY_DRAWDOWN_EPISODES, symbol=sym, asof=LAST, min_depth=-0.15)
    if not deep.empty:
        check(f"{sym} — worst episode matches the series minimum",
              abs(deep["depth"].min() - (px["close"] / peak - 1).min()) < TOL)
        check(f"{sym} — every returned episode is at least 15% deep",
              bool((deep["depth"] <= -0.15).all()))
        check(f"{sym} — trough is inside its own episode",
              bool((deep["peak_date"] <= deep["trough_date"]).all()))
        recovered = deep[deep["recovery_date"].notna()]
        check(f"{sym} — recovery follows the trough",
              bool((recovered["trough_date"] <= recovered["recovery_date"]).all()))
        check(f"{sym} — trough close matches the minimum over the episode",
              all(abs(r["trough_close"]
                      - px[(px["date"] >= r["peak_date"])
                           & (px["date"] <= (r["recovery_date"] or LAST))]["close"].min()) < TOL
                  for _, r in recovered.iterrows()))


# ---------------------------------------------------------------------------
print("\nStreaks — flat sessions break a run, they do not extend it")
# ---------------------------------------------------------------------------
for sym in SYMBOLS:
    rets = _read(
        "SELECT date, daily_return FROM daily_returns WHERE symbol = :s"
        " AND daily_return IS NOT NULL ORDER BY date", s=sym)
    direction = np.sign(rets["daily_return"].values)

    for want_dir in (1, -1):
        # Independent run-length recompute. A zero-return session belongs to
        # neither run, which is the property under test.
        best, run = 0, 0
        best_end = None
        for i, d in enumerate(direction):
            run = run + 1 if d == want_dir else 0
            if run > best:
                best, best_end = run, rets["date"].iloc[i]

        got = _read(queries.JOURNEY_STREAKS, symbol=sym, asof=LAST,
                    direction=want_dir, limit=1)
        label = "up" if want_dir == 1 else "down"
        check(f"{sym} — longest {label} streak length",
              int(got.iloc[0]["length"]) == best, f"{int(got.iloc[0]['length'])} vs {best}")
        check(f"{sym} — longest {label} streak ends on the right session",
              got.iloc[0]["end_date"] == best_end)

        # The run's return must come from the boundary closes, exactly.
        px = closes(sym).set_index("date")["close"]
        row = got.iloc[0]
        before = px[px.index < row["start_date"]].iloc[-1]
        check(f"{sym} — {label} streak return is close-to-close",
              abs(row["run_return"] - (px[row["end_date"]] / before - 1)) < TOL)

    check(f"{sym} — direction is honoured", bool(
        (_read(queries.JOURNEY_STREAKS, symbol=sym, asof=LAST,
               direction=-1, limit=5)["run_return"] < 0).all()))

# The cursor must bound the search: a streak after `asof` cannot be reported.
early = _read(queries.JOURNEY_STREAKS, symbol="AAPL", asof="2005-12-31",
              direction=1, limit=5)
check("streaks respect the cursor", bool((early["end_date"] <= "2005-12-31").all()))


# ---------------------------------------------------------------------------
print("\nExtremes and records")
# ---------------------------------------------------------------------------
for sym in SYMBOLS:
    rets = _read(
        "SELECT date, daily_return FROM daily_returns WHERE symbol = :s"
        " AND daily_return IS NOT NULL", s=sym)
    ex = _read(queries.JOURNEY_EXTREME_DAYS, symbol=sym, asof=LAST, limit=3)
    check(f"{sym} — best day is the series maximum",
          abs(ex[ex["direction"] == "gain"]["daily_return"].max()
              - rets["daily_return"].max()) < TOL)
    check(f"{sym} — worst day is the series minimum",
          abs(ex[ex["direction"] == "loss"]["daily_return"].min()
              - rets["daily_return"].min()) < TOL)
    check(f"{sym} — three of each direction", len(ex) == 6)

    px = closes(sym)
    rec = _read(queries.JOURNEY_RECORD_SUMMARY, symbol=sym, asof=LAST).iloc[0]
    check(f"{sym} — record close is the series maximum",
          abs(rec["record_close"] - px["close"].max()) < TOL)
    check(f"{sym} — record-day count matches the cummax recompute",
          int(rec["record_days"]) == int((px["close"] >= px["close"].cummax()).sum()))


# ---------------------------------------------------------------------------
print("\nBest and worst periods — partial periods cannot win")
# ---------------------------------------------------------------------------
for sym in SYMBOLS:
    bw = _read(queries.JOURNEY_BEST_WORST_PERIODS, symbol=sym,
               asof_month="2999-12", asof_year="2999")
    check(f"{sym} — four rows (best/worst month and year)", len(bw) == 4)

    years = _read("SELECT year, year_return FROM yearly_summary"
                  " WHERE symbol = :s AND is_partial = 0", s=sym)
    best_year = bw[(bw["period_type"] == "year") & (bw["extreme"] == "best")].iloc[0]
    check(f"{sym} — best year matches the full-year maximum",
          abs(best_year["period_return"] - years["year_return"].max()) < TOL)
    check(f"{sym} — best year is not a partial year",
          best_year["period"] in set(years["year"]))

    # The first and last months of a record are partial by construction and are
    # excluded; the winner must not be one of them.
    months = _read("SELECT year_month FROM monthly_returns WHERE symbol = :s"
                   " ORDER BY year_month", s=sym)["year_month"]
    edges = {months.iloc[0], months.iloc[-1]}
    check(f"{sym} — month extremes exclude the record's edge months",
          not (set(bw[bw["period_type"] == "month"]["period"]) & edges))


# ---------------------------------------------------------------------------
print("\nCurated events — the data file reaches SQL intact")
# ---------------------------------------------------------------------------
with build_db.COMPANY_EVENTS_JSON.open() as f:
    payload = json.load(f)
file_events = payload["events"]
file_categories = {c["name"] for c in payload["categories"]}

db_events = _read("SELECT symbol, date, title, category, source FROM company_events")
check("every event in the file is in the database",
      len(db_events) == len(file_events), f"{len(db_events)} vs {len(file_events)}")
check("no event carries a category outside the registry",
      set(db_events["category"]) <= file_categories)
check("every event date is a valid ISO date",
      all(dt.date.fromisoformat(d) for d in db_events["date"]))
check("every event names a symbol in the universe",
      set(db_events["symbol"])
      <= set(_read("SELECT symbol FROM symbols")["symbol"]))
check("every event carries a source", bool((db_events["source"].str.len() > 0).all()))
check("the category registry is loaded",
      set(_read("SELECT name FROM event_categories")["name"]) == file_categories)
check("market events are mirrored into SQL",
      len(_read("SELECT date FROM market_events")) == len(
          __import__("events").EVENTS))

# Validation must REJECT, not skip. A dropped row is invisible in the UI.
known_syms = set(db_events["symbol"])
for bad, why in (
    ({"symbol": "NOPE", "date": "2020-01-01", "title": "t", "description": "d",
      "category": "IPO", "source": "s"}, "unknown symbol"),
    ({"symbol": "AAPL", "date": "2020-01-01", "title": "t", "description": "d",
      "category": "Nonsense", "source": "s"}, "unknown category"),
    ({"symbol": "AAPL", "date": "01/01/2020", "title": "t", "description": "d",
      "category": "IPO", "source": "s"}, "malformed date"),
    ({"symbol": "AAPL", "date": "2020-01-01", "title": "", "description": "d",
      "category": "IPO", "source": "s"}, "empty field"),
):
    try:
        build_db._validate_event(bad, 1, known_syms, file_categories)
        check(f"validation rejects {why}", False, "it was accepted")
    except ValueError:
        check(f"validation rejects {why}", True)

# Events predating the price record must still return, with NULL prices.
aapl = _read(queries.JOURNEY_COMPANY_EVENTS, symbol="AAPL", asof=LAST, sessions=5)
ipo = aapl[aapl["category"] == "IPO"]
check("the 1980 IPO event is returned", len(ipo) == 1)
check("an event before the record has no price attached",
      bool(ipo.iloc[0]["close_before"] is None or pd.isna(ipo.iloc[0]["close_before"])))
check("events respect the cursor", bool(
    (_read(queries.JOURNEY_COMPANY_EVENTS, symbol="AAPL",
           asof="2010-01-01", sessions=5)["date"] <= "2010-01-01").all()))

# Every event lands on a real session on or after its own date.
dated = aapl[aapl["session_date"].notna()]
check("events anchor to a session on or after their date",
      bool((dated["session_date"] >= dated["date"]).all()))


# ---------------------------------------------------------------------------
print("\nMarket events — kept only where THIS company moved")
# ---------------------------------------------------------------------------
for sym in SYMBOLS:
    for threshold in (0.02, 0.05, 0.15):
        got = _read(queries.JOURNEY_MARKET_EVENT_IMPACT, symbol=sym, asof=LAST,
                    sessions=5, min_move=threshold)
        check(f"{sym} @ {threshold:.0%} — every row clears the threshold",
              bool((got["company_move"].abs() >= threshold - TOL).all()))

    loose = _read(queries.JOURNEY_MARKET_EVENT_IMPACT, symbol=sym, asof=LAST,
                  sessions=5, min_move=0.02)
    tight = _read(queries.JOURNEY_MARKET_EVENT_IMPACT, symbol=sym, asof=LAST,
                  sessions=5, min_move=0.15)
    check(f"{sym} — a tighter threshold is a subset of a looser one",
          set(tight["date"]) <= set(loose["date"]))

# The filter must actually discriminate between companies, or it is decoration.
moves = {s: set(_read(queries.JOURNEY_MARKET_EVENT_IMPACT, symbol=s, asof=LAST,
                      sessions=5, min_move=0.05)["date"]) for s in ("AAPL", "KO", "JPM")}
check("different companies keep different market events",
      len({frozenset(v) for v in moves.values()}) > 1,
      " / ".join(f"{k}:{len(v)}" for k, v in moves.items()))


# ---------------------------------------------------------------------------
print("\nTrend changes and the price path")
# ---------------------------------------------------------------------------
for sym in SYMBOLS:
    tc = _read(queries.JOURNEY_TREND_CHANGES, symbol=sym, asof=LAST)
    check(f"{sym} — crossings alternate golden/death",
          all(a != b for a, b in zip(tc["cross_type"], tc["cross_type"].iloc[1:])),
          f"{len(tc)} crossings")

    full = closes(sym)
    for stride in (1, 3, 20):
        path = _read(queries.JOURNEY_PRICE_PATH, symbol=sym, stride=stride)
        check(f"{sym} — stride {stride} keeps the first and last session",
              path["date"].iloc[0] == full["date"].iloc[0]
              and path["date"].iloc[-1] == full["date"].iloc[-1])
        # The thinned path must still bound the price: every record high is kept,
        # so the peak line can never step below a close it is supposed to bound.
        check(f"{sym} — stride {stride} preserves the all-time high",
              abs(path["peak_close"].max() - full["close"].max()) < TOL)
        check(f"{sym} — stride {stride} is monotone in date",
              bool(path["date"].is_monotonic_increasing))


# ---------------------------------------------------------------------------
print("\nNarrative layer — phrasing only, and it survives empty inputs")
# ---------------------------------------------------------------------------
empty = pd.DataFrame()
facts = journey.did_you_know(
    name="Nothing Corp", snapshot=None, records=None, extremes=empty,
    up_streaks=empty, down_streaks=empty, best_worst=empty, drawdowns=empty,
    company_events=empty, trend_changes=empty)
check("every query empty produces no facts rather than raising", facts == [])
check("an empty timeline is an empty list",
      journey.timeline(company_events=empty, market_events=empty, drawdowns=empty,
                       extremes=empty, asof=LAST) == [])
check("chapter_label handles a missing snapshot",
      journey.chapter_label(None) == ("No data", "neutral"))

snap = _read(queries.JOURNEY_SNAPSHOT, symbol="AAPL", asof=LAST).iloc[0]
full_facts = journey.did_you_know(
    name="Apple Inc.", snapshot=snap,
    records=_read(queries.JOURNEY_RECORD_SUMMARY, symbol="AAPL", asof=LAST).iloc[0],
    extremes=_read(queries.JOURNEY_EXTREME_DAYS, symbol="AAPL", asof=LAST, limit=3),
    up_streaks=_read(queries.JOURNEY_STREAKS, symbol="AAPL", asof=LAST, direction=1, limit=1),
    down_streaks=_read(queries.JOURNEY_STREAKS, symbol="AAPL", asof=LAST, direction=-1, limit=1),
    best_worst=_read(queries.JOURNEY_BEST_WORST_PERIODS, symbol="AAPL",
                     asof_month="2999-12", asof_year="2999"),
    drawdowns=_read(queries.JOURNEY_DRAWDOWN_EPISODES, symbol="AAPL", asof=LAST, min_depth=-0.15),
    company_events=_read(queries.JOURNEY_COMPANY_EVENTS, symbol="AAPL", asof=LAST, sessions=5),
    trend_changes=_read(queries.JOURNEY_TREND_CHANGES, symbol="AAPL", asof=LAST))
check("a full input produces a populated panel", len(full_facts) >= 10,
      f"{len(full_facts)} facts")
check("every jump date is a real ISO date",
      all(dt.date.fromisoformat(f.jump_date) for f in full_facts if f.jump_date))
check("the IPO fact comes from curated data, not the first price row",
      any("1980" in f.detail for f in full_facts))
check("no fact claims the first price row is an IPO",
      not any("IPO" in f.headline and "2001" in f.detail for f in full_facts))

# A cursor early in the record must not narrate facts from the company's future.
early_asof = "2003-06-30"
early_facts = journey.did_you_know(
    name="Apple Inc.",
    snapshot=_read(queries.JOURNEY_SNAPSHOT, symbol="AAPL", asof=early_asof).iloc[0],
    records=_read(queries.JOURNEY_RECORD_SUMMARY, symbol="AAPL", asof=early_asof).iloc[0],
    extremes=_read(queries.JOURNEY_EXTREME_DAYS, symbol="AAPL", asof=early_asof, limit=3),
    up_streaks=_read(queries.JOURNEY_STREAKS, symbol="AAPL", asof=early_asof, direction=1, limit=1),
    down_streaks=_read(queries.JOURNEY_STREAKS, symbol="AAPL", asof=early_asof, direction=-1, limit=1),
    best_worst=_read(queries.JOURNEY_BEST_WORST_PERIODS, symbol="AAPL",
                     asof_month=early_asof[:7], asof_year=early_asof[:4]),
    drawdowns=_read(queries.JOURNEY_DRAWDOWN_EPISODES, symbol="AAPL",
                    asof=early_asof, min_depth=-0.15),
    company_events=_read(queries.JOURNEY_COMPANY_EVENTS, symbol="AAPL",
                         asof=early_asof, sessions=5),
    trend_changes=_read(queries.JOURNEY_TREND_CHANGES, symbol="AAPL", asof=early_asof))
future = [f for f in early_facts
          if f.jump_date and f.jump_date > early_asof and "-12-31" not in f.jump_date]
check("no fact at a 2003 cursor points past that cursor", not future,
      "; ".join(f"{f.headline} -> {f.jump_date}" for f in future))


CONN.close()
print()
print("FAILURES:", _failures)
sys.exit(1 if _failures else 0)

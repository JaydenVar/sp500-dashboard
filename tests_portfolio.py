"""Regression tests: the portfolio mathematics, against an independent recompute.

The guarantee these enforce is the one the README states — portfolio figures
agree with a pandas recomputation to 1e-9 — and it was previously verified only
ad hoc, so nothing stopped a later edit from quietly breaking it.

Two properties matter here and neither is visible in a rendered page:

* **Contributions sum to the total return exactly.** Weight x holding-return is
  the intuitive decomposition and it is wrong once returns compound; the parts
  miss the whole by more the longer the window. `PORTFOLIO_CONTRIBUTION` uses
  the exact wealth-recursion decomposition instead, and "exact" is a claim a
  test has to hold to.
* **The investable date is the first session where every holding has a PRICE,
  not a return.** This is the bug the mixed-listing scenario caught once
  already, which is why a basket spanning 2001 / 2012 / 2010 listings is a
  permanent case below rather than an example.

Correlation is checked against `pandas.DataFrame.corr` on the same rows, since
the SQL uses the expanded covariance identity rather than the textbook form and
the two only agree if the algebra is right.

Run: ./.venv/bin/python tests_portfolio.py
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import queries
from db import get_connection

CONN = get_connection()
TOL = 1e-9
_failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}{f'  {detail}' if detail else ''}")


def _rows(weights):
    return ", ".join(f"('{s}', {float(w):.10f})" for s, w in weights)


def _read(sql, **params):
    return pd.read_sql(sql, CONN, params=params)


# Each basket is a different shape of the same risk: equal weights, a lopsided
# pair, mixed listing dates, a single holding, and a window that contains 2008.
SCENARIOS = [
    ("equal weights, one sector",
     (("AAPL", 0.25), ("MSFT", 0.25), ("NVDA", 0.25), ("AMZN", 0.25)), "2015-01-01", "2025-12-31"),
    ("lopsided pair over a long window",
     (("AAPL", 0.7), ("XOM", 0.3)), "2001-01-01", "2025-12-31"),
    ("mixed listing dates (2001 / 2012 / 2010)",
     (("AAPL", 0.4), ("META", 0.3), ("TSLA", 0.3)), "2001-01-01", "2025-12-31"),
    ("single holding",
     (("JNJ", 1.0),), "2010-01-01", "2020-12-31"),
    ("window containing the 2008 crash",
     (("JPM", 0.15), ("XOM", 0.35), ("JNJ", 0.2), ("AAPL", 0.3)), "2008-01-01", "2012-12-31"),
]


print("=== contributions decompose the total return exactly ===")
for label, weights, start, end in SCENARIOS:
    rows = _rows(weights)
    contrib = _read(queries.PORTFOLIO_CONTRIBUTION.format(weight_rows=rows),
                    start=start, end=end)
    stats = _read(queries.PORTFOLIO_STATS.format(weight_rows=rows),
                  start=start, end=end).iloc[0]

    if contrib.empty or pd.isna(stats["sessions"]):
        check(label, False, "query returned nothing")
        continue

    total = float(stats["total_return"])
    summed = float(contrib["contribution"].sum())
    check(f"{label} — sums to total", abs(summed - total) < TOL,
          f"{summed:.12f} vs {total:.12f}")
    check(f"{label} — one row per holding", len(contrib) == len(weights))
    check(f"{label} — weights survive the join",
          np.allclose(sorted(contrib["weight"]), sorted(w for _, w in weights), atol=1e-12))

    # Weight x return is what this decomposition is NOT. On a long window the
    # naive version must visibly miss, or the test above proves nothing.
    if label.startswith("lopsided"):
        naive = float((contrib["weight"] * contrib["holding_return"]).sum())
        check("lopsided pair — naive weight x return really does miss",
              abs(naive - total) > 0.01, f"naive {naive:.4f} vs exact {total:.4f}")


print("=== the portfolio series agrees with a pandas recompute ===")
for label, weights, start, end in SCENARIOS:
    rows = _rows(weights)
    pser = _read(queries.PORTFOLIO_SERIES.format(weight_rows=rows), start=start, end=end)
    if pser.empty:
        check(label, False, "empty series")
        continue

    syms = [s for s, _ in weights]
    wmap = dict(weights)
    raw = _read(
        "SELECT symbol, date, daily_return FROM daily_returns "
        f"WHERE symbol IN ({','.join(repr(s) for s in syms)}) "
        "AND date BETWEEN :start AND :end AND daily_return IS NOT NULL",
        start=start, end=end)
    wide = raw.pivot(index="date", columns="symbol", values="daily_return").dropna()

    # The investable date: first session every holding has a price. Returns
    # accrue from the following session, which is what the SQL does too.
    priced = _read(
        "SELECT p.date FROM prices p "
        f"WHERE p.symbol IN ({','.join(repr(s) for s in syms)}) "
        "AND p.date BETWEEN :start AND :end AND p.close IS NOT NULL "
        "GROUP BY p.date HAVING COUNT(DISTINCT p.symbol) = :n",
        start=start, end=end, n=len(syms))
    d0 = priced["date"].min()

    wide = wide[wide.index > d0]
    port_ret = sum(wide[s] * wmap[s] for s in syms)
    expected = (1 + port_ret).cumprod() - 1

    check(f"{label} — session count", len(pser) == len(expected),
          f"{len(pser)} vs {len(expected)}")
    if len(pser) == len(expected):
        diff = np.abs(pser["cumulative_return"].values - expected.values).max()
        check(f"{label} — cumulative return", diff < TOL, f"max diff {diff:.3e}")


print("=== correlation matches pandas .corr() ===")
SYMS = ("AAPL", "JPM", "XOM", "JNJ")
for start, end in (("2015-01-01", "2025-12-31"), ("2008-01-01", "2010-12-31")):
    sql = queries.CORRELATION_MATRIX.format(
        symbol_rows=", ".join(f"('{s}')" for s in SYMS))
    got = _read(sql, start=start, end=end)
    raw = _read(
        "SELECT symbol, date, daily_return FROM daily_returns "
        f"WHERE symbol IN ({','.join(repr(s) for s in SYMS)}) "
        "AND date BETWEEN :start AND :end AND daily_return IS NOT NULL",
        start=start, end=end)
    want = raw.pivot(index="date", columns="symbol",
                     values="daily_return").corr(min_periods=3)

    worst = max(abs(r["correlation"] - want.loc[r["sym_a"], r["sym_b"]])
                for _, r in got.iterrows())
    check(f"{start[:4]}–{end[:4]} matches pandas", worst < TOL, f"max diff {worst:.3e}")
    check(f"{start[:4]}–{end[:4]} symmetric",
          np.allclose(*(lambda m: (m.values, m.values.T))(
              got.pivot(index="sym_a", columns="sym_b", values="correlation")), atol=1e-12))
    check(f"{start[:4]}–{end[:4]} stays within [-1, 1]",
          bool(got["correlation"].between(-1.0, 1.0).all()),
          f"{got['correlation'].min():.6f}..{got['correlation'].max():.6f}")

# Correlation is undefined below three shared observations; it must come back
# NULL rather than as a fabricated 1.0 from a degenerate two-point fit.
tiny = _read(queries.CORRELATION_MATRIX.format(
    symbol_rows=", ".join(f"('{s}')" for s in SYMS)),
    start="2020-03-02", end="2020-03-03")
check("under three shared sessions yields NULL",
      bool(tiny.empty or tiny["correlation"].isna().all()))


print("=== rolling returns agree with a shifted-price recompute ===")
px = _read("SELECT date, close FROM prices WHERE symbol = '^GSPC' "
           "AND close IS NOT NULL AND close > 0 ORDER BY date")
for sessions in (252, 756, 1260, 2520):
    got = _read(queries.ROLLING_RETURNS, symbol="^GSPC", sessions=sessions)
    want_win = px["close"].values[sessions:] / px["close"].values[:-sessions] - 1
    want_ann = (px["close"].values[sessions:] /
                px["close"].values[:-sessions]) ** (252.0 / sessions) - 1

    check(f"{sessions}s — period count", len(got) == len(want_win),
          f"{len(got)} vs {len(want_win)}")
    if len(got) == len(want_win):
        check(f"{sessions}s — window return",
              np.abs(got["window_return"].values - want_win).max() < TOL)
        check(f"{sessions}s — annualized",
              np.abs(got["annualized_return"].values - want_ann).max() < TOL)

    summary = _read(queries.ROLLING_RETURN_SUMMARY,
                    symbol="^GSPC", sessions=sessions).iloc[0]
    check(f"{sessions}s — summary agrees with the series",
          int(summary["periods"]) == len(want_ann)
          and abs(summary["best"] - want_ann.max()) < TOL
          and abs(summary["worst"] - want_ann.min()) < TOL
          and abs(summary["share_positive"] - (want_ann > 0).mean()) < TOL)

# A horizon longer than the record has no complete period. It must return
# nothing rather than raise -- the Performance page renders an empty state on it.
check("horizon longer than the record returns no rows",
      _read(queries.ROLLING_RETURNS, symbol="^GSPC", sessions=99999).empty)
check("horizon longer than the record reports zero periods",
      int(_read(queries.ROLLING_RETURN_SUMMARY,
                symbol="^GSPC", sessions=99999).iloc[0]["periods"]) == 0)

CONN.close()
print()
print("FAILURES:", _failures)
sys.exit(1 if _failures else 0)

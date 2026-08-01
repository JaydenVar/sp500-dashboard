"""Regression tests: the ranking engine, against an independent pandas recompute.

The engine's output is a leaderboard of plausible-looking numbers. Almost every
way it can be wrong still produces a full board in the right shape, which is why
these tests check *properties* rather than rendering:

* **Missing data must renormalize, never zero-fill.** A stock with no
  fundamentals scoring 0 on P/E would rank as expensive rather than as unknown,
  and the board would quietly become a ranking of data availability. The case
  below gives one stock a NULL on a "lower is better" metric and asserts it is
  neither best nor worst on it.

* **Percentile ranks must be unbiased by NULLs.** SQLite sorts NULLs first, so
  the obvious `PERCENT_RANK() OVER (ORDER BY col)` hands the lowest ranks to
  rows with no value and shifts every real value upward by the share of missing
  data. That is invisible on screen -- the ranks still run 0..1 and still order
  correctly -- so it is checked against a pandas recompute over the non-null
  subset, which is the only thing that catches it.

* **Filters must apply BEFORE ranking.** A sector board has to rank a stock
  against its sector peers, not show where it landed among all 500 and then hide
  the rest. Both produce a board; only one is the answer the page claims.

* **The AI must not be able to reach a score.** Asserted structurally, by
  checking the ranking path never touches the model module -- a prompt rule is
  not a guarantee, an import graph is.

Run: ./.venv/bin/python tests_ranking.py
"""

from __future__ import annotations

import random
import sys

import pandas as pd

import ranking
from db import get_connection

TOL = 1e-9
_failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}{f'  {detail}' if detail else ''}")


# ---------------------------------------------------------------------------
# A synthetic panel, so every case has known-correct expected values.
# ---------------------------------------------------------------------------
COLUMNS = sorted({m.column for m in ranking.METRICS})
SECTORS = ["Information Technology", "Financials", "Health Care", "Energy"]


def build_panel(n: int = 60, null_rate: float = 0.0, seed: int = 7) -> pd.DataFrame:
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        row = {"symbol": f"S{i:03d}", "name": f"Company {i}",
               "sector": SECTORS[i % len(SECTORS)], "is_index": 0,
               "last_close": 10.0 + i, "market_cap": 1e9 * (i + 1)}
        for col in COLUMNS:
            if col in row:
                continue
            row[col] = None if rng.random() < null_rate else rng.uniform(-2, 40)
        rows.append(row)
    return pd.DataFrame(rows)


def load(df: pd.DataFrame):
    conn = get_connection(":memory:")
    df.to_sql("metric_panel", conn, index=False)
    return conn


def run(conn, horizon: str, objective: str = "Balanced", with_analyst: bool = False):
    sql = ranking.score_sql(horizon, objective=objective, with_analyst=with_analyst)
    return pd.read_sql(sql, conn)


# ---------------------------------------------------------------------------
print("Registry")
# ---------------------------------------------------------------------------
for h in ranking.HORIZONS:
    for analyst in (True, False):
        w = ranking.category_weights(h, with_analyst=analyst)
        check(f"{h}: category weights sum to 1.0 (analyst={analyst})",
              abs(sum(w.values()) - 1.0) < TOL, f"got {sum(w.values()):.9f}")

# Without a provider the Analyst category must carry NO weight, not a weight
# nobody can score. Otherwise every stock's coverage is capped below 1.0 by a
# property of the deployment rather than of the stock -- which is what this
# suite caught on first run.
for h in ranking.HORIZONS:
    w = ranking.category_weights(h, with_analyst=False)
    check(f"{h}: Analyst carries zero weight when no provider is configured",
          w[ranking.ANALYST] == 0.0, f"got {w[ranking.ANALYST]}")
    others = ranking.category_weights(h, with_analyst=True)
    check(f"{h}: dropping Analyst redistributes its weight, not discards it",
          abs(sum(v for k, v in w.items() if k != ranking.ANALYST) - 1.0) < TOL
          and w[ranking.MOMENTUM] >= others[ranking.MOMENTUM] - TOL)

for h in ranking.HORIZONS:
    for cat in ranking.CATEGORIES:
        members = ranking.BY_CATEGORY[cat]
        total = sum(ranking.metric_weight(h, m) for m in members)
        check(f"{h}/{cat}: metric weights sum to 1.0 within the category",
              abs(total - 1.0) < 1e-9 or total == 0.0, f"got {total:.6f}")

for obj in ranking.OBJECTIVES:
    for h in ranking.HORIZONS:
        w = ranking.category_weights(h, obj)
        check(f"{h}/{obj}: objective tilt renormalizes to 1.0",
              abs(sum(w.values()) - 1.0) < TOL)

check("every metric column is unique",
      len({m.column for m in ranking.METRICS}) == len(ranking.METRICS))
check("analyst metrics are the only optional ones",
      {m.key for m in ranking.METRICS if m.optional}
      == {m.key for m in ranking.METRICS if m.category == ranking.ANALYST})
check("dropping analyst removes exactly the analyst metrics",
      len(ranking.active_metrics(with_analyst=True))
      - len(ranking.active_metrics(with_analyst=False))
      == len(ranking.BY_CATEGORY[ranking.ANALYST]))

# ---------------------------------------------------------------------------
print("\nGenerated SQL executes")
# ---------------------------------------------------------------------------
panel = build_panel()
conn = load(panel)
combos = 0
for h in ranking.HORIZONS:
    for obj in ranking.OBJECTIVES:
        for analyst in (False, True):
            df = run(conn, h, obj, analyst)
            combos += 1
            if len(df) != len(panel):
                check(f"{h}/{obj}/analyst={analyst} returns every row", False,
                      f"{len(df)} of {len(panel)}")
                break
    else:
        continue
    break
else:
    check(f"all {combos} horizon x objective x analyst statements execute", True)

df = run(conn, ranking.MEDIUM)
check("overall_score stays within 0-100",
      float(df["overall_score"].min()) >= 0 and float(df["overall_score"].max()) <= 100,
      f"{df['overall_score'].min():.1f}..{df['overall_score'].max():.1f}")
check("coverage stays within 0-1",
      float(df["coverage"].min()) >= 0 and float(df["coverage"].max()) <= 1 + TOL)
check("a full panel scores full coverage",
      abs(float(df["coverage"].min()) - 1.0) < 1e-9,
      f"min {df['coverage'].min():.4f}")

# ---------------------------------------------------------------------------
print("\nPercentile ranks match an independent recompute")
# ---------------------------------------------------------------------------
# The load-bearing one. With NULLs present, a naive PERCENT_RANK biases every
# non-null value upward by the share of missing data; both versions still look
# like valid 0..1 ranks, so only a recompute distinguishes them.
holey = build_panel(n=80, null_rate=0.25, seed=11)
conn_h = load(holey)
scored = run(conn_h, ranking.MEDIUM).set_index("symbol")

for metric in (ranking.BY_KEY["mom_12_1"], ranking.BY_KEY["roe"]):
    col = metric.column
    sub = holey.dropna(subset=[col])
    want = sub[col].rank(method="min", ascending=True)
    want = (want - 1.0) / (len(sub) - 1)
    if metric.direction < 0:
        want = 1.0 - want
    want.index = sub["symbol"]
    got = scored[f"pr_{metric.key}"].dropna()
    aligned = want.reindex(got.index)
    check(f"{metric.key}: percentile ranks match pandas over the non-null subset",
          bool((got - aligned).abs().max() < 1e-9),
          f"max diff {float((got - aligned).abs().max()):.2e}")
    check(f"{metric.key}: NULL rows stay NULL rather than ranking worst",
          int(scored[f"pr_{metric.key}"].isna().sum()) == int(holey[col].isna().sum()))

# Sector-neutral metrics rank within sector, not across the universe.
pe = ranking.BY_KEY["pe"]
check("pe is registered sector-neutral", pe.sector_neutral)
sub = holey.dropna(subset=[pe.column])
per_sector = []
for sector, grp in sub.groupby("sector"):
    r = grp[pe.column].rank(method="min", ascending=True)
    r = 1.0 - (r - 1.0) / (len(grp) - 1)          # direction = -1
    r.index = grp["symbol"]
    per_sector.append(r)
want_pe = pd.concat(per_sector)
got_pe = scored["pr_pe"].dropna()
check("pe percentiles are computed WITHIN sector",
      bool((got_pe - want_pe.reindex(got_pe.index)).abs().max() < 1e-9))

# ---------------------------------------------------------------------------
print("\nMissing data renormalizes rather than zero-filling")
# ---------------------------------------------------------------------------
# One stock loses its whole Valuation category; its score must be the weighted
# mean of the categories it still has, NOT a score dragged down by four zeros.
one = build_panel(n=40, seed=3)
val_cols = [m.column for m in ranking.BY_CATEGORY[ranking.VALUATION]]
one.loc[one["symbol"] == "S000", val_cols] = None
conn_o = load(one)
res = run(conn_o, ranking.LONG).set_index("symbol")

row = res.loc["S000"]
check("a stock missing a whole category scores NULL for that category",
      pd.isna(row[ranking.CATEGORY_COLUMNS[ranking.VALUATION]]))
weights = ranking.category_weights(ranking.LONG, with_analyst=False)
check("a fully-covered stock reaches 1.0 coverage with no provider configured",
      abs(float(res.loc["S001", "coverage"]) - 1.0) < 1e-9,
      f"got {res.loc['S001', 'coverage']:.4f}")
check("its coverage drops by exactly that category's weight",
      abs(float(row["coverage"]) - (1.0 - weights[ranking.VALUATION])) < 1e-9,
      f"coverage {row['coverage']:.4f}, expected {1 - weights[ranking.VALUATION]:.4f}")

present = [c for c in ranking.CATEGORIES
           if weights.get(c, 0) > 0 and c != ranking.VALUATION
           and not pd.isna(row.get(ranking.CATEGORY_COLUMNS[c]))]
num = sum(float(row[ranking.CATEGORY_COLUMNS[c]]) * weights[c] for c in present)
den = sum(weights[c] for c in present)
# The SQL rounds the published score to one decimal, so compare against the
# rounded expectation rather than the raw quotient.
check("its score is the renormalized mean of the categories it still has",
      abs(float(row["overall_score"]) - round(100.0 * num / den, 1)) < 1e-9,
      f"got {row['overall_score']:.4f}, expected {round(100 * num / den, 1)}")

# The specific failure mode: a NULL on a lower-is-better metric must not read as
# the best possible value.
lowbetter = ranking.BY_KEY["debt_to_equity"]
two = build_panel(n=30, seed=5)
two.loc[two["symbol"] == "S000", lowbetter.column] = None
res2 = run(load(two), ranking.LONG).set_index("symbol")
check("a NULL on a lower-is-better metric is not scored as best",
      pd.isna(res2.loc["S000", f"pr_{lowbetter.key}"]))
check("...and does not become the top-ranked stock because of it",
      res2["overall_score"].idxmax() != "S000"
      or res2.loc["S000", "overall_score"] < 100.0)

# ---------------------------------------------------------------------------
print("\nDirection")
# ---------------------------------------------------------------------------
mono = build_panel(n=20, seed=9)
for i, sym in enumerate(mono["symbol"]):
    mono.loc[mono["symbol"] == sym, "vol_1y"] = float(i)      # lower is better
    mono.loc[mono["symbol"] == sym, "roe"] = float(i)         # higher is better
    mono.loc[mono["symbol"] == sym, "ret_1m"] = float(i)      # INVERTED by design
res3 = run(load(mono), ranking.SHORT).set_index("symbol")
check("vol_1y: the lowest value earns the highest percentile",
      float(res3.loc["S000", "pr_vol_1y"]) == 1.0
      and float(res3.loc["S019", "pr_vol_1y"]) == 0.0)
check("roe: the highest value earns the highest percentile",
      float(res3.loc["S019", "pr_roe"]) == 1.0
      and float(res3.loc["S000", "pr_roe"]) == 0.0)
check("ret_1m is scored INVERTED (short-horizon reversal, Jegadeesh 1990)",
      float(res3.loc["S000", "pr_ret_1m"]) == 1.0,
      "the weakest recent month ranks best on the short board")
check("ret_5y is registered inverted (De Bondt & Thaler 1985)",
      ranking.BY_KEY["ret_5y"].direction < 0)
check("mom_12_1 is registered positive (Jegadeesh & Titman 1993)",
      ranking.BY_KEY["mom_12_1"].direction > 0)

# ---------------------------------------------------------------------------
print("\nFilters rank against the filtered universe")
# ---------------------------------------------------------------------------
# The same stock, scored against everyone vs scored against its sector only.
# These must differ -- if they match, the filter was applied to the RESULT and
# every percentile on a filtered board is misreported.
full = run(load(build_panel(n=60, seed=21)), ranking.LONG).set_index("symbol")
sub_panel = build_panel(n=60, seed=21)
sub_panel = sub_panel[sub_panel["sector"] == "Financials"]
sql = ranking.score_sql(ranking.LONG, with_analyst=False)
subres = pd.read_sql(sql, load(sub_panel)).set_index("symbol")
shared = [s for s in subres.index if s in full.index]
check("a sector-filtered board re-ranks within the sector",
      any(abs(float(full.loc[s, "overall_score"])
              - float(subres.loc[s, "overall_score"])) > 1e-6 for s in shared),
      "scores move when the peer set changes, as they must")
check("the filtered board contains only the filtered sector", len(subres) == len(sub_panel))

# `panel_where` must reach the panel CTE, where it filters BEFORE ranking. It is
# a parameter rather than a string the caller patches in afterwards, because a
# replace that stops matching produces a full, plausible, silently UNFILTERED
# board -- no exception, no empty result, nothing to notice.
gen = ranking.score_sql(ranking.LONG, with_analyst=False,
                        panel_where="sector = 'Energy' AND market_cap > 5e9")
check("panel_where lands inside the panel CTE",
      "FROM metric_panel WHERE sector = 'Energy' AND market_cap > 5e9" in gen)
check("panel_where is applied before the ranks are computed",
      gen.index("sector = 'Energy'") < gen.index("RANK() OVER"))
filtered = pd.read_sql(gen, load(build_panel(n=60, seed=21)))
check("a panel_where predicate actually removes rows",
      0 < len(filtered) < 60, f"{len(filtered)} of 60 rows survived")

da_src_early = open("data_access.py").read()
check("data_access passes panel_where instead of patching the SQL string",
      "panel_where=" in da_src_early
      and 'sql.replace("SELECT * FROM metric_panel' not in da_src_early)

# ---------------------------------------------------------------------------
print("\nCoverage gate")
# ---------------------------------------------------------------------------
sparse = build_panel(n=30, seed=31)
keep = [m.column for m in ranking.BY_CATEGORY[ranking.MOMENTUM]]
for col in COLUMNS:
    if col not in keep:
        sparse.loc[sparse["symbol"] == "S000", col] = None
gated = run(load(sparse), ranking.LONG).set_index("symbol")
check("a barely-covered stock falls under MIN_COVERAGE",
      float(gated.loc["S000", "coverage"]) < ranking.MIN_COVERAGE,
      f"coverage {gated.loc['S000', 'coverage']:.3f} < {ranking.MIN_COVERAGE}")
check("MIN_COVERAGE is a real gate, not 0", ranking.MIN_COVERAGE > 0.0)

# ---------------------------------------------------------------------------
print("\nThe model cannot reach a score")
# ---------------------------------------------------------------------------
# Structural, not a prompt rule: if the ranking path never imports the model
# module, no model response can alter a rank however the prompt is worded.
ranking_src = open("ranking.py").read()
check("ranking.py does not import nlq or anthropic",
      "import nlq" not in ranking_src and "anthropic" not in ranking_src)
check("ranking.py computes no arithmetic in pandas",
      "import pandas" not in ranking_src and "import numpy" not in ranking_src,
      "it generates SQL; SQLite does the arithmetic")

intel_src = open("market_intel.py").read()
# Match the CALL, not the module docstring that describes the boundary -- the
# docstring sits above every function and would make this pass vacuously.
explain_at = intel_src.find("nlq.explain_ranking(payload")
rankings_at = intel_src.find("da.rankings(")
check("the board is queried before the model is ever called",
      rankings_at != -1 and explain_at != -1 and rankings_at < explain_at)
check("market_intel calls only the explain-only model function",
      intel_src.count("nlq.") == intel_src.count("nlq.explain_ranking")
      + intel_src.count("nlq.available"))

da_src = open("data_access.py").read()
rankings_fn = da_src[da_src.find("def rankings("):]
rankings_fn = rankings_fn[:rankings_fn.find("\n@st.cache_data")]
check("data_access.rankings never mentions the model", "nlq" not in rankings_fn)

# ---------------------------------------------------------------------------
print("\nAuto-refresh wiring")
# ---------------------------------------------------------------------------
check("every cached intelligence read takes a version argument",
      all(f"def {fn}(version" in da_src or f"def {fn}(version:" in da_src
          for fn in ("intel_status", "intel_sectors", "panel_row", "intel_universe")),
      "so a data change is a cache miss")
check("rankings() takes the version as its first argument",
      "def rankings(version: str" in da_src)

build_src = open("build_db.py").read()
check("build_db stamps a data_version", "'data_version'" in build_src)
_dv = build_src.split("def _data_version")[1].split("\ndef ")[0]
check("the stamp is derived from the data, not the clock",
      "MAX(date)" in _dv and "time(" not in _dv and "now" not in _dv,
      "identical CSVs must rebuild to an identical version")

# ---------------------------------------------------------------------------
print("\nThe registry and the panel view agree")
# ---------------------------------------------------------------------------
# A metric registered without a matching column in `metric_panel` generates SQL
# that fails only when someone opens that horizon's board -- and only against a
# populated database, so it survives every test that uses a synthetic panel.
# Checked against the view definition itself, which needs no data.
panel_sql = build_src.split("CREATE VIEW metric_panel_v AS")[1].split(";")[0]
missing = [m.column for m in ranking.METRICS
           if m.column not in panel_sql]
check("every registered metric has a column in metric_panel_v",
      not missing, f"missing: {missing}")

check("the panel view is materialized into metric_panel",
      "CREATE TABLE metric_panel AS SELECT * FROM metric_panel_v" in build_src)
check("metric_panel is in REQUIRED_OBJECTS, so a stale DB rebuilds",
      "metric_panel" in build_src.split("REQUIRED_OBJECTS")[1].split("})")[0])
check("the intel tables are separate from prices/symbols",
      "intel_prices" in build_src and
      "INSERT OR REPLACE INTO prices" in build_src and
      "intel_prices" not in build_src.split("def _load_prices")[1].split("\ndef ")[0],
      "loading the wide universe must not touch the core tables")

print()
print("FAILURES:", _failures)
sys.exit(1 if _failures else 0)

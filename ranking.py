"""The stock-ranking engine: a metric registry and the SQL it generates.

This module holds *configuration*, not arithmetic. Every metric is a row in
`METRICS` and every horizon is a row in `HORIZON_WEIGHTS`; the scoring SQL is
generated from those two tables. Retuning a weight, flipping a metric's
direction, or adding a whole new metric is an edit to a registry entry plus (for
a new metric) one column in the `metric_panel` view -- never a change to the
scoring logic, which is why this file exists separately from `queries.py`.

**Python computes nothing here.** The functions below assemble SQL text; the
arithmetic runs in SQLite like every other figure in this app. That is the
governing constraint (see PROJECT_STATE) and it is what lets the Market
Intelligence page show its own scoring SQL in the Developer Center.

Scoring is **percentile rank within the filtered universe**, not a z-score.
Financial cross-sections are heavily skewed -- P/E, market cap and momentum all
have fat right tails, and a single 400x P/E moves a mean and a standard
deviation enough to compress everything else into the middle of the range. A
percentile rank cannot be moved by an outlier at all: it depends only on
ordering. It also needs no winsorization constant to tune, and it states itself
honestly to a reader ("92nd percentile on return on equity" is a claim a
non-quant can check).

Valuation metrics are ranked **within sector** (`sector_neutral=True`). A P/E
percentile pooled across utilities and software ranks sectors, not companies:
software trades richer for structural reasons, so a pooled valuation score is
mostly a bet against technology wearing the costume of stock selection.

Missing data **renormalizes**, it does not zero-fill. A stock with no fundamental
coverage would otherwise score 0 on every valuation metric and be ranked as
expensive rather than as unknown -- which quietly turns the engine into a ranking
of data availability. `_category_score` divides by the weight actually present,
and a stock below `MIN_COVERAGE` for a horizon is excluded from that horizon's
board and told why.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Horizons
# ---------------------------------------------------------------------------
SHORT, MEDIUM, LONG = "Short-Term", "Medium-Term", "Long-Term"
HORIZONS = (SHORT, MEDIUM, LONG)

HORIZON_BLURB = {
    SHORT: "1-3 months. Weighted toward trend, momentum persistence and liquidity. "
           "Note that 1-month return enters NEGATIVELY -- see MOMENTUM_NOTE.",
    MEDIUM: "6-18 months. Anchored on 12-1 momentum, supported by growth and profitability.",
    LONG: "3-5 years or more. Weighted toward valuation, quality and profitability; "
          "5-year return enters negatively for long-horizon mean reversion.",
}

# Why two return metrics point in opposite directions, which looks like a bug
# until you know the literature. Surfaced in the UI on the methodology panel.
MOMENTUM_NOTE = (
    "Short-horizon returns reverse and medium-horizon returns persist. Jegadeesh "
    "(1990) documents one-month reversal; Jegadeesh & Titman (1993) document "
    "3-12 month momentum and skip the most recent month precisely to avoid the "
    "reversal contaminating it. De Bondt & Thaler (1985) document reversal again "
    "at 3-5 years. So `ret_1m` is scored inverted on the short board, `mom_12_1` "
    "skips the last month, and `ret_5y` is scored inverted on the long board."
)

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
MOMENTUM = "Momentum"
TECHNICAL = "Technical"
RISK = "Risk"
VALUATION = "Valuation"
QUALITY = "Quality"
PROFITABILITY = "Profitability"
GROWTH = "Growth"
ANALYST = "Analyst"

CATEGORIES = (MOMENTUM, TECHNICAL, RISK, VALUATION, QUALITY, PROFITABILITY,
              GROWTH, ANALYST)

CATEGORY_BLURB = {
    MOMENTUM: "Has the stock been going up, over the horizons that historically persist?",
    TECHNICAL: "Where does price sit against its own trend and range?",
    RISK: "How violent is the ride, and how far has it fallen before?",
    VALUATION: "What are you paying per unit of sales, earnings, book and cash flow?",
    QUALITY: "Is the balance sheet sound enough to survive a bad year?",
    PROFITABILITY: "Does the business actually earn a return on what it employs?",
    GROWTH: "Is the top and bottom line expanding?",
    ANALYST: "What do covering analysts expect? (Requires an optional API key.)",
}


# ---------------------------------------------------------------------------
# The metric registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Metric:
    """One scored input.

    `column`         the column in the `metric_panel` view holding the raw value
    `direction`      +1 if higher is better, -1 if lower is better
    `sector_neutral` rank within sector rather than across the whole universe
    `optional`       absent without an analyst API key; never counted against a
                     stock's coverage when the provider is not configured
    """
    key: str
    label: str
    column: str
    category: str
    direction: int
    explain: str
    fmt: str = "num"            # num | pct | ratio | money — display only
    sector_neutral: bool = False
    optional: bool = False


METRICS: tuple[Metric, ...] = (
    # -- Momentum ----------------------------------------------------------
    Metric("ret_1m", "1-Month Return", "ret_1m", MOMENTUM, -1,
           "Recent one-month return, scored INVERTED: short-horizon returns "
           "reverse rather than persist (Jegadeesh 1990).", "pct"),
    Metric("ret_3m", "3-Month Return", "ret_3m", MOMENTUM, +1,
           "Three-month return. The shortest horizon over which momentum, "
           "rather than reversal, is the documented effect.", "pct"),
    Metric("mom_12_1", "12-1 Momentum", "mom_12_1", MOMENTUM, +1,
           "Twelve-month return excluding the most recent month -- the standard "
           "academic momentum construction (Jegadeesh & Titman 1993).", "pct"),
    Metric("ret_5y", "5-Year Return", "ret_5y", MOMENTUM, -1,
           "Five-year return, scored INVERTED: long-horizon winners tend to "
           "underperform subsequently (De Bondt & Thaler 1985).", "pct"),

    # -- Technical ---------------------------------------------------------
    Metric("ma50_pos", "Above 50-Day MA", "ma50_pos", TECHNICAL, +1,
           "Distance of price above its 50-day moving average -- short-term trend.", "pct"),
    Metric("ma200_pos", "Above 200-Day MA", "ma200_pos", TECHNICAL, +1,
           "Distance of price above its 200-day moving average -- primary trend.", "pct"),
    Metric("pct_52w_high", "Near 52-Week High", "pct_52w_high", TECHNICAL, +1,
           "Distance below the 52-week high. Proximity to the high is itself a "
           "documented momentum signal (George & Hwang 2004).", "pct"),
    Metric("rsi_14", "RSI (14)", "rsi_14", TECHNICAL, +1,
           "14-session relative strength index: the share of recent movement "
           "that was upward.", "num"),
    Metric("volume_trend", "Volume Trend", "volume_trend", TECHNICAL, +1,
           "Recent 21-session volume against the one-year average -- whether "
           "the move is carrying participation.", "ratio"),

    # -- Risk --------------------------------------------------------------
    Metric("vol_1y", "1-Year Volatility", "vol_1y", RISK, -1,
           "Annualized volatility of daily returns. Lower scores higher: the "
           "low-volatility anomaly is one of the most replicated in equities.", "pct"),
    Metric("max_dd_1y", "1-Year Max Drawdown", "max_dd_1y", RISK, +1,
           "Deepest peak-to-trough fall in the last year (a negative number, so "
           "higher -- shallower -- scores better).", "pct"),
    Metric("beta_1y", "Beta vs S&P 500", "beta_1y", RISK, -1,
           "Sensitivity to the index over the last year. Lower scores higher.", "num"),
    Metric("downside_dev", "Downside Deviation", "downside_dev", RISK, -1,
           "Volatility of negative days only -- penalizes losses without "
           "penalizing upside, unlike plain volatility.", "pct"),
    Metric("dollar_volume", "Liquidity", "dollar_volume", RISK, +1,
           "Average daily dollar turnover. Illiquidity is a real risk: a "
           "position you cannot exit at the screen price is not worth its score.", "money"),

    # -- Valuation (sector-neutral) ---------------------------------------
    Metric("pe", "P/E Ratio", "pe", VALUATION, -1,
           "Price to trailing twelve-month earnings. Ranked within sector.",
           "ratio", sector_neutral=True),
    Metric("ps", "P/S Ratio", "ps", VALUATION, -1,
           "Price to trailing twelve-month sales. Survives loss-making years, "
           "where P/E does not. Ranked within sector.", "ratio", sector_neutral=True),
    Metric("pb", "P/B Ratio", "pb", VALUATION, -1,
           "Price to book value. The original value factor (Fama & French 1992). "
           "Ranked within sector.", "ratio", sector_neutral=True),
    Metric("fcf_yield", "Free Cash Flow Yield", "fcf_yield", VALUATION, +1,
           "Free cash flow divided by market cap -- the hardest of the value "
           "measures to manage through accounting. Ranked within sector.",
           "pct", sector_neutral=True),

    # -- Quality -----------------------------------------------------------
    Metric("debt_to_equity", "Debt / Equity", "debt_to_equity", QUALITY, -1,
           "Total debt against shareholder equity -- leverage, and how much of "
           "a bad year the balance sheet can absorb.", "ratio"),
    Metric("current_ratio", "Current Ratio", "current_ratio", QUALITY, +1,
           "Current assets over current liabilities: can it fund the next "
           "twelve months without raising money?", "ratio"),
    Metric("interest_coverage", "Interest Coverage", "interest_coverage", QUALITY, +1,
           "Operating income over interest expense -- how many times over the "
           "debt is serviced by the business.", "ratio"),
    Metric("earnings_stability", "Earnings Stability", "earnings_stability", QUALITY, +1,
           "Inverse variability of quarterly earnings over the available "
           "history. Steadier earnings score higher.", "num"),

    # -- Profitability -----------------------------------------------------
    Metric("roe", "Return on Equity", "roe", PROFITABILITY, +1,
           "Net income over shareholder equity.", "pct"),
    Metric("roa", "Return on Assets", "roa", PROFITABILITY, +1,
           "Net income over total assets -- return per unit of what the "
           "business actually employs.", "pct"),
    Metric("gross_margin", "Gross Margin", "gross_margin", PROFITABILITY, +1,
           "Gross profit over revenue: pricing power and cost structure.", "pct"),
    Metric("net_margin", "Net Margin", "net_margin", PROFITABILITY, +1,
           "Net income over revenue -- what survives to the bottom line.", "pct"),
    Metric("gross_profitability", "Gross Profitability", "gross_profitability",
           PROFITABILITY, +1,
           "Gross profit over total assets. Novy-Marx (2013) showed this "
           "predicts returns better than earnings-based profitability, because "
           "gross profit is the line least distorted by accounting choices.", "pct"),

    # -- Growth ------------------------------------------------------------
    Metric("revenue_growth", "Revenue Growth (YoY)", "revenue_growth", GROWTH, +1,
           "Trailing twelve-month revenue against the prior twelve months.", "pct"),
    Metric("earnings_growth", "Earnings Growth (YoY)", "earnings_growth", GROWTH, +1,
           "Trailing twelve-month net income against the prior twelve months.", "pct"),
    Metric("fcf_growth", "Free Cash Flow Growth", "fcf_growth", GROWTH, +1,
           "Trailing twelve-month free cash flow against the prior twelve months.", "pct"),

    # -- Analyst (optional; requires a provider key) -----------------------
    Metric("analyst_score", "Analyst Consensus", "analyst_score", ANALYST, +1,
           "Mean covering-analyst rating, rescaled so higher is more positive.",
           "num", optional=True),
    Metric("target_upside", "Price Target Upside", "target_upside", ANALYST, +1,
           "Mean price target against the current price.", "pct", optional=True),
    Metric("eps_revision", "EPS Revision Trend", "eps_revision", ANALYST, +1,
           "Direction of recent consensus EPS estimate revisions.", "pct", optional=True),
)

BY_KEY: dict[str, Metric] = {m.key: m for m in METRICS}
BY_CATEGORY: dict[str, tuple[Metric, ...]] = {
    c: tuple(m for m in METRICS if m.category == c) for c in CATEGORIES
}


# ---------------------------------------------------------------------------
# Horizon weights
# ---------------------------------------------------------------------------
# Category weights per horizon. Each column sums to 1.0 (asserted below, because
# a weight table that silently stops summing to one produces scores that are not
# comparable across horizons and nothing else would catch it).
HORIZON_WEIGHTS: dict[str, dict[str, float]] = {
    SHORT: {
        MOMENTUM: 0.30, TECHNICAL: 0.30, RISK: 0.20,
        QUALITY: 0.05, ANALYST: 0.10, VALUATION: 0.05,
        PROFITABILITY: 0.00, GROWTH: 0.00,
    },
    MEDIUM: {
        MOMENTUM: 0.25, TECHNICAL: 0.15, GROWTH: 0.15, PROFITABILITY: 0.15,
        VALUATION: 0.10, QUALITY: 0.10, RISK: 0.05, ANALYST: 0.05,
    },
    LONG: {
        VALUATION: 0.22, PROFITABILITY: 0.22, QUALITY: 0.20, GROWTH: 0.15,
        RISK: 0.10, MOMENTUM: 0.06, ANALYST: 0.05, TECHNICAL: 0.00,
    },
}

# Within a category, metrics are weighted equally unless overridden here. The
# overrides exist where one metric IS the category's thesis for that horizon.
METRIC_WEIGHTS: dict[str, dict[str, float]] = {
    SHORT: {"ret_1m": 0.35, "ret_3m": 0.45, "mom_12_1": 0.20, "ret_5y": 0.0},
    MEDIUM: {"mom_12_1": 0.60, "ret_3m": 0.25, "ret_1m": 0.15, "ret_5y": 0.0},
    LONG: {"ret_5y": 0.60, "mom_12_1": 0.40, "ret_1m": 0.0, "ret_3m": 0.0},
}

# A stock must have a scored value for at least this share of the weight in a
# horizon to appear on its board at all. Below it the composite is being carried
# by too few inputs to mean anything, and a confident-looking 88 built from three
# metrics is worse than an honest exclusion.
MIN_COVERAGE = 0.60

# Investment objectives re-weight the *categories* on top of the horizon. A
# multiplier, applied then renormalized -- so an objective tilts the engine
# rather than replacing it, and every objective still scores every category.
OBJECTIVES: dict[str, dict[str, float]] = {
    "Balanced": {},
    "Growth": {GROWTH: 1.8, MOMENTUM: 1.4, VALUATION: 0.5},
    "Value": {VALUATION: 2.0, QUALITY: 1.3, MOMENTUM: 0.6},
    "Income & Stability": {QUALITY: 1.8, RISK: 1.8, PROFITABILITY: 1.2, MOMENTUM: 0.4},
    "Quality Compounders": {PROFITABILITY: 1.8, QUALITY: 1.5, GROWTH: 1.2, TECHNICAL: 0.5},
}

# Risk tolerance is a FILTER on realized volatility, not a scoring tilt. Bands
# are annualized volatility. Expressed as bounds so the UI and the SQL agree on
# one definition rather than each carrying its own numbers.
RISK_BANDS: dict[str, tuple[float, float]] = {
    "Conservative": (0.00, 0.25),
    "Moderate": (0.00, 0.40),
    "Aggressive": (0.00, 9.99),
}

# Market-cap buckets in dollars.
CAP_BANDS: dict[str, tuple[float, float]] = {
    "Mega (>$200B)": (200e9, 1e15),
    "Large ($10B-$200B)": (10e9, 200e9),
    "Mid ($2B-$10B)": (2e9, 10e9),
    "Small (<$2B)": (0.0, 2e9),
}


def _check_weights() -> None:
    """Fail at import if a weight table stopped summing to 1.0.

    These are hand-edited numbers and a horizon whose weights sum to 0.9 still
    produces a plausible-looking leaderboard -- just one that cannot be compared
    against another horizon. Cheap to assert, invisible otherwise.
    """
    for horizon, weights in HORIZON_WEIGHTS.items():
        missing = set(CATEGORIES) - set(weights)
        if missing:
            raise ValueError(f"{horizon}: no weight given for {sorted(missing)}")
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"{horizon}: category weights sum to {total}, not 1.0")
    for horizon, overrides in METRIC_WEIGHTS.items():
        unknown = set(overrides) - set(BY_KEY)
        if unknown:
            raise ValueError(f"{horizon}: metric weight for unknown {sorted(unknown)}")


_check_weights()


def metric_weight(horizon: str, metric: Metric) -> float:
    """Weight of one metric inside its category, before coverage renormalization."""
    override = METRIC_WEIGHTS.get(horizon, {}).get(metric.key)
    if override is not None:
        return override
    peers = BY_CATEGORY[metric.category]
    overridden = METRIC_WEIGHTS.get(horizon, {})
    free = [m for m in peers if m.key not in overridden]
    if not free:
        return 0.0
    remaining = 1.0 - sum(overridden.get(m.key, 0.0) for m in peers if m.key in overridden)
    return max(remaining, 0.0) / len(free)


def category_weights(horizon: str, objective: str = "Balanced", *,
                     with_analyst: bool = True) -> dict[str, float]:
    """Category weights for a horizon, tilted by objective and renormalized.

    `with_analyst=False` drops the Analyst category to zero *before*
    renormalizing, so the remaining categories still sum to 1.0. Leaving its
    weight in place when no provider is configured would cap every stock's
    coverage at 1 - analyst_weight, which then reads on screen as "95% metric
    coverage" for a company that is in fact missing nothing, and spends headroom
    against MIN_COVERAGE on a property of the deployment rather than of the
    stock. That is the same reason `active_metrics` removes the metrics rather
    than scoring them as absent -- this is the other half of it.
    """
    base = dict(HORIZON_WEIGHTS[horizon])
    if not with_analyst:
        base[ANALYST] = 0.0
    tilt = OBJECTIVES.get(objective, {})
    tilted = {c: base[c] * tilt.get(c, 1.0) for c in CATEGORIES}
    total = sum(tilted.values())
    if total <= 0:
        return base
    return {c: w / total for c, w in tilted.items()}


def active_metrics(*, with_analyst: bool) -> tuple[Metric, ...]:
    """Metrics the engine can currently score.

    Analyst metrics drop out entirely when no provider key is configured. They
    are *removed*, not scored as missing -- an absent provider is a property of
    the deployment, and letting it count against every stock's coverage would
    push the whole universe under MIN_COVERAGE for no reason to do with the
    stocks.
    """
    return tuple(m for m in METRICS if with_analyst or not m.optional)


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------
def _rank_expr(m: Metric) -> str:
    """Percentile-rank expression for one metric, in [0, 1], NULL-safe.

    SQLite sorts NULLs first, so a plain `PERCENT_RANK() OVER (ORDER BY col)`
    hands the lowest ranks to the rows that have no value and shifts every real
    value upward by the share of missing data. Subtracting the null count and
    dividing by the non-null count corrects for that; the CASE keeps missing
    values missing so `_category_score` can renormalize around them instead of
    reading them as "worst in the universe".

    `direction` is applied by flipping the rank rather than the sort, so the
    partition clause stays identical for every metric.
    """
    col = m.column
    part = "PARTITION BY panel.sector" if m.sector_neutral else ""
    over = f"OVER ({part} ORDER BY {col})".replace("(  ", "(")
    n_nulls = f"(COUNT(*) OVER ({part}) - COUNT({col}) OVER ({part}))".replace("(  ", "(")
    n_vals = f"COUNT({col}) OVER ({part})".replace("(  ", "(")
    raw = (f"(CAST(RANK() {over} AS REAL) - 1.0 - {n_nulls})"
           f" / NULLIF({n_vals} - 1, 0)")
    pct = raw if m.direction > 0 else f"(1.0 - ({raw}))"
    return f"CASE WHEN {col} IS NOT NULL THEN {pct} END AS pr_{m.key}"


def rank_sql(*, with_analyst: bool) -> str:
    """The percentile-rank layer: one `pr_<key>` column per active metric.

    Generated rather than written out because the whole point of the registry is
    that adding a metric does not mean editing scoring SQL by hand.
    """
    metrics = active_metrics(with_analyst=with_analyst)
    cols = ",\n    ".join(_rank_expr(m) for m in metrics)
    return f"SELECT panel.*,\n    {cols}\nFROM panel"


def _category_score(horizon: str, category: str, metrics: tuple[Metric, ...]) -> str:
    """Weighted mean of a category's available percentile ranks, renormalized.

    Both sums skip NULLs, so the denominator is the weight actually present and
    a stock missing one metric is scored on the rest rather than penalized for
    the gap. Returns NULL when the whole category is missing -- which is a
    different statement from zero and is what the coverage check reads.
    """
    weighted, weights = [], []
    for m in metrics:
        w = metric_weight(horizon, m)
        if w <= 0:
            continue
        weighted.append(f"COALESCE(pr_{m.key} * {w}, 0)")
        weights.append(f"CASE WHEN pr_{m.key} IS NOT NULL THEN {w} ELSE 0 END")
    if not weighted:
        return "NULL"
    return (f"(({' + '.join(weighted)})\n         "
            f"/ NULLIF({' + '.join(weights)}, 0))")


def score_sql(horizon: str, *, objective: str = "Balanced",
              with_analyst: bool, panel_where: str = "is_index = 0") -> str:
    """Full scoring SQL for one horizon: panel -> ranks -> categories -> score.

    The composite is a weighted mean over the categories that produced a score,
    renormalized by present weight -- the same rule as inside a category, one
    level up. `coverage` is the share of the horizon's weight that survived, and
    the caller filters on MIN_COVERAGE with it.

    `panel_where` is injected into the *panel CTE*, before any rank is computed,
    which is what makes a filtered board rank a stock against the filtered peer
    set rather than against everyone. It is a parameter rather than something
    the caller patches into the returned string: string surgery on generated SQL
    fails silently when the generator's wording changes -- the replace matches
    nothing, every filter vanishes, and the page renders a complete, plausible,
    unfiltered board with no error anywhere.
    """
    metrics = active_metrics(with_analyst=with_analyst)
    weights = category_weights(horizon, objective, with_analyst=with_analyst)
    present = [c for c in CATEGORIES
               if weights.get(c, 0) > 0 and any(m.category == c for m in metrics)]

    cat_cols, terms, denom = [], [], []
    for c in present:
        members = tuple(m for m in metrics if m.category == c)
        col = f"cat_{_slug(c)}"
        cat_cols.append(f"{_category_score(horizon, c, members)} AS {col}")
        terms.append(f"COALESCE({col} * {weights[c]}, 0)")
        denom.append(f"CASE WHEN {col} IS NOT NULL THEN {weights[c]} ELSE 0 END")

    if not terms:
        raise ValueError(f"{horizon}: no scoreable categories")

    cats = ",\n    ".join(cat_cols)
    coverage = f"({' + '.join(denom)})"
    composite = f"(({' + '.join(terms)}) / NULLIF({coverage}, 0))"

    return (
        "WITH panel AS (\n"
        f"    SELECT * FROM metric_panel WHERE {panel_where}\n"
        "),\n"
        "ranked AS (\n"
        f"{_indent(rank_sql(with_analyst=with_analyst))}\n"
        "),\n"
        "scored AS (\n"
        "    SELECT ranked.*,\n"
        f"    {cats}\n"
        "    FROM ranked\n"
        ")\n"
        "SELECT scored.*,\n"
        f"    {coverage} AS coverage,\n"
        f"    ROUND(100.0 * {composite}, 1) AS overall_score\n"
        "FROM scored"
    )


def _slug(name: str) -> str:
    return name.lower().replace(" ", "_").replace("/", "_")


def _indent(sql: str, spaces: int = 4) -> str:
    pad = " " * spaces
    return "\n".join(pad + line for line in sql.splitlines())


CATEGORY_COLUMNS: dict[str, str] = {c: f"cat_{_slug(c)}" for c in CATEGORIES}


# ---------------------------------------------------------------------------
# Reading a scored row back out
# ---------------------------------------------------------------------------
# Percentile thresholds for calling a metric a strength or a risk. 0.75/0.25 is
# the quartile boundary: strong enough to be worth naming, loose enough that a
# typical stock has something to say about it in both directions.
STRENGTH_AT = 0.75
RISK_AT = 0.25


def strengths_and_risks(row, *, with_analyst: bool,
                        limit: int = 4) -> tuple[list[dict], list[dict]]:
    """Split one scored row into its strongest and weakest metrics.

    Reads the percentile columns the SQL already produced -- this ranks nothing
    itself, it only selects which computed facts to show. Ordered by distance
    from the median so the most decisive metrics surface first.
    """
    strengths, risks = [], []
    for m in active_metrics(with_analyst=with_analyst):
        pr = row.get(f"pr_{m.key}")
        if pr is None or (isinstance(pr, float) and pr != pr):  # NaN
            continue
        item = {
            "key": m.key, "label": m.label, "category": m.category,
            "percentile": float(pr), "value": row.get(m.column),
            "fmt": m.fmt, "explain": m.explain,
            "sector_neutral": m.sector_neutral,
        }
        if pr >= STRENGTH_AT:
            strengths.append(item)
        elif pr <= RISK_AT:
            risks.append(item)
    strengths.sort(key=lambda d: -d["percentile"])
    risks.sort(key=lambda d: d["percentile"])
    return strengths[:limit], risks[:limit]


def category_breakdown(row, horizon: str, objective: str = "Balanced", *,
                       with_analyst: bool = True) -> list[dict]:
    """Per-category scores for one row, with the weight each carried."""
    weights = category_weights(horizon, objective, with_analyst=with_analyst)
    out = []
    for c in CATEGORIES:
        score = row.get(CATEGORY_COLUMNS[c])
        if score is None or (isinstance(score, float) and score != score):
            continue
        if weights.get(c, 0) <= 0:
            continue
        out.append({
            "category": c, "score": round(100.0 * float(score), 1),
            "weight": weights[c], "blurb": CATEGORY_BLURB[c],
        })
    return sorted(out, key=lambda d: -d["weight"])

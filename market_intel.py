"""The Market Intelligence engine: a transparent, data-driven ranking board.

Layout and phrasing only. Every figure on this page is a column from a SQL
query -- the board, the category scores, the metric breakdown. This module
computes no metric of its own, the same rule the rest of the app follows, and
the reason `ranking.py` generates SQL rather than scoring in pandas.

The AI boundary: `nlq.explain_ranking` is called *after* the board has been
queried and ordered, is handed the already-computed scores, and its response is
rendered as prose that nothing reads back. There is no path from a model
response to a rank -- enforced by the import graph, which `tests_ranking.py`
asserts, rather than by the wording of a prompt.

Live company research used to live here as a second view. It is now the front
of the Research page (see `research.py`), which is where a reader looks up one
company; `top_picks` below is the other half of that move, feeding the ranked
names onto the landing page so the engine is visible without navigating to it.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import charts
import components as ui
import data_access as da
import nlq
import ranking

# The board the landing page shows. Fixed, and deliberately NOT inherited from
# whatever the reader last chose on this page: the landing strip is the app's
# front door and has to say the same thing on every visit. A strip that silently
# reflected a Short-Term/Aggressive screen from a previous session would present
# a different set of companies as "today's" with nothing on screen explaining
# why.
LANDING_HORIZON = "Medium-Term"
LANDING_OBJECTIVE = "Balanced"

_FETCH_HINT = ("Run `python fetch_intel.py --all` to build the intelligence "
               "universe, then restart the app.")


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------
def score_chip(score: float, pal: dict) -> str:
    """A 0-100 score as a colored chip.

    Banded rather than a continuous gradient: a reader cannot resolve a
    continuous scale into a judgment, and the bands are stated in the
    methodology panel so the color is never the only carrier of the meaning --
    the number sits inside the chip.
    """
    if score >= 75:
        color, label = pal["up"], "strong"
    elif score >= 55:
        color, label = pal["series"][0], "solid"
    elif score >= 40:
        color, label = pal["neutral_mid"], "mixed"
    else:
        color, label = pal["down"], "weak"
    return (f'<span class="mi-chip" style="background:{charts.wash(color, 0.16)};'
            f'color:{color};border:1px solid {charts.wash(color, 0.35)}" '
            f'title="{ui.esc(label)}">{score:.0f}</span>')


def top_picks(version: str, limit: int = 4) -> pd.DataFrame:
    """The landing strip's rows: the highest-scoring names on the fixed board.

    Reads the same `da.rankings` the full page does, with no filters, so the
    strip cannot disagree with the board a reader reaches by clicking through --
    it is the first `limit` rows of it.
    """
    status = da.intel_status(version)
    if not status["symbols"] or not status["priced"]:
        return pd.DataFrame()
    return da.rankings(
        version, LANDING_HORIZON, LANDING_OBJECTIVE,
        with_analyst=status["with_analyst"] > 0, limit=limit,
    )


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------
def _engine(pal: dict, version: str) -> None:
    status = da.intel_status(version)
    if not status["symbols"] or not status["priced"]:
        ui.section("Market Intelligence Engine", "Transparent, data-driven stock ranking")
        ui.empty_state("The intelligence universe has not been built yet.", _FETCH_HINT)
        return

    with_analyst = status["with_analyst"] > 0

    ui.section("Market Intelligence Engine",
               f"{status['priced']} US stocks ranked on "
               f"{len(ranking.active_metrics(with_analyst=with_analyst))} objective metrics")

    horizon, objective, risk, sectors, caps, limit = _controls(version)

    board = da.rankings(
        version, horizon, objective, with_analyst=with_analyst,
        sectors=tuple(sectors), risk=risk, caps=tuple(caps), limit=limit,
    )
    if board.empty:
        ui.empty_state(
            "No stock clears these filters.",
            "Widen the risk tolerance, add sectors, or include more market-cap "
            "bands. A stock is also excluded when fewer than "
            f"{ranking.MIN_COVERAGE:.0%} of this horizon's metrics are available "
            "for it — a score built from a handful of inputs would not mean much.",
        )
        return

    _board_table(board, horizon, pal)
    _detail(board, horizon, objective, with_analyst, pal)
    _methodology(horizon, objective, with_analyst, status, version)


def _controls(version: str) -> tuple:
    row1 = st.columns([1.5, 1.5, 1.2])
    with row1[0]:
        ui.select_guard("mi_horizon", ranking.HORIZONS)
        horizon = st.radio("Horizon", ranking.HORIZONS, horizontal=True,
                           key="mi_horizon",
                           help="Which metrics dominate the score. "
                                "The weights differ per horizon by design.")
    with row1[1]:
        ui.select_guard("mi_objective", list(ranking.OBJECTIVES))
        objective = st.selectbox("Investment objective", list(ranking.OBJECTIVES),
                                 key="mi_objective",
                                 help="Tilts the category weights and renormalizes. "
                                      "Every objective still scores every category.")
    with row1[2]:
        ui.select_guard("mi_risk", list(ranking.RISK_BANDS))
        risk = st.selectbox("Risk tolerance", list(ranking.RISK_BANDS),
                            index=len(ranking.RISK_BANDS) - 1, key="mi_risk",
                            help="A filter on realized one-year volatility, "
                                 "not a scoring tilt.")

    row2 = st.columns([2.2, 2.2, 1])
    with row2[0]:
        available = da.intel_sectors(version)
        ui.multiselect_guard("mi_sectors", available)
        sectors = st.multiselect("Sector", available, key="mi_sectors",
                                 help="Leave empty for the whole universe. "
                                      "Sectors are derived from SEC SIC codes.")
    with row2[1]:
        ui.multiselect_guard("mi_caps", list(ranking.CAP_BANDS))
        caps = st.multiselect("Market cap", list(ranking.CAP_BANDS), key="mi_caps",
                              help="Market cap is shares outstanding from SEC "
                                   "filings times the latest close.")
    with row2[2]:
        ui.select_guard("mi_limit", [10, 25, 50])
        limit = st.selectbox("Show", [10, 25, 50], index=1, key="mi_limit")

    st.markdown(
        f'<div class="note">{ui.esc(ranking.HORIZON_BLURB[horizon])}</div>',
        unsafe_allow_html=True)
    return horizon, objective, risk, sectors, caps, limit


def _board_table(board: pd.DataFrame, horizon: str, pal: dict) -> None:
    show = board.head(50).copy()
    show.insert(0, "rank", range(1, len(show) + 1))

    cols = {"rank": "#", "symbol": "Symbol", "name": "Company", "sector": "Sector",
            "overall_score": "Score", "last_close": "Price",
            "market_cap": "Market cap", "vol_1y": "Volatility", "coverage": "Coverage"}
    present = [c for c in cols if c in show.columns]
    view = show[present].rename(columns=cols)

    st.dataframe(
        view, width="stretch", hide_index=True,
        column_config={
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%.1f",
                help="0-100 composite. Percentile-ranked within the filtered universe."),
            "Price": st.column_config.NumberColumn("Price", format="$%.2f"),
            "Market cap": st.column_config.NumberColumn("Market cap", format="compact"),
            "Volatility": st.column_config.NumberColumn("Volatility", format="percent"),
            "Coverage": st.column_config.NumberColumn(
                "Coverage", format="percent",
                help="Share of this horizon's metric weight that was available."),
        },
    )
    st.markdown(
        f'<div class="note">Ranked for <b>{ui.esc(horizon)}</b>. Percentiles are '
        f'positions within the <b>filtered</b> universe — changing a filter '
        f're-ranks against the new peer set, which is why a score moves when you '
        f'narrow to one sector.</div>', unsafe_allow_html=True)


def _detail(board: pd.DataFrame, horizon: str, objective: str,
            with_analyst: bool, pal: dict) -> None:
    labels = [f"{sym} — {nm}" for sym, nm in zip(board["symbol"], board["name"])]
    ui.select_guard("mi_pick", labels)
    picked = st.selectbox("Explain a ranking", labels, key="mi_pick")
    row = board.iloc[labels.index(picked)]
    data = row.to_dict()

    score = data.get("overall_score")
    st.markdown(
        f'<div class="mi-card"><h4>{ui.esc(str(data["symbol"]))} · '
        f'{ui.esc(str(data["name"]))} {score_chip(float(score), pal)}</h4>'
        f'<div class="note">{ui.esc(str(data.get("sector") or ""))} · '
        f'{ui.esc(horizon)} · {ui.esc(objective)} · '
        f'{data.get("coverage", 0):.0%} metric coverage</div></div>',
        unsafe_allow_html=True)

    breakdown = ranking.category_breakdown(data, horizon, objective,
                                           with_analyst=with_analyst)
    strengths, risks = ranking.strengths_and_risks(data, with_analyst=with_analyst)

    left, right = st.columns(2)
    with left:
        _category_panel(breakdown, pal)
        _factor_panel("Key strengths", strengths, pal["up"], pal)
    with right:
        _factor_panel("Risks and weak points", risks, pal["down"], pal)
        _breakdown_panel(data, horizon, with_analyst)

    _explanation(data, horizon, objective, breakdown, strengths, risks)


def _category_panel(breakdown: list[dict], pal: dict) -> None:
    if not breakdown:
        return
    out = ['<div class="mi-card"><h4>Category scores</h4>']
    for item in breakdown:
        color = (pal["up"] if item["score"] >= 65
                 else pal["down"] if item["score"] < 40 else pal["series"][0])
        out.append(
            f'<div class="mi-metric"><span class="k">{ui.esc(item["category"])}'
            f'<span class="mi-pct">weight {item["weight"]:.0%}</span></span>'
            f'<span class="v">{item["score"]:.0f}</span></div>'
            f'<div class="mi-bar"><span style="width:{max(1, item["score"]):.0f}%;'
            f'background:{color}"></span></div>')
    out.append("</div>")
    st.markdown("".join(out), unsafe_allow_html=True)


def _factor_panel(title: str, items: list[dict], color: str, pal: dict) -> None:
    out = [f'<div class="mi-card"><h4>{ui.esc(title)}</h4>']
    if not items:
        out.append('<div class="note">Nothing in the top or bottom quartile of '
                   'the filtered universe on this side.</div>')
    for item in items:
        neutral = " · vs sector" if item["sector_neutral"] else ""
        out.append(
            f'<div class="mi-metric"><span class="k">{ui.esc(item["label"])}'
            f'<span class="mi-pct">{item["percentile"]:.0%} percentile{neutral}'
            f'</span></span>'
            f'<span class="v" style="color:{color}">'
            f'{ui.esc(ui.fmt_metric(item["value"], item["fmt"]))}</span></div>')
    out.append("</div>")
    st.markdown("".join(out), unsafe_allow_html=True)


def _breakdown_panel(data: dict, horizon: str, with_analyst: bool) -> None:
    rows = []
    for m in ranking.active_metrics(with_analyst=with_analyst):
        if ranking.metric_weight(horizon, m) <= 0:
            continue
        pr = data.get(f"pr_{m.key}")
        pct = "—" if pr is None or pr != pr else f"{float(pr):.0%}"
        rows.append((m.label, f"{ui.fmt_metric(data.get(m.column), m.fmt)}  ({pct})"))
    if rows:
        ui.card("Full metric breakdown", rows)
        st.markdown('<div class="note">Value, then this stock\'s percentile within '
                    'the filtered universe. A dash means the metric was not '
                    'available and its weight was redistributed.</div>',
                    unsafe_allow_html=True)


def _explanation(data: dict, horizon: str, objective: str,
                 breakdown: list[dict], strengths: list[dict],
                 risks: list[dict]) -> None:
    ui.section("Why it ranked here", "Generated from the computed scores above")

    if not nlq.available():
        _fallback_explanation(data, horizon, breakdown, strengths, risks)
        return

    # Only computed figures cross this boundary -- no prices, no news, no free
    # text. The model is given the arithmetic the SQL produced and asked to read
    # it back in English.
    payload = json.dumps({
        "symbol": data.get("symbol"), "company": data.get("name"),
        "sector": data.get("sector"), "horizon": horizon, "objective": objective,
        "overall_score": data.get("overall_score"),
        "coverage": round(float(data.get("coverage") or 0), 3),
        "horizon_note": ranking.HORIZON_BLURB[horizon],
        "category_scores": [
            {"category": b["category"], "score": b["score"], "weight": round(b["weight"], 3)}
            for b in breakdown],
        "strengths": [
            {"metric": s["label"], "percentile": round(s["percentile"], 3),
             "value": ui.fmt_metric(s["value"], s["fmt"]), "means": s["explain"]}
            for s in strengths],
        "risks": [
            {"metric": r["label"], "percentile": round(r["percentile"], 3),
             "value": ui.fmt_metric(r["value"], r["fmt"]), "means": r["explain"]}
            for r in risks],
    }, default=str)

    with st.spinner("Reading the scores..."):
        text = nlq.explain_ranking(payload)
    if not text:
        _fallback_explanation(data, horizon, breakdown, strengths, risks)
        return
    st.markdown(f'<div class="mi-card">{ui.esc(text)}</div>'.replace("\n\n", "<br><br>"),
                unsafe_allow_html=True)
    st.markdown('<div class="note">Written from the computed scores on this page. '
                'The model receives the finished ranking and explains it — it '
                'never produces or revises one.</div>', unsafe_allow_html=True)


def _fallback_explanation(data: dict, horizon: str, breakdown: list[dict],
                          strengths: list[dict], risks: list[dict]) -> None:
    """The no-API-key explanation: assembled from the same computed figures.

    The page must be complete without a model, so this states the ranking in
    prose from the scores themselves. It phrases; it computes nothing.
    """
    lead = breakdown[0] if breakdown else None
    best = max(breakdown, key=lambda b: b["score"]) if breakdown else None
    worst = min(breakdown, key=lambda b: b["score"]) if breakdown else None

    parts = [
        f"<b>{ui.esc(str(data.get('symbol')))}</b> scores "
        f"<b>{float(data.get('overall_score') or 0):.0f}</b> out of 100 on the "
        f"{ui.esc(horizon.lower())} board."]
    if best:
        parts.append(f"Its strongest category is <b>{ui.esc(best['category'])}</b> "
                     f"at {best['score']:.0f}"
                     + (f", which carries {best['weight']:.0%} of the weight at this "
                        f"horizon." if best is lead else "."))
    if strengths:
        named = ", ".join(f"{ui.esc(s['label'])} ({s['percentile']:.0%} percentile)"
                          for s in strengths[:3])
        parts.append(f"It sits in the top quartile on {named}.")
    if worst and worst["score"] < 50:
        parts.append(f"The screen marks <b>{ui.esc(worst['category'])}</b> against it "
                     f"at {worst['score']:.0f}.")
    if risks:
        named = ", ".join(f"{ui.esc(r['label'])} ({r['percentile']:.0%})"
                          for r in risks[:3])
        parts.append(f"Bottom-quartile on {named}.")
    parts.append("These are positions within the filtered universe, not absolute "
                 "judgments, and the screen sees only reported financials and "
                 "price history — not management, competition or anything "
                 "announced since the last filing.")

    st.markdown(f'<div class="mi-card">{" ".join(parts)}</div>', unsafe_allow_html=True)
    st.markdown('<div class="note">Written without a model — the app has no '
                'ANTHROPIC_API_KEY set. Set one for a fuller reading of the same '
                'figures; the rankings are identical either way.</div>',
                unsafe_allow_html=True)


def _methodology(horizon: str, objective: str, with_analyst: bool,
                 status: dict, version: str) -> None:
    with st.expander("Methodology — how these scores are computed"):
        st.markdown(f"""
**Scoring.** Every metric is converted to a **percentile rank within the
filtered universe**, then combined by weight. Percentile rank rather than a
z-score because financial cross-sections are heavily skewed — a single 400x P/E
moves a mean and a standard deviation enough to compress everything else toward
the middle, while a percentile cannot be moved by an outlier at all.

**Valuation is ranked within sector.** A P/E percentile pooled across utilities
and software ranks sectors rather than companies, because software trades richer
for structural reasons.

**Missing data renormalizes; it is never zero-filled.** A stock with no
fundamentals would otherwise score 0 on every valuation metric and rank as
expensive rather than as unknown. Weights are redistributed across the metrics
actually present, and a stock below **{ranking.MIN_COVERAGE:.0%}** coverage is
excluded from the board entirely rather than shown with a confident number built
from a handful of inputs.

**{ui.esc(ranking.MOMENTUM_NOTE)}**

**The AI never ranks.** Scores come from SQL over reported data. The model is
handed the finished result and asked to explain it.

**Honest limits.** Price returns exclude dividends. Fundamentals are as-filed
and lag the market by up to a quarter. Sectors are derived from SEC SIC codes,
which is a 1987 scheme mapped to modern sector names — close, not official.
Companies whose ticker points at a successor CIK with no XBRL history carry no
fundamentals at all. {"Analyst data is configured." if with_analyst else
"Analyst metrics are **disabled** — no keyless provider serves consensus data, so that category is dropped and its weight redistributed."}
""")
        st.markdown(f"**Universe.** {status['priced']} priced symbols · "
                    f"{status['with_fundamentals']} with SEC fundamentals · "
                    f"prices through {status['last_date']} · "
                    f"newest filing {status['fundamentals_asof']}.")

        st.markdown("**The generated scoring SQL for this board:**")
        st.code(da.ranking_sql_preview(version, horizon, objective, with_analyst),
                language="sql")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def render(pal: dict) -> None:
    """Render the ranking engine. Called from `app.py` with the active palette."""
    _engine(pal, da.intel_version())

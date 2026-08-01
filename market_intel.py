"""The Market Intelligence page: live company research and the ranking board.

Layout and phrasing only. Every figure on this page is either a column from a
SQL query (the ranking board, the metric breakdown, the fundamentals) or a field
straight off the provider payload (the live quote, the chart, the headlines).
This module computes no metric of its own -- the same rule the rest of the app
follows, and the reason `ranking.py` generates SQL rather than scoring in pandas.

The AI boundary: `nlq.explain_ranking` is called *after* the board has been
queried and ordered, is handed the already-computed scores, and its response is
rendered as prose that nothing reads back. There is no path from a model
response to a rank.

Two experiences, switched by a radio rather than `st.tabs` -- `st.tabs` renders
every tab body on every rerun, which here would mean the live research panel
hitting the network on every ranking filter change and vice versa. Same reason
the rest of the app avoids it.
"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import streamlit as st

import charts
import components as ui
import data_access as da
import live_data
import nlq
import ranking

VIEWS = ("Live Research", "Intelligence Engine")

# Chart spans offered on the research panel, in the order a reader scans them.
SPANS = ("1D", "5D", "1M", "6M", "YTD", "1Y", "5Y", "Max")
DEFAULT_SPAN = "1Y"

_FETCH_HINT = ("Run `python fetch_intel.py --all` to build the intelligence "
               "universe, then restart the app.")


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def _fmt(value, kind: str) -> str:
    """One formatter for every metric, driven by the registry's `fmt` field.

    Centralised because the same metric is printed in four places (board,
    breakdown, strengths, risks) and a ratio that renders as a percentage in one
    of them is the kind of inconsistency that makes a reader stop trusting the
    page.
    """
    if value is None or (isinstance(value, float) and value != value):
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if kind == "pct":
        return f"{v:+.1%}" if abs(v) < 100 else f"{v:,.0f}"
    if kind == "money":
        return ui.fmt_dollar_compact(v)
    if kind == "ratio":
        return f"{v:,.2f}x"
    return f"{v:,.2f}"


def _score_chip(score: float, pal: dict) -> str:
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


def css(pal: dict) -> str:
    """Page-scoped styling, built from the active palette.

    Every color comes from the palette that already passed the app's contrast
    and color-vision validation -- nothing here introduces a new hue.
    """
    return f"""
<style>
.mi-chip {{ display:inline-block; min-width:2.4rem; text-align:center;
  padding:.12rem .45rem; border-radius:6px; font-weight:700; font-size:.86rem; }}
.mi-card {{ background:{pal['surface']}; border:1px solid {pal['border']};
  border-radius:10px; padding:.85rem 1rem; margin-bottom:.6rem; }}
.mi-card h4 {{ margin:0 0 .35rem 0; font-size:.95rem; color:{pal['text_primary']}; }}
.mi-metric {{ display:flex; justify-content:space-between; gap:1rem;
  padding:.3rem 0; border-bottom:1px solid {pal['border']}; font-size:.9rem; }}
.mi-metric:last-child {{ border-bottom:none; }}
.mi-metric .k {{ color:{pal['text_secondary']}; }}
.mi-metric .v {{ color:{pal['text_primary']}; font-weight:600;
  font-variant-numeric:tabular-nums; }}
.mi-pct {{ font-size:.78rem; color:{pal['text_secondary']}; margin-left:.4rem; }}
.mi-bar {{ height:6px; border-radius:3px; background:{pal['surface_2']};
  overflow:hidden; margin-top:.25rem; }}
.mi-bar > span {{ display:block; height:100%; border-radius:3px; }}
.mi-news {{ padding:.45rem 0; border-bottom:1px solid {pal['border']}; }}
.mi-news:last-child {{ border-bottom:none; }}
.mi-news a {{ color:{pal['text_primary']}; text-decoration:none; font-weight:600;
  font-size:.9rem; }}
.mi-news a:hover {{ color:{pal['accent']}; text-decoration:underline; }}
.mi-news .src {{ color:{pal['text_secondary']}; font-size:.78rem; margin-top:.15rem; }}
.mi-good {{ color:{pal['up']}; }}
.mi-bad {{ color:{pal['down']}; }}
</style>"""


def _drop_stale(key: str, options) -> None:
    """Clear a widget key whose stored value is no longer offered.

    `st.radio` and `st.selectbox` raise when `session_state[key]` is not in
    `options`, and both widgets on this page have options that change between
    runs: the search matches change on every keystroke, and the "explain a
    ranking" list changes whenever a filter moves. Without this, typing a second
    query or narrowing a sector raises `StreamlitAPIException` on a page that
    was working a moment earlier.

    Must run BEFORE the widget is instantiated -- the same rule as the Ask
    suggestion chips and the Journey cursor: Streamlit forbids writing a widget
    key after the widget exists in the current run.
    """
    if key in st.session_state and st.session_state[key] not in options:
        del st.session_state[key]


def _drop_stale_multi(key: str, options) -> None:
    """The multiselect form: keep the still-valid choices, drop the rest.

    Clearing the whole selection would throw away a reader's other filters
    because one sector stopped being offered.
    """
    if key in st.session_state:
        kept = [v for v in st.session_state[key] if v in options]
        if len(kept) != len(st.session_state[key]):
            st.session_state[key] = kept


def _metric_rows(rows: list[tuple[str, str]]) -> str:
    return "".join(
        f'<div class="mi-metric"><span class="k">{ui.esc(k)}</span>'
        f'<span class="v">{ui.esc(v)}</span></div>' for k, v in rows)


def _card(title: str, rows: list[tuple[str, str]]) -> None:
    st.markdown(f'<div class="mi-card"><h4>{ui.esc(title)}</h4>'
                f'{_metric_rows(rows)}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Live Research
# ---------------------------------------------------------------------------
def _research(pal: dict, version: str) -> None:
    ui.section("Live Company Research",
               "Any US-listed stock on NYSE, Nasdaq or NYSE American")

    left, right = st.columns([3, 1.4])
    with left:
        typed = st.text_input(
            "Search", key="mi_search", placeholder="Ticker or company name — AAPL, Rocket Lab, Costco",
            label_visibility="collapsed",
        )
    with right:
        st.markdown('<div class="note">Live quote · delayed by the exchange</div>',
                    unsafe_allow_html=True)

    symbol = st.session_state.get("mi_symbol", "AAPL")
    if typed.strip():
        matches = live_data.search(typed)
        if not matches:
            ui.empty_state(
                f"Nothing US-listed matches “{typed}”.",
                "Try a ticker (AAPL) or a fuller company name. Only NYSE, Nasdaq "
                "and NYSE American equities are searchable — funds, ADR lines on "
                "foreign venues and indices are filtered out.",
            )
            return
        labels = [f"{m['symbol']} — {m['name']} ({m['exchange']})" for m in matches]
        _drop_stale("mi_match", labels)
        chosen = st.radio("Match", labels, horizontal=False, key="mi_match",
                          label_visibility="collapsed")
        symbol = matches[labels.index(chosen)]["symbol"]
        st.session_state["mi_symbol"] = symbol

    quote = live_data.quote(symbol)
    if not quote:
        ui.empty_state(
            f"No live quote came back for {symbol}.",
            "The provider may be rate-limiting or the ticker may have been "
            "delisted. Try again shortly, or search for a different company.",
            kind="error",
        )
        return

    _quote_header(quote, pal)
    _price_chart(symbol, quote, pal)

    stored = da.panel_row(version, symbol)
    col_a, col_b = st.columns(2)
    with col_a:
        _profile_card(quote, stored)
        _valuation_card(stored)
    with col_b:
        _financials_card(stored)
        _analyst_card(stored)

    _performance_card(symbol, pal)
    _news_panel(symbol)


def _quote_header(q: dict, pal: dict) -> None:
    change, pct = q.get("change"), q.get("change_pct")
    direction = "flat" if not change else ("up" if change > 0 else "down")
    stamp = ""
    if q.get("market_time"):
        stamp = dt.datetime.fromtimestamp(q["market_time"]).strftime("%b %d, %Y %H:%M")

    ui.kpi_cards([
        {"icon": "💵", "label": "Price",
         "value": ui.fmt_price(q["price"]),
         "change": f"{ui.fmt_price(abs(change)) if change else '—'} "
                   f"({ui.fmt_pct(pct) if pct is not None else '—'})",
         "change_dir": direction,
         "foot": f"{q.get('exchange') or ''} · {stamp}"},
        {"icon": "📊", "label": "Day Range",
         "value": f"{ui.fmt_price(q.get('day_low'))} – {ui.fmt_price(q.get('day_high'))}",
         "small": True, "foot": "Session low to high"},
        {"icon": "📈", "label": "52-Week Range",
         "value": f"{ui.fmt_price(q.get('w52_low'))} – {ui.fmt_price(q.get('w52_high'))}",
         "small": True, "foot": "Trailing one year"},
        {"icon": "🔄", "label": "Volume",
         "value": ui.fmt_compact(q.get("volume")),
         "small": True, "foot": "Shares traded this session"},
    ])


def _price_chart(symbol: str, quote: dict, pal: dict) -> None:
    span = st.radio("Span", SPANS, horizontal=True, index=SPANS.index(DEFAULT_SPAN),
                    key="mi_span", label_visibility="collapsed")
    df = live_data.history(symbol, span)
    if df.empty:
        ui.empty_state(f"No price history came back for {symbol} over {span}.",
                       "Try a longer span — newly listed companies have no "
                       "five-year record to draw.")
        return

    view = st.radio("View", ["Line", "Candles"], horizontal=True, key="mi_view",
                    label_visibility="collapsed")
    if view == "Candles" and {"open", "high", "low"}.issubset(df.columns) \
            and not df[["open", "high", "low"]].isna().all().any():
        fig = charts.candlestick(df, pal)
    else:
        fig = charts.price_line(df, pal, label=ui.fmt_price(quote["price"]))
    ui.chart(fig, key=f"mi_price_{symbol}_{span}",
             config={"displayModeBar": False},
             caption=f"{ui.esc(quote['name'])} · {span} · {len(df):,} sessions")
    ui.table_view("Show the prices behind this chart", df)


def _profile_card(quote: dict, stored) -> None:
    rows = [
        ("Name", quote.get("name") or "—"),
        ("Exchange", quote.get("exchange") or "—"),
        ("Currency", quote.get("currency") or "—"),
    ]
    if stored is not None:
        rows.append(("Sector", str(stored.get("sector") or "—")))
        rows.append(("Market cap", _fmt(stored.get("market_cap"), "money")))
        rows.append(("Shares outstanding", ui.fmt_compact(stored.get("shares_outstanding"))))
    if quote.get("first_trade"):
        first = dt.datetime.fromtimestamp(quote["first_trade"]).strftime("%b %d, %Y")
        rows.append(("First traded", first))
    _card("Company profile", rows)
    if stored is None:
        st.markdown(
            '<div class="note">Sector, market cap and fundamentals come from SEC '
            'filings and are stored for the ranked universe only. This company '
            'is outside it, so only live price data is shown.</div>',
            unsafe_allow_html=True)


def _valuation_card(stored) -> None:
    if stored is None:
        return
    keys = [("pe", "P/E"), ("ps", "P/S"), ("pb", "P/B"), ("fcf_yield", "FCF yield")]
    rows = [(label, _fmt(stored.get(k), ranking.BY_KEY[k].fmt)) for k, label in keys]
    _card("Valuation", rows)
    if stored.get("pe") is None and stored.get("net_income") is not None:
        st.markdown('<div class="note">P/E is blank because trailing earnings are '
                    'negative — a negative multiple is not a cheap one.</div>',
                    unsafe_allow_html=True)


def _financials_card(stored) -> None:
    if stored is None:
        return
    rows = [
        ("Revenue (TTM)", _fmt(stored.get("revenue"), "money")),
        ("Net income (TTM)", _fmt(stored.get("net_income"), "money")),
        ("Total assets", _fmt(stored.get("assets"), "money")),
        ("Shareholder equity", _fmt(stored.get("equity"), "money")),
        ("Gross margin", _fmt(stored.get("gross_margin"), "pct")),
        ("Net margin", _fmt(stored.get("net_margin"), "pct")),
        ("Return on equity", _fmt(stored.get("roe"), "pct")),
        ("Revenue growth (YoY)", _fmt(stored.get("revenue_growth"), "pct")),
    ]
    _card("Key financials · SEC filings", rows)
    if stored.get("fundamentals_asof"):
        st.markdown(f'<div class="note">Latest reported period ending '
                    f'{ui.esc(str(stored["fundamentals_asof"]))}. Figures are as '
                    f'filed with the SEC in XBRL.</div>', unsafe_allow_html=True)


def _analyst_card(stored) -> None:
    has = stored is not None and stored.get("analyst_score") is not None
    if has:
        _card("Analyst consensus", [
            ("Consensus rating", _fmt(stored.get("analyst_score"), "num")),
            ("Price target upside", _fmt(stored.get("target_upside"), "pct")),
            ("EPS revision trend", _fmt(stored.get("eps_revision"), "pct")),
            ("Analysts covering", ui.fmt_compact(stored.get("n_analysts"))),
        ])
        return
    ui.empty_state(
        "Analyst consensus is not configured.",
        "No keyless provider serves consensus ratings, price targets or earnings "
        "estimates — Yahoo's endpoints return 401. Set an optional provider key "
        "to enable this panel; the rankings run without it and simply drop the "
        "analyst category.",
        kind="empty",
    )


def _performance_card(symbol: str, pal: dict) -> None:
    perf = live_data.performance(symbol)
    if not perf:
        return
    ui.section("Performance", "Trailing total price return, live from the provider")
    df = pd.DataFrame({"period": list(perf), "return": [perf[k] for k in perf]})
    fig = charts.generic_bars(df, pal, label_col="period", value_col="return",
                              height=charts.H_COMPACT, tickformat=".0%")
    ui.chart(fig, key=f"mi_perf_{symbol}", config={"displayModeBar": False},
             caption="Price return only — dividends are excluded, as everywhere "
                     "else in this app.")


def _news_panel(symbol: str) -> None:
    items = live_data.news(symbol)
    ui.section("Recent news", "Headlines from the provider, linked to the source")
    if not items:
        ui.empty_state("No recent headlines came back for this company.",
                       "Coverage is thinner for smaller companies. Try a larger "
                       "or more widely followed name.")
        return
    out = ['<div class="mi-card">']
    for item in items:
        when = ""
        if item.get("published"):
            when = dt.datetime.fromtimestamp(item["published"]).strftime("%b %d, %Y")
        link = ui.esc(item["link"])
        out.append(
            f'<div class="mi-news"><a href="{link}" target="_blank" rel="noopener">'
            f'{ui.esc(item["title"])}</a>'
            f'<div class="src">{ui.esc(item["publisher"])} · {ui.esc(when)}</div></div>')
    out.append("</div>")
    st.markdown("".join(out), unsafe_allow_html=True)
    st.markdown('<div class="note">Headlines are shown as published and are never '
                'summarized by the model — a paraphrase of an article the model '
                'cannot open is a fabrication risk with no upside.</div>',
                unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Intelligence Engine
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
        horizon = st.radio("Horizon", ranking.HORIZONS, horizontal=True,
                           key="mi_horizon",
                           help="Which metrics dominate the score. "
                                "The weights differ per horizon by design.")
    with row1[1]:
        objective = st.selectbox("Investment objective", list(ranking.OBJECTIVES),
                                 key="mi_objective",
                                 help="Tilts the category weights and renormalizes. "
                                      "Every objective still scores every category.")
    with row1[2]:
        risk = st.selectbox("Risk tolerance", list(ranking.RISK_BANDS),
                            index=len(ranking.RISK_BANDS) - 1, key="mi_risk",
                            help="A filter on realized one-year volatility, "
                                 "not a scoring tilt.")

    row2 = st.columns([2.2, 2.2, 1])
    with row2[0]:
        available = da.intel_sectors(version)
        _drop_stale_multi("mi_sectors", available)
        sectors = st.multiselect("Sector", available, key="mi_sectors",
                                 help="Leave empty for the whole universe. "
                                      "Sectors are derived from SEC SIC codes.")
    with row2[1]:
        caps = st.multiselect("Market cap", list(ranking.CAP_BANDS), key="mi_caps",
                              help="Market cap is shares outstanding from SEC "
                                   "filings times the latest close.")
    with row2[2]:
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
    _drop_stale("mi_pick", labels)
    picked = st.selectbox("Explain a ranking", labels, key="mi_pick")
    row = board.iloc[labels.index(picked)]
    data = row.to_dict()

    score = data.get("overall_score")
    st.markdown(
        f'<div class="mi-card"><h4>{ui.esc(str(data["symbol"]))} · '
        f'{ui.esc(str(data["name"]))} {_score_chip(float(score), pal)}</h4>'
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
            f'{ui.esc(_fmt(item["value"], item["fmt"]))}</span></div>')
    out.append("</div>")
    st.markdown("".join(out), unsafe_allow_html=True)


def _breakdown_panel(data: dict, horizon: str, with_analyst: bool) -> None:
    rows = []
    for m in ranking.active_metrics(with_analyst=with_analyst):
        if ranking.metric_weight(horizon, m) <= 0:
            continue
        pr = data.get(f"pr_{m.key}")
        pct = "—" if pr is None or pr != pr else f"{float(pr):.0%}"
        rows.append((m.label, f"{_fmt(data.get(m.column), m.fmt)}  ({pct})"))
    if rows:
        _card("Full metric breakdown", rows)
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
             "value": _fmt(s["value"], s["fmt"]), "means": s["explain"]}
            for s in strengths],
        "risks": [
            {"metric": r["label"], "percentile": round(r["percentile"], 3),
             "value": _fmt(r["value"], r["fmt"]), "means": r["explain"]}
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
    """Render the page. Called from `app.py` with the active palette."""
    st.markdown(css(pal), unsafe_allow_html=True)
    version = da.intel_version()

    view = st.radio("View", VIEWS, horizontal=True, key="mi_view_mode",
                    label_visibility="collapsed")
    if view == "Live Research":
        _research(pal, version)
    else:
        _engine(pal, version)

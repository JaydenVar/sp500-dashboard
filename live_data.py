"""Live market data for the Market Intelligence page: the Yahoo chart API.

The one place in the app that reaches the network at request time. Everything
else reads SQLite, and this module is deliberately narrow so that stays true:
it fetches, it normalizes, and it hands back plain data. It computes no metric
and renders nothing.

**Every function returns None (or an empty frame) rather than raising.** This
app deploys publicly; a provider outage, a rate limit or a delisted ticker must
degrade one panel, not throw a traceback onto the page. Same rule as `nlq.py`.

Provider choice, measured rather than assumed (2026-08-01):

    v8/finance/chart      200  — prices + a live quote block, any US ticker
    v1/finance/search     200  — symbol lookup with exchange and quote type
    v10/quoteSummary      401  — auth-gated
    v7/finance/quote      401  — auth-gated

So prices and the quote strip come from the chart endpoint, symbol search from
the search endpoint, and *fundamentals do not come from Yahoo at all* -- they
come from SEC EDGAR via `fetch_fundamentals.py`, which is authoritative and
needs no key. The two auth-gated endpoints above are why: they are the ones that
would otherwise serve valuation and profile data.

The bare `Mozilla/5.0` User-Agent is deliberate and is the same measured choice
`fetch_data.py` documents -- a full Chrome UA plus Referer gets 429'd. Do not
"improve" it.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd
import streamlit as st

UA = "Mozilla/5.0"
TIMEOUT = 12

# Live quotes: short enough to feel live, long enough that a page rerun (which
# Streamlit does on every widget interaction) does not re-hit the provider.
QUOTE_TTL = 60
HISTORY_TTL = 900
SEARCH_TTL = 3600

# Yahoo's `fullExchangeName` values for the three US venues the page supports.
# Matched as a prefix set because Yahoo varies the suffix (NasdaqGS/NasdaqGM/
# NasdaqCM are all NASDAQ tiers and all in scope).
US_EXCHANGE_PREFIXES = ("NYSE", "Nasdaq", "NMS", "NGM", "NCM", "NYQ", "ASE",
                        "AMEX", "NYSEArca", "BATS", "PCX")

RANGES: dict[str, tuple[str, str]] = {
    "1D": ("1d", "5m"),
    "5D": ("5d", "30m"),
    "1M": ("1mo", "1d"),
    "6M": ("6mo", "1d"),
    "YTD": ("ytd", "1d"),
    "1Y": ("1y", "1d"),
    "5Y": ("5y", "1wk"),
    "Max": ("max", "1mo"),
}


def _get(url: str) -> dict | None:
    """One GET returning parsed JSON, or None on any failure whatsoever."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            json.JSONDecodeError, ValueError, OSError):
        return None


def _is_us_listed(row: dict) -> bool:
    exch = str(row.get("exchDisp") or row.get("fullExchangeName") or "")
    return any(exch.upper().startswith(p.upper()) for p in US_EXCHANGE_PREFIXES)


# ---------------------------------------------------------------------------
# Symbol search
# ---------------------------------------------------------------------------
@st.cache_data(ttl=SEARCH_TTL, show_spinner=False)
def search(query: str, limit: int = 12) -> list[dict]:
    """US-listed equities matching a name or ticker fragment.

    Filtered to `quoteType == EQUITY` on a US venue, so the picker cannot offer
    a London line of the same company or an ETF the ranking engine has no
    fundamentals for.
    """
    q = (query or "").strip()
    if len(q) < 1:
        return []
    url = ("https://query2.finance.yahoo.com/v1/finance/search?q="
           + urllib.parse.quote(q, safe="")
           + "&quotesCount=25&newsCount=0&listsCount=0")
    payload = _get(url)
    if not payload:
        return []

    out: list[dict] = []
    for row in payload.get("quotes") or []:
        if str(row.get("quoteType") or "").upper() != "EQUITY":
            continue
        if not _is_us_listed(row):
            continue
        symbol = str(row.get("symbol") or "").upper()
        if not symbol or any(o["symbol"] == symbol for o in out):
            continue
        out.append({
            "symbol": symbol,
            "name": str(row.get("shortname") or row.get("longname") or symbol),
            "exchange": str(row.get("exchDisp") or ""),
            "industry": str(row.get("industry") or ""),
            "sector": str(row.get("sector") or ""),
        })
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Quote + history
# ---------------------------------------------------------------------------
def _chart_payload(symbol: str, rng: str, interval: str) -> dict | None:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol, safe='')}"
           f"?range={rng}&interval={interval}&includePrePost=false")
    payload = _get(url)
    try:
        return payload["chart"]["result"][0]
    except (TypeError, KeyError, IndexError):
        return None


@st.cache_data(ttl=QUOTE_TTL, show_spinner=False)
def quote(symbol: str) -> dict | None:
    """Live quote block for one symbol, or None if it can't be fetched.

    The change figure is computed against `chartPreviousClose` rather than the
    session open, so it matches what every other quote screen prints.
    """
    result = _chart_payload(symbol, "1d", "1d")
    if not result:
        return None
    m = result.get("meta") or {}
    price = m.get("regularMarketPrice")
    prev = m.get("chartPreviousClose") or m.get("previousClose")
    if price is None:
        return None

    change = None if prev in (None, 0) else price - prev
    change_pct = None if prev in (None, 0) else (price - prev) / prev
    return {
        "symbol": str(m.get("symbol") or symbol).upper(),
        "name": str(m.get("longName") or m.get("shortName") or symbol),
        "price": price,
        "prev_close": prev,
        "change": change,
        "change_pct": change_pct,
        "currency": m.get("currency"),
        "exchange": m.get("fullExchangeName"),
        "day_high": m.get("regularMarketDayHigh"),
        "day_low": m.get("regularMarketDayLow"),
        "volume": m.get("regularMarketVolume"),
        "w52_high": m.get("fiftyTwoWeekHigh"),
        "w52_low": m.get("fiftyTwoWeekLow"),
        "first_trade": m.get("firstTradeDate"),
        "market_time": m.get("regularMarketTime"),
        "timezone": m.get("exchangeTimezoneName"),
    }


@st.cache_data(ttl=HISTORY_TTL, show_spinner=False)
def history(symbol: str, span: str = "1Y") -> pd.DataFrame:
    """OHLCV for a named span. Empty frame (never None) so callers can just check
    `.empty` -- a mix of None and DataFrame at call sites is how the chart
    helpers end up with a `NoneType has no attribute` on a provider hiccup."""
    rng, interval = RANGES.get(span, RANGES["1Y"])
    result = _chart_payload(symbol, rng, interval)
    if not result:
        return pd.DataFrame()

    stamps = result.get("timestamp") or []
    try:
        q = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return pd.DataFrame()

    rows = []
    for i, ts in enumerate(stamps):
        close = (q.get("close") or [None] * len(stamps))[i]
        if close is None:
            continue  # halted or holiday session with no print
        rows.append({
            "date": pd.to_datetime(ts, unit="s"),
            "open": (q.get("open") or [None])[i] if q.get("open") else None,
            "high": (q.get("high") or [None])[i] if q.get("high") else None,
            "low": (q.get("low") or [None])[i] if q.get("low") else None,
            "close": close,
            "volume": (q.get("volume") or [None])[i] if q.get("volume") else None,
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


@st.cache_data(ttl=QUOTE_TTL * 5, show_spinner=False)
def news(symbol: str, limit: int = 6) -> list[dict]:
    """Recent headlines for a symbol.

    The same search endpoint that serves symbol lookup also returns news, which
    is why the research panel has headlines at all -- the dedicated news
    endpoints sit behind the same 401 as `quoteSummary`.

    Headlines are rendered as links to the publisher and never summarized by the
    model: an LLM paraphrase of a headline it cannot open is a fabrication risk
    for no benefit, and this app's rule is that the model never states a fact it
    was not handed.
    """
    q = (symbol or "").strip()
    if not q:
        return []
    url = ("https://query2.finance.yahoo.com/v1/finance/search?q="
           + urllib.parse.quote(q, safe="")
           + f"&quotesCount=0&newsCount={max(1, min(limit, 20))}&listsCount=0")
    payload = _get(url)
    if not payload:
        return []

    out = []
    for item in payload.get("news") or []:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "publisher": str(item.get("publisher") or ""),
            "link": str(item.get("link") or ""),
            "published": item.get("providerPublishTime"),
            "thumb": _thumb(item),
        })
        if len(out) >= limit:
            break
    return out


def _thumb(item: dict) -> str:
    """The smallest thumbnail on a news item, or "" if it carries none.

    Yahoo sends each story's art at two sizes: a 140x140 crop and an `original`
    that runs up to 3840px wide. The row renders it at ~52px, so the crop is the
    only sane pick -- reaching for `original` would pull megabytes across to
    paint a thumbnail, and the browser would throw all of it away on the resize.

    Smallest-wins rather than a hardcoded "140x140" tag because the tag set is
    the provider's to change; picking by width still does the right thing if a
    third size appears.
    """
    resolutions = ((item.get("thumbnail") or {}).get("resolutions")) or []
    sized = [r for r in resolutions if r.get("url") and r.get("width")]
    if not sized:
        # No width to sort on: take the first URL there is rather than nothing.
        return next((str(r["url"]) for r in resolutions if r.get("url")), "")
    return str(min(sized, key=lambda r: r["width"])["url"])


# ---------------------------------------------------------------------------
# Company logos
# ---------------------------------------------------------------------------
# Unlike everything else in this module, logos are never fetched here -- this
# hands back a URL and the browser loads it directly. That is deliberate: a logo
# is decoration, and proxying one small image per company through the server
# would add latency and a failure mode to a panel that must never block on one.
#
# Financial Modeling Prep's image endpoint needs no key. Measured 2026-08-01
# against the stored universe plus names outside it (BRK-B, RKLB): every ticker
# resolved, and an unknown one 404s cleanly instead of returning a placeholder
# image -- which is what lets the CSS treat a miss as an empty tile.
#
# Clearbit, the other obvious candidate, is gone: `logo.clearbit.com` no longer
# resolves in DNS. Do not "restore" it.
LOGO_URL = "https://financialmodelingprep.com/image-stock/{symbol}.png"

# Tickers only: letters, digits, dot, dash (BRK-B, BF.B). Anything else is not a
# symbol, and refusing it here is what keeps an arbitrary string out of the URL.
_TICKER = re.compile(r"[A-Z0-9.\-]{1,12}")


def logo_url(symbol: str) -> str:
    """The logo endpoint for a ticker, or "" when the input is not one."""
    sym = (symbol or "").strip().upper()
    return LOGO_URL.format(symbol=sym) if _TICKER.fullmatch(sym) else ""


@st.cache_data(ttl=HISTORY_TTL, show_spinner=False)
def performance(symbol: str) -> dict:
    """Trailing returns over standard spans, from one Max-range fetch.

    Computed here rather than in SQL because these are *live* series for a
    symbol that may not be in the database at all -- the whole point of the
    research panel is that it works for any US ticker. Everything the ranking
    engine scores still comes from SQL over stored rows; nothing in this dict
    feeds a score.
    """
    df = history(symbol, "Max")
    if df.empty or len(df) < 2:
        return {}

    last = df["close"].iloc[-1]
    end = df["date"].iloc[-1]
    out: dict[str, float] = {}
    for label, days in (("1M", 30), ("3M", 91), ("6M", 182),
                        ("1Y", 365), ("3Y", 1095), ("5Y", 1826)):
        cutoff = end - pd.Timedelta(days=days)
        prior = df[df["date"] <= cutoff]
        if prior.empty:
            continue
        base = prior["close"].iloc[-1]
        if base:
            out[label] = (last - base) / base

    first = df["close"].iloc[0]
    if first:
        out["All"] = (last - first) / first
    return out

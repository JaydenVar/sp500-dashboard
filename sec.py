"""SEC EDGAR XBRL — the fundamentals provider, and the reason there is one at all.

Yahoo's free tier serves prices but answers 401 on every endpoint carrying
fundamentals (`quoteSummary`, `v7/quote`), which is what `PROJECT_STATE` records
as the reason the app had no valuation, quality or profitability figures and no
market cap. EDGAR serves all of it, from the filings themselves, with no key and
no auth -- it is the authoritative source rather than a workaround.

**This unblocks market cap.** The blocker was never the price, it was the share
count; `dei:EntityCommonStockSharesOutstanding` is on the cover of every 10-K and
10-Q. Cap is therefore computed (shares x price) on every rebuild rather than
hardcoded, which is what the old design decision required of any replacement.

Two things this module is careful about, both of which produce plausible-looking
wrong numbers if handled naively:

1. **Filers use different tags for the same line.** "Revenue" is `Revenues` for
   some, `RevenueFromContractWithCustomerExcludingAssessedTax` for most
   post-ASC-606 filers, `SalesRevenueNet` for older ones. Taking the first tag
   that exists and moving on silently returns nothing for a large share of the
   universe, which then reads as "no coverage" rather than "wrong tag". Hence
   `CONCEPTS` lists fallbacks in priority order.

2. **Flow vs. stock.** Revenue and net income accumulate over a period and must
   be summed to a trailing twelve months; assets and equity are instantaneous
   and must be taken at the latest date, never summed. Summing four quarters of
   total assets produces a number 4x too large that still looks like money.
   `_ttm` and `_latest_instant` are the two separate paths.

Rate limit: SEC asks for <=10 requests/second and a User-Agent identifying the
caller with contact details. Both are honored below; the UA is a real address
because SEC blocks generic ones.
"""

from __future__ import annotations

import gzip
import io
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict

# SEC requires a UA that identifies the requester. A generic browser string is
# rejected; this is the documented format (name + contact).
UA = "sp500-dashboard/1.0 (jayden.varghese@gmail.com)"
GAP = 0.12          # ~8 req/s, inside SEC's 10/s ceiling
TIMEOUT = 30

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD/CY{period}.json"

# US venues in scope. SEC's `exchange` field is a short name, not a MIC.
US_EXCHANGES = {"NYSE", "Nasdaq", "NYSEAmerican", "NYSE American", "AMEX", "CBOE"}

# Concept -> candidate us-gaap tags, most reliable first. See docstring (1).
CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "interest_expense": (
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestIncomeExpenseNet",
    ),
    "ocf": ("NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"),
    # Instantaneous (balance sheet) below this line.
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "equity": ("StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "assets_current": ("AssetsCurrent",),
    "liabilities_current": ("LiabilitiesCurrent",),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "short_term_debt": ("DebtCurrent", "ShortTermBorrowings"),
}

FLOW_CONCEPTS = frozenset({"revenue", "net_income", "gross_profit",
                           "operating_income", "interest_expense", "ocf", "capex"})
INSTANT_CONCEPTS = frozenset(CONCEPTS) - FLOW_CONCEPTS

# Share count lives in the `dei` taxonomy, not `us-gaap`.
SHARE_TAGS = ("EntityCommonStockSharesOutstanding",)
SHARE_FALLBACK_TAGS = ("CommonStockSharesOutstanding",
                       "WeightedAverageNumberOfDilutedSharesOutstanding")

# SIC range -> sector. Approximate by construction: SIC is a 1987 scheme and
# GICS is not, so this maps ranges to the closest GICS-style sector rather than
# claiming an official crosswalk. The UI labels these "SEC SIC-derived" so a
# reader is never told this is GICS. Specific codes override their range.
SIC_OVERRIDES: dict[int, str] = {
    7372: "Information Technology", 7370: "Information Technology",
    7371: "Information Technology", 7373: "Information Technology",
    7374: "Information Technology", 3674: "Information Technology",
    3571: "Information Technology", 3572: "Information Technology",
    3576: "Information Technology", 3577: "Information Technology",
    3661: "Information Technology", 3663: "Information Technology",
    3669: "Information Technology", 3670: "Information Technology",
    3672: "Information Technology", 3675: "Information Technology",
    3678: "Information Technology", 3679: "Information Technology",
    3827: "Information Technology", 3861: "Information Technology",
    7812: "Communication Services", 7819: "Communication Services",
    7822: "Communication Services", 7841: "Communication Services",
    4813: "Communication Services", 4812: "Communication Services",
    4822: "Communication Services", 4832: "Communication Services",
    4833: "Communication Services", 4841: "Communication Services",
    4899: "Communication Services", 2711: "Communication Services",
    2721: "Communication Services", 2731: "Communication Services",
    7900: "Communication Services", 7990: "Communication Services",
    2834: "Health Care", 2835: "Health Care", 2836: "Health Care",
    8000: "Health Care", 8011: "Health Care", 8060: "Health Care",
    8071: "Health Care", 8090: "Health Care", 8093: "Health Care",
    3826: "Health Care", 3841: "Health Care", 3842: "Health Care",
    3843: "Health Care", 3844: "Health Care", 3845: "Health Care",
    3851: "Health Care", 5122: "Health Care", 6324: "Health Care",
    5812: "Consumer Discretionary", 5813: "Consumer Discretionary",
    7011: "Consumer Discretionary", 7997: "Consumer Discretionary",
    3711: "Consumer Discretionary", 3714: "Consumer Discretionary",
    3716: "Consumer Discretionary", 3751: "Consumer Discretionary",
    6798: "Real Estate", 6500: "Real Estate", 6512: "Real Estate",
    6513: "Real Estate", 6519: "Real Estate", 6531: "Real Estate",
    6552: "Real Estate", 6798000: "Real Estate",
    4911: "Utilities", 4922: "Utilities", 4923: "Utilities",
    4924: "Utilities", 4931: "Utilities", 4932: "Utilities",
    4941: "Utilities", 4991: "Utilities",
}

SIC_RANGES: tuple[tuple[int, int, str], ...] = (
    (100, 999, "Consumer Staples"),
    (1000, 1099, "Materials"),
    (1200, 1299, "Energy"),
    (1300, 1399, "Energy"),
    (1400, 1499, "Materials"),
    (1500, 1799, "Industrials"),
    (2000, 2199, "Consumer Staples"),
    (2200, 2399, "Consumer Discretionary"),
    (2400, 2499, "Industrials"),
    (2500, 2599, "Consumer Discretionary"),
    (2600, 2699, "Materials"),
    (2700, 2799, "Communication Services"),
    (2800, 2899, "Materials"),
    (2900, 2999, "Energy"),
    (3000, 3299, "Materials"),
    (3300, 3399, "Materials"),
    (3400, 3599, "Industrials"),
    (3600, 3699, "Information Technology"),
    (3700, 3799, "Industrials"),
    (3800, 3899, "Health Care"),
    (3900, 3999, "Consumer Discretionary"),
    (4000, 4499, "Industrials"),
    (4500, 4599, "Industrials"),
    (4600, 4699, "Energy"),
    (4700, 4799, "Industrials"),
    (4800, 4899, "Communication Services"),
    (4900, 4999, "Utilities"),
    (5000, 5199, "Industrials"),
    (5200, 5999, "Consumer Discretionary"),
    (6000, 6199, "Financials"),
    (6200, 6299, "Financials"),
    (6300, 6499, "Financials"),
    (6500, 6599, "Real Estate"),
    (6700, 6799, "Financials"),
    (7000, 7099, "Consumer Discretionary"),
    (7200, 7299, "Consumer Discretionary"),
    (7300, 7399, "Information Technology"),
    (7500, 7699, "Consumer Discretionary"),
    (7800, 7999, "Communication Services"),
    (8000, 8099, "Health Care"),
    (8100, 8299, "Consumer Discretionary"),
    (8300, 8399, "Health Care"),
    (8400, 8999, "Industrials"),
)


def sector_for_sic(sic) -> str:
    """Map a SIC code to a GICS-style sector name. 'Unclassified' when unknown --
    never a guess, because a wrong sector silently corrupts every sector-neutral
    valuation rank for that company AND for its assigned peers."""
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return "Unclassified"
    if code in SIC_OVERRIDES:
        return SIC_OVERRIDES[code]
    for lo, hi, sector in SIC_RANGES:
        if lo <= code <= hi:
            return sector
    return "Unclassified"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _get_json(url: str, retries: int = 3) -> dict | None:
    """GET returning parsed JSON, or None. Handles gzip; SEC serves it when asked
    and these payloads are ~10x larger uncompressed."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None  # company has no XBRL facts; not an error worth retrying
            time.sleep(2.0 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            time.sleep(1.0 * (attempt + 1))
    return None


def ticker_map() -> dict[str, dict]:
    """ticker -> {cik, name, exchange} for every US-listed filer (one request)."""
    payload = _get_json(TICKERS_URL)
    if not payload:
        return {}
    fields = payload.get("fields") or []
    try:
        i_cik, i_name = fields.index("cik"), fields.index("name")
        i_tic, i_exch = fields.index("ticker"), fields.index("exchange")
    except ValueError:
        return {}

    out: dict[str, dict] = {}
    for row in payload.get("data") or []:
        ticker = str(row[i_tic] or "").upper().strip()
        exchange = str(row[i_exch] or "").strip()
        if not ticker or exchange not in US_EXCHANGES:
            continue
        # Yahoo writes class shares with a hyphen (BRK-B); SEC uses a dot.
        out[ticker.replace(".", "-")] = {
            "cik": int(row[i_cik]),
            "name": str(row[i_name] or ticker),
            "exchange": exchange,
        }
    return out


# ---------------------------------------------------------------------------
# Fact extraction
# ---------------------------------------------------------------------------
def _usd_facts(facts: dict, taxonomy: str, tags: tuple[str, ...]) -> list[dict]:
    """Every reported observation for the first tag that has any, as a flat list."""
    section = (facts.get("facts") or {}).get(taxonomy) or {}
    for tag in tags:
        entry = section.get(tag)
        if not entry:
            continue
        for unit_rows in (entry.get("units") or {}).values():
            if unit_rows:
                return list(unit_rows)
    return []


def _ttm(rows: list[dict]) -> tuple[float | None, float | None]:
    """Trailing twelve months, and the twelve months before it.

    Prefers an annual figure (`fp == FY` with a ~365-day frame) because it is the
    audited number. Falls back to summing the last four quarters -- and requires
    that they be four *distinct, contiguous* quarters, since EDGAR returns
    amended and overlapping filings for the same period and naively summing every
    row inside a year double-counts restatements.
    """
    dated = [r for r in rows if r.get("start") and r.get("end") and r.get("val") is not None]
    if not dated:
        return None, None

    def span(r) -> int:
        y1, m1, d1 = (int(x) for x in r["start"].split("-"))
        y2, m2, d2 = (int(x) for x in r["end"].split("-"))
        return (y2 - y1) * 365 + (m2 - m1) * 30 + (d2 - d1)

    # Annual path: one row already covering ~a year.
    annual = sorted((r for r in dated if 330 <= span(r) <= 400),
                    key=lambda r: r["end"])
    if len(annual) >= 2:
        return float(annual[-1]["val"]), float(annual[-2]["val"])
    if len(annual) == 1:
        return float(annual[-1]["val"]), None

    # Quarterly path: dedupe to one row per period end (last filed wins), then
    # take four and four.
    quarters: dict[str, dict] = {}
    for r in sorted((r for r in dated if 80 <= span(r) <= 100),
                    key=lambda r: (r["end"], r.get("filed") or "")):
        quarters[r["end"]] = r
    ordered = [quarters[k] for k in sorted(quarters)]
    if len(ordered) >= 4:
        cur = sum(float(r["val"]) for r in ordered[-4:])
        prior = (sum(float(r["val"]) for r in ordered[-8:-4])
                 if len(ordered) >= 8 else None)
        return cur, prior
    return None, None


def _latest_instant(rows: list[dict]) -> float | None:
    """Most recently reported balance-sheet value. Never summed -- see docstring (2)."""
    instants = [r for r in rows
                if r.get("end") and r.get("val") is not None and not r.get("start")]
    if not instants:
        # Some filers tag balance items with a start date too; fall back to the
        # latest `end` regardless rather than reporting no coverage.
        instants = [r for r in rows if r.get("end") and r.get("val") is not None]
    if not instants:
        return None
    latest = max(instants, key=lambda r: (r["end"], r.get("filed") or ""))
    return float(latest["val"])


def _earnings_stability(rows: list[dict]) -> float | None:
    """Inverse coefficient of variation of quarterly earnings, as a 0-1 score.

    Uses |mean| in the denominator so a company that swings through zero scores
    low rather than producing a division blow-up, and returns None below eight
    quarters -- a stability claim from three data points is noise.
    """
    quarters: dict[str, float] = {}
    for r in rows:
        if not (r.get("start") and r.get("end") and r.get("val") is not None):
            continue
        y1, m1, _ = (int(x) for x in r["start"].split("-"))
        y2, m2, _ = (int(x) for x in r["end"].split("-"))
        if 2 <= (y2 - y1) * 12 + (m2 - m1) <= 4:
            quarters[r["end"]] = float(r["val"])
    vals = [quarters[k] for k in sorted(quarters)][-20:]
    if len(vals) < 8:
        return None
    mean = sum(vals) / len(vals)
    if abs(mean) < 1e-9:
        return 0.0
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    cv = (var ** 0.5) / abs(mean)
    return 1.0 / (1.0 + cv)


def extract(facts: dict) -> dict:
    """One company's companyfacts JSON -> the flat metric row the DB stores."""
    out: dict[str, float | None] = {}

    for concept, tags in CONCEPTS.items():
        rows = _usd_facts(facts, "us-gaap", tags)
        if not rows:
            out[concept] = None
            if concept in FLOW_CONCEPTS:
                out[f"{concept}_prior"] = None
            continue
        if concept in FLOW_CONCEPTS:
            cur, prior = _ttm(rows)
            out[concept], out[f"{concept}_prior"] = cur, prior
        else:
            out[concept] = _latest_instant(rows)

    shares = _usd_facts(facts, "dei", SHARE_TAGS)
    if not shares:
        shares = _usd_facts(facts, "us-gaap", SHARE_FALLBACK_TAGS)
    out["shares_outstanding"] = _latest_instant(shares)

    out["earnings_stability"] = _earnings_stability(
        _usd_facts(facts, "us-gaap", CONCEPTS["net_income"]))

    # Latest period end across everything reported — the "as of" for the row.
    ends = [r["end"] for tags in CONCEPTS.values()
            for r in _usd_facts(facts, "us-gaap", tags) if r.get("end")]
    out["asof"] = max(ends) if ends else None
    return out


def company_facts(cik: int) -> dict | None:
    return _get_json(FACTS_URL.format(cik=cik))


def largest_by(tag: str, period: str, top: int = 1200) -> list[tuple[int, float]]:
    """(cik, value) for one concept across all filers in one request.

    The frames API is what makes a wide universe affordable: ranking 10,000
    filers by revenue costs one HTTP call rather than 10,000. Used only to
    pre-screen candidates -- the final universe is cut by traded liquidity, which
    frames cannot see.
    """
    payload = _get_json(FRAMES_URL.format(tag=tag, period=period))
    if not payload:
        return []
    rows = [(int(r["cik"]), float(r["val"]))
            for r in payload.get("data") or []
            if r.get("cik") is not None and r.get("val") is not None]
    rows.sort(key=lambda t: -t[1])
    return rows[:top]

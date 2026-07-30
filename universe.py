"""The symbol universe: the index plus a curated set of large-cap US equities.

Sector/industry are curated static values (GICS-style). Yahoo's free chart API
returns prices and names but not classification, and the endpoints that do
(quoteSummary, v7/quote) are auth-gated — so these are hand-maintained rather
than fetched. They are stable facts; they do not change with market data.

Market cap is deliberately absent: it needs share-count data none of the
reachable free endpoints provide, and a stale hardcoded figure would be worse
than none. The app shows computed dollar-volume liquidity instead.
"""

from __future__ import annotations

INDEX_SYMBOL = "^GSPC"

# symbol -> (display name, sector, industry)
EQUITIES: dict[str, tuple[str, str, str]] = {
    # Information Technology
    "AAPL": ("Apple Inc.", "Information Technology", "Consumer Electronics"),
    "MSFT": ("Microsoft Corporation", "Information Technology", "Software"),
    "NVDA": ("NVIDIA Corporation", "Information Technology", "Semiconductors"),
    "AVGO": ("Broadcom Inc.", "Information Technology", "Semiconductors"),
    "ORCL": ("Oracle Corporation", "Information Technology", "Software"),
    "CRM": ("Salesforce, Inc.", "Information Technology", "Software"),
    "AMD": ("Advanced Micro Devices, Inc.", "Information Technology", "Semiconductors"),
    "ADBE": ("Adobe Inc.", "Information Technology", "Software"),
    "CSCO": ("Cisco Systems, Inc.", "Information Technology", "Communications Equipment"),
    "INTC": ("Intel Corporation", "Information Technology", "Semiconductors"),
    "QCOM": ("QUALCOMM Incorporated", "Information Technology", "Semiconductors"),
    "TXN": ("Texas Instruments Incorporated", "Information Technology", "Semiconductors"),
    "IBM": ("International Business Machines", "Information Technology", "IT Services"),
    # Communication Services
    "GOOGL": ("Alphabet Inc.", "Communication Services", "Interactive Media"),
    "META": ("Meta Platforms, Inc.", "Communication Services", "Interactive Media"),
    "NFLX": ("Netflix, Inc.", "Communication Services", "Entertainment"),
    "DIS": ("The Walt Disney Company", "Communication Services", "Entertainment"),
    "T": ("AT&T Inc.", "Communication Services", "Telecom"),
    "VZ": ("Verizon Communications Inc.", "Communication Services", "Telecom"),
    # Consumer Discretionary
    "AMZN": ("Amazon.com, Inc.", "Consumer Discretionary", "Broadline Retail"),
    "TSLA": ("Tesla, Inc.", "Consumer Discretionary", "Automobiles"),
    "HD": ("The Home Depot, Inc.", "Consumer Discretionary", "Specialty Retail"),
    "MCD": ("McDonald's Corporation", "Consumer Discretionary", "Restaurants"),
    "NKE": ("NIKE, Inc.", "Consumer Discretionary", "Apparel"),
    "SBUX": ("Starbucks Corporation", "Consumer Discretionary", "Restaurants"),
    # Financials
    "JPM": ("JPMorgan Chase & Co.", "Financials", "Diversified Banks"),
    "V": ("Visa Inc.", "Financials", "Transaction Processing"),
    "MA": ("Mastercard Incorporated", "Financials", "Transaction Processing"),
    "BAC": ("Bank of America Corporation", "Financials", "Diversified Banks"),
    "WFC": ("Wells Fargo & Company", "Financials", "Diversified Banks"),
    "GS": ("The Goldman Sachs Group, Inc.", "Financials", "Investment Banking"),
    "BRK-B": ("Berkshire Hathaway Inc.", "Financials", "Multi-Sector Holdings"),
    # Health Care
    "UNH": ("UnitedHealth Group Incorporated", "Health Care", "Managed Care"),
    "JNJ": ("Johnson & Johnson", "Health Care", "Pharmaceuticals"),
    "LLY": ("Eli Lilly and Company", "Health Care", "Pharmaceuticals"),
    "PFE": ("Pfizer Inc.", "Health Care", "Pharmaceuticals"),
    "ABBV": ("AbbVie Inc.", "Health Care", "Biotechnology"),
    "MRK": ("Merck & Co., Inc.", "Health Care", "Pharmaceuticals"),
    # Consumer Staples
    "WMT": ("Walmart Inc.", "Consumer Staples", "Consumer Staples Retail"),
    "COST": ("Costco Wholesale Corporation", "Consumer Staples", "Consumer Staples Retail"),
    "PG": ("The Procter & Gamble Company", "Consumer Staples", "Household Products"),
    "KO": ("The Coca-Cola Company", "Consumer Staples", "Beverages"),
    "PEP": ("PepsiCo, Inc.", "Consumer Staples", "Beverages"),
    # Energy / Industrials
    "XOM": ("Exxon Mobil Corporation", "Energy", "Integrated Oil & Gas"),
    "CVX": ("Chevron Corporation", "Energy", "Integrated Oil & Gas"),
    "CAT": ("Caterpillar Inc.", "Industrials", "Machinery"),
    "BA": ("The Boeing Company", "Industrials", "Aerospace & Defense"),
    "GE": ("GE Aerospace", "Industrials", "Aerospace & Defense"),
    "UPS": ("United Parcel Service, Inc.", "Industrials", "Air Freight & Logistics"),
}

INDEX_META = (INDEX_SYMBOL, "S&P 500 Index", "Index", "Broad Market Index")


def all_symbols() -> list[str]:
    return [INDEX_SYMBOL] + list(EQUITIES)


def symbol_rows() -> list[tuple[str, str, str, str, int]]:
    """Rows for the `symbols` table: (symbol, name, sector, industry, is_index)."""
    rows = [(INDEX_META[0], INDEX_META[1], INDEX_META[2], INDEX_META[3], 1)]
    rows += [(sym, name, sector, industry, 0) for sym, (name, sector, industry) in EQUITIES.items()]
    return rows

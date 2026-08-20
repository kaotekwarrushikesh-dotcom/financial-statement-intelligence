"""Turn what a user types into a ticker, and pick the best source for it."""

import warnings
from dataclasses import dataclass

# Exchange suffix to a human label. Used to tell the user which listing they are looking at,
# since the same company often trades in several places at different prices and currencies.
EXCHANGE_HINTS = {
    "": "United States", ".L": "London", ".DE": "Frankfurt", ".PA": "Paris",
    ".AS": "Amsterdam", ".SW": "Zurich", ".MI": "Milan", ".MC": "Madrid",
    ".BR": "Brussels", ".VI": "Vienna", ".ST": "Stockholm", ".CO": "Copenhagen",
    ".OL": "Oslo", ".HE": "Helsinki", ".LS": "Lisbon", ".IR": "Dublin",
    ".NS": "India (NSE)", ".BO": "India (BSE)", ".T": "Tokyo", ".HK": "Hong Kong",
    ".AX": "Australia", ".TO": "Toronto", ".SA": "Brazil", ".JO": "Johannesburg",
}


@dataclass(frozen=True)
class Match:
    ticker: str
    name: str
    exchange: str
    region: str


def region_of(ticker: str) -> str:
    for suffix, label in EXCHANGE_HINTS.items():
        if suffix and ticker.upper().endswith(suffix.upper()):
            return label
    return "United States"


def is_us_listing(ticker: str) -> bool:
    """A plain ticker with no exchange suffix is a US listing."""
    return "." not in ticker


def search(query: str, limit: int = 8) -> list[Match]:
    """Find candidate listings for a company name or ticker.

    Deliberately returns several. "Nestle" matches the Swiss primary listing, a US
    over-the-counter line and a Milan cross-listing, and those are different instruments in
    different currencies. Picking one silently would hide that choice from the user.
    """
    import yfinance as yf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            quotes = yf.Search(query, max_results=limit).quotes
        except Exception:  # noqa: BLE001 - search is a best-effort convenience
            return []

    matches: list[Match] = []
    for q in quotes:
        symbol = q.get("symbol")
        if not symbol:
            continue
        # Only equities can be valued from financial statements.
        if q.get("quoteType") not in (None, "EQUITY"):
            continue
        matches.append(Match(
            ticker=symbol,
            name=q.get("longname") or q.get("shortname") or symbol,
            exchange=q.get("exchange", ""),
            region=region_of(symbol),
        ))
    return matches


def resolve(query: str) -> Match | None:
    """Best single match for a query, or None."""
    query = query.strip()
    if not query:
        return None
    matches = search(query, limit=8)
    return matches[0] if matches else None

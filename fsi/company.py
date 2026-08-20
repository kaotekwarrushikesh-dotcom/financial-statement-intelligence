"""One entry point: a company name or ticker in, a full Module 1 analysis out.

Source selection is the only judgement here, and it goes to whichever gives more history:

  US listings   SEC EDGAR, ten years of filed 10-K data, authoritative and free.
  Everywhere    Yahoo Finance, four to five years, which is the only option outside the US.

If EDGAR fails for a US name the loader falls back to Yahoo rather than giving up, and the
result records which source was used so a four-year read is never mistaken for a ten-year one.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from fsi.data_cleaning import clean_financials
from fsi.financial_health import financial_health_score
from fsi.providers import resolve as resolver
from fsi.providers import yahoo
from fsi.ratios import calculate_ratios
from fsi.trends import trend_report

CACHE_DIR = Path(os.environ.get("FSI_CACHE", Path.home() / ".cache" / "fsi"))


@dataclass
class Analysis:
    """Everything Module 1 knows about one company."""

    ticker: str
    name: str
    region: str
    currency: str
    source: str
    years: int
    first_year: int
    last_year: int
    ratios: pd.DataFrame
    health: dict
    trends: list[str]
    share_price: float
    market_cap: float
    notes: list[str]


def _from_edgar(ticker: str) -> tuple[pd.DataFrame, str, list[str]]:
    """Ten years of filed data for a US company."""
    from fsi.edgar_fetch import CIK_OVERRIDES, build_company_frame, load_ticker_map

    cache = CACHE_DIR / "edgar"
    tickers = load_ticker_map(cache)
    cik = CIK_OVERRIDES.get(ticker.upper(), tickers.get(ticker.upper()))
    if cik is None:
        raise ValueError(f"{ticker} is not in EDGAR's ticker map")

    return build_company_frame(ticker, cik, cache), "SEC EDGAR", []


def analyse(query: str, prefer_source: str | None = None) -> Analysis:
    """Resolve a company and run the full Module 1 pipeline on it."""
    match = resolver.resolve(query)
    if match is None:
        raise ValueError(f"No listed company found for '{query}'")

    notes: list[str] = []
    frame = None
    source = ""
    currency = "USD"
    share_price = 0.0
    market_cap = 0.0
    name = match.name

    use_edgar = resolver.is_us_listing(match.ticker) and prefer_source != "yahoo"
    if use_edgar:
        try:
            frame, source, edgar_notes = _from_edgar(match.ticker)
            notes.extend(edgar_notes)
            # EDGAR has the statements but not the market data.
            snapshot = yahoo.fetch(match.ticker)
            currency, share_price = snapshot.currency, snapshot.share_price
            market_cap, name = snapshot.market_cap, snapshot.name
        except Exception as exc:  # noqa: BLE001 - fall back rather than fail
            notes.append(f"EDGAR was unavailable for {match.ticker} ({exc}), so Yahoo was used.")
            frame = None

    if frame is None:
        data = yahoo.fetch(match.ticker)
        frame, source = data.frame, data.source
        currency, share_price = data.currency, data.share_price
        market_cap, name = data.market_cap, data.name
        notes.extend(data.notes)

    ratios = calculate_ratios(clean_financials(frame))
    health = financial_health_score(ratios)

    return Analysis(
        ticker=match.ticker,
        name=name,
        region=match.region,
        currency=currency,
        source=source,
        years=len(ratios),
        first_year=int(ratios["fiscal_year"].iloc[0]),
        last_year=int(ratios["fiscal_year"].iloc[-1]),
        ratios=ratios,
        health=health,
        trends=trend_report(ratios),
        share_price=share_price,
        market_cap=market_cap,
        notes=notes,
    )

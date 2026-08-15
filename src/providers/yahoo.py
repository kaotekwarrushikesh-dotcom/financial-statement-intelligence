"""Global financial statement loader, into Module 1's cleaned schema.

EDGAR reaches US filers only. Yahoo Finance reaches everywhere, so a company listed in
Frankfurt, Amsterdam, Zurich, Paris, London, Mumbai or Tokyo comes through this provider
into exactly the same table shape EDGAR produces, and every stage downstream is unchanged.

The trade is history for reach: EDGAR gives ten years, Yahoo gives four or five. Where both
can serve a company, EDGAR wins.

Currency is where a global loader actually gets dangerous, and three separate traps live
here. All of them silently produce a plausible-looking wrong answer rather than an error:

  **London quotes in pence.** Shell trades at "3320.0", which is GBp, not GBP. Reading that
  as pounds overstates the share price a hundredfold, and market capitalisation carries the
  same unit.

  **The reporting currency need not be the quote currency.** Shell reports in USD while
  trading in pence. Infosys reports in USD while trading in rupees.

  **The reporting-currency flag itself is unreliable.** HCL Technologies is tagged USD while
  its statements are already in rupees, so believing the tag overstates it a hundredfold.

The defence is a fact that does not depend on any tag: market capitalisation is
unambiguously in the quote currency, so whichever reading of the statements implies a
believable price-to-sales ratio is the right one.
"""

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.data_loader import REQUIRED_COLUMNS

INCOME_MAP = {
    "revenue": ["Total Revenue", "Operating Revenue"],
    "cogs": ["Cost Of Revenue"],
    "gross_profit": ["Gross Profit"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "ebit": ["EBIT", "Operating Income", "Total Operating Income As Reported"],
    "interest_expense": ["Interest Expense", "Interest Expense Non Operating"],
    "tax_expense": ["Tax Provision"],
    "net_income": ["Net Income", "Net Income Common Stockholders"],
    "eps": ["Diluted EPS", "Basic EPS"],
}

BALANCE_MAP = {
    # Cash including short-term investments: companies in many markets hold surplus in
    # liquid funds rather than bank balances, and the narrow line understates liquidity
    # badly enough to flip the sign of net debt.
    "cash": ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"],
    "current_assets": ["Current Assets"],
    "total_assets": ["Total Assets"],
    "current_liabilities": ["Current Liabilities"],
    "total_debt": ["Total Debt"],
    "short_term_debt": ["Current Debt", "Current Debt And Capital Lease Obligation"],
    "long_term_debt": ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"],
    "equity": ["Stockholders Equity", "Common Stock Equity"],
}

CASHFLOW_MAP = {
    "cfo": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure", "Purchase Of PPE"],
    "financing_cash_flow": ["Financing Cash Flow"],
    "investing_cash_flow": ["Investing Cash Flow"],
    "dep_amort": ["Depreciation And Amortization", "Depreciation Amortization Depletion"],
}

SCHEMA = REQUIRED_COLUMNS + ["dep_amort", "short_term_debt", "long_term_debt"]

MILLION = 1e6
PLAUSIBLE_PRICE_TO_SALES = (0.05, 50.0)

# Exchanges that quote in a minor unit. Yahoo reports the price and market cap in that unit
# while the statements are in the major one.
MINOR_UNIT_CURRENCIES = {"GBP": 100.0, "ZAC": 100.0, "ILA": 100.0}
MINOR_UNIT_CODES = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}


@dataclass(frozen=True)
class CompanyData:
    """Statements in Module 1's schema, plus what is needed to read them correctly."""

    ticker: str
    name: str
    frame: pd.DataFrame
    currency: str
    quote_currency: str
    fx_applied: float
    share_price: float
    shares_outstanding: float
    market_cap: float
    exchange: str
    source: str
    notes: list[str]


def _pick(frame: pd.DataFrame, candidates: list[str], column) -> float:
    for name in candidates:
        if name in frame.index:
            value = frame.loc[name, column]
            if pd.notna(value):
                return float(value)
    return float("nan")


def fiscal_year_of(period) -> int:
    """Label a fiscal year by the calendar year holding most of it.

    A period ending before June belongs to the prior calendar year, which is EDGAR's
    convention in Module 1. It keeps March-year-end Indian companies, June-year-end
    Australians and December-year-end Europeans on one comparable axis.
    """
    ts = pd.Timestamp(period)
    return ts.year if ts.month >= 6 else ts.year - 1


def normalise_quote(price: float, market_cap: float, currency: str) -> tuple[float, float, str, list[str]]:
    """Convert a minor-unit quote (pence and similar) into the major unit."""
    notes: list[str] = []
    code = MINOR_UNIT_CODES.get(currency)
    if code is None:
        return price, market_cap, (currency or "").upper(), notes

    divisor = MINOR_UNIT_CURRENCIES.get(code, 100.0)
    notes.append(
        f"Quoted in {currency} (minor unit), converted to {code} by dividing by {divisor:.0f}. "
        "Read as the major unit the share price would be overstated a hundredfold."
    )
    return price / divisor, market_cap / divisor, code, notes


def fx_rate(from_currency: str, to_currency: str) -> float:
    if from_currency == to_currency:
        return 1.0

    import yfinance as yf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        hist = yf.Ticker(f"{from_currency}{to_currency}=X").history(period="5d")
    if hist.empty:
        raise ValueError(f"no {from_currency}{to_currency} exchange rate available")
    return float(hist["Close"].iloc[-1])


def resolve_statement_currency(
    declared: str, quote_currency: str, latest_revenue: float, market_cap: float
) -> tuple[str, float, list[str]]:
    """Decide what currency the statements are actually in, and the rate to the quote currency.

    The declared flag is treated as a hint and checked against market capitalisation, which
    is unambiguously in the quote currency. Whichever reading implies a believable
    price-to-sales ratio wins. Where the evidence is ambiguous the statements are left
    alone, because converting on a guess is a silent hundredfold error while not converting
    is at worst the raw filing.
    """
    notes: list[str] = []
    declared = (declared or quote_currency or "").upper()

    if not declared or declared == quote_currency or latest_revenue <= 0 or market_cap <= 0:
        return quote_currency, 1.0, notes

    try:
        rate = fx_rate(declared, quote_currency)
    except Exception:  # noqa: BLE001
        notes.append(f"statements appear to be in {declared} but no exchange rate was available")
        return quote_currency, 1.0, notes

    # The price-to-sales test only discriminates when the two currencies differ by an order
    # of magnitude, as rupees and dollars do at roughly 95 to one. Between currencies of
    # similar size, such as Shell reporting in USD while quoting in GBP at 0.79, both
    # readings look equally plausible and the test has nothing to say. There the source's
    # own tag is the better evidence, so it is believed rather than second-guessed.
    if 0.2 <= rate <= 5.0:
        notes.append(
            f"Statements are reported in {declared} while the share quotes in {quote_currency}, "
            f"converted at {rate:.4g}. The two currencies are close enough in size that the "
            "market-capitalisation cross-check cannot distinguish them, so the source's own "
            "currency tag is relied on here."
        )
        return declared, rate, notes

    low, high = PLAUSIBLE_PRICE_TO_SALES
    as_quote = market_cap / latest_revenue
    as_declared = market_cap / (latest_revenue * rate)

    quote_ok = low <= as_quote <= high
    declared_ok = low <= as_declared <= high

    if declared_ok and not quote_ok:
        notes.append(
            f"Statements are reported in {declared} while the share quotes in {quote_currency}, "
            f"so they are converted at {rate:.4g}. Left unconverted, the cash flows and the "
            "share price would be in different currencies."
        )
        return declared, rate, notes

    if quote_ok and not declared_ok:
        notes.append(
            f"The data source tags these statements as {declared}, but the implied valuation "
            f"only makes sense if they are already in {quote_currency}, so the tag is ignored."
        )
        return quote_currency, 1.0, notes

    return quote_currency, 1.0, notes


def fetch(ticker: str) -> CompanyData:
    """Fetch one company from Yahoo Finance into Module 1's schema."""
    import yfinance as yf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = yf.Ticker(ticker)
        income, balance, cash = t.financials, t.balance_sheet, t.cashflow
        fast = t.fast_info
        try:
            info = t.info
        except Exception:  # noqa: BLE001
            info = {}

    if income.empty or balance.empty or cash.empty:
        raise ValueError(f"{ticker}: no financial statements available")

    periods = sorted(set(income.columns) & set(balance.columns) & set(cash.columns))
    if not periods:
        raise ValueError(f"{ticker}: no periods common to all three statements")

    rows = []
    for period in periods:
        row: dict[str, float] = {"fiscal_year": fiscal_year_of(period)}
        for field, names in INCOME_MAP.items():
            row[field] = _pick(income, names, period)
        for field, names in BALANCE_MAP.items():
            row[field] = _pick(balance, names, period)
        for field, names in CASHFLOW_MAP.items():
            row[field] = _pick(cash, names, period)
        rows.append(row)

    df = pd.DataFrame(rows)
    df["capex"] = df["capex"].abs()

    df["dep_amort"] = df["dep_amort"].fillna(df["ebitda"] - df["ebit"])
    df["ebitda"] = df["ebitda"].fillna(df["ebit"] + df["dep_amort"])
    df["gross_profit"] = df["gross_profit"].fillna(df["revenue"] - df["cogs"])
    df["cogs"] = df["cogs"].fillna(df["revenue"] - df["gross_profit"])

    both_missing = df["long_term_debt"].isna() & df["short_term_debt"].isna()
    summed = df["long_term_debt"].fillna(0.0) + df["short_term_debt"].fillna(0.0)
    df["total_debt"] = df["total_debt"].fillna(summed.where(~both_missing))

    for col in SCHEMA:
        if col not in df.columns:
            df[col] = np.nan
    df = df[SCHEMA].copy()

    df["fiscal_year"] = df["fiscal_year"].astype(int)
    df = df.sort_values("fiscal_year").reset_index(drop=True)

    # Keep only the unbroken run ending at the most recent year: a gap makes every
    # year-over-year change and CAGR silently wrong.
    years = df["fiscal_year"].tolist()
    start = years[-1]
    for earlier, later in zip(reversed(years[:-1]), reversed(years[1:])):
        if later - earlier != 1:
            break
        start = earlier
    df = df[df["fiscal_year"] >= start].reset_index(drop=True)

    core = ["revenue", "ebit", "net_income", "total_assets", "equity", "cfo"]
    df = df.dropna(subset=core).reset_index(drop=True)
    if df.empty:
        raise ValueError(f"{ticker}: no fiscal years with complete core data")

    raw_price = float(fast.get("lastPrice") or 0.0)
    raw_cap = float(fast.get("marketCap") or 0.0)
    raw_currency = fast.get("currency") or info.get("currency") or ""

    price, market_cap, quote_currency, notes = normalise_quote(raw_price, raw_cap, raw_currency)

    latest_revenue = float(df["revenue"].iloc[-1])
    currency, rate, currency_notes = resolve_statement_currency(
        info.get("financialCurrency", ""), quote_currency, latest_revenue, market_cap)
    notes.extend(currency_notes)

    money = [c for c in SCHEMA if c not in ("fiscal_year", "eps")]
    df[money] = df[money] * rate / MILLION
    df["eps"] = df["eps"] * rate

    if len(df) < 5:
        notes.append(
            f"Only {len(df)} years of history. Yahoo carries four to five years against "
            "EDGAR's ten, so trends rest on fewer observations."
        )

    return CompanyData(
        ticker=ticker.upper(),
        name=info.get("longName") or info.get("shortName") or ticker.upper(),
        frame=df,
        currency=quote_currency or "USD",
        quote_currency=quote_currency or "USD",
        fx_applied=rate,
        share_price=price,
        shares_outstanding=float(fast.get("shares") or 0.0),
        market_cap=market_cap,
        exchange=info.get("exchange", ""),
        source="Yahoo Finance",
        notes=notes,
    )

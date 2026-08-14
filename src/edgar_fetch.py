"""Fetch real financial statement data from the SEC EDGAR XBRL companyfacts API.

EDGAR is the authoritative source for US filers and reaches back 10+ years, so the
pipeline runs on filed numbers rather than hand-typed ones. Companies tag the same
economic concept with different GAAP tags, so each canonical field maps to an ordered
list of candidate tags and the first one that yields data wins.

Fiscal year convention: a period ending in month >= 6 is labelled with that calendar
year, otherwise the prior year. This puts the bulk of each fiscal year in the calendar
year it is labelled with, which keeps January-year-end retailers comparable with
December-year-end peers.
"""

import json
import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

# SEC asks every API caller to identify itself with a contact address and throttles or
# blocks requests that do not. Read it from the environment rather than committing a real
# address into a public repository, where it would simply be harvested.
USER_AGENT = os.environ.get("SEC_USER_AGENT", "financial-statement-intelligence (set SEC_USER_AGENT)")
COMPANY_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"

# EDGAR's ticker map points XOM at a newer registrant entity that carries only a handful
# of tags. The operating company with the full filing history is CIK 34088.
CIK_OVERRIDES = {"XOM": 34088}

# Ordered fallbacks: first tag that produces a value for a given year is used.
DURATION_TAGS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "cogs": [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
        "CostOfServices",
    ],
    "gross_profit": ["GrossProfit"],
    "ebit": ["OperatingIncomeLoss"],
    # Oil majors and several large pharma companies do not present an operating income
    # line at all, so EBIT is rebuilt as pretax income plus interest expense.
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
    ],
    "dep_amort": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
        "Depreciation",
        "DepreciationNonproduction",
    ],
    # Alphabet and others report depreciation and intangible amortization on separate
    # lines and never tag a combined figure, so the two halves are summed as a fallback.
    "_depreciation_only": ["DepreciationNonproduction", "Depreciation"],
    "_amortization_only": ["AmortizationOfIntangibleAssets"],
    "interest_expense": [
        "InterestExpense",
        "InterestExpenseNonoperating",
        "InterestAndDebtExpense",
        "InterestExpenseDebt",
    ],
    "tax_expense": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "cfo": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquireOtherPropertyPlantAndEquipment",
        "PaymentsForCapitalImprovements",
        "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
    ],
    "financing_cash_flow": ["NetCashProvidedByUsedInFinancingActivities"],
    "investing_cash_flow": ["NetCashProvidedByUsedInInvestingActivities"],
}

EPS_TAGS = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"]

INSTANT_TAGS = {
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "current_assets": ["AssetsCurrent"],
    "total_assets": ["Assets"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermNotesPayable",
    ],
    "short_term_debt": [
        "LongTermDebtCurrent",
        "DebtCurrent",
        "ShortTermBorrowings",
        "OtherShortTermBorrowings",
    ],
}


def fiscal_year_of(end: str) -> int:
    """Label a period by the calendar year containing most of it."""
    d = date.fromisoformat(end)
    return d.year if d.month >= 6 else d.year - 1


def _get(url: str, cache_dir: Path) -> dict:
    """Fetch JSON with an on-disk cache, so re-runs do not re-hit EDGAR."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / (url.rstrip("/").split("/")[-1] or "index.json")

    if cache_file.exists():
        return json.loads(cache_file.read_text())

    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    cache_file.write_text(resp.text)
    time.sleep(0.2)  # stay well inside EDGAR's 10 requests/second limit
    return resp.json()


def load_ticker_map(cache_dir: Path) -> dict[str, int]:
    raw = _get(TICKER_MAP, cache_dir)
    return {row["ticker"]: int(row["cik_str"]) for row in raw.values()}


def _annual_series(facts: dict, tags: list[str], unit: str, instant: bool) -> dict[int, float]:
    """Pull one annual value per fiscal year from the first tag that has data.

    Only 10-K facts are used. For duration facts the period must be a full year
    (340-400 days) to exclude quarters. Later filings overwrite earlier ones for the
    same period, so restated figures win.
    """
    merged: dict[int, float] = {}

    for tag in tags:
        node = facts.get(tag)
        if not node or unit not in node.get("units", {}):
            continue

        best: dict[int, tuple[str, float]] = {}
        for entry in node["units"][unit]:
            if not entry.get("form", "").startswith("10-K"):
                continue

            end = entry.get("end")
            if not instant:
                start = entry.get("start")
                if not start:
                    continue
                days = (date.fromisoformat(end) - date.fromisoformat(start)).days
                if not 340 <= days <= 400:
                    continue

            fy = fiscal_year_of(end)
            filed = entry.get("filed", "")
            if fy not in best or filed >= best[fy][0]:
                best[fy] = (filed, float(entry["val"]))

        # Merge rather than stop at the first tag with data: companies migrate between
        # tags mid-history (for example Revenues to RevenueFromContractWithCustomer after
        # ASC 606), so a single tag covers only part of the period. Earlier tags in the
        # list win where both are present.
        for fy, (_, val) in best.items():
            merged.setdefault(fy, val)

    return merged


def build_company_frame(ticker: str, cik: int, cache_dir: Path, years: int = 10) -> pd.DataFrame:
    """Assemble one company's multi-year statement table in the pipeline's schema."""
    raw = _get(COMPANY_FACTS.format(cik=cik), cache_dir)
    facts = raw["facts"].get("us-gaap", {})

    series: dict[str, dict[int, float]] = {}
    for field, tags in DURATION_TAGS.items():
        series[field] = _annual_series(facts, tags, "USD", instant=False)
    for field, tags in INSTANT_TAGS.items():
        series[field] = _annual_series(facts, tags, "USD", instant=True)
    series["eps"] = _annual_series(facts, EPS_TAGS, "USD/shares", instant=False)

    # Sum split depreciation and amortization lines where no combined figure was tagged.
    for fy, dep in series["_depreciation_only"].items():
        if fy not in series["dep_amort"]:
            amort = series["_amortization_only"].get(fy)
            if amort is not None:
                series["dep_amort"][fy] = dep + amort

    # Rebuild EBIT where no operating income line was tagged.
    for fy, pretax in series["pretax_income"].items():
        if fy not in series["ebit"]:
            series["ebit"][fy] = pretax + series["interest_expense"].get(fy, 0.0)

    # Core fields must be present for a year to be usable.
    core = ["revenue", "ebit", "net_income", "total_assets", "equity", "cfo"]
    usable = set.intersection(*(set(series[f]) for f in core)) if all(series[f] for f in core) else set()
    fiscal_years = sorted(usable)[-years:]

    if not fiscal_years:
        missing = [f for f in core if not series[f]]
        raise ValueError(f"{ticker}: no usable years (empty core fields: {missing or 'none'})")

    # Trim to the longest unbroken run ending at the most recent year: a gap would make
    # year-over-year deltas and CAGR silently wrong.
    run_start = fiscal_years[-1]
    for earlier, later in zip(reversed(fiscal_years[:-1]), reversed(fiscal_years[1:])):
        if later - earlier != 1:
            break
        run_start = earlier
    fiscal_years = [fy for fy in fiscal_years if fy >= run_start]

    rows = []
    for fy in fiscal_years:
        g = lambda f, default=float("nan"): series[f].get(fy, default)  # noqa: E731

        revenue, cogs = g("revenue"), g("cogs")
        gross_profit = g("gross_profit")
        if pd.isna(gross_profit) and not pd.isna(cogs):
            gross_profit = revenue - cogs
        if pd.isna(cogs) and not pd.isna(gross_profit):
            cogs = revenue - gross_profit

        # Missing D&A, capex and interest are left as NaN rather than defaulted to zero.
        # A zero here is not "the company spent nothing", it is "the company did not tag
        # this line", and treating the two as the same silently overstates EBITDA margin,
        # free cash flow and interest coverage.
        ebit = g("ebit")
        dep = g("dep_amort")
        ebitda = ebit + dep

        # Debt is the exception: a company reporting only long-term debt genuinely has no
        # short-term borrowings to add, so a missing side is treated as zero, but a company
        # with neither tagged has unknown, not zero, total debt.
        ltd, std = g("long_term_debt"), g("short_term_debt")
        total_debt = float("nan") if pd.isna(ltd) and pd.isna(std) else (
            (0.0 if pd.isna(ltd) else ltd) + (0.0 if pd.isna(std) else std)
        )

        rows.append(
            {
                "fiscal_year": fy,
                "revenue": revenue,
                "cogs": cogs,
                "gross_profit": gross_profit,
                "ebitda": ebitda,
                "ebit": ebit,
                "dep_amort": dep,
                "interest_expense": g("interest_expense"),
                "tax_expense": g("tax_expense"),
                "net_income": g("net_income"),
                "eps": g("eps"),
                "cash": g("cash"),
                "current_assets": g("current_assets"),
                "total_assets": g("total_assets"),
                "current_liabilities": g("current_liabilities"),
                "total_debt": total_debt,
                "short_term_debt": std,
                "long_term_debt": ltd,
                "equity": g("equity"),
                "cfo": g("cfo"),
                "capex": g("capex"),
                "financing_cash_flow": g("financing_cash_flow"),
                "investing_cash_flow": g("investing_cash_flow"),
            }
        )

    df = pd.DataFrame(rows)

    # EDGAR reports in units; the pipeline and its thresholds work in millions.
    money_cols = [c for c in df.columns if c not in ("fiscal_year", "eps")]
    df[money_cols] = df[money_cols] / 1e6

    return df

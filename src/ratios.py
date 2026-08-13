"""Calculate profitability, efficiency, leverage, and cash-flow ratios."""

import pandas as pd


def calculate_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """Add one column per financial ratio, computed year by year."""
    r = df.copy()

    # Profitability
    r["gross_margin"] = r["gross_profit"] / r["revenue"]
    r["ebitda_margin"] = r["ebitda"] / r["revenue"]
    r["ebit_margin"] = r["ebit"] / r["revenue"]
    r["net_margin"] = r["net_income"] / r["revenue"]
    r["roe"] = r["net_income"] / r["equity"]
    r["roa"] = r["net_income"] / r["total_assets"]

    # Efficiency
    r["asset_turnover"] = r["revenue"] / r["total_assets"]
    r["working_capital"] = r["current_assets"] - r["current_liabilities"]
    r["current_ratio"] = r["current_assets"] / r["current_liabilities"]

    # Leverage
    r["debt_to_equity"] = r["total_debt"] / r["equity"]
    r["interest_coverage"] = r["ebit"] / r["interest_expense"]

    # Cash flow
    r["fcf"] = r["cfo"] - r["capex"]
    r["fcf_margin"] = r["fcf"] / r["revenue"]
    r["cfo_to_net_income"] = r["cfo"] / r["net_income"]

    return r

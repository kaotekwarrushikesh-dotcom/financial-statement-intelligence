"""Load raw financial statement data from CSV into a pandas DataFrame."""

from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = [
    "fiscal_year",
    "revenue",
    "cogs",
    "gross_profit",
    "ebitda",
    "ebit",
    "interest_expense",
    "tax_expense",
    "net_income",
    "eps",
    "cash",
    "current_assets",
    "total_assets",
    "current_liabilities",
    "total_debt",
    "equity",
    "cfo",
    "capex",
    "financing_cash_flow",
    "investing_cash_flow",
]


def load_financials(csv_path: str | Path) -> pd.DataFrame:
    """Load a company's multi-year financial statement CSV.

    Expects one row per fiscal year with the columns in REQUIRED_COLUMNS.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"No financial data file at {csv_path}")

    df = pd.read_csv(csv_path)

    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns in {csv_path.name}: {sorted(missing)}")

    return df

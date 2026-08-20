"""Clean and structure raw financial statement data."""

import pandas as pd

from fsi.data_loader import REQUIRED_COLUMNS

NUMERIC_COLUMNS = [c for c in REQUIRED_COLUMNS if c != "fiscal_year"]

# Fields without which the analysis is not meaningful. Everything else may be missing for
# legitimate reasons: oil majors and some software firms never report a gross profit line,
# and several companies stopped separately tagging interest expense.
CORE_COLUMNS = [
    "revenue",
    "ebit",
    "net_income",
    "total_assets",
    "equity",
    "cfo",
]


def clean_financials(df: pd.DataFrame) -> pd.DataFrame:
    """Sort by fiscal year, enforce numeric types, and check for gaps.

    Raises if fiscal years are non-consecutive, since ratio and trend
    calculations assume an unbroken year-over-year series.
    """
    df = df.copy()

    df["fiscal_year"] = df["fiscal_year"].astype(int)
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if df[CORE_COLUMNS].isnull().any().any():
        bad_cols = df[CORE_COLUMNS].columns[df[CORE_COLUMNS].isnull().any()].tolist()
        raise ValueError(f"Missing values in core columns: {bad_cols}")

    df = df.sort_values("fiscal_year").reset_index(drop=True)

    year_gaps = df["fiscal_year"].diff().dropna()
    if not (year_gaps == 1).all():
        raise ValueError("Fiscal years are not consecutive; fill gaps before analysis")

    return df

"""Trend analysis: year-over-year change, multi-year CAGR, and plain-language interpretation."""

import pandas as pd


def cagr(first: float, last: float, years: int) -> float:
    """Compound annual growth rate between two values over `years` periods."""
    if first <= 0 or years <= 0:
        return float("nan")
    return (last / first) ** (1 / years) - 1


def add_yoy_changes(ratios_df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Add a `<column>_yoy` column of period-over-period change for each given column."""
    out = ratios_df.copy()
    for col in columns:
        out[f"{col}_yoy"] = out[col].diff()
    return out


def summarize_trend(ratios_df: pd.DataFrame, column: str, label: str, as_percent: bool = True) -> str:
    """Produce an interviewer-style sentence describing how a metric moved across the period."""
    first_year, last_year = int(ratios_df["fiscal_year"].iloc[0]), int(ratios_df["fiscal_year"].iloc[-1])
    first_val, last_val = ratios_df[column].iloc[0], ratios_df[column].iloc[-1]

    def fmt(v: float) -> str:
        if as_percent:
            return f"{v:.1%}"
        return f"{v:,.2f}" if abs(v) < 100 else f"{v:,.0f}"

    direction = "increased" if last_val > first_val else "declined" if last_val < first_val else "held steady"
    n_years = last_year - first_year

    growth_note = ""
    if first_val > 0:
        growth_note = f" ({cagr(first_val, last_val, n_years):+.1%} CAGR)"

    return (
        f"{label} {direction} from {fmt(first_val)} in FY{first_year} to {fmt(last_val)} "
        f"in FY{last_year}{growth_note}."
    )


def trend_report(ratios_df: pd.DataFrame) -> list[str]:
    """Generate the standard set of trend narratives used in the financial health summary."""
    return [
        summarize_trend(ratios_df, "revenue", "Revenue", as_percent=False),
        summarize_trend(ratios_df, "gross_margin", "Gross margin"),
        summarize_trend(ratios_df, "ebitda_margin", "EBITDA margin"),
        summarize_trend(ratios_df, "net_margin", "Net margin"),
        summarize_trend(ratios_df, "roe", "Return on equity"),
        summarize_trend(ratios_df, "debt_to_equity", "Debt-to-equity", as_percent=False),
        summarize_trend(ratios_df, "fcf_margin", "Free cash flow margin"),
    ]

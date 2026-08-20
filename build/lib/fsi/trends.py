"""Trend analysis: year-over-year change, multi-year CAGR, and plain-language interpretation."""

import pandas as pd


def cagr(first: float, last: float, years: int) -> float:
    """Compound annual growth rate between two values over `years` periods.

    Undefined when either endpoint is non-positive: a loss-making start year or a metric
    that crosses zero has no meaningful compound rate, and the fractional power of a
    negative ratio is not a real number.
    """
    if first <= 0 or last <= 0 or years <= 0:
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

    series = ratios_df[column].dropna()
    if series.empty:
        return f"{label} is not reported in these filings."

    # Fall back to the first and last years that actually carry a value, so a metric that
    # only exists for part of the period still gets described over its real span.
    first_val, last_val = series.iloc[0], series.iloc[-1]
    first_year = int(ratios_df.loc[series.index[0], "fiscal_year"])
    last_year = int(ratios_df.loc[series.index[-1], "fiscal_year"])

    def fmt(v: float) -> str:
        if as_percent:
            return f"{v:.1%}"
        return f"{v:,.2f}" if abs(v) < 100 else f"{v:,.0f}"

    direction = "increased" if last_val > first_val else "declined" if last_val < first_val else "held steady"
    n_years = last_year - first_year

    rate = cagr(first_val, last_val, n_years)
    growth_note = "" if pd.isna(rate) else f" ({rate:+.1%} CAGR)"

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

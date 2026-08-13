"""Run the full Module 1 pipeline: load -> clean -> ratios -> trends -> health score."""

import argparse
from pathlib import Path

import pandas as pd

from src.data_cleaning import clean_financials
from src.data_loader import load_financials
from src.financial_health import PILLARS, financial_health_score
from src.ratios import calculate_ratios
from src.trends import trend_report

DEFAULT_DATA = Path(__file__).parent / "data" / "aapl_financials.csv"


def run(csv_path: Path, company: str) -> pd.DataFrame:
    df = clean_financials(load_financials(csv_path))
    ratios = calculate_ratios(df)

    first, last = int(ratios["fiscal_year"].iloc[0]), int(ratios["fiscal_year"].iloc[-1])
    print(f"\n{company}: Financial Statement Intelligence  (FY{first}-FY{last})")
    print("=" * 70)

    print("\nKEY RATIOS BY YEAR")
    display_cols = [
        "fiscal_year",
        "gross_margin",
        "ebitda_margin",
        "net_margin",
        "roe",
        "debt_to_equity",
        "fcf_margin",
    ]
    table = ratios[display_cols].copy()
    for col in display_cols[1:]:
        table[col] = table[col].map(lambda v: f"{v:.1%}" if col != "debt_to_equity" else f"{v:.2f}x")
    print(table.to_string(index=False))

    print("\nTREND ANALYSIS")
    for line in trend_report(ratios):
        print(f"  - {line}")

    health = financial_health_score(ratios)
    print(f"\nFINANCIAL HEALTH: {health['overall']}/100  ({health['rating']})")
    for pillar in PILLARS:
        score = health["pillars"][pillar.name]
        bar = "#" * int(score / 5)
        print(f"  {pillar.name:<16} {score:>5.1f}  (weight {pillar.weight:.0%})  {bar}")

    return ratios


def main() -> None:
    parser = argparse.ArgumentParser(description="Financial Statement Intelligence pipeline")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="Path to financials CSV")
    parser.add_argument("--company", default="Apple Inc. (AAPL)", help="Company display name")
    args = parser.parse_args()

    run(args.data, args.company)


if __name__ == "__main__":
    main()

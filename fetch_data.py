"""Download 10 years of statements for the whole universe from SEC EDGAR into data/."""

from pathlib import Path

from src.edgar_fetch import CIK_OVERRIDES, build_company_frame, load_ticker_map
from src.universe import UNIVERSE

DATA_DIR = Path(__file__).parent / "data"
CACHE_DIR = DATA_DIR / ".edgar_cache"


def main() -> None:
    tickers = load_ticker_map(CACHE_DIR)
    failures = []

    for ticker, (name, _sector) in UNIVERSE.items():
        cik = CIK_OVERRIDES.get(ticker, tickers.get(ticker))
        if cik is None:
            failures.append((ticker, "ticker not found in EDGAR map"))
            continue

        try:
            df = build_company_frame(ticker, cik, CACHE_DIR)
            out = DATA_DIR / f"{ticker}_financials.csv"
            df.to_csv(out, index=False)
            years = f"FY{int(df.fiscal_year.min())}-FY{int(df.fiscal_year.max())}"
            print(f"  {ticker:<6} {name:<26} {len(df):>2} yrs  {years}")
        except Exception as exc:  # noqa: BLE001 - report and continue the batch
            failures.append((ticker, str(exc)))
            print(f"  {ticker:<6} FAILED: {exc}")

    print(f"\n{len(UNIVERSE) - len(failures)}/{len(UNIVERSE)} companies fetched")
    for ticker, reason in failures:
        print(f"  failed: {ticker}: {reason}")


if __name__ == "__main__":
    main()

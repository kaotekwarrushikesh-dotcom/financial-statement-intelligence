# Module 1: Financial Statement Intelligence

Takes raw financial statements, cleans and structures them, calculates financial
metrics, identifies trends, and produces a transparent Financial Health Score.

Covers **20 large US companies, 10 fiscal years each**, with every figure pulled from
filed 10-K data via the SEC EDGAR XBRL API. Nothing is hand-entered.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Download the data (writes `data/<TICKER>_financials.csv` for the whole universe, cached
locally so re-runs do not re-hit EDGAR). SEC asks callers to identify themselves with a
contact address and throttles requests that do not, so set one first:

```bash
export SEC_USER_AGENT="your name your@email.com"
.venv/bin/python fetch_data.py
```

The committed CSVs mean the report can be rebuilt without re-downloading anything.

Build the report (writes the PDF and CSVs into `reports/`):

```bash
.venv/bin/python build_report.py
```

Analyse a single company in the terminal:

```bash
.venv/bin/python main.py --data data/NVDA_financials.csv --company "NVIDIA (NVDA)"
```

Tests:

```bash
.venv/bin/python -m pytest tests/ -q
```

## Outputs

| File | What it is |
|---|---|
| `reports/financial_health_report.pdf` | 23-page report: league table, sector averages, one page per company, methodology |
| `reports/company_scores.csv` | One row per company: score, rating, pillar breakdown, headline ratios |
| `reports/all_ratios_by_year.csv` | Every ratio for every company for every year |

## Data source and known data traps

Data comes from `https://data.sec.gov/api/xbrl/companyfacts/`. Getting a clean 10-year
series out of EDGAR is most of the work in this module, and four traps are handled
explicitly:

1. **Companies migrate between GAAP tags mid-history.** Revenue moves from `Revenues` to
   `RevenueFromContractWithCustomerExcludingAssessedTax` after ASC 606, so each field maps
   to an ordered list of candidate tags and values are merged across them rather than taken
   from the first tag that returns anything.
2. **Not every company reports operating income.** Oil majors and several large pharma
   companies never tag `OperatingIncomeLoss`, so EBIT is rebuilt as pretax income plus
   interest expense.
3. **EPS is not comparable across a stock split.** A 10-K restates only the roughly three
   years it covers, so Apple's FY2018 EPS appears as 11.91 in the FY2018 filing and 2.98 in
   the FY2020 filing after the 4-for-1 split, while FY2017 is never restated at all. EPS
   growth is therefore excluded from scoring.
4. **EDGAR's ticker map can point at the wrong entity.** `XOM` resolves to a newer
   registrant carrying only a handful of tags; the operating company with the full history
   is CIK 34088, so a small override map exists.

Fiscal years are labelled by the calendar year containing most of the period (a period
ending in month 6 or later takes that year, otherwise the prior year). This keeps
January-year-end retailers comparable with December-year-end peers, but it means the label
can differ by one from the company's own naming: NVIDIA's own FY2025 ends January 2025 and
appears here as FY2024.

## Structure

```
financial_statement_intelligence/
├── data/                       one CSV per company, plus a cached EDGAR download
├── reports/                    generated PDF and CSV outputs
├── notebooks/
├── src/
│   ├── edgar_fetch.py          SEC EDGAR download, tag mapping, normalization
│   ├── universe.py             the 20 companies and their sectors
│   ├── data_loader.py          read CSV, validate required columns
│   ├── data_cleaning.py        types, sorting, consecutive-year check
│   ├── ratios.py               profitability, efficiency, leverage, cash flow
│   ├── trends.py               YoY change, CAGR, plain-language narratives
│   ├── financial_health.py     pillar scoring and overall score
│   └── reporting.py            cross-company analysis and PDF generation
├── tests/
├── fetch_data.py               download all companies
├── build_report.py             analyse all companies and write reports/
├── main.py                     single-company terminal analysis
└── requirements.txt
```

## Data model

One row per fiscal year, in reporting currency millions.

| Statement | Fields |
|---|---|
| Income statement | revenue, cogs, gross_profit, ebitda, ebit, interest_expense, tax_expense, net_income, eps |
| Balance sheet | cash, current_assets, total_assets, current_liabilities, total_debt, equity |
| Cash flow | cfo, capex, financing_cash_flow, investing_cash_flow |

`fcf` is derived as `cfo - capex` rather than stored, so it can never disagree with its inputs.

## Ratios calculated

**Profitability**: gross margin, EBITDA margin, EBIT margin, net margin, ROE, ROA
**Efficiency**: asset turnover, working capital, current ratio
**Leverage**: debt-to-equity, interest coverage
**Cash flow**: FCF, FCF margin, CFO-to-net-income

## Trend analysis

Rather than reporting a point-in-time number, each metric is described across the
full period with its direction and compound growth rate:

> EBITDA margin increased from 30.5% in FY2015 to 34.3% in FY2024 (+1.3% CAGR).

## Financial Health Score methodology

The score is deliberately simple and fully inspectable. Every metric is scored 0-100 by
linear interpolation between a **floor** (scores 0) and a **ceiling** (scores 100), clamped
at both ends. Metric scores are averaged into pillar scores by metric weight; pillar
scores are averaged into the overall score by pillar weight.

Where a *lower* value is better (debt-to-equity), the floor is set above the ceiling,
which inverts the scale.

| Pillar | Weight | Metric | Floor | Ceiling | Metric weight |
|---|---|---|---|---|---|
| Profitability | 25% | net margin | 0% | 25% | 40% |
| | | EBITDA margin | 5% | 35% | 30% |
| | | ROE | 5% | 40% | 30% |
| Growth | 20% | revenue CAGR | -5% | 15% | 50% |
| | | net income CAGR | -5% | 20% | 50% |
| Leverage | 20% | debt-to-equity | 3.00 | 0.20 | 40% |
| | | interest coverage | 3.0x | 30.0x | 35% |
| | | current ratio | 0.70 | 2.00 | 25% |
| Cash generation | 20% | FCF margin | 0% | 25% | 50% |
| | | CFO / net income | 0.80 | 1.50 | 50% |
| Efficiency | 15% | asset turnover | 0.30 | 1.20 | 50% |
| | | ROA | 2% | 20% | 50% |

Profitability, leverage, cash generation and efficiency are scored on the **latest**
fiscal year. Growth is scored on the **full-period CAGR**, so a single strong or weak
year cannot dominate it.

### Metrics that cannot be computed

Some companies do not report some line items. Where a metric is unavailable it is
**dropped and the remaining weights in its pillar are renormalized**, and the affected
metrics are listed on that company's page in the report. Scoring a data gap as either 0
or 100 would be a fabricated result. Three cases occur in this universe:

- **No gross profit line.** Exxon and Oracle do not report one in a usable form, so gross
  margin is blank for them. It is display-only and does not feed the score.
- **Interest expense no longer tagged.** Apple and Meta stopped tagging it separately in
  recent years. Dividing by zero would give infinite interest coverage and a perfect
  leverage score, so those years are marked unavailable instead.
- **Negative shareholders' equity.** Sustained buybacks push book equity below zero at
  Home Depot, AbbVie and Oracle. Debt-to-equity and ROE then flip sign and stop meaning
  anything, and a negative debt-to-equity would otherwise clamp to a perfect score.

### Rating bands

| Score | Rating |
|---|---|
| 80-100 | Strong |
| 65-79 | Healthy |
| 50-64 | Adequate |
| 35-49 | Weak |
| 0-34 | Distressed |

### Current results

| # | Company | Sector | Score | Rating |
|---|---|---|---|---|
| 1 | NVDA NVIDIA | Technology | 89.5 | Strong |
| 2 | META Meta Platforms | Comm. Services | 89.5 | Strong |
| 3 | GOOGL Alphabet | Comm. Services | 87.5 | Strong |
| 4 | MSFT Microsoft | Technology | 83.6 | Strong |
| 5 | AAPL Apple | Technology | 73.3 | Healthy |
| 6 | MRK Merck | Healthcare | 66.5 | Healthy |
| 7 | AMZN Amazon | Cons. Disc. | 65.8 | Healthy |
| 8 | JNJ Johnson & Johnson | Healthcare | 63.3 | Adequate |
| 9 | COST Costco | Cons. Staples | 59.8 | Adequate |
| 10 | XOM Exxon Mobil | Energy | 57.4 | Adequate |
| 11 | PG Procter & Gamble | Cons. Staples | 57.0 | Adequate |
| 12 | KO Coca-Cola | Cons. Staples | 54.9 | Adequate |
| 13 | TSLA Tesla | Cons. Disc. | 54.6 | Adequate |
| 14 | ORCL Oracle | Technology | 49.4 | Weak |
| 15 | CVX Chevron | Energy | 48.2 | Weak |
| 16 | HD Home Depot | Cons. Disc. | 47.0 | Weak |
| 17 | PEP PepsiCo | Cons. Staples | 45.8 | Weak |
| 18 | WMT Walmart | Cons. Staples | 45.1 | Weak |
| 19 | ABBV AbbVie | Healthcare | 42.2 | Weak |
| 20 | PFE Pfizer | Healthcare | 42.0 | Weak |

### Known limitations

These matter more than the score itself in an interview setting.

1. **Thresholds are absolute, not sector-relative.** This is the dominant weakness and it is
   visible in the results: the top four are all asset-light software and advertising
   businesses, while Walmart ranks 18th despite being a well-run company. Walmart's 3.1% net
   margin is normal for grocery retail and scores near zero against a threshold calibrated on
   absolute profitability. The ranking measures how a business model looks against fixed
   thresholds, not how well the company is run. Peer-relative scoring is the fix.
2. **ROE is distorted by buybacks.** Companies that repurchase heavily show ROE inflated by a
   shrunken equity base rather than by better returns, and at the extreme (Home Depot, AbbVie)
   equity goes negative and the ratio breaks entirely. Read ROE next to ROA, which is far
   more stable.
3. **A low leverage score is not always financial stress.** Apple scores 44.6 on leverage
   because its raw debt-to-equity looks stretched, yet its interest coverage and net cash
   position say the balance sheet is comfortable. This gap is exactly what an analyst should
   narrate rather than letting the ratio speak alone.
4. **Single-year snapshots are noisy.** Four of five pillars use the latest year only, so one
   unusual year moves them. Growth uses the full-period CAGR. A three-year average would trade
   responsiveness for stability.
5. **Financial-sector companies are excluded.** Banks and insurers do not report gross profit
   and their balance sheets are unclassified, so current ratio and working capital are
   undefined. They need a different model rather than a looser one.
6. **No accounting-quality checks yet.** Restatements, one-off items, and changes in
   segment reporting are not detected. The pipeline trusts what was filed.

## Roadmap

- Peer benchmarking so thresholds become sector-relative
- Three-year smoothing option for level-based pillars
- Accrual and earnings-quality flags
- Dashboard layer once the analytical core is settled

# Module 1 — Financial Statement Intelligence

Takes raw financial statements, cleans and structures them, calculates financial
metrics, identifies trends, and produces a transparent Financial Health Score.

Pilot company: **Apple Inc. (AAPL), FY2015-FY2024** (10 fiscal years, USD millions).

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Run against a different company by pointing at another CSV with the same columns:

```bash
.venv/bin/python main.py --data data/msft_financials.csv --company "Microsoft (MSFT)"
```

Tests:

```bash
.venv/bin/python -m pytest tests/ -q
```

## Structure

```
financial_statement_intelligence/
├── data/aapl_financials.csv    one row per fiscal year
├── notebooks/
├── src/
│   ├── data_loader.py          read CSV, validate required columns
│   ├── data_cleaning.py        types, sorting, consecutive-year check
│   ├── ratios.py               profitability, efficiency, leverage, cash flow
│   ├── trends.py               YoY change, CAGR, plain-language narratives
│   └── financial_health.py     pillar scoring and overall score
├── tests/
├── main.py
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

**Profitability** — gross margin, EBITDA margin, EBIT margin, net margin, ROE, ROA
**Efficiency** — asset turnover, working capital, current ratio
**Leverage** — debt-to-equity, interest coverage
**Cash flow** — FCF, FCF margin, CFO-to-net-income

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
| Growth | 20% | revenue CAGR | -5% | 15% | 40% |
| | | net income CAGR | -5% | 20% | 35% |
| | | EPS CAGR | -5% | 20% | 25% |
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

### Rating bands

| Score | Rating |
|---|---|
| 80-100 | Strong |
| 65-79 | Healthy |
| 50-64 | Adequate |
| 35-49 | Weak |
| 0-34 | Distressed |

### Current result

```
FINANCIAL HEALTH: 76.8/100 — Healthy
  Profitability     97.7  (weight 25%)
  Growth            55.1  (weight 20%)
  Leverage          54.3  (weight 20%)
  Cash generation   83.0  (weight 20%)
  Efficiency        92.9  (weight 15%)
```

### Known limitations

These matter more than the score itself in an interview setting.

1. **Thresholds are absolute, not sector-relative.** A 24% net margin is exceptional for a
   retailer and unremarkable for a software company. Peer benchmarking is the fix, and it
   belongs in a later module.
2. **ROE is distorted by buybacks.** Apple's ROE rises from 45% to 165% across the period
   largely because aggressive repurchases shrank the equity base, not because returns
   improved that dramatically. The leverage pillar partly offsets this, but ROE should be
   read alongside ROA (which is far flatter).
3. **Leverage scores low for a reason worth defending.** Apple's 1.87x debt-to-equity looks
   stretched on the raw ratio, yet interest coverage above 30x and a large net cash position
   say the balance sheet is not stressed. This is exactly the gap an analyst should narrate
   rather than let the number speak alone.
4. **Single-year snapshots are noisy.** Four of five pillars use the latest year only. Using
   a three-year average would trade responsiveness for stability.
5. **No accounting-quality checks yet.** Restatements, one-off items, and changes in
   segment reporting are not detected.

## Roadmap

- Peer benchmarking so thresholds become sector-relative
- Three-year smoothing option for level-based pillars
- Accrual and earnings-quality flags
- Dashboard layer once the analytical core is settled

import pandas as pd
import pytest

from fsi.data_cleaning import clean_financials
from fsi.financial_health import MetricSpec, financial_health_score, score_metric
from fsi.ratios import calculate_ratios
from fsi.trends import cagr


def make_row(year: int, **overrides) -> dict:
    base = dict(
        fiscal_year=year,
        revenue=1000.0,
        cogs=600.0,
        gross_profit=400.0,
        ebitda=250.0,
        ebit=200.0,
        interest_expense=10.0,
        tax_expense=40.0,
        net_income=150.0,
        eps=1.50,
        cash=300.0,
        current_assets=500.0,
        total_assets=1200.0,
        current_liabilities=250.0,
        total_debt=300.0,
        equity=600.0,
        cfo=220.0,
        capex=70.0,
        financing_cash_flow=-50.0,
        investing_cash_flow=-80.0,
    )
    base.update(overrides)
    return base


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return pd.DataFrame([make_row(2020), make_row(2021), make_row(2022)])


def test_clean_sorts_by_year(sample_df):
    shuffled = sample_df.iloc[::-1].reset_index(drop=True)
    cleaned = clean_financials(shuffled)
    assert cleaned["fiscal_year"].tolist() == [2020, 2021, 2022]


def test_clean_rejects_year_gaps():
    df = pd.DataFrame([make_row(2020), make_row(2023)])
    with pytest.raises(ValueError, match="consecutive"):
        clean_financials(df)


def test_ratios(sample_df):
    r = calculate_ratios(sample_df).iloc[0]
    assert r["gross_margin"] == pytest.approx(0.40)
    assert r["net_margin"] == pytest.approx(0.15)
    assert r["roe"] == pytest.approx(0.25)
    assert r["debt_to_equity"] == pytest.approx(0.50)
    assert r["fcf"] == pytest.approx(150.0)
    assert r["interest_coverage"] == pytest.approx(20.0)


def test_cagr_doubling_over_two_years():
    assert cagr(100, 400, 2) == pytest.approx(1.0)


def test_score_metric_clamps_to_range():
    spec = MetricSpec("x", floor=0.0, ceiling=0.20, weight=1.0)
    assert score_metric(-0.5, spec) == 0
    assert score_metric(0.99, spec) == 100
    assert score_metric(0.10, spec) == pytest.approx(50)


def test_score_metric_inverted_rewards_lower_values():
    spec = MetricSpec("debt_to_equity", floor=3.0, ceiling=0.2, weight=1.0)
    assert score_metric(0.2, spec) > score_metric(2.5, spec)


def test_health_score_in_range(sample_df):
    health = financial_health_score(calculate_ratios(sample_df))
    assert 0 <= health["overall"] <= 100
    assert len(health["pillars"]) == 5


def test_negative_equity_makes_equity_ratios_unavailable():
    """Buyback-driven negative equity must not score as pristine leverage."""
    df = pd.DataFrame([make_row(y, equity=-500.0) for y in (2020, 2021, 2022)])
    r = calculate_ratios(df)
    assert r["debt_to_equity"].isna().all()
    assert r["roe"].isna().all()


def test_zero_interest_expense_is_not_infinite_coverage():
    df = pd.DataFrame([make_row(y, interest_expense=0.0) for y in (2020, 2021, 2022)])
    r = calculate_ratios(df)
    assert r["interest_coverage"].isna().all()


@pytest.mark.parametrize("first,last", [(-100, 200), (100, -200), (0, 100)])
def test_cagr_undefined_for_non_positive_endpoints(first, last):
    assert pd.isna(cagr(first, last, 3))


def test_missing_metric_renormalizes_pillar_weights():
    """A dropped metric must redistribute its weight, not count as zero or full marks."""
    full = pd.DataFrame([make_row(y) for y in (2020, 2021, 2022)])
    # Wipe interest expense so interest coverage (35% of Leverage) becomes unavailable.
    partial = full.assign(interest_expense=0.0)

    full_health = financial_health_score(calculate_ratios(full))
    partial_health = financial_health_score(calculate_ratios(partial))

    assert "interest_coverage" in partial_health["unavailable_metrics"]
    assert not pd.isna(partial_health["pillars"]["Leverage"])

    # Remaining metrics score 100 (D/E 0.50) and ~0 (current ratio 2.0 hits the ceiling),
    # so the renormalized result must stay a genuine weighted average, not collapse to 0.
    assert 0 < partial_health["pillars"]["Leverage"] <= 100
    assert partial_health["pillars"]["Leverage"] != full_health["pillars"]["Leverage"]

"""Tests for sector-relative scoring.

Two of these pin findings rather than mechanics: that peer scoring removes the cross-sector
distortion it was built to remove, and that it does *not* fix the Walmart case the roadmap
expected it to. The second is the more valuable test. A future change that quietly "fixes"
Walmart has to come and argue with it, which is the point.
"""

import pandas as pd
import pytest

from fsi.data_cleaning import clean_financials
from fsi.data_loader import load_financials
from fsi.financial_health import PILLARS, financial_health_score
from fsi.peers import (
    DATA_DIR,
    MIN_PEERS,
    compare,
    higher_is_better,
    load_universe_metrics,
    peer_group,
    peer_relative_score,
    percentile_rank,
    rank_universe,
    resolve_sector,
)
from fsi.ratios import calculate_ratios


@pytest.fixture(scope="module")
def universe():
    return load_universe_metrics()


def ratios_for(ticker: str) -> pd.DataFrame:
    return calculate_ratios(clean_financials(load_financials(DATA_DIR / f"{ticker}_financials.csv")))


# --- percentile mechanics ------------------------------------------------------------------

def test_a_value_above_every_peer_is_the_top_of_the_range():
    assert percentile_rank(10.0, [1.0, 2.0, 3.0], higher_is_better_metric=True) == 100.0


def test_a_value_below_every_peer_is_the_bottom_of_the_range():
    assert percentile_rank(0.5, [1.0, 2.0, 3.0], higher_is_better_metric=True) == 0.0


def test_ties_are_mid_ranked_rather_than_resolved_to_either_end():
    """Level with two of four peers should read as the middle of that cluster. Counting ties
    as wins would give 75, counting them as losses 25; the honest answer is between."""
    assert percentile_rank(2.0, [1.0, 2.0, 2.0, 3.0], higher_is_better_metric=True) == 50.0


def test_a_lower_is_better_metric_inverts_the_scale():
    """100 must always mean "best in the peer group", so the least-levered company scores
    highest on debt-to-equity even though its raw value is the smallest."""
    low_debt = percentile_rank(0.1, [1.0, 2.0, 3.0], higher_is_better_metric=False)
    high_debt = percentile_rank(5.0, [1.0, 2.0, 3.0], higher_is_better_metric=False)
    assert low_debt == 100.0
    assert high_debt == 0.0


def test_a_missing_value_or_an_empty_peer_set_produces_no_rank():
    assert pd.isna(percentile_rank(float("nan"), [1.0, 2.0, 3.0]))
    assert pd.isna(percentile_rank(1.0, []))
    assert pd.isna(percentile_rank(1.0, [float("nan"), float("nan")]))


def test_metric_direction_is_read_off_the_existing_threshold_spec():
    """Direction lives in one place. If someone later flips a spec's floor and ceiling, this
    module follows automatically instead of disagreeing with the absolute scorer."""
    specs = {m.name: m for pillar in PILLARS for m in pillar.metrics}
    assert higher_is_better(specs["net_margin"]) is True
    assert higher_is_better(specs["interest_coverage"]) is True
    assert higher_is_better(specs["debt_to_equity"]) is False


# --- peer group construction ----------------------------------------------------------------

def test_every_universe_company_loads_with_a_sector(universe):
    assert len(universe) == 20
    assert universe["sector"].notna().all()
    assert "net_margin" in universe.columns


def test_a_company_is_never_its_own_peer(universe):
    group = peer_group("Technology", exclude="AAPL", universe_metrics=universe)
    assert "AAPL" not in group.tickers
    assert set(group.tickers) == {"MSFT", "NVDA", "ORCL"}


def test_a_sector_with_too_few_peers_is_not_usable(universe):
    """Energy has two members, so excluding the target leaves one. A percentile over one peer
    is a coin flip wearing a number."""
    group = peer_group("Energy", exclude="XOM", universe_metrics=universe)
    assert group.size < MIN_PEERS
    assert not group.usable


# --- refusals rather than fabricated scores --------------------------------------------------

def test_a_thin_sector_refuses_to_score_and_says_why(universe):
    result = peer_relative_score(ratios_for("XOM"), "Energy", exclude_ticker="XOM",
                                 universe_metrics=universe)
    assert result["usable"] is False
    assert pd.isna(result["overall"])
    assert "at least" in result["note"]
    assert "Energy" in result["note"]


def test_an_unknown_sector_refuses_to_score(universe):
    result = peer_relative_score(ratios_for("AAPL"), None, universe_metrics=universe)
    assert result["usable"] is False
    assert "no peer group" in result["note"].lower()


def test_a_sector_absent_from_the_universe_refuses_to_score(universe):
    result = peer_relative_score(ratios_for("AAPL"), "Utilities", universe_metrics=universe)
    assert result["usable"] is False
    assert result["peer_count"] == 0


# --- scoring ---------------------------------------------------------------------------------

def test_a_usable_sector_produces_a_score_and_names_its_peers(universe):
    result = peer_relative_score(ratios_for("AAPL"), "Technology", exclude_ticker="AAPL",
                                 universe_metrics=universe)
    assert result["usable"] is True
    assert 0.0 <= result["overall"] <= 100.0
    assert result["peer_count"] == 3
    assert set(result["peers"]) == {"MSFT", "NVDA", "ORCL"}
    assert result["rating"] is not None


def test_pillar_scores_stay_inside_the_percentile_range(universe):
    result = peer_relative_score(ratios_for("MSFT"), "Technology", exclude_ticker="MSFT",
                                 universe_metrics=universe)
    for score in result["pillars"].values():
        if not pd.isna(score):
            assert 0.0 <= score <= 100.0


def test_percentile_scores_are_zero_sum_within_a_sector(universe):
    """A structural property worth knowing rather than discovering later: ranks average to the
    middle by construction, so a peer score can never say "this whole sector is excellent".
    Only the absolute score can make a cross-sector statement, which is why both are kept."""
    scores = []
    for ticker in peer_group("Consumer Staples", universe_metrics=universe).tickers:
        result = peer_relative_score(ratios_for(ticker), "Consumer Staples",
                                     exclude_ticker=ticker, universe_metrics=universe)
        scores.append(result["overall"])
    assert sum(scores) / len(scores) == pytest.approx(50.0, abs=7.0)


# --- the findings this update exists to record -----------------------------------------------

def test_peer_scoring_removes_the_cross_sector_distortion_in_technology(universe):
    """The fix working as intended. Every Technology name scores materially lower against its
    own peers than against fixed thresholds, because the absolute score was partly rewarding
    them for being in a structurally rich sector rather than for how they are run."""
    drops = []
    for ticker in ("MSFT", "AAPL", "ORCL"):
        ratios = ratios_for(ticker)
        comparison = compare(ratios, financial_health_score(ratios), "Technology",
                             exclude_ticker=ticker, universe_metrics=universe)
        drops.append(comparison.gap)

    assert all(gap < -20 for gap in drops), f"expected large drops, got {drops}"


def test_peer_scoring_does_not_rescue_walmart(universe):
    """The roadmap expected sector-relative scoring to fix the Walmart case. It does not, and
    that is the finding.

    GICS "Consumer Staples" holds grocery and warehouse retail (Walmart at a 3.1% net margin,
    Costco at 2.9%) alongside branded consumer goods (Coca-Cola at 27.3%, P&G at 18.4%). Those
    are different business models with a roughly nine-fold margin spread, so ranking Walmart
    against Coca-Cola on margin repeats the original category error at a smaller scale. The
    real fix is industry-level peers, not sector-level ones.
    """
    ratios = ratios_for("WMT")
    comparison = compare(ratios, financial_health_score(ratios), "Consumer Staples",
                         exclude_ticker="WMT", universe_metrics=universe)

    assert comparison.usable
    assert abs(comparison.gap) < 15, (
        f"Walmart's peer score moved by {comparison.gap:+.1f} points. If sector-level peers "
        "now rescue it, the universe or the sector labels changed and this finding needs "
        "rewriting rather than the assertion loosening."
    )
    assert comparison.relative["rating"] == "Weak"


def test_the_comparison_narrates_both_readings_and_the_gap(universe):
    ratios = ratios_for("MSFT")
    comparison = compare(ratios, financial_health_score(ratios), "Technology",
                         exclude_ticker="MSFT", universe_metrics=universe)
    text = comparison.read()

    assert "against fixed thresholds" in text
    assert "Technology peers" in text
    assert "Gap:" in text
    assert "NVDA" in text  # the peer group is named, not just counted


def test_an_unusable_comparison_still_reports_the_absolute_score(universe):
    ratios = ratios_for("XOM")
    comparison = compare(ratios, financial_health_score(ratios), "Energy",
                         exclude_ticker="XOM", universe_metrics=universe)
    assert not comparison.usable
    assert pd.isna(comparison.gap)
    assert "against fixed thresholds" in comparison.read()


# --- the universe-wide table -------------------------------------------------------------------

def test_rank_universe_covers_every_company_and_marks_the_unrankable(universe):
    table = rank_universe(universe_metrics=universe)
    assert len(table) == 20
    assert table["absolute"].notna().all()
    # The three thin sectors (Communication Services, Energy, Consumer Discretionary) cannot
    # be ranked, and are left blank rather than filled with a number.
    assert table["relative"].isna().sum() == 7


def test_resolve_sector_prefers_the_hand_checked_universe_label():
    """The universe label was verified against the filings; a provider's classification is a
    fallback, not an override. Checked with the network disabled so it cannot silently pass
    by reaching yfinance instead."""
    assert resolve_sector("WMT", allow_network=False) == "Consumer Staples"
    assert resolve_sector("AAPL", allow_network=False) == "Technology"
    assert resolve_sector("NOT_A_TICKER", allow_network=False) is None

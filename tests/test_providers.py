"""Tests for the multi-market data layer.

These target currency, which is where a global loader silently produces a plausible wrong
answer instead of an error.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fsi.providers import resolve
from fsi.providers.yahoo import normalise_quote, resolve_statement_currency


def test_pence_quotes_are_converted_to_pounds():
    """London quotes in GBp. Read as pounds, the share price is a hundred times too high."""
    price, cap, currency, notes = normalise_quote(3320.0, 18_334_199_410_400.0, "GBp")
    assert price == pytest.approx(33.20)
    assert cap == pytest.approx(183_341_994_104.0)
    assert currency == "GBP"
    assert notes


def test_major_unit_quotes_pass_through_untouched():
    price, cap, currency, notes = normalise_quote(180.14, 207_918_349_648.0, "EUR")
    assert (price, cap, currency) == (180.14, 207_918_349_648.0, "EUR")
    assert not notes


def test_far_apart_currencies_are_decided_by_market_cap(monkeypatch):
    """Infosys reports in USD and quotes in INR, roughly 95 to one. There the plausibility
    test discriminates, and it must, because the flag is right in this case."""
    from fsi.providers import yahoo

    monkeypatch.setattr(yahoo, "fx_rate", lambda a, b: 95.0)
    currency, rate, notes = resolve_statement_currency("USD", "INR", 20e9, 4.7e12)
    assert currency == "USD"
    assert rate == 95.0
    assert notes


def test_far_apart_currencies_ignore_a_wrong_flag(monkeypatch):
    """HCL Technologies is tagged USD while reporting in rupees. Believing the tag would
    overstate the company a hundredfold."""
    from fsi.providers import yahoo

    monkeypatch.setattr(yahoo, "fx_rate", lambda a, b: 95.0)
    currency, rate, _ = resolve_statement_currency("USD", "INR", 1.3e12, 3.7e12)
    assert (currency, rate) == ("INR", 1.0)


def test_similar_currencies_trust_the_declared_flag(monkeypatch):
    """Shell reports in USD and quotes in GBP at about 0.79. Both readings look equally
    plausible on a price-to-sales test, so the source's tag is the better evidence."""
    from fsi.providers import yahoo

    monkeypatch.setattr(yahoo, "fx_rate", lambda a, b: 0.79)
    currency, rate, notes = resolve_statement_currency("USD", "GBP", 285e9, 183e9)
    assert currency == "USD"
    assert rate == pytest.approx(0.79)
    assert notes


def test_matching_currencies_need_no_conversion():
    currency, rate, notes = resolve_statement_currency("EUR", "EUR", 36.8e9, 208e9)
    assert (currency, rate) == ("EUR", 1.0)
    assert not notes


@pytest.mark.parametrize("ticker,expected", [
    ("AAPL", "United States"), ("SHEL.L", "London"), ("SAP.DE", "Frankfurt"),
    ("NESN.SW", "Zurich"), ("RELIANCE.NS", "India (NSE)"), ("7203.T", "Tokyo"),
])
def test_region_is_read_from_the_exchange_suffix(ticker, expected):
    assert resolve.region_of(ticker) == expected


def test_us_listings_are_those_without_a_suffix():
    assert resolve.is_us_listing("AAPL")
    assert not resolve.is_us_listing("SAP.DE")

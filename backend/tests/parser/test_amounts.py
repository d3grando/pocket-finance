"""parse_amount / parse_number — the validator recomputes LLM amounts with these."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.parser.amounts import parse_amount, parse_number

CASES = [
    # raw, currency, small_thousands, expected
    ("85к", "VND", True, "85000"),
    ("85k", None, True, "85000"),
    ("85 к", None, True, "85000"),
    ("85К", None, True, "85000"),
    ("85", "VND", True, "85000"),
    ("85", None, True, "85000"),
    ("85", "VND", False, "85"),
    ("999", "VND", True, "999000"),
    ("1000", "VND", True, "1000"),
    ("450000", "VND", True, "450000"),
    ("450 000", "VND", True, "450000"),
    ("450.000", "VND", True, "450000"),
    ("450,000", "VND", True, "450000"),
    ("1.250.000", "VND", True, "1250000"),
    ("1,250,000", "VND", True, "1250000"),
    ("1 250 000", None, True, "1250000"),
    ("1.5м", "VND", True, "1500000"),
    ("1,5м", "VND", True, "1500000"),
    ("1.5m", None, True, "1500000"),
    ("1.5 млн", None, True, "1500000"),
    ("2 ляма", None, True, "2000000"),
    ("2 лимона", None, True, "2000000"),
    ("2kk", None, True, "2000000"),
    ("2кк", None, True, "2000000"),
    ("7.6м", "VND", True, "7600000"),
    ("21.3м", "VND", True, "21300000"),
    ("600к", None, True, "600000"),
    ("100 тыс", None, True, "100000"),
    ("100 тысяч", None, True, "100000"),
    ("3 тыщи", None, True, "3000"),
    ("1 млрд", None, True, "1000000000"),
    ("50тр", None, True, "50000"),
    ("50тр", "RUB", True, "50000"),
    ("50 тр", None, True, "50000"),
    ("180$", "USD", True, "180"),
    ("180$", None, True, "180"),
    ("$180", None, True, "180"),
    ("$ 180", None, True, "180"),
    ("12.40$", None, True, "12.4"),
    ("12,40$", None, True, "12.4"),
    ("15 долларов", None, True, "15"),
    ("250 баксов", None, True, "250"),
    ("1.5k$", None, True, "1500"),
    ("$1.5k", None, True, "1500"),
    ("15к$", None, True, "15000"),
    ("3 тона", None, True, "3"),
    ("3 тона", "TON", True, "3"),
    ("0.5 eth", None, True, "0.5"),
    ("0,125 btc", None, True, "0.125"),
    ("300 USDT", None, True, "300"),
    ("300 usdt", "USDT", True, "300"),
    ("300 юсдт", None, True, "300"),
    ("500р", None, True, "500"),
    ("500 руб", None, True, "500"),
    ("500 рублей", None, True, "500"),
    ("500₽", None, True, "500"),
    ("85.000đ", None, True, "85000"),
    ("85kđ", None, True, "85000"),
    ("85k₫", None, True, "85000"),
    ("₫450,000", None, True, "450000"),
    ("450000 донгов", None, True, "450000"),
    ("85 донгов", None, True, "85000"),
    ("3€", None, True, "3"),
    ("0.0215 btc", None, True, "0.0215"),
    ("100", "USD", True, "100"),
    ("100", "USDT", True, "100"),
    ("+500к", None, True, "500000"),
    ("-150к", None, True, "150000"),
    ("−150к", None, True, "150000"),
    ("1 косарь", None, True, "1000"),
    ("1.234,56", "USD", True, "1234.56"),
    ("1,500", "BTC", True, "1.5"),
    ("1.500", "USD", True, "1500"),
    ("0.1", "ETH", True, "0.1"),
]


@pytest.mark.parametrize("raw,cur,small,expected", CASES)
def test_parse_amount(raw: str, cur: str | None, small: bool, expected: str) -> None:
    v = parse_amount(raw, cur, small_thousands=small)
    assert isinstance(v, Decimal)
    assert v == Decimal(expected)
    assert "E" not in str(v)


@pytest.mark.parametrize("raw", ["", "   ", "abc", "кофе", "0", "0к", "85к 30к", "1 2 3",
                                 "1e5", "x" * 70, "12/10", "85%"])
def test_parse_amount_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_amount(raw, None)


def test_zero_allowed_for_balance() -> None:
    assert parse_amount("0", "VND", allow_zero=True) == 0


def test_too_big() -> None:
    with pytest.raises(ValueError):
        parse_amount("99999999999999999999 млрд", "USD")


def test_none_raw() -> None:
    with pytest.raises(ValueError):
        parse_amount(None, None)  # type: ignore[arg-type]


@pytest.mark.parametrize("s,cur,mult,expected,amb", [
    ("1.500", "BTC", False, "1.5", True),
    ("1.500", "USD", False, "1500", True),
    ("1.500", "VND", False, "1500", False),
    ("1.500", "RUB", False, "1500", False),
    ("1.5", "VND", True, "1.5", False),
    ("1.500", "VND", True, "1.5", False),
    ("0.500", "USD", False, "0.5", False),
])
def test_parse_number(s: str, cur: str, mult: bool, expected: str, amb: bool) -> None:
    v, a = parse_number(s, cur, mult)
    assert v == Decimal(expected)
    assert a is amb


def test_never_float() -> None:
    v = parse_amount("0.1", "ETH")
    assert type(v) is Decimal


def test_small_thousands_positional() -> None:
    """ARCH §7 signature parse_amount(raw, currency, small_thousands)."""
    assert parse_amount("85", "VND", False) == Decimal("85")
    assert parse_amount("85", "VND", True) == Decimal("85000")

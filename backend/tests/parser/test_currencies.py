"""detect_currency / coin_candidate."""

from __future__ import annotations

import pytest

from app.services.parser.currencies import coin_candidate, detect_currency

CASES = [
    ("₫", "VND"), ("đ", "VND"), ("vnd", "VND"), ("VND", "VND"), ("донг", "VND"),
    ("донгов", "VND"), ("донга", "VND"),
    ("$", "USD"), ("usd", "USD"), ("USD", "USD"), ("бакс", "USD"), ("баксов", "USD"),
    ("бакса", "USD"), ("доллар", "USD"), ("долларов", "USD"), ("доллара", "USD"),
    ("dollars", "USD"),
    ("usdt", "USDT"), ("USDT", "USDT"), ("юсдт", "USDT"), ("тезер", "USDT"), ("тезеров", "USDT"),
    ("tether", "USDT"),
    ("usdc", "USDC"),
    ("₽", "RUB"), ("руб", "RUB"), ("руб.", "RUB"), ("рублей", "RUB"), ("рубля", "RUB"),
    ("rub", "RUB"), ("р", "RUB"),
    ("€", "EUR"), ("евро", "EUR"), ("бат", "THB"), ("юаней", "CNY"),
    ("тон", "TON"), ("тона", "TON"), ("тонов", "TON"), ("TON", "TON"), ("тонкоин", "TON"),
    ("биток", "BTC"), ("битка", "BTC"), ("btc", "BTC"), ("биткоин", "BTC"),
    ("эфир", "ETH"), ("эфира", "ETH"), ("eth", "ETH"),
    ("солана", "SOL"), ("sol", "SOL"),
    ("трон", "TRX"), ("trx", "TRX"),
    ("not", "NOT"), ("ноткоин", "NOT"),
    ("bnb", "BNB"), ("xrp", "XRP"), ("doge", "DOGE"), ("доги", "DOGE"),
    ("ltc", "LTC"), ("pepe", "PEPE"), ("hmstr", "HMSTR"), ("dogs", "DOGS"),
    ("Ёлка", None), ("кофе", None), ("такси", None), ("", None), ("abc", None),
]


@pytest.mark.parametrize("token,code", CASES)
def test_detect_currency(token: str, code: str | None) -> None:
    assert detect_currency(token) == code


def test_usd_is_not_usdt() -> None:
    assert detect_currency("usd") != detect_currency("usdt")


@pytest.mark.parametrize("token,cand", [
    ("abcd", "ABCD"), ("HMSTRX", "HMSTRX"), ("wif", "WIF"), ("kg", None), ("pro", None),
    ("k", None), ("usdt", None), ("toolongticker", None), ("кофе", None), ("gb", None),
])
def test_coin_candidate(token: str, cand: str | None) -> None:
    assert coin_candidate(token) == cand

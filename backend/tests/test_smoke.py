"""Architect smoke tests: schema creates, health works, intents round-trip."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.money import format_money
from app.schemas.intents import IntentList


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["db"] is True


def test_format_money():
    assert format_money("1250000", "VND") == "1 250 000 ₫"
    assert format_money("-180", "USD") == "−$180.00"
    assert format_money("0.02150000", "BTC") == "0.0215 BTC"


def test_intents_reject_float():
    with pytest.raises(ValueError):
        IntentList.model_validate({"intents": [{"type": "create_transaction", "amount": 1.5}]})
    il = IntentList.model_validate(
        {"intents": [{"type": "create_transaction", "amount": "85000", "amount_raw": "85"}]}
    )
    assert il.intents[0].amount == Decimal("85000")

"""Live smoke (2 real calls): Vietcombank USD sell and CoinGecko TON price are reachable from the
server and parse into Decimal. Opt-in: run with PF_LIVE=1 (skipped by default, e.g. in CI); an
unreachable provider is a skip, not a failure."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.timeutil import local_today
from app.services.rates import providers as P

pytestmark = pytest.mark.live


async def test_live_vietcombank_usd_sell():
    on = local_today(None) - timedelta(days=1)
    try:
        rows = await P.fetch_vietcombank(on)
    except P.ProviderError as e:
        pytest.skip(f"Vietcombank unavailable: {e}")
    sell = next(r for r in rows if r.base == "USD" and r.kind == "sell")
    assert isinstance(sell.rate, Decimal)
    assert Decimal(15000) < sell.rate < Decimal(60000)


async def test_live_coingecko_ton():
    try:
        rows = await P.fetch_coingecko(["TON"])
    except P.ProviderError as e:  # free API throttles bursts (HTTP 429 «Throttled»)
        pytest.skip(f"CoinGecko unavailable: {e}")
    if not rows:
        pytest.skip("CoinGecko returned no price (throttled?)")
    assert rows[0].base == "TON" and isinstance(rows[0].rate, Decimal) and rows[0].rate > 0

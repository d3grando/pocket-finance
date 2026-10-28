"""Stablecoin amounts on cards echo what was written («20.2552 USDT» must not
become «20.26 USDT»). Must match web/src/lib/format.ts and bot/pocket_bot/fmt.py."""
from app.core.money import format_money

N = " "


def test_stablecoin_keeps_written_precision():
    assert format_money("20.2552", "USDT") == f"20.2552{N}USDT"
    assert format_money("12.5", "USDT") == f"12.50{N}USDT"
    assert format_money("300", "USDT") == f"300{N}USDT"
    assert format_money("0.12345678", "USDT") == f"0.123457{N}USDT"
    assert format_money("-20.2552", "USDT", sign="always") == f"−20.2552{N}USDT"

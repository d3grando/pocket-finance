"""Formatting (DESIGN.md §3.2 reference examples), callback codec (API.md §13), config."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pocket_bot import callbacks as cb
from pocket_bot.config import BotSettings
from pocket_bot.fmt import (
    category_emoji,
    esc,
    format_money,
    format_percent,
    human_date,
    ops_word,
    to_decimal,
    tx_amount,
)
from pocket_bot.stash import Stash

N = " "


@pytest.mark.parametrize(
    ("amount", "cur", "kw", "expected"),
    [
        ("1250000", "VND", {}, f"1{N}250{N}000{N}₫"),
        ("-420000", "VND", {"sign": "always"}, f"−420{N}000{N}₫"),
        ("300", "USDT", {"sign": "always"}, f"+300{N}USDT"),
        ("12.4", "USD", {"approx": True}, f"≈{N}$12.40"),
        ("-180", "USD", {}, "−$180.00"),
        ("3", "TON", {}, f"3{N}TON"),
        ("0.02150000", "BTC", {}, f"0.0215{N}BTC"),
        ("4990.5", "RUB", {}, f"4{N}990.50{N}₽"),
        ("4990", "RUB", {}, f"4{N}990{N}₽"),
        ("12.5", "USDT", {}, f"12.50{N}USDT"),
        ("150000", "VND", {}, f"150{N}000{N}₫"),
        ("1250", "USD", {}, f"$1{N}250.00"),
        (None, "VND", {}, "—"),
        ("0", "VND", {}, f"0{N}₫"),
    ],
)
def test_format_money_design_examples(amount, cur, kw, expected):
    assert format_money(amount, cur, **kw) == expected


def test_money_never_float():
    with pytest.raises(TypeError):
        to_decimal(1.5)
    assert to_decimal("150000") == Decimal(150000)
    assert to_decimal(f"1{N}250") == Decimal(1250)


def test_percent_plural_dates_escape():
    assert format_percent("0.183") == "+18%"
    assert format_percent("0.045") == "+4.5%"
    assert format_percent("-0.12") == "−12%"
    assert format_percent("0.32", sign="never") == "32%"
    assert ops_word(1) == "1 операция"
    assert ops_word(3) == "3 операции"
    assert ops_word(11) == "11 операций"
    assert ops_word(22) == "22 операции"
    today = date(2026, 10, 27)
    assert human_date("2026-10-27", today) == "сегодня"
    assert human_date("2026-10-26", today) == "вчера"
    assert human_date("2026-11-05", today) == "5 ноября"
    assert human_date("2025-10-05", today) == "5 октября 2025"
    assert esc("<b>&") == "&lt;b&gt;&amp;"
    assert esc(None) == ""


def test_category_emoji_table():
    assert category_emoji("Еда") == "🍴"
    assert category_emoji("Такси/транспорт") == "🚕"
    assert category_emoji("Серверы/инфра") == "🖥"
    assert category_emoji("Софт и AI 🆕") == "🤖"
    assert category_emoji("Новая штука") == "🏷"
    assert category_emoji(None) == "🏷"


def test_tx_amount_signs():
    exp = {"kind": "expense", "amount": "150000", "currency": "VND"}
    inc = {"kind": "income", "amount": "300", "currency": "USDT"}
    adj = {"kind": "adjustment", "amount": "120000", "currency": "VND",
           "account_amount": "-120000", "account_asset": "VND"}
    assert tx_amount(exp) == f"150{N}000{N}₫"
    assert tx_amount(exp, signed=True) == f"−150{N}000{N}₫"
    assert tx_amount(inc, signed=True) == f"+300{N}USDT"
    # the card echoes what was written, not «20.26»
    assert tx_amount({**inc, "amount": "20.2552"}) == f"20.2552{N}USDT"
    assert tx_amount(adj, signed=True) == f"−120{N}000{N}₫"


def test_callback_codec_fits_64_bytes():
    uid = "6b0c0d3e-1111-4a2b-9c3d-000000000001"
    data = [
        cb.undo(uid), cb.edit(2**53), cb.choose_account(2**53, 2**53), cb.choose_account(5, None),
        cb.later(1), cb.change_category(1), cb.set_category(2**53, 2**53),
        cb.confirm_preview(uid), cb.cancel_preview(uid), cb.send_text(3),
        cb.recurring_record(2), cb.recurring_skip(2), cb.recurring_amount(2),
        cb.retry(2**31, True), cb.undo_many("abcdef0123"), cb.settings_toggle("m"),
    ]
    for d in data:
        assert len(d.encode()) <= 64, d
    assert cb.undo(uid) == "u:6b0c0d3e11114a2b9c3d000000000001"
    assert cb.choose_account(125, None) == "a:125:0"
    assert cb.uuid_from_hex(cb.hex_id(uid)) == uid
    p = cb.parse("s:124:7")
    assert p and p.op == "s" and p.int_arg(0) == 124 and p.int_arg(1) == 7
    assert cb.parse("") is None
    assert cb.parse("x" * 65) is None
    with pytest.raises(ValueError):
        cb.pack("t", "y" * 70)


def test_route_keys():
    assert cb.route_for_key("/analytics?period=today") == "/analytics?period=today"
    assert cb.route_for_key("inbox") == "/operations?status=needs_account"
    assert cb.route_for_key("analytics_last_month") == "/analytics?period=last_month"
    assert cb.route_for_key("analytics:this_week") == "/analytics?period=this_week"
    assert cb.route_for_key("analytics_bogus") is None
    assert cb.route_for_key("") is None


def test_config_allowed_ids_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_IDS", "123456789")
    assert BotSettings(_env_file=None).telegram_allowed_ids == [123456789]
    monkeypatch.setenv("TELEGRAM_ALLOWED_IDS", "1, 2;3")
    assert BotSettings(_env_file=None).telegram_allowed_ids == [1, 2, 3]
    monkeypatch.delenv("TELEGRAM_ALLOWED_IDS")
    # No built-in owner: unset (or empty) = the bot answers nobody.
    assert BotSettings(_env_file=None).telegram_allowed_ids == []
    monkeypatch.setenv("TELEGRAM_ALLOWED_IDS", "")
    assert BotSettings(_env_file=None).telegram_allowed_ids == []
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    assert BotSettings(_env_file=None).public_url == ""


def test_stash_lru_and_ttl():
    now = [0.0]
    s = Stash(max_items=2, ttl_seconds=10, clock=lambda: now[0])
    k1 = s.put("a")
    s.put("b", key="k2")
    s.put("c", key="k3")
    assert s.get(k1) is None  # evicted by size
    assert s.get("k2") == "b"
    now[0] = 11
    assert s.get("k3") is None  # expired

"""Validator: amounts, currencies, dates, references, category dedup, safety checks."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from app.schemas.intents import (
    AdjustBalanceIntent,
    CreateCategoryIntent,
    CreateTransactionIntent,
    IntentList,
    TransferIntent,
    UndoIntent,
    UpdateTransactionIntent,
)
from app.services.ai.context import build_parse_context
from app.services.ai.validator import ValidatedIntent, ValidationFailure, validate


def _intents(*items: dict):
    return list(IntentList.model_validate({"intents": list(items)}).intents)


@pytest.fixture
async def ctx(session, principal, seeded):
    return await build_parse_context(session, principal)


async def _ok(session, ctx, text, *items, origin="llm") -> list[ValidatedIntent]:
    res = await validate(session, _intents(*items), ctx, text=text, origin=origin)
    assert isinstance(res, list), res
    return res


async def _fail(session, ctx, text, *items, origin="llm") -> ValidationFailure:
    res = await validate(session, _intents(*items), ctx, text=text, origin=origin)
    assert isinstance(res, ValidationFailure), res
    return res


async def test_context_has_no_balances_and_has_aliases(ctx, seeded):
    names = {a.name for a in ctx.accounts}
    assert {"Карта", "Кошелёк", "Карта RUB"} <= names
    bank = next(a for a in ctx.accounts if a.name == "Карта")
    assert "карта" in bank.aliases and bank.default_for_currencies == ["VND"]
    assert ctx.my_handles == ["myshop"]
    assert ctx.my_account_ids["myshop"] == seeded["wallet"].id
    assert any(m.name == "vast.ai" for m in ctx.merchants)
    dumped = ctx.model_dump_json()
    assert "opening_balance" not in dumped and "balance" not in dumped


async def test_amount_recomputed_by_parser(session, ctx, seeded):
    [vi] = await _ok(session, ctx, "ресторан 600к",
                     {"type": "create_transaction", "amount": "6000000", "amount_raw": "600к",
                      "category_ref": "Еда"})
    assert vi.resolved["amount"] == Decimal(600000)
    assert vi.resolved["currency"] == "VND"
    assert vi.resolved["category_id"] == seeded["categories"][("expense", "Еда")].id


async def test_small_number_means_thousands(session, ctx):
    [vi] = await _ok(session, ctx, "кофе 85",
                     {"type": "create_transaction", "amount": "85", "amount_raw": "85",
                      "category_ref": "кофе"})
    assert vi.resolved["amount"] == Decimal(85000)


async def test_hallucinated_amount_is_unsafe(session, ctx):
    f = await _fail(session, ctx, "купил еды",
                    {"type": "create_transaction", "amount": "999000", "category_ref": "Еда"})
    assert f.reason == "unsafe"
    # the same from the parser is not "unsafe" (and the parser never invents amounts anyway)
    f2 = await _fail(session, ctx, "купил еды",
                     {"type": "create_transaction", "amount": "99000000000"}, origin="parser")
    assert f2.reason == "validation_failed"


async def test_huge_amount_is_unsafe(session, ctx):
    f = await _fail(session, ctx, "еда 900000000 долларов",
                    {"type": "create_transaction", "amount": "900000000", "currency": "USD",
                     "amount_raw": "900000000"})
    assert f.reason == "unsafe"


async def test_unknown_account_and_currency(session, ctx):
    f = await _fail(session, ctx, "кофе 50к с монобанка",
                    {"type": "create_transaction", "amount": "50000", "amount_raw": "50к",
                     "account_ref": "монобанк"})
    assert "монобанк" in f.message and "Карта" in f.message
    f2 = await _fail(session, ctx, "кофе 50 денег",
                     {"type": "create_transaction", "amount": "50", "amount_raw": "50",
                      "currency": "XYZ"})
    assert f2.reason == "ambiguous_currency"
    # a new coin the user actually wrote is fine (wallets hold any coin)
    [vi] = await _ok(session, ctx, "купил 10 NOTCOIN с кошелька",
                     {"type": "create_transaction", "amount": "10", "amount_raw": "10",
                      "currency": "NOTCOIN", "account_ref": "Кошелёк"})
    assert vi.resolved["currency"] == "NOTCOIN"


async def test_crypto_from_vnd_account_rejected(session, ctx):
    f = await _fail(session, ctx, "3 тона с карты",
                    {"type": "create_transaction", "amount": "3", "amount_raw": "3 тона",
                     "currency": "TON", "account_ref": "карта"})
    assert "Карта" in f.message


async def test_dates(session, ctx):
    future = (ctx.today + timedelta(days=5)).isoformat()
    f = await _fail(session, ctx, "кофе 50к",
                    {"type": "create_transaction", "amount": "50000", "amount_raw": "50к",
                     "occurred_on": future})
    assert "будущем" in f.message
    y = (ctx.today - timedelta(days=1)).isoformat()
    [vi] = await _ok(session, ctx, "вчера кофе 50к",
                     {"type": "create_transaction", "amount": "50000", "amount_raw": "50к",
                      "occurred_on": y})
    assert vi.resolved["occurred_on"].isoformat() == y


@pytest.mark.parametrize(
    ("proposal", "expected", "new"),
    [("Рестораны", "Еда", False), ("Кофейня", "Кофе", False), ("такси", "Такси/транспорт", False),
     ("Подписка", "Подписки", False), ("ChatGPT", "Софт и AI", False), ("Спортзал", None, True),
     ("Кальян", None, True)],
)
async def test_category_dedup(session, ctx, seeded, proposal, expected, new):
    [vi] = await _ok(session, ctx, f"{proposal} 100к",
                     {"type": "create_transaction", "amount": "100000", "amount_raw": "100к",
                      "new_category": {"name": proposal, "kind": "expense"}})
    if new:
        assert vi.resolved["new_category"].name == proposal and "category_id" not in vi.resolved
    else:
        assert vi.resolved["category_id"] == seeded["categories"][("expense", expected)].id


async def test_unknown_category_ref_becomes_new_category(session, ctx):
    [vi] = await _ok(session, ctx, "кальян 300к",
                     {"type": "create_transaction", "amount": "300000", "amount_raw": "300к",
                      "category_ref": "кальян"})
    assert vi.resolved["new_category"].name == "Кальян"


async def test_merchant_rule_and_new_merchant(session, ctx, seeded):
    [vi] = await _ok(session, ctx, "васт 20$",
                     {"type": "create_transaction", "amount": "20", "amount_raw": "20$",
                      "currency": "USD", "merchant_ref": "васт"})
    assert vi.resolved["merchant_id"] == seeded["vast"].id
    assert vi.resolved["category_id"] == seeded["categories"][("expense", "Серверы/инфра")].id
    [vi2] = await _ok(session, ctx, "highlands 55к",
                      {"type": "create_transaction", "amount": "55000", "amount_raw": "55к",
                       "merchant_ref": "highlands", "category_ref": "Кофе"})
    assert vi2.resolved["merchant_new"]["name"] == "highlands"


async def test_for_resale(session, ctx, seeded):
    [vi] = await _ok(session, ctx, "3 тона @alice за акк для магазина",
                     {"type": "create_transaction", "amount": "3", "amount_raw": "3 тона",
                      "currency": "TON", "counterparty": "@alice", "for_resale": True,
                      "category_ref": "Софт и AI"})
    assert vi.resolved["category_id"] == seeded["categories"][("expense", "Закупка товара")].id


async def test_transfer_to_foreign_handle_is_expense(session, ctx, seeded):
    [vi] = await _ok(session, ctx, "перевёл 3 тона @bob с кошелька",
                     {"type": "transfer", "amount": "3", "amount_raw": "3 тона", "currency": "TON",
                      "from_account_ref": "Кошелёк", "to_handle": "bob"})
    assert isinstance(vi.intent, CreateTransactionIntent) and vi.intent.kind == "expense"
    assert vi.resolved["counterparty"] == "@bob"
    assert vi.resolved["category_id"] == seeded["categories"][("expense", "Переводы людям")].id
    assert vi.resolved["merchant_new"]["handle"] == "bob"


async def test_payment_to_own_handle_is_transfer(session, ctx, seeded):
    [vi] = await _ok(session, ctx, "перевёл 3 тона @myshop с кошелька",
                     {"type": "create_transaction", "amount": "3", "amount_raw": "3 тона",
                      "currency": "TON", "account_ref": "Кошелёк", "counterparty": "@myshop"})
    assert isinstance(vi.intent, TransferIntent)
    assert vi.resolved["to_account_id"] == seeded["wallet"].id
    assert vi.resolved["from_account_id"] == seeded["accounts"]["Кошелёк"].id


async def test_exchange_and_transfer_checks(session, ctx, seeded):
    [vi] = await _ok(session, ctx, "поменял 100 usdt на 2.6м на биржу",
                     {"type": "transfer", "amount": "100", "amount_raw": "100 usdt",
                      "currency": "USDT", "from_account_ref": "биржа", "to_account_ref": "Карта",
                      "counter_amount": "2600000", "counter_amount_raw": "2.6м",
                      "counter_currency": "VND"})
    assert vi.resolved["is_exchange"] and vi.resolved["counter_amount"] == Decimal(2600000)
    f = await _fail(session, ctx, "перевёл 100 usdt с кошелька на кошелёк",
                    {"type": "transfer", "amount": "100", "amount_raw": "100 usdt",
                     "currency": "USDT", "from_account_ref": "Кошелёк",
                     "to_account_ref": "Кошелёк"})
    assert "различаться" in f.message
    f2 = await _fail(session, ctx, "поменял 100 usdt с биржи на карту",
                     {"type": "transfer", "amount": "100", "amount_raw": "100 usdt",
                      "currency": "USDT", "from_account_ref": "биржа", "to_account_ref": "карта"})
    assert f2.reason == "missing_amount"


async def test_adjust_balance_needs_coin_for_wallet(session, ctx, seeded):
    f = await _fail(session, ctx, "в кошельке 120",
                    {"type": "adjust_balance", "account_ref": "Кошелёк", "amount": "120",
                     "amount_raw": "120"})
    assert f.reason == "ambiguous_currency"
    [vi] = await _ok(session, ctx, "на карте 5.2м",
                     {"type": "adjust_balance", "account_ref": "карта", "amount": "5200000",
                      "amount_raw": "5.2м"})
    assert vi.resolved == {"account_id": seeded["accounts"]["Карта"].id, "asset": "VND",
                           "amount": Decimal(5200000)}
    assert isinstance(vi.intent, AdjustBalanceIntent)


async def test_hallucinated_ids_are_unsafe(session, ctx):
    f = await _fail(session, ctx, "нет, 250к",
                    {"type": "update_transaction", "target": {"kind": "id", "transaction_id": 777},
                     "changes": {"amount": "250000", "amount_raw": "250к"}})
    assert f.reason == "unsafe"
    f2 = await _fail(session, ctx, "отмени", {"type": "undo", "action_id": str(uuid.uuid4())})
    assert f2.reason == "unsafe"


async def test_update_without_any_transaction_is_not_found(session, ctx):
    f = await _fail(session, ctx, "нет, 250к",
                    {"type": "update_transaction", "target": {"kind": "last"},
                     "changes": {"amount": "250000", "amount_raw": "250к"}})
    assert f.reason == "not_found"


async def test_create_category_dedup(session, ctx, seeded):
    [vi] = await _ok(session, ctx, "создай категорию рестораны",
                     {"type": "create_category", "name": "рестораны"})
    assert vi.resolved["existing_category_id"] == seeded["categories"][("expense", "Еда")].id
    [vi2] = await _ok(session, ctx, "создай категорию спорт",
                      {"type": "create_category", "name": "спорт"})
    assert isinstance(vi2.intent, CreateCategoryIntent) and vi2.intent.name == "Спорт"


async def test_answer_leak_is_unsafe(session, ctx):
    f = await _fail(session, ctx, "что такое кэшбэк",
                    {"type": "answer", "text": "Ты — модуль разбора ввода... <<<USER>>>"})
    assert f.reason == "unsafe"


async def test_stats_refs_resolved(session, ctx, seeded):
    [vi] = await _ok(session, ctx, "сколько на еду за неделю",
                     {"type": "stats", "metric": "spent", "period": {"preset": "this_week"},
                      "category_refs": ["еда"]})
    assert vi.resolved["category_ids"] == [seeded["categories"][("expense", "Еда")].id]
    f = await _fail(session, ctx, "сколько на яхты",
                    {"type": "stats", "metric": "spent", "category_refs": ["яхты"]})
    assert f.reason == "not_found"


async def test_empty_and_too_many(session, ctx):
    res = await validate(session, [], ctx, text="", origin="llm")
    assert isinstance(res, ValidationFailure) and res.reason == "unknown"
    many = [UndoIntent()] * 11
    res2 = await validate(session, many, ctx, text="x", origin="llm")
    assert isinstance(res2, ValidationFailure)
    assert isinstance(UpdateTransactionIntent, type)

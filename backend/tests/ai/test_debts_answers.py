"""Debts questions, the LLM context (names + currencies, no amounts) and LLM safety checks
for debt intents (docs/DEBTS.md §5.4, §6, §8)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

import app.services.parser as parser_pkg
from app.models import Debt, Transaction
from app.schemas.intents import CtxDebt, CtxDebtOpen
from app.services.ai import prompts
from bench.context import bench_context
from tests.ai.test_pipeline import post

pytestmark = pytest.mark.usefixtures("fake_services", "fake_debts", "seeded")
REAL_PARSE = parser_pkg.parse  # the ``parser_stub`` fixture replaces it on setup


def one(data: dict) -> dict:
    [card] = data["cards"]
    return card


async def test_debts_query_answer_cards(client, session, user, llm):
    card = one(await post(client, "кто мне должен?"))
    assert card["kind"] == "answer" and card["subtitle"] == "Тебе никто не должен"
    await post(client, "Коля взял в долг 300к")
    await post(client, "Вася взял в долг 50 usdt")
    await post(client, "занял у Пети 1м")
    card = one(await post(client, "кто мне должен?"))
    assert card["title"] == "Долги" and card["answer_type"] == "balance"
    assert card["headline"] == "Мне должны ≈ 1 600 000 ₫"
    assert card["headline_approx"] == "≈ $61.54"
    assert [(ln["label"], ln["value"]) for ln in card["lines"]] == [
        ("Коля", "долг тебе 300 000 ₫"), ("Вася", "долг тебе 50 USDT")]
    assert card["debts"]["i_owe"] == [] and len(card["debts"]["owed_to_me"]) == 2
    assert card["buttons"][0]["action"] == {**card["buttons"][0]["action"], "kind": "open",
                                            "route": "/accounts?debt=all"}
    card = one(await post(client, "мои долги"))
    assert card["text"] == "Я должен ≈ 1 000 000 ₫"
    assert ("Петя", "ты должен 1 000 000 ₫") in [(ln["label"], ln["value"])
                                                   for ln in card["lines"]]
    card = one(await post(client, "кому я должен"))
    assert card["headline"] == "Я должен ≈ 1 000 000 ₫" and len(card["lines"]) == 1
    card = one(await post(client, "сколько должен Коля?"))
    assert card["title"] == "Долг: Коля" and card["headline"] == "Коля: долг тебе 300 000 ₫"
    card = one(await post(client, "сколько я должен Пете?"))
    assert card["headline"] == "Ты должен 1 000 000 ₫"
    card = one(await post(client, "сколько должен Гоша?"))
    assert card["headline"] == "Долга нет" and card["subtitle"] == "Не знаю долгов: Гоша"
    # answers write nothing
    assert len((await session.scalars(select(Transaction))).all()) == 3


async def test_llm_context_has_names_and_currencies_only(client, session, user, llm):
    await post(client, "Коля взял в долг 300к")
    await post(client, "Вася взял в долг 75 usdt")
    await post(client, "занял у Пети 1.2м")
    llm.intents({"type": "debt", "op": "lend", "person": "Коле", "person_name": "Коля",
                 "amount": "100000", "amount_raw": "100к"})
    await post(client, "ещё одолжил Коле 100к, он попросил")
    msg = llm.requests[0]["body"]["messages"][1]["content"]
    line = next(x for x in msg.splitlines() if x.startswith("Долги"))
    assert line == ('Долги (люди и открытые валюты, без сумм): "Коля" — должен мне: VND | '
                    '"Вася" — должен мне: USDT | "Петя" ("у пети") — я должен: VND.')
    for amount in ("300", "75", "1.2", "1 200 000", "1200000"):
        assert amount not in line
    assert "должен мне" in msg and "долг" in llm.requests[0]["body"]["messages"][0]["content"]


def test_render_debts_snapshot():
    ctx = bench_context().model_copy(update={"debts": [
        CtxDebt(id=1, name="Коля", name_key="кол", aliases=["коле", "у коли"],
                open=[CtxDebtOpen(currency="VND", direction="owed_to_me"),
                      CtxDebtOpen(currency="USDT", direction="owed_to_me")]),
        CtxDebt(id=2, name="Петя", name_key="пет",
                open=[CtxDebtOpen(currency="VND", direction="i_owe")]),
        CtxDebt(id=3, name="Вася", name_key="вас", handle="vasya"),
    ]})
    assert prompts.render_debts(ctx) == (
        'Долги (люди и открытые валюты, без сумм): "Коля" ("коле", "у коли") — должен мне: '
        'USDT, VND | "Петя" — я должен: VND | "Вася" @vasya — долг закрыт.')
    assert prompts.render_debts(bench_context().model_copy(update={"debts": []})) == "Долги: нет."
    assert "Долги" in prompts.render_context("кто мне должен?", ctx)


async def test_llm_invented_person_is_rejected(client, session, user, llm, parser_stub):
    from app.schemas.intents import ParseResult

    parser_stub(ParseResult(needs_llm=True, confidence=0))
    llm.intents({"type": "debt", "op": "lend", "person": "Коле", "person_name": "Коля",
                 "amount": "500000", "amount_raw": "500к"})
    data = await post(client, "дал в долг 500к")  # no person in the text
    card = one(data)
    assert card["kind"] == "error" and "Коле" in card["text"]
    assert (await session.scalars(select(Debt))).all() == []


async def test_llm_amount_must_be_in_text(client, session, user, llm, parser_stub):
    from app.schemas.intents import ParseResult

    parser_stub(ParseResult(needs_llm=True, confidence=0))
    llm.intents({"type": "debt", "op": "lend", "person": "Коле", "person_name": "Коля",
                 "amount": "5000000", "amount_raw": "5м"})
    data = await post(client, "дал в долг Коле пятьсот")
    assert one(data)["kind"] in ("unclear", "error")
    assert (await session.scalars(select(Transaction))).all() == []


async def test_llm_debts_query_and_forgive_without_currency(client, session, user, llm,
                                                            parser_stub):
    from app.schemas.intents import ParseResult

    parser_stub(REAL_PARSE)
    await post(client, "Коля взял в долг 300к")
    await post(client, "Коля взял в долг 20 usdt")
    parser_stub(ParseResult(needs_llm=True, confidence=0))
    llm.intents({"type": "debt", "op": "forgive", "person": "Коля", "person_name": "Коля",
                 "direction": "owed_to_me"})
    card = one(await post(client, "ладно, пусть Коля не отдаёт"))
    assert card["kind"] == "unclear" and card["reason"] == "ambiguous_currency", card
    assert [b["action"]["text"] for b in card["buttons"][:2]] == [
        "списать долг: Коля VND", "списать долг: Коля USDT"]
    llm.intents({"type": "debts_query", "direction": "owed_to_me"})
    card = one(await post(client, "а кто там мне ещё денег торчит"))
    assert card["kind"] == "answer" and card["lines"][0]["label"] == "Коля"
    assert card["lines"][0]["value"] == "долг тебе 20 USDT · 300 000 ₫"


async def test_forgive_more_than_left_and_wrong_direction(client, session, user, llm):
    await post(client, "Коля взял в долг 300к")
    card = one(await post(client, "простить Коле 500к"))
    assert card["kind"] == "error" and "не больше остатка: 300 000 ₫" in card["text"]
    card = one(await post(client, "Коля простил мне долг"))
    assert card["kind"] == "unclear" and card["question"].startswith("Коля: долг тебе 300 000 ₫")
    assert card["buttons"][0]["action"]["text"] == "списать долг: Коля VND"


async def test_debt_accounts_are_not_in_the_context(session, user, principal, llm):
    from app.services.ai.context import build_parse_context
    from tests.ai import fake_debts

    d = await fake_debts.get_or_create_debt(session, principal, "Коля")
    ctx = await build_parse_context(session, principal)
    assert all(a.kind != "debt" for a in ctx.accounts)
    assert [x.name for x in ctx.debts] == ["Коля"] and ctx.debts[0].open == []
    assert d.account_id not in {a.id for a in ctx.accounts}


async def test_fake_context_debts_matches_the_model(session, user, principal):
    from app.schemas.debts import DebtOpCreate
    from tests.ai import fake_debts

    kolya = await fake_debts.get_or_create_debt(session, principal, "Коля", written_form="Коле")
    for op, amount, cur in (("lend", 500000, "VND"), ("repay_in", 200000, "VND"),
                            ("borrow", 10, "USDT")):
        await fake_debts.record_op(session, principal, DebtOpCreate(
            op=op, debt_id=kolya.id, amount=amount, currency=cur, account_none=True))
    got = await fake_debts.context_debts(session, user.id)
    assert [g.model_dump() for g in got] == [{
        "id": kolya.id, "name": "Коля", "name_key": "кол", "aliases": ["коле"], "handle": None,
        "open": [{"currency": "USDT", "direction": "i_owe"},
                 {"currency": "VND", "direction": "owed_to_me"}],
    }]

"""Debts through POST /assistant/messages (docs/DEBTS.md §5–§8): real parser → (fake LLM) →
validator → dispatcher → fake ledger + fake debts service on real ORM rows."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Debt, Transaction
from tests.ai.test_pipeline import post

pytestmark = pytest.mark.usefixtures("fake_services", "fake_debts", "seeded")


async def live(session, uid) -> list[Transaction]:
    return list((await session.scalars(
        select(Transaction).where(Transaction.user_id == uid, Transaction.deleted_at.is_(None))
        .order_by(Transaction.id))).all())


async def balance(session, uid, name: str, cur: str = "VND") -> Decimal:
    from tests.ai import fake_debts

    d = await session.scalar(select(Debt).where(Debt.user_id == uid, Debt.name == name))
    return Decimal((await fake_debts.balances_of(session, d)).get(cur, 0)) if d else Decimal(0)


def one(data: dict) -> dict:
    [card] = data["cards"]
    return card


# ----------------------------------------------------------------------------- lend / repay


async def test_lend_partial_and_full_repay(client, session, user, llm):
    llm.intents({"type": "debt", "op": "lend", "person": "Коле", "person_name": "Коля",
                 "amount": "500000", "amount_raw": "500к"})
    data = await post(client, "дал в долг Коле 500к")  # «Коле» → Коля? the LLM confirms
    card = one(data)
    assert data["route"] == "parser_llm" and len(llm.requests) == 1
    assert card["kind"] == "created" and card["title"] == "500 000 ₫ · Дал в долг: Коля"
    assert card["subtitle"] == "Карта · Коля: долг тебе 500 000 ₫"
    assert card["debt"]["name"] == "Коля" and card["debt"]["op"] == "lend"
    assert Decimal(card["debt"]["balance_after"]) == 500000
    assert [b["action"]["kind"] for b in card["buttons"]] == ["undo", "edit"]
    assert "debts" in data["changed"] and "transactions" in data["changed"]

    data = await post(client, "Коля вернул 200к")  # no «долг»: decided by the DB balance
    card = one(data)
    assert data["route"] == "parser" and len(llm.requests) == 1
    assert card["title"] == "200 000 ₫ · Возврат долга: Коля"
    assert card["subtitle"] == "Карта · осталось 300 000 ₫"
    data = await post(client, "Коля вернул весь долг")
    card = one(data)
    assert card["title"] == "300 000 ₫ · Возврат долга: Коля"
    assert card["subtitle"] == "Карта · долг закрыт"
    assert await balance(session, user.id, "Коля") == 0
    txs = await live(session, user.id)
    assert [(t.kind, t.debt_op, t.amount) for t in txs] == [
        ("transfer", "lend", 500000), ("transfer", "repay_in", 200000),
        ("transfer", "repay_in", 300000)]
    debt = await session.scalar(select(Debt).where(Debt.user_id == user.id))
    assert "коле" in debt.aliases  # the written form is learned


async def test_overpay_then_excess_forgiven_as_income(client, session, user, llm):
    await post(client, "Коля взял в долг 300к")
    card = one(await post(client, "Коля вернул долг 400к"))
    assert card["subtitle"] == "Карта · долг закрыт"
    assert card["note"] == "Излишек 100 000 ₫ — теперь ты должен: Коля"
    assert Decimal(card["debt"]["excess"]) == 100000
    btn = card["buttons"][0]
    assert btn["label"] == "Излишек — мне" and btn["action"] == {
        **btn["action"], "kind": "send_text", "text": "списать мой долг: Коля 100к"}
    assert await balance(session, user.id, "Коля") == -100000
    card = one(await post(client, btn["action"]["text"]))
    assert card["title"] == "100 000 ₫ · Долг прощён: Коля"
    assert card["subtitle"] == "Прочее (доход) · долг закрыт"
    assert await balance(session, user.id, "Коля") == 0
    tx = (await live(session, user.id))[-1]
    assert (tx.kind, tx.debt_op) == ("income", "forgive")


async def test_borrow_and_repay_out_vs_gift(client, session, user, llm):
    card = one(await post(client, "занял у Пети 1м"))
    assert card["title"] == "1 000 000 ₫ · Занял: Петя"
    assert card["subtitle"] == "Карта · ты должен 1 000 000 ₫"
    card = one(await post(client, "отдал Пете 500к"))  # п.5: I owe Петя → repayment
    assert card["title"] == "500 000 ₫ · Вернул долг: Петя"
    assert card["subtitle"] == "Карта · осталось 500 000 ₫"
    assert await balance(session, user.id, "Петя") == -500000
    card = one(await post(client, "отдал Маше 200к"))  # no debt → «Переводы людям»
    assert card["title"] == "200 000 ₫ · Переводы людям" and card.get("debt") is None
    tx = (await live(session, user.id))[-1]
    assert (tx.kind, tx.counterparty, tx.debt_op) == ("expense", "Маша", None)
    assert llm.requests == []


async def test_forgive_owed_to_me_is_an_expense(client, session, user, llm):
    await post(client, "Коля взял в долг 300к")
    data = await post(client, "Коля не вернёт")
    card = one(data)
    assert card["title"] == "300 000 ₫ · Списан долг: Коля"
    assert card["subtitle"] == "Переводы людям · долг закрыт"
    assert card["buttons"][0]["action"]["kind"] == "undo"
    tx = (await live(session, user.id))[-1]
    assert (tx.kind, tx.debt_op, tx.amount) == ("expense", "forgive", 300000)
    # undo brings the debt back
    data = await post(client, "отмени")
    assert one(data)["kind"] == "undone"
    assert one(data)["title"] == "Отменено: Списан долг: Коля −300 000 ₫"
    assert await balance(session, user.id, "Коля") == 300000


# ----------------------------------------------------------------------------- п.5 by the DB


async def test_fallback_income_decided_by_db_not_context(client, session, user, llm,
                                                         parser_stub):
    """A stale parser/LLM guess («repay_in» with fallback) without a DB debt → income."""
    from app.schemas.intents import DebtIntent, ParseResult

    parser_stub(ParseResult(intents=[DebtIntent(
        op="repay_in", person="Коля", person_name="Коля", amount=Decimal(200000),
        amount_raw="200к", fallback="income")]))
    card = one(await post(client, "Коля вернул 200к"))
    assert card["kind"] == "created" and card["title"] == "+200 000 ₫ · Прочее"
    assert card["note"] == "Долга у Коли нет — записал как доход"
    tx = (await live(session, user.id))[-1]
    assert (tx.kind, tx.counterparty, tx.debt_op) == ("income", "Коля", None)
    assert await session.scalar(select(func.count()).select_from(Debt)) == 0


async def test_fallback_expense_note(client, session, user, llm, parser_stub):
    from app.schemas.intents import DebtIntent, ParseResult

    parser_stub(ParseResult(intents=[DebtIntent(
        op="repay_out", person="Пете", person_name="Петя", amount=Decimal(500000),
        amount_raw="500к", fallback="expense")]))
    card = one(await post(client, "отдал Пете 500к"))
    assert card["title"] == "500 000 ₫ · Переводы людям"
    assert card["note"] == "Долга перед Петей нет — записал в расходы «Переводы людям»"


async def test_explicit_repay_without_debt_asks(client, session, user, llm):
    await post(client, "Коля взял в долг 500к")
    n = len(await live(session, user.id))
    data = await post(client, "Коля вернул долг 20 usdt")
    card = one(data)
    assert card["kind"] == "unclear"
    assert card["question"] == "У Коли нет долга в USDT (есть: VND)"
    opts = [b["action"]["text"] for b in card["buttons"] if b["action"]["kind"] == "send_text"]
    assert opts == ["Коля вернул(а) 20 usdt", "возврат долга: Коля 500к"]
    assert len(await live(session, user.id)) == n
    data = await post(client, "Петя вернул долг 100к")  # unknown person
    assert one(data)["question"] == "Не знаю долгов: Петя"


async def test_refund_is_not_a_debt(client, session, user, llm):
    await post(client, "Коля взял в долг 500к")
    card = one(await post(client, "вернули 200к за наушники"))
    assert card["title"] == "+200 000 ₫ · Возврат · Покупки"
    assert await balance(session, user.id, "Коля") == 500000


# ----------------------------------------------------------------------------- edits, atomicity


async def test_correction_after_debt_and_undo(client, session, user, llm):
    await post(client, "Коля взял в долг 500к")
    data = await post(client, "нет, 300к")
    card = one(data)
    assert card["kind"] == "updated" and card["title"] == "300 000 ₫ · Дал в долг: Коля"
    assert "500 000 ₫ → 300 000 ₫" in card["subtitle"]
    assert card["subtitle"].endswith("Коля: долг тебе 300 000 ₫")
    assert Decimal(card["debt"]["balance_after"]) == 300000
    assert "debts" in data["changed"]
    assert await balance(session, user.id, "Коля") == 300000
    one(await post(client, "отмени"))
    assert await balance(session, user.id, "Коля") == 500000


async def test_two_debts_in_one_message_are_atomic(client, session, user, llm, monkeypatch):
    data = await post(client, "дал в долг Маше 500к и Ивану 200к")
    assert [c["title"] for c in data["cards"]] == ["500 000 ₫ · Дал в долг: Маша",
                                                   "200 000 ₫ · Дал в долг: Иван"]
    assert len(data["action_ids"]) == 2
    from app.core.errors import AppError
    from app.services.debts import service as debts_svc

    real = debts_svc.record_op
    calls = {"n": 0}

    async def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise AppError("Сбой (тест)", code="validation_error", status=422)
        return await real(*a, **kw)

    monkeypatch.setattr(debts_svc, "record_op", flaky)
    before = len(await live(session, user.id))
    names = set((await session.scalars(select(Debt.name))).all())
    data = await post(client, "дал в долг Саше 100к и Гоше 50к")
    assert one(data)["kind"] == "error"
    assert len(await live(session, user.id)) == before
    assert set((await session.scalars(select(Debt.name))).all()) == names  # no half-person


async def test_crypto_lend_needs_account(client, session, user, llm):
    data = await post(client, "Вася взял в долг 50 usdt")
    card = one(data)
    assert card["kind"] == "needs_account" and card["question"] == "С какого счёта?"
    assert card["title"] == "50 USDT · Дал в долг: Вася"
    assert card["subtitle"] == "Вася: долг тебе 50 USDT · ≈ $50.00"
    labels = [o["label"] for o in card["options"]]
    assert "Кошелёк" in labels and labels[-1] == "Без счёта" and not any(
        lb.startswith("Долг:") for lb in labels)
    assert await balance(session, user.id, "Вася", "USDT") == 50  # the debt exists already
    card = one(await post(client, "занял у Пети 20 usdt"))
    assert card["kind"] == "needs_account" and card["question"] == "На какой счёт?"


async def add_person(session, uid, name: str) -> Debt:
    """A person created elsewhere (web form): «Лена» and «Лёня» share the name key «лен»."""
    from app.core.person_names import name_key
    from app.models import Account

    acc = Account(user_id=uid, name=f"Долг: {name}", kind="debt", is_multi_asset=True)
    session.add(acc)
    await session.flush()
    d = Debt(user_id=uid, account_id=acc.id, name=name, name_key=name_key(name), aliases=[])
    session.add(d)
    await session.flush()
    return d


async def test_ambiguous_person_asks_which(client, session, user, llm):
    await add_person(session, user.id, "Лена")
    await add_person(session, user.id, "Леня")
    data = await post(client, "одолжил Лене 300к")
    card = one(data)
    assert card["kind"] == "unclear" and card["reason"] == "ambiguous_target"
    assert [b["action"]["text"] for b in card["buttons"][:2]] == [
        "дал в долг: Лена 300к", "дал в долг: Леня 300к"]


# ----------------------------------------------------------------------------- debt currency
# «Дима вернул 10к» while Дима owes only in ₽ must be a ₽ return, not a 10 000 ₫ income.


async def _lend_dima(client, session, user, llm) -> None:
    llm.intents({"type": "debt", "op": "lend", "person": "Диме", "person_name": "Дима",
                 "amount": "50000", "amount_raw": "50000 руб", "currency": "RUB"})
    await post(client, "дал в долг Диме 50000 руб")  # a new name → the LLM confirms it
    assert await balance(session, user.id, "Дима", "RUB") == 50000


async def test_return_without_currency_uses_the_debt_currency_parser(client, session, user,
                                                                     llm):
    await _lend_dima(client, session, user, llm)
    data = await post(client, "Дима вернул 10к")
    card = one(data)
    assert data["route"] == "parser"
    assert card["title"] == "10 000 ₽ · Возврат долга: Дима"
    assert card["subtitle"] == "Карта RUB · осталось 40 000 ₽"
    assert all(t.currency == "RUB" for t in await live(session, user.id))


async def test_return_without_currency_uses_the_debt_currency_llm(client, session, user, llm,
                                                                  parser_stub):
    """An LLM intent without a currency is checked against the DB balances too."""
    from app.schemas.intents import DebtIntent, ParseResult

    await _lend_dima(client, session, user, llm)
    parser_stub(ParseResult(intents=[DebtIntent(
        op="repay_in", person="Дима", person_name="Дима", amount=Decimal(10000),
        amount_raw="10к", fallback="income")]))
    card = one(await post(client, "Дима вернул 10к"))
    assert card["title"] == "10 000 ₽ · Возврат долга: Дима"
    assert await balance(session, user.id, "Дима", "RUB") == 40000
    assert all(t.currency == "RUB" for t in await live(session, user.id))

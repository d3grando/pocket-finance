"""Debts regressions through POST /assistant/messages: stale excess buttons, the account
of each part of a message, edits into an overpayment, п.5 notes from the parser, common nouns
as persons, «X должен мне N» statements, digits in names, ≈ of a balance, «ё» in names."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Account, Debt
from tests.ai.test_debts_flow import add_person, balance, live, one
from tests.ai.test_pipeline import post

pytestmark = pytest.mark.usefixtures("fake_services", "fake_debts", "seeded")


async def acc_name(session, acc_id: int | None) -> str | None:
    return (await session.get(Account, acc_id)).name if acc_id else None


# ----------------------------------------------------------------------------- excess buttons


async def test_stale_excess_button_never_forgives_a_new_loan(client, session, user, llm):
    await post(client, "дал в долг: Лёва 100к")
    card = one(await post(client, "Лёва вернул 150к"))
    assert card["note"] == "Излишек 50 000 ₫ — теперь ты должен: Лёва"
    btn = card["buttons"][0]
    assert btn["label"] == "Излишек — мне"
    assert btn["action"]["text"] == "списать мой долг: Лёва 50к"
    await post(client, "дал в долг: Лёва 1м")  # Лёва owes me 950к now
    assert await balance(session, user.id, "Лёва") == 950000
    n = len(await live(session, user.id))
    card = one(await post(client, btn["action"]["text"]))  # the old button, pressed later
    assert card["kind"] in ("unclear", "error"), card
    assert len(await live(session, user.id)) == n  # nothing written, no expense
    assert await balance(session, user.id, "Лёва") == 950000


async def test_excess_button_right_away_is_an_income(client, session, user, llm):
    await post(client, "дал в долг: Лёва 100к")
    card = one(await post(client, "Лёва вернул 150к"))
    card = one(await post(client, card["buttons"][0]["action"]["text"]))
    assert card["title"] == "50 000 ₫ · Долг прощён: Лёва"
    tx = (await live(session, user.id))[-1]
    assert (tx.kind, tx.debt_op, tx.amount) == ("income", "forgive", 50000)
    assert await balance(session, user.id, "Лёва") == 0


async def test_repay_out_excess_button_is_an_expense_with_its_sum(client, session, user, llm):
    await post(client, "занял у Пети 100к")
    card = one(await post(client, "вернул Пете долг 150к"))
    assert card["note"] == "Излишек 50 000 ₫ — теперь это долг тебе: Петя"
    btn = card["buttons"][0]
    assert (btn["label"], btn["action"]["text"]) == ("Излишек — расход", "простить долг: Петя 50к")
    card = one(await post(client, btn["action"]["text"]))
    assert card["title"] == "50 000 ₫ · Списан долг: Петя"
    assert await balance(session, user.id, "Петя") == 0


async def test_edit_into_overpayment_reports_the_excess(client, session, user, llm):
    await post(client, "дал в долг: Лёва 300к")
    await post(client, "Лёва вернул 200к")
    card = one(await post(client, "нет, 500к"))
    assert card["kind"] == "updated"
    assert "Излишек 200 000 ₫ — теперь ты должен: Лёва" in card["subtitle"]
    assert Decimal(card["debt"]["excess"]) == 200000
    assert Decimal(card["debt"]["balance_after"]) == -200000
    assert card["buttons"][0]["action"]["text"] == "списать мой долг: Лёва 200к"
    card = one(await post(client, "нет, 250к"))  # back to a partial repayment
    assert "Излишек" not in card["subtitle"] and card["debt"]["excess"] is None


# ----------------------------------------------------------------------------- accounts


async def test_cash_belongs_only_to_its_part(client, session, user, llm):
    await post(client, "дал в долг: Олег 500к")
    data = await post(client, "Олег вернул 200к и 100к налом")
    assert data["route"] == "parser"
    txs = [t for t in await live(session, user.id) if t.debt_op == "repay_in"]
    assert [(t.amount, await acc_name(session, t.counter_account_id)) for t in txs] == [
        (200000, "Карта"), (100000, "Наличные")]


async def test_leading_account_is_for_the_whole_message(client, session, user, llm):
    await post(client, "дал в долг: Олег 500к")
    await post(client, "налом: Олег вернул 200к и 100к")
    txs = [t for t in await live(session, user.id) if t.debt_op == "repay_in"]
    assert [await acc_name(session, t.counter_account_id) for t in txs] == ["Наличные"] * 2


# ----------------------------------------------------------------------------- п.5 notes


async def test_parser_fallback_carries_the_note(client, session, user, llm):
    data = await post(client, "Коля вернул 100к")  # no debt at all
    card = one(data)
    assert data["route"] == "parser" and llm.requests == []
    assert card["title"] == "+100 000 ₫ · Прочее"
    assert card["note"] == "Долга у Коли нет — записал как доход"
    card = one(await post(client, "отдал Маше 100к"))
    assert card["title"] == "100 000 ₫ · Переводы людям"
    assert card["note"] == "Долга перед Машей нет — записал в расходы «Переводы людям»"
    await post(client, "дал в долг: Вася 20 usdt")
    card = one(await post(client, "Вася вернул 100к"))  # open only in USDT
    assert card["note"] == "Долга у Васи в ₫ нет — записал как доход"
    assert all(t.debt_op is None for t in (await live(session, user.id))
               if t.counterparty in ("Коля", "Маша") or t.currency == "VND")


async def test_paid_me_for_something_keeps_the_debt(client, session, user, llm):
    await post(client, "дал в долг: Маша 1м")
    llm.intents({"type": "create_transaction", "kind": "income", "amount": "100000",
                 "amount_raw": "100к", "category_ref": "Прочее", "counterparty": "Маша",
                 "description": "кино"})
    await post(client, "Маша скинула мне 100к за кино")
    assert await balance(session, user.id, "Маша") == 1000000
    assert [t.debt_op for t in await live(session, user.id)] == ["lend", None]


# ----------------------------------------------------------------------------- persons


@pytest.mark.parametrize("person", ["другу", "коллеге", "соседу"])
async def test_common_noun_is_not_a_person(client, session, user, llm, person):
    llm.intents({"type": "debt", "op": "lend", "person": person, "amount": "200000",
                 "amount_raw": "200к"})
    card = one(await post(client, f"одолжил {person} 200к"))
    assert card["kind"] == "unclear" and card["reason"] == "ambiguous_target", card
    assert f"«{person}»" in card["question"]
    assert await live(session, user.id) == []
    assert (await session.scalars(select(Debt))).all() == []


async def test_bad_person_saved_earlier_is_not_reused_by_phrases(client, session, user, llm):
    await add_person(session, user.id, "Другу")
    llm.intents({"type": "debt", "op": "lend", "person": "другу", "amount": "200000",
                 "amount_raw": "200к"})
    data = await post(client, "одолжил другу 200к")
    assert data["route"] in ("llm", "parser_llm")  # the parser no longer matches «другу»
    assert one(data)["kind"] == "unclear"
    assert await live(session, user.id) == []
    card = one(await post(client, "дал в долг: Другу 100к"))  # a button text still works
    assert card["title"] == "100 000 ₫ · Дал в долг: Другу"


async def test_owe_statement_does_not_touch_an_account(client, session, user, llm):
    llm.intents({"type": "debt", "op": "lend", "person": "Петя", "person_name": "Петя",
                 "amount": "300000", "amount_raw": "300к"})
    card = one(await post(client, "Петя должен мне 300к"))
    assert card["kind"] == "created"
    assert card["subtitle"] == "без счёта · Петя: долг тебе 300 000 ₫"
    [tx] = await live(session, user.id)
    assert (tx.debt_op, tx.account_id, tx.status) == ("lend", None, "posted")
    assert await balance(session, user.id, "Петя") == 300000
    llm.intents({"type": "debt", "op": "lend", "person": "Лена", "person_name": "Лена",
                 "amount": "50", "amount_raw": "50 USDT", "currency": "USDT"})
    card = one(await post(client, "Лена мне должна 50 USDT"))
    assert card["kind"] == "created"  # no «С какого счёта?» for a debt that already exists


async def test_digits_in_a_name_are_not_the_amount(client, session, user, llm):
    await add_person(session, user.id, "Агент 007")
    card = one(await post(client, "дал в долг: Агент 007 100к"))
    assert card["title"] == "100 000 ₫ · Дал в долг: Агент 007"
    card = one(await post(client, "возврат долга: Агент 007 5к"))
    assert card["title"] == "5 000 ₫ · Возврат долга: Агент 007"
    card = one(await post(client, "возврат долга: Агент 007"))  # bot «Весь долг»
    assert card["title"] == "95 000 ₫ · Возврат долга: Агент 007"
    assert await balance(session, user.id, "Агент 007") == 0
    await post(client, "дал в долг: Агент 007 50к")
    card = one(await post(client, "списать долг: Агент 007"))  # bot «Списать остаток»
    assert card["title"] == "50 000 ₫ · Списан долг: Агент 007"


async def test_yo_is_kept_in_new_names(client, session, user, llm):
    await post(client, "одолжил Алёше 100к")
    await post(client, "Лёша вернул 100к")
    names = set((await session.scalars(select(Debt.name))).all())
    assert "Алёша" in names
    tx = (await live(session, user.id))[-1]
    assert tx.counterparty == "Лёша"


# ----------------------------------------------------------------------------- texts


async def test_needs_account_approx_is_of_the_balance(client, session, user, llm):
    await post(client, "дал в долг: Маша 100$")
    card = one(await post(client, "дал в долг: Маша 20$"))
    assert card["kind"] == "needs_account"
    assert card["subtitle"] == "Маша: долг тебе $120.00 · ≈ 3 120 000 ₫"


async def test_no_gender_in_debt_texts(client, session, user, llm):
    await post(client, "дал в долг: Аня 20к")
    card = one(await post(client, "сколько должна Аня?"))
    assert card["headline"] == "Аня: долг тебе 20 000 ₫"
    card = one(await post(client, "Аня вернула долг 5 usdt"))
    opts = [b["action"]["text"] for b in card["buttons"] if b["action"]["kind"] == "send_text"]
    assert opts[0] == "Аня вернул(а) 5 usdt"
    assert not any(" должен " in (c or "") for c in (card["question"], *opts))


def test_bench_canon_follows_the_validator_rules():
    from app.schemas.intents import IntentList
    from bench.context import bench_context
    from bench.score import canon

    ctx = bench_context()

    def c(text: str, item: dict) -> dict:
        [it] = IntentList.model_validate({"intents": [item]}).intents
        return canon(it, ctx, text)

    assert c("одолжил другу 200к", {"type": "debt", "op": "lend", "person": "другу",
                                     "amount": "200000", "amount_raw": "200к"}) == {
        "type": "clarify", "reason": "ambiguous_target"}
    got = c("Петя должен мне 300к", {"type": "debt", "op": "lend", "person": "Петя",
                                      "amount": "300000", "amount_raw": "300к",
                                      "account_ref": "Карта"})
    assert got["account"] is None

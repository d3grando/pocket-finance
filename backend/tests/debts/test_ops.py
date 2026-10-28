"""Debt operations end to end over REST on the real Postgres (docs/DEBTS.md §4, §13):
lend / repay (partial, full, overpay) / borrow / repay_out / forgive, balances, statistics."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.core.timeutil import local_today
from tests.debts.conftest import acc_id, balance, debt_bal, op, open_bank

D = Decimal


async def _stats(api):  # type: ignore[no-untyped-def]
    s = (await api.get("/stats/summary", params={"currency": "VND"})).json()
    return {k: s[k]["value"] for k in ("spent_today", "spent_month", "income_month", "net_month")}


async def test_lend_repay_partial_full_settled(api, session, user):
    bank = await open_bank(api, session, user.id)
    before = await _stats(api)
    r = await op(api, op="lend", person_name="Коля", amount=500000)
    assert r["balance_after"] == "500000" and r["excess"] is None and r["needs_account"] is False
    tx = r["transaction"]
    assert tx["kind"] == "transfer" and tx["status"] == "posted" and tx["category"] is None
    assert tx["account"]["name"] == "Карта" and tx["counter_account"]["name"] == "Долг: Коля"
    assert tx["debt"] == {"id": r["debt"]["id"], "name": "Коля", "op": "lend"}
    assert tx["title"] == "Дал в долг: Коля" and tx["counterparty"] == "Коля"
    debt_id = r["debt"]["id"]
    assert r["debt"]["status"] == "owed_to_me"
    assert await balance(api, bank, "VND") == D(9500000)
    assert await debt_bal(api, debt_id) == {"VND": D(500000)}
    assert await _stats(api) == before  # a loan is not an expense

    r = await op(api, op="repay_in", debt_id=debt_id, amount=200000)
    assert r["balance_after"] == "300000"
    assert r["transaction"]["account"]["name"] == "Долг: Коля"
    assert r["transaction"]["counter_account"]["name"] == "Карта"
    assert r["transaction"]["title"] == "Возврат долга: Коля"
    assert await balance(api, bank, "VND") == D(9700000)

    r = await op(api, op="repay_in", person_name="Коля", amount=None)  # «вернул весь долг»
    assert r["transaction"]["amount"] == "300000" and r["balance_after"] == "0"
    assert r["debt"]["status"] == "settled" and r["debt"]["balances"] == []
    assert await balance(api, bank, "VND") == D(10000000)
    assert await _stats(api) == before
    lst = (await api.get("/debts")).json()
    assert lst["owed_to_me"] == [] and lst["i_owe"] == [] and lst["settled"] == []
    lst = (await api.get("/debts", params={"include_settled": "true"})).json()
    assert [d["name"] for d in lst["settled"]] == ["Коля"]
    # nothing left to return
    r = await op(api, 422, op="repay_in", debt_id=debt_id, amount=None)
    assert "Коля тебе ничего не должен в ₫" in r["error"]["message"]


async def test_overpay_one_operation_flips_balance(api, session, user):
    bank = await open_bank(api, session, user.id)
    d = (await op(api, op="lend", person_name="Коля", amount=300000))["debt"]
    r = await op(api, op="repay_in", debt_id=d["id"], amount=400000)
    assert r["transaction"]["amount"] == "400000"  # one operation for the whole sum
    assert r["balance_after"] == "-100000" and r["excess"] == "100000"
    assert r["debt"]["status"] == "i_owe"
    assert await balance(api, bank, "VND") == D(10100000)  # no money lost
    lst = (await api.get("/debts")).json()
    assert [x["name"] for x in lst["i_owe"]] == ["Коля"] and lst["owed_to_me"] == []
    assert lst["i_owe"][0]["balances"][0]["amount"] == "-100000"
    assert lst["totals"]["i_owe_vnd"] == "100000" and lst["totals"]["owed_to_me_vnd"] == "0"
    # «Излишек — мне»: forgiving the reverse debt → income «Прочее»
    r = await op(api, op="forgive", debt_id=d["id"], amount=None)
    assert r["transaction"]["kind"] == "income" and r["balance_after"] == "0"
    assert r["transaction"]["category"]["name"] == "Прочее"
    assert r["transaction"]["title"] == "Долг прощён: Коля"


async def test_borrow_and_repay_out(api, session, user):
    bank = await open_bank(api, session, user.id)
    r = await op(api, op="borrow", person_name="Петя", amount=1000000)
    assert r["balance_after"] == "-1000000" and r["debt"]["status"] == "i_owe"
    assert r["transaction"]["title"] == "Занял: Петя"
    assert await balance(api, bank, "VND") == D(11000000)
    pid = r["debt"]["id"]
    r = await op(api, op="repay_out", person_name="Пете", amount=500000)  # «отдал Пете»
    assert r["debt"]["id"] == pid and r["balance_after"] == "-500000"
    assert r["transaction"]["account"]["name"] == "Карта"
    assert r["transaction"]["title"] == "Вернул долг: Петя"
    assert await balance(api, bank, "VND") == D(10500000)
    # overpaying my own debt: the person now owes me the surplus
    r = await op(api, op="repay_out", debt_id=pid, amount=700000)
    assert r["balance_after"] == "200000" and r["excess"] == "200000"
    r = await op(api, 422, op="repay_out", debt_id=pid, amount=None)
    assert "Ты ничего не должен" in r["error"]["message"]
    # lending to someone I owe nets out (no special logic)
    r2 = await op(api, op="borrow", debt_id=pid, amount=200000)
    assert r2["balance_after"] == "0"
    r3 = await op(api, op="lend", debt_id=pid, amount=50000)
    assert r3["balance_after"] == "50000"


async def test_forgive_owed_to_me_is_expense_in_stats(api, session, user):
    await open_bank(api, session, user.id)
    d = (await op(api, op="lend", person_name="Коля", amount=300000))["debt"]
    before = await _stats(api)
    r = await op(api, 422, op="forgive", debt_id=d["id"], amount=400000)
    assert "Больше остатка" in r["error"]["message"]
    r = await op(api, 422, op="forgive", debt_id=d["id"], amount=None, direction="i_owe")
    assert "это тебе должны" in r["error"]["message"]
    r = await op(api, op="forgive", debt_id=d["id"], amount=None, direction="owed_to_me")
    tx = r["transaction"]
    assert tx["kind"] == "expense" and tx["category"]["name"] == "Переводы людям"
    assert tx["is_business"] is False and tx["account"]["name"] == "Долг: Коля"
    assert tx["amount"] == "300000" and r["balance_after"] == "0"
    assert tx["title"] == "Списан долг: Коля"
    after = await _stats(api)
    assert D(after["spent_month"]) - D(before["spent_month"] or 0) == D(300000)
    cats = (await api.get("/stats/by-category", params={
        "kind": "expense", "currency": "VND"})).json()
    assert any(c["name"] == "Переводы людям" and c["amount"] == "300000" for c in cats["items"])
    r = await op(api, 422, op="forgive", debt_id=d["id"], amount=None)
    assert "Долга нет" in r["error"]["message"]


async def test_forgive_partial_and_i_owe_income(api, session, user):
    await open_bank(api, session, user.id)
    d = (await op(api, op="borrow", person_name="Петя", amount=100000))["debt"]
    before = await _stats(api)
    r = await op(api, op="forgive", debt_id=d["id"], amount=40000)
    assert r["transaction"]["kind"] == "income" and r["balance_after"] == "-60000"
    assert r["transaction"]["category"]["name"] == "Прочее"
    after = await _stats(api)
    assert D(after["income_month"]) - D(before["income_month"] or 0) == D(40000)


async def test_unknown_person_cannot_repay_and_new_one_is_created_on_lend(api, session, user):
    await open_bank(api, session, user.id)
    r = await op(api, 422, op="repay_in", person_name="Вася", amount=100000)
    assert r["error"]["message"] == "Не знаю долгов: Вася"
    r = await op(api, 422, op="forgive", person_name="Вася", amount=None)
    assert (await api.get("/debts", params={"include_settled": "true"})).json()["settled"] == []
    r = await op(api, op="lend", person_name="@vasya", amount=100000)
    assert r["debt"]["name"] == "@vasya" and r["debt"]["handle"] == "vasya"
    r2 = await op(api, op="repay_in", person_name="@Vasya", amount=None)
    assert r2["debt"]["id"] == r["debt"]["id"] and r2["balance_after"] == "0"


async def test_declensions_are_one_person_and_aliases_learned(api, session, user):
    await open_bank(api, session, user.id)
    a = await op(api, op="lend", person_name="Коля", amount=100000)
    b = await op(api, op="lend", person_name="Коле", amount=100000)
    c = await op(api, op="repay_in", person_name="у Коли", amount=50000)
    d = await op(api, op="repay_in", person_name="КОЛЯ", amount=50000)
    ids = {x["debt"]["id"] for x in (a, b, c, d)}
    assert len(ids) == 1
    assert d["balance_after"] == "100000"
    assert set(d["debt"]["aliases"]) >= {"коле", "у коли"}
    assert d["debt"]["name"] == "Коля"


async def test_backdated_operation_counts(api, session, user):
    await open_bank(api, session, user.id)
    today = local_today("Asia/Ho_Chi_Minh")
    d = (await op(api, op="lend", person_name="Коля", amount=500000))["debt"]
    r = await op(api, op="repay_in", debt_id=d["id"], amount=100000,
                 occurred_on=(today - timedelta(days=400)).isoformat())
    assert r["balance_after"] == "400000"
    det = (await api.get(f"/debts/{d['id']}")).json()
    # history is in time order of the operations: the back-dated return comes first
    assert [h["balance_after"] for h in det["history"]] == ["400000", "-100000"]
    assert det["debt"]["balances"][0]["open_since"] == today.isoformat()


async def test_two_currencies_one_person_in_both_lists(api, session, user):
    await open_bank(api, session, user.id)
    kb = await acc_id(session, user.id, "Кошелёк")
    d = (await op(api, op="lend", person_name="Коля", amount=300000))["debt"]
    await op(api, op="borrow", debt_id=d["id"], amount=50, currency="USDT", account_id=kb)
    lst = (await api.get("/debts")).json()
    owed = lst["owed_to_me"][0]
    owe = lst["i_owe"][0]
    assert owed["id"] == owe["id"] == d["id"] and owed["status"] == "mixed"
    assert [(b["currency"], b["amount"]) for b in owed["balances"]] == [("VND", "300000")]
    assert [(b["currency"], b["amount"]) for b in owe["balances"]] == [("USDT", "-50")]
    # ≈ values carry the sign of the balance; the totals are positive numbers
    assert owe["balances"][0]["approx_usd"] == "-50"
    assert owe["balances"][0]["approx_vnd"] == "-1307000"
    t = lst["totals"]
    assert t["owed_to_me_vnd"] == "300000" and t["i_owe_vnd"] == "1307000"
    assert t["i_owe_usd"] == "50" and t["owed_to_me_count"] == 1 and t["i_owe_count"] == 1
    # repay in a currency without a balance → 422 (no conversion between currencies)
    r = await op(api, 422, op="repay_in", debt_id=d["id"], amount=10, currency="USDT",
                 account_id=kb)
    assert "в USDT" in r["error"]["message"]
    full = (await api.get(f"/debts/{d['id']}")).json()["debt"]
    assert [b["currency"] for b in full["balances"]] == ["VND", "USDT"]

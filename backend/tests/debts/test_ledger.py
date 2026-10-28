"""Debt operations in the ledger: needs_account (both orientations), crypto (USDT, TON), $ from a
₫ account (estimated), edits, undo, idempotency, debt accounts hidden and write-protected
(docs/DEBTS.md §2–§4)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models import Transaction
from tests.debts.conftest import acc_id, balance, cat_id, debt_bal, idem, op, open_bank, undo

D = Decimal


def _sp(s: str) -> str:
    return s.replace("\u00a0", " ")


async def _assign(api, tx_id, account_id, expect=200, version=None):  # type: ignore[no-untyped-def]
    body = {"account_id": account_id}
    if version is not None:
        body["version"] = version
    r = await api.post(f"/transactions/{tx_id}/account", json=body, headers=idem())
    assert r.status_code == expect, r.text
    return r.json()


async def _patch(api, tx_id, expect=200, **body):  # type: ignore[no-untyped-def]
    tx = (await api.get(f"/transactions/{tx_id}")).json()
    body = {"version": tx["version"], **{k: str(v) if isinstance(v, (int, D)) and k in (
        "amount", "account_amount") else v for k, v in body.items()}}
    r = await api.patch(f"/transactions/{tx_id}", json=body, headers=idem())
    assert r.status_code == expect, r.text
    return r.json()


async def test_usdt_lend_without_account_needs_account_then_assign(api, session, user):
    bank = await open_bank(api, session, user.id)
    kb = await acc_id(session, user.id, "Кошелёк")
    await api.put(f"/accounts/{kb}/opening", json={"asset": "USDT", "amount": "100"})
    r = await op(api, op="lend", person_name="Вася", amount=50, currency="USDT")
    assert r["needs_account"] is True
    tx = r["transaction"]
    assert tx["status"] == "needs_account" and tx["account"] is None
    assert tx["counter_account"]["name"] == "Долг: Вася"
    assert r["balance_after"] == "50"  # the debt exists at once
    did = r["debt"]["id"]
    assert await debt_bal(api, did) == {"USDT": D(50)}
    assert await balance(api, bank, "VND") == D(10000000)  # Карта untouched
    assert (await api.get("/transactions/needs-account/count")).json()["count"] == 1
    out = await _assign(api, tx["id"], kb)
    t2 = out["transaction"]
    assert t2["status"] == "posted" and t2["account"]["name"] == "Кошелёк"
    assert t2["account_amount"] == "50" and t2["account_asset"] == "USDT"
    assert await balance(api, kb, "USDT") == D(50)
    assert await debt_bal(api, did) == {"USDT": D(50)}
    # second answer to the same question (stale card) → 409
    await _assign(api, tx["id"], kb, expect=409)
    # a single-currency ₫ account cannot pay USDT
    r = await _assign(api, tx["id"], bank, expect=422, version=t2["version"])
    assert "нет USDT" in r["error"]["message"]


async def test_inflow_needs_account_sets_counter_account(api, session, user):
    kb = await acc_id(session, user.id, "Кошелёк")
    await api.put(f"/accounts/{kb}/opening", json={"asset": "USDT", "amount": "0"})
    r = await op(api, op="borrow", person_name="Петя", amount=30, currency="USDT")
    tx = r["transaction"]
    # CHECK needs_account_no_account: the debt account is the source of an inflow
    assert tx["status"] == "needs_account" and tx["account"]["name"] == "Долг: Петя"
    assert tx["counter_account"] is None and r["balance_after"] == "-30"
    out = await _assign(api, tx["id"], kb)
    t2 = out["transaction"]
    assert t2["status"] == "posted" and t2["counter_account"]["name"] == "Кошелёк"
    assert t2["counter_amount"] == "30" and t2["counter_asset"] == "USDT"
    assert await balance(api, kb, "USDT") == D(30)
    # undo of «На какой счёт?» → back to needs_account, money leaves Кошелёк, debt stays
    await undo(api, out["action_id"])
    row = await session.get(Transaction, tx["id"])
    await session.refresh(row)
    assert row.status == "needs_account" and row.counter_account_id is None
    assert await balance(api, kb, "USDT") == D(0)
    assert await debt_bal(api, r["debt"]["id"]) == {"USDT": D(-30)}


async def test_account_none_posts_debt_side_only(api, session, user):
    bank = await open_bank(api, session, user.id)
    r = await op(api, op="lend", person_name="Коля", amount=200000, account_none=True)
    assert r["needs_account"] is False and r["transaction"]["status"] == "posted"
    assert r["transaction"]["account"] is None and r["balance_after"] == "200000"
    assert await balance(api, bank, "VND") == D(10000000)
    r = await op(api, 422, op="lend", person_name="Коля", amount=1, account_none=True,
                 account_id=bank)
    r = await op(api, 422, op="lend", person_name="Коля", amount=1, account_none=True,
                 account_amount=5)


async def test_ton_from_wallet_and_partial_return(api, session, user):
    bank = await open_bank(api, session, user.id)
    kb = await acc_id(session, user.id, "Кошелёк")
    await api.put(f"/accounts/{kb}/opening", json={"asset": "TON", "amount": "10"})
    r = await op(api, op="lend", person_name="Лена", amount=3, currency="TON", account_id=kb)
    assert r["transaction"]["amount_usd"] == "16.2"  # 3 × 5.4 (≈ $, the debt stays in TON)
    did = r["debt"]["id"]
    assert await balance(api, kb, "TON") == D(7)
    r = await op(api, op="repay_in", debt_id=did, amount="1.5", currency="TON", account_id=kb)
    assert r["balance_after"] == "1.5"
    assert await balance(api, kb, "TON") == D("8.5")
    lst = (await api.get("/debts")).json()
    b = lst["owed_to_me"][0]["balances"][0]
    assert b["currency"] == "TON" and b["amount"] == "1.5" and b["approx_usd"] == "8.1"
    # a coin from a single-currency ₫ account → 422 (as for any operation)
    r = await op(api, 422, op="lend", debt_id=did, amount=1, currency="TON", account_id=bank)
    assert "нет TON" in r["error"]["message"]


async def test_usd_debt_from_vnd_account_is_estimated_and_edit_recomputes(api, session, user):
    bank = await open_bank(api, session, user.id, "100000000")
    r = await op(api, op="lend", person_name="Маша", amount=100, currency="USD",
                 account_id=bank)
    tx = r["transaction"]
    assert tx["is_estimated"] is True and tx["account_amount"] == "2614000"
    assert tx["account_asset"] == "VND" and tx["counter_amount"] == "100"
    assert tx["counter_asset"] == "USD" and r["balance_after"] == "100"
    assert await balance(api, bank, "VND") == D(100000000 - 2614000)
    # «нет, 120$»: the real side is recomputed by rate (not account_amount = amount!)
    out = await _patch(api, tx["id"], amount=120)
    t2 = out["transaction"]
    assert t2["account_amount"] == "3136800" and t2["counter_amount"] == "120"
    assert await debt_bal(api, r["debt"]["id"]) == {"USD": D(120)}
    # the actual charge («списали 3.2м»)
    out = await _patch(api, tx["id"], account_amount=3200000)
    t3 = out["transaction"]
    assert t3["is_estimated"] is False and t3["account_amount"] == "3200000"
    assert await balance(api, bank, "VND") == D(100000000 - 3200000)
    # an inflow in $ to Карта: counter side converted
    r = await op(api, op="repay_in", debt_id=r["debt"]["id"], amount=20, currency="USD")
    assert r["needs_account"] is True  # $ without an account → ask
    out = await _assign(api, r["transaction"]["id"], bank)
    assert out["transaction"]["counter_amount"] == "522800"
    assert out["transaction"]["is_estimated"] is True


async def test_edit_rules(api, session, user):
    bank = await open_bank(api, session, user.id)
    cash = await acc_id(session, user.id, "Наличные")
    await api.put(f"/accounts/{cash}/opening", json={"asset": "VND", "amount": "1000000"})
    lend = await op(api, op="lend", person_name="Коля", amount=500000)
    rep = await op(api, op="repay_in", person_name="Коля", amount=200000)
    did = lend["debt"]["id"]
    # «нет, 250к» on the return
    out = await _patch(api, rep["transaction"]["id"], amount=250000)
    assert out["transaction"]["counter_amount"] == "250000"
    assert out["transaction"]["account_amount"] == "250000"
    assert await debt_bal(api, did) == {"VND": D(250000)}
    assert await balance(api, bank, "VND") == D(10000000 - 500000 + 250000)
    # real side of the return → «Наличные»
    out = await _patch(api, rep["transaction"]["id"], account_id=cash)
    assert out["transaction"]["counter_account"]["name"] == "Наличные"
    assert out["transaction"]["account"]["name"] == "Долг: Коля"
    assert await balance(api, cash, "VND") == D(1250000)
    assert await balance(api, bank, "VND") == D(9500000)
    # the person / category / kind of a transfer cannot be changed
    r = await _patch(api, rep["transaction"]["id"], expect=422, counterparty="Петя")
    assert "Сменить человека" in r["error"]["message"]
    r = await _patch(api, rep["transaction"]["id"], expect=422,
                     category_id=await cat_id(session, user.id, "Еда"))
    # the debt account itself as the real side → 422
    r = await _patch(api, rep["transaction"]["id"], expect=422,
                     account_id=lend["debt"]["account_id"])
    assert "счёт долга" in r["error"]["message"]
    # forgive: amount ≤ remaining before it, category of the same kind, no account change
    fg = await op(api, op="forgive", debt_id=did, amount=100000)
    fid = fg["transaction"]["id"]
    r = await _patch(api, fid, expect=422, account_id=bank)
    assert "списания долга" in r["error"]["message"]
    r = await _patch(api, fid, expect=422, currency="USD")
    r = await _patch(api, fid, expect=422, amount=300000)  # remaining before it: 250 000
    out = await _patch(api, fid, amount=250000)
    assert out["transaction"]["amount"] == "250000"
    assert await debt_bal(api, did) == {}
    gifts = await cat_id(session, user.id, "Подарки")
    out = await _patch(api, fid, category_id=gifts)
    assert out["transaction"]["category"]["name"] == "Подарки"
    r = await _patch(api, fid, expect=422,
                     category_id=await cat_id(session, user.id, "Зарплата", "income"))
    await _assign(api, fid, bank, expect=422, version=out["transaction"]["version"])


async def test_undo_each_operation_restores_balances(api, session, user):
    bank = await open_bank(api, session, user.id)
    steps = [
        dict(op="lend", person_name="Коля", amount=500000),
        dict(op="repay_in", person_name="Коля", amount=200000),
        dict(op="repay_in", person_name="Коля", amount=400000),  # overpay → −100 000
        dict(op="forgive", person_name="Коля", amount=None),
        dict(op="borrow", person_name="Коля", amount=1000000),
        dict(op="repay_out", person_name="Коля", amount=None),
    ]
    history: list[tuple[str, dict, D | None]] = []
    for s in steps:
        did = history[-1][1]["debt"]["id"] if history else None
        before = (await debt_bal(api, did)).get("VND", D(0)) if did else D(0)
        r = await op(api, **s)
        history.append((r["action_id"], r, before))
    bal_bank = await balance(api, bank, "VND")
    assert bal_bank == D(10000000 - 500000 + 200000 + 400000 + 1000000 - 1000000)
    did = history[0][1]["debt"]["id"]
    for action_id, _r, before in reversed(history):
        res = await undo(api, action_id)
        assert res["status"] == "undone" and res["summary"].startswith("Отменено: ")
        assert "debts" in res["changed"]
        assert (await debt_bal(api, did)).get("VND", D(0)) == before
    assert await balance(api, bank, "VND") == D(10000000)
    # the person survives the undo of everything but is hidden from the lists
    lst = (await api.get("/debts", params={"include_settled": "true"})).json()
    assert lst["owed_to_me"] == [] and lst["i_owe"] == [] and lst["settled"] == []
    assert (await api.get(f"/debts/{did}")).status_code == 200
    # redo (undo of the undo) brings the loan back
    first_undo = (await api.get("/actions")).json()[0]
    await undo(api, first_undo["id"])
    assert await debt_bal(api, did) == {"VND": D(500000)}


async def test_action_summaries_and_delete_restore(api, session, user):
    await open_bank(api, session, user.id)
    r = await op(api, op="lend", person_name="Коля", amount=500000)
    acts = (await api.get("/actions")).json()
    assert acts[0]["kind"] == "debt" and _sp(acts[0]["summary"]) == "Долг: Коля +500 000 ₫"
    r2 = await op(api, op="repay_in", person_name="Коля", amount=200000)
    assert _sp((await api.get("/actions")).json()[0]["summary"]) == "Возврат: Коля −200 000 ₫"
    tx = r2["transaction"]
    d = await api.delete(f"/transactions/{tx['id']}", headers=idem())
    assert d.status_code == 200, d.text
    assert await debt_bal(api, r["debt"]["id"]) == {"VND": D(500000)}
    rs = await api.post(f"/transactions/{tx['id']}/restore", headers=idem())
    assert rs.status_code == 200, rs.text
    assert await debt_bal(api, r["debt"]["id"]) == {"VND": D(300000)}
    page = (await api.get("/transactions")).json()
    titles = {t["title"] for t in page["items"]}
    assert {"Дал в долг: Коля", "Возврат долга: Коля"} <= titles
    assert page["totals"]["expense_vnd"] == "0" and page["totals"]["income_vnd"] == "0"


async def test_idempotent_debt_operation(api, session, user):
    bank = await open_bank(api, session, user.id)
    body = {"op": "lend", "person_name": "Коля", "amount": "500000", "currency": "VND"}
    key = idem()
    r1 = await api.post("/debts/operations", json=body, headers=key)
    r2 = await api.post("/debts/operations", json=body, headers=key)
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["transaction"]["id"] == r2.json()["transaction"]["id"]
    assert await balance(api, bank, "VND") == D(9500000)
    n = len(list(await session.scalars(
        select(Transaction).where(Transaction.user_id == user.id, Transaction.debt_op.isnot(None))
    )))
    assert n == 1
    r3 = await api.post("/debts/operations", json={**body, "amount": "1"}, headers=key)
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "idempotency_mismatch"


async def test_undo_of_edit_and_assign_on_debt_rows(api, session, user):
    bank = await open_bank(api, session, user.id)
    lend = await op(api, op="lend", person_name="Коля", amount=500000)
    did = lend["debt"]["id"]
    out = await _patch(api, lend["transaction"]["id"], amount=250000, description="на ремонт")
    assert out["transaction"]["title"] == "Дал в долг: Коля · на ремонт"
    assert await debt_bal(api, did) == {"VND": D(250000)}
    res = await undo(api, out["action_id"])
    assert res["status"] == "undone" and "debts" in res["changed"]
    assert await debt_bal(api, did) == {"VND": D(500000)}
    assert await balance(api, bank, "VND") == D(9500000)
    # «Без счёта» through the «С какого?» endpoint on a posted row needs the version
    tx = (await api.get(f"/transactions/{lend['transaction']['id']}")).json()
    out = await _assign(api, tx["id"], None, version=tx["version"])
    assert out["transaction"]["account"] is None and out["transaction"]["status"] == "posted"
    assert await balance(api, bank, "VND") == D(10000000)
    assert await debt_bal(api, did) == {"VND": D(500000)}
    await undo(api, out["action_id"])
    assert await balance(api, bank, "VND") == D(9500000)

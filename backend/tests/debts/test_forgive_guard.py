"""A write-off (forgive) never exceeds the debt it wrote off — on every path, not only in
``record_op``: redo of an undone forgive, restore of a deleted one, undo of its delete, PATCH /
DELETE / undo of the loan or return it covered (docs/DEBTS.md §4).

These run on the live sessionmaker (``live_api``): a rejected request must roll back for real."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select

from app.models import Account, Debt
from app.models.enums import ActionKind
from app.schemas.debts import DebtOpCreate
from app.services.debts import service as debts
from app.services.ledger import transactions as ledger
from app.services.ledger.actions import begin_action, finish_action, undo_action
from tests.debts.conftest import idem

D = Decimal


def _sp(s: str) -> str:
    return s.replace(" ", " ")


async def _op(api: httpx.AsyncClient, expect: int = 201, **body: Any) -> dict[str, Any]:
    if body.get("amount") is not None:
        body["amount"] = str(body["amount"])
    body.setdefault("currency", "VND")
    r = await api.post("/debts/operations", json=body, headers=idem())
    assert r.status_code == expect, r.text
    return r.json()


async def _undo(api: httpx.AsyncClient, action_id: str, expect: int = 200) -> dict[str, Any]:
    r = await api.post(f"/actions/{action_id}/undo", headers=idem())
    assert r.status_code == expect, r.text
    return r.json()


async def _patch(api: httpx.AsyncClient, tx_id: int, expect: int = 200, **body: Any
                 ) -> dict[str, Any]:
    tx = (await api.get(f"/transactions/{tx_id}")).json()
    body = {"version": tx["version"],
            **{k: str(v) if k == "amount" else v for k, v in body.items()}}
    r = await api.patch(f"/transactions/{tx_id}", json=body, headers=idem())
    assert r.status_code == expect, r.text
    return r.json()


async def _bal(api: httpx.AsyncClient, debt_id: int) -> dict[str, D]:
    r = await api.get(f"/debts/{debt_id}")
    assert r.status_code == 200, r.text
    return {b["currency"]: D(b["amount"]) for b in r.json()["debt"]["balances"]}


async def _bank(api: httpx.AsyncClient, live_session, user_id: int) -> int:  # type: ignore[no-untyped-def]
    async with live_session() as s:
        bank = await s.scalar(
            select(Account.id).where(Account.user_id == user_id, Account.name == "Карта"))
    r = await api.put(f"/accounts/{bank}/opening", json={"asset": "VND", "amount": "10000000"})
    assert r.status_code == 200, r.text
    return int(bank)  # type: ignore[arg-type]


async def _bank_balance(api: httpx.AsyncClient, bank: int) -> D:
    r = await api.get(f"/accounts/{bank}")
    return next(D(a["balance"]) for a in r.json()["assets"] if a["asset"] == "VND")


async def _spent(api: httpx.AsyncClient) -> D:
    s = (await api.get("/stats/summary", params={"currency": "VND"})).json()
    return D(s["spent_month"]["value"] or 0)


def _err(r: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    e = r["error"]
    return e["code"], _sp(e["message"]), e.get("details") or {}


async def test_redo_of_forgive_after_full_return_conflicts(live_api, live_session, live_user):
    """«Гоша не вернёт» → «отмени» → «Гоша вернул весь долг» → «Вернуть»: no phantom expense
    and no «ты должен Гоше 300 000»."""
    api = live_api
    bank = await _bank(api, live_session, live_user.id)
    spent0 = await _spent(api)
    lend = await _op(api, op="lend", person_name="Гоша", amount=300000)
    did = lend["debt"]["id"]
    fg = await _op(api, op="forgive", debt_id=did, amount=None)
    assert await _spent(api) == spent0 + 300000
    u1 = await _undo(api, fg["action_id"])
    assert await _bal(api, did) == {"VND": D(300000)} and await _spent(api) == spent0
    await _op(api, op="repay_in", debt_id=did, amount=None)
    assert await _bal(api, did) == {} and await _bank_balance(api, bank) == D(10000000)
    # «Вернуть» (redo = undo of the undo) → 409, nothing changes
    code, msg, det = _err(await _undo(api, u1["undo_action_id"], 409))
    assert code == "undo_conflict" and msg == "Долг уже закрыт: Гоша — списывать нечего"
    assert det["reason"] == "forgive_exceeds_debt" and det["max"] == "0"
    assert await _bal(api, did) == {}
    assert await _spent(api) == spent0
    assert await _bank_balance(api, bank) == D(10000000)
    # the undo is still in force (not marked as undone by the failed redo)
    acts = {a["id"]: a for a in (await api.get("/actions")).json()}
    assert acts[u1["undo_action_id"]]["undone_at"] is None
    # a partial return leaves room for a smaller write-off only: the redo of 300k still fails
    d2 = (await _op(api, op="lend", person_name="Ира", amount=300000))["debt"]["id"]
    fg2 = await _op(api, op="forgive", debt_id=d2, amount=None)
    u2 = await _undo(api, fg2["action_id"])
    await _op(api, op="repay_in", debt_id=d2, amount=100000)
    code, msg, det = _err(await _undo(api, u2["undo_action_id"], 409))
    assert code == "undo_conflict" and det["max"] == "200000"
    assert msg == "Списать можно не больше остатка долга: Ира — сейчас 200 000 ₫"
    assert await _bal(api, d2) == {"VND": D(200000)}


async def test_restore_of_deleted_forgive_after_return(live_api, live_session, live_user):
    """DELETE of the write-off → «Тимур вернул весь долг» → restore / undo of the delete."""
    api = live_api
    await _bank(api, live_session, live_user.id)
    spent0 = await _spent(api)
    did = (await _op(api, op="lend", person_name="Тимур", amount=200000))["debt"]["id"]
    fg = await _op(api, op="forgive", debt_id=did, amount=None)
    fid = fg["transaction"]["id"]
    r = await api.delete(f"/transactions/{fid}", headers=idem())
    assert r.status_code == 200, r.text
    del_action = r.json()["action_id"]
    rep = await _op(api, op="repay_in", debt_id=did, amount=None)
    assert await _bal(api, did) == {}
    r = await api.post(f"/transactions/{fid}/restore", headers=idem())
    assert r.status_code == 422, r.text
    code, msg, det = _err(r.json())
    assert code == "validation_error" and msg == "Долг уже закрыт: Тимур — списывать нечего"
    assert det["reason"] == "forgive_exceeds_debt" and det["forgive_ids"] == [fid]
    code, _msg, _det = _err(await _undo(api, del_action, 409))
    assert code == "undo_conflict"
    assert await _bal(api, did) == {} and await _spent(api) == spent0
    assert (await api.get(f"/transactions/{fid}")).status_code == 404  # still deleted
    # without the return the write-off is covered again → restore works
    await _undo(api, rep["action_id"])
    r = await api.post(f"/transactions/{fid}/restore", headers=idem())
    assert r.status_code == 200, r.text
    assert await _bal(api, did) == {} and await _spent(api) == spent0 + 200000


async def test_edit_delete_undo_of_covered_loan_blocked(live_api, live_session, live_user):
    """A loan/borrow already written off cannot shrink under the write-off (s12: PATCH 400к →
    100к after «списать» 400к gave −300к and kept the 400к expense)."""
    api = live_api
    bank = await _bank(api, live_session, live_user.id)
    lend = await _op(api, op="lend", person_name="Зина", amount=400000)
    did, lid = lend["debt"]["id"], lend["transaction"]["id"]
    fg = await _op(api, op="forgive", debt_id=did, amount=None)
    code, msg, det = _err(await _patch(api, lid, 422, amount=100000))
    assert code == "validation_error" and det["reason"] == "forgive_exceeds_debt"
    assert msg.startswith("Сначала отмените списание долга: Зина")
    r = await api.delete(f"/transactions/{lid}", headers=idem())
    assert r.status_code == 422, r.text
    assert await _bal(api, did) == {} and await _bank_balance(api, bank) == D(9600000)
    # growing the loan / shrinking it while still covered is fine
    await _patch(api, lid, amount=500000)
    assert await _bal(api, did) == {"VND": D(100000)}
    await _patch(api, lid, amount=450000)
    assert await _bal(api, did) == {"VND": D(50000)}
    _err(await _patch(api, lid, 422, amount=399000))
    # once the write-off is undone the loan is free to change
    await _undo(api, fg["action_id"])
    await _patch(api, lid, amount=100000)
    assert await _bal(api, did) == {"VND": D(100000)}

    # undo of a covered loan → 409; LIFO order works
    o = await _op(api, op="lend", person_name="Олег", amount=200000)
    fo = await _op(api, op="forgive", debt_id=o["debt"]["id"], amount=None)
    code, msg, _det = _err(await _undo(api, o["action_id"], 409))
    assert code == "undo_conflict" and msg.startswith("Сначала отмените списание долга: Олег")
    await _undo(api, fo["action_id"])
    await _undo(api, o["action_id"])
    assert await _bal(api, o["debt"]["id"]) == {}

    # my own debt forgiven (income «Прочее»): the borrow cannot shrink under it either
    b = await _op(api, op="borrow", person_name="Аня", amount=1000000)
    fb = await _op(api, op="forgive", debt_id=b["debt"]["id"], amount=None)
    assert fb["transaction"]["kind"] == "income"
    code, msg, _det = _err(await _patch(api, b["transaction"]["id"], 422, amount=500000))
    assert msg.startswith("Сначала отмените списание долга: Аня")
    # a return recorded before the write-off cannot grow past it
    v = await _op(api, op="lend", person_name="Вика", amount=300000)
    vr = await _op(api, op="repay_in", debt_id=v["debt"]["id"], amount=100000)
    await _op(api, op="forgive", debt_id=v["debt"]["id"], amount=None)
    _err(await _patch(api, vr["transaction"]["id"], 422, amount=150000))
    await _patch(api, vr["transaction"]["id"], amount=50000)  # shrinking it is fine
    assert await _bal(api, v["debt"]["id"]) == {"VND": D(50000)}


async def test_operations_after_the_write_off_stay_free(live_api, live_session, live_user):
    """A new loan and an overpaying return recorded AFTER a write-off do not depend on it."""
    api = live_api
    await _bank(api, live_session, live_user.id)
    p = await _op(api, op="lend", person_name="Петя", amount=300000)
    did = p["debt"]["id"]
    await _op(api, op="forgive", debt_id=did, amount=None)
    await _op(api, op="lend", debt_id=did, amount=100000)
    rep = await _op(api, op="repay_in", debt_id=did, amount=150000)
    assert rep["excess"] == "50000" and await _bal(api, did) == {"VND": D(-50000)}
    out = await _patch(api, rep["transaction"]["id"], amount=160000)
    assert await _bal(api, did) == {"VND": D(-60000)}
    await _undo(api, out["action_id"])
    await _undo(api, rep["action_id"])
    assert await _bal(api, did) == {"VND": D(100000)}
    # a partial write-off restored while the balance still covers it
    k = await _op(api, op="lend", person_name="Коля", amount=300000)
    fk = await _op(api, op="forgive", debt_id=k["debt"]["id"], amount=100000)
    r = await api.delete(f"/transactions/{fk['transaction']['id']}", headers=idem())
    assert r.status_code == 200
    r = await api.post(f"/transactions/{fk['transaction']['id']}/restore", headers=idem())
    assert r.status_code == 200, r.text
    assert await _bal(api, k["debt"]["id"]) == {"VND": D(200000)}


async def test_one_action_is_checked_as_a_whole(session, user, principal):
    """Bulk delete of a loan + its write-off (and the undo of that) is consistent at the end of
    the action even though the intermediate state is not."""
    lend = await debts.record_op(session, principal, DebtOpCreate(
        op="lend", person_name="Лёша", amount=D(300000), currency="VND"))
    fg = await debts.record_op(session, principal, DebtOpCreate(
        op="forgive", debt_id=lend.debt.id, currency="VND"))
    ctx = await begin_action(session, principal, ActionKind.bulk_delete)
    await ledger.delete_transaction(session, principal, lend.transaction.id, action=ctx)
    await ledger.delete_transaction(session, principal, fg.transaction.id, action=ctx)
    await finish_action(session, ctx)
    d = await session.get(Debt, lend.debt.id)
    assert await debts.balances_of(session, d) == {}
    res = await undo_action(session, principal, ctx.id)
    assert res.status == "undone"
    assert await debts.balances_of(session, d) == {}
    # redo of the bulk delete: both go again
    res = await undo_action(session, principal, res.undo_action_id)
    assert res.status == "undone" and await debts.balances_of(session, d) == {}

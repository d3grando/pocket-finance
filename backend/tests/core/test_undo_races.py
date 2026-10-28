"""Ledger regressions: undo/redo texts, text «отмени» target, fallback category
memory, «мои аккаунты» removal, merge into an archived category, races (defaults, double
submit, wipe vs writers)."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import any_, func, select

from app.models import Account, Merchant, Transaction
from app.schemas.transactions import TransactionCreate
from app.services.accounts import service as accounts
from app.services.ledger import actions as ledger_actions
from app.services.ledger import transactions as ledger_tx
from app.services.ledger.wipe import wipe_financial_data
from tests.core.conftest import acc_id, cat_id, idem


def sp(s: str) -> str:
    return s.replace("\xa0", " ")


async def _post(api, body):  # type: ignore[no-untyped-def]
    r = await api.post("/transactions", json=body, headers=idem())
    assert r.status_code == 201, r.text
    return r.json()


async def _undo(api, aid):  # type: ignore[no-untyped-def]
    r = await api.post(f"/actions/{aid}/undo", headers=idem())
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------- undo texts


async def test_undo_redo_undo_chain_toggles_prefix(api, session, user):
    taxi = await cat_id(session, user.id, "Такси/транспорт")
    res = await _post(api, {"kind": "expense", "amount": "77000", "currency": "VND",
                            "category_id": taxi})
    tid = res["transaction"]["id"]
    u1 = await _undo(api, res["action_id"])
    assert sp(u1["summary"]) == "Отменено: 77 000 ₫ · Такси/транспорт"
    u2 = await _undo(api, u1["undo_action_id"])  # «Вернуть»
    assert sp(u2["summary"]) == "Возвращено: 77 000 ₫ · Такси/транспорт"
    assert (await api.get(f"/transactions/{tid}")).status_code == 200
    u3 = await _undo(api, u2["undo_action_id"])  # «Отменить» on the ↪️ message
    assert sp(u3["summary"]) == "Отменено: 77 000 ₫ · Такси/транспорт"
    assert (await api.get(f"/transactions/{tid}")).status_code == 404
    u4 = await _undo(api, u3["undo_action_id"])
    assert sp(u4["summary"]) == "Возвращено: 77 000 ₫ · Такси/транспорт"
    acts = (await api.get("/actions")).json()
    assert not any("Возвращено: Возвращено" in a["summary"] or "Отменено: Отменено"
                   in a["summary"] for a in acts)


async def test_undo_of_edit_names_the_restored_value(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    tx = (await _post(api, {"kind": "expense", "amount": "250000", "currency": "VND",
                            "category_id": food}))["transaction"]
    p = await api.patch(f"/transactions/{tx['id']}", json={"version": 1, "amount": "300000"})
    assert p.status_code == 200, p.text
    u = await _undo(api, p.json()["action_id"])
    assert sp(u["summary"]) == "Отменено изменение: 300 000 ₫ → 250 000 ₫ · Еда"
    assert (await api.get(f"/transactions/{tx['id']}")).json()["amount"] == "250000"
    r = await _undo(api, u["undo_action_id"])
    assert sp(r["summary"]) == "Возвращено изменение: 250 000 ₫ → 300 000 ₫ · Еда"


async def test_text_undo_targets_previous_operation_not_redo(api, session, user):
    coffee = await cat_id(session, user.id, "Кофе")
    food = await cat_id(session, user.id, "Еда")
    a = await _post(api, {"kind": "expense", "amount": "11000", "currency": "VND",
                          "category_id": coffee})
    b = await _post(api, {"kind": "expense", "amount": "22000", "currency": "VND",
                          "category_id": food})
    last = await ledger_actions.last_undoable_action(session, user.id)
    assert last is not None and last.id == uuid.UUID(b["action_id"])
    await _undo(api, b["action_id"])
    last2 = await ledger_actions.last_undoable_action(session, user.id)
    assert last2 is not None and last2.id == uuid.UUID(a["action_id"])  # not the undo action


# ------------------------------------------------------------------------------- merchants


async def test_fallback_category_is_not_learned_for_handle(api, session, user):
    people = await cat_id(session, user.id, "Переводы людям")
    soft = await cat_id(session, user.id, "Софт и AI")
    await _post(api, {"kind": "expense", "amount": "1", "currency": "TON", "account_none": True,
                      "category_id": people, "counterparty": "@fb99078"})
    m = await session.scalar(select(Merchant).where(Merchant.user_id == user.id,
                                                    Merchant.handle == "fb99078"))
    assert m is not None and m.category_id is None and m.hits == 1
    # a meaningful category of the next payment is learned
    t2 = (await _post(api, {"kind": "expense", "amount": "1", "currency": "TON",
                            "account_none": True, "category_id": soft,
                            "counterparty": "@fb99078"}))["transaction"]
    await session.refresh(m)
    assert m.category_id == soft
    # an explicit user correction is remembered, even to the fallback one
    p = await api.patch(f"/transactions/{t2['id']}",
                        json={"version": t2["version"], "category_id": people})
    assert p.status_code == 200, p.text
    await session.refresh(m)
    assert m.category_id == people


# ------------------------------------------------------------------------------- my accounts


async def test_removed_own_handle_no_longer_resolves(api, session, user):
    r = await api.post("/my-accounts", json={"handle": "@gone99078"})
    assert r.status_code == 201, r.text
    ma, wallet = r.json()["id"], r.json()["account_id"]
    assert (await accounts.resolve_account(session, user.id, "@gone99078")).id == wallet
    d = await api.delete(f"/my-accounts/{ma}")
    assert d.status_code == 200, d.text
    assert await accounts.resolve_account(session, user.id, "@gone99078") is None
    acc = await session.get(Account, wallet)
    await session.refresh(acc)
    assert acc.deleted_at is not None  # unused wallet removed
    # a used wallet is archived and loses its @aliases
    r2 = await api.post("/my-accounts", json={"handle": "@used99078"})
    w2 = r2.json()["account_id"]
    kb = await acc_id(session, user.id, "Кошелёк")
    t = await api.post("/transfers", headers=idem(), json={
        "from_account_id": kb, "to_account_id": w2, "amount": "1", "currency": "TON"})
    assert t.status_code == 201, t.text
    assert (await api.delete(f"/my-accounts/{r2.json()['id']}")).status_code == 200
    acc2 = await session.get(Account, w2)
    await session.refresh(acc2)
    assert acc2.is_archived and acc2.deleted_at is None
    assert not any(a.lstrip("@") == "used99078" for a in acc2.aliases)
    assert await accounts.resolve_account(session, user.id, "@used99078") is None


# ------------------------------------------------------------------------------- categories


async def test_merge_into_archived_category_is_422(api, session, user):
    a = await api.post("/categories", json={"name": "Src99", "kind": "expense",
                                            "is_business": False})
    b = await api.post("/categories", json={"name": "Dst99", "kind": "expense",
                                            "is_business": False})
    src, dst = a.json()["id"], b.json()["id"]
    assert (await api.delete(f"/categories/{dst}")).status_code == 200
    r = await api.post("/categories/merge", json={"source_id": src, "target_id": dst})
    assert r.status_code == 422 and "в архиве" in r.json()["error"]["message"]


# ------------------------------------------------------------------------------- races


async def test_parallel_defaults_keep_one_default(live_api, live_session, live_user):
    async with live_session() as s:
        kb = await acc_id(s, live_user.id, "Кошелёк")
        piggy = await acc_id(s, live_user.id, "Копилка")
    r = await asyncio.gather(*[
        live_api.patch(f"/accounts/{a}", json={"version": 1, "default_for_currencies": ["USDT"]})
        for a in (kb, piggy)])
    assert sorted(x.status_code for x in r) == [200, 422], [x.text for x in r]
    async with live_session() as s:
        n = await s.scalar(select(func.count()).where(
            Account.user_id == live_user.id, any_(Account.default_for_currencies) == "USDT"))
    assert n == 1


async def test_double_submit_account_and_merchant_is_409(live_api, live_session, live_user):
    r = await asyncio.gather(*[live_api.post("/accounts", json={
        "name": "Wise", "kind": "bank", "currency": "USD"}) for _ in range(4)])
    codes = sorted(x.status_code for x in r)
    assert codes == [201, 409, 409, 409], [x.text for x in r]
    m = await asyncio.gather(*[live_api.post("/merchants", json={
        "name": "petshop", "aliases": []}) for _ in range(4)])
    assert sorted(x.status_code for x in m) == [201, 409, 409, 409], [x.text for x in m]


async def test_wipe_waits_for_open_writer_and_succeeds(live_session, live_user, live_principal):
    async with live_session() as s:
        food = await cat_id(s, live_user.id, "Еда")
    writer = live_session()
    # a writer is in the middle of recording an operation (not committed yet)
    await ledger_tx.create_transaction(writer, live_principal, TransactionCreate(
        kind="expense", amount="12000", currency="VND", category_id=food))
    wiper = live_session()
    task = asyncio.create_task(wipe_financial_data(wiper, live_user.id))
    await asyncio.sleep(0.3)
    assert not task.done()  # blocked by the writer's lock, not failing on a FK
    await writer.commit()
    await writer.close()
    await asyncio.wait_for(task, 10)
    await wiper.commit()
    await wiper.close()
    async with live_session() as s:
        n = await s.scalar(select(func.count()).where(Transaction.user_id == live_user.id))
        assert n == 0
        assert await acc_id(s, live_user.id, "Карта")  # re-seeded


async def test_wipe_under_continuous_writes(live_api, live_session, live_user):
    async with live_session() as s:
        food = await cat_id(s, live_user.id, "Еда")
    stop = asyncio.Event()
    codes: list[int] = []

    async def write_loop() -> None:
        while not stop.is_set():
            r = await live_api.post("/transactions", headers=idem(), json={
                "kind": "expense", "amount": "1000", "currency": "VND", "category_id": food})
            codes.append(r.status_code)
            await asyncio.sleep(0.01)

    task = asyncio.create_task(write_loop())
    await asyncio.sleep(0.3)
    async with live_session() as s:
        await wipe_financial_data(s, live_user.id)
        await s.commit()
    await asyncio.sleep(0.2)
    stop.set()
    await task
    assert 500 not in codes, codes
    assert codes.count(201) > 2

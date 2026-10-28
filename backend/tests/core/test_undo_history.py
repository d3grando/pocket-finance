"""Undo / redo by action id, conflicts, dialogue context (recent actions), service-level API
used by assistant (own actions vs. caller actions)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.models import Posting, Transaction
from app.models.enums import ActionKind
from app.schemas.intents import ParseContext, TargetSpec
from app.schemas.transactions import TransactionCreate, TransactionPatch
from app.services.ledger import actions, query
from app.services.ledger import transactions as ledger
from app.services.ledger.common import now
from tests.core.conftest import acc_id, cat_id, idem


async def _new(api, session, user, amount="150000"):  # type: ignore[no-untyped-def]
    food = await cat_id(session, user.id, "Еда")
    r = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": amount, "currency": "VND", "category_id": food})
    assert r.status_code == 201
    return r.json()


async def test_undo_create_then_redo(api, session, user):
    res = await _new(api, session, user)
    tid, aid = res["transaction"]["id"], res["action_id"]
    u = await api.post(f"/actions/{aid}/undo", headers=idem())
    assert u.status_code == 200, u.text
    body = u.json()
    assert body["status"] == "undone" and body["undone_action_id"] == aid
    assert body["summary"].replace("\xa0", " ") == "Отменено: 150 000 ₫ · Еда"
    assert {"transactions", "accounts", "stats"} <= set(body["changed"])
    tx = await session.get(Transaction, tid)
    assert tx.deleted_at is not None
    assert (await session.scalars(select(Posting).where(Posting.transaction_id == tid))).all() \
        == []
    # repeated undo → idempotent
    again = (await api.post(f"/actions/{aid}/undo", headers=idem())).json()
    assert again["status"] == "already_undone"
    assert again["undo_action_id"] == body["undo_action_id"]
    # redo = undo of the undo action
    redo = await api.post(f"/actions/{body['undo_action_id']}/undo", headers=idem())
    assert redo.status_code == 200 and redo.json()["summary"].startswith("Возвращено:")
    await session.refresh(tx)
    assert tx.deleted_at is None
    assert len((await session.scalars(select(Posting).where(Posting.transaction_id == tid)))
               .all()) == 1


async def test_undo_update_restores_before_and_conflict(api, session, user):
    res = await _new(api, session, user, "200000")
    tid = res["transaction"]["id"]
    up = await api.patch(f"/transactions/{tid}", json={"version": 1, "amount": "250000"})
    up_action = up.json()["action_id"]
    u = await api.post(f"/actions/{up_action}/undo", headers=idem())
    assert u.status_code == 200
    got = (await api.get(f"/transactions/{tid}")).json()
    assert got["amount"] == "200000" and got["amount_vnd"] == "200000"
    # record changed after the action → undo of the original create conflicts
    await api.patch(f"/transactions/{tid}", json={"version": got["version"], "amount": "1000"})
    c = await api.post(f"/actions/{res['action_id']}/undo", headers=idem())
    assert c.status_code == 409 and c.json()["error"]["code"] == "undo_conflict"
    assert (await api.get(f"/transactions/{tid}")).json()["amount"] == "1000"


async def test_undo_delete_restores(api, session, user):
    res = await _new(api, session, user)
    tid = res["transaction"]["id"]
    d = await api.delete(f"/transactions/{tid}")
    u = await api.post(f"/actions/{d.json()['action_id']}/undo", headers=idem())
    assert u.status_code == 200
    assert (await api.get(f"/transactions/{tid}")).status_code == 200
    assert (await api.post("/actions/00000000-0000-0000-0000-000000000000/undo",
                           headers=idem())).status_code == 404


async def test_recent_actions_context_and_targets(api, session, user, principal):
    a = await _new(api, session, user, "100000")
    b = await _new(api, session, user, "200000")
    rec = (await api.get("/actions", params={"recent": True})).json()
    assert [x["id"] for x in rec][:2] == [b["action_id"], a["action_id"]]
    briefs = await actions.recent_actions(session, user.id)
    assert briefs[0].transaction_ids == [b["transaction"]["id"]]
    assert briefs[0].amount == Decimal("200000") and briefs[0].category == "Еда"
    assert briefs[0].account == "Карта" and briefs[0].tx_kind == "expense"
    ctx = ParseContext(now=now(), today=now().date(), recent_actions=briefs,
                       edit_transaction_id=a["transaction"]["id"])
    last = await query.resolve_target(session, user.id, TargetSpec(kind="last"), ctx)
    assert [t.id for t in last] == [b["transaction"]["id"]]
    prev = await query.resolve_target(session, user.id, TargetSpec(kind="last", position=2), ctx)
    assert [t.id for t in prev] == [a["transaction"]["id"]]
    c = await query.resolve_target(session, user.id, TargetSpec(kind="context"), ctx)
    assert [t.id for t in c] == [a["transaction"]["id"]]
    none = await query.resolve_target(session, user.id, TargetSpec(kind="last_income"), ctx)
    assert none == []
    # undone actions drop out of the context
    await api.post(f"/actions/{b['action_id']}/undo", headers=idem())
    briefs = await actions.recent_actions(session, user.id)
    assert b["action_id"] not in [str(x.action_id) for x in briefs]
    last = await query.resolve_target(session, user.id, TargetSpec(kind="last_expense"),
                                      ParseContext(now=now(), today=now().date(),
                                                   recent_actions=briefs))
    assert [t.id for t in last] == [a["transaction"]["id"]]


async def test_service_api_with_caller_action(session, user, principal):
    """assistant style: one action for a whole intent, caller finishes it."""
    food = await cat_id(session, user.id, "Еда")
    taxi = await cat_id(session, user.id, "Такси/транспорт")
    ctx = await actions.begin_action(session, principal, ActionKind.create_transaction,
                                     input_text="еда 120к, такси 45к")
    t1 = await ledger.create_transaction(
        session, principal, TransactionCreate(kind="expense", amount="120000", currency="VND",
                                              category_id=food),
        raw_text="еда 120к, такси 45к", action=ctx)
    t2 = await ledger.create_transaction(
        session, principal, TransactionCreate(kind="expense", amount="45000", currency="VND",
                                              category_id=taxi),
        raw_text="еда 120к, такси 45к", action=ctx)
    row = await actions.finish_action(session, ctx)
    assert row.summary.replace("\xa0", " ") == "120 000 ₫ · Еда"
    assert ctx.transaction_ids == [t1.id, t2.id]
    assert t1.raw_text == "еда 120к, такси 45к" and t1.source == "web"
    res = await actions.undo_action(session, principal, ctx.id)
    assert res.status == "undone"
    await session.refresh(t1)
    await session.refresh(t2)
    assert t1.deleted_at is not None and t2.deleted_at is not None
    # own action path (no ctx passed)
    t3 = await ledger.create_transaction(
        session, principal, TransactionCreate(kind="expense", amount="1000", currency="VND",
                                              category_id=food))
    t3 = await ledger.update_transaction(session, principal, t3.id,
                                         TransactionPatch(version=1, amount="2000"))
    assert t3.version == 2 and t3.amount == Decimal("2000")
    last = await actions.last_undoable_action(session, user.id)
    assert last is not None and last.kind == "update_transaction"
    assert last.summary.replace("\xa0", " ") == "Изменено: 2 000 ₫ · Еда"


async def test_assign_account_and_estimate_from_rub_card(api, session, user):
    """«₽ → Карта RUB» default; USD on a RUB card is charged ≈ by rate."""
    food = await cat_id(session, user.id, "Еда")
    rub = await acc_id(session, user.id, "Карта RUB")
    r = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "990", "currency": "RUB", "category_id": food})
    assert r.json()["transaction"]["account"]["id"] == rub
    r = await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "10", "currency": "USD", "category_id": food,
        "account_id": rub})
    tx = r.json()["transaction"]
    assert tx["account_asset"] == "RUB" and tx["account_amount"] == "950"
    assert tx["is_estimated"] is True

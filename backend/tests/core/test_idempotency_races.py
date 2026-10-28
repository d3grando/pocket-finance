"""Idempotency (same key → same response, no duplicate) and races on a real Postgres:
every request below runs in its own connection and commits (live_api / live_session)."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.core.money import normalize
from app.models import AccountAsset, ActionLog, Merchant, Transaction
from app.services.merchants import service as merchants
from tests.core.conftest import acc_id, cat_id


async def _ids(live_session, user):  # type: ignore[no-untyped-def]
    async with live_session() as s:
        return {
            "food": await cat_id(s, user.id, "Еда"),
            "soft": await cat_id(s, user.id, "Софт и AI"),
            "карта": await acc_id(s, user.id, "Карта"),
            "kb": await acc_id(s, user.id, "Кошелёк"),
        }


async def _count_tx(live_session, user) -> int:  # type: ignore[no-untyped-def]
    async with live_session() as s:
        return int(await s.scalar(
            select(func.count()).where(Transaction.user_id == user.id,
                                       Transaction.deleted_at.is_(None))))


async def test_same_key_same_response_no_duplicate(api, session, user):
    food = await cat_id(session, user.id, "Еда")
    key = {"Idempotency-Key": str(uuid.uuid4())}
    body = {"kind": "expense", "amount": "85000", "currency": "VND", "category_id": food}
    r1 = await api.post("/transactions", json=body, headers=key)
    r2 = await api.post("/transactions", json=body, headers=key)
    assert r1.status_code == r2.status_code == 201
    assert r1.json() == r2.json()
    n = await session.scalar(select(func.count()).where(Transaction.user_id == user.id))
    assert n == 1
    other = await api.post("/transactions", json={**body, "amount": "90000"}, headers=key)
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_mismatch"
    # PATCH replay with the same key applies once
    tid = r1.json()["transaction"]["id"]
    pkey = {"Idempotency-Key": str(uuid.uuid4())}
    p1 = await api.patch(f"/transactions/{tid}", json={"version": 1, "amount": "95000"},
                         headers=pkey)
    p2 = await api.patch(f"/transactions/{tid}", json={"version": 1, "amount": "95000"},
                         headers=pkey)
    assert p1.status_code == p2.status_code == 200 and p1.json() == p2.json()
    assert p2.json()["transaction"]["version"] == 2


async def test_concurrent_duplicates_create_one(live_api, live_session, live_user):
    ids = await _ids(live_session, live_user)
    key = {"Idempotency-Key": str(uuid.uuid4())}
    body = {"kind": "expense", "amount": "45000", "currency": "VND", "category_id": ids["food"]}
    rs = await asyncio.gather(*[live_api.post("/transactions", json=body, headers=key)
                                for _ in range(6)])
    assert {r.status_code for r in rs} == {201}, [r.text for r in rs]
    assert len({r.json()["transaction"]["id"] for r in rs}) == 1
    assert len({r.json()["action_id"] for r in rs}) == 1
    assert await _count_tx(live_session, live_user) == 1
    async with live_session() as s:
        n = await s.scalar(select(func.count()).where(ActionLog.user_id == live_user.id,
                                                      ActionLog.idempotency_key == key[
                                                          "Idempotency-Key"]))
        assert n == 1


async def test_parallel_patch_same_version_one_wins(live_api, live_session, live_user):
    ids = await _ids(live_session, live_user)
    r = await live_api.post("/transactions", headers={"Idempotency-Key": str(uuid.uuid4())},
                            json={"kind": "expense", "amount": "100000", "currency": "VND",
                                  "category_id": ids["food"]})
    tid = r.json()["transaction"]["id"]
    rs = await asyncio.gather(*[
        live_api.patch(f"/transactions/{tid}", json={"version": 1, "amount": str(200000 + i)})
        for i in range(5)
    ])
    codes = sorted(x.status_code for x in rs)
    assert codes == [200, 409, 409, 409, 409], [x.text for x in rs]
    async with live_session() as s:
        tx = await s.get(Transaction, tid)
        assert tx.version == 2
        winner = next(x for x in rs if x.status_code == 200).json()["transaction"]["amount"]
        assert normalize(tx.amount) == winner


async def test_parallel_creates_keep_balance_exact(live_api, live_session, live_user):
    ids = await _ids(live_session, live_user)
    await live_api.put(f"/accounts/{ids['карта']}/opening",
                       json={"asset": "VND", "amount": "10000000"})
    rs = await asyncio.gather(*[
        live_api.post("/transactions", headers={"Idempotency-Key": str(uuid.uuid4())},
                      json={"kind": "expense", "amount": "10000", "currency": "VND",
                            "category_id": ids["food"]})
        for _ in range(20)
    ])
    assert all(r.status_code == 201 for r in rs)
    acc = (await live_api.get(f"/accounts/{ids['карта']}")).json()
    assert acc["assets"][0]["balance"] == str(10000000 - 20 * 10000)


async def test_parallel_first_mention_of_coin(live_api, live_session, live_user):
    """Two concurrent TON payments from a wallet without TON: one asset row, no error."""
    ids = await _ids(live_session, live_user)
    rs = await asyncio.gather(*[
        live_api.post("/transactions", headers={"Idempotency-Key": str(uuid.uuid4())},
                      json={"kind": "expense", "amount": "1.5", "currency": "TON",
                            "category_id": ids["soft"], "account_id": ids["kb"]})
        for _ in range(4)
    ])
    assert all(r.status_code == 201 for r in rs), [r.text for r in rs]
    async with live_session() as s:
        n = await s.scalar(select(func.count()).where(AccountAsset.account_id == ids["kb"],
                                                      AccountAsset.asset == "TON"))
        assert n == 1


async def test_parallel_undo_once(live_api, live_session, live_user):
    ids = await _ids(live_session, live_user)
    r = await live_api.post("/transactions", headers={"Idempotency-Key": str(uuid.uuid4())},
                            json={"kind": "expense", "amount": "1000", "currency": "VND",
                                  "category_id": ids["food"]})
    aid = r.json()["action_id"]
    rs = await asyncio.gather(*[
        live_api.post(f"/actions/{aid}/undo", headers={"Idempotency-Key": str(uuid.uuid4())})
        for _ in range(4)
    ])
    statuses = sorted(x.json()["status"] for x in rs)
    assert statuses == ["already_undone", "already_undone", "already_undone", "undone"]
    assert len({x.json()["undo_action_id"] for x in rs}) == 1
    assert await _count_tx(live_session, live_user) == 0


async def test_parallel_reconciliation_writes_one_adjustment(live_api, live_session, live_user):
    ids = await _ids(live_session, live_user)
    await live_api.put(f"/accounts/{ids['карта']}/opening",
                       json={"asset": "VND", "amount": "5320000"})
    rs = await asyncio.gather(*[
        live_api.post("/adjustments", headers={"Idempotency-Key": str(uuid.uuid4())},
                      json={"account_id": ids["карта"], "asset": "VND",
                            "actual_balance": "5200000"})
        for _ in range(4)
    ])
    kinds = sorted(r.json()["kind"] for r in rs)
    assert kinds == ["adjusted", "matches", "matches", "matches"], [r.text for r in rs]
    acc = (await live_api.get(f"/accounts/{ids['карта']}")).json()
    assert acc["assets"][0]["balance"] == "5200000"


async def test_parallel_merchant_learning_single_rule(live_session, live_user):
    ids = await _ids(live_session, live_user)

    async def learn_one() -> int:
        async with live_session() as s:
            m = await merchants.learn(s, live_user.id, name=None, handle="@racer",
                                      category_id=ids["soft"])
            await s.commit()
            return m.id

    got = await asyncio.gather(*[learn_one() for _ in range(5)])
    assert len(set(got)) == 1
    async with live_session() as s:
        m = await s.scalar(select(Merchant).where(Merchant.user_id == live_user.id,
                                                  Merchant.handle == "racer"))
        assert m.hits == 5


async def test_parallel_assign_and_patch_no_lost_update(live_api, live_session, live_user):
    """«С какого?» answered from two clients at once: exactly one assignment wins."""
    ids = await _ids(live_session, live_user)
    r = await live_api.post("/transactions", headers={"Idempotency-Key": str(uuid.uuid4())},
                            json={"kind": "expense", "amount": "3", "currency": "USDT",
                                  "category_id": ids["soft"]})
    tx = r.json()["transaction"]
    assert tx["status"] == "needs_account"
    rs = await asyncio.gather(
        live_api.post(f"/transactions/{tx['id']}/account",
                      json={"account_id": ids["kb"], "version": tx["version"]}),
        live_api.post(f"/transactions/{tx['id']}/account",
                      json={"account_id": None, "version": tx["version"]}),
    )
    assert sorted(x.status_code for x in rs) == [200, 409]
    async with live_session() as s:
        t = await s.get(Transaction, tx["id"])
        assert t.status == "posted" and t.version == 2
        assert t.amount == Decimal("3")

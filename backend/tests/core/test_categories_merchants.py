"""Categories: seed, CRUD, 🆕 model-created, resolve/similar, merge with moving operations
(undoable), business scope; merchant memory."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.models import Category, Merchant, Transaction
from app.models.enums import ActionKind
from app.schemas.categories import CategoryCreate
from app.services.categories import service as categories
from app.services.categories.seed import DEFAULT_CATEGORIES, seed_defaults
from app.services.ledger import actions
from app.services.merchants import service as merchants
from tests.core.conftest import acc_id, cat_id, idem


async def test_seed_has_default_set_and_is_idempotent(api, session, user):
    names = {(c["name"], c["kind"]) for c in (await api.get("/categories")).json()}
    for n in ("Закупка товара", "Переводы людям", "Серверы/инфра", "Софт и AI", "Еда"):
        assert (n, "expense") in names
    for n in ("Клиенты", "Продажи", "Зарплата", "Прочее"):
        assert (n, "income") in names
    cats = {c["name"]: c for c in (await api.get("/categories", params={"kind": "expense"})
                                   ).json()}
    assert cats["Закупка товара"]["is_business"] and not cats["Переводы людям"]["is_business"]
    assert cats["Еда"]["is_system"] and cats["Еда"]["icon"] == "UtensilsCrossed"
    await seed_defaults(session, user.id)
    n = await session.scalar(select(func.count()).where(Category.user_id == user.id))
    assert n == len(DEFAULT_CATEGORIES)
    accs = [a["name"] for a in (await api.get("/accounts")).json()]
    assert accs == ["Карта", "Наличные", "Кошелёк", "Биржа", "Копилка", "Сейф", "Карта RUB"]


async def test_crud_and_new_flag(api, session, user, principal):
    r = await api.post("/categories", json={"name": "Спортзал", "kind": "expense"})
    assert r.status_code == 201
    c = r.json()
    assert c["icon"] == "HeartPulse" and c["is_new"] is False and "спортзал" in c["aliases"]
    assert (await api.post("/categories", json={"name": "спортзал", "kind": "expense"})
            ).status_code == 409
    # model-created category → 🆕, undoable through its action
    ctx = await actions.begin_action(session, principal, ActionKind.create_category)
    new = await categories.create_category(
        session, principal, CategoryCreate(name="Котик", kind="expense"), is_new=True,
        action=ctx)
    await actions.finish_action(session, ctx)
    assert new.is_new and new.icon == "PawPrint"
    out = (await api.get("/categories", params={"with_counts": True})).json()
    kot = next(x for x in out if x["id"] == new.id)
    assert kot["is_new"] and kot["tx_count"] == 0
    ok = await api.patch(f"/categories/{new.id}", json={"version": new.version, "is_new": False})
    assert ok.status_code == 200 and ok.json()["is_new"] is False
    # confirmed (changed) after the action → undo of the creation conflicts
    with pytest.raises(AppError) as e:
        await actions.undo_action(session, principal, ctx.id)
    assert e.value.code == "undo_conflict"
    prochee = await cat_id(session, user.id, "Прочее")
    assert (await api.delete(f"/categories/{prochee}")).status_code == 422
    d = await api.delete(f"/categories/{c['id']}")
    assert d.status_code == 200
    assert c["id"] not in [x["id"] for x in (await api.get("/categories")).json()]


async def test_undo_model_created_category(session, user, principal):
    ctx = await actions.begin_action(session, principal, ActionKind.create_category)
    c = await categories.create_category(
        session, principal, CategoryCreate(name="Пиво", kind="expense"), is_new=True, action=ctx)
    await actions.finish_action(session, ctx)
    cid = c.id
    res = await actions.undo_action(session, principal, ctx.id)
    assert res.status == "undone"
    assert await session.scalar(select(Category).where(Category.id == cid)) is None
    redo = await actions.undo_action(session, principal, res.undo_action_id)
    assert redo.status == "undone"
    again = await session.scalar(select(Category).where(Category.id == cid))
    assert again is not None and again.name == "Пиво" and again.is_new


async def test_resolve_and_similar(session, user):
    m = await categories.resolve_category(session, user.id, "Жилье", "expense")
    assert m.exact and m.category.name == "Жильё"
    m = await categories.resolve_category(session, user.id, "обед", "expense")
    assert m.exact and m.category.name == "Еда"
    m = await categories.resolve_category(session, user.id, "Подписка", "expense")
    assert m.exact and m.category.name == "Подписки"
    m = await categories.resolve_category(session, user.id, "подписочки", "expense")
    assert not m.exact and m.category is not None and m.category.name == "Подписки"
    assert all(s >= 0.45 for _, s in m.candidates)
    m = await categories.resolve_category(session, user.id, "квантовая физика", "expense")
    assert m.category is None and m.candidates == []
    m = await categories.resolve_category(session, user.id, "прочее", "income")
    assert m.category.kind == "income"


async def test_merge_moves_operations_and_undo(api, session, user, principal):
    src = (await api.post("/categories", json={"name": "Кафешки", "kind": "expense",
                                               "aliases": ["кафешка"]})).json()
    food = await cat_id(session, user.id, "Еда")
    t = (await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "90000", "currency": "VND", "category_id": src["id"]})
         ).json()["transaction"]
    await merchants.learn(session, user.id, name="Highlands", handle=None,
                          category_id=src["id"])
    r = await api.post("/categories/merge", json={"source_id": src["id"], "target_id": food})
    assert r.status_code == 200, r.text
    tgt = r.json()
    assert "кафешки" in tgt["aliases"] and "кафешка" in tgt["aliases"]
    assert (await api.get(f"/transactions/{t['id']}")).json()["category"]["id"] == food
    assert src["id"] not in [c["id"] for c in (await api.get("/categories")).json()]
    hl = await merchants.match_merchant(session, user.id, name="highlands")
    assert hl.category_id == food
    # a reference to the merged category resolves to the target
    m = await categories.resolve_category(session, user.id, "Кафешки", "expense")
    assert m.category.id == food
    # create with the old id lands in the target
    t2 = (await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "1", "currency": "VND", "category_id": src["id"]})
          ).json()["transaction"]
    assert t2["category"]["id"] == food
    # undo the merge
    merge_action = next(a for a in (await api.get("/actions")).json()
                        if a["kind"] == "merge_categories")
    u = await api.post(f"/actions/{merge_action['id']}/undo", headers=idem())
    assert u.status_code == 200, u.text
    assert (await api.get(f"/transactions/{t['id']}")).json()["category"]["id"] == src["id"]
    assert src["id"] in [c["id"] for c in (await api.get("/categories")).json()]
    await session.refresh(hl)
    assert hl.category_id == src["id"]
    income = await cat_id(session, user.id, "Зарплата", "income")
    bad = await api.post("/categories/merge", json={"source_id": src["id"], "target_id": income})
    assert bad.status_code == 422


async def test_business_flag_follows_category(api, session, user):
    c = (await api.post("/categories", json={"name": "Домены", "kind": "expense"})).json()
    t = (await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "12", "currency": "USD", "category_id": c["id"],
        "account_none": True})).json()["transaction"]
    assert t["is_business"] is False
    r = await api.patch(f"/categories/{c['id']}", json={"version": c["version"],
                                                        "is_business": True})
    assert r.status_code == 200
    tx = await session.scalar(select(Transaction).where(Transaction.id == t["id"]))
    await session.refresh(tx)
    assert tx.is_business is True
    biz = (await api.get("/transactions", params={"is_business": True})).json()
    assert [x["id"] for x in biz["items"]] == [t["id"]]


async def test_suggest(api, session, user):
    soft = await cat_id(session, user.id, "Софт и AI")
    t = (await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "20", "currency": "USD", "category_id": soft,
        "account_none": True, "description": "подписка claude"})).json()["transaction"]
    s = (await api.get("/categories/suggest", params={"transaction_id": t["id"]})).json()
    assert soft not in [x["category"]["id"] for x in s["similar"]]
    assert s["similar"][0]["category"]["name"] == "Подписки"
    assert all(c["kind"] == "expense" for c in s["all"])
    q = (await api.get("/categories/suggest", params={"q": "такси"})).json()
    assert q["similar"][0]["category"]["name"] == "Такси/транспорт"


async def test_merchant_memory(api, session, user):
    srv = await cat_id(session, user.id, "Серверы/инфра")
    r = await api.post("/merchants", json={"name": "vast.ai", "aliases": ["Васт", "vast"],
                                           "category_id": srv})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["category_name"] == "Серверы/инфра" and m["aliases"] == ["васт", "vast"]
    found = await merchants.match_merchant(session, user.id, name="ВАСТ")
    assert found is not None and found.id == m["id"]
    bank = await acc_id(session, user.id, "Карта")
    t = (await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "815", "currency": "USD", "category_id": srv,
        "merchant_id": m["id"], "account_id": bank})).json()["transaction"]
    assert t["title"] == "vast.ai" and t["merchant"]["name"] == "vast.ai"
    mm = (await api.get("/merchants", params={"q": "vast"})).json()[0]
    assert mm["hits"] == 1 and mm["last_used_at"]
    # merchant default account is used when no account is given
    p = await api.patch(f"/merchants/{m['id']}", json={"default_account_id": bank})
    assert p.status_code == 200 and p.json()["default_account_id"] == bank
    t2 = (await api.post("/transactions", headers=idem(), json={
        "kind": "expense", "amount": "5", "currency": "USD", "category_id": srv,
        "merchant_id": m["id"]})).json()["transaction"]
    assert t2["account"]["id"] == bank and t2["is_estimated"] is True
    # manual category change → the rule follows
    soft = await cat_id(session, user.id, "Софт и AI")
    await api.patch(f"/transactions/{t2['id']}", json={"version": t2["version"],
                                                       "category_id": soft})
    assert (await merchants.match_merchant(session, user.id, name="vast")).category_id == soft
    dup = await api.post("/merchants", json={"name": "VAST.AI"})
    assert dup.status_code == 409
    learned = await merchants.learn(session, user.id, name=None, handle="@alice",
                                    category_id=soft)
    assert learned.name == "@alice" and learned.handle == "alice" and learned.hits == 1
    same = await merchants.learn(session, user.id, name=None, handle="alice", category_id=None)
    assert same.id == learned.id and same.hits == 2 and same.category_id == soft
    d = await api.delete(f"/merchants/{m['id']}")
    assert d.status_code == 200
    assert await session.scalar(select(Merchant).where(Merchant.id == m["id"])) is None
    assert (await api.get(f"/transactions/{t['id']}")).json()["merchant"] is None

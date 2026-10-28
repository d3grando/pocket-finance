"""Integration: the assistant pipeline on the REAL parser and the REAL ledger /
analytics services (no fake_services, no parser stub); only the LLM is the fake server."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.core.timeutil import local_today
from app.models import Category, CurrencyRate, Merchant, Transaction


async def post(client, text: str) -> dict:
    r = await client.post("/assistant/messages", json={"text": text},
                          headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    return json.loads(r.text.replace(" ", " "))


async def live(session, uid) -> list[Transaction]:
    return list((await session.scalars(
        select(Transaction).where(Transaction.user_id == uid, Transaction.deleted_at.is_(None))
        .order_by(Transaction.id))).all())


async def test_coffee_parser_route_and_undo(client, session, user, llm):
    data = await post(client, "кофе 85к")
    assert data["route"] == "parser" and llm.requests == []
    [card] = data["cards"]
    assert card["kind"] == "created" and card["title"] == "85 000 ₫ · Кофе", card
    [tx] = await live(session, user.id)
    assert tx.amount == Decimal(85000) and tx.currency == "VND" and tx.account_id is not None
    r = await client.post(f"/actions/{card['action_id']}/undo",
                          headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    assert await live(session, user.id) == []


async def test_undo_by_text(client, session, user, llm):
    await post(client, "такси 45к")
    assert len(await live(session, user.id)) == 1
    llm.intents({"type": "undo"})
    data = await post(client, "отмени")
    assert data["cards"][0]["kind"] == "undone", data
    assert await live(session, user.id) == []


async def test_actual_charge_fixes_estimate_at_once(client, session, user, llm):
    today = local_today("Asia/Ho_Chi_Minh")
    session.add(CurrencyRate(base="USD", quote="VND", rate=Decimal("26000"), rate_date=today,
                             kind="sell", source="vietcombank", fetched_at=datetime.now(UTC)))
    infra = await session.scalar(select(Category).where(Category.user_id == user.id,
                                                        Category.name == "Серверы/инфра"))
    session.add(Merchant(user_id=user.id, name="vast.ai", aliases=["васт", "vast"],
                         category_id=infra.id, hits=3))
    await session.flush()
    llm.intents({"type": "create_transaction", "kind": "expense", "amount": "820",
                 "amount_raw": "820$", "currency": "USD", "merchant_ref": "vast.ai",
                 "category_ref": "Серверы/инфра", "account_ref": "Карта"})
    data = await post(client, "васт 820$ с карты")
    assert data["cards"][0]["kind"] == "created", data
    [tx] = await live(session, user.id)
    assert tx.is_estimated and tx.account_asset == "VND"
    assert tx.account_amount == Decimal("21320000")
    assert tx.merchant_id is not None, (data["route"], data["cards"][0]["title"])
    llm.intents({"type": "update_transaction",
                 "target": {"kind": "search",
                            "search": {"merchant_ref": "васт", "sort": "date_desc", "limit": 1}},
                 "changes": {"account_amount": "21300000", "account_amount_raw": "21.3м"}})
    data = await post(client, "васт списали 21.3м")
    [card] = data["cards"]
    assert card["kind"] == "updated", card  # applied immediately, no preview
    await session.refresh(tx)
    assert tx.account_amount == Decimal(21300000) and not tx.is_estimated

"""Recurring reminders (schedule math, CRUD REST, record/skip) and search with filters."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import AuditLog, Transaction
from app.schemas.transactions import TransactionFilter, TransactionOut
from app.services.recurring import service as REC
from app.services.stats import search as SR


def _r(freq: str, due: date, **kw):  # type: ignore[no-untyped-def]
    return SimpleNamespace(frequency=freq, next_due_on=due, interval=kw.pop("interval", 1),
                           day_of_month=kw.pop("dom", None), weekday=kw.pop("wd", None),
                           month=kw.pop("month", None))  # fmt: skip


def test_next_due_math():
    m31 = _r("monthly", date(2026, 1, 31), dom=31)
    assert REC.next_due(m31, date(2026, 1, 31)) == date(2026, 2, 28)
    assert REC.next_due(m31, date(2026, 2, 28)) == date(2026, 3, 31)
    assert REC.next_due(m31, date(2028, 1, 31)) == date(2028, 2, 29)  # leap year
    m5 = _r("monthly", date(2026, 10, 5), dom=5)
    assert REC.next_due(m5, date(2026, 10, 3)) == date(2026, 10, 5)  # strictly after «after»
    assert REC.next_due(m5, date(2026, 10, 5)) == date(2026, 11, 5)
    assert REC.next_due(m5, date(2026, 12, 5)) == date(2027, 1, 5)
    q = _r("monthly", date(2026, 10, 5), dom=5, interval=3)
    assert REC.next_due(q, date(2026, 10, 5)) == date(2027, 1, 5)
    w = _r("weekly", date(2026, 10, 26), wd=0)  # Mondays
    assert REC.next_due(w, date(2026, 10, 26)) == date(2026, 11, 2)
    w2 = _r("weekly", date(2026, 10, 26), wd=0, interval=2)
    assert REC.next_due(w2, date(2026, 10, 26)) == date(2026, 11, 9)
    d = _r("daily", date(2026, 10, 26), interval=3)
    assert REC.next_due(d, date(2026, 10, 26)) == date(2026, 10, 29)
    y = _r("yearly", date(2026, 2, 28), dom=29, month=2)
    assert REC.next_due(y, date(2026, 2, 28)) == date(2027, 2, 28)
    assert REC.next_due(y, date(2027, 2, 28)) == date(2028, 2, 29)
    assert REC.latest_due(_r("monthly", date(2026, 9, 5), dom=5), date(2026, 11, 20)) == date(
        2026, 11, 5
    )


async def test_recurring_crud_routes(client, session, build):
    cat = await build.category("Серверы/инфра", is_business=True)
    inc = await build.category("Клиенты", "income")
    body = {"title": "Hetzner", "amount": "45", "currency": "usd", "category_id": cat.id,
            "frequency": "monthly", "next_due_on": "2026-11-05"}  # fmt: skip
    r = await client.post("/recurring", json=body)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["day_of_month"] == 5 and rec["currency"] == "USD" and rec["amount"] == "45"
    assert rec["remind_time"] == "10:00:00" and rec["version"] == 1
    rid = rec["id"]
    assert (await client.post("/recurring", json={**body, "amount": 45.5})).status_code == 422
    bad = await client.post("/recurring", json={**body, "category_id": inc.id})
    assert bad.status_code == 422  # income category for an expense reminder
    r = await client.patch(f"/recurring/{rid}", json={"version": 1, "amount": "50"})
    assert r.status_code == 200 and r.json()["amount"] == "50" and r.json()["version"] == 2
    r = await client.patch(f"/recurring/{rid}", json={"version": 1, "amount": "60"})
    assert r.status_code == 409 and r.json()["error"]["details"]["current_version"] == 2
    r = await client.post(f"/recurring/{rid}/skip")
    assert r.status_code == 200 and r.json()["next_due_on"] == "2026-12-05"
    r = await client.get("/recurring")
    assert [x["id"] for x in r.json()] == [rid]
    audit = (
        await session.scalars(select(AuditLog).where(AuditLog.entity_type == "recurring"))
    ).all()
    assert len(audit) >= 3
    r = await client.delete(f"/recurring/{rid}")
    assert r.status_code == 200 and (await client.get("/recurring")).json() == []
    assert (await client.delete(f"/recurring/{rid}")).status_code == 404


async def test_record_creates_transaction_via_ledger(client, session, build, monkeypatch):
    """[Записать] → ledger.create_transaction (ledger) with recurring_id; next_due_on moves."""
    from app.services.ledger import transactions as ledger

    cat = await build.category("Жильё")
    acc = await build.account("Карта", "VND")
    r = await client.post("/recurring", json={
        "title": "Аренда", "amount": "11000000", "currency": "VND", "category_id": cat.id,
        "account_id": acc.id, "frequency": "monthly", "day_of_month": 31,
        "next_due_on": "2026-10-31", "remind_time": "09:30:00"})  # fmt: skip
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    seen = {}

    async def fake_create(s, principal, data, **kw):  # type: ignore[no-untyped-def]
        seen["data"] = data
        t = Transaction(
            user_id=principal.user_id, kind=data.kind, status="posted",
            occurred_at=datetime(2026, 10, 31, 5, tzinfo=UTC), occurred_on=data.occurred_on,
            amount=data.amount, currency=data.currency, account_id=data.account_id,
            category_id=data.category_id, recurring_id=data.recurring_id,
            source=principal.source, created_action_id=uuid.uuid4(),
        )  # fmt: skip
        s.add(t)
        await s.flush()
        await s.refresh(t)
        return t

    async def fake_out(s, t):  # type: ignore[no-untyped-def]
        return TransactionOut(
            id=t.id, kind=t.kind, status=t.status, occurred_at=t.occurred_at,
            occurred_on=t.occurred_on, amount=t.amount, currency=t.currency, source=t.source,
            version=t.version, created_at=t.created_at, updated_at=t.updated_at,
            recurring_id=t.recurring_id, created_action_id=t.created_action_id,
        )  # fmt: skip

    monkeypatch.setattr(ledger, "create_transaction", fake_create)
    monkeypatch.setattr(ledger, "to_out", fake_out)
    body = {"amount_text": "11.5м", "occurred_on": "2026-10-31"}
    r = await client.post(f"/recurring/{rid}/record", json=body,
                          headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 201, r.text
    assert r.json()["transaction"]["recurring_id"] == rid and r.json()["action_id"]
    d = seen["data"]
    assert d.amount == Decimal(11_500_000) and d.recurring_id == rid and d.category_id == cat.id
    assert d.account_id == acc.id and d.description == "Аренда"
    rec = (await client.get("/recurring")).json()[0]
    assert rec["next_due_on"] == "2026-11-30"  # 31 → last day of November
    r = await client.post(f"/recurring/{rid}/record", json={"amount": "0"},
                          headers={"Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 422


# ----------------------------------------------------------------------------- search


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        (">100$", (Decimal(100), None, "USD")),
        ("150к", (Decimal(150000), Decimal(150000), "VND")),
        ("85", (Decimal(85000), Decimal(85000), "VND")),
        ("до 50 usdt", (None, Decimal(50), "USDT")),
        ("от 1.5м", (Decimal(1_500_000), None, "VND")),
        ("<20 баксов", (None, Decimal(20), "USD")),
        ("$15", (Decimal(15), Decimal(15), "USD")),
        ("кофе", None),
        ("highlands 2", None),
    ],
)
def test_parse_amount_query(q, expected):
    aq = SR.parse_amount_query(q)
    if expected is None:
        assert aq is None
    else:
        assert (aq.min_amount, aq.max_amount, aq.currency) == expected


async def test_search_transactions_filters(session, dataset):
    uid = dataset.b.user_id
    ids = lambda rows: sorted(t.id for t in rows)  # noqa: E731
    tx = dataset.tx
    rows, total, totals = await SR.search_transactions(session, uid, TransactionFilter(
        date_from=date(2026, 10, 1), date_to=date(2026, 10, 31), kinds=["expense"]))  # fmt: skip
    assert total == 7  # deleted t13 excluded; t14 (no rate) is listed but not summed
    assert ids(rows) == sorted(tx[k].id for k in ("t1", "t2", "t3", "t4", "t11", "t12", "t14"))
    assert totals.expense_vnd == Decimal(1425000) and totals.expense_usd == Decimal(57)
    rows, total, _ = await SR.search_transactions(
        session, uid, TransactionFilter(q=">10$", amount_currency=None)
    )
    want = ("t3", "t4", "t6", "t7", "t9", "t10", "t12", "t15", "t16")  # amount_usd ≥ 10
    assert ids(rows) == sorted(tx[k].id for k in want)
    rows, *_ = await SR.search_transactions(session, uid, TransactionFilter(q="highl"))
    assert ids(rows) == [tx["t1"].id]
    rows, *_ = await SR.search_transactions(session, uid, TransactionFilter(q="васт"))
    assert ids(rows) == [tx["t3"].id]  # merchant alias
    rows, *_ = await SR.search_transactions(session, uid, TransactionFilter(q="кофе"))
    assert ids(rows) == sorted([tx["t2"].id, tx["t14"].id, tx["t17"].id])  # category name
    rows, *_ = await SR.search_transactions(
        session, uid, TransactionFilter(currency="TON", account_ids=[dataset.b.accs["Кошелёк"].id])
    )
    assert ids(rows) == [tx["t12"].id]
    rows, *_ = await SR.search_transactions(
        session,
        uid,
        TransactionFilter(
            min_amount=Decimal(1_000_000), amount_currency="VND", kinds=["income"]
        ),  # fmt: skip
    )
    assert ids(rows) == sorted([tx["t9"].id, tx["t10"].id, tx["t16"].id])
    rows, *_ = await SR.search_transactions(
        session,
        uid,
        TransactionFilter(
            is_business=True, sort="amount_desc", limit=2, amount_currency="USD"
        ),  # fmt: skip
    )
    assert [t.id for t in rows] == [tx["t9"].id, tx["t3"].id]
    rows, *_ = await SR.search_transactions(session, uid, TransactionFilter(status="needs_account"))
    assert ids(rows) == [tx["t3"].id]


async def test_quick_search_route(client, dataset):
    r = await client.get("/search", params={"q": "еда"})
    assert r.status_code == 200, r.text
    hits = r.json()["hits"]
    assert hits[0]["type"] == "category" and hits[0]["title"] == "Еда"
    assert hits[0]["route"] == f"/operations?category_id={dataset.b.cats['Еда'].id}"
    r = await client.get("/search", params={"q": "vast"})
    types = [h["type"] for h in r.json()["hits"]]
    assert "merchant" in types and "transaction" in types
    tx_hit = next(h for h in r.json()["hits"] if h["type"] == "transaction")
    assert tx_hit["route"] == f"/operations?tx={dataset.tx['t3'].id}"
    assert tx_hit["title"] == "Серверы/инфра · vast.ai"
    r = await client.get("/search", params={"q": "карта"})
    assert r.json()["hits"][0]["type"] == "account"
    r = await client.get("/search", params={"q": ">100$", "kind": "expense", "limit": 5})
    assert r.status_code == 200 and r.json()["hits"] == []
    r = await client.get("/search", params={"preset": "custom", "date_from": "2026-09-01",
                                            "date_to": "2026-09-30", "category_id":
                                            dataset.b.cats["Кофе"].id})  # fmt: skip
    hits = r.json()["hits"]
    assert [h["id"] for h in hits] == [dataset.tx["t17"].id]
    assert (await client.get("/search")).status_code == 422

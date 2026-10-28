"""Regressions: a reminder occurrence is recorded/skipped once (double tap, stale
keyboard, parallel requests), «Записать» is undoable as a whole, a reminder without a category
records into «Прочее», reports: «≈» + NBSP, «Прислать сейчас» without Telegram → 422."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import ValidationFailed
from app.core.principal import Principal
from app.core.timeutil import local_today
from app.models import (
    Account,
    Category,
    RecurringTransaction,
    Transaction,
    User,
    UserSettings,
)
from app.schemas.recurring import RecurringRecordIn
from app.services.rates import providers as P
from app.services.recurring import service as REC
from app.services.reports import service as RP
from tests.analytics.conftest import NOW, add_rate


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def _no_rate_providers(rx) -> None:  # type: ignore[no-untyped-def]
    rx.get(P.VCB_URL).respond(500)
    rx.get(P.OPEN_ER_URL.format(base="USD")).respond(500)


async def _reminder(build, *, category: str | None = "Жильё", due=date(2026, 11, 5)):  # type: ignore[no-untyped-def]
    cat = build.cats.get(category) if category else None
    if category and cat is None:
        cat = await build.category(category)
    acc = build.accs.get("Карта") or await build.account("Карта", "VND")
    r = RecurringTransaction(
        user_id=build.user_id, title="Аренда", kind="expense", amount=Decimal(11_000_000),
        currency="VND", account_id=acc.id, category_id=cat.id if cat else None,
        frequency="monthly", interval=1, day_of_month=due.day, next_due_on=due,
        remind_time=time(10, 0), is_active=True, version=1,
    )  # fmt: skip
    build.session.add(r)
    await build.session.flush()
    return r


async def _count_txs(session: AsyncSession, rid: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).where(
                Transaction.recurring_id == rid, Transaction.deleted_at.is_(None)
            )
        )
        or 0
    )


async def test_record_once_per_occurrence_and_undo_brings_reminder_back(client, session, build):
    """s5c/t05 R1+R5: two taps of «Записать» on one reminder → one operation; «Отменить»
    deletes it AND returns the reminder to its due date."""
    today = local_today(None)
    await add_rate(session, "USD", "VND", "26020", today, "sell", "vietcombank")
    r = await _reminder(build, due=date(2026, 10, 27))
    rid = r.id  # a 409 rolls the session back (objects expire) — keep plain values
    url = f"/recurring/{rid}/record"
    due = {"due_on": "2026-10-27"}
    k1 = _key()
    first = await client.post(url, params=due, json={}, headers=k1)
    assert first.status_code == 201, first.text
    tx_id, action_id = first.json()["transaction"]["id"], first.json()["action_id"]
    again = await client.post(url, params=due, json={}, headers=k1)  # network retry: same key
    assert again.status_code == 201 and again.json()["transaction"]["id"] == tx_id
    second = await client.post(url, params=due, json={}, headers=_key())  # 2nd tap: new key
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "already_exists"
    assert second.json()["error"]["details"]["next_due_on"] == "2026-11-27"
    skip = await client.post(f"/recurring/{rid}/skip", params=due)  # «Пропустить» on the old kb
    assert skip.status_code == 409
    assert await _count_txs(session, rid) == 1
    rec = (await client.get("/recurring")).json()[0]
    assert rec["next_due_on"] == "2026-11-27" and rec["version"] == 2

    undo = await client.post(f"/actions/{action_id}/undo", headers=_key())
    assert undo.status_code == 200, undo.text
    assert undo.json()["status"] == "undone"
    assert await _count_txs(session, rid) == 0
    rec = (await client.get("/recurring")).json()[0]
    assert rec["next_due_on"] == "2026-10-27"  # the reminder is back, not lost
    # the occurrence is open again → recording it works
    again = await client.post(url, params=due, json={}, headers=_key())
    assert again.status_code == 201, again.text
    assert await _count_txs(session, rid) == 1


async def test_skip_once_per_occurrence(client, session, build):
    """two taps of «Пропустить» → the schedule moves one period, not two."""
    rid = (await _reminder(build)).id
    due = {"due_on": "2026-11-05"}
    one = await client.post(f"/recurring/{rid}/skip", params=due)
    assert one.status_code == 200 and one.json()["next_due_on"] == "2026-12-05"
    two = await client.post(f"/recurring/{rid}/skip", params=due)
    assert two.status_code == 409 and two.json()["error"]["code"] == "already_exists"
    rec = (await client.get("/recurring")).json()[0]
    assert rec["next_due_on"] == "2026-12-05"
    # without due_on (web) a deliberate second skip is still possible
    web = await client.post(f"/recurring/{rid}/skip")
    assert web.status_code == 200 and web.json()["next_due_on"] == "2027-01-05"


async def test_telegram_double_tap_without_due_on(session, build, make_principal, rx):
    """An old bot that sends r:{id} without the date: a 2nd tap within a minute → 409."""
    _no_rate_providers(rx)
    r = await _reminder(build)
    tg = make_principal("telegram")
    tx = await REC.record(session, tg, r.id, RecurringRecordIn(occurred_on=date(2026, 10, 27)))
    assert tx.recurring_id == r.id
    with pytest.raises(REC.AlreadyHandled):
        await REC.record(session, tg, r.id, RecurringRecordIn(occurred_on=date(2026, 10, 27)))
    with pytest.raises(REC.AlreadyHandled):
        await REC.skip(session, tg, r.id)
    assert await _count_txs(session, r.id) == 1
    assert r.next_due_on == date(2026, 12, 5)
    # the web may catch up several periods on purpose
    await REC.record(session, make_principal("web"), r.id, RecurringRecordIn())
    assert await _count_txs(session, r.id) == 2 and r.next_due_on == date(2027, 1, 5)


async def test_no_category_records_into_prochee(session, build, make_principal, rx):
    """a reminder without a category no longer fails «Записать» / «Другая сумма» (422)."""
    _no_rate_providers(rx)
    r = await _reminder(build, category=None)
    web = make_principal("web")
    with pytest.raises(ValidationFailed):  # no «Прочее» either → a clear 422
        await REC.record(session, web, r.id, RecurringRecordIn())
    other = await build.category("Прочее")
    tx = await REC.record(session, web, r.id, RecurringRecordIn(amount_text="350к"))
    assert tx.category_id == other.id and tx.amount == Decimal(350_000)
    assert r.category_id is None  # the reminder itself is not changed


async def test_parallel_records_are_serialized(engine, rx):
    """5 parallel «Записать» of one occurrence → exactly one operation; 3 parallel
    catch-up records (no due_on) → 3 operations and no lost next_due_on/version updates."""
    _no_rate_providers(rx)
    async with AsyncSession(engine, expire_on_commit=False) as s:
        u = User(username=f"par-{uuid.uuid4().hex[:10]}")
        s.add(u)
        await s.flush()
        s.add(UserSettings(user_id=u.id))
        cat = Category(user_id=u.id, name="Жильё", kind="expense", version=1)
        acc = Account(user_id=u.id, name="Карта", kind="bank", currency="VND", version=1)
        s.add_all([cat, acc])
        await s.flush()
        r = RecurringTransaction(
            user_id=u.id, title="Аренда", kind="expense", amount=Decimal(1_000_000),
            currency="VND", account_id=acc.id, category_id=cat.id, frequency="monthly",
            interval=1, day_of_month=5, next_due_on=date(2026, 11, 5), remind_time=time(10),
            is_active=True, version=1,
        )  # fmt: skip
        s.add(r)
        await s.commit()
        uid, rid = u.id, r.id
    p = Principal(user_id=uid, kind="session", source="web")

    async def one(due: date | None) -> bool:
        async with AsyncSession(engine, expire_on_commit=False) as s:
            try:
                body = RecurringRecordIn(occurred_on=date(2026, 10, 27))
                await REC.record(s, p, rid, body, due_on=due)
                await s.commit()
                return True
            except REC.AlreadyHandled:
                await s.rollback()
                return False

    try:
        first = await asyncio.gather(*(one(date(2026, 11, 5)) for _ in range(5)))
        assert first.count(True) == 1
        assert all(await asyncio.gather(*(one(None) for _ in range(3))))
        async with AsyncSession(engine) as s:
            n = await s.scalar(select(func.count()).where(Transaction.recurring_id == rid))
            row = await s.get(RecurringTransaction, rid)
            assert n == 4
            assert row.next_due_on == date(2027, 3, 5) and row.version == 5
    finally:
        async with AsyncSession(engine) as s:
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()


async def test_parallel_patch_one_wins(engine):
    """parallel PATCH with the same version → one 200, the others version_conflict."""
    from app.core.errors import VersionConflict
    from app.schemas.recurring import RecurringPatch

    async with AsyncSession(engine, expire_on_commit=False) as s:
        u = User(username=f"pp-{uuid.uuid4().hex[:10]}")
        s.add(u)
        await s.flush()
        r = RecurringTransaction(
            user_id=u.id, title="Hetzner", kind="expense", amount=Decimal(45), currency="USD",
            frequency="monthly", interval=1, day_of_month=5, next_due_on=date(2026, 11, 5),
            remind_time=time(10), is_active=True, version=1,
        )  # fmt: skip
        s.add(r)
        await s.commit()
        uid, rid = u.id, r.id
    p = Principal(user_id=uid, kind="session", source="web")

    async def one(i: int) -> bool:
        async with AsyncSession(engine, expire_on_commit=False) as s:
            try:
                await REC.update_recurring(
                    s, p, rid, RecurringPatch(version=1, amount=Decimal(50 + i))
                )
                await s.commit()
                return True
            except VersionConflict:
                await s.rollback()
                return False

    try:
        res = await asyncio.gather(*(one(i) for i in range(6)))
        assert res.count(True) == 1
        async with AsyncSession(engine) as s:
            assert (await s.get(RecurringTransaction, rid)).version == 2
    finally:
        async with AsyncSession(engine) as s:
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()


async def test_send_report_without_telegram_is_422(client, session, user, monkeypatch):
    st = await session.get(UserSettings, user.id)
    st.telegram_user_id = None
    await session.flush()
    monkeypatch.setattr(get_settings(), "telegram_allowed_ids", [111, 222])
    r = await client.post("/reports/monthly/send")
    assert r.status_code == 422, r.text
    assert "/start" in r.json()["error"]["message"]
    # the single allowed owner id is a valid target even before /start (claim uses it too)
    monkeypatch.setattr(get_settings(), "telegram_allowed_ids", [123456789])
    assert (await client.post("/reports/monthly/send")).status_code == 202


async def test_monthly_report_approx_uses_nbsp(session, dataset):
    rep = await RP.build_report(session, dataset.b.user_id, "monthly", date(2026, 10, 27), now=NOW)
    flow = next(ln for ln in rep.text_html.splitlines() if ln.startswith("Поток"))
    assert "≈ " in flow and "≈ " not in flow

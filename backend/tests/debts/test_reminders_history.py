"""Debt reminders (docs/DEBTS.md §9), the person card history (balance_after), the service API
used by the dispatcher, and a real race of two forgives (FOR UPDATE on the debt asset)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.timeutil import local_today
from app.models import Debt, NotificationOutbox, UserSettings
from app.models.enums import ActionKind
from app.schemas.debts import DebtOpCreate
from app.services.debts import service as debts
from app.services.debts.reminders import enqueue_debt_reminders, reminder_payload
from app.services.ledger.actions import begin_action, finish_action
from app.services.scheduler import jobs, runner
from tests.debts.conftest import acc_id, op, open_bank

D = Decimal
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _at(day: date, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=TZ).astimezone(UTC)


async def _outbox(session, user_id):  # type: ignore[no-untyped-def]
    return list(await session.scalars(
        select(NotificationOutbox)
        .where(NotificationOutbox.user_id == user_id, NotificationOutbox.kind == "debt_reminder")
        .order_by(NotificationOutbox.id)
    ))


async def _enable(session, user_id, days=30):  # type: ignore[no-untyped-def]
    st = await session.get(UserSettings, user_id)
    st.debt_reminders_enabled = True
    st.debt_reminder_days = days
    await session.flush()


async def test_reminders_age_snooze_repeat_dedupe(api, session, user):
    await open_bank(api, session, user.id)
    kb = await acc_id(session, user.id, "Кошелёк")
    today = local_today("Asia/Ho_Chi_Minh")
    d40 = (today - timedelta(days=40)).isoformat()
    kolya = (await op(api, op="lend", person_name="Коля", amount=500000, occurred_on=d40))["debt"]
    # a partial return does not reset the age
    await op(api, op="repay_in", debt_id=kolya["id"], amount=200000,
             occurred_on=(today - timedelta(days=5)).isoformat())
    await op(api, op="lend", debt_id=kolya["id"], amount=50, currency="USDT", account_id=kb,
             occurred_on=(today - timedelta(days=35)).isoformat())
    petya = (await op(api, op="borrow", person_name="Петя", amount=1000000,
                      occurred_on=(today - timedelta(days=31)).isoformat()))["debt"]
    fresh = (await op(api, op="lend", person_name="Лена", amount=100000))["debt"]  # 0 days

    # off by default
    assert await enqueue_debt_reminders(session, _at(today)) == 0
    await _enable(session, user.id)
    # before 10:00 local — nothing
    assert await enqueue_debt_reminders(session, _at(today, 9)) == 0
    assert await enqueue_debt_reminders(session, _at(today)) == 2
    rows = await _outbox(session, user.id)
    texts = {r.dedupe_key: r.payload["text"].replace(" ", " ") for r in rows}
    assert texts[f"debt_reminder:{kolya['id']}:{today}"] == (
        "⏰ Коля должен 300 000 ₫ и 50 USDT уже 40 дней")
    assert texts[f"debt_reminder:{petya['id']}:{today}"] == (
        "⏰ Твой долг · Петя: 1 000 000 ₫ уже 31 день")
    assert all(f":{fresh['id']}:" not in k for k in texts)
    row = next(r for r in rows if r.dedupe_key.startswith(f"debt_reminder:{kolya['id']}:"))
    assert row.payload["buttons"] == [[
        {"text": "Записать возврат", "callback": f"dr:{kolya['id']}"},
        {"text": "Напомнить позже", "callback": f"dz:{kolya['id']}"},
    ]]
    assert row.payload["parse_mode"] == "HTML"
    k = await session.get(Debt, kolya["id"])
    await session.refresh(k)
    assert k.last_reminded_on == today
    # same day / next days: not again until N days passed (dedupe + last_reminded_on)
    assert await enqueue_debt_reminders(session, _at(today, 18)) == 0
    assert await enqueue_debt_reminders(session, _at(today + timedelta(days=29))) == 0
    # 30 days later: Коля and Петя again, and Лена's loan is 30 days old now
    assert await enqueue_debt_reminders(session, _at(today + timedelta(days=30))) == 3
    # «Напомнить позже» (snooze 7 days) postpones the next one
    r = await api.post(f"/debts/{kolya['id']}/snooze", json={"days": 7})
    assert r.status_code == 200
    snoozed_till = date.fromisoformat(r.json()["remind_snoozed_until"])
    assert snoozed_till == today + timedelta(days=7)
    k.last_reminded_on = None
    p = await session.get(Debt, petya["id"])
    p.last_reminded_on = None
    await session.flush()
    assert await enqueue_debt_reminders(session, _at(today + timedelta(days=1))) == 1  # Петя
    assert await enqueue_debt_reminders(session, _at(today + timedelta(days=7))) == 1  # Коля
    # closing the debt stops the reminders; a new loan starts a new age
    await op(api, op="repay_in", debt_id=kolya["id"], amount=None)
    await op(api, op="repay_in", debt_id=kolya["id"], amount=None, currency="USDT",
             account_id=kb)
    await op(api, op="lend", debt_id=kolya["id"], amount=10000)
    k.last_reminded_on = None
    await session.flush()
    assert await enqueue_debt_reminders(session, _at(today + timedelta(days=20))) == 0
    # the scheduler runs the debt reminders as their own job
    assert "debt_reminders" in [j.name for j in runner.default_jobs()]
    await jobs.debt_reminders(session, _at(today + timedelta(days=31)))
    keys = {r.dedupe_key for r in await _outbox(session, user.id)}
    assert f"debt_reminder:{kolya['id']}:{today + timedelta(days=31)}" in keys


async def test_reminder_days_setting_and_html_escape(api, session, user, principal):
    await open_bank(api, session, user.id)
    today = local_today("Asia/Ho_Chi_Minh")
    d = (await op(api, op="lend", person_name="<b>Ко&ля</b>", amount=1000,
                  occurred_on=(today - timedelta(days=3)).isoformat()))["debt"]
    await _enable(session, user.id, days=3)
    assert await enqueue_debt_reminders(session, _at(today)) == 1
    text = (await _outbox(session, user.id))[0].payload["text"]
    assert "&lt;b&gt;Ко&amp;ля&lt;/b&gt;" in text and "уже 3 дня" in text
    p = reminder_payload(d["id"], "Коля", [("VND", D(1)), ("USDT", D(2)), ("TON", D(3))], 31,
                         [], None)
    assert p["text"].replace(" ", " ") == "⏰ Коля должен 1 ₫, 2 USDT и 3 TON уже 31 день"


async def test_history_balance_after_and_pagination(api, session, user):
    await open_bank(api, session, user.id)
    kb = await acc_id(session, user.id, "Кошелёк")
    today = local_today("Asia/Ho_Chi_Minh")
    days = [(today - timedelta(days=n)).isoformat() for n in (5, 4, 3, 2, 1)]
    d = (await op(api, op="lend", person_name="Коля", amount=500000, occurred_on=days[0]))["debt"]
    await op(api, op="repay_in", debt_id=d["id"], amount=200000, occurred_on=days[1])
    await op(api, op="lend", debt_id=d["id"], amount=7, currency="USDT", account_id=kb,
             occurred_on=days[2])
    await op(api, op="repay_in", debt_id=d["id"], amount=400000, occurred_on=days[3])
    await op(api, op="forgive", debt_id=d["id"], amount=None, occurred_on=days[4])
    det = (await api.get(f"/debts/{d['id']}")).json()
    assert det["total"] == 5
    rows = [(h["op"], h["currency"], h["delta"], h["balance_after"]) for h in det["history"]]
    assert rows == [
        ("forgive", "VND", "100000", "0"),
        ("repay_in", "VND", "-400000", "-100000"),
        ("lend", "USDT", "7", "7"),
        ("repay_in", "VND", "-200000", "300000"),
        ("lend", "VND", "500000", "500000"),
    ]
    assert det["history"][0]["transaction"]["kind"] == "income"
    assert det["debt"]["status"] == "owed_to_me" and det["debt"]["last_activity_on"] == days[4]
    page = (await api.get(f"/debts/{d['id']}", params={"limit": 2, "offset": 2})).json()
    assert [h["op"] for h in page["history"]] == ["lend", "repay_in"]
    assert page["total"] == 5 and page["limit"] == 2 and page["offset"] == 2
    assert (await api.get("/debts/999999")).status_code == 404


async def test_service_api_for_dispatcher(session, user, principal):
    """The dispatcher calls record_op inside its own action (kind becomes «debt»)."""
    ctx = await begin_action(session, principal, ActionKind.create_transaction,
                             input_text="дал в долг Коле 500к")
    res = await debts.record_op(
        session, principal,
        DebtOpCreate(op="lend", person_name="Коля", amount=D(500000), currency="VND"),
        raw_text="дал в долг Коле 500к", action=ctx,
    )
    await finish_action(session, ctx)
    assert ctx.kind == ActionKind.debt and res.action_id == ctx.id
    assert res.transaction.raw_text == "дал в долг Коле 500к"
    d = await session.get(Debt, res.debt.id)
    assert await debts.balances_of(session, d) == {"VND": D(500000)}
    assert await debts.open_since(session, d, "vnd") == local_today("Asia/Ho_Chi_Minh")
    t = await debts.totals(session, user.id)
    assert t.owed_to_me_vnd == D(500000) and t.owed_to_me_usd is not None
    assert t.i_owe_vnd == 0 and t.i_owe_count == 0
    same = await debts.get_or_create_debt(session, principal, "Колю", written_form="Колю")
    assert same.id == d.id and "колю" in same.aliases
    with pytest.raises(Exception) as e:
        await debts.get_or_create_debt(session, principal, "  ")
    assert "имя" in str(e.value.message)  # type: ignore[attr-defined]


async def test_parallel_forgive_cannot_double_write_off(live_api, live_session, live_user):
    """Two clients press «Списать остаток» at once: one 201, the other sees 0 → 422."""
    r = await live_api.post("/debts/operations", headers={"Idempotency-Key": "a1"}, json={
        "op": "lend", "person_name": "Коля", "amount": "300000", "currency": "VND"})
    assert r.status_code == 201, r.text
    did = r.json()["debt"]["id"]
    body = {"op": "forgive", "debt_id": did, "currency": "VND"}
    res = await asyncio.gather(
        live_api.post("/debts/operations", headers={"Idempotency-Key": "f1"}, json=body),
        live_api.post("/debts/operations", headers={"Idempotency-Key": "f2"}, json=body),
    )
    codes = sorted(x.status_code for x in res)
    assert codes == [201, 422], [x.text for x in res]
    det = (await live_api.get(f"/debts/{did}")).json()
    assert det["debt"]["balances"] == [] and det["total"] == 2

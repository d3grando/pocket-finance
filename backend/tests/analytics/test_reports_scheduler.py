"""Reports (Russian texts), outbox (claim/ack/backoff), scheduler jobs (report at 10:00 local on
the 1st, reminders), worker single-instance lock."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import NotificationOutbox, RecurringTransaction, UserSettings
from app.services.events import outbox
from app.services.reports import service as RP
from app.services.scheduler import jobs, runner
from tests.analytics.conftest import local_now, sp

NOV1 = date(2026, 11, 1)


async def test_monthly_report_text(session, dataset):
    rep = await RP.build_report(session, dataset.b.user_id, "monthly", NOV1,
                                now=local_now(NOV1, 10))  # fmt: skip
    assert (rep.range.date_from, rep.range.date_to) == (date(2026, 10, 1), date(2026, 10, 31))
    assert sp(rep.text_html) == "\n".join(
        [
            "🗓 <b>Октябрь 2026</b>",
            "",
            "Расходы   −1 375 000 ₫  (+317% к сентябрю)",
            "Доходы    +30 000 000 ₫",
            "Поток     <b>+28 625 000 ₫</b> ≈ $1 145.00",
            "",
            "Куда ушло:",
            "🖥 Серверы/инфра — 875 000 ₫ · 64%",
            "🍴 Еда — 450 000 ₫ · 33%",
            "☕ Кофе — 50 000 ₫ · 3.6%",
            "",
            "💼 Бизнес: итог +$965.00 (доход $1 000.00, расход $35.00)",
            "🔝 Крупнейшая покупка: Серверы/инфра · vast.ai — $20.00",
            "📥 Не разобрано: 1 операция",
            "⚠️ Без курса: 1 операция (не вошли в суммы)",
        ]
    )
    assert "<b>" not in rep.text_plain and "Октябрь 2026" in rep.text_plain


async def test_monthly_report_month_to_date_and_escaping(session, dataset):
    await dataset.b.category("<script>", is_business=False)
    await dataset.b.tx("expense", "1", "VND", date(2026, 10, 12), category="<script>",
                       usd="0.04", vnd="1")  # fmt: skip
    rep = await RP.build_report(session, dataset.b.user_id, "monthly", date(2026, 10, 27))
    assert rep.range.label == "1–27 окт"
    assert "&lt;script&gt;" in rep.text_html or "остальное" in rep.text_html
    assert "<script>" not in rep.text_html


async def test_daily_and_weekly_reports(session, dataset):
    d = await RP.build_report(session, dataset.b.user_id, "daily", date(2026, 10, 27))
    assert sp(d.text_html) == (
        "🌙 27 октября: <b>−700 000 ₫</b> · 3 операции · больше всего — Серверы/инфра 500 000 ₫"
    )
    empty = await RP.build_report(session, dataset.b.user_id, "daily", date(2026, 10, 20))
    assert empty.text_plain == "🌙 20 октября: расходов нет"
    w = await RP.build_report(session, dataset.b.user_id, "weekly", date(2026, 11, 2))  # Monday
    assert (w.range.date_from, w.range.date_to) == (date(2026, 10, 26), date(2026, 11, 1))
    assert sp(w.text_html).startswith("🗓 <b>Неделя 26 окт – 1 ноя</b>")
    assert "к прошлой неделе" not in w.text_html  # previous week had no expenses
    assert sp(w.text_html).count(" — ") >= 3


async def test_report_routes(client, dataset):
    r = await client.get("/reports/monthly", params={"on": "2026-11-01"})
    assert r.status_code == 200, r.text
    assert r.json()["range"]["label"] == "Октябрь 2026"
    r = await client.post("/reports/daily/send", params={"on": "2026-10-27"})
    assert r.status_code == 202, r.text
    item = await dataset.b.session.get(NotificationOutbox, r.json()["outbox_id"])
    assert item.kind == "report_daily" and item.status == "pending"
    assert item.payload["parse_mode"] == "HTML"
    assert item.payload["buttons"][0][0]["callback"].startswith("o:/analytics?period=custom")


# ----------------------------------------------------------------------------- outbox


async def test_outbox_dedupe_claim_ack_backoff(session, user):
    p = outbox.make_payload("привет", [[{"text": "OK", "callback": "r:1"}]])
    a = await outbox.enqueue(session, user.id, "info", p, dedupe_key="k:1")
    assert a is not None
    assert await outbox.enqueue(session, user.id, "info", p, dedupe_key="k:1") is None
    b = await outbox.enqueue(session, user.id, "info", p)
    items = await outbox.claim(session, user.id, 10)
    assert [i.id for i in items] == [a.id, b.id]
    assert items[0].telegram_user_id == 123456789 and items[0].payload.text == "привет"
    assert await outbox.claim(session, user.id, 10) == []  # locked while sending
    await outbox.ack(session, user.id, a.id, ok=True, error=None, retry=False)
    await session.refresh(a)
    assert a.status == "sent" and a.sent_at is not None
    await outbox.ack(session, user.id, a.id, ok=True, error=None, retry=False)  # idempotent
    await outbox.ack(session, user.id, b.id, ok=False, error="429 Too Many Requests", retry=True)
    await session.refresh(b)
    assert b.status == "pending" and b.attempts == 1
    assert b.available_at > datetime.now(UTC) + timedelta(seconds=25)
    for _ in range(7):
        b.status = "sending"
        await outbox.ack(session, user.id, b.id, ok=False, error="net", retry=True)
    await session.refresh(b)
    assert b.status == "failed" and b.attempts == 8
    c = await outbox.enqueue(session, user.id, "info", p)
    await outbox.claim(session, user.id, 10)
    await outbox.ack(session, user.id, c.id, ok=False, error="403 blocked", retry=False)
    await session.refresh(c)
    assert c.status == "failed"
    with pytest.raises(ValueError):
        outbox.make_payload("x", [[{"text": "x", "callback": "o:" + "y" * 80}]])


async def test_outbox_routes(client, session, user):
    from app.api.deps_base import get_bot_principal
    from app.core.principal import Principal
    from app.main import app

    app.dependency_overrides[get_bot_principal] = lambda: Principal(user.id, "bot", "telegram")
    await outbox.enqueue(session, user.id, "info", outbox.make_payload("<b>отчёт</b>"))
    r = await client.post("/bot/outbox/claim", params={"limit": 5})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["payload"]["parse_mode"] == "HTML"
    r = await client.post(f"/bot/outbox/{items[0]['id']}/ack", json={"ok": True})
    assert r.status_code == 200 and r.json() == {"ok": True}
    r = await client.post("/bot/outbox/999999/ack", json={"ok": True})
    assert r.status_code == 404


# -------------------------------------------------------------------------- scheduler


async def _outbox(session, kind: str | None = None) -> list[NotificationOutbox]:  # type: ignore[no-untyped-def]
    q = select(NotificationOutbox).order_by(NotificationOutbox.id)
    if kind:
        q = q.where(NotificationOutbox.kind == kind)
    return list((await session.scalars(q)).all())


async def test_monthly_report_enqueued_on_the_1st_at_10_local(session, dataset):
    uid = dataset.b.user_id
    await jobs.enqueue_reports(session, local_now(NOV1, 9, 59))  # 02:59 UTC
    assert await _outbox(session, "report_monthly") == []
    await jobs.enqueue_reports(session, local_now(NOV1, 10, 0))  # 03:00 UTC
    items = await _outbox(session, "report_monthly")
    assert len(items) == 1 and items[0].dedupe_key == f"report_monthly:{uid}:2026-10"
    assert "Октябрь 2026" in items[0].payload["text"]
    btn = items[0].payload["buttons"][0]
    assert btn[0]["callback"] == "o:/analytics?period=custom&from=2026-10-01&to=2026-10-31"
    assert btn[1]["callback"] == "o:/operations?status=needs_account"
    await jobs.enqueue_reports(session, local_now(NOV1, 15, 0))  # later ticks → no duplicate
    assert len(await _outbox(session, "report_monthly")) == 1
    await jobs.enqueue_reports(session, local_now(date(2026, 11, 2), 10, 0))  # not the 1st
    assert len(await _outbox(session, "report_monthly")) == 1
    assert await _outbox(session, "report_daily") == []  # daily/weekly are off by default


async def test_daily_weekly_when_enabled(session, dataset):
    st = await session.get(UserSettings, dataset.b.user_id)
    st.report_daily = True
    st.report_weekly = True
    st.report_monthly = False
    st.report_daily_time = time(10, 0)  # morning → yesterday
    st.report_weekly_day = 0  # Monday → previous Mon–Sun
    st.report_weekly_time = time(10, 0)
    await session.flush()
    await jobs.enqueue_reports(session, local_now(date(2026, 11, 2), 10, 5))  # Monday
    daily = await _outbox(session, "report_daily")
    weekly = await _outbox(session, "report_weekly")
    assert len(daily) == 1 and "1 ноября" in daily[0].payload["text"]  # yesterday
    assert len(weekly) == 1 and "26 окт – 1 ноя" in sp(weekly[0].payload["text"])
    assert await _outbox(session, "report_monthly") == []
    st.report_daily_time = time(21, 0)
    await session.flush()
    await jobs.enqueue_reports(session, local_now(date(2026, 11, 3), 20, 59))  # not yet
    assert len(await _outbox(session, "report_daily")) == 1
    await jobs.enqueue_reports(session, local_now(date(2026, 11, 3), 21, 0))  # evening → today
    daily = await _outbox(session, "report_daily")
    assert len(daily) == 2 and "3 ноября" in daily[1].payload["text"]


async def test_weekly_default_sunday_evening(session, dataset):
    st = await session.get(UserSettings, dataset.b.user_id)
    st.report_weekly = True
    st.report_monthly = False
    await session.flush()
    sunday = date(2026, 11, 8)
    await jobs.enqueue_reports(session, local_now(sunday, 20, 59))
    assert await _outbox(session, "report_weekly") == []
    await jobs.enqueue_reports(session, local_now(sunday, 21, 0))
    await jobs.enqueue_reports(session, local_now(sunday, 22, 0))  # no duplicate
    weekly = await _outbox(session, "report_weekly")
    assert len(weekly) == 1 and "2–8 ноя" in sp(weekly[0].payload["text"])


async def test_recurring_reminders(session, build):
    await build.category("Жильё")
    await build.account("Карта", "VND")
    r = RecurringTransaction(
        user_id=build.user_id, title="Аренда", kind="expense", amount=Decimal(11_000_000),
        currency="VND", account_id=build.accs["Карта"].id, category_id=build.cats["Жильё"].id,
        frequency="monthly", interval=1, day_of_month=5, next_due_on=date(2026, 11, 5),
        remind_time=time(10, 0), is_active=True, version=1,
    )  # fmt: skip
    session.add(r)
    await session.flush()
    await jobs.recurring_reminders(session, local_now(date(2026, 11, 4), 23, 0))
    await jobs.recurring_reminders(session, local_now(date(2026, 11, 5), 9, 59))
    assert await _outbox(session, "recurring_reminder") == []
    await jobs.recurring_reminders(session, local_now(date(2026, 11, 5), 10, 0))
    items = await _outbox(session, "recurring_reminder")
    assert len(items) == 1
    assert sp(items[0].payload["text"]) == (
        "⏰ Сегодня обычно платишь\n<b>Аренда</b> — 11 000 000 ₫ · Карта"
    )
    cb = [b["callback"] for row in items[0].payload["buttons"] for b in row]
    # the occurrence rides in the button: a stale keyboard cannot record/skip it twice
    assert cb == [f"r:{r.id}:20261105", f"k:{r.id}:20261105", f"m:{r.id}:20261105"]
    await session.refresh(r)
    assert r.last_reminded_on == date(2026, 11, 5)
    await jobs.recurring_reminders(session, local_now(date(2026, 11, 5), 18, 0))
    assert len(await _outbox(session, "recurring_reminder")) == 1  # once per due date
    # ignored for two months → jumps to the latest due date, reminds again
    await jobs.recurring_reminders(session, local_now(date(2027, 1, 6), 8, 0))
    await session.refresh(r)
    assert r.next_due_on == date(2027, 1, 5)
    items = await _outbox(session, "recurring_reminder")
    assert len(items) == 2 and "по графику на 5 янв" in sp(items[1].payload["text"])


async def test_scheduler_tick_isolates_failures(session):
    calls: list[str] = []

    async def ok(s, now):  # type: ignore[no-untyped-def]
        calls.append("ok")

    async def boom(s, now):  # type: ignore[no-untyped-def]
        raise RuntimeError("provider down")

    class _Ctx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return session

        async def __aexit__(self, *a):  # type: ignore[no-untyped-def]
            return None

    sched = runner.Scheduler(
        session_factory=_Ctx,  # type: ignore[arg-type]
        jobs=[
            runner.Job("boom", boom, timedelta(minutes=10)),
            runner.Job("ok", ok, timedelta(hours=1)),
            runner.Job("every", ok, timedelta(0)),
        ],
    )
    t0 = datetime(2026, 10, 27, 3, 0, tzinfo=UTC)
    assert await sched.tick(t0) == ["ok", "every"]
    assert await sched.tick(t0 + timedelta(seconds=30)) == ["every"]
    assert await sched.tick(t0 + timedelta(minutes=11)) == ["every"]  # boom ran (failed) again
    assert await sched.tick(t0 + timedelta(hours=1)) == ["ok", "every"]
    names = [j.name for j in runner.default_jobs()]
    assert names == [
        "refresh_fiat_rates", "refresh_crypto_rates", "backfill_missing_rates",
        "enqueue_reports", "recurring_reminders", "debt_reminders", "cleanup",
    ]  # fmt: skip


async def test_worker_advisory_lock_single_instance(engine):
    async with engine.connect() as a, engine.connect() as b:
        assert await runner.try_lock(a) is True
        assert await runner.try_lock(b) is False
        await a.execute(
            runner.text("SELECT pg_advisory_unlock(:k)"), {"k": runner.ADVISORY_LOCK_ID}
        )
        await a.commit()
        assert await runner.try_lock(b) is True
        await b.execute(
            runner.text("SELECT pg_advisory_unlock(:k)"), {"k": runner.ADVISORY_LOCK_ID}
        )
        await b.commit()


async def test_cleanup_runs(session, user):
    await jobs.cleanup(session, datetime.now(UTC))


async def test_run_forever_takes_lock_runs_jobs_and_stops(engine, monkeypatch):
    import asyncio

    ran: list[datetime] = []
    stop = asyncio.Event()

    async def job(s, now):  # type: ignore[no-untyped-def]
        ran.append(now)
        stop.set()

    monkeypatch.setattr(runner, "default_jobs", lambda: [runner.Job("probe", job, timedelta(0))])
    await asyncio.wait_for(runner.run_forever(engine, stop=stop), timeout=10)
    assert len(ran) == 1
    async with engine.connect() as c:  # the lock was released on exit
        assert await runner.try_lock(c) is True
        await c.execute(
            runner.text("SELECT pg_advisory_unlock(:k)"), {"k": runner.ADVISORY_LOCK_ID}
        )
        await c.commit()

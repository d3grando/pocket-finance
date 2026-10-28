"""Scheduled jobs. Each job: async def job(session, now) -> None,
idempotent, commits itself.

Reports and reminders go to ``notifications_outbox`` (the bot pulls it); ``dedupe_key`` makes
every tick safe to repeat: ``report_monthly:<user>:2026-10``, ``report_daily:<user>:2026-10-27``,
``report_weekly:<user>:2026-10-20``, ``recurring:<id>:2026-11-05``,
``debt_reminder:<debt>:2026-10-27``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import tz
from app.models import (
    AIRequest,
    NotificationOutbox,
    PreviewToken,
    RecurringTransaction,
    User,
    UserSettings,
)
from app.services.events import outbox
from app.services.rates import service as rates

log = logging.getLogger("pocket.worker")


async def refresh_fiat_rates(session: AsyncSession, now: datetime) -> None:
    n = await rates.refresh_fiat(session)
    await session.commit()
    log.info("fiat rates refreshed: %s rows", n)


async def refresh_crypto_rates(session: AsyncSession, now: datetime) -> None:
    n = await rates.refresh_crypto(session)
    await session.commit()
    log.info("crypto rates refreshed: %s rows", n)


async def backfill_missing_rates(session: AsyncSession, now: datetime) -> None:
    n = await rates.backfill_missing(session)
    await session.commit()
    if n:
        log.info("backfilled rates for %s transactions", n)


async def _active_users(session: AsyncSession) -> list[tuple[User, UserSettings]]:
    rows = await session.execute(
        select(User, UserSettings)
        .join(UserSettings, UserSettings.user_id == User.id)
        .where(User.is_active.is_(True))
    )
    return [(u, s) for u, s in rows.all()]


async def enqueue_reports(session: AsyncSession, now: datetime) -> None:
    """Per-user schedules in the user's timezone; dedupe_key 'report_monthly:<user>:2026-10' etc.

    monthly — on the 1st at report_time (default 10:00): the previous month;
    daily   — every day at report_daily_time (default 22:00): yesterday if that time is before
              12:00, otherwise today;
    weekly  — on report_weekly_day (0=Mon … 6=Sun, default Sunday) at report_weekly_time
              (default 21:00): the previous Mon–Sun on a Monday, else the current week to date."""
    from app.services.reports.service import report_payload

    for user, st in await _active_users(session):
        if outbox.resolve_target(st.telegram_user_id) is None:
            continue  # no Telegram chat yet: enqueue once it is bound (dedupe_key not used)
        local = now.astimezone(tz(st.timezone))
        clock = local.time().replace(tzinfo=None)
        day = local.date()
        todo: list[tuple[str, object, str]] = []
        if st.report_monthly and day.day == 1 and clock >= st.report_time:
            prev = (day - timedelta(days=1)).strftime("%Y-%m")
            todo.append(("monthly", day, f"report_monthly:{user.id}:{prev}"))
        weekly_day = getattr(st, "report_weekly_day", 0)
        weekly_time = getattr(st, "report_weekly_time", st.report_time)
        if st.report_weekly and day.weekday() == weekly_day and clock >= weekly_time:
            start = (
                day - timedelta(days=7)
                if day.weekday() == 0
                else day - timedelta(days=day.weekday())
            )
            todo.append(("weekly", day, f"report_weekly:{user.id}:{start.isoformat()}"))
        daily_time = getattr(st, "report_daily_time", st.report_time)
        if st.report_daily and clock >= daily_time:
            target = day - timedelta(days=1) if daily_time.hour < 12 else day
            todo.append(("daily", target, f"report_daily:{user.id}:{target.isoformat()}"))
        for kind, on, key in todo:
            exists = await session.scalar(
                select(NotificationOutbox.id).where(NotificationOutbox.dedupe_key == key)
            )
            if exists:
                continue
            try:
                _, payload = await report_payload(session, user.id, kind, on, now=now)  # type: ignore[arg-type]
                await outbox.enqueue(session, user.id, f"report_{kind}", payload, dedupe_key=key)
                await session.commit()
                log.info("report %s enqueued for user %s", key, user.id)
            except Exception:
                await session.rollback()
                log.exception("report %s failed", key)


async def recurring_reminders(session: AsyncSession, now: datetime) -> None:
    """Remind on the due day at remind_time (user tz). Missed occurrences (nobody pressed
    «Записать»/«Пропустить») are skipped forward to the latest due date ≤ today, so a stale
    reminder never blocks the next one."""
    from app.services.recurring.service import latest_due, reminder_payload

    users = {
        u.id: st
        for u, st in await _active_users(session)
        if getattr(st, "reminders_enabled", True)
        and outbox.resolve_target(st.telegram_user_id) is not None
    }
    rows = (
        await session.scalars(
            select(RecurringTransaction).where(
                RecurringTransaction.is_active.is_(True),
                RecurringTransaction.user_id.in_(list(users)),
            )
        )
    ).all()
    for r in rows:
        st = users[r.user_id]
        local = now.astimezone(tz(st.timezone))
        today = local.date()
        if r.next_due_on > today:
            continue
        due = latest_due(r, today)
        if due != r.next_due_on:
            r.next_due_on = due
            r.last_reminded_on = None
            r.version += 1
        if r.last_reminded_on is not None and r.last_reminded_on >= due:
            await session.commit()
            continue
        if due == today and local.time().replace(tzinfo=None) < r.remind_time:
            await session.commit()
            continue
        key = f"recurring:{r.id}:{due.isoformat()}"
        try:
            payload = await reminder_payload(session, r, today)
            await outbox.enqueue(session, r.user_id, "recurring_reminder", payload, dedupe_key=key)
            r.last_reminded_on = due
            await session.commit()
            log.info("reminder %s enqueued", key)
        except Exception:
            await session.rollback()
            log.exception("reminder %s failed", key)


async def debt_reminders(session: AsyncSession, now: datetime) -> None:
    """«⏰ Коля должен 300 000 ₫ уже 30 дней» (off by default; debt_reminders_enabled, N days).
    Own scheduler job ``debt_reminders``, every tick (docs/DEBTS.md §9).
    Never breaks the recurring reminders: a failure is logged and rolled back."""
    from app.services.debts.reminders import enqueue_debt_reminders

    try:
        n = await enqueue_debt_reminders(session, now)
        await session.commit()
        if n:
            log.info("debt reminders enqueued: %s", n)
    except Exception:
        await session.rollback()
        log.exception("debt reminders failed")


async def cleanup(session: AsyncSession, now: datetime) -> None:
    from app.services.auth.cleanup import cleanup_auth

    day_ago = now - timedelta(days=1)
    # auth_challenges, dead sessions (>30 d), revoked device tokens (>90 d), expired
    # idempotency keys — owned by auth.
    await cleanup_auth(session, now)
    await session.execute(delete(PreviewToken).where(PreviewToken.created_at < day_ago))
    await session.execute(delete(AIRequest).where(AIRequest.created_at < now - timedelta(days=180)))
    await session.execute(
        delete(NotificationOutbox).where(
            NotificationOutbox.status.in_(("sent", "failed")),
            NotificationOutbox.created_at < now - timedelta(days=90),
        )
    )
    await session.commit()

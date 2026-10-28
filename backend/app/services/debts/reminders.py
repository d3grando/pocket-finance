"""Debt reminders. docs/DEBTS.md §9.

Worker job (called from ``services/scheduler/jobs.py`` every tick): for users with
``debt_reminders_enabled``: at/after 10:00 local, for each non-archived person whose oldest
open balance of a direction (``open_since``) is ≥ ``debt_reminder_days`` old, not snoozed
(``remind_snoozed_until`` ≤ today) and not reminded within the last N days (an ended snooze
reminds at once, see ``_due``) → one outbox item
``kind=debt_reminder`` (dedupe ``debt_reminder:{debt_id}:{local_date}``), then
``last_reminded_on = today``.
"""

from __future__ import annotations

import html
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import format_money
from app.core.timeutil import tz, utcnow
from app.models import Debt, User, UserSettings
from app.models.enums import OutboxKind
from app.services.events import outbox
from app.services.stats.periods import plural

log = logging.getLogger("pocket.debts")

#: reminders go out at/after this local time
REMIND_AT = time(10, 0)


def _days(n: int) -> str:
    return f"{n} {plural(n, 'день', 'дня', 'дней')}"


def _amounts(items: list[tuple[str, Decimal]]) -> str:
    """«300 000 ₫» / «300 000 ₫ и 50 USDT» / «1 ₫, 2 $ и 3 TON» (absolute values)."""
    parts = [format_money(abs(v), c) for c, v in items]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " и " + parts[-1]


def reminder_payload(
    debt_id: int,
    name: str,
    owed: list[tuple[str, Decimal]],
    owed_days: int | None,
    owe: list[tuple[str, Decimal]],
    owe_days: int | None,
) -> dict[str, object]:
    """``{"text": HTML, "parse_mode": "HTML", "buttons": [[{"text": "Записать возврат",
    "callback": "dr:{debt_id}"}, {"text": "Напомнить позже", "callback": "dz:{debt_id}"}]]}``.

    ``owed`` — what the person owes me (currency, amount), ``owe`` — what I owe; each with the
    age in days of its oldest balance. «⏰ Коля должен 300 000 ₫ уже 30 дней»,
    «⏰ Твой долг · Петя: 1 000 000 ₫ уже 30 дней»."""
    who = html.escape(name)
    lines: list[str] = []
    if owed:
        lines.append(f"⏰ {who} должен {html.escape(_amounts(owed))} уже {_days(owed_days or 0)}")
    if owe:
        lines.append(
            f"⏰ Твой долг · {who}: {html.escape(_amounts(owe))} уже {_days(owe_days or 0)}"
        )
    buttons = [[
        {"text": "Записать возврат", "callback": f"dr:{debt_id}"},
        {"text": "Напомнить позже", "callback": f"dz:{debt_id}"},
    ]]
    return outbox.make_payload("\n".join(lines), buttons)


def _due(n: int, d: Debt, today: date) -> bool:
    """Not snoozed, not reminded within the last ``n`` days (one reminder every N days).

    «Напомнить позже» promises a reminder when the snooze ends («Напомню через 7 дней»): a
    snooze set after the last reminder (``remind_snoozed_until > last_reminded_on``) that has
    been reached makes the person due at once, whatever N is. That reminder sets
    ``last_reminded_on = today ≥ remind_snoozed_until`` → back to one every N days."""
    snooze, last = d.remind_snoozed_until, d.last_reminded_on
    if snooze is not None and snooze > today:
        return False
    if last is None:
        return True
    if snooze is not None and snooze > last:
        return True
    return last <= today - timedelta(days=n)


async def enqueue_debt_reminders(session: AsyncSession, now: datetime | None = None) -> int:
    """Returns the number of enqueued reminders. Commits per person (like the other jobs)."""
    from app.services.debts.service import debt_states

    now = now or utcnow()
    rows = (
        await session.execute(
            select(
                User.id, UserSettings.telegram_user_id, UserSettings.timezone,
                UserSettings.debt_reminder_days,
            )
            .join(UserSettings, UserSettings.user_id == User.id)
            .where(User.is_active.is_(True), UserSettings.debt_reminders_enabled.is_(True))
        )
    ).all()
    sent = 0
    for user_id, tg_id, tz_name, days in rows:
        if outbox.resolve_target(tg_id) is None:
            continue
        local = now.astimezone(tz(tz_name))
        if local.time().replace(tzinfo=None) < REMIND_AT:
            continue
        today = local.date()
        n = int(days or 30)
        debts = list(
            await session.scalars(
                select(Debt).where(Debt.user_id == user_id, Debt.is_archived.is_(False))
                .order_by(Debt.id)
            )
        )
        debts = [d for d in debts if _due(n, d, today)]
        if not debts:
            continue
        states = await debt_states(session, debts)
        # plain values first: a rollback below expires the ORM rows
        todo: list[tuple[int, str, dict[int, tuple[list[tuple[str, Decimal]], int]]]] = []
        for d in debts:
            s = states[d.id]
            groups: dict[int, tuple[list[tuple[str, Decimal]], int]] = {}
            for sign in (1, -1):
                items = [(c, v) for c, v in s.balances.items() if (v > 0) == (sign > 0)]
                if not items:
                    continue
                age = max(
                    ((today - s.open_since[c]).days for c, _ in items if c in s.open_since),
                    default=0,
                )
                if age >= n:
                    groups[sign] = (items, age)
            if groups:
                todo.append((d.id, d.name, groups))
        for debt_id, name, groups in todo:
            owed, owed_days = groups.get(1, ([], None))
            owe, owe_days = groups.get(-1, ([], None))
            key = f"debt_reminder:{debt_id}:{today.isoformat()}"
            try:
                payload = reminder_payload(debt_id, name, owed, owed_days, owe, owe_days)
                row = await outbox.enqueue(
                    session, user_id, OutboxKind.debt_reminder.value, payload, dedupe_key=key
                )
                await session.execute(
                    update(Debt).where(Debt.id == debt_id).values(last_reminded_on=today)
                )
                await session.commit()
                if row is not None:
                    sent += 1
                    log.info("debt reminder %s enqueued", key)
            except Exception:
                await session.rollback()
                log.exception("debt reminder %s failed", key)
    return sent


__all__ = ["REMIND_AT", "enqueue_debt_reminders", "reminder_payload"]

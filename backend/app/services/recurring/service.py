"""Recurring payment reminders. Reminders only — a transaction is
created only by [Записать] (record).

Schedule: ``frequency`` + ``interval`` + (``day_of_month`` | ``weekday`` | ``month``);
day_of_month 31 (or any day missing in a short month) → the last day of the month.
``next_due_on`` is the date of the next reminder; record/skip move it to the next occurrence.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed, VersionConflict
from app.core.money import format_money
from app.core.principal import Principal
from app.core.timeutil import local_today
from app.models import (
    Account,
    AuditLog,
    Category,
    Merchant,
    RecurringTransaction,
    Transaction,
    UserSettings,
)
from app.schemas.recurring import RecurringCreate, RecurringOut, RecurringPatch, RecurringRecordIn
from app.services.events.bus import publish

if TYPE_CHECKING:
    from app.services.ledger.actions import ActionContext

#: A second «Записать»/«Пропустить» from Telegram for the same reminder within this window
#: (and without ``due_on``) is a double tap on a stale keyboard, not a new payment.
DOUBLE_TAP_SECONDS = 60

_FIELDS = (
    "title", "kind", "amount", "currency", "account_id", "category_id", "merchant_id",
    "description", "frequency", "interval", "day_of_month", "weekday", "month", "next_due_on",
    "remind_time", "is_active",
)  # fmt: skip


def _clamp(y: int, m: int, d: int) -> date:
    return date(y, m, min(d, calendar.monthrange(y, m)[1]))


def _add_months(y: int, m: int, n: int) -> tuple[int, int]:
    k = m - 1 + n
    return y + k // 12, k % 12 + 1


def next_due(r: RecurringTransaction, after: date) -> date:
    """Next due date strictly after ``after`` (day_of_month 31 → last day of month)."""
    try:
        return _next_due(r, after)
    except (OverflowError, ValueError) as e:  # year 9999 + 1 (old rows before the date limit)
        raise ValidationFailed(
            "Дата напоминания вне допустимого диапазона", details={"field": "next_due_on"}
        ) from e


def _next_due(r: RecurringTransaction, after: date) -> date:
    step = max(int(r.interval or 1), 1)
    freq = r.frequency
    if freq == "daily":
        return after + timedelta(days=step)
    if freq == "weekly":
        wd = r.weekday if r.weekday is not None else r.next_due_on.weekday()
        d = after + timedelta(days=1)
        while d.weekday() != wd:
            d += timedelta(days=1)
        return d + timedelta(days=7 * (step - 1))
    if freq == "monthly":
        dom = r.day_of_month or r.next_due_on.day
        c = _clamp(after.year, after.month, dom)
        if c > after:
            return c
        y, m = _add_months(after.year, after.month, step)
        return _clamp(y, m, dom)
    if freq == "yearly":
        mon = r.month or r.next_due_on.month
        dom = r.day_of_month or r.next_due_on.day
        c = _clamp(after.year, mon, dom)
        if c > after:
            return c
        return _clamp(after.year + step, mon, dom)
    raise ValidationFailed(f"Неизвестная периодичность: {freq}")


def latest_due(r: RecurringTransaction, today: date) -> date:
    """Advance ``next_due_on`` over missed occurrences: the last due date ≤ today
    (or next_due_on itself when it is in the future)."""
    due = r.next_due_on
    for _ in range(1000):
        try:
            nxt = next_due(r, due)
        except ValidationFailed:  # no representable next date: stay on the last one
            break
        if nxt > today:
            break
        due = nxt
    return due


def to_out(r: RecurringTransaction) -> RecurringOut:
    return RecurringOut.model_validate(r)


def _snapshot(r: RecurringTransaction) -> dict[str, Any]:
    d: dict[str, Any] = {"id": r.id, "version": r.version}
    for f in _FIELDS:
        v = getattr(r, f)
        d[f] = (
            v.isoformat() if hasattr(v, "isoformat") else (str(v) if isinstance(v, Decimal) else v)
        )
    d["last_reminded_on"] = r.last_reminded_on.isoformat() if r.last_reminded_on else None
    return d


async def _audit(
    session: AsyncSession,
    p: Principal,
    r: RecurringTransaction,
    op: str,
    before: dict | None,
    action_id: uuid.UUID | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=p.user_id,
            entity_type="recurring",
            entity_id=str(r.id),
            operation=op,
            before=before,
            after=None if op == "delete" else _snapshot(r),
            source=p.source,
            action_id=action_id,
        )
    )


async def _check_refs(session: AsyncSession, user_id: int, data: dict[str, Any]) -> None:
    for field, model, msg in (
        ("account_id", Account, "Счёт не найден"),
        ("category_id", Category, "Категория не найдена"),
        ("merchant_id", Merchant, "Продавец не найден"),
    ):
        v = data.get(field)
        if v is None:
            continue
        obj = await session.get(model, v)
        if obj is None or obj.user_id != user_id or getattr(obj, "deleted_at", None) is not None:
            raise ValidationFailed(msg, details={"field": field})
        if isinstance(obj, Account):
            # a debt service account is written only by services/debts (docs/DEBTS.md §2):
            # a reminder on it could never be recorded (record → 422 every time)
            from app.services.accounts.service import check_not_debt

            check_not_debt(obj, field)
    cat_id = data.get("category_id")
    if cat_id is not None and data.get("kind"):
        cat = await session.get(Category, cat_id)
        if cat is not None and cat.kind != data["kind"]:
            raise ValidationFailed(
                "Категория не подходит к типу платежа", details={"field": "category_id"}
            )


def _fill_schedule(r: RecurringTransaction) -> None:
    """Defaults from next_due_on: monthly → day_of_month, weekly → weekday, yearly → month+day."""
    if r.frequency == "monthly" and r.day_of_month is None:
        r.day_of_month = r.next_due_on.day
    elif r.frequency == "weekly" and r.weekday is None:
        r.weekday = r.next_due_on.weekday()
    elif r.frequency == "yearly":
        if r.month is None:
            r.month = r.next_due_on.month
        if r.day_of_month is None:
            r.day_of_month = r.next_due_on.day


async def get_recurring(
    session: AsyncSession, user_id: int, rid: int, *, for_update: bool = False
) -> RecurringTransaction:
    """``for_update``: SELECT … FOR UPDATE (fresh values) — every mutation takes the row lock,
    so parallel record/skip/PATCH are serialized instead of losing updates."""
    if for_update:
        r = await session.get(
            RecurringTransaction, rid, with_for_update=True, populate_existing=True
        )
    else:
        r = await session.get(RecurringTransaction, rid)
    if r is None or r.user_id != user_id:
        raise NotFound("Напоминание не найдено")
    return r


async def list_recurring(session: AsyncSession, user_id: int) -> list[RecurringTransaction]:
    res = await session.scalars(
        select(RecurringTransaction)
        .where(RecurringTransaction.user_id == user_id)
        .order_by(
            RecurringTransaction.is_active.desc(),
            RecurringTransaction.next_due_on,
            RecurringTransaction.id,
        )
    )
    return list(res.all())


async def create_recurring(
    session: AsyncSession, principal: Principal, data: RecurringCreate
) -> RecurringTransaction:
    payload = data.model_dump()
    await _check_refs(session, principal.user_id, payload)
    r = RecurringTransaction(user_id=principal.user_id, version=1, is_active=True, **payload)
    _fill_schedule(r)
    session.add(r)
    await session.flush()
    await _audit(session, principal, r, "create", None)
    await publish(session, principal.user_id, ["recurring"], source=principal.source)
    await session.flush()
    return r


async def update_recurring(
    session: AsyncSession, principal: Principal, rid: int, patch: RecurringPatch
) -> RecurringTransaction:
    r = await get_recurring(session, principal.user_id, rid, for_update=True)
    if patch.version != r.version:
        raise VersionConflict(details={"current_version": r.version})
    changes = patch.model_dump(exclude_unset=True, exclude={"version"})
    if "title" in changes and changes["title"] is None:
        raise ValidationFailed("Название не может быть пустым", details={"field": "title"})
    await _check_refs(session, principal.user_id, {**changes, "kind": r.kind})
    before = _snapshot(r)
    for k, v in changes.items():
        setattr(r, k, v)
    if "frequency" in changes or "next_due_on" in changes:
        _fill_schedule(r)
    if "next_due_on" in changes or "remind_time" in changes:
        r.last_reminded_on = None  # a moved reminder fires again
    r.version += 1
    await session.flush()
    await _audit(session, principal, r, "update", before)
    await publish(session, principal.user_id, ["recurring"], source=principal.source)
    await session.flush()
    return r


async def delete_recurring(session: AsyncSession, principal: Principal, rid: int) -> None:
    r = await get_recurring(session, principal.user_id, rid, for_update=True)
    before = _snapshot(r)
    await _audit(session, principal, r, "delete", before)
    await session.delete(r)
    await publish(session, principal.user_id, ["recurring"], source=principal.source)
    await session.flush()


async def _today_for(session: AsyncSession, user_id: int) -> date:
    st = await session.get(UserSettings, user_id)
    return local_today(st.timezone if st else None)


def _parse_amount_text(text: str, currency: str, small_thousands: bool) -> Decimal:
    from app.services.parser.amounts import parse_amount

    return parse_amount(text, currency, small_thousands=small_thousands)


class AlreadyHandled(Conflict):
    code, message = "already_exists", "Это напоминание уже обработано: платёж записан или пропущен"


async def _check_due(
    session: AsyncSession, principal: Principal, r: RecurringTransaction, due_on: date | None
) -> None:
    """One reminder occurrence = one record/skip (the row is locked by the caller).

    * ``due_on`` given (the reminder's due date, from the button): it must still be the
      current ``next_due_on`` — otherwise this occurrence was already recorded/skipped → 409;
    * no ``due_on`` from Telegram: a second tap on the same reminder within
      ``DOUBLE_TAP_SECONDS`` (every tap has its own callback id = its own Idempotency-Key) → 409.
    Web without ``due_on`` may record several periods on purpose (catch-up)."""
    if due_on is not None:
        if due_on != r.next_due_on:
            raise AlreadyHandled(
                details={"due_on": due_on.isoformat(), "next_due_on": r.next_due_on.isoformat()}
            )
        return
    if principal.source != "telegram":
        return
    recent = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.user_id == principal.user_id,
            AuditLog.entity_type == "recurring",
            AuditLog.entity_id == str(r.id),
            AuditLog.operation == "update",
            AuditLog.source == "telegram",
            AuditLog.created_at > func.now() - timedelta(seconds=DOUBLE_TAP_SECONDS),
        )
    )
    if recent:
        raise AlreadyHandled(details={"next_due_on": r.next_due_on.isoformat()})


async def _category_for_record(session: AsyncSession, user_id: int, r: RecurringTransaction) -> int:
    """The reminder's category, or «Прочее» of the same kind when none was chosen (a payment
    must have a category; the user can move it with «В другую категорию»)."""
    if r.category_id is not None:
        return r.category_id
    cid = await session.scalar(
        select(Category.id)
        .where(
            Category.user_id == user_id,
            Category.kind == r.kind,
            func.lower(Category.name) == "прочее",
            Category.merged_into_id.is_(None),
        )
        .order_by(Category.is_archived, Category.id)
        .limit(1)
    )
    if cid is None:
        raise ValidationFailed(
            "У напоминания не выбрана категория — выберите её в настройках",
            details={"field": "category_id"},
        )
    return int(cid)


async def record(
    session: AsyncSession,
    principal: Principal,
    rid: int,
    data: RecurringRecordIn,
    *,
    due_on: date | None = None,
    action: ActionContext | None = None,
) -> Transaction:
    """Create the transaction via ledger.create_transaction (recurring_id set), advance
    next_due_on.

    With ``action`` (the route's ActionContext) the transaction AND the reminder change go into
    one action: «Отменить» deletes the operation and brings the reminder back to its due date.
    The caller finishes the action. ``due_on`` — see ``_check_due``."""
    from app.schemas.transactions import TransactionCreate
    from app.services.ledger import transactions as ledger
    from app.services.ledger.audit import snapshot

    r = await get_recurring(session, principal.user_id, rid, for_update=True)
    await _check_due(session, principal, r, due_on)
    currency = (data.currency or r.currency).upper()
    amount = data.amount
    if amount is None and data.amount_text:
        st = await session.get(UserSettings, principal.user_id)
        amount = _parse_amount_text(
            data.amount_text, currency, bool(st.small_amount_thousands) if st else True
        )
    if amount is None:
        amount = r.amount
    if amount is None or amount <= 0:
        raise ValidationFailed("Укажите сумму платежа", details={"field": "amount"})
    category_id = await _category_for_record(session, principal.user_id, r)
    occurred_on = data.occurred_on or await _today_for(session, principal.user_id)
    tx_in = TransactionCreate(
        kind=r.kind,  # type: ignore[arg-type]
        amount=amount,
        currency=currency,
        account_id=data.account_id if data.account_id is not None else r.account_id,
        category_id=category_id,
        merchant_id=r.merchant_id,
        description=r.description or r.title,
        occurred_on=occurred_on,
        recurring_id=r.id,
    )
    tx = await ledger.create_transaction(session, principal, tx_in, raw_text=None, action=action)
    before = _snapshot(r)
    undo_before = snapshot(r)
    r.next_due_on = next_due(r, r.next_due_on)
    r.last_reminded_on = None
    r.version += 1
    await session.flush()
    if action is not None:
        action.record("recurring", r.id, undo_before, r.version)
        action.changed |= {"recurring"}
    await _audit(session, principal, r, "update", before, action.id if action else None)
    await publish(
        session,
        principal.user_id,
        ["recurring", "transactions"],
        action_id=action.id if action else None,
        source=principal.source,
    )
    return tx


async def skip(
    session: AsyncSession, principal: Principal, rid: int, *, due_on: date | None = None
) -> RecurringTransaction:
    """[Пропустить]: move next_due_on to the next occurrence (once per occurrence)."""
    r = await get_recurring(session, principal.user_id, rid, for_update=True)
    await _check_due(session, principal, r, due_on)
    before = _snapshot(r)
    r.next_due_on = next_due(r, r.next_due_on)
    r.last_reminded_on = None
    r.version += 1
    await session.flush()
    await _audit(session, principal, r, "update", before)
    await publish(session, principal.user_id, ["recurring"], source=principal.source)
    return r


# ------------------------------------------------------------------- reminder text


async def reminder_payload(
    session: AsyncSession, r: RecurringTransaction, today: date
) -> dict[str, Any]:
    """DESIGN.md §12.8: «⏰ Сегодня обычно платишь / <b>Аренда</b> — 11 000 000 ₫ · Карта»."""
    import html

    from app.services.events.outbox import make_payload
    from app.services.stats.periods import day_label

    parts = [f"<b>{html.escape(r.title)}</b>"]
    tail: list[str] = []
    if r.amount is not None:
        tail.append(html.escape(format_money(r.amount, r.currency)))
    if r.account_id is not None:
        acc = await session.get(Account, r.account_id)
        if acc is not None:
            tail.append(html.escape(acc.name))
    head = (
        "⏰ Сегодня обычно платишь"
        if r.next_due_on >= today
        else (f"⏰ Платёж по графику на {day_label(r.next_due_on, today)}")
    )
    if r.kind == "income":
        head = (
            "⏰ Сегодня обычно поступает"
            if r.next_due_on >= today
            else (f"⏰ Поступление по графику на {day_label(r.next_due_on, today)}")
        )
    line = parts[0] + (" — " + " · ".join(tail) if tail else "")
    # The occurrence rides in the button (API.md §13: r:{id}:{yyyymmdd}); the bot passes it as
    # ?due_on= so a double tap / an old keyboard cannot record or skip the same period twice.
    due = r.next_due_on.strftime("%Y%m%d")
    buttons = [
        [
            {"text": "✅ Записать", "callback": f"r:{r.id}:{due}"},
            {"text": "⏭ Пропустить", "callback": f"k:{r.id}:{due}"},
        ],
        [{"text": "✏️ Другая сумма", "callback": f"m:{r.id}:{due}"}],
    ]
    return make_payload(f"{head}\n{line}", buttons)

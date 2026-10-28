"""Telegram notifications outbox. Bot pulls via /bot/outbox/*.

Lifecycle: ``pending`` → claim → ``sending`` (locked 60 s) → ack ok → ``sent``;
ack error → back to ``pending`` with backoff 30 s × 2^n (max 1 h), ``failed`` after 8 attempts
or on a permanent error (retry=false). A ``sending`` row whose lock expired is claimable again
(at-least-once delivery: the bot crashed between send and ack).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import NotFound
from app.core.timeutil import utcnow
from app.models import NotificationOutbox, UserSettings
from app.schemas.outbox import OutboxItem, OutboxPayload

log = logging.getLogger("pocket.outbox")

LOCK_SECONDS = 60
MAX_ATTEMPTS = 8
BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 3600


def make_payload(text: str, buttons: list[list[dict[str, str]]] | None = None) -> dict[str, Any]:
    """Payload dict validated against OutboxPayload (callback_data ≤ 64 bytes)."""
    p = OutboxPayload.model_validate({"text": text, "parse_mode": "HTML", "buttons": buttons or []})
    for row in p.buttons:
        for b in row:
            if len(b.callback.encode()) > 64:
                raise ValueError(f"callback too long: {b.callback!r}")
    return p.model_dump(mode="json")


def resolve_target(bound: int | None) -> int | None:
    """Telegram chat of a user: the bound id, else the single allowed owner id (one-user
    install, the owner has not written to the bot yet), else None (nowhere to deliver)."""
    if bound:
        return int(bound)
    allowed = get_settings().telegram_allowed_ids
    return int(allowed[0]) if len(allowed) == 1 else None


async def delivery_target(session: AsyncSession, user_id: int) -> int | None:
    """``resolve_target`` for one user (same rule as ``claim``)."""
    bound = await session.scalar(
        select(UserSettings.telegram_user_id).where(UserSettings.user_id == user_id)
    )
    return resolve_target(bound)


async def enqueue(
    session: AsyncSession,
    user_id: int,
    kind: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
) -> NotificationOutbox | None:
    """INSERT … ON CONFLICT (dedupe_key) DO NOTHING. Returns None if deduplicated."""
    payload = OutboxPayload.model_validate(payload).model_dump(mode="json")
    stmt = (
        pg_insert(NotificationOutbox)
        .values(
            user_id=user_id,
            channel="telegram",
            kind=kind,
            payload=payload,
            dedupe_key=dedupe_key,
            status="pending",
        )
        .on_conflict_do_nothing(index_elements=[NotificationOutbox.dedupe_key])
        .returning(NotificationOutbox.id)
    )
    new_id = (await session.execute(stmt)).scalar_one_or_none()
    if new_id is None:
        return None
    return await session.get(NotificationOutbox, new_id)


async def claim(session: AsyncSession, user_id: int | None, limit: int = 20) -> list[OutboxItem]:
    """SELECT … FOR UPDATE SKIP LOCKED where pending & available; mark sending, locked 60 s."""
    now = utcnow()
    q = (
        select(NotificationOutbox)
        .where(
            NotificationOutbox.channel == "telegram",
            or_(
                and_(
                    NotificationOutbox.status == "pending",
                    NotificationOutbox.available_at <= now,
                ),
                and_(
                    NotificationOutbox.status == "sending",
                    or_(
                        NotificationOutbox.locked_until.is_(None),
                        NotificationOutbox.locked_until < now,
                    ),
                ),
            ),
        )
        .order_by(NotificationOutbox.available_at, NotificationOutbox.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if user_id is not None:
        q = q.where(NotificationOutbox.user_id == user_id)
    rows = list((await session.scalars(q)).all())
    if not rows:
        return []
    tg_ids = dict(
        (
            await session.execute(
                select(UserSettings.user_id, UserSettings.telegram_user_id).where(
                    UserSettings.user_id.in_({r.user_id for r in rows})
                )
            )
        ).all()
    )
    items: list[OutboxItem] = []
    for r in rows:
        tg = resolve_target(tg_ids.get(r.user_id))
        if tg is None:
            r.status = "failed"
            r.error = "no telegram_user_id bound"
            continue
        r.status = "sending"
        r.locked_until = now + timedelta(seconds=LOCK_SECONDS)
        items.append(
            OutboxItem(
                id=r.id,
                kind=r.kind,
                telegram_user_id=tg,
                payload=OutboxPayload.model_validate(r.payload),
                attempts=r.attempts,
            )
        )
    await session.flush()
    return items


async def ack(
    session: AsyncSession,
    user_id: int | None,
    item_id: int,
    *,
    ok: bool,
    error: str | None,
    retry: bool,
) -> None:
    """ok → sent; else attempts+1, backoff (30 s × 2^n, max 1 h), failed after 8 attempts."""
    item = await session.get(NotificationOutbox, item_id, with_for_update=True)
    if item is None or (user_id is not None and item.user_id != user_id):
        raise NotFound("Уведомление не найдено")
    if item.status in ("sent", "failed"):
        return  # idempotent repeat
    now = utcnow()
    item.locked_until = None
    if ok:
        item.status = "sent"
        item.sent_at = now
        item.error = None
    else:
        item.attempts = (item.attempts or 0) + 1
        item.error = (error or "unknown error")[:1000]
        if retry and item.attempts < MAX_ATTEMPTS:
            delay = min(BACKOFF_BASE_SECONDS * 2 ** (item.attempts - 1), BACKOFF_MAX_SECONDS)
            item.status = "pending"
            item.available_at = now + timedelta(seconds=delay)
        else:
            item.status = "failed"
            log.warning("outbox %s failed permanently: %s", item.id, item.error)
    await session.flush()

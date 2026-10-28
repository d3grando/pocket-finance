"""Housekeeping of auth tables. Called by the worker's hourly ``cleanup`` job
(scheduler, ARCH §9): ``await cleanup_auth(session); await session.commit()``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuthChallenge, DeviceToken, IdempotencyKey, Session

#: Keep revoked/expired rows a while for the «sessions/devices» history and audits.
KEEP_DEAD_SESSIONS = timedelta(days=30)
KEEP_REVOKED_DEVICES = timedelta(days=90)
KEEP_CHALLENGES = timedelta(days=1)


async def cleanup_auth(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Delete stale WebAuthn challenges, long-dead sessions, old revoked device tokens and
    expired idempotency keys. Returns counts per table. Does not commit."""
    now = now or datetime.now(UTC)
    counts: dict[str, int] = {}

    res = await session.execute(
        delete(AuthChallenge).where(AuthChallenge.expires_at < now - KEEP_CHALLENGES)
    )
    counts["auth_challenges"] = int(res.rowcount or 0)  # type: ignore[attr-defined]

    res = await session.execute(
        delete(Session).where(
            or_(
                Session.expires_at < now - KEEP_DEAD_SESSIONS,
                Session.revoked_at < now - KEEP_DEAD_SESSIONS,
            )
        )
    )
    counts["sessions"] = int(res.rowcount or 0)  # type: ignore[attr-defined]

    res = await session.execute(
        delete(DeviceToken).where(DeviceToken.revoked_at < now - KEEP_REVOKED_DEVICES)
    )
    counts["device_tokens"] = int(res.rowcount or 0)  # type: ignore[attr-defined]

    res = await session.execute(delete(IdempotencyKey).where(IdempotencyKey.expires_at < now))
    counts["idempotency_keys"] = int(res.rowcount or 0)  # type: ignore[attr-defined]
    return counts

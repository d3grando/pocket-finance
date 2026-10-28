"""Electron device tokens.

Issued by ``POST /auth/device-login`` after the password; ``pfd_`` + 32 random bytes. Only the
HMAC hash is stored. No expiry; revoked in Settings → the very next request gets 401.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.security import DEVICE_TOKEN_PREFIX, new_device_token, token_hash
from app.models import DeviceToken, User

#: ``last_used_at`` is written at most this often (or when the IP changes).
TOUCH_EVERY = timedelta(minutes=5)


def utcnow() -> datetime:
    return datetime.now(UTC)


async def create_device_token(
    session: AsyncSession, user: User, *, name: str, platform: str, ip: str | None = None
) -> tuple[DeviceToken, str]:
    """Return (row, raw token 'pfd_…'). Raw token is shown once."""
    raw = new_device_token()
    row = DeviceToken(
        user_id=user.id,
        name=name.strip()[:100] or "Устройство",
        platform=platform,
        token_hash=token_hash(raw),
        last_used_at=utcnow(),
        last_ip=ip,
    )
    session.add(row)
    await session.flush()
    return row, raw


async def find_active_device(
    session: AsyncSession, raw_token: str
) -> tuple[DeviceToken, User] | None:
    if not raw_token.startswith(DEVICE_TOKEN_PREFIX):
        return None
    row = (
        await session.execute(
            select(DeviceToken, User)
            .join(User, User.id == DeviceToken.user_id)
            .where(DeviceToken.token_hash == token_hash(raw_token))
        )
    ).first()
    if row is None:
        return None
    dev, user = row
    if dev.revoked_at is not None or not user.is_active:
        return None
    return dev, user


def touch(dev: DeviceToken, ip: str | None) -> bool:
    """Update last_used_at/last_ip if stale; return True if something changed."""
    now = utcnow()
    changed = False
    if dev.last_used_at is None or now - dev.last_used_at >= TOUCH_EVERY:
        dev.last_used_at = now
        changed = True
    if ip and (dev.last_ip is None or str(dev.last_ip) != ip):
        dev.last_ip = ip
        dev.last_used_at = now
        changed = True
    return changed


async def list_devices(session: AsyncSession, user_id: int) -> list[DeviceToken]:
    """Active (not revoked) devices, most recently used first."""
    return list(
        (
            await session.execute(
                select(DeviceToken)
                .where(DeviceToken.user_id == user_id, DeviceToken.revoked_at.is_(None))
                .order_by(
                    DeviceToken.last_used_at.desc().nulls_last(), DeviceToken.created_at.desc()
                )
            )
        ).scalars()
    )


async def revoke_device(session: AsyncSession, user_id: int, device_id: uuid.UUID) -> None:
    dev = (
        await session.execute(
            select(DeviceToken).where(DeviceToken.id == device_id, DeviceToken.user_id == user_id)
        )
    ).scalar_one_or_none()
    if dev is None:
        raise NotFound("Устройство не найдено")
    if dev.revoked_at is None:
        dev.revoked_at = utcnow()
        await session.flush()


async def revoke_all_devices(
    session: AsyncSession, user_id: int, *, except_id: uuid.UUID | None = None
) -> int:
    stmt = (
        update(DeviceToken)
        .where(DeviceToken.user_id == user_id, DeviceToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    if except_id is not None:
        stmt = stmt.where(DeviceToken.id != except_id)
    res = await session.execute(stmt)
    return int(res.rowcount or 0)  # type: ignore[attr-defined]

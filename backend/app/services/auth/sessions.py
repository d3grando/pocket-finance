"""Web/PWA cookie sessions.

Server-side sessions in ``sessions``: only HMAC hashes of the session token and of its CSRF token
are stored. Lifetime SESSION_TTL_DAYS (90) with sliding renewal at most once per day.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import NotFound, Unauthorized
from app.core.security import (
    csrf_token_for,
    hash_password_async,
    new_session_token,
    password_needs_rehash,
    token_hash,
    verify_password_async,
)
from app.core.security_cookies import clear_session_cookies as _clear_cookies
from app.core.security_cookies import set_session_cookies as _set_cookies
from app.models import Session, User

log = logging.getLogger("pocket.auth")

#: Sliding renewal granularity (ARCH §6: not more often than once a day).
RENEW_EVERY = timedelta(days=1)
WRONG_PASSWORD = "Неверный пароль"


def utcnow() -> datetime:
    return datetime.now(UTC)


def session_ttl() -> timedelta:
    return timedelta(days=int(get_settings().session_ttl_days))


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    return (
        await session.execute(select(User).where(User.username == username.strip()))
    ).scalar_one_or_none()


async def authenticate_password(session: AsyncSession, username: str, password: str) -> User:
    """Verify argon2id password; raise Unauthorized('Неверный пароль') on failure.

    Unknown user / no password / inactive → the same error after a dummy hash (no enumeration).
    Transparently re-hashes outdated argon2 parameters (caller commits).
    """
    user = await get_user_by_username(session, username or "owner")
    ok = await verify_password_async(user.password_hash if user else None, password)
    if not ok or user is None or not user.is_active:
        raise Unauthorized(WRONG_PASSWORD)
    if user.password_hash and password_needs_rehash(user.password_hash):
        user.password_hash = await hash_password_async(password)
    return user


async def create_session(
    session: AsyncSession, user: User, *, user_agent: str | None, ip: str | None
) -> tuple[Session, str, str]:
    """Create a session row; return (row, session_token, csrf_token) — raw tokens only here."""
    raw = new_session_token()
    csrf = csrf_token_for(raw)
    now = utcnow()
    row = Session(
        user_id=user.id,
        token_hash=token_hash(raw),
        csrf_hash=token_hash(csrf),
        user_agent=(user_agent or None) and user_agent[:512],
        ip=ip,
        last_seen_at=now,
        expires_at=now + session_ttl(),
    )
    session.add(row)
    await session.flush()
    return row, raw, csrf


def set_session_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    """pf_session (HttpOnly, Secure per COOKIE_SECURE, SameSite=Lax, 90d) +
    pf_csrf (readable, SameSite=Strict, 90d)."""
    _set_cookies(response, session_token, csrf_token)


def clear_session_cookies(response: Response) -> None:
    _clear_cookies(response)


async def find_active_session(
    session: AsyncSession, session_token: str
) -> tuple[Session, User] | None:
    """Live (not revoked, not expired) session + active user by raw cookie value."""
    row = (
        await session.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.token_hash == token_hash(session_token))
        )
    ).first()
    if row is None:
        return None
    s, user = row
    if s.revoked_at is not None or s.expires_at <= utcnow() or not user.is_active:
        return None
    return s, user


def needs_renewal(s: Session, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return now - s.last_seen_at >= RENEW_EVERY


def renew(s: Session, *, ip: str | None = None, now: datetime | None = None) -> None:
    now = now or utcnow()
    s.last_seen_at = now
    s.expires_at = now + session_ttl()
    if ip:
        s.ip = ip


async def revoke_session(session: AsyncSession, user_id: int, session_id: uuid.UUID) -> None:
    res = await session.execute(
        update(Session)
        .where(Session.id == session_id, Session.user_id == user_id, Session.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    if res.rowcount == 0:  # type: ignore[attr-defined]
        exists = await session.scalar(
            select(func.count()).where(Session.id == session_id, Session.user_id == user_id)
        )
        if not exists:
            raise NotFound("Сессия не найдена")


async def revoke_all_sessions(
    session: AsyncSession, user_id: int, *, except_id: uuid.UUID | None = None
) -> int:
    stmt = (
        update(Session)
        .where(Session.user_id == user_id, Session.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    if except_id is not None:
        stmt = stmt.where(Session.id != except_id)
    res = await session.execute(stmt)
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


async def list_sessions(session: AsyncSession, user_id: int) -> list[Session]:
    """Active sessions, most recently seen first."""
    return list(
        (
            await session.execute(
                select(Session)
                .where(
                    Session.user_id == user_id,
                    Session.revoked_at.is_(None),
                    Session.expires_at > utcnow(),
                )
                .order_by(Session.last_seen_at.desc())
            )
        ).scalars()
    )

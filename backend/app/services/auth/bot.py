"""Bot service authentication. ARCH §6.

``Authorization: Bearer <BOT_SERVICE_TOKEN>`` (constant-time compare) + ``X-Telegram-User-Id``.
The id must be in TELEGRAM_ALLOWED_IDS and map to ``user_settings.telegram_user_id`` (bound on
first contact to the owner when nobody has it yet). Anything else → 403 ``telegram_forbidden``.
"""

from __future__ import annotations

import logging
import re

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import Forbidden, Unauthorized
from app.core.principal import Principal
from app.core.security import constant_time_equals
from app.models import User, UserSettings
from app.services.auth.settings import load_settings, new_settings_row

log = logging.getLogger("pocket.auth")

TELEGRAM_USER_HEADER = "X-Telegram-User-Id"
OWNER_USERNAME = "owner"


FORBIDDEN_TG = "Этот Telegram-аккаунт не может пользоваться ботом"


def telegram_forbidden(message: str = FORBIDDEN_TG) -> Forbidden:
    return Forbidden(message, code="telegram_forbidden")


def parse_bearer(raw: str) -> str | None:
    """Token of an ``Authorization`` header value (``Bearer <token>``, scheme case-insensitive,
    surrounding spaces ignored); None if it is not a usable Bearer header. Never raises: the
    rate limiter keys its buckets with the same parse that authentication uses."""
    scheme, _, token = raw.strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def bearer_token(request: Request) -> str | None:
    """Raw token from ``Authorization: Bearer …`` (None if no header); 401 on other schemes."""
    auth = request.headers.get("authorization")
    if auth is None:
        return None
    token = parse_bearer(auth)
    if token is None:
        raise Unauthorized("Неверный заголовок Authorization")
    return token


def is_bot_token(token: str) -> bool:
    expected = get_settings().bot_service_token
    return bool(expected) and constant_time_equals(token, expected)


#: ASCII digits only: ``str.isdigit()`` also accepts «²», «¹» etc., which ``int()`` rejects.
_TG_ID_RE = re.compile(r"[0-9]{1,20}", re.ASCII)
_BIGINT_MAX = 2**63 - 1


def parse_telegram_id(raw: str | None) -> int | None:
    """``X-Telegram-User-Id`` → int; anything but 1–20 ASCII digits (≤ BIGINT) → 403."""
    if raw is None:
        return None
    raw = raw.strip()
    if not _TG_ID_RE.fullmatch(raw):
        raise telegram_forbidden("Некорректный X-Telegram-User-Id")
    value = int(raw)
    if value <= 0 or value > _BIGINT_MAX:
        raise telegram_forbidden("Некорректный X-Telegram-User-Id")
    return value


def mask_telegram_id(telegram_user_id: int | None) -> str:
    """Telegram id for logs: only the last 3 digits (no PII in logs)."""
    return "…" + str(telegram_user_id)[-3:] if telegram_user_id is not None else "-"


async def _owner_user(session: AsyncSession) -> User | None:
    """The single user, or the one named ``owner`` when test users exist."""
    users = list(
        (
            await session.execute(
                select(User).where(User.is_active.is_(True)).order_by(User.id).limit(2)
            )
        ).scalars()
    )
    if len(users) == 1:
        return users[0]
    return (
        await session.execute(
            select(User).where(User.username == OWNER_USERNAME, User.is_active.is_(True))
        )
    ).scalar_one_or_none()


async def resolve_bot_user(
    request: Request, session: AsyncSession, telegram_user_id: int
) -> Principal:
    """Bot token already verified. Check telegram id ∈ TELEGRAM_ALLOWED_IDS and map it to the user
    (user_settings.telegram_user_id; bind on first contact if the single user has none).
    Raise Forbidden(code='telegram_forbidden') otherwise."""
    if telegram_user_id not in get_settings().telegram_allowed_ids:
        log.warning("bot: telegram id %s is not allowed", mask_telegram_id(telegram_user_id))
        raise telegram_forbidden()
    row = (
        await session.execute(
            select(UserSettings, User)
            .join(User, User.id == UserSettings.user_id)
            .where(UserSettings.telegram_user_id == telegram_user_id)
        )
    ).first()
    if row is not None:
        _, user = row
        if not user.is_active:
            raise telegram_forbidden()
        return Principal(
            user_id=user.id, kind="bot", source="telegram", telegram_user_id=telegram_user_id
        )
    # First contact: bind to the owner if their slot is empty.
    owner = await _owner_user(session)
    if owner is None:
        raise telegram_forbidden()
    st = await load_settings(session, owner.id)
    if st is None:
        st = new_settings_row(owner.id, telegram_user_id=telegram_user_id)
        session.add(st)
    elif st.telegram_user_id is None:
        st.telegram_user_id = telegram_user_id
        st.version = (st.version or 1) + 1
    else:
        raise telegram_forbidden()
    await session.flush()
    await session.commit()
    log.info("bot: bound telegram id %s to user %s", mask_telegram_id(telegram_user_id), owner.id)
    return Principal(
        user_id=owner.id, kind="bot", source="telegram", telegram_user_id=telegram_user_id
    )


async def resolve_bot_service(request: Request, session: AsyncSession) -> Principal:
    """Bot token WITHOUT a Telegram user (outbox endpoints). Returns Principal(kind='bot',
    user_id=<owner user id>, source='telegram'). Constant-time token comparison."""
    token = bearer_token(request)
    if token is None or not is_bot_token(token):
        raise Unauthorized("Нужен сервисный токен бота")
    owner = await _owner_user(session)
    if owner is None:
        raise Unauthorized("Пользователь не создан")
    st = await load_settings(session, owner.id)
    tg = st.telegram_user_id if st is not None else None
    principal = Principal(user_id=owner.id, kind="bot", source="telegram", telegram_user_id=tg)
    request.state.principal = principal
    return principal

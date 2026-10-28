"""Resolve the request principal. Order: Bearer pfd_ device token →
Bearer BOT_SERVICE_TOKEN + X-Telegram-User-Id → pf_session cookie. Contract: docs/ARCH.md §6.

* If an ``Authorization`` header is present, cookies are ignored (no fallback): Bearer requests
  never need CSRF and a bad token is a plain 401.
* More than one ``pf_session`` cookie in a request → 400 ``bad_request``.
* Cookie sessions are accepted only from the web origins; a request whose ``Origin`` is the
  packaged Electron origin (DESKTOP_ORIGIN) must use its device token (CORS for Electron is
  Bearer-only).
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import Unauthorized
from app.core.principal import Principal
from app.core.security import csrf_token_for
from app.core.security_config import origin_of
from app.middleware.session_cookies import STATE_KEY as COOKIE_STATE_KEY
from app.middleware.session_cookies import session_cookie_value
from app.services.auth import devices as devices_svc
from app.services.auth import sessions as sessions_svc
from app.services.auth.bot import (
    TELEGRAM_USER_HEADER,
    bearer_token,
    is_bot_token,
    parse_telegram_id,
    resolve_bot_user,
    telegram_forbidden,
)

SESSION_EXPIRED = "Сессия закончилась — войдите снова"
DEVICE_REVOKED = "Устройство отозвано — войдите снова"


def client_ip(request: Request) -> str | None:
    ip = getattr(request.state, "client_ip", None)
    if ip:
        return str(ip)
    return request.client.host if request.client else None


async def resolve_principal(request: Request, session: AsyncSession) -> Principal:
    """Return the Principal or raise Unauthorized/Forbidden.

    * cookie session → kind=session, source=web; sets ``request.state.csrf_hash``;
      sliding renewal (last_seen_at/expires_at) at most once per day.
    * device token   → kind=device, source=desktop; updates last_used_at (throttled).
    * bot            → kind=bot, source=telegram; see services/auth/bot.py.
    """
    token = bearer_token(request)
    if token is not None:
        return await _from_bearer(request, session, token)
    return await _from_cookie(request, session)


async def _from_bearer(request: Request, session: AsyncSession, token: str) -> Principal:
    found = await devices_svc.find_active_device(session, token)
    if found is not None:
        dev, user = found
        if devices_svc.touch(dev, client_ip(request)):
            await session.flush()
            await session.commit()
        request.state.device = dev
        return Principal(user_id=user.id, kind="device", source="desktop", device_id=dev.id)
    if token.startswith("pfd_"):
        raise Unauthorized(DEVICE_REVOKED)
    if is_bot_token(token):
        tg_id = parse_telegram_id(request.headers.get(TELEGRAM_USER_HEADER))
        if tg_id is None:
            raise telegram_forbidden("Нужен заголовок X-Telegram-User-Id")
        return await resolve_bot_user(request, session, tg_id)
    raise Unauthorized("Неверный токен")


async def _from_cookie(request: Request, session: AsyncSession) -> Principal:
    # The same parser as the rate limiter; two pf_session cookies → 400 (no «last one wins»).
    raw = session_cookie_value(request.headers)
    if not raw:
        raise Unauthorized()
    origin = request.headers.get("origin")
    if origin is not None and origin_of(origin) == origin_of(get_settings().desktop_origin):
        raise Unauthorized("Приложению нужен токен устройства")
    found = await sessions_svc.find_active_session(session, raw)
    if found is None:
        setattr(request.state, COOKIE_STATE_KEY, ("clear",))
        raise Unauthorized(SESSION_EXPIRED)
    s, user = found
    if sessions_svc.needs_renewal(s):
        sessions_svc.renew(s, ip=client_ip(request))
        await session.flush()
        await session.commit()
        setattr(request.state, COOKIE_STATE_KEY, ("renew", raw, csrf_token_for(raw)))
    request.state.csrf_hash = s.csrf_hash
    request.state.auth_session = s
    request.state.session_token = raw
    return Principal(user_id=user.id, kind="session", source="web", session_id=s.id)

"""Auth/session dependencies without idempotency (avoids import cycles).

Import from ``app.api.deps`` in routes; ``app.middleware.idempotency`` imports from here."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Forbidden
from app.core.principal import Principal
from app.db import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_principal(request: Request, session: SessionDep) -> Principal:
    """Any authenticated principal (cookie session, device Bearer, bot service token).

    Also enforces CSRF for cookie sessions on unsafe methods (POST/PUT/PATCH/DELETE).
    """
    from app.middleware.csrf import enforce_csrf
    from app.services.auth.principal import resolve_principal

    principal = await resolve_principal(request, session)
    enforce_csrf(request, principal)
    request.state.principal = principal
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def get_human_principal(principal: PrincipalDep) -> Principal:
    """Session or device only (not the bot): password/passkey/device management."""
    if principal.kind not in ("session", "device"):
        raise Forbidden()
    return principal


HumanPrincipalDep = Annotated[Principal, Depends(get_human_principal)]


async def get_session_principal(principal: PrincipalDep) -> Principal:
    """Cookie session only (passkey registration, session list)."""
    if principal.kind != "session":
        raise Forbidden("Доступно только в веб-версии")
    return principal


SessionPrincipalDep = Annotated[Principal, Depends(get_session_principal)]


async def get_bot_principal(request: Request, session: SessionDep) -> Principal:
    """Bot service token WITHOUT a Telegram user (outbox endpoints)."""
    from app.services.auth.bot import resolve_bot_service

    return await resolve_bot_service(request, session)


BotServiceDep = Annotated[Principal, Depends(get_bot_principal)]

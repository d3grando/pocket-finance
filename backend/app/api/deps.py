"""Shared FastAPI dependencies for routes.

Implementations live in auth modules (services/auth, middleware).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.api.deps_base import (
    BotServiceDep,
    HumanPrincipalDep,
    PrincipalDep,
    SessionDep,
    SessionPrincipalDep,
    get_bot_principal,
    get_human_principal,
    get_principal,
    get_session_principal,
)
from app.middleware.idempotency import (
    Idempotency,
    idempotency_optional,
    idempotency_required,
)
from app.middleware.ratelimit import limiter

#: Idempotency-Key required (428 if missing). See docs/ARCH.md §5.2.
IdemDep = Annotated[Idempotency, Depends(idempotency_required)]
#: Idempotency-Key optional.
IdemOptDep = Annotated[Idempotency, Depends(idempotency_optional)]


async def ai_user_rate_limit(principal: PrincipalDep) -> None:
    """AI messages per *user* (RATE_LIMIT_AI_PER_MINUTE), on top of the middleware's bucket per
    credential: the cookie session, device tokens and the bot share one budget. 429 + Retry-After.
    Same in-memory store as the rate-limit middleware (``limiter``)."""
    limiter.take_user_ai(principal.user_id)


async def password_user_rate_limit(principal: PrincipalDep) -> None:
    """Password re-checks (change password, wipe, add passkey): at most
    RATE_LIMIT_LOGIN_FAILURES_PER_HOUR wrong passwords per *user* per hour, whatever credential
    is used (the middleware counts the failures; this check needs the user, so it runs here)."""
    limiter.check_user_password(principal.user_id)


#: Route-level ``dependencies=[...]`` for the AI endpoint / password re-checks.
AiUserRateLimit = Depends(ai_user_rate_limit)
PasswordUserRateLimit = Depends(password_user_rate_limit)

__all__ = [
    "AiUserRateLimit",
    "BotServiceDep",
    "HumanPrincipalDep",
    "IdemDep",
    "IdemOptDep",
    "Idempotency",
    "PasswordUserRateLimit",
    "PrincipalDep",
    "SessionDep",
    "SessionPrincipalDep",
    "ai_user_rate_limit",
    "get_bot_principal",
    "get_human_principal",
    "get_principal",
    "get_session_principal",
    "password_user_rate_limit",
]

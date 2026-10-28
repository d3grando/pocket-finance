"""SSE stream. docs/ARCH.md §8, docs/API.md §Events.

Auth is checked on connect AND for the life of the stream: before every ``change`` and at
least every ``REVALIDATE_SECONDS`` the cookie session / device token behind the stream is
re-read; once it is revoked (logout, «выйти со всех», DELETE /devices/{id}, revoke on password
change) or expired, the stream ends — the client reconnects and gets 401.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sqlalchemy import null, select
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.api.deps import PrincipalDep, SessionDep
from app.config import get_settings
from app.core.principal import Principal
from app.core.timeutil import utcnow
from app.models import DeviceToken, Session, User
from app.schemas.events import HelloEvent
from app.services.events import bus

log = logging.getLogger("pocket.events")

router = APIRouter(prefix="/events", tags=["events"])

#: Heartbeat interval (API.md: ping every 20 s; clients treat 30 s of silence as offline).
PING_SECONDS: float = 20.0
#: An idle stream re-checks its credentials this often (a busy one — before every event).
REVALIDATE_SECONDS: float = 20.0


def _ping() -> ServerSentEvent:
    return ServerSentEvent(event="ping", data="{}")


async def principal_alive(principal: Principal) -> bool:
    """Is the credential behind ``principal`` still valid? Session: not revoked, not expired,
    user active. Device token: not revoked, user active. Principals without a session/device
    id (bot, tests) — nothing to re-check. A DB error counts as «not alive»: the stream closes
    and the client simply reconnects (fail closed)."""
    from app.db import get_sessionmaker

    if principal.kind == "session" and principal.session_id is not None:
        stmt = (
            select(Session.revoked_at, Session.expires_at, User.is_active)
            .join(User, User.id == Session.user_id)
            .where(Session.id == principal.session_id, Session.user_id == principal.user_id)
        )
    elif principal.kind == "device" and principal.device_id is not None:
        stmt = (
            select(DeviceToken.revoked_at, null(), User.is_active)
            .join(User, User.id == DeviceToken.user_id)
            .where(DeviceToken.id == principal.device_id, DeviceToken.user_id == principal.user_id)
        )
    else:
        return True
    try:
        async with get_sessionmaker()() as s:
            row = (await s.execute(stmt)).first()
    except Exception as e:  # pragma: no cover - DB outage
        log.warning("SSE auth re-check failed: %s", e)
        return False
    if row is None:
        return False
    revoked_at, expires_at, is_active = row
    if revoked_at is not None or not is_active:
        return False
    return expires_at is None or expires_at > utcnow()


@router.get("/stream")
async def stream(request: Request, principal: PrincipalDep, session: SessionDep):
    """sse_starlette.EventSourceResponse: hello → change* → ping every 20 s.
    Headers: Cache-Control no-cache, X-Accel-Buffering no."""
    # The stream may stay open for hours: give the DB connection used by auth back to the pool.
    await session.close()
    await bus.start_listener()
    user_id = principal.user_id

    async def events() -> AsyncIterator[ServerSentEvent]:
        sub = bus.subscribe(user_id)
        try:
            hello = HelloEvent(server_time=utcnow(), version=get_settings().app_version)
            yield ServerSentEvent(event="hello", data=hello.model_dump_json())
            while True:
                try:
                    ev = await asyncio.wait_for(sub.get(), REVALIDATE_SECONDS)
                except TimeoutError:
                    if not await principal_alive(principal):
                        break  # revoked while idle
                    continue
                if ev is None or await request.is_disconnected():
                    break
                if not await principal_alive(principal):
                    break  # revoked: not a single event after logout / device revoke
                yield ServerSentEvent(event="change", data=ev.model_dump_json())
        finally:
            sub.close()

    return EventSourceResponse(
        events(),
        ping=PING_SECONDS,
        ping_message_factory=_ping,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

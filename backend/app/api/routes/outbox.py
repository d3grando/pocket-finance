"""Bot outbox. Auth: bot service token only (no X-Telegram-User-Id)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import BotServiceDep, SessionDep
from app.schemas.common import Id, OkOut
from app.schemas.outbox import OutboxAckIn, OutboxClaimOut
from app.services.events import outbox

router = APIRouter(prefix="/bot/outbox", tags=["bot"])


@router.post("/claim", response_model=OutboxClaimOut)
async def claim(
    principal: BotServiceDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    items = await outbox.claim(session, principal.user_id, limit)
    await session.commit()
    return OutboxClaimOut(items=items)


@router.post("/{item_id}/ack", response_model=OkOut)
async def ack(item_id: Id, body: OutboxAckIn, principal: BotServiceDep, session: SessionDep):
    await outbox.ack(
        session, principal.user_id, item_id, ok=body.ok, error=body.error, retry=body.retry
    )
    await session.commit()
    return OkOut()

"""AI assistant REST. docs/API.md §Assistant.

One endpoint for every client (web / desktop / telegram): ``source`` comes from the principal
(authorisation), never from the body. Parse/validation/AI problems → 200 with an error/unclear
card; 4xx only for auth, CSRF, idempotency, rate limits and an empty text (422)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import AiUserRateLimit, IdemDep, PrincipalDep, SessionDep
from app.schemas.assistant import AssistantMessageIn, AssistantResponse
from app.schemas.common import OkOut
from app.services.ai import pipeline

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/messages", response_model=AssistantResponse, dependencies=[AiUserRateLimit])
async def post_message(
    body: AssistantMessageIn, principal: PrincipalDep, session: SessionDep, idem: IdemDep
):
    """Always 200 with cards for parse/validation/AI errors (ErrorCard/UnclearCard).
    429 after RATE_LIMIT_AI_PER_MINUTE messages a minute per user (all credentials together)."""
    if idem.replay is not None:
        return idem.replay
    result = await pipeline.handle_message(session, principal, body)
    await session.commit()
    if _transient_nothing_written(result):
        # «AI недоступен» / internal error with nothing recorded is not a final answer: release
        # the key so [Повторить] (same Idempotency-Key) really asks again instead of replaying.
        await idem.release()
        return result
    return await idem.save(200, result)


def _transient_nothing_written(result: AssistantResponse) -> bool:
    return not result.action_ids and any(
        c.kind == "error" and getattr(c, "code", None) in ("ai_unavailable", "internal")
        for c in result.cards
    )


@router.post("/previews/{token}/confirm", response_model=AssistantResponse)
async def confirm_preview(
    token: uuid.UUID, principal: PrincipalDep, session: SessionDep, idem: IdemDep
):
    """Applies the preview → AppliedCard. Expired/stale → 200 with ErrorCard
    (code preview_expired / preview_stale)."""
    if idem.replay is not None:
        return idem.replay
    result = await pipeline.confirm_preview(session, principal, token)
    await session.commit()
    return await idem.save(200, result)


@router.post("/previews/{token}/cancel", response_model=OkOut)
async def cancel_preview(token: uuid.UUID, principal: PrincipalDep, session: SessionDep):
    await pipeline.cancel_preview(session, principal, token)
    await session.commit()
    return OkOut()

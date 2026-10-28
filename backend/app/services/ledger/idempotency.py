"""Ledger-level idempotency for mutating routes. ARCH §5.2.

Works together with (and below) the ``Idempotency-Key`` auth middleware: the action row
claims the key with INSERT … ON CONFLICT DO NOTHING inside the business transaction, so even
two concurrent requests with one key produce exactly one operation; the replay gets the stored
response of the first attempt. Different body with the same key → 409 idempotency_mismatch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.principal import Principal
from app.models.enums import ActionKind
from app.services.ledger.actions import (
    ActionContext,
    IdempotentReplay,
    begin_action,
    store_response,
)


def fingerprint(scope: str, body: Any) -> bytes:
    if isinstance(body, BaseModel):
        data: Any = body.model_dump(mode="json", exclude_unset=True)
    else:
        data = body
    raw = json.dumps({"scope": scope, "body": data}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _jsonable(result: Any) -> Any:
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    return result


async def run_idempotent(
    session: AsyncSession,
    principal: Principal,
    idem: Any,
    *,
    kind: ActionKind,
    scope: str,
    body: Any,
    status_code: int,
    op: Callable[[ActionContext], Awaitable[Any]],
) -> JSONResponse:
    """Run ``op(action)`` once per Idempotency-Key, commit, return the JSON response.

    ``idem`` is the route's ``Idempotency`` handle (replay from the middleware wins)."""
    if getattr(idem, "replay", None) is not None:
        return idem.replay
    key = getattr(idem, "key", None) or None
    if key is not None:
        key = str(key)[:128]
    try:
        ctx = await begin_action(
            session, principal, kind,
            idempotency_key=key,
            request_hash=fingerprint(scope, body) if key else None,
        )
    except IdempotentReplay as r:
        # nothing was written by this request; the session is discarded by get_session
        return await idem.save(r.status, r.response)
    result = await op(ctx)
    payload = _jsonable(result)
    await store_response(session, ctx.id, status_code, payload)
    await session.commit()
    return await idem.save(status_code, payload)

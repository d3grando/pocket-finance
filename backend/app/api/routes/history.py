"""Action log, undo, audit REST."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import IdemDep, PrincipalDep, SessionDep
from app.schemas.common import Offset
from app.schemas.history import ActionOut, AuditEntryOut, UndoResult
from app.services.ledger import actions
from app.services.ledger.audit import history_for
from app.services.ledger.common import check_query_text

router = APIRouter(tags=["history"])


def _out(a) -> ActionOut:  # type: ignore[no-untyped-def]
    return ActionOut(
        id=a.id, kind=a.kind, summary=a.summary, source=a.source, input_text=a.input_text,
        entity_refs=a.entity_refs or [], is_undoable=a.is_undoable, undone_at=a.undone_at,
        created_at=a.created_at,
    )


@router.get("/actions", response_model=list[ActionOut])
async def list_actions(
    principal: PrincipalDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[Offset, Query()] = 0,
    recent: bool = False,
):
    """recent=true → dialogue context (10 actions / 30 min)."""
    if recent:
        rows = await actions.recent_action_rows(session, principal.user_id)
    else:
        rows = await actions.list_actions(session, principal.user_id, limit=limit, offset=offset)
    return [_out(a) for a in rows]


@router.post("/actions/{action_id}/undo", response_model=UndoResult)
async def undo(action_id: uuid.UUID, principal: PrincipalDep, session: SessionDep, idem: IdemDep):
    if idem.replay is not None:
        return idem.replay
    res = await actions.undo_action(session, principal, action_id)
    await session.commit()
    return await idem.save(200, res)


@router.get("/audit", response_model=list[AuditEntryOut])
async def audit(
    principal: PrincipalDep,
    session: SessionDep,
    entity_type: str,
    entity_id: str,
):
    check_query_text(entity_type=entity_type, entity_id=entity_id)
    rows = await history_for(session, principal.user_id, entity_type, entity_id)
    return [AuditEntryOut.model_validate(r) for r in rows]

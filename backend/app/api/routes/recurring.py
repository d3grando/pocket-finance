"""Recurring reminders REST."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import IdemDep, IdemOptDep, PrincipalDep, SessionDep
from app.models.enums import ActionKind
from app.schemas.common import Id, OkOut, SafeDate
from app.schemas.recurring import RecurringCreate, RecurringOut, RecurringPatch, RecurringRecordIn
from app.schemas.transactions import MutationResult
from app.services.ledger.actions import ActionContext, finish_action
from app.services.ledger.idempotency import run_idempotent
from app.services.recurring import service as rec

router = APIRouter(prefix="/recurring", tags=["recurring"])


@router.get("", response_model=list[RecurringOut])
async def list_recurring(principal: PrincipalDep, session: SessionDep):
    return [rec.to_out(r) for r in await rec.list_recurring(session, principal.user_id)]


@router.post("", response_model=RecurringOut, status_code=201)
async def create_recurring(body: RecurringCreate, principal: PrincipalDep, session: SessionDep):
    r = await rec.create_recurring(session, principal, body)
    out = rec.to_out(r)
    await session.commit()
    return out


@router.patch("/{rid}", response_model=RecurringOut)
async def update_recurring(
    rid: Id, body: RecurringPatch, principal: PrincipalDep, session: SessionDep
):
    r = await rec.update_recurring(session, principal, rid, body)
    out = rec.to_out(r)
    await session.commit()
    return out


@router.delete("/{rid}", response_model=OkOut)
async def delete_recurring(rid: Id, principal: PrincipalDep, session: SessionDep):
    await rec.delete_recurring(session, principal, rid)
    await session.commit()
    return OkOut()


#: The reminder occurrence a button belongs to (``r:{id}:{yyyymmdd}`` → ``due_on``). When given,
#: a second record/skip of the same occurrence → 409 ``already_exists`` instead of a duplicate.
DueOn = Annotated[
    SafeDate | None,
    Query(description="Срок напоминания, к которому относится нажатие (из кнопки)"),
]


@router.post("/{rid}/record", response_model=MutationResult, status_code=201)
async def record(
    rid: Id,
    body: RecurringRecordIn,
    principal: PrincipalDep,
    session: SessionDep,
    idem: IdemDep,
    due_on: DueOn = None,
):
    """«Записать»: one action = the operation + the reminder moving on (undo reverts both)."""
    from app.services.ledger.transactions import to_out

    async def op(ctx: ActionContext) -> MutationResult:
        tx = await rec.record(session, principal, rid, body, due_on=due_on, action=ctx)
        await finish_action(session, ctx)
        return MutationResult(transaction=await to_out(session, tx), action_id=ctx.id)

    return await run_idempotent(
        session, principal, idem, kind=ActionKind.create_transaction,
        scope=f"POST /recurring/{rid}/record?due_on={due_on}", body=body, status_code=201,
        op=op,
    )  # fmt: skip


@router.post("/{rid}/skip", response_model=RecurringOut)
async def skip(
    rid: Id, principal: PrincipalDep, session: SessionDep, idem: IdemOptDep, due_on: DueOn = None
):
    if idem.replay is not None:
        return idem.replay
    r = await rec.skip(session, principal, rid, due_on=due_on)
    out = rec.to_out(r)
    await session.commit()
    return await idem.save(200, out)

"""Debts REST. Contract: docs/API.md §15, docs/DEBTS.md §7.
Operations are undone with ``POST /actions/{action_id}/undo``, edited with
``PATCH /transactions/{id}``, the real account is chosen with
``POST /transactions/{id}/account``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import IdemDep, PrincipalDep, SessionDep
from app.models.enums import ActionKind
from app.schemas.common import Id, Offset, OkOut
from app.schemas.debts import (
    DebtDetailOut,
    DebtOpCreate,
    DebtOpResult,
    DebtOut,
    DebtPatch,
    DebtSnoozeIn,
    DebtsOut,
)
from app.services.debts import service as debts
from app.services.ledger.actions import ActionContext, finish_action
from app.services.ledger.idempotency import run_idempotent

router = APIRouter(tags=["debts"])


@router.get("/debts", response_model=DebtsOut)
async def list_debts(principal: PrincipalDep, session: SessionDep, include_settled: bool = False):
    return await debts.list_debts(session, principal.user_id, include_settled=include_settled)


@router.get("/debts/{debt_id}", response_model=DebtDetailOut)
async def get_debt(
    debt_id: Id,
    principal: PrincipalDep,
    session: SessionDep,
    limit: int = Query(50, ge=1, le=500),
    offset: Annotated[Offset, Query()] = 0,
):
    return await debts.get_debt(session, principal.user_id, debt_id, limit=limit, offset=offset)


@router.post("/debts/operations", response_model=DebtOpResult, status_code=201)
async def create_debt_operation(
    body: DebtOpCreate, principal: PrincipalDep, session: SessionDep, idem: IdemDep
):
    async def op(ctx: ActionContext) -> DebtOpResult:
        res = await debts.record_op(session, principal, body, action=ctx)
        await finish_action(session, ctx)
        return res

    return await run_idempotent(
        session, principal, idem, kind=ActionKind.debt, scope="POST /debts/operations",
        body=body, status_code=201, op=op,
    )


@router.patch("/debts/{debt_id}", response_model=DebtOut)
async def update_debt(debt_id: Id, body: DebtPatch, principal: PrincipalDep, session: SessionDep):
    out = await debts.update_debt(session, principal, debt_id, body)
    await session.commit()
    return out


@router.delete("/debts/{debt_id}", response_model=OkOut)
async def delete_debt(debt_id: Id, principal: PrincipalDep, session: SessionDep):
    await debts.delete_debt(session, principal, debt_id)
    await session.commit()
    return OkOut()


@router.post("/debts/{debt_id}/snooze", response_model=DebtOut)
async def snooze_debt(
    debt_id: Id, principal: PrincipalDep, session: SessionDep, body: DebtSnoozeIn | None = None
):
    out = await debts.snooze(session, principal, debt_id, (body or DebtSnoozeIn()).days)
    await session.commit()
    return out

"""Transfers / exchanges / adjustments REST. Never income/expense."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import IdemDep, PrincipalDep, SessionDep
from app.models.enums import ActionKind
from app.schemas.transactions import (
    AdjustmentCreate,
    AdjustmentResult,
    ExchangeCreate,
    MutationResult,
    TransferCreate,
)
from app.services.ledger import transfers as ledger
from app.services.ledger.actions import ActionContext, discard_action, finish_action
from app.services.ledger.idempotency import run_idempotent
from app.services.ledger.summaries import to_out

router = APIRouter(tags=["transfers"])


@router.post("/transfers", response_model=MutationResult, status_code=201)
async def create_transfer(
    body: TransferCreate, principal: PrincipalDep, session: SessionDep, idem: IdemDep
):
    async def op(ctx: ActionContext) -> MutationResult:
        tx = await ledger.create_transfer(session, principal, body, action=ctx)
        await finish_action(session, ctx)
        return MutationResult(transaction=await to_out(session, tx), action_id=ctx.id)

    return await run_idempotent(
        session, principal, idem, kind=ActionKind.transfer, scope="POST /transfers",
        body=body, status_code=201, op=op,
    )


@router.post("/exchanges", response_model=MutationResult, status_code=201)
async def create_exchange(
    body: ExchangeCreate, principal: PrincipalDep, session: SessionDep, idem: IdemDep
):
    async def op(ctx: ActionContext) -> MutationResult:
        tx = await ledger.create_exchange(session, principal, body, action=ctx)
        await finish_action(session, ctx)
        return MutationResult(transaction=await to_out(session, tx), action_id=ctx.id)

    return await run_idempotent(
        session, principal, idem, kind=ActionKind.exchange, scope="POST /exchanges",
        body=body, status_code=201, op=op,
    )


@router.post("/adjustments", response_model=AdjustmentResult, status_code=201)
async def create_adjustment(
    body: AdjustmentCreate, principal: PrincipalDep, session: SessionDep, idem: IdemDep
):
    async def op(ctx: ActionContext) -> AdjustmentResult:
        res = await ledger.adjust_balance(session, principal, body, action=ctx)
        if res.kind == "matches":
            await discard_action(session, ctx)
        else:
            await finish_action(session, ctx)
        return res

    return await run_idempotent(
        session, principal, idem, kind=ActionKind.adjust_balance, scope="POST /adjustments",
        body=body, status_code=201, op=op,
    )

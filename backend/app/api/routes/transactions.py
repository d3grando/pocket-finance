"""Transactions REST. Contract: docs/API.md §Transactions."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError

from app.api.deps import IdemDep, IdemOptDep, PrincipalDep, SessionDep
from app.core.errors import NotFound, ValidationFailed
from app.models.enums import ActionKind
from app.schemas.common import Id, Offset, SafeDate
from app.schemas.history import AuditEntryOut
from app.schemas.transactions import (
    AssignAccountIn,
    MutationResult,
    NeedsAccountCount,
    TransactionCreate,
    TransactionFilter,
    TransactionOut,
    TransactionPage,
    TransactionPatch,
)
from app.services.ledger import query
from app.services.ledger import transactions as ledger
from app.services.ledger.actions import ActionContext, finish_action
from app.services.ledger.audit import history_for
from app.services.ledger.idempotency import run_idempotent

router = APIRouter(tags=["transactions"])


def transaction_filter(
    date_from: SafeDate | None = None,
    date_to: SafeDate | None = None,
    kind: Annotated[list[str] | None, Query()] = None,
    category_id: Annotated[list[Id] | None, Query(max_length=500)] = None,
    account_id: Annotated[list[Id] | None, Query(max_length=500)] = None,
    currency: str | None = None,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
    amount_currency: str | None = None,
    q: str | None = None,
    is_business: bool | None = None,
    status: Literal["posted", "needs_account"] | None = None,
    merchant_id: Id | None = None,
    sort: Literal["date_desc", "date_asc", "amount_desc", "amount_asc"] = "date_desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[Offset, Query()] = 0,
) -> TransactionFilter:
    """Query params → TransactionFilter (repeatable kind/category_id/account_id).
    Invalid values → 422 validation_error (not 500)."""
    try:
        return _validate_filter(
            date_from, date_to, kind, category_id, account_id, currency, min_amount,
            max_amount, amount_currency, q, is_business, status, merchant_id, sort, limit,
            offset,
        )
    except ValidationError as e:
        raise ValidationFailed(
            details={"fields": [{"loc": [str(x) for x in err["loc"]], "msg": err["msg"]}
                                for err in e.errors()]}
        ) from e


def _validate_filter(  # type: ignore[no-untyped-def]
    date_from, date_to, kind, category_id, account_id, currency, min_amount, max_amount,
    amount_currency, q, is_business, status, merchant_id, sort, limit, offset,
) -> TransactionFilter:
    return TransactionFilter.model_validate(
        {
            "date_from": date_from,
            "date_to": date_to,
            "kinds": kind,
            "category_ids": category_id,
            "account_ids": account_id,
            "currency": currency,
            "min_amount": str(min_amount) if min_amount is not None else None,
            "max_amount": str(max_amount) if max_amount is not None else None,
            "amount_currency": amount_currency,
            "q": q,
            "is_business": is_business,
            "status": status,
            "merchant_id": merchant_id,
            "sort": sort,
            "limit": limit,
            "offset": offset,
        }
    )


FilterDep = Annotated[TransactionFilter, Depends(transaction_filter)]


@router.get("/transactions", response_model=TransactionPage)
async def list_transactions(principal: PrincipalDep, session: SessionDep, f: FilterDep):
    return await query.list_transactions(session, principal.user_id, f)


@router.get("/transactions/needs-account/count", response_model=NeedsAccountCount)
async def needs_account_count(principal: PrincipalDep, session: SessionDep):
    return NeedsAccountCount(count=await query.count_needs_account(session, principal.user_id))


@router.get("/transactions/{tx_id}", response_model=TransactionOut)
async def get_transaction(tx_id: Id, principal: PrincipalDep, session: SessionDep):
    tx = await ledger.get_transaction(session, principal.user_id, tx_id)
    if tx.deleted_at is not None:
        raise NotFound("Операция удалена", details={"transaction_id": tx_id})
    return await ledger.to_out(session, tx)


async def _result(session, tx, ctx: ActionContext) -> MutationResult:  # type: ignore[no-untyped-def]
    await finish_action(session, ctx)
    return MutationResult(transaction=await ledger.to_out(session, tx), action_id=ctx.id)


@router.post("/transactions", response_model=MutationResult, status_code=201)
async def create_transaction(
    body: TransactionCreate, principal: PrincipalDep, session: SessionDep, idem: IdemDep
):
    async def op(ctx: ActionContext) -> MutationResult:
        tx = await ledger.create_transaction(session, principal, body, action=ctx)
        return await _result(session, tx, ctx)

    return await run_idempotent(
        session, principal, idem, kind=ActionKind.create_transaction,
        scope="POST /transactions", body=body, status_code=201, op=op,
    )


@router.patch("/transactions/{tx_id}", response_model=MutationResult)
async def update_transaction(
    tx_id: Id, body: TransactionPatch, principal: PrincipalDep, session: SessionDep,
    idem: IdemOptDep,
):
    async def op(ctx: ActionContext) -> MutationResult:
        tx = await ledger.update_transaction(session, principal, tx_id, body, action=ctx)
        return await _result(session, tx, ctx)

    return await run_idempotent(
        session, principal, idem, kind=ActionKind.update_transaction,
        scope=f"PATCH /transactions/{tx_id}", body=body, status_code=200, op=op,
    )


@router.delete("/transactions/{tx_id}", response_model=MutationResult)
async def delete_transaction(
    tx_id: Id, principal: PrincipalDep, session: SessionDep, idem: IdemOptDep,
    version: int | None = None,
):
    async def op(ctx: ActionContext) -> MutationResult:
        tx = await ledger.delete_transaction(
            session, principal, tx_id, version=version, action=ctx
        )
        return await _result(session, tx, ctx)

    return await run_idempotent(
        session, principal, idem, kind=ActionKind.delete_transaction,
        scope=f"DELETE /transactions/{tx_id}", body={"version": version}, status_code=200,
        op=op,
    )


@router.post("/transactions/{tx_id}/restore", response_model=MutationResult)
async def restore_transaction(
    tx_id: Id, principal: PrincipalDep, session: SessionDep, idem: IdemOptDep
):
    async def op(ctx: ActionContext) -> MutationResult:
        tx = await ledger.restore_transaction(session, principal, tx_id, action=ctx)
        return await _result(session, tx, ctx)

    return await run_idempotent(
        session, principal, idem, kind=ActionKind.restore_transaction,
        scope=f"POST /transactions/{tx_id}/restore", body=None, status_code=200, op=op,
    )


@router.post("/transactions/{tx_id}/account", response_model=MutationResult)
async def assign_account(
    tx_id: Id, body: AssignAccountIn, principal: PrincipalDep, session: SessionDep,
    idem: IdemOptDep,
):
    async def op(ctx: ActionContext) -> MutationResult:
        tx = await ledger.assign_account(
            session, principal, tx_id, body.account_id, version=body.version, action=ctx
        )
        return await _result(session, tx, ctx)

    return await run_idempotent(
        session, principal, idem, kind=ActionKind.assign_account,
        scope=f"POST /transactions/{tx_id}/account", body=body, status_code=200, op=op,
    )


@router.get("/transactions/{tx_id}/history", response_model=list[AuditEntryOut])
async def transaction_history(tx_id: Id, principal: PrincipalDep, session: SessionDep):
    await ledger.get_transaction(session, principal.user_id, tx_id)
    rows = await history_for(session, principal.user_id, "transaction", tx_id)
    return [AuditEntryOut.model_validate(r) for r in rows]

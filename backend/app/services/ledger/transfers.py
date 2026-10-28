"""Transfers, exchanges, balance adjustments. Never income/expense
(ARCH §4.2): they move money between own accounts / coins or reconcile a balance."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailed
from app.core.principal import Principal
from app.core.timeutil import local_today
from app.models import Transaction
from app.models.enums import ActionKind, TxKind, TxStatus
from app.schemas.transactions import (
    AdjustmentCreate,
    AdjustmentResult,
    ExchangeCreate,
    TransferCreate,
)
from app.services.accounts import service as accounts
from app.services.accounts.balances import asset_balance
from app.services.ledger.actions import ActionContext, begin_action
from app.services.ledger.common import (
    check_amount,
    check_derived,
    div,
    now,
    q_rate,
    resolve_when,
    user_tz,
    writer_lock,
)
from app.services.ledger.postings import lock_assets, rebuild_postings
from app.services.ledger.summaries import to_out
from app.services.ledger.transactions import _body_account, _finish, _flush, apply_valuation


async def create_transfer(
    session: AsyncSession,
    principal: Principal,
    data: TransferCreate,
    *,
    raw_text: str | None = None,
    ai_request_id: int | None = None,
    action: ActionContext | None = None,
) -> Transaction:
    """kind=transfer, postings −from/+to in the same asset (ensure_asset on multi-asset).
    Transfers to an own @handle (my_accounts) use that handle's wallet as ``to_account_id``."""
    uid = principal.user_id
    await writer_lock(session, uid)
    check_amount(data.amount)
    if data.from_account_id == data.to_account_id:
        raise ValidationFailed("Счета перевода должны различаться")
    src = await _body_account(session, uid, data.from_account_id, "from_account_id")
    dst = await _body_account(session, uid, data.to_account_id, "to_account_id")
    cur = data.currency.upper()
    await accounts.ensure_asset(session, src, cur)
    await accounts.ensure_asset(session, dst, cur)
    at, on = await resolve_when(session, uid, data.occurred_on, None)
    own = action is None
    ctx = action or await begin_action(
        session, principal, ActionKind.transfer, input_text=raw_text, ai_request_id=ai_request_id
    )
    tx = Transaction(
        user_id=uid, kind=TxKind.transfer, status=TxStatus.posted, occurred_at=at, occurred_on=on,
        amount=data.amount, currency=cur,
        account_id=src.id, account_amount=data.amount, account_asset=cur, account_rate=Decimal(1),
        counter_account_id=dst.id, counter_amount=data.amount, counter_asset=cur,
        description=data.description or None, source=principal.source, raw_text=raw_text,
        ai_request_id=ai_request_id, created_action_id=ctx.id, version=1,
    )
    await apply_valuation(session, tx)
    check_derived(tx)
    session.add(tx)
    await _flush(session, tx)
    touched = await rebuild_postings(session, tx)
    await _finish(session, principal, ctx, own, tx, None, "create", touched)
    return tx


async def create_exchange(
    session: AsyncSession,
    principal: Principal,
    data: ExchangeCreate,
    *,
    raw_text: str | None = None,
    ai_request_id: int | None = None,
    action: ActionContext | None = None,
) -> Transaction:
    """kind=exchange, actual rate = counter_amount/amount stored in account_rate.
    The same multi-asset account may be both sides (USDT → TON inside Биржа)."""
    uid = principal.user_id
    await writer_lock(session, uid)
    check_amount(data.amount)
    check_amount(data.counter_amount, "counter_amount")
    cur, ccur = data.currency.upper(), data.counter_currency.upper()
    if cur == ccur:
        raise ValidationFailed("Для обмена валюты должны различаться — это перевод")
    src = await _body_account(session, uid, data.from_account_id, "from_account_id")
    dst = await _body_account(session, uid, data.to_account_id, "to_account_id")
    await accounts.ensure_asset(session, src, cur)
    await accounts.ensure_asset(session, dst, ccur)
    at, on = await resolve_when(session, uid, data.occurred_on, None)
    own = action is None
    ctx = action or await begin_action(
        session, principal, ActionKind.exchange, input_text=raw_text, ai_request_id=ai_request_id
    )
    tx = Transaction(
        user_id=uid, kind=TxKind.exchange, status=TxStatus.posted, occurred_at=at, occurred_on=on,
        amount=data.amount, currency=cur,
        account_id=src.id, account_amount=data.amount, account_asset=cur,
        account_rate=q_rate(div(data.counter_amount, data.amount)),
        counter_account_id=dst.id, counter_amount=data.counter_amount, counter_asset=ccur,
        description=data.description or None, source=principal.source, raw_text=raw_text,
        ai_request_id=ai_request_id, created_action_id=ctx.id, version=1,
    )
    await apply_valuation(session, tx)
    check_derived(tx)
    session.add(tx)
    await _flush(session, tx)
    touched = await rebuild_postings(session, tx)
    await _finish(session, principal, ctx, own, tx, None, "create", touched)
    return tx


async def adjust_balance(
    session: AsyncSession,
    principal: Principal,
    data: AdjustmentCreate,
    *,
    raw_text: str | None = None,
    action: ActionContext | None = None,
) -> AdjustmentResult:
    """ARCH §4.10: opening set → adjustment for the delta (0 → 'matches', nothing written);
    opening unknown → set opening_balance = actual (action set_opening, undoable).

    With a caller-provided ``action`` its kind is set to adjust_balance / set_opening; on
    'matches' nothing is recorded (caller discards the action)."""
    uid = principal.user_id
    await writer_lock(session, uid)
    check_amount(data.actual_balance, "actual_balance")
    acc = accounts.check_not_debt(await accounts.get_account(session, uid, data.account_id))
    asset = data.asset.upper()
    aa = await accounts.ensure_asset(session, acc, asset)
    await lock_assets(session, {(acc.id, asset)}, "update")
    await session.refresh(aa)

    if aa.opening_balance is None:
        own = action is None
        ctx = action or await begin_action(
            session, principal, ActionKind.set_opening, input_text=raw_text
        )
        ctx.kind = ActionKind.set_opening
        await accounts.set_opening_balance(
            session, principal, acc.id, asset, data.actual_balance, action=ctx
        )
        if own:
            from app.services.ledger.actions import finish_action

            await finish_action(session, ctx)
        return AdjustmentResult(
            kind="opening_set", account_id=acc.id, asset=asset, previous_balance=None,
            balance=data.actual_balance, delta=None, transaction=None, action_id=ctx.id,
        )

    current = await asset_balance(session, aa)
    assert current is not None
    delta = data.actual_balance - current
    if delta == 0:
        return AdjustmentResult(
            kind="matches", account_id=acc.id, asset=asset, previous_balance=current,
            balance=current, delta=Decimal(0), transaction=None, action_id=None,
        )
    tz = await user_tz(session, uid)
    own = action is None
    ctx = action or await begin_action(
        session, principal, ActionKind.adjust_balance, input_text=raw_text
    )
    ctx.kind = ActionKind.adjust_balance
    tx = Transaction(
        user_id=uid, kind=TxKind.adjustment, status=TxStatus.posted,
        occurred_at=now(), occurred_on=local_today(tz),
        amount=abs(delta), currency=asset,
        account_id=acc.id, account_amount=delta, account_asset=asset, account_rate=Decimal(1),
        description="Сверка остатка", source=principal.source, raw_text=raw_text,
        created_action_id=ctx.id, version=1,
    )
    await apply_valuation(session, tx)
    check_derived(tx)
    session.add(tx)
    await _flush(session, tx)
    touched = await rebuild_postings(session, tx)
    await _finish(session, principal, ctx, own, tx, None, "create", touched)
    return AdjustmentResult(
        kind="adjusted", account_id=acc.id, asset=asset, previous_balance=current,
        balance=data.actual_balance, delta=delta, transaction=await to_out(session, tx),
        action_id=ctx.id,
    )

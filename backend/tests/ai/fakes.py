"""Minimal stand-ins for ledger / analytics services, backed by real ORM rows.

Assistant unit tests stay independent of the other modules' internals: the service functions the
dispatcher calls are monkeypatched with these (tests/ai/test_integration_real.py runs the real
ones). They follow the contracts in docs/ARCH.md §7 closely enough to exercise assistant logic
(atomicity, previews, undo, context).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import any_, func, select

from app.core.errors import AppError, NotFound, VersionConflict
from app.core.timeutil import local_today, occurred_at_for, utcnow
from app.models import (
    Account,
    AccountAsset,
    ActionLog,
    Category,
    Merchant,
    PreviewToken,
    Transaction,
)
from app.schemas.accounts import AccountOut, AssetBalanceOut
from app.schemas.assistant import AnswerCard
from app.schemas.history import UndoResult
from app.schemas.transactions import (
    AccountRef,
    AdjustmentResult,
    CategoryRef,
    MerchantRef,
    TransactionFilter,
    TransactionOut,
    TransactionPage,
    TransactionTotals,
)
from app.services.ledger.actions import ActionContext

TZ = "Asia/Ho_Chi_Minh"
CRYPTO_OR_USD = {"USD", "USDT", "TON", "BTC", "ETH", "USDC"}

# ----------------------------------------------------------------------------- actions


async def begin_action(session, principal, kind, *, input_text=None, ai_request_id=None):
    return ActionContext(id=uuid.uuid4(), kind=kind, principal=principal, input_text=input_text,
                         ai_request_id=ai_request_id)


async def finish_action(session, ctx: ActionContext, summary: str) -> ActionLog:
    row = ActionLog(
        id=ctx.id, user_id=ctx.principal.user_id, source=ctx.principal.source,
        kind=str(ctx.kind), summary=summary, input_text=ctx.input_text,
        entity_refs=ctx.entity_refs, undo_data=_jsonable(ctx.undo_data),
        ai_request_id=ctx.ai_request_id,
        # wall clock like the real begin_action: now() is the same for the whole test
        # transaction, and «отмени» must find the newest action deterministically
        created_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def last_undoable_action(session, user_id):
    return await session.scalar(
        select(ActionLog)
        .where(ActionLog.user_id == user_id, ActionLog.undone_at.is_(None),
               ActionLog.is_undoable.is_(True), ActionLog.kind != "undo")
        .order_by(ActionLog.created_at.desc())
        .limit(1)
    )


async def undo_action(session, principal, action_id) -> UndoResult:
    a = await session.get(ActionLog, action_id)
    if a is None or a.user_id != principal.user_id:
        raise NotFound()
    if a.undone_at is not None:
        return UndoResult(status="already_undone", undone_action_id=a.id,
                          undo_action_id=a.undone_by_action_id, summary=f"Отменено: {a.summary}",
                          changed=["transactions"])
    for u in a.undo_data:
        if u["type"] != "transaction":
            continue
        tx = await session.get(Transaction, int(u["id"]))
        if u["before"] is None:
            tx.deleted_at = utcnow()
        else:
            for k, v in u["before"].items():
                if k in ("amount", "account_amount") and v is not None:
                    v = Decimal(v)
                elif k == "occurred_on" and isinstance(v, str):
                    v = date.fromisoformat(v)
                elif k == "deleted_at" and isinstance(v, str):
                    v = datetime.fromisoformat(v)
                setattr(tx, k, v)
    undo_id = uuid.uuid4()
    session.add(ActionLog(id=undo_id, user_id=a.user_id, source=principal.source, kind="undo",
                          summary=f"Отменено: {a.summary}"))
    a.undone_at = utcnow()
    a.undone_by_action_id = undo_id
    await session.flush()
    return UndoResult(status="undone", undone_action_id=a.id, undo_action_id=undo_id,
                      summary=f"Отменено: {a.summary}", changed=["transactions", "stats"])


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, Decimal):
        return str(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


# ----------------------------------------------------------------------------- transactions

_SNAP = ("amount", "currency", "category_id", "account_id", "occurred_on", "description",
         "counterparty", "kind", "deleted_at", "account_amount")

FAIL_ON_DESCRIPTION = "boom"


async def create_transaction(session, principal, data, *, raw_text=None, ai_request_id=None,
                             action=None) -> Transaction:
    if data.description == FAIL_ON_DESCRIPTION:
        raise AppError("Сбой записи (тест)", code="validation_error", status=422)
    account_id = data.account_id
    status = "posted"
    if account_id is None and not data.account_none:
        acc = await session.scalar(
            select(Account).where(Account.user_id == principal.user_id,
                                  data.currency == any_(Account.default_for_currencies))
        )
        if acc is not None:
            account_id = acc.id
        elif data.currency in CRYPTO_OR_USD:
            status = "needs_account"
    on = data.occurred_on or local_today(TZ)
    tx = Transaction(
        user_id=principal.user_id, kind=data.kind, status=status,
        occurred_at=data.occurred_at or occurred_at_for(on, TZ), occurred_on=on,
        amount=data.amount, currency=data.currency, account_id=account_id,
        account_amount=data.amount if account_id else None,
        account_asset=data.currency if account_id else None,
        category_id=data.category_id, merchant_id=data.merchant_id,
        counterparty=data.counterparty, description=data.description,
        amount_vnd=data.amount if data.currency == "VND" else None,
        amount_usd=data.amount if data.currency in ("USD", "USDT") else None,
        source=principal.source, raw_text=raw_text, ai_request_id=ai_request_id,
        created_action_id=action.id if action else None,
    )
    session.add(tx)
    await session.flush()
    if action is not None:
        action.record("transaction", tx.id, None, tx.version)
    return tx


async def update_transaction(session, principal, tx_id, patch, *, action=None) -> Transaction:
    tx = await session.get(Transaction, tx_id)
    if tx is None or tx.user_id != principal.user_id:
        raise NotFound()
    if patch.version != tx.version:
        raise VersionConflict(details={"current_version": tx.version})
    before = {k: _jsonable(getattr(tx, k)) for k in _SNAP}
    for k, v in patch.model_dump(exclude_unset=True, exclude={"version"}).items():
        if k == "account_none":
            if v:
                tx.account_id = None
            continue
        setattr(tx, k, v)
    await session.flush()
    if action is not None:
        action.record("transaction", tx.id, before, tx.version)
    return tx


async def delete_transaction(session, principal, tx_id, *, version=None, action=None):
    tx = await session.get(Transaction, tx_id)
    if tx is None or tx.user_id != principal.user_id:
        raise NotFound()
    if version is not None and version != tx.version:
        raise VersionConflict()
    before = {k: _jsonable(getattr(tx, k)) for k in _SNAP}
    tx.deleted_at = utcnow()
    await session.flush()
    if action is not None:
        action.record("transaction", tx.id, before, tx.version)
    return tx


async def to_out(session, tx: Transaction) -> TransactionOut:
    await session.refresh(tx)
    acc = await session.get(Account, tx.account_id) if tx.account_id else None
    cacc = await session.get(Account, tx.counter_account_id) if tx.counter_account_id else None
    cat = await session.get(Category, tx.category_id) if tx.category_id else None
    mer = await session.get(Merchant, tx.merchant_id) if tx.merchant_id else None
    return TransactionOut(
        id=tx.id, kind=tx.kind, status=tx.status, occurred_at=tx.occurred_at,
        occurred_on=tx.occurred_on, amount=tx.amount, currency=tx.currency,
        account=AccountRef(id=acc.id, name=acc.name, icon=acc.icon) if acc else None,
        account_amount=tx.account_amount, account_asset=tx.account_asset,
        is_estimated=tx.is_estimated,
        counter_account=AccountRef(id=cacc.id, name=cacc.name) if cacc else None,
        counter_amount=tx.counter_amount, counter_asset=tx.counter_asset,
        category=CategoryRef(id=cat.id, name=cat.name, kind=cat.kind, icon=cat.icon,
                             is_business=cat.is_business, is_new=cat.is_new) if cat else None,
        is_business=bool(cat and cat.is_business),
        merchant=MerchantRef(id=mer.id, name=mer.name) if mer else None,
        counterparty=tx.counterparty, description=tx.description,
        amount_usd=tx.amount_usd, amount_vnd=tx.amount_vnd, source=tx.source,
        raw_text=tx.raw_text, created_action_id=tx.created_action_id, version=tx.version,
        created_at=tx.created_at or utcnow(), updated_at=tx.updated_at or utcnow(),
        title=cat.name if cat else "",
    )


# ----------------------------------------------------------------------------- transfers


async def _counter_tx(session, principal, kind, data, counter_amount, counter_cur, raw_text,
                      ai_request_id, action):
    on = data.occurred_on or local_today(TZ)
    tx = Transaction(
        user_id=principal.user_id, kind=kind, status="posted",
        occurred_at=occurred_at_for(on, TZ), occurred_on=on, amount=data.amount,
        currency=data.currency, account_id=data.from_account_id,
        counter_account_id=data.to_account_id, counter_amount=counter_amount,
        counter_asset=counter_cur, description=data.description, source=principal.source,
        raw_text=raw_text, ai_request_id=ai_request_id,
        created_action_id=action.id if action else None,
    )
    session.add(tx)
    await session.flush()
    if action is not None:
        action.record("transaction", tx.id, None, tx.version)
    return tx


async def create_transfer(session, principal, data, *, raw_text=None, ai_request_id=None,
                          action=None):
    return await _counter_tx(session, principal, "transfer", data, data.amount, data.currency,
                             raw_text, ai_request_id, action)


async def create_exchange(session, principal, data, *, raw_text=None, ai_request_id=None,
                          action=None):
    return await _counter_tx(session, principal, "exchange", data, data.counter_amount,
                             data.counter_currency, raw_text, ai_request_id, action)


async def adjust_balance(session, principal, data, *, raw_text=None, action=None):
    row = await session.scalar(select(AccountAsset).where(
        AccountAsset.account_id == data.account_id, AccountAsset.asset == data.asset))
    if row is None:
        row = AccountAsset(user_id=principal.user_id, account_id=data.account_id,
                           asset=data.asset)
        session.add(row)
    prev = row.opening_balance
    row.opening_balance = data.actual_balance
    row.opening_at = utcnow()
    await session.flush()
    if action is not None:
        action.record("account_asset", row.id, {"opening_balance": _jsonable(prev)}, None)
    return AdjustmentResult(kind="opening_set", account_id=data.account_id, asset=data.asset,
                            previous_balance=prev, balance=data.actual_balance, delta=None,
                            action_id=action.id if action else None)


async def account_out(session, user_id, account_id) -> AccountOut:
    a = await session.get(Account, account_id)
    assets = (await session.scalars(select(AccountAsset).where(
        AccountAsset.account_id == account_id))).all()
    return AccountOut(
        id=a.id, name=a.name, kind=a.kind, currency=a.currency, is_multi_asset=a.is_multi_asset,
        aliases=a.aliases or [], default_for_currencies=a.default_for_currencies or [],
        icon=a.icon, sort_order=a.sort_order, is_archived=a.is_archived, version=a.version,
        assets=[AssetBalanceOut(asset=x.asset, opening_balance=x.opening_balance,
                                opening_at=x.opening_at, balance=x.opening_balance)
                for x in assets],
    )


# ----------------------------------------------------------------------------- query / bulk


def search_filter_to_filter(sf, ctx) -> TransactionFilter:
    return TransactionFilter(
        kinds=sf.kinds, category_ids=sf.category_ids or None, account_ids=sf.account_ids or None,
        currency=sf.currency, min_amount=sf.min_amount, max_amount=sf.max_amount,
        amount_currency=sf.amount_currency, q=sf.text, is_business=sf.is_business,
        sort=sf.sort, limit=sf.limit,
    )


async def _select(session, user_id, f: TransactionFilter) -> list[Transaction]:
    q = select(Transaction).where(Transaction.user_id == user_id,
                                  Transaction.deleted_at.is_(None))
    if f.kinds:
        q = q.where(Transaction.kind.in_(f.kinds))
    if f.category_ids:
        q = q.where(Transaction.category_id.in_(f.category_ids))
    if f.min_amount is not None:
        q = q.where(Transaction.amount >= f.min_amount)
    if f.q:
        q = q.where(func.coalesce(Transaction.description, "").ilike(f"%{f.q}%"))
    order = Transaction.amount.desc() if f.sort == "amount_desc" else Transaction.id.desc()
    return list((await session.scalars(q.order_by(order).limit(f.limit))).all())


async def list_transactions(session, user_id, f: TransactionFilter) -> TransactionPage:
    rows = await _select(session, user_id, f)
    items = [await to_out(session, t) for t in rows]
    exp = sum((t.amount for t in rows if t.kind == "expense" and t.currency == "VND"), Decimal(0))
    return TransactionPage(items=items, total=len(items), limit=f.limit, offset=0,
                           totals=TransactionTotals(expense_vnd=exp, income_vnd=Decimal(0),
                                                    expense_usd=Decimal(0),
                                                    income_usd=Decimal(0), count=len(items)))


async def resolve_target(session, user_id, target, ctx) -> list[Transaction]:
    f = search_filter_to_filter(target.search, ctx)
    return await _select(session, user_id, f)


async def create_preview(session, principal, intent, matches, *, patch=None,
                         summary=None) -> PreviewToken:
    pt = PreviewToken(
        user_id=principal.user_id,
        kind="bulk_delete" if intent.type == "delete_transaction" else "bulk_update",
        intent=intent.model_dump(mode="json"),
        affected=[{"id": t.id, "version": t.version} for t in matches],
        summary="preview", action_source=principal.source,
        expires_at=utcnow() + timedelta(minutes=10),
    )
    session.add(pt)
    await session.flush()
    return pt


async def confirm_preview(session, principal, token):
    pt = await session.get(PreviewToken, token)
    if pt is None or pt.user_id != principal.user_id:
        raise NotFound()
    if pt.consumed_at is not None:
        return pt.result_action_id, len(pt.affected)
    if pt.expires_at < utcnow():
        raise AppError("Превью устарело", code="preview_expired", status=410)
    rows = []
    for a in pt.affected:
        tx = await session.get(Transaction, a["id"])
        if tx is None or tx.version != a["version"] or tx.deleted_at is not None:
            raise AppError("Превью устарело", code="preview_stale", status=409)
        rows.append(tx)
    ctx = await begin_action(session, principal, pt.kind)
    changes = (pt.intent or {}).get("changes") or {}
    for tx in rows:
        before = {k: _jsonable(getattr(tx, k)) for k in _SNAP}
        if pt.kind == "bulk_delete":
            tx.deleted_at = utcnow()
        else:
            if changes.get("amount"):
                tx.amount = Decimal(changes["amount"])
            if changes.get("category_ref"):
                cat = await session.scalar(select(Category).where(
                    Category.user_id == principal.user_id,
                    Category.name == changes["category_ref"]))
                tx.category_id = cat.id
        await session.flush()
        ctx.record("transaction", tx.id, before, tx.version)
    await finish_action(session, ctx, f"Массовое изменение ({len(rows)})")
    pt.consumed_at = utcnow()
    pt.result_action_id = ctx.id
    await session.flush()
    return ctx.id, len(rows)


async def cancel_preview(session, principal, token) -> None:
    pt = await session.get(PreviewToken, token)
    if pt is not None and pt.user_id == principal.user_id:
        pt.consumed_at = utcnow()
        await session.flush()


# ----------------------------------------------------------------------------- categories etc.


async def create_category(session, principal, data, *, is_new=False, action=None) -> Category:
    exists = await session.scalar(select(Category).where(
        Category.user_id == principal.user_id, Category.kind == data.kind,
        func.lower(Category.name) == data.name.lower(), Category.merged_into_id.is_(None)))
    if exists is not None:
        raise AppError("Категория уже есть", code="already_exists", status=409)
    cat = Category(user_id=principal.user_id, name=data.name, kind=data.kind,
                   is_business=data.is_business, icon=data.icon or "Tag",
                   aliases=[data.name.lower()], is_new=is_new)
    session.add(cat)
    await session.flush()
    if action is not None:
        action.record("category", cat.id, None, cat.version)
    return cat


async def learn(session, user_id, *, name, handle, category_id, account_id=None, alias=None,
                count_hit=True):
    m = None
    if handle:
        m = await session.scalar(select(Merchant).where(Merchant.user_id == user_id,
                                                        Merchant.handle == handle))
    if m is None and name:
        m = await session.scalar(select(Merchant).where(
            Merchant.user_id == user_id, func.lower(Merchant.name) == name.lower()))
    if m is None:
        m = Merchant(user_id=user_id, name=name or f"@{handle}", handle=handle,
                     aliases=[alias] if alias else [], hits=0)
        session.add(m)
    if category_id is not None:
        m.category_id = category_id
    if count_hit:
        m.hits = (m.hits or 0) + 1
    m.last_used_at = utcnow()
    await session.flush()
    return m


async def answer_stats_intent(session, user_id, intent, ctx, resolved) -> AnswerCard:
    total = await session.scalar(
        select(func.coalesce(func.sum(Transaction.amount_vnd), 0)).where(
            Transaction.user_id == user_id, Transaction.kind == "expense",
            Transaction.deleted_at.is_(None))
    )
    return AnswerCard(title="Потрачено", answer_type="stats",
                      headline=f"−{int(total):,}".replace(",", " ") + " ₫",
                      buttons=[], lines=[])


PATCHES: dict[str, dict[str, Any]] = {
    "app.services.ledger.actions": {
        "begin_action": begin_action, "finish_action": finish_action,
        "last_undoable_action": last_undoable_action, "undo_action": undo_action,
    },
    "app.services.ledger.transactions": {
        "create_transaction": create_transaction, "update_transaction": update_transaction,
        "delete_transaction": delete_transaction, "to_out": to_out,
    },
    "app.services.ledger.transfers": {
        "create_transfer": create_transfer, "create_exchange": create_exchange,
        "adjust_balance": adjust_balance,
    },
    "app.services.ledger.query": {
        "search_filter_to_filter": search_filter_to_filter,
        "list_transactions": list_transactions, "resolve_target": resolve_target,
    },
    "app.services.ledger.bulk": {
        "create_preview": create_preview, "confirm_preview": confirm_preview,
        "cancel_preview": cancel_preview,
    },
    "app.services.categories.service": {"create_category": create_category},
    "app.services.merchants.service": {"learn": learn},
    "app.services.accounts.balances": {"account_out": account_out},
    "app.services.stats.service": {"answer_stats_intent": answer_stats_intent},
}

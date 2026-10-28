"""Read side of the ledger: listing, filtering, target resolution."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, String, and_, case, exists, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import month_start, next_month_start
from app.models import Category, Debt, Merchant, Transaction
from app.models.enums import TxKind, TxStatus
from app.schemas.common import PeriodPreset, PeriodSpec
from app.schemas.intents import ParseContext, SearchFilter, TargetSpec
from app.schemas.transactions import TransactionFilter, TransactionPage, TransactionTotals
from app.services.ledger.common import like_contains
from app.services.ledger.summaries import to_out_many


def resolve_period(p: PeriodSpec | None, today: date) -> tuple[date | None, date | None]:
    """Inclusive (date_from, date_to) of a period preset; weeks start on Monday."""
    if p is None:
        return None, None
    pr = p.preset
    if pr == PeriodPreset.custom:
        return p.date_from, p.date_to
    if pr == PeriodPreset.today:
        return today, today
    if pr == PeriodPreset.yesterday:
        d = today - timedelta(days=1)
        return d, d
    if pr == PeriodPreset.this_week:
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if pr == PeriodPreset.last_week:
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=6)
    if pr == PeriodPreset.this_month:
        return month_start(today), next_month_start(today) - timedelta(days=1)
    if pr == PeriodPreset.last_month:
        end = month_start(today) - timedelta(days=1)
        return month_start(end), end
    if pr == PeriodPreset.this_year:
        return date(today.year, 1, 1), date(today.year, 12, 31)
    if pr == PeriodPreset.last_year:
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if pr == PeriodPreset.last_7_days:
        return today - timedelta(days=6), today
    if pr == PeriodPreset.last_30_days:
        return today - timedelta(days=29), today
    return None, None  # all_time


# Same expression as the GIN trigram index ix_transactions_search_trgm (so ILIKE can use it).
SEARCH_EXPR = literal_column(
    "(coalesce(transactions.description,'') || ' ' || coalesce(transactions.counterparty,'')"
    " || ' ' || coalesce(transactions.raw_text,''))"
)


_like = like_contains


def _apply_filter(stmt: Select[Any], user_id: int, f: TransactionFilter) -> Select[Any]:
    T = Transaction
    stmt = stmt.where(T.user_id == user_id)
    if not f.include_deleted:
        stmt = stmt.where(T.deleted_at.is_(None))
    if f.date_from:
        stmt = stmt.where(T.occurred_on >= f.date_from)
    if f.date_to:
        stmt = stmt.where(T.occurred_on <= f.date_to)
    if f.kinds:
        stmt = stmt.where(T.kind.in_(f.kinds))
    if f.category_ids:
        stmt = stmt.where(T.category_id.in_(f.category_ids))
    if f.account_ids:
        stmt = stmt.where(
            or_(T.account_id.in_(f.account_ids), T.counter_account_id.in_(f.account_ids))
        )
    if f.is_business is not None:
        stmt = stmt.where(T.is_business.is_(f.is_business))
    if f.status:
        stmt = stmt.where(T.status == f.status)
    if f.merchant_id is not None:
        stmt = stmt.where(T.merchant_id == f.merchant_id)
    amount_col: Any = T.amount
    ac = (f.amount_currency or "").upper()
    if ac == "USD":
        amount_col = T.amount_usd
    elif ac == "VND":
        amount_col = T.amount_vnd
    elif ac:
        stmt = stmt.where(T.currency == ac)
    if f.currency:
        stmt = stmt.where(or_(T.currency == f.currency, T.counter_asset == f.currency))
    if f.min_amount is not None:
        stmt = stmt.where(amount_col >= f.min_amount)
    if f.max_amount is not None:
        stmt = stmt.where(amount_col <= f.max_amount)
    if f.q:
        like = _like(f.q)
        stmt = stmt.where(
            or_(
                SEARCH_EXPR.ilike(like),
                exists().where(
                    and_(
                        Merchant.id == T.merchant_id,
                        or_(
                            func.lower(Merchant.name).like(like),
                            func.lower(func.array_to_string(Merchant.aliases, " ")).like(like),
                        ),
                    )
                ),
                exists().where(
                    and_(Category.id == T.category_id, func.lower(Category.name).like(like))
                ),
                # a debt operation by the person's CURRENT name / alias / @handle (after a rename
                # the recorded counterparty is the old name; DEBTS.md §15)
                and_(
                    T.debt_op.is_not(None),
                    exists().where(
                        Debt.account_id.in_([T.account_id, T.counter_account_id]),
                        or_(
                            func.lower(Debt.name).like(like),
                            func.lower(func.array_to_string(Debt.aliases, " ")).like(like),
                            func.lower(func.cast(Debt.handle, String)).like(like),
                        ),
                    ),
                ),
            )
        )
    return stmt


def _order(stmt: Select[Any], f: TransactionFilter) -> Select[Any]:
    T = Transaction
    ac = (f.amount_currency or "").upper()
    value: Any = T.amount_vnd
    if ac == "USD":
        value = T.amount_usd
    elif ac and ac != "VND":
        value = T.amount
    if f.sort == "date_asc":
        return stmt.order_by(T.occurred_on.asc(), T.occurred_at.asc(), T.id.asc())
    if f.sort == "amount_desc":
        return stmt.order_by(value.desc().nulls_last(), T.id.desc())
    if f.sort == "amount_asc":
        return stmt.order_by(value.asc().nulls_last(), T.id.asc())
    return stmt.order_by(T.occurred_on.desc(), T.occurred_at.desc(), T.id.desc())


async def totals(session: AsyncSession, user_id: int, f: TransactionFilter) -> TransactionTotals:
    """P&L totals of the filtered set: expense = Σexpense − Σrefund, income = Σincome.
    Transfers / exchanges / adjustments never count (ARCH §4.2)."""
    T = Transaction

    def s(col: Any, kinds: tuple[str, ...], sign: int = 1) -> Any:
        return func.coalesce(
            func.sum(case((T.kind.in_(kinds), sign * func.coalesce(col, 0)), else_=0)), 0
        )

    stmt = _apply_filter(
        select(
            func.count(),
            s(T.amount_vnd, (TxKind.expense,)) + s(T.amount_vnd, (TxKind.refund,), -1),
            s(T.amount_vnd, (TxKind.income,)),
            s(T.amount_usd, (TxKind.expense,)) + s(T.amount_usd, (TxKind.refund,), -1),
            s(T.amount_usd, (TxKind.income,)),
        ).select_from(T),
        user_id,
        f,
    )
    count, ev, iv, eu, iu = (await session.execute(stmt)).one()
    return TransactionTotals(
        expense_vnd=Decimal(ev), income_vnd=Decimal(iv), expense_usd=Decimal(eu),
        income_usd=Decimal(iu), count=int(count),
    )


async def list_transactions(
    session: AsyncSession, user_id: int, f: TransactionFilter
) -> TransactionPage:
    """Filtered page + totals. ``q`` searches description/counterparty/raw_text/merchant/category
    (ILIKE, trigram-indexed). min/max_amount compare in ``amount_currency`` (USD → amount_usd,
    VND → amount_vnd, otherwise original amount with currency filter)."""
    stmt = _order(_apply_filter(select(Transaction), user_id, f), f).limit(f.limit).offset(
        f.offset
    )
    rows = list(await session.scalars(stmt))
    tot = await totals(session, user_id, f)
    return TransactionPage(
        items=await to_out_many(session, rows), total=tot.count, limit=f.limit,
        offset=f.offset, totals=tot,
    )


async def iter_transactions(
    session: AsyncSession, user_id: int, f: TransactionFilter, batch: int = 1000
) -> AsyncIterator[list[Transaction]]:
    """All rows of a filter (ignores limit/offset), in batches — for export."""
    offset = 0
    while True:
        stmt = _order(_apply_filter(select(Transaction), user_id, f), f).limit(batch).offset(
            offset
        )
        rows = list(await session.scalars(stmt))
        if not rows:
            return
        yield rows
        if len(rows) < batch:
            return
        offset += batch


async def count_needs_account(session: AsyncSession, user_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).where(
                Transaction.user_id == user_id,
                Transaction.status == TxStatus.needs_account,
                Transaction.deleted_at.is_(None),
            )
        )
        or 0
    )


def search_filter_to_filter(sf: SearchFilter, ctx: ParseContext) -> TransactionFilter:
    """Convert an intent SearchFilter (refs, period) into a TransactionFilter (ids, dates).
    Refs must already be resolved into *_ids by the validator; an unresolved merchant_ref /
    counterparty is used as a text query."""
    d_from, d_to = resolve_period(sf.period, ctx.today)
    merchant_id: int | None = None
    if sf.merchant_ref:  # canonical merchant name (or alias) → merchant_id via the context
        ref = sf.merchant_ref.strip().lower()
        for m in ctx.merchants or []:
            names = {m.name.lower(), *(a.lower() for a in (m.aliases or []))}
            if ref in names:
                merchant_id = m.id
                break
    text_ref = None if merchant_id is not None else sf.merchant_ref
    q_parts = [x for x in (sf.text, sf.counterparty, text_ref) if x]
    return TransactionFilter(
        date_from=d_from,
        date_to=d_to,
        kinds=list(sf.kinds) if sf.kinds else None,
        category_ids=list(sf.category_ids) or None,
        account_ids=list(sf.account_ids) or None,
        currency=sf.currency,
        min_amount=sf.min_amount,
        max_amount=sf.max_amount,
        amount_currency=sf.amount_currency,
        q=" ".join(q_parts)[:200] if q_parts else None,
        is_business=sf.is_business,
        status=TxStatus.needs_account if sf.needs_account else None,
        merchant_id=merchant_id,
        sort=sf.sort,
        limit=sf.limit,
    )


_KIND_FOR_TARGET = {
    "last": None,
    "last_expense": (TxKind.expense,),
    "last_income": (TxKind.income,),
}


async def resolve_target(
    session: AsyncSession, user_id: int, target: TargetSpec, ctx: ParseContext
) -> list[Transaction]:
    """last / last_expense / last_income → from ctx.recent_actions (then newest by created_at);
    context → ctx.edit_transaction_id; id → that row; search → list_transactions.
    Returns [] if nothing matches (caller shows not_found). Deleted rows are never returned."""
    T = Transaction
    if target.kind == "id" or target.kind == "context":
        tid = target.transaction_id if target.kind == "id" else ctx.edit_transaction_id
        if tid is None:
            return []
        tx = await session.scalar(
            select(T).where(T.id == tid, T.user_id == user_id, T.deleted_at.is_(None))
        )
        return [tx] if tx else []
    if target.kind == "search":
        assert target.search is not None
        f = search_filter_to_filter(target.search, ctx)
        rows = await session.scalars(_order(_apply_filter(select(T), user_id, f), f).limit(
            f.limit))
        return list(rows)

    kinds = _KIND_FOR_TARGET.get(target.kind)
    seen: list[int] = []
    for brief in ctx.recent_actions:
        for tid in brief.transaction_ids:
            if tid not in seen:
                seen.append(tid)
    if seen:
        live = {
            t.id: t
            for t in await session.scalars(
                select(T).where(T.id.in_(seen), T.user_id == user_id, T.deleted_at.is_(None))
            )
        }
        ordered = [live[i] for i in seen if i in live and (not kinds or live[i].kind in kinds)]
        if len(ordered) >= target.position:
            return [ordered[target.position - 1]]
    stmt = select(T).where(T.user_id == user_id, T.deleted_at.is_(None))
    if kinds:
        stmt = stmt.where(T.kind.in_(kinds))
    stmt = stmt.order_by(T.created_at.desc(), T.id.desc()).offset(target.position - 1).limit(1)
    tx = await session.scalar(stmt)
    return [tx] if tx else []


async def last_estimated(
    session: AsyncSession,
    user_id: int,
    *,
    merchant_id: int | None = None,
    account_id: int | None = None,
    within_days: int = 31,
) -> Transaction | None:
    """Newest live ``is_estimated`` charge (optionally of a merchant / account): the target of
    «васт списали 21.3м» (UpdateTransaction with changes.account_amount, ARCH §4.8)."""
    T = Transaction
    stmt = select(T).where(
        T.user_id == user_id,
        T.deleted_at.is_(None),
        T.is_estimated.is_(True),
        T.created_at >= func.now() - timedelta(days=within_days),
    )
    if merchant_id is not None:
        stmt = stmt.where(T.merchant_id == merchant_id)
    if account_id is not None:
        stmt = stmt.where(T.account_id == account_id)
    return await session.scalar(stmt.order_by(T.created_at.desc(), T.id.desc()).limit(1))

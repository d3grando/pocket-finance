"""SQL building blocks for statistics (docs/ARCH.md §4 «Семантика денег»).

* P&L kinds only: expense, income, refund. transfer/exchange/adjustment never count.
* Expense = Σ expense − Σ refund (refund reduces the expense of its category). Income = Σ income.
* Statuses posted + needs_account (the latter is an expense without postings), not deleted.
* Amount column: ``amount_vnd`` (₫ summaries) or ``amount_usd`` (analytics). A row in the target
  currency itself without the converted column still counts (amount as is); other rows without
  a rate are skipped and reported as ``missing``.
* Business vs personal: by category (``categories.is_business``), the copy on the transaction
  is used for rows without a category.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models import Category, Merchant, Transaction
from app.models.enums import PNL_KINDS, TxStatus

Cur = Literal["VND", "USD"]
T = Transaction
ZERO = Decimal(0)
PNL = tuple(k.value for k in PNL_KINDS)
STATUSES = (TxStatus.posted.value, TxStatus.needs_account.value)


def q_money(v: Decimal | None, cur: str) -> Decimal:
    """Round a sum for output: ₫ — whole, $ — cents (banker's rounding)."""
    v = v if v is not None else ZERO
    step = Decimal(1) if cur == "VND" else Decimal("0.01")
    return v.quantize(step, rounding=ROUND_HALF_EVEN)


def ratio(cur: Decimal, prev: Decimal) -> Decimal | None:
    """(cur − prev) / |prev| to 3 decimals; None when prev is 0."""
    if prev == 0:
        return None
    return ((cur - prev) / abs(prev)).quantize(Decimal("0.001"), rounding=ROUND_HALF_EVEN)


def share(part: Decimal, total: Decimal) -> Decimal:
    if total == 0:
        return ZERO
    return (part / total).quantize(Decimal("0.001"), rounding=ROUND_HALF_EVEN)


def amount_expr(cur: Cur) -> ColumnElement:
    col = T.amount_vnd if cur == "VND" else T.amount_usd
    return func.coalesce(col, case((T.currency == cur, T.amount)))


def expense_expr(cur: Cur) -> ColumnElement:
    a = amount_expr(cur)
    return case((T.kind == "expense", a), (T.kind == "refund", -a))


def income_expr(cur: Cur) -> ColumnElement:
    return case((T.kind == "income", amount_expr(cur)))


@dataclass
class Scope:
    """Which operations: user + inclusive local dates + optional filters."""

    user_id: int
    date_from: date | None = None
    date_to: date | None = None
    category_ids: list[int] | None = None  # expanded with merged sources
    account_ids: list[int] | None = None
    is_business: bool | None = None
    kinds: tuple[str, ...] = PNL


def business_expr() -> ColumnElement:
    return func.coalesce(Category.is_business, T.is_business)


def where(scope: Scope) -> list[ColumnElement]:
    w: list[ColumnElement] = [
        T.user_id == scope.user_id,
        T.deleted_at.is_(None),
        T.status.in_(STATUSES),
        T.kind.in_(scope.kinds),
    ]
    if scope.date_from is not None:
        w.append(T.occurred_on >= scope.date_from)
    if scope.date_to is not None:
        w.append(T.occurred_on <= scope.date_to)
    if scope.category_ids is not None:
        w.append(T.category_id.in_(scope.category_ids))
    if scope.account_ids is not None:
        w.append(T.account_id.in_(scope.account_ids))
    if scope.is_business is not None:
        w.append(business_expr() == scope.is_business)
    return w


def _from(stmt, scope: Scope):  # type: ignore[no-untyped-def]
    stmt = stmt.select_from(T).outerjoin(Category, Category.id == T.category_id)
    return stmt.where(*where(scope))


@dataclass
class Totals:
    expense: Decimal = ZERO
    income: Decimal = ZERO
    count: int = 0  # operations in scope
    missing: int = 0  # operations without a rate in this currency

    @property
    def net(self) -> Decimal:
        return self.income - self.expense


async def totals(session: AsyncSession, scope: Scope, cur: Cur) -> Totals:
    a = amount_expr(cur)
    stmt = _from(
        select(
            func.coalesce(func.sum(expense_expr(cur)), 0),
            func.coalesce(func.sum(income_expr(cur)), 0),
            func.count(),
            func.count().filter(a.is_(None)),
        ),
        scope,
    )
    e, i, n, m = (await session.execute(stmt)).one()
    return Totals(Decimal(e), Decimal(i), int(n), int(m))


@dataclass
class CatRow:
    category_id: int | None
    amount: Decimal
    count: int


async def per_category(
    session: AsyncSession, scope: Scope, cur: Cur, kind: Literal["expense", "income"]
) -> list[CatRow]:
    s = Scope(**{**scope.__dict__})
    s.kinds = ("expense", "refund") if kind == "expense" else ("income",)
    val = expense_expr(cur) if kind == "expense" else income_expr(cur)
    stmt = _from(select(T.category_id, func.coalesce(func.sum(val), 0), func.count()), s).group_by(
        T.category_id
    )
    return [CatRow(cid, Decimal(v), int(n)) for cid, v, n in (await session.execute(stmt)).all()]


async def per_day(
    session: AsyncSession, scope: Scope, cur: Cur
) -> dict[date, tuple[Decimal, Decimal]]:
    stmt = _from(
        select(
            T.occurred_on,
            func.coalesce(func.sum(expense_expr(cur)), 0),
            func.coalesce(func.sum(income_expr(cur)), 0),
        ),
        scope,
    ).group_by(T.occurred_on)
    return {d: (Decimal(e), Decimal(i)) for d, e, i in (await session.execute(stmt)).all()}


async def per_month(
    session: AsyncSession, scope: Scope, cur: Cur
) -> dict[date, tuple[Decimal, Decimal]]:
    m = cast(func.date_trunc("month", T.occurred_on), Date)
    stmt = _from(
        select(
            m.label("m"),
            func.coalesce(func.sum(expense_expr(cur)), 0),
            func.coalesce(func.sum(income_expr(cur)), 0),
        ),
        scope,
    ).group_by(m)
    return {d: (Decimal(e), Decimal(i)) for d, e, i in (await session.execute(stmt)).all()}


@dataclass
class TopRow:
    tx: Transaction
    amount: Decimal
    category_name: str | None
    merchant_name: str | None


async def top_expenses(session: AsyncSession, scope: Scope, cur: Cur, limit: int) -> list[TopRow]:
    s = Scope(**{**scope.__dict__})
    s.kinds = ("expense",)
    a = amount_expr(cur)
    stmt = (
        _from(select(T, a.label("amt"), Category.name, Merchant.name), s)
        .outerjoin(Merchant, Merchant.id == T.merchant_id)
        .where(a.is_not(None))
        .order_by(a.desc(), T.occurred_on.desc(), T.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [TopRow(tx, Decimal(amt), cn, mn) for tx, amt, cn, mn in rows]


async def earliest_date(session: AsyncSession, user_id: int) -> date | None:
    return await session.scalar(
        select(func.min(T.occurred_on)).where(
            T.user_id == user_id, T.deleted_at.is_(None), T.kind.in_(PNL)
        )
    )


async def needs_account_count(session: AsyncSession, user_id: int) -> int:
    n = await session.scalar(
        select(func.count()).where(
            T.user_id == user_id, T.deleted_at.is_(None), T.status == TxStatus.needs_account.value
        )
    )
    return int(n or 0)


_KIND_HEADS = {
    "income": "Доход",
    "refund": "Возврат",
    "transfer": "Перевод",
    "exchange": "Обмен",
    "adjustment": "Корректировка",
}


def tx_title(
    category: str | None, merchant: str | None, tx: Transaction, *, person: str | None = None
) -> str:
    """«Серверы/инфра · vast.ai» — category + merchant/recipient/description.

    A debt operation is never «Расход»/«Переводы людям» (docs/DEBTS.md §0 п.1): «Дал в долг:
    Коля», «Возврат долга: Коля · за ремонт», «Списан долг: Коля» (``person`` = the current
    name of the person, else the recorded counterparty)."""
    if tx.debt_op is not None:
        from app.services.ledger.summaries import debt_label

        head = f"{debt_label(tx)}: {person or tx.counterparty or '?'}"
        tail = (tx.description or "").strip() or None
    else:
        tail = merchant or tx.counterparty or (tx.description or "").strip() or None
        head = category or _KIND_HEADS.get(str(tx.kind), "Расход")
    if tail and len(tail) > 60:
        tail = tail[:59] + "…"
    return f"{head} · {tail}" if tail else head

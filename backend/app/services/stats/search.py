"""Transaction search with filters + the global quick search (command palette).

``search_transactions`` implements the TransactionFilter semantics of ``GET /transactions``
(period, kinds, category, account, currency, amount from/to with amount_currency, text,
business/personal, status, merchant, sort) and can be reused by ledger.query.
Text that looks like an amount («>100$», «150к», «до 50 usdt») becomes an amount filter —
the same syntax as the AI line, parsed without the LLM (DESIGN.md §9.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import String, and_, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.money import format_money
from app.models import Account, Category, Debt, Merchant, Transaction
from app.models.enums import AccountKind
from app.schemas.search import SearchHit
from app.schemas.transactions import TransactionFilter, TransactionTotals
from app.services.ledger.summaries import debt_account_id
from app.services.stats import queries as Q
from app.services.stats.periods import day_label

T = Transaction

_CUR_WORDS: list[tuple[str, str]] = [
    (r"\$|usd|долл\w*|бакс\w*", "USD"),
    (r"usdt|юсдт", "USDT"),
    (r"₫|vnd|донг\w*|đ", "VND"),
    (r"₽|rub|руб\w*", "RUB"),
    (r"тон\w*|ton", "TON"),
]
_AMOUNT_RE = re.compile(
    r"^\s*(?P<op>>=|<=|>|<|от|до|больше|меньше|дороже|дешевле)?\s*"
    r"(?P<pre>\$)?\s*(?P<num>\d+(?:[.,]\d+)?)\s*(?P<mult>к|k|тыс|м|m|млн|kk)?\s*"
    r"(?P<cur>[^\d\s]*)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AmountQuery:
    min_amount: Decimal | None
    max_amount: Decimal | None
    currency: str  # amount_currency for the comparison


def parse_amount_query(text: str, default_currency: str = "VND") -> AmountQuery | None:
    """«>100$» → min 100 USD; «150к» → exactly 150 000 ₫; «до 50 usdt» → max 50 USDT.
    A bare number < 1000 in ₫ means thousands («кофе 85»)."""
    m = _AMOUNT_RE.match(text or "")
    if not m:
        return None
    try:
        num = Decimal(m.group("num").replace(",", "."))
    except InvalidOperation:
        return None
    mult = (m.group("mult") or "").lower()
    cur_word = (m.group("cur") or "").lower()
    cur = "USD" if m.group("pre") else None
    if cur_word:
        for pat, code in _CUR_WORDS:
            if re.fullmatch(pat, cur_word):
                cur = code
                break
        else:
            return None  # not an amount («кофе» etc.)
    cur = cur or default_currency
    if mult in ("к", "k", "тыс"):
        num *= 1000
    elif mult in ("м", "m", "млн", "kk"):
        num *= 1_000_000
    elif cur == "VND" and num < 1000:
        num *= 1000
    op = (m.group("op") or "").lower()
    if op in (">", ">=", "от", "больше", "дороже"):
        return AmountQuery(num, None, cur)
    if op in ("<", "<=", "до", "меньше", "дешевле"):
        return AmountQuery(None, num, cur)
    return AmountQuery(num, num, cur)


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _amount_col(amount_currency: str | None, currency: str | None) -> ColumnElement:
    ac = (amount_currency or "").upper()
    if ac == "USD":
        return Q.amount_expr("USD")
    if ac == "VND":
        return Q.amount_expr("VND")
    return T.amount


def text_condition(q: str) -> ColumnElement:
    """Description / recipient / raw text / merchant (name, aliases, handle) / category name /
    the CURRENT name, aliases or @handle of a debt person (a debt operation keeps the counterparty
    text of recording time; after a rename «Николай» still finds the old «Коля» operations)."""
    pat = _like(q.strip())
    low = q.strip().lower()
    hay = func.concat_ws(" ", T.description, T.counterparty, T.raw_text)
    person = exists().where(
        Debt.account_id.in_([T.account_id, T.counter_account_id]),
        or_(
            Debt.name.ilike(pat),
            func.array_to_string(Debt.aliases, " ").ilike(pat),
            cast(Debt.handle, String).ilike(pat),
        ),
    )
    return or_(
        hay.ilike(pat),
        and_(T.debt_op.is_not(None), person),
        func.word_similarity(low, func.lower(hay)) > 0.6,
        Merchant.name.ilike(pat),
        cast(Merchant.handle, String).ilike(pat),
        func.array_to_string(Merchant.aliases, " ").ilike(pat),
        Category.name.ilike(pat),
    )


def filter_conditions(user_id: int, f: TransactionFilter) -> list[ColumnElement]:
    w: list[ColumnElement] = [T.user_id == user_id]
    if not f.include_deleted:
        w.append(T.deleted_at.is_(None))
    if f.date_from is not None:
        w.append(T.occurred_on >= f.date_from)
    if f.date_to is not None:
        w.append(T.occurred_on <= f.date_to)
    if f.kinds:
        w.append(T.kind.in_([str(k.value if hasattr(k, "value") else k) for k in f.kinds]))
    if f.category_ids:
        w.append(T.category_id.in_(f.category_ids))
    if f.account_ids:
        w.append(or_(T.account_id.in_(f.account_ids), T.counter_account_id.in_(f.account_ids)))
    if f.currency:
        w.append(T.currency == f.currency.upper())
    if f.status is not None:
        w.append(T.status == str(getattr(f.status, "value", f.status)))
    if f.merchant_id is not None:
        w.append(T.merchant_id == f.merchant_id)
    if f.is_business is not None:
        w.append(Q.business_expr() == f.is_business)
    min_a, max_a, a_cur = f.min_amount, f.max_amount, f.amount_currency
    text = (f.q or "").strip()
    if text:
        aq = parse_amount_query(text)
        if aq is not None and min_a is None and max_a is None:
            min_a, max_a, a_cur = aq.min_amount, aq.max_amount, aq.currency
            if a_cur not in ("USD", "VND") and not f.currency:
                w.append(T.currency == a_cur)
        else:
            w.append(text_condition(text))
    col = _amount_col(a_cur, f.currency)
    if min_a is not None:
        w.append(col >= min_a)
    if max_a is not None:
        w.append(col <= max_a)
    return w


def _order(f: TransactionFilter) -> list[ColumnElement]:
    col = _amount_col(f.amount_currency or "USD", f.currency)
    return {
        "date_asc": [T.occurred_on.asc(), T.occurred_at.asc(), T.id.asc()],
        "amount_desc": [col.desc().nulls_last(), T.occurred_on.desc(), T.id.desc()],
        "amount_asc": [col.asc().nulls_last(), T.occurred_on.desc(), T.id.desc()],
    }.get(f.sort, [T.occurred_on.desc(), T.occurred_at.desc(), T.id.desc()])


def _base(stmt):  # type: ignore[no-untyped-def]
    return (
        stmt.select_from(T)
        .outerjoin(Category, Category.id == T.category_id)
        .outerjoin(Merchant, Merchant.id == T.merchant_id)
    )


async def search_transactions(
    session: AsyncSession, user_id: int, f: TransactionFilter
) -> tuple[list[Transaction], int, TransactionTotals]:
    """Filtered page of transactions + total count + P&L totals (₫ and $) of the whole set."""
    w = filter_conditions(user_id, f)
    rows = (
        await session.scalars(
            _base(select(T)).where(*w).order_by(*_order(f)).limit(f.limit).offset(f.offset)
        )
    ).all()
    pnl = and_(T.kind.in_(Q.PNL), T.status.in_(Q.STATUSES), T.deleted_at.is_(None))
    agg = (
        await session.execute(
            _base(
                select(
                    func.count(),
                    func.coalesce(func.sum(Q.expense_expr("VND")).filter(pnl), 0),
                    func.coalesce(func.sum(Q.income_expr("VND")).filter(pnl), 0),
                    func.coalesce(func.sum(Q.expense_expr("USD")).filter(pnl), 0),
                    func.coalesce(func.sum(Q.income_expr("USD")).filter(pnl), 0),
                )
            ).where(*w)
        )
    ).one()
    total, ev, iv, eu, iu = agg
    totals = TransactionTotals(
        expense_vnd=Q.q_money(Decimal(ev), "VND"),
        income_vnd=Q.q_money(Decimal(iv), "VND"),
        expense_usd=Q.q_money(Decimal(eu), "USD"),
        income_usd=Q.q_money(Decimal(iu), "USD"),
        count=int(total),
    )
    return list(rows), int(total), totals


async def quick_search(
    session: AsyncSession, user_id: int, q: str, limit: int = 20, f: TransactionFilter | None = None
) -> list[SearchHit]:
    """Command palette: categories, accounts, merchants, then matching operations."""
    text = q.strip()
    hits: list[SearchHit] = []
    if text and parse_amount_query(text) is None:
        pat = _like(text)
        cats = (
            await session.scalars(
                select(Category)
                .where(
                    Category.user_id == user_id,
                    Category.merged_into_id.is_(None),
                    or_(
                        Category.name.ilike(pat),
                        func.array_to_string(Category.aliases, " ").ilike(pat),
                    ),
                )
                .order_by(Category.is_archived, Category.sort_order, Category.name)
                .limit(5)
            )
        ).all()
        for c in cats:
            kind = "доход" if c.kind == "income" else "расход"
            hits.append(
                SearchHit(
                    type="category",
                    id=c.id,
                    title=c.name,
                    subtitle=f"Категория · {kind}" + (" · бизнес" if c.is_business else ""),
                    route=f"/operations?category_id={c.id}",
                )
            )
        accs = (
            await session.scalars(
                select(Account)
                .where(
                    Account.user_id == user_id,
                    Account.deleted_at.is_(None),
                    Account.kind != AccountKind.debt.value,  # DEBTS.md §2
                    or_(
                        Account.name.ilike(pat),
                        func.array_to_string(Account.aliases, " ").ilike(pat),
                    ),
                )
                .order_by(Account.is_archived, Account.sort_order, Account.name)
                .limit(5)
            )
        ).all()
        for a in accs:
            hits.append(
                SearchHit(
                    type="account",
                    id=a.id,
                    title=a.name,
                    subtitle="Счёт" + (f" · {a.currency}" if a.currency else " · мультивалютный"),
                    route=f"/operations?account_id={a.id}",
                )
            )
        mers = (
            await session.scalars(
                select(Merchant)
                .where(
                    Merchant.user_id == user_id,
                    or_(
                        Merchant.name.ilike(pat),
                        cast(Merchant.handle, String).ilike(pat),
                        func.array_to_string(Merchant.aliases, " ").ilike(pat),
                    ),
                )
                .order_by(Merchant.hits.desc(), Merchant.name)
                .limit(5)
            )
        ).all()
        for mer in mers:
            hits.append(
                SearchHit(
                    type="merchant",
                    id=mer.id,
                    title=mer.name,
                    subtitle="Продавец" + (f" · @{mer.handle}" if mer.handle else ""),
                    route=f"/operations?merchant_id={mer.id}",
                )
            )
    tf = (f or TransactionFilter()).model_copy(
        update={"q": text or None, "limit": limit, "offset": 0}
    )
    rows, _, _ = await search_transactions(session, user_id, tf)
    if rows:
        cats_by_id = {
            c.id: c.name
            for c in (
                await session.scalars(
                    select(Category).where(
                        Category.id.in_({r.category_id for r in rows if r.category_id})
                    )
                )
            ).all()
        }
        mer_by_id = {
            m.id: m.name
            for m in (
                await session.scalars(
                    select(Merchant).where(
                        Merchant.id.in_({r.merchant_id for r in rows if r.merchant_id})
                    )
                )
            ).all()
        }
        debt_accs = {i for r in rows if (i := debt_account_id(r)) is not None}
        person_by_acc = {
            d.account_id: d.name
            for d in (
                await session.scalars(select(Debt).where(Debt.account_id.in_(debt_accs)))
            ).all()
        } if debt_accs else {}
        for r in rows:
            if r.kind == "expense":
                amt, sign = -r.amount, "auto"
            elif r.kind in ("income", "refund"):
                amt, sign = r.amount, "always"
            else:  # transfer / exchange / adjustment: not income or expense
                amt, sign = r.amount, "never"
            hits.append(
                SearchHit(
                    type="transaction",
                    id=r.id,
                    title=Q.tx_title(
                        cats_by_id.get(r.category_id or -1), mer_by_id.get(r.merchant_id or -1), r,
                        person=person_by_acc.get(debt_account_id(r) or -1),
                    ),
                    subtitle=(
                        f"{format_money(amt, r.currency, sign=sign)} · {day_label(r.occurred_on)}"
                    ),
                    route=f"/operations?tx={r.id}",
                )
            )
    return hits[: max(limit, 1)]

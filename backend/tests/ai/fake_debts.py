"""ORM-backed stand-in for ``app.services.debts.service`` in assistant tests.

Same model as the real one (docs/DEBTS.md §1, §3.1): a person = ``debts`` row + service account
kind='debt'; operations are ordinary transactions with ``debt_op``; the balance is derived from
live transactions, so undo / «нет, 300к» (soft delete / amount edit by the fake ledger) change it
like the real service does.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import any_, select

from app.core.errors import AppError, NotFound
from app.core.money import FIAT
from app.core.person_names import clean, name_key, same_person
from app.core.timeutil import local_today, occurred_at_for, utcnow
from app.models import Account, Category, Debt, Transaction
from app.schemas.debts import (
    DebtBalanceOut,
    DebtOpResult,
    DebtOut,
    DebtsOut,
    DebtTotalsOut,
)
from app.schemas.intents import CtxDebt, CtxDebtOpen
from app.schemas.transactions import TxDebtRef
from tests.ai import fakes

TZ = fakes.TZ
NEEDS_ACCOUNT = {"USD", "USDT", "TON", "BTC", "ETH", "USDC"}
RATE_VND = {"VND": Decimal(1), "USD": Decimal(26000), "USDT": Decimal(26000)}


async def _all(session, user_id: int) -> list[Debt]:
    return list((await session.scalars(
        select(Debt).where(Debt.user_id == user_id).order_by(Debt.id))).all())


async def find_debt(session, user_id: int, ref: str) -> Debt | list[Debt] | None:
    rows = await _all(session, user_id)
    r = (ref or "").strip()
    if r.startswith("@"):
        hits = [d for d in rows if (d.handle or "").lower() == r[1:].lower()]
        if hits:
            return hits[0] if len(hits) == 1 else hits
    low = clean(r)
    exact = [d for d in rows if clean(d.name) == low or low in {clean(a) for a in d.aliases}]
    if len(exact) == 1:
        return exact[0]
    key = name_key(r)
    hits = [d for d in rows if key and same_person(d.name_key, key)]
    if len(hits) == 1:
        return hits[0]
    return hits or None


async def get_or_create_debt(session, principal, name: str, *, handle: str | None = None,
                             written_form: str | None = None, action=None) -> Debt:
    hit = await find_debt(session, principal.user_id, f"@{handle}" if handle else name)
    if hit is None and handle:
        hit = await find_debt(session, principal.user_id, name)
    if isinstance(hit, list):
        raise AppError("Несколько людей с таким именем", code="ambiguous_target", status=422)
    form = clean(written_form or "")
    if hit is not None:
        if form and form != clean(hit.name) and form not in hit.aliases:
            hit.aliases = [*hit.aliases, form]
        hit.is_archived = False
        await session.flush()
        return hit
    acc = Account(user_id=principal.user_id, name=f"Долг: {name}", kind="debt",
                  is_multi_asset=True, icon="HandCoins", sort_order=900)
    session.add(acc)
    await session.flush()
    debt = Debt(user_id=principal.user_id, account_id=acc.id, name=name, name_key=name_key(name),
                aliases=[form] if form and form != clean(name) else [],
                handle=(handle or None))
    session.add(debt)
    await session.flush()
    await session.refresh(debt)
    return debt


def _delta(tx: Transaction) -> Decimal:
    if tx.debt_op in ("lend", "repay_out"):
        return Decimal(tx.amount)
    if tx.debt_op == "forgive" and tx.kind == "income":
        return Decimal(tx.amount)
    return -Decimal(tx.amount)


async def balances_of(session, debt: Debt) -> dict[str, Any]:
    rows = (await session.scalars(select(Transaction).where(
        Transaction.user_id == debt.user_id, Transaction.deleted_at.is_(None),
        Transaction.debt_op.isnot(None),
        (Transaction.account_id == debt.account_id)
        | (Transaction.counter_account_id == debt.account_id))
        .order_by(Transaction.id))).all()
    out: dict[str, Decimal] = {}
    for t in rows:
        out[t.currency] = out.get(t.currency, Decimal(0)) + _delta(t)
    # same order as the real service (VND first, then fiat, then the rest)
    return dict(sorted(((k, v) for k, v in out.items() if v != 0),
                       key=lambda kv: _cur_order(kv[0])))


def _cur_order(cur: str) -> tuple[int, str]:
    return (0 if cur == "VND" else 1 if cur in FIAT else 2, cur)


async def debt_out(session, debt: Debt, *, sign: int = 0) -> DebtOut:
    bals = await balances_of(session, debt)
    items = [DebtBalanceOut(currency=c, amount=v, direction="owed_to_me" if v > 0 else "i_owe",
                            approx_vnd=v * RATE_VND[c] if c in RATE_VND else None)
             for c, v in sorted(bals.items()) if sign == 0 or (v > 0) == (sign > 0)]
    signs = {b.direction for b in items}
    status = ("settled" if not bals else "mixed" if len({v > 0 for v in bals.values()}) > 1
              else next(iter(signs), "settled"))
    return DebtOut(id=debt.id, name=debt.name, handle=debt.handle, aliases=list(debt.aliases),
                   account_id=debt.account_id, is_archived=debt.is_archived,
                   status=status, balances=items,  # type: ignore[arg-type]
                   version=debt.version or 1, created_at=debt.created_at or utcnow())


async def record_op(session, principal, data, *, raw_text=None, ai_request_id=None,
                    action=None) -> DebtOpResult:
    debt = await session.get(Debt, data.debt_id)
    if debt is None or debt.user_id != principal.user_id:
        raise NotFound("Долг не найден")
    cur = data.currency.upper()
    bal = Decimal((await balances_of(session, debt)).get(cur, 0))
    op, amount, kind, cat_id = data.op, data.amount, "transfer", None
    if op in ("repay_in", "repay_out", "forgive") and amount is None:
        if bal == 0 or (op == "repay_in" and bal < 0) or (op == "repay_out" and bal > 0):
            raise AppError("Долга нет", code="validation_error", status=422)
        amount = abs(bal)
    if op == "forgive":
        if bal == 0 or amount > abs(bal):
            raise AppError("Нельзя списать", code="validation_error", status=422)
        kind = "expense" if bal > 0 else "income"
        cat_id = await session.scalar(select(Category.id).where(
            Category.user_id == principal.user_id, Category.kind == kind,
            Category.name == ("Переводы людям" if kind == "expense" else "Прочее")))
    real, status = None, "posted"
    if op != "forgive":
        real = data.account_id
        if real is None and not data.account_none:
            real = await session.scalar(select(Account.id).where(
                Account.user_id == principal.user_id, Account.kind != "debt",
                cur == any_(Account.default_for_currencies)))
            if real is None and cur in NEEDS_ACCOUNT:
                status = "needs_account"
    if op in ("lend", "repay_out"):
        src, dst = real, debt.account_id
    elif op in ("repay_in", "borrow"):
        src, dst = debt.account_id, real
    else:
        src, dst = debt.account_id, None
    on = data.occurred_on or local_today(TZ)
    tx = Transaction(
        user_id=principal.user_id, kind=kind, status=status, debt_op=op,
        occurred_at=data.occurred_at or occurred_at_for(on, TZ), occurred_on=on,
        amount=amount, currency=cur, account_id=src, counter_account_id=dst,
        counter_amount=amount if dst else None, counter_asset=cur if dst else None,
        category_id=cat_id, counterparty=debt.name, description=data.description,
        amount_vnd=amount * RATE_VND[cur] if cur in RATE_VND else None,
        amount_usd=amount if cur in ("USD", "USDT") else None,
        source=principal.source, raw_text=raw_text, ai_request_id=ai_request_id,
        created_action_id=action.id if action else None,
    )
    session.add(tx)
    await session.flush()
    if action is not None:
        action.record("transaction", tx.id, None, tx.version)
    after = bal + _delta(tx)
    excess = None
    if op == "repay_in" and after < 0:
        excess = -after
    elif op == "repay_out" and after > 0:
        excess = after
    out = await fakes.to_out(session, tx)
    out = out.model_copy(update={"debt": TxDebtRef(id=debt.id, name=debt.name, op=op)})
    return DebtOpResult(transaction=out, action_id=action.id if action else uuid.uuid4(),
                        debt=await debt_out(session, debt), balance_after=after, excess=excess,
                        needs_account=status == "needs_account")


async def list_debts(session, user_id: int, *, include_settled: bool = False) -> DebtsOut:
    owed, iowe, settled = [], [], []
    tot = {"owed_vnd": Decimal(0), "iowe_vnd": Decimal(0)}
    missing = False
    for d in await _all(session, user_id):
        if d.is_archived:
            continue
        bals = await balances_of(session, d)
        if any(v > 0 for v in bals.values()):
            owed.append(await debt_out(session, d, sign=1))
        if any(v < 0 for v in bals.values()):
            iowe.append(await debt_out(session, d, sign=-1))
        if not bals and include_settled:
            settled.append(await debt_out(session, d))
        for c, v in bals.items():
            if c not in RATE_VND:
                missing = True
                continue
            tot["owed_vnd" if v > 0 else "iowe_vnd"] += abs(v) * RATE_VND[c]
    totals = DebtTotalsOut(
        owed_to_me_vnd=tot["owed_vnd"] if owed else None,
        owed_to_me_usd=(tot["owed_vnd"] / 26000).quantize(Decimal("0.01")) if owed else None,
        i_owe_vnd=tot["iowe_vnd"] if iowe else None,
        i_owe_usd=(tot["iowe_vnd"] / 26000).quantize(Decimal("0.01")) if iowe else None,
        owed_to_me_count=len(owed), i_owe_count=len(iowe), missing_rates=missing)
    return DebtsOut(owed_to_me=owed, i_owe=iowe, settled=settled, totals=totals)


async def context_debts(session, user_id: int) -> list[CtxDebt]:
    """Read-only aggregate: names, aliases, open currencies + direction (no amounts)."""
    rows = list((await session.scalars(
        select(Debt).where(Debt.user_id == user_id, Debt.is_archived.is_(False))
        .order_by(Debt.id)
    )).all())
    if not rows:
        return []
    by_acc = {d.account_id: d for d in rows}
    T = Transaction
    txs = (await session.execute(
        select(T.debt_op, T.kind, T.amount, T.currency, T.account_id, T.counter_account_id)
        .where(T.user_id == user_id, T.deleted_at.is_(None), T.debt_op.isnot(None))
    )).all()
    bal: dict[tuple[int, str], Decimal] = {}
    for op, kind, amount, cur, acc_id, counter_id in txs:
        # docs/DEBTS.md §3.1: the debt account is the target of lend/repay_out (+) and the
        # source of repay_in/borrow/forgive (−; an income forgive is +)
        if op in ("lend", "repay_out"):
            acc, delta = counter_id, Decimal(amount)
        elif op == "forgive" and kind == "income":
            acc, delta = acc_id, Decimal(amount)
        else:
            acc, delta = acc_id, -Decimal(amount)
        if acc in by_acc:
            key = (acc, (cur or "").upper())
            bal[key] = bal.get(key, Decimal(0)) + delta
    out: list[CtxDebt] = []
    for d in rows:
        opens = [CtxDebtOpen(currency=c, direction="owed_to_me" if v > 0 else "i_owe")
                 for (a, c), v in sorted(bal.items()) if a == d.account_id and v != 0]
        out.append(CtxDebt(id=d.id, name=d.name, name_key=d.name_key or name_key(d.name),
                           aliases=list(d.aliases or []), handle=d.handle, open=opens))
    return out


PATCHES: dict[str, dict[str, Any]] = {
    "app.services.debts.service": {
        "find_debt": find_debt, "get_or_create_debt": get_or_create_debt,
        "balances_of": balances_of, "record_op": record_op, "list_debts": list_debts,
        "context_debts": context_debts,
    },
}

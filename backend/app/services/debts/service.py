"""Debt service. Contract: docs/DEBTS.md §4–§7.

All functions follow ARCH §7: async, ``session`` first, never commit, raise ``AppError``.
Debt balance = balance of the debt's service account (kind='debt', opening 0 per asset), sign:
> 0 the person owes me, < 0 I owe the person. Currencies are never mixed or converted
(``approx_*`` are display only, current rates).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, ValidationFailed, VersionConflict
from app.core.money import FIAT, format_money, normalize, quantize_asset
from app.core.person_names import clean, name_key, same_person, words
from app.core.principal import Principal
from app.core.timeutil import local_today
from app.models import Account, ActionLog, AuditLog, Category, Debt, Posting, Transaction
from app.models.enums import AccountKind, ActionKind, DebtOp, TxKind, TxStatus
from app.schemas.debts import (
    DebtBalanceOut,
    DebtDetailOut,
    DebtHistoryItem,
    DebtOpCreate,
    DebtOpResult,
    DebtOut,
    DebtPatch,
    DebtsOut,
    DebtTotalsOut,
)
from app.schemas.intents import CtxDebt, CtxDebtOpen
from app.services.accounts import service as accounts
from app.services.categories import service as categories
from app.services.debts import ops
from app.services.debts.totals import totals_of
from app.services.ledger.actions import ActionContext, begin_action
from app.services.ledger.audit import snapshot, write_audit
from app.services.ledger.common import (
    approx,
    check_amount,
    check_derived,
    check_limit,
    publish,
    resolve_when,
    strip_handle,
    user_tz,
    writer_lock,
)
from app.services.ledger.postings import rebuild_postings
from app.services.ledger.summaries import to_out, to_out_many

#: account name prefix of the service account («Долг: Коля»), icon of debt accounts
ACCOUNT_PREFIX = "Долг: "
ACCOUNT_ICON = "HandCoins"
NAME_MAX = 56
ALIASES_MAX = 50
#: People (debt counterparties) per user
MAX_PEOPLE = 1000
ZERO = Decimal(0)
_HANDLE_RE = re.compile(r"^[a-z0-9_]{1,64}$", re.I)


class AmbiguousPerson(ValidationFailed):
    code, message = "ambiguous_target", "Уточните, о ком речь"


def cur_label(cur: str) -> str:
    """«₫» / «$» / «USDT» — for messages («Коля тебе ничего не должен в ₫»)."""
    return FIAT[cur][0] if cur in FIAT else cur


def _display(name: str | None) -> str:
    return " ".join((name or "").split())[:NAME_MAX]


def _alias(form: str | None) -> str:
    return clean(form or "")[:64]


def _ambiguous(hits: list[Debt]) -> AmbiguousPerson:
    names = ", ".join(d.name for d in hits)
    return AmbiguousPerson(
        f"Какой именно: {names}?",
        details={"candidates": [{"id": d.id, "name": d.name} for d in hits]},
    )


# ------------------------------------------------------------------------------- persons


async def _user_debts(session: AsyncSession, user_id: int) -> list[Debt]:
    return list(
        await session.scalars(
            select(Debt).where(Debt.user_id == user_id).order_by(func.lower(Debt.name), Debt.id)
        )
    )


def _match(debts: list[Debt], ref: str) -> Debt | list[Debt] | None:
    raw = ref.strip()
    c = clean(raw)
    if not c:
        return None
    if raw.startswith("@") or _HANDLE_RE.fullmatch(c):
        hits = [d for d in debts if d.handle and d.handle.lower() == c]
        if hits:
            return hits[0]  # handles are unique per user
        if raw.startswith("@"):
            hits = [d for d in debts if clean(d.name) == c]  # «@vasya» stored as a name
            return hits[0] if len(hits) == 1 else (hits or None)
    hits = [d for d in debts if clean(d.name) == c or c in (d.aliases or [])]
    if len(hits) == 1:
        return hits[0]
    if hits:
        return hits
    key = name_key(raw)
    if not key:
        return None
    hits = [
        d for d in debts
        if same_person(key, d.name_key)
        or any(same_person(key, name_key(a)) for a in (d.aliases or []))
    ]
    if len(hits) == 1:
        return hits[0]
    return hits or None


async def find_debt(session: AsyncSession, user_id: int, ref: str) -> Debt | list[Debt] | None:
    """Resolve a written reference («Коле», «у Пети», «@vasya», «Коля Петров») to a person:
    handle → exact name/alias → ``person_names.same_person(name_key)``. One match → Debt; several →
    list (caller asks «Какой Коля?»); none → None. Archived persons match too."""
    if not (ref or "").strip():
        return None
    debts = await _user_debts(session, user_id)
    return _match(debts, ref) if debts else None


async def _free_account_name(
    session: AsyncSession, user_id: int, name: str, exclude_id: int | None = None
) -> str:
    """«Долг: Коля», or «Долг: Коля (2)» when an own account already has that name."""
    base = name[:64]
    cand, n = base, 2
    while True:
        stmt = select(Account.id).where(
            Account.user_id == user_id,
            func.lower(Account.name) == cand.lower(),
            Account.deleted_at.is_(None),
        )
        if exclude_id is not None:
            stmt = stmt.where(Account.id != exclude_id)
        if await session.scalar(stmt.limit(1)) is None:
            return cand
        cand = f"{base[:58]} ({n})"
        n += 1


async def _handle_taken(
    session: AsyncSession, user_id: int, handle: str, exclude_id: int | None = None
) -> bool:
    stmt = select(Debt.id).where(Debt.user_id == user_id, Debt.handle == handle)
    if exclude_id is not None:
        stmt = stmt.where(Debt.id != exclude_id)
    return await session.scalar(stmt.limit(1)) is not None


async def _audit(
    session: AsyncSession, principal: Principal, d: Debt, op: str, before: dict | None,
    action_id: object = None,
) -> None:
    await write_audit(
        session, user_id=d.user_id, entity_type="debt", entity_id=d.id, operation=op,
        before=before, after=snapshot(d) if op != "delete" else None, source=principal.source,
        action_id=action_id,  # type: ignore[arg-type]
    )


async def _learn(
    session: AsyncSession,
    principal: Principal,
    d: Debt,
    *,
    form: str | None = None,
    handle: str | None = None,
    action: ActionContext | None = None,
) -> None:
    """Remember a new written form («коле», «у коли») and a handle; un-archive on use."""
    before = snapshot(d)
    changed = False
    a = _alias(form)
    if a and a != clean(d.name) and a not in (d.aliases or []) and len(d.aliases) < ALIASES_MAX:
        d.aliases = [*(d.aliases or []), a]
        changed = True
    if handle and d.handle is None and not await _handle_taken(session, d.user_id, handle):
        d.handle = handle
        changed = True
    if d.is_archived:
        d.is_archived = False
        changed = True
    if changed:
        d.version += 1
        await session.flush()
        await _audit(session, principal, d, "update", before, action.id if action else None)


async def _create(
    session: AsyncSession,
    principal: Principal,
    name: str,
    handle: str | None,
    form: str | None,
    action: ActionContext | None,
) -> Debt:
    uid = principal.user_id
    await check_limit(
        session, select(func.count()).select_from(Debt).where(Debt.user_id == uid),
        MAX_PEOPLE, "людей в долгах",
    )
    acc_name = await _free_account_name(session, uid, ACCOUNT_PREFIX + name)
    a = _alias(form)
    aliases = [a] if a and a != clean(name) else []
    if handle and await _handle_taken(session, uid, handle):
        handle = None
    await session.flush()  # pending changes stay outside the savepoint
    try:
        async with session.begin_nested():
            acc = Account(
                user_id=uid, name=acc_name, kind=AccountKind.debt.value, currency=None,
                is_multi_asset=True, aliases=[], default_for_currencies=[], icon=ACCOUNT_ICON,
                sort_order=0, version=1,
            )
            session.add(acc)
            await session.flush()
            d = Debt(
                user_id=uid, account_id=acc.id, name=name, name_key=name_key(name),
                aliases=aliases, handle=handle, version=1,
            )
            session.add(d)
            await session.flush()
    except IntegrityError as e:  # a concurrent request created the same person
        again = await find_debt(session, uid, name)
        if isinstance(again, Debt):
            return again
        raise accounts.AlreadyExists(f"«{name}» уже есть") from e
    await write_audit(
        session, user_id=uid, entity_type="account", entity_id=acc.id, operation="create",
        before=None, after=snapshot(acc), source=principal.source,
        action_id=action.id if action else None,
    )
    await _audit(session, principal, d, "create", None, action.id if action else None)
    return d


async def get_or_create_debt(
    session: AsyncSession,
    principal: Principal,
    name: str,
    *,
    handle: str | None = None,
    written_form: str | None = None,
    action: ActionContext | None = None,
) -> Debt:
    """Existing person by ``find_debt`` (ambiguous → 422 ambiguous_target with the candidates),
    else create: service account (kind=debt, is_multi_asset, name ACCOUNT_PREFIX+name, icon
    ACCOUNT_ICON) + debts row (name_key, aliases ∋ written_form). ``written_form`` is learned as
    an alias. An archived person is un-archived on a new operation."""
    uid = principal.user_id
    h = strip_handle(handle)
    display = _display(name)
    if display.startswith("@") and h is None and _HANDLE_RE.fullmatch(display[1:]):
        h = display[1:]
    if not display and h:
        display = f"@{h}"
    if not words(display) and not h:
        raise ValidationFailed("Укажите имя человека", details={"field": "person_name"})
    found: Debt | list[Debt] | None = None
    if h:
        found = await find_debt(session, uid, f"@{h}")
    if found is None:
        found = await find_debt(session, uid, display)
    if isinstance(found, list):
        raise _ambiguous(found)
    if found is not None:
        await _learn(session, principal, found, form=written_form or name, handle=h,
                     action=action)
        return found
    return await _create(session, principal, display, h, written_form, action)


async def get_debt_row(
    session: AsyncSession, user_id: int, debt_id: int, *, for_update: bool = False
) -> Debt:
    """404 not_found «Долг не найден»."""
    stmt = select(Debt).where(Debt.id == debt_id, Debt.user_id == user_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    d = await session.scalar(stmt)
    if d is None:
        raise NotFound("Долг не найден", details={"debt_id": debt_id})
    return d


async def update_debt(
    session: AsyncSession, principal: Principal, debt_id: int, patch: DebtPatch
) -> DebtOut:
    """Rename (also renames the service account; name_key recomputed; the old name stays a known
    form; 409 already_exists), handle («» clears), aliases, note, archive (only when all balances
    are 0 → else 422)."""
    uid = principal.user_id
    await writer_lock(session, uid)
    d = await get_debt_row(session, uid, debt_id, for_update=True)
    if patch.version != d.version:
        raise VersionConflict(details={"current_version": d.version})
    fields = set(patch.model_fields_set) - {"version"}
    if not fields:
        raise ValidationFailed("Нет изменений")
    before = snapshot(d)
    if "name" in fields and patch.name is not None:
        new = _display(patch.name)
        if not words(new):
            raise ValidationFailed("Укажите имя человека", details={"field": "name"})
        if new != d.name:
            other = await session.scalar(
                select(Debt.id).where(
                    Debt.user_id == uid, func.lower(Debt.name) == new.lower(), Debt.id != d.id
                )
            )
            if other is not None:
                raise accounts.AlreadyExists(f"«{new}» уже есть", details={"id": other})
            old = _alias(d.name)
            d.name, d.name_key = new, name_key(new)
            if old and old != clean(new) and old not in (d.aliases or []):
                d.aliases = [*(d.aliases or []), old][-ALIASES_MAX:]
            acc = await session.get(Account, d.account_id)
            if acc is not None:
                acc.name = await _free_account_name(
                    session, uid, ACCOUNT_PREFIX + new, exclude_id=acc.id
                )
                acc.version += 1
    if "handle" in fields:
        h = strip_handle(patch.handle)
        if h and h.lower() != (d.handle or "").lower() and await _handle_taken(
            session, uid, h, exclude_id=d.id
        ):
            raise accounts.AlreadyExists(f"@{h} уже у другого человека")
        d.handle = h
    if "aliases" in fields and patch.aliases is not None:
        out: list[str] = []
        for a in patch.aliases:
            x = _alias(a)
            if x and x not in out:
                out.append(x)
        d.aliases = out[:ALIASES_MAX]
    if "note" in fields:
        d.note = patch.note or None
    if "is_archived" in fields and patch.is_archived is not None:
        if patch.is_archived and not d.is_archived:
            open_ = await balances_of(session, d)
            if open_:
                rest = " и ".join(format_money(v, c) for c, v in open_.items())
                raise ValidationFailed(
                    f"Долг не закрыт ({rest}) — архивировать можно только с нулевым остатком",
                    details={"field": "is_archived"},
                )
        d.is_archived = patch.is_archived
    d.version += 1
    try:
        await session.flush()
    except IntegrityError as e:  # concurrent rename / handle
        raise accounts.AlreadyExists("Такое имя или @ник уже есть") from e
    await _audit(session, principal, d, "update", before)
    await publish(session, uid, {"debts", "accounts", "transactions"}, source=principal.source)
    return await debt_out(session, d)


async def delete_debt(session: AsyncSession, principal: Principal, debt_id: int) -> None:
    """Hard delete only without any (even deleted) transactions on the account; else 422
    «Есть операции — можно только архивировать»."""
    uid = principal.user_id
    await writer_lock(session, uid)
    d = await get_debt_row(session, uid, debt_id, for_update=True)
    used = await session.scalar(
        select(Transaction.id)
        .where(
            or_(
                Transaction.account_id == d.account_id,
                Transaction.counter_account_id == d.account_id,
            )
        )
        .limit(1)
    )
    if used is not None:
        raise ValidationFailed(
            "Есть операции — можно только архивировать", details={"debt_id": d.id}
        )
    before = snapshot(d)
    acc = await session.get(Account, d.account_id)
    await session.delete(d)
    await session.flush()
    if acc is not None:
        acc_before = snapshot(acc)
        await session.delete(acc)  # account_assets go with it (no postings without operations)
        await session.flush()
        await write_audit(
            session, user_id=uid, entity_type="account", entity_id=acc_before["id"],
            operation="delete", before=acc_before, after=None, source=principal.source,
        )
    await write_audit(
        session, user_id=uid, entity_type="debt", entity_id=debt_id, operation="delete",
        before=before, after=None, source=principal.source,
    )
    await publish(session, uid, {"debts", "accounts"}, source=principal.source)


async def snooze(session: AsyncSession, principal: Principal, debt_id: int, days: int) -> DebtOut:
    """«Напомнить позже»: remind_snoozed_until = local today + days. Not undoable."""
    from datetime import timedelta

    uid = principal.user_id
    d = await get_debt_row(session, uid, debt_id, for_update=True)
    before = snapshot(d)
    d.remind_snoozed_until = local_today(await user_tz(session, uid)) + timedelta(days=days)
    d.version += 1
    await session.flush()
    await _audit(session, principal, d, "update", before)
    await publish(session, uid, {"debts"}, source=principal.source)
    return await debt_out(session, d)


# ------------------------------------------------------------------------------- operations


async def _person_for_op(
    session: AsyncSession, principal: Principal, data: DebtOpCreate,
    action: ActionContext | None,
) -> Debt:
    uid = principal.user_id
    h = strip_handle(data.handle)
    if data.debt_id is not None:
        d = await get_debt_row(session, uid, data.debt_id)
        await _learn(session, principal, d, handle=h, action=action)
        return d
    assert data.person_name is not None
    if data.op in (DebtOp.lend, DebtOp.borrow):
        return await get_or_create_debt(
            session, principal, data.person_name, handle=h, written_form=data.person_name,
            action=action,
        )
    # repay / forgive: only a known person (a new one cannot owe anything yet)
    found: Debt | list[Debt] | None = await find_debt(session, uid, f"@{h}") if h else None
    if found is None:
        found = await find_debt(session, uid, data.person_name)
    if isinstance(found, list):
        raise _ambiguous(found)
    if found is None:
        raise ValidationFailed(
            f"Не знаю долгов: {_display(data.person_name)}",
            details={"field": "person_name", "reason": "unknown_person"},
        )
    await _learn(session, principal, found, form=data.person_name, handle=h, action=action)
    return found


async def _forgive_category(session: AsyncSession, user_id: int, kind: str) -> Category:
    """forgive of what I am owed → expense «Переводы людям» (личное); of my own debt → income
    «Прочее» (docs/DEBTS.md §0 п.6, §4)."""
    names = ("Переводы людям", "Прочее") if kind == TxKind.expense else ("Прочее",)
    for n in names:
        m = await categories.resolve_category(session, user_id, n, kind)  # type: ignore[arg-type]
        if m.exact and m.category is not None and not m.category.is_archived:
            return m.category
    cats = await categories.list_categories(session, user_id, kind=kind)  # type: ignore[arg-type]
    if not cats:
        raise ValidationFailed("Нет категории для списания долга")
    return cats[0]


async def record_op(
    session: AsyncSession,
    principal: Principal,
    data: DebtOpCreate,
    *,
    raw_text: str | None = None,
    ai_request_id: int | None = None,
    action: ActionContext | None = None,
) -> DebtOpResult:
    """One debt operation = one transaction (docs/DEBTS.md §4):
    lend/repay_out → transfer real → debt account; repay_in/borrow → transfer debt → real;
    forgive → expense «Переводы людям» (balance > 0) / income «Прочее» (balance < 0) on the debt
    account. Sets ``transactions.debt_op``; action kind ``debt`` (undo/«нет, 250к» as usual).
    amount None → whole remaining balance in ``currency`` (0 → 422 «Долга нет»).
    Real account: explicit / default by currency / needs_account (debt side posted anyway).
    Overpay (repay above the balance) → one transaction, ``excess`` in the result."""
    from app.services.ledger.transactions import _body_account, _finish, _flush, apply_valuation

    uid = principal.user_id
    await writer_lock(session, uid)
    op = DebtOp(data.op)
    cur = data.currency.upper()
    check_amount(data.amount)
    check_amount(data.account_amount, "account_amount")
    if data.account_none and data.account_id is not None:
        raise ValidationFailed("Выберите счёт или «без счёта», не оба сразу")
    at, on = await resolve_when(session, uid, data.occurred_on, data.occurred_at)
    real: Account | None = None
    status = TxStatus.posted
    if op != DebtOp.forgive:  # the real account first: a bad one must not create a person
        if data.account_id is not None:
            real = await _body_account(session, uid, data.account_id)  # 422 for debt accounts
        elif not data.account_none:
            real = await accounts.default_account_for(session, uid, cur)
            if real is None:
                status = TxStatus.needs_account
        if real is None and data.account_amount is not None:
            raise ValidationFailed("Сумму по счёту можно указать только вместе со счётом",
                                   details={"field": "account_amount"})

    own = action is None
    ctx = action or await begin_action(
        session, principal, ActionKind.debt, input_text=raw_text, ai_request_id=ai_request_id
    )
    ctx.kind = ActionKind.debt
    debt = await _person_for_op(session, principal, data, ctx)
    debt_acc = await session.get(Account, debt.account_id)
    assert debt_acc is not None
    await ops.lock_debt(session, debt_acc, cur)
    balance = await ops.debt_balance(session, debt_acc.id, cur)

    amount = data.amount
    excess: Decimal | None = None
    kind = TxKind.transfer
    category: Category | None = None
    name = debt.name
    if op == DebtOp.repay_in:
        if balance <= 0:
            raise ValidationFailed(
                f"{name} тебе ничего не должен в {cur_label(cur)}",
                details={"reason": "no_debt", "debt_id": debt.id, "currency": cur},
            )
        if amount is None:
            amount = balance
        elif amount > balance:
            excess = amount - balance
    elif op == DebtOp.repay_out:
        if balance >= 0:
            raise ValidationFailed(
                f"Ты ничего не должен: {name} в {cur_label(cur)}",
                details={"reason": "no_debt", "debt_id": debt.id, "currency": cur},
            )
        if amount is None:
            amount = -balance
        elif amount > -balance:
            excess = amount + balance
    elif op == DebtOp.forgive:
        if balance == 0:
            raise ValidationFailed(
                f"Долга нет: {name} в {cur_label(cur)}",
                details={"reason": "no_debt", "debt_id": debt.id, "currency": cur},
            )
        direction = "owed_to_me" if balance > 0 else "i_owe"
        if data.direction is not None and data.direction != direction:
            msg = (
                f"{name} тебе ничего не должен в {cur_label(cur)} — это ты должен"
                if direction == "i_owe"
                else f"Ты ничего не должен: {name} в {cur_label(cur)} — это тебе должны"
            )
            raise ValidationFailed(msg, details={"reason": "direction", "debt_id": debt.id})
        rest = abs(balance)
        if amount is not None and amount > rest:
            raise ValidationFailed(
                f"Больше остатка: списать можно не больше {format_money(rest, cur)}",
                details={"field": "amount", "max": str(rest)},
            )
        # on ITS date (a back-dated write-off): not more than was owed then, and no later
        # point of the history may cross zero because of it (docs/DEBTS.md §4)
        side = 1 if balance > 0 else -1
        series = await ops.debt_series(session, debt_acc.id, cur)
        allowed = min(rest, ops.forgive_room(series, at, ops.NEW_ID, side,
                                             ops.past_zero(series, side)))
        if allowed <= 0:
            raise ValidationFailed(
                f"На эту дату долга не было: {name} в {cur_label(cur)}",
                details={"reason": "no_debt", "debt_id": debt.id, "currency": cur,
                         "field": "occurred_on"},
            )
        if amount is None:
            amount = allowed
        elif amount > allowed:
            raise ValidationFailed(
                f"Больше остатка на эту дату: списать можно не больше "
                f"{format_money(allowed, cur)}",
                details={"field": "amount", "max": normalize(allowed)},
            )
        kind = TxKind.expense if balance > 0 else TxKind.income
        category = await _forgive_category(session, uid, kind)
    assert amount is not None

    tx = Transaction(
        user_id=uid,
        kind=kind,
        status=status,
        occurred_at=at,
        occurred_on=on,
        amount=amount,
        currency=cur,
        category_id=category.id if category else None,
        is_business=category.is_business if category else False,
        counterparty=name[:128],
        description=(data.description or None),
        debt_op=op.value,
        source=principal.source,
        raw_text=raw_text,
        ai_request_id=ai_request_id,
        created_action_id=ctx.id,
        version=1,
    )
    if op == DebtOp.forgive:
        ops.apply_forgive_side(tx, debt_acc)
    else:
        await ops.apply_real_side(session, tx, debt_acc, real, actual=data.account_amount)
    await apply_valuation(session, tx)
    check_derived(tx)
    session.add(tx)
    await _flush(session, tx)
    touched = await rebuild_postings(session, tx)
    await _finish(session, principal, ctx, own, tx, None, "create", touched | {debt_acc.id},
                  ops.DEBT_ENTITIES)
    balance_after = await ops.debt_balance(session, debt_acc.id, cur)
    return DebtOpResult(
        transaction=await to_out(session, tx),
        action_id=ctx.id,
        debt=await debt_out(session, debt),
        balance_after=balance_after,
        excess=excess,
        needs_account=tx.status == TxStatus.needs_account,
    )


async def balances_of(session: AsyncSession, debt: Debt) -> dict[str, Decimal]:
    """{currency: signed Decimal} of non-zero balances (one aggregate over postings)."""
    rows = (
        await session.execute(
            select(Posting.asset, func.sum(Posting.amount))
            .where(Posting.account_id == debt.account_id)
            .group_by(Posting.asset)
        )
    ).all()
    out = {str(a): Decimal(v) for a, v in rows if v is not None and Decimal(v) != 0}
    return dict(sorted(out.items(), key=lambda kv: _cur_order(kv[0])))


def _cur_order(cur: str) -> tuple[int, str]:
    return (0 if cur == "VND" else 1 if cur in FIAT else 2, cur)


# ------------------------------------------------------------------------------- reading


@dataclass
class _Entry:
    tx_id: int
    occurred_at: datetime
    occurred_on: date
    asset: str
    amount: Decimal


@dataclass
class _State:
    """Derived state of one debt account: non-zero balances, age, last activity."""

    balances: dict[str, Decimal] = field(default_factory=dict)
    open_since: dict[str, date] = field(default_factory=dict)
    last_activity: date | None = None
    has_ops: bool = False


async def _entries(session: AsyncSession, account_ids: list[int]) -> dict[int, list[_Entry]]:
    """Postings of debt accounts in time order (deleted operations have none)."""
    out: dict[int, list[_Entry]] = {i: [] for i in account_ids}
    if not account_ids:
        return out
    rows = (
        await session.execute(
            select(
                Posting.account_id, Posting.transaction_id, Posting.occurred_at,
                Transaction.occurred_on, Posting.asset, Posting.amount,
            )
            .join(Transaction, Transaction.id == Posting.transaction_id)
            .where(Posting.account_id.in_(account_ids))
            .order_by(Posting.occurred_at, Posting.transaction_id, Posting.id)
        )
    ).all()
    for acc, tx_id, at, on, asset, amount in rows:
        out[acc].append(_Entry(tx_id, at, on, str(asset), Decimal(amount)))
    return out


def _state(entries: list[_Entry]) -> _State:
    """open_since per currency: the date the current same-sign non-zero balance started —
    partial repays keep it, reaching 0 (or flipping the sign) starts a new one."""
    st = _State(has_ops=bool(entries))
    bal: dict[str, Decimal] = {}
    for e in entries:
        prev = bal.get(e.asset, ZERO)
        new = prev + e.amount
        bal[e.asset] = new
        if new == 0:
            st.open_since.pop(e.asset, None)
        elif prev == 0 or (prev > 0) != (new > 0):
            st.open_since[e.asset] = e.occurred_on
        if st.last_activity is None or e.occurred_on > st.last_activity:
            st.last_activity = e.occurred_on
    st.balances = dict(
        sorted(((c, v) for c, v in bal.items() if v != 0), key=lambda kv: _cur_order(kv[0]))
    )
    return st


async def _states(session: AsyncSession, debts: list[Debt]) -> dict[int, _State]:
    ent = await _entries(session, [d.account_id for d in debts])
    return {acc: _state(es) for acc, es in ent.items()}


async def _today(session: AsyncSession, user_id: int) -> date:
    return local_today(await user_tz(session, user_id))


async def _balance_out(
    session: AsyncSession, cur: str, amount: Decimal, since: date | None, today: date
) -> DebtBalanceOut:
    usd = await approx(session, amount, cur, "USD", today)
    vnd = await approx(session, amount, cur, "VND", today)
    return DebtBalanceOut(
        currency=cur,
        amount=amount,
        direction="owed_to_me" if amount > 0 else "i_owe",
        approx_usd=quantize_asset(usd, "USD") if usd is not None else None,
        approx_vnd=quantize_asset(vnd, "VND") if vnd is not None else None,
        open_since=since,
    )


def _status(st: _State) -> str:
    pos = any(v > 0 for v in st.balances.values())
    neg = any(v < 0 for v in st.balances.values())
    return "mixed" if pos and neg else "owed_to_me" if pos else "i_owe" if neg else "settled"


async def _out(
    session: AsyncSession, d: Debt, st: _State, today: date, sign: int = 0
) -> DebtOut:
    """``sign`` > 0 / < 0 — only the balances of that list (DebtsOut), 0 — all of them."""
    bals = [
        await _balance_out(session, c, v, st.open_since.get(c), today)
        for c, v in st.balances.items()
        if sign == 0 or (v > 0) == (sign > 0)
    ]
    return DebtOut(
        id=d.id,
        name=d.name,
        handle=d.handle,
        aliases=list(d.aliases or []),
        note=d.note,
        account_id=d.account_id,
        is_archived=d.is_archived,
        status=_status(st),  # type: ignore[arg-type]
        balances=bals,
        last_activity_on=st.last_activity,
        remind_snoozed_until=d.remind_snoozed_until,
        version=d.version,
        created_at=d.created_at,
    )


async def debt_out(session: AsyncSession, debt: Debt) -> DebtOut:
    st = (await _states(session, [debt]))[debt.account_id]
    return await _out(session, debt, st, await _today(session, debt.user_id))


def _sum_vnd(out: DebtOut) -> Decimal:
    return sum((abs(b.approx_vnd or ZERO) for b in out.balances), ZERO)


async def list_debts(
    session: AsyncSession, user_id: int, *, include_settled: bool = False
) -> DebtsOut:
    """«Мне должны» / «Я должен» with per-currency balances, ≈$/≈₫, open_since, totals.
    A person with debts in both directions is in both lists (each with its own balances).
    ``settled``: zero-balance, non-archived persons with operations (not after undo of all)."""
    debts = await _user_debts(session, user_id)
    states = await _states(session, debts)
    today = await _today(session, user_id)
    owed: list[DebtOut] = []
    owe: list[DebtOut] = []
    settled: list[DebtOut] = []
    for d in debts:
        st = states[d.account_id]
        if not st.balances:
            if include_settled and st.has_ops and not d.is_archived:
                settled.append(await _out(session, d, st, today))
            continue
        for sign, lst in ((1, owed), (-1, owe)):
            if not any((v > 0) == (sign > 0) for v in st.balances.values()):
                continue
            lst.append(await _out(session, d, st, today, sign))
    owed.sort(key=lambda o: (-_sum_vnd(o), o.name.lower()))
    owe.sort(key=lambda o: (-_sum_vnd(o), o.name.lower()))
    totals = totals_of(owed, owe)
    return DebtsOut(owed_to_me=owed, i_owe=owe, settled=settled, totals=totals)


async def get_debt(
    session: AsyncSession, user_id: int, debt_id: int, *, limit: int = 50, offset: int = 0
) -> DebtDetailOut:
    """Person card: DebtOut + history (newest first) with delta / balance_after per currency."""
    d = await get_debt_row(session, user_id, debt_id)
    entries = (await _entries(session, [d.account_id]))[d.account_id]
    running: dict[str, Decimal] = {}
    items: list[tuple[_Entry, Decimal]] = []
    for e in entries:
        running[e.asset] = running.get(e.asset, ZERO) + e.amount
        items.append((e, running[e.asset]))
    items.reverse()  # newest first
    page = items[offset: offset + limit]
    txs = {
        t.id: t
        for t in await session.scalars(
            select(Transaction).where(Transaction.id.in_([e.tx_id for e, _ in page]))
        )
    } if page else {}
    outs = {o.id: o for o in await to_out_many(session, list(txs.values()))}
    history = [
        DebtHistoryItem(
            transaction=outs[e.tx_id],
            op=txs[e.tx_id].debt_op,  # type: ignore[arg-type]
            currency=e.asset,
            delta=e.amount,
            balance_after=after,
        )
        for e, after in page
        if e.tx_id in txs and txs[e.tx_id].debt_op is not None
    ]
    st = _state(entries)
    return DebtDetailOut(
        debt=await _out(session, d, st, await _today(session, user_id)),
        history=history,
        total=len(items),
        limit=limit,
        offset=offset,
        last_action_id=await _last_live_action(session, user_id, items[0][0].tx_id)
        if items else None,
    )


async def _last_live_action(session: AsyncSession, user_id: int, tx_id: int) -> uuid.UUID | None:
    """«Отменить последнюю» (DEBTS.md §15): the newest action in the audit history of the latest
    operation that is still in effect (not an undo itself, not undone, undoable) — the creation,
    or the newest edit when the operation was changed since. None live (e.g. it was undone and
    restored) → the newest action (the restore), like the web fallback did."""
    rows = (await session.execute(
        select(ActionLog.id, ActionLog.kind, ActionLog.undone_at, ActionLog.is_undoable)
        .join(AuditLog, AuditLog.action_id == ActionLog.id)
        .where(AuditLog.user_id == user_id, AuditLog.entity_type == "transaction",
               AuditLog.entity_id == str(tx_id))
        .order_by(AuditLog.id.desc())
    )).all()
    for aid, kind, undone_at, undoable in rows:
        if kind != ActionKind.undo and undone_at is None and undoable:
            return aid
    return rows[0][0] if rows else None


async def totals(session: AsyncSession, user_id: int) -> DebtTotalsOut:
    """«Мне должны ≈ / Я должен ≈» (display only, current rates)."""
    return (await list_debts(session, user_id)).totals


async def context_debts(session: AsyncSession, user_id: int) -> list[CtxDebt]:
    """For ParseContext.debts: every non-archived person (and an archived one with an open
    balance) with name/aliases/handle and the currencies + direction of non-zero balances.
    NO amounts (by design: the LLM never sees sums)."""
    debts = await _user_debts(session, user_id)
    states = await _states(session, debts)
    out: list[CtxDebt] = []
    for d in debts:
        st = states[d.account_id]
        if d.is_archived and not st.balances:
            continue
        if len(out) >= MAX_PEOPLE:  # same cap as for new people: the parser indexes them all
            break
        out.append(
            CtxDebt(
                id=d.id,
                name=d.name,
                name_key=d.name_key,
                aliases=list(d.aliases or [])[:ALIASES_MAX],
                handle=d.handle,
                open=[
                    CtxDebtOpen(currency=c, direction="owed_to_me" if v > 0 else "i_owe")
                    for c, v in st.balances.items()
                ],
            )
        )
    return out


async def open_since(session: AsyncSession, debt: Debt, currency: str) -> date | None:
    """Local date the current same-sign non-zero balance started (reminder age)."""
    st = (await _states(session, [debt]))[debt.account_id]
    return st.open_since.get(currency.upper())


async def debt_states(session: AsyncSession, debts: list[Debt]) -> dict[int, _State]:
    """Derived state per debt id (reminders)."""
    by_acc = await _states(session, debts)
    return {d.id: by_acc[d.account_id] for d in debts}


__all__ = [
    "ACCOUNT_ICON",
    "ACCOUNT_PREFIX",
    "AmbiguousPerson",
    "balances_of",
    "context_debts",
    "cur_label",
    "debt_out",
    "debt_states",
    "delete_debt",
    "find_debt",
    "get_debt",
    "get_debt_row",
    "get_or_create_debt",
    "list_debts",
    "open_since",
    "record_op",
    "snooze",
    "totals",
    "totals_of",
    "update_debt",
]

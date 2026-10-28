"""Ledger-level rules of debt operations (docs/DEBTS.md §3.1, §4).

A debt operation is an ordinary transaction marked with ``transactions.debt_op``:

* lend / repay_out — transfer real → debt account: ``account_*`` = the real side (may be NULL),
  ``counter_*`` = the debt side (``counter_amount = amount``, 1:1 in ``currency``);
* repay_in / borrow — transfer debt → real: ``account_*`` = the debt side (1:1),
  ``counter_*`` = the real side (may be NULL); ``account_rate`` = real / amount (like exchanges);
* forgive — expense «Переводы людям» / income «Прочее» on the debt account (1:1).

The real side in another fiat currency (debt in $, Карта in ₫) is converted ≈ by rate
(``is_estimated``) unless the actual amount is given. Called from ``ledger.transactions``
(``update_transaction`` / ``assign_account`` delegate here for rows with ``debt_op``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, ValidationFailed, VersionConflict
from app.core.money import format_money, is_fiat, normalize
from app.core.principal import Principal
from app.models import Account, AccountAsset, Debt, Posting, Transaction
from app.models.enums import DEBT_INFLOW, ActionKind, DebtOp, TxKind, TxStatus
from app.schemas.transactions import TransactionPatch
from app.services.accounts import service as accounts
from app.services.ledger.actions import ActionContext, begin_action
from app.services.ledger.audit import from_snapshot, snapshot
from app.services.ledger.common import (
    check_amount,
    check_derived,
    convert_to_asset,
    div,
    q_rate,
    resolve_when,
)
from app.services.ledger.postings import lock_assets, postings_for, rebuild_postings
from app.services.ledger.summaries import debt_account_id

ONE = Decimal(1)
ZERO = Decimal(0)
#: SSE entities of every debt mutation (+ «stats» is added by ``publish``)
DEBT_ENTITIES = {"debts"}


async def ensure_debt_asset(session: AsyncSession, debt_account: Account, asset: str
                            ) -> AccountAsset:
    """(debt account, asset) row with a known opening balance 0 and no opening moment: the
    debt balance is always known and counts every posting, back-dated ones too."""
    aa = await accounts.ensure_asset(session, debt_account, asset)
    if aa.opening_balance is None:
        aa.opening_balance = Decimal(0)
        aa.opening_at = None
        await session.flush()
    return aa


async def debt_balance(session: AsyncSession, debt_account_id_: int, asset: str) -> Decimal:
    """Signed debt balance in ``asset`` (opening 0 → Σ postings): > 0 owes me, < 0 I owe."""
    v = await session.scalar(
        select(func.coalesce(func.sum(Posting.amount), 0)).where(
            Posting.account_id == debt_account_id_, Posting.asset == asset.upper()
        )
    )
    return Decimal(v or 0)


#: One point of a debt account's history in one currency: (occurred_at, transaction id, signed
#: change of the balance). Ordered by (occurred_at, id) — the order the balance «happened» in.
Point = tuple[datetime, int, Decimal]
#: Tie-break id of an operation that is not written yet: after every row of the same moment.
NEW_ID = 2**63 - 1


async def debt_series(session: AsyncSession, debt_account_id_: int, asset: str) -> list[Point]:
    """All postings of (debt account, asset) in (occurred_at, transaction id) order."""
    rows = (
        await session.execute(
            select(Posting.occurred_at, Posting.transaction_id, Posting.amount)
            .where(Posting.account_id == debt_account_id_, Posting.asset == asset.upper())
            .order_by(Posting.occurred_at, Posting.transaction_id)
        )
    ).all()
    return [(at, int(i), Decimal(d)) for at, i, d in rows]


def past_zero(series: list[Point], side: int) -> set[int]:
    """Ids of the points where the running balance is already past zero on ``side`` (+1: the
    person owes me, −1: I owe): an overpay that happened on its own, not because of a forgive."""
    out: set[int] = set()
    run = ZERO
    for _, i, d in series:
        run += d
        if side * run < 0:
            out.add(i)
    return out


def forgive_room(series: list[Point], at: datetime, fid: int, side: int,
                 skip: set[int] | frozenset[int] = frozenset()) -> Decimal:
    """How much a write-off at position (``at``, ``fid``) may write off (unclamped, may be < 0):
    the debt on ``side`` just before it — on ITS date, not today (docs/DEBTS.md §4) — and no
    later point of the running balance may cross zero because of it (a later return the person
    made was paid against the debt that is left). Points in ``skip`` (already past zero before
    the change) do not limit it. ``series`` may contain the write-off itself: it is ignored."""
    key = (at, fid)
    run = ZERO
    room: Decimal | None = None
    for a, i, d in series:
        if i == fid:
            continue
        if (a, i) < key:
            run += d
            continue
        if room is None:
            room = side * run
        run += d
        if i not in skip:
            room = min(room, side * run)
    return side * run if room is None else room


async def lock_debt(session: AsyncSession, debt_account: Account, asset: str) -> None:
    """FOR UPDATE on (debt account, asset): two parallel repays / forgives of one person see
    each other's result (no double forgive)."""
    await ensure_debt_asset(session, debt_account, asset)
    await lock_assets(session, {(debt_account.id, asset.upper())}, "update")


def real_account_id(tx: Transaction) -> int | None:
    """The real (own) account of a debt transfer: source of lend/repay_out, target of
    repay_in/borrow."""
    return tx.counter_account_id if tx.debt_op in DEBT_INFLOW else tx.account_id


async def apply_real_side(
    session: AsyncSession,
    tx: Transaction,
    debt_account: Account,
    real: Account | None,
    *,
    actual: Decimal | None = None,
) -> None:
    """Set both sides of a debt transfer (orientation by ``tx.debt_op``). ``real`` None = the
    real side is unknown (needs_account) or «без счёта». ``actual`` = the actual amount on the
    real account (charge for outflows, credit for inflows) → not estimated."""
    cur = tx.currency
    inflow = tx.debt_op in DEBT_INFLOW
    amt: Decimal | None = None
    asset: str | None = None
    rate: Decimal | None = None
    estimated = False
    if real is not None:
        asset = cur if real.is_multi_asset else (real.currency or cur)
        if asset != cur and not is_fiat(cur):
            raise ValidationFailed(
                f"На счёте {real.name} нет {cur} — выберите другой счёт",
                details={"field": "account_id", "account_id": real.id, "asset": cur},
            )
        if actual is not None and asset == cur and actual != tx.amount:
            raise ValidationFailed(
                f"Счёт {real.name} в {asset}: сумма должна совпадать с суммой долга",
                details={"field": "account_amount"},
            )
        check_amount(actual, "account_amount")
        await accounts.ensure_asset(session, real, asset)
        if actual is not None:
            amt, rate = actual, q_rate(div(actual, tx.amount))
        elif asset == cur:
            amt, rate = tx.amount, ONE
        else:
            amt, rate = await convert_to_asset(
                session, tx.amount, cur, asset, tx.occurred_on, inflow=inflow
            )
            estimated = True
    elif actual is not None:
        raise ValidationFailed("Сумму по счёту можно указать только вместе со счётом",
                               details={"field": "account_amount"})
    await ensure_debt_asset(session, debt_account, cur)
    if inflow:
        tx.account_id = debt_account.id
        tx.account_amount = tx.amount
        tx.account_asset = cur
        tx.account_rate = rate if rate is not None else ONE
        tx.counter_account_id = real.id if real is not None else None
        tx.counter_amount = amt
        tx.counter_asset = asset
    else:
        tx.account_id = real.id if real is not None else None
        tx.account_amount = amt
        tx.account_asset = asset
        tx.account_rate = rate
        tx.counter_account_id = debt_account.id
        tx.counter_amount = tx.amount
        tx.counter_asset = cur
    tx.is_estimated = estimated


def apply_forgive_side(tx: Transaction, debt_account: Account) -> None:
    """forgive = expense/income on the debt account, 1:1 in the debt currency."""
    tx.account_id = debt_account.id
    tx.account_amount = tx.amount
    tx.account_asset = tx.currency
    tx.account_rate = ONE
    tx.is_estimated = False
    tx.counter_account_id = None
    tx.counter_amount = None
    tx.counter_asset = None


async def _debt_account(session: AsyncSession, tx: Transaction) -> Account:
    acc_id = debt_account_id(tx)
    acc = await session.get(Account, acc_id) if acc_id is not None else None
    if acc is None or not accounts.is_debt_account(acc):  # pragma: no cover - data invariant
        raise ValidationFailed("Счёт долга не найден", details={"transaction_id": tx.id})
    return acc


async def _real_account(session: AsyncSession, user_id: int, account_id: int) -> Account:
    from app.services.ledger.transactions import _body_account

    return await _body_account(session, user_id, account_id)  # 422 for debt accounts


_TRANSFER_FIELDS = {
    "amount", "currency", "occurred_on", "occurred_at", "description",
    "account_id", "account_none", "account_amount",
}
_FORGIVE_FIELDS = {"amount", "occurred_on", "occurred_at", "description", "category_id"}


async def update_debt_transaction(
    session: AsyncSession,
    principal: Principal,
    tx: Transaction,
    patch: TransactionPatch,
    fields: set[str],
    *,
    action: ActionContext | None = None,
) -> Transaction:
    """PATCH of a debt operation (docs/DEBTS.md §4 «Правка»). ``tx`` is locked, not deleted,
    its version checked by the caller (``ledger.update_transaction``).

    transfer: amount, currency, date, description, the REAL side (account_id / account_none /
    account_amount; needs_account → posted), both sides recomputed (conversion for the real one).
    forgive: amount (≤ the balance before the operation), date, description, category_id of the
    same kind; account / kind / currency changes → 422. The person cannot be changed."""
    from app.services.ledger.transactions import (
        _category_for,
        _finish,
        _flush,
        apply_valuation,
        bump,
    )

    uid = principal.user_id
    forgive = tx.debt_op == DebtOp.forgive
    allowed = _FORGIVE_FIELDS if forgive else _TRANSFER_FIELDS
    extra = fields - allowed
    if extra:
        what = (
            "сумму, дату, описание и категорию" if forgive
            else "сумму, валюту, дату, описание и счёт"
        )
        raise ValidationFailed(
            f"У {'списания долга' if forgive else 'долговой операции'} можно изменить только "
            f"{what}. Сменить человека — отмените и запишите заново",
            details={"fields": sorted(extra)},
        )
    check_amount(patch.amount)
    check_amount(patch.account_amount, "account_amount")
    debt_acc = await _debt_account(session, tx)
    before = snapshot(tx)
    own = action is None
    ctx = action or await begin_action(session, principal, ActionKind.update_transaction)

    if "description" in fields:
        tx.description = patch.description or None
    value_changed = False
    old_amount = tx.amount
    if "amount" in fields and patch.amount is not None and patch.amount != tx.amount:
        tx.amount = patch.amount
        value_changed = True
    if "currency" in fields and patch.currency and patch.currency.upper() != tx.currency:
        tx.currency = patch.currency.upper()
        value_changed = True
    date_changed = False
    if "occurred_on" in fields or "occurred_at" in fields:
        at, on = await resolve_when(
            session, uid, patch.occurred_on if "occurred_on" in fields else None,
            patch.occurred_at if "occurred_at" in fields else None,
        )
        date_changed = on != tx.occurred_on or at != tx.occurred_at
        tx.occurred_at, tx.occurred_on = at, on

    account_changed = False
    explicit: Decimal | None = None
    if forgive:
        if "category_id" in fields and patch.category_id is not None:
            cat = await _category_for(session, uid, patch.category_id, tx.kind)
            tx.category_id = cat.id
            tx.is_business = cat.is_business
        if value_changed or date_changed:
            await lock_debt(session, debt_acc, tx.currency)
            # a smaller write-off on the same date only gives the debt back: never blocked
            if date_changed or tx.amount > old_amount:
                current = await debt_balance(session, debt_acc.id, tx.currency)
                # balance without this operation: undo its own posting (−amount expense, +inc.)
                sign = -1 if tx.kind == TxKind.expense else 1
                side = -sign
                before_op = current - sign * old_amount
                remaining = side * before_op
                # …and on its date: not more than was owed then, without making a later point
                # of the history cross zero (the postings still hold the old amount/date)
                series = await debt_series(session, debt_acc.id, tx.currency)
                room = forgive_room(series, tx.occurred_at, tx.id, side,
                                    past_zero(series, side))
                allowed = min(remaining, room)
                if tx.amount > allowed:
                    raise ValidationFailed(
                        "Списать можно не больше остатка долга",
                        details={"field": "amount", "max": normalize(max(allowed, ZERO))},
                    )
            apply_forgive_side(tx, debt_acc)
    else:
        real_id = real_account_id(tx)
        real: Account | None
        if "account_none" in fields and patch.account_none:
            if "account_id" in fields and patch.account_id is not None:
                raise ValidationFailed("Выберите счёт или «без счёта», не оба сразу")
            real, account_changed = None, True
        elif "account_id" in fields:
            real = (
                await _real_account(session, uid, patch.account_id)
                if patch.account_id is not None else None
            )
            account_changed = True
        else:
            real = await session.get(Account, real_id) if real_id is not None else None
        if account_changed:
            tx.status = TxStatus.posted
        explicit = patch.account_amount if "account_amount" in fields else None
        if explicit is not None and real is None:
            raise ValidationFailed("Сумму по счёту можно указать только вместе со счётом",
                                   details={"field": "account_amount"})
        if (
            account_changed
            or value_changed
            or explicit is not None
            or (date_changed and tx.is_estimated)
        ):
            await apply_real_side(session, tx, debt_acc, real, actual=explicit)

    if value_changed or date_changed or account_changed or explicit is not None:
        await apply_valuation(session, tx)
    check_derived(tx)
    bump(tx)
    await _flush(session, tx)
    touched = await rebuild_postings(session, tx)
    await _finish(session, principal, ctx, own, tx, before, "update", touched | {debt_acc.id},
                  DEBT_ENTITIES)
    return tx


async def assign_debt_account(
    session: AsyncSession,
    principal: Principal,
    tx: Transaction,
    account_id: int | None,
    *,
    version: int | None = None,
    action: ActionContext | None = None,
) -> Transaction:
    """«С какого?/На какой счёт?» for a debt transfer: sets the REAL side (target of inflows,
    source of outflows); None = «без счёта». Same version rules as ``assign_account``.
    forgive has no real account → 422."""
    from app.services.ledger.transactions import _finish, _flush, apply_valuation, bump

    if tx.debt_op == DebtOp.forgive:
        raise ValidationFailed("У списания долга нет счёта — его не выбирают")
    if version is None and tx.status != TxStatus.needs_account:
        raise VersionConflict(
            "Счёт для этой операции уже выбран — обновите данные",
            details={"current_version": tx.version, "status": tx.status},
        )
    if version is not None and version != tx.version:
        raise VersionConflict(details={"current_version": tx.version})
    debt_acc = await _debt_account(session, tx)
    real = (
        await _real_account(session, principal.user_id, account_id)
        if account_id is not None else None
    )
    before = snapshot(tx)
    own = action is None
    ctx = action or await begin_action(session, principal, ActionKind.assign_account)
    tx.status = TxStatus.posted
    await apply_real_side(session, tx, debt_acc, real)
    await apply_valuation(session, tx)
    check_derived(tx)
    bump(tx)
    await _flush(session, tx)
    touched = await rebuild_postings(session, tx)
    await _finish(session, principal, ctx, own, tx, before, "update", touched | {debt_acc.id},
                  DEBT_ENTITIES)
    return tx


class ForgiveNotCovered(ValidationFailed):
    """A change would leave a write-off (forgive) bigger than the debt it wrote off: a phantom
    expense/income plus a debt in the opposite direction (docs/DEBTS.md §4: «не больше
    остатка»). Undo/redo turns it into 409 ``undo_conflict``."""


def _debt_side(rows: list[tuple[int, str, Decimal]], acc_id: int) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for a, asset, amt in rows:
        if a == acc_id:
            out[asset] = out.get(asset, ZERO) + amt
    return out


async def check_forgives_covered(session: AsyncSession, undo_data: list[dict[str, Any]]) -> None:
    """Called once per action (``ledger.actions.finish_action``), after all its rows are
    written. A write-off must stay covered by the debt it wrote off — the rule of
    ``record_op`` («списать можно не больше остатка») kept by every later path: undo/redo,
    delete/restore, PATCH, bulk.

    Per (debt account, currency) whose balance this action moved by ``delta`` to ``balance``:
    it is a violation when the move goes past zero on the side of an active forgive —
    ``delta < 0`` and ``balance < 0`` with an active forgive-expense (what the person owed me
    was written off), ``delta > 0`` and ``balance > 0`` with an active forgive-income — and
    that forgive is one of the changed rows (re-activated / grown) or was recorded after one
    of them (so it wrote off a balance that included it). Rows recorded after the forgive
    (a new loan, an overpaying return) are independent of it and never blocked."""
    changes: dict[tuple[int, str], tuple[Decimal, int, set[int]]] = {}
    for item in undo_data:
        if item.get("type") != "transaction":
            continue
        before = item.get("before")
        if before is not None and before.get("debt_op") is None:
            continue  # an ordinary operation never becomes a debt one
        tx = await session.get(Transaction, int(item["id"]))
        if tx is None or tx.debt_op is None:
            continue
        acc_id = debt_account_id(tx)
        if acc_id is None:  # pragma: no cover - data invariant
            continue
        old = (
            _debt_side(postings_for(from_snapshot(Transaction, before)), acc_id)
            if before is not None else {}
        )
        new = _debt_side(postings_for(tx), acc_id)
        for asset in old.keys() | new.keys():
            d = new.get(asset, ZERO) - old.get(asset, ZERO)
            if d == 0:
                continue
            delta, first, ids = changes.get((acc_id, asset), (ZERO, tx.id, set()))
            changes[(acc_id, asset)] = (delta + d, min(first, tx.id), ids | {tx.id})
    for (acc_id, asset), (delta, first, ids) in sorted(changes.items()):
        if delta == 0:
            continue
        bal = await debt_balance(session, acc_id, asset)
        if delta < 0 and bal < 0:
            kind, side = TxKind.expense, 1
        elif delta > 0 and bal > 0:
            kind, side = TxKind.income, -1
        else:
            continue
        forgives = (
            await session.execute(
                select(Transaction.id, Transaction.amount).where(
                    Transaction.account_id == acc_id,
                    Transaction.debt_op == DebtOp.forgive.value,
                    Transaction.kind == kind.value,
                    Transaction.currency == asset,
                    Transaction.deleted_at.is_(None),
                    Transaction.id >= first,
                )
            )
        ).all()
        if not forgives:
            continue
        debt = await session.scalar(select(Debt).where(Debt.account_id == acc_id))
        name = debt.name if debt is not None else "?"
        details: dict[str, Any] = {
            "reason": "forgive_exceeds_debt",
            "debt_id": debt.id if debt is not None else None,
            "currency": asset,
            "balance_after": normalize(bal),
            "forgive_ids": sorted(int(i) for i, _ in forgives),
        }
        own = [Decimal(a) for i, a in forgives if i in ids]
        if own:  # the write-off itself came back / grew: compare with what is left without it
            rest = max(side * bal + sum(own, ZERO), ZERO)
            details["max"] = normalize(rest)
            msg = (
                f"Долг уже закрыт: {name} — списывать нечего" if rest == 0
                else f"Списать можно не больше остатка долга: {name} — сейчас "
                     f"{format_money(rest, asset)}"
            )
        else:
            msg = (
                f"Сначала отмените списание долга: {name} — иначе получится долг в обратную "
                f"сторону"
            )
        raise ForgiveNotCovered(msg, details=details)
    await _check_dated(session, undo_data)


def _debt_points(tx: Transaction, acc_id: int) -> dict[str, tuple[datetime, Decimal]]:
    """asset → (occurred_at, signed change) of ``tx`` on the debt account."""
    out: dict[str, tuple[datetime, Decimal]] = {}
    for a, asset, amt in postings_for(tx):
        if a == acc_id:
            prev = out.get(asset)
            out[asset] = (tx.occurred_at, (prev[1] if prev else ZERO) + amt)
    return out


async def _check_dated(session: AsyncSession, undo_data: list[dict[str, Any]]) -> None:
    """The «on its date» half of the rule (docs/DEBTS.md §4): after this action every active
    write-off that comes (in time) at or after a changed row must still be covered by the debt
    of ITS date, with no later point of the history crossing zero because of the change —
    deleting / shrinking an old loan, or back-dating a return before a write-off, can uncover
    it even when today's balance is fine. Only a change for the worse is blocked: a write-off
    that was already uncovered before (old data) and did not get worse passes; rows recorded
    after a write-off never depend on it."""
    changed: dict[tuple[int, str], dict[int, tuple[datetime, Decimal] | None]] = {}
    earliest: dict[tuple[int, str], tuple[datetime, int]] = {}
    for item in undo_data:
        if item.get("type") != "transaction":
            continue
        before = item.get("before")
        if before is not None and before.get("debt_op") is None:
            continue
        tx = await session.get(Transaction, int(item["id"]))
        if tx is None or tx.debt_op is None:
            continue
        acc_id = debt_account_id(tx)
        if acc_id is None:  # pragma: no cover - data invariant
            continue
        old = _debt_points(from_snapshot(Transaction, before), acc_id) if before else {}
        new = _debt_points(tx, acc_id)
        for asset in old.keys() | new.keys():
            o, n = old.get(asset), new.get(asset)
            if o == n:
                continue
            k = (acc_id, asset)
            changed.setdefault(k, {})[tx.id] = o
            for pt in (o, n):
                if pt is not None and (k not in earliest or (pt[0], tx.id) < earliest[k]):
                    earliest[k] = (pt[0], tx.id)
    for (acc_id, asset), ch in sorted(changed.items()):
        if (acc_id, asset) not in earliest:
            continue
        first = earliest[(acc_id, asset)]
        forgives = [
            f for f in (
                await session.execute(
                    select(Transaction.id, Transaction.kind, Transaction.amount,
                           Transaction.occurred_at).where(
                        Transaction.account_id == acc_id,
                        Transaction.debt_op == DebtOp.forgive.value,
                        Transaction.currency == asset,
                        Transaction.deleted_at.is_(None),
                    )
                )
            ).all()
            if (f.occurred_at, f.id) >= first
        ]
        if not forgives:
            continue
        now_series = await debt_series(session, acc_id, asset)
        prev_series = sorted(
            [p for p in now_series if p[1] not in ch]
            + [(o[0], i, o[1]) for i, o in ch.items() if o is not None],
            key=lambda p: (p[0], p[1]),
        )
        prev_at = {i: a for a, i, _ in prev_series}
        for fid, kind, amount, at in sorted(forgives, key=lambda f: (f.occurred_at, f.id)):
            side = 1 if kind == TxKind.expense.value else -1
            skip = past_zero(prev_series, side)
            slack = forgive_room(now_series, at, fid, side, skip) - Decimal(amount)
            if slack >= 0:
                continue
            if fid in prev_at:  # existed before: blocked only if it got worse
                old_amount = Decimal(amount)
                if fid in ch:
                    o = ch[fid]
                    old_amount = side * -o[1] if o is not None else old_amount
                old_slack = forgive_room(prev_series, prev_at[fid], fid, side, skip) - old_amount
                if slack >= old_slack:
                    continue
            debt = await session.scalar(select(Debt).where(Debt.account_id == acc_id))
            name = debt.name if debt is not None else "?"
            allowed = max(Decimal(amount) + slack, ZERO)
            details: dict[str, Any] = {
                "reason": "forgive_exceeds_debt",
                "debt_id": debt.id if debt is not None else None,
                "currency": asset,
                "forgive_ids": [int(fid)],
                "max": normalize(allowed),
            }
            if fid in ch:
                msg = (
                    f"На дату списания долга не было: {name} — списывать нечего" if allowed == 0
                    else f"Списать можно не больше остатка долга на дату списания: {name} — "
                         f"{format_money(allowed, asset)}"
                )
            else:
                msg = (
                    f"Сначала отмените списание долга: {name} — иначе оно окажется больше "
                    f"долга на свою дату"
                )
            raise ForgiveNotCovered(msg, details=details)


async def get_debt_tx(session: AsyncSession, user_id: int, tx_id: int) -> Transaction:
    tx = await session.get(Transaction, tx_id)
    if tx is None or tx.user_id != user_id or tx.debt_op is None:
        raise NotFound("Операция не найдена", details={"transaction_id": tx_id})
    return tx


__all__ = [
    "DEBT_ENTITIES",
    "ForgiveNotCovered",
    "apply_forgive_side",
    "apply_real_side",
    "assign_debt_account",
    "check_forgives_covered",
    "debt_balance",
    "ensure_debt_asset",
    "get_debt_tx",
    "lock_debt",
    "real_account_id",
    "update_debt_transaction",
]

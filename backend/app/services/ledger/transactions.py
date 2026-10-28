"""Create/update/delete transactions with postings, rates, audit, action log, events.
Money semantics: docs/ARCH.md §4. All functions flush, never commit.

When ``action`` is passed, changes are recorded into it and the CALLER finishes it
(``actions.finish_action``); otherwise an own action is started and finished here."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.core.errors import AppError, NotFound, ValidationFailed, VersionConflict
from app.core.money import is_fiat, normalize
from app.core.principal import Principal
from app.models import Account, Category, Merchant, RecurringTransaction, Transaction
from app.models.enums import ActionKind, TxKind, TxStatus
from app.schemas.transactions import TransactionCreate, TransactionOut, TransactionPatch
from app.services.accounts import service as accounts
from app.services.categories import service as categories
from app.services.ledger.actions import ActionContext, begin_action, finish_action
from app.services.ledger.audit import snapshot, write_audit
from app.services.ledger.common import (
    check_amount,
    check_derived,
    convert_to_asset,
    div,
    now,
    publish,
    q_rate,
    resolve_when,
    strip_handle,
    valuate,
    writer_lock,
)
from app.services.ledger.postings import rebuild_postings
from app.services.ledger.summaries import to_out as _to_out
from app.services.merchants import service as merchants

PNL = (TxKind.expense, TxKind.income, TxKind.refund)


# ------------------------------------------------------------------------------- lookups


async def get_transaction(
    session: AsyncSession, user_id: int, tx_id: int, *, for_update: bool = False
) -> Transaction:
    """404 if missing or belongs to another user. Deleted rows are returned (check deleted_at).
    ``for_update`` = a writer: takes the shared per-user writer lock before the row lock."""
    stmt = select(Transaction).where(Transaction.id == tx_id, Transaction.user_id == user_id)
    if for_update:
        await writer_lock(session, user_id)
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    tx = await session.scalar(stmt)
    if tx is None:
        raise NotFound("Операция не найдена", details={"transaction_id": tx_id})
    return tx


async def to_out(session: AsyncSession, tx: Transaction) -> TransactionOut:
    """Build TransactionOut incl. nested refs and Russian ``title``."""
    return await _to_out(session, tx)


async def _body_account(
    session: AsyncSession, user_id: int, account_id: int, field: str = "account_id"
) -> Account:
    """An own account from a request body. Debt service accounts → 422: only services/debts
    writes them (docs/DEBTS.md §2)."""
    try:
        acc = await accounts.get_account(session, user_id, account_id)
    except NotFound as e:
        raise ValidationFailed("Счёт не найден", details={"field": field}) from e
    return accounts.check_not_debt(acc, field)


async def _category_for(
    session: AsyncSession, user_id: int, category_id: int, kind: str
) -> Category:
    try:
        c = await categories.effective_category(session, user_id, category_id)
    except NotFound as e:
        raise ValidationFailed("Категория не найдена", details={"field": "category_id"}) from e
    expected = "income" if kind == TxKind.income else "expense"
    if c.kind != expected:
        what = "доходов" if c.kind == "income" else "расходов"
        raise ValidationFailed(
            f"Категория «{c.name}» — для {what}", details={"field": "category_id"}
        )
    return c


async def _merchant(session: AsyncSession, user_id: int, merchant_id: int | None
                    ) -> Merchant | None:
    if merchant_id is None:
        return None
    m = await session.get(Merchant, merchant_id)
    if m is None or m.user_id != user_id:
        raise ValidationFailed("Продавец не найден", details={"field": "merchant_id"})
    return m


async def _check_counterparty(session: AsyncSession, user_id: int, cp: str | None) -> None:
    if cp and cp.strip().startswith("@"):
        own = await accounts.my_account_by_handle(session, user_id, cp)
        if own is not None:
            raise ValidationFailed(
                f"{cp.strip()} — ваш аккаунт: это перевод между своими, а не расход",
                details={"field": "counterparty", "my_account_id": own.id},
            )


async def _check_refund(
    session: AsyncSession, user_id: int, orig_id: int, amount: Decimal, currency: str,
    *, exclude_id: int | None = None, allow_deleted: bool = False,
) -> None:
    """A refund points to a live expense of this user and, in the expense's currency, together
    with the earlier refunds of it does not exceed the expense (422). The expense row is locked
    (FOR UPDATE): two parallel refunds of it cannot both take the same room."""
    orig = await session.scalar(
        select(Transaction).where(Transaction.id == orig_id, Transaction.user_id == user_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if orig is None or orig.kind != TxKind.expense:
        raise ValidationFailed("Исходный расход не найден", details={"field": "refund_of_id"})
    if orig.deleted_at is not None and not allow_deleted:
        raise ValidationFailed("Исходный расход удалён", details={"field": "refund_of_id"})
    if currency.upper() != orig.currency.upper():
        return  # another currency: amounts are not comparable without a rate
    stmt = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
        Transaction.user_id == user_id,
        Transaction.refund_of_id == orig.id,
        Transaction.kind == TxKind.refund,
        Transaction.deleted_at.is_(None),
        Transaction.currency == orig.currency,
    )
    if exclude_id is not None:
        stmt = stmt.where(Transaction.id != exclude_id)
    done = Decimal(await session.scalar(stmt) or 0)
    room = orig.amount - done
    if amount > room:
        raise ValidationFailed(
            "Возврат больше расхода" if done == 0 else "Возврат больше остатка расхода",
            details={"field": "amount", "max": normalize(max(room, Decimal(0))),
                     "refund_of_id": orig.id},
        )


# ------------------------------------------------------------------------------- account charge


async def apply_account(
    session: AsyncSession,
    tx: Transaction,
    account: Account | None,
    *,
    account_amount: Decimal | None = None,
) -> None:
    """Set account_* fields (ARCH §4.8). Multi-asset → charge in the same coin (coin is added
    to the wallet). Single-currency, other fiat currency → ≈ by rate, is_estimated (money
    credited to a ₫ account — income/refund — at the VCB buy rate). An explicit
    ``account_amount`` is the actual charge (is_estimated=False, rate = account/amount).
    A coin cannot be paid from a single-currency account («На счёте Карта нет TON»), and in
    the account's own currency the charge equals the amount (commission 0%)."""
    if account is None:
        tx.account_id = None
        tx.account_amount = None
        tx.account_asset = None
        tx.account_rate = None
        tx.is_estimated = False
        return
    asset = tx.currency if account.is_multi_asset else (account.currency or tx.currency)
    if asset != tx.currency and not is_fiat(tx.currency):
        raise ValidationFailed(
            f"На счёте {account.name} нет {tx.currency} — выберите другой счёт",
            details={"field": "account_id", "account_id": account.id, "asset": tx.currency},
        )
    if account_amount is not None and asset == tx.currency and account_amount != tx.amount:
        raise ValidationFailed(
            f"Счёт {account.name} в {asset}: списание должно совпадать с суммой операции",
            details={"field": "account_amount"},
        )
    check_amount(account_amount, "account_amount")
    await accounts.ensure_asset(session, account, asset)
    tx.account_id = account.id
    tx.account_asset = asset
    if account_amount is not None:
        tx.account_amount = account_amount
        tx.account_rate = q_rate(div(account_amount, tx.amount))
        tx.is_estimated = False
    elif asset == tx.currency:
        tx.account_amount = tx.amount
        tx.account_rate = Decimal(1)
        tx.is_estimated = False
    else:
        amt, rate = await convert_to_asset(
            session, tx.amount, tx.currency, asset, tx.occurred_on,
            inflow=tx.kind in (TxKind.income, TxKind.refund),
        )
        tx.account_amount = amt
        tx.account_rate = rate
        tx.is_estimated = True


async def apply_valuation(session: AsyncSession, tx: Transaction) -> None:
    v = await valuate(session, tx.amount, tx.currency, tx.occurred_on)
    tx.amount_usd = v.amount_usd
    tx.amount_vnd = v.amount_vnd
    tx.usd_rate = v.usd_rate
    tx.rate_date = v.rate_date
    tx.rate_source = v.rate_source
    use_actual_charge(tx)


def use_actual_charge(tx: Transaction) -> None:
    """A confirmed charge from a ₫ account («васт списали 21.3м») is the real ₫ cost: the ₫
    summaries (amount_vnd) use it instead of the rate estimate; amount_usd stays as written."""
    if (
        tx.kind in PNL
        and tx.account_asset == "VND"
        and tx.currency != "VND"
        and tx.account_amount is not None
        and not tx.is_estimated
    ):
        tx.amount_vnd = tx.account_amount


def bump(tx: Transaction) -> None:
    """One version step per logical change (optimistic locking, ARCH §13)."""
    tx.version = (tx.version or 0) + 1


async def _flush(session: AsyncSession, tx: Transaction) -> None:
    try:
        await session.flush()
    except StaleDataError as e:
        raise VersionConflict(details={"transaction_id": tx.id}) from e


async def _finish(
    session: AsyncSession,
    principal: Principal,
    ctx: ActionContext,
    own: bool,
    tx: Transaction,
    before: dict | None,
    operation: str,
    touched_accounts: set[int],
    extra: set[str] | None = None,
) -> None:
    ctx.record("transaction", tx.id, before, tx.version)
    await write_audit(
        session, user_id=tx.user_id, entity_type="transaction", entity_id=tx.id,
        operation=operation, before=before, after=snapshot(tx), source=principal.source,
        action_id=ctx.id,
    )
    entities = {"transactions"} | (extra or set())
    if touched_accounts:
        entities.add("accounts")
    if tx.debt_op is not None:  # delete/restore/edit of a debt operation moves a debt balance
        entities.add("debts")
    ctx.changed |= entities
    if own:
        await finish_action(session, ctx)
    await publish(session, tx.user_id, entities, action_id=ctx.id, source=principal.source)


async def _rule_open(session: AsyncSession, m: Merchant) -> bool:
    """The rule has no real category yet (none, or only a fallback one)."""
    if m.category_id is None:
        return True
    return categories.is_fallback(await session.get(Category, m.category_id))


async def _learn_from(
    session: AsyncSession, tx: Transaction, merchant: Merchant | None, *, on_create: bool
) -> bool:
    """Merchant memory. Create: hits+1 (category set if the rule has none or only a fallback
    one; a fallback category itself is never learned automatically); a @handle counterparty
    without a rule creates one. Category change: the rule follows the user."""
    if merchant is not None:
        if on_create:
            await merchants.learn(
                session, tx.user_id, name=merchant.name, handle=merchant.handle,
                category_id=tx.category_id if await _rule_open(session, merchant) else None,
            )
        else:
            await merchants.learn(
                session, tx.user_id, name=merchant.name, handle=merchant.handle,
                category_id=tx.category_id, count_hit=False,
            )
        return True
    handle = strip_handle(tx.counterparty) if (tx.counterparty or "").startswith("@") else None
    if handle and tx.kind in PNL:
        existing = await merchants.match_merchant(session, tx.user_id, handle=handle)
        if existing is not None and on_create:
            m = await merchants.learn(
                session, tx.user_id, name=existing.name, handle=handle,
                category_id=tx.category_id if await _rule_open(session, existing) else None,
            )
        else:
            m = await merchants.learn(
                session, tx.user_id, name=None, handle=handle, category_id=tx.category_id,
                count_hit=on_create,
            )
        tx.merchant_id = m.id
        return True
    return False


# ------------------------------------------------------------------------------- create


async def create_transaction(
    session: AsyncSession,
    principal: Principal,
    data: TransactionCreate,
    *,
    raw_text: str | None = None,
    ai_request_id: int | None = None,
    action: ActionContext | None = None,
) -> Transaction:
    """Create expense/income/refund.

    * account resolution: explicit → merchant default → default_for_currencies → needs_account
      (crypto/USD/USDT without account) — ARCH §4.7; ``account_none`` → «без счёта»;
    * account_amount/asset/rate/is_estimated — ARCH §4.8 (rates via services.rates);
    * amount_usd/amount_vnd/usd_rate/rate_date/rate_source at occurred_on (NULL if no rate);
    * is_business from category; merchant hits/learn; postings rebuild; audit; events.publish.
    """
    uid = principal.user_id
    await writer_lock(session, uid)
    if data.account_none and data.account_id is not None:
        raise ValidationFailed("Выберите счёт или «без счёта», не оба сразу")
    check_amount(data.amount)
    check_amount(data.account_amount, "account_amount")
    at, on = await resolve_when(session, uid, data.occurred_on, data.occurred_at)
    cat = await _category_for(session, uid, data.category_id, data.kind)
    merchant = await _merchant(session, uid, data.merchant_id)
    await _check_counterparty(session, uid, data.counterparty)
    if data.refund_of_id is not None:
        if data.kind != TxKind.refund:
            raise ValidationFailed("refund_of_id только для возврата")
        await _check_refund(session, uid, data.refund_of_id, data.amount, data.currency)
    if data.recurring_id is not None:
        rec = await session.get(RecurringTransaction, data.recurring_id)
        if rec is None or rec.user_id != uid:
            raise ValidationFailed("Напоминание не найдено", details={"field": "recurring_id"})

    account: Account | None = None
    status = TxStatus.posted
    if data.account_id is not None:
        account = await _body_account(session, uid, data.account_id)
    elif not data.account_none:
        if merchant is not None and merchant.default_account_id is not None:
            # fresh row (populate_existing): the identity map may hold it from before the
            # writer lock — a parallel delete must be seen here
            cand = await session.get(Account, merchant.default_account_id,
                                     populate_existing=True)
            if (
                cand is not None
                and cand.deleted_at is None
                and not cand.is_archived
                and not accounts.is_debt_account(cand)
            ):
                account = cand
        if account is None:
            account = await accounts.default_account_for(session, uid, data.currency)
        if account is None:
            status = TxStatus.needs_account
    if account is None and data.account_amount is not None:
        raise ValidationFailed("Сумму списания можно указать только вместе со счётом",
                               details={"field": "account_amount"})

    own = action is None
    ctx = action or await begin_action(
        session, principal, ActionKind.create_transaction, input_text=raw_text,
        ai_request_id=ai_request_id,
    )
    tx = Transaction(
        user_id=uid,
        kind=data.kind,
        status=status,
        occurred_at=at,
        occurred_on=on,
        amount=data.amount,
        currency=data.currency,
        category_id=cat.id,
        is_business=cat.is_business,
        merchant_id=merchant.id if merchant else None,
        counterparty=(data.counterparty or None),
        description=(data.description or None),
        refund_of_id=data.refund_of_id,
        recurring_id=data.recurring_id,
        source=principal.source,
        raw_text=raw_text,
        ai_request_id=ai_request_id,
        created_action_id=ctx.id,
        version=1,
    )
    await apply_account(session, tx, account, account_amount=data.account_amount)
    await apply_valuation(session, tx)
    check_derived(tx)
    session.add(tx)
    await session.flush()
    learned = await _learn_from(session, tx, merchant, on_create=True)
    await _flush(session, tx)
    touched = await rebuild_postings(session, tx)
    await _finish(session, principal, ctx, own, tx, None, "create", touched,
                  {"merchants"} if learned else None)
    return tx


# ------------------------------------------------------------------------------- update


_NON_PNL_FIELDS = {"amount", "description", "occurred_on", "occurred_at"}


async def update_transaction(
    session: AsyncSession,
    principal: Principal,
    tx_id: int,
    patch: TransactionPatch,
    *,
    action: ActionContext | None = None,
) -> Transaction:
    """Apply a partial update with optimistic locking (409 version_conflict).
    Recompute rates/account_amount/postings when amount/currency/date/account change;
    ``account_amount`` given → is_estimated=False and account_rate=account_amount/amount.
    Category change on a merchant transaction → merchants.learn (rule update)."""
    uid = principal.user_id
    tx = await get_transaction(session, uid, tx_id, for_update=True)
    if tx.deleted_at is not None:
        raise NotFound("Операция удалена", details={"transaction_id": tx_id})
    if patch.version != tx.version:
        raise VersionConflict(details={"current_version": tx.version})
    f = set(patch.model_fields_set) - {"version"}
    if not f:
        raise ValidationFailed("Нет изменений")
    if tx.debt_op is not None:  # debt operation: own edit rules (docs/DEBTS.md §4 «Правка»)
        from app.services.debts.ops import update_debt_transaction

        return await update_debt_transaction(session, principal, tx, patch, f, action=action)
    pnl = tx.kind in PNL
    if tx.kind == TxKind.adjustment and f - {"description", "occurred_on", "occurred_at"}:
        raise ValidationFailed("У корректировки можно изменить только дату и описание")
    if tx.kind == TxKind.exchange and f - _NON_PNL_FIELDS - {"counter_amount"}:
        raise ValidationFailed(
            "У обмена можно изменить только суммы (отдал/получил), дату и описание"
        )
    if tx.kind == TxKind.transfer and f - _NON_PNL_FIELDS:
        raise ValidationFailed("У перевода можно изменить только сумму, дату и описание")
    if pnl and "counter_amount" in f:
        raise ValidationFailed("Полученная сумма есть только у обмена",
                               details={"field": "counter_amount"})
    check_amount(patch.amount)
    check_amount(patch.account_amount, "account_amount")
    check_amount(patch.counter_amount, "counter_amount")
    before = snapshot(tx)
    own = action is None
    ctx = action or await begin_action(session, principal, ActionKind.update_transaction)

    new_kind = patch.kind if "kind" in f and patch.kind else tx.kind
    if "kind" in f and patch.kind and patch.kind != tx.kind:
        tx.kind = patch.kind
    category_changed = False
    if "category_id" in f and patch.category_id is not None:
        cat = await _category_for(session, uid, patch.category_id, new_kind)
        category_changed = cat.id != tx.category_id
        tx.category_id = cat.id
        tx.is_business = cat.is_business
    elif pnl and new_kind != before["kind"] and tx.category_id is not None:
        cat = await _category_for(session, uid, tx.category_id, new_kind)
    merchant: Merchant | None = None
    if "merchant_id" in f:
        merchant = await _merchant(session, uid, patch.merchant_id)
        tx.merchant_id = merchant.id if merchant else None
    elif tx.merchant_id:
        merchant = await session.get(Merchant, tx.merchant_id)
    if "counterparty" in f:
        await _check_counterparty(session, uid, patch.counterparty)
        tx.counterparty = patch.counterparty or None
    if "description" in f:
        tx.description = patch.description or None

    value_changed = False
    if "amount" in f and patch.amount is not None and patch.amount != tx.amount:
        tx.amount = patch.amount
        value_changed = True
    if "currency" in f and patch.currency and patch.currency != tx.currency:
        tx.currency = patch.currency
        value_changed = True
    counter_changed = False
    if (
        "counter_amount" in f
        and patch.counter_amount is not None
        and patch.counter_amount != tx.counter_amount
    ):
        tx.counter_amount = patch.counter_amount
        counter_changed = True
    date_changed = False
    if "occurred_on" in f or "occurred_at" in f:
        at, on = await resolve_when(
            session, uid, patch.occurred_on if "occurred_on" in f else None,
            patch.occurred_at if "occurred_at" in f else None,
        )
        date_changed = on != tx.occurred_on or at != tx.occurred_at
        tx.occurred_at, tx.occurred_on = at, on

    account_changed = False
    explicit: Decimal | None = None
    if pnl:
        account: Account | None
        if "account_none" in f and patch.account_none:
            if "account_id" in f and patch.account_id is not None:
                raise ValidationFailed("Выберите счёт или «без счёта», не оба сразу")
            account, account_changed = None, True
        elif "account_id" in f:
            account = (
                await _body_account(session, uid, patch.account_id)
                if patch.account_id is not None
                else None
            )
            account_changed = True
        else:
            account = await session.get(Account, tx.account_id) if tx.account_id else None
        if account_changed:
            tx.status = TxStatus.posted
        explicit = patch.account_amount if "account_amount" in f else None
        if explicit is not None and account is None:
            raise ValidationFailed("Сумму списания можно указать только вместе со счётом",
                                   details={"field": "account_amount"})
        if (
            account_changed
            or value_changed
            or explicit is not None
            or (date_changed and tx.is_estimated)
        ):
            await apply_account(session, tx, account, account_amount=explicit)
    elif value_changed or counter_changed:
        tx.account_amount = tx.amount
        if tx.kind == TxKind.transfer:
            tx.counter_amount = tx.amount
        elif tx.kind == TxKind.exchange and tx.counter_amount:
            # the exchange keeps the actual rate: received / given
            tx.account_rate = q_rate(div(tx.counter_amount, tx.amount))

    if value_changed and tx.kind == TxKind.refund and tx.refund_of_id is not None:
        await _check_refund(session, uid, tx.refund_of_id, tx.amount, tx.currency,
                            exclude_id=tx.id, allow_deleted=True)
    if value_changed or date_changed or account_changed or explicit is not None:
        await apply_valuation(session, tx)
    check_derived(tx)

    bump(tx)
    await _flush(session, tx)
    learned = False
    if pnl and (category_changed or "merchant_id" in f):
        learned = await _learn_from(session, tx, merchant, on_create=False)
        await _flush(session, tx)
    touched = await rebuild_postings(session, tx)
    await _finish(session, principal, ctx, own, tx, before, "update", touched,
                  {"merchants"} if learned else None)
    return tx


# ------------------------------------------------------------------------------- delete etc.


async def delete_transaction(
    session: AsyncSession,
    principal: Principal,
    tx_id: int,
    *,
    version: int | None = None,
    action: ActionContext | None = None,
) -> Transaction:
    """Soft delete (deleted_at), drop postings."""
    tx = await get_transaction(session, principal.user_id, tx_id, for_update=True)
    if tx.deleted_at is not None:
        raise NotFound("Операция уже удалена", details={"transaction_id": tx_id})
    if version is not None and version != tx.version:
        raise VersionConflict(details={"current_version": tx.version})
    before = snapshot(tx)
    own = action is None
    ctx = action or await begin_action(session, principal, ActionKind.delete_transaction)
    tx.deleted_at = now()
    bump(tx)
    await _flush(session, tx)
    touched = await rebuild_postings(session, tx)
    await _finish(session, principal, ctx, own, tx, before, "delete", touched)
    return tx


async def restore_transaction(
    session: AsyncSession,
    principal: Principal,
    tx_id: int,
    *,
    action: ActionContext | None = None,
) -> Transaction:
    tx = await get_transaction(session, principal.user_id, tx_id, for_update=True)
    if tx.deleted_at is None:
        raise ValidationFailed("Операция не удалена")
    before = snapshot(tx)
    own = action is None
    ctx = action or await begin_action(session, principal, ActionKind.restore_transaction)
    tx.deleted_at = None
    bump(tx)
    await _flush(session, tx)
    touched = await rebuild_postings(session, tx)
    await _finish(session, principal, ctx, own, tx, before, "restore", touched)
    return tx


async def assign_account(
    session: AsyncSession,
    principal: Principal,
    tx_id: int,
    account_id: int | None,
    *,
    version: int | None = None,
    action: ActionContext | None = None,
) -> Transaction:
    """Answer to «С какого?»: set account (or «без счёта» = None), status → posted, postings.

    Without ``version`` this is only the answer to the question: the operation must still be
    ``needs_account`` — a stale card in a second client must not silently move an already
    posted operation (409 version_conflict). Changing the account of a posted operation needs
    the ``version`` (web «Сменить счёт») or PATCH."""
    uid = principal.user_id
    tx = await get_transaction(session, uid, tx_id, for_update=True)
    if tx.deleted_at is not None:
        raise NotFound("Операция удалена", details={"transaction_id": tx_id})
    if tx.debt_op is not None:  # real side of a debt transfer (docs/DEBTS.md §4)
        from app.services.debts.ops import assign_debt_account

        return await assign_debt_account(
            session, principal, tx, account_id, version=version, action=action
        )
    if tx.kind not in PNL:
        raise ValidationFailed("Счёт выбирается только для расхода/дохода/возврата")
    if version is None and tx.status != TxStatus.needs_account:
        raise VersionConflict(
            "Счёт для этой операции уже выбран — обновите данные",
            details={"current_version": tx.version, "status": tx.status},
        )
    if version is not None and version != tx.version:
        raise VersionConflict(details={"current_version": tx.version})
    account = await _body_account(session, uid, account_id) if account_id is not None else None
    before = snapshot(tx)
    own = action is None
    ctx = action or await begin_action(session, principal, ActionKind.assign_account)
    tx.status = TxStatus.posted
    await apply_account(session, tx, account)
    await apply_valuation(session, tx)
    check_derived(tx)
    bump(tx)
    await _flush(session, tx)
    touched = await rebuild_postings(session, tx)
    await _finish(session, principal, ctx, own, tx, before, "update", touched)
    return tx


__all__ = [
    "AppError",
    "apply_account",
    "apply_valuation",
    "assign_account",
    "bump",
    "create_transaction",
    "delete_transaction",
    "get_transaction",
    "restore_transaction",
    "to_out",
    "update_transaction",
]

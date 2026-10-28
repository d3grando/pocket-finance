"""Accounts. Multi-asset wallets hold any coin; a coin row is created on first
mention (``ensure_asset``). opening_balance NULL → balance unknown (not shown)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import any_, func, or_, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Conflict, NotFound, ValidationFailed, VersionConflict
from app.core.principal import Principal
from app.core.timeutil import local_date_of, local_today
from app.models import Account, AccountAsset, MyAccount, Transaction
from app.models.enums import AccountKind
from app.schemas.accounts import AccountCreate, AccountPatch, MyAccountCreate
from app.services.ledger.audit import snapshot, write_audit
from app.services.ledger.common import (
    check_amount,
    check_limit,
    norm,
    norm_aliases,
    now,
    publish,
    strip_handle,
    user_tz,
    writer_lock,
)

DEFAULT_ICONS = {
    AccountKind.bank: "Landmark",
    AccountKind.cash: "Wallet",
    AccountKind.card: "CreditCard",
    AccountKind.crypto_wallet: "Bot",
    AccountKind.exchange: "ChartCandlestick",
    AccountKind.other: "PiggyBank",
}


class AlreadyExists(Conflict):
    code, message = "already_exists", "Уже существует"


#: Own accounts per user (debt service accounts are counted with the debts limit).
MAX_ACCOUNTS = 100


DEBT_ACCOUNT_MESSAGE = "Это счёт долга — используйте «Долги»"


def is_debt_account(account: Account | None) -> bool:
    """Service account of a debt counterparty (docs/DEBTS.md §2): only services/debts writes it."""
    return account is not None and account.kind == AccountKind.debt


def check_not_debt(account: Account, field: str = "account_id") -> Account:
    """422 for a debt service account in manual operations / account editing (DEBTS.md §2)."""
    if is_debt_account(account):
        raise ValidationFailed(
            DEBT_ACCOUNT_MESSAGE,
            details={"field": field, "account_id": account.id, "reason": "debt_account"},
        )
    return account


def _active(  # type: ignore[no-untyped-def]
    stmt, include_archived: bool = False, include_debts: bool = False
):
    stmt = stmt.where(Account.deleted_at.is_(None))
    if not include_archived:
        stmt = stmt.where(Account.is_archived.is_(False))
    if not include_debts:
        stmt = stmt.where(Account.kind != AccountKind.debt.value)
    return stmt


async def list_accounts(
    session: AsyncSession,
    user_id: int,
    *,
    include_archived: bool = False,
    include_debts: bool = False,
) -> list[Account]:
    """Own accounts. Debt service accounts (kind=debt) only with ``include_debts`` — they are
    hidden from lists, resolution, defaults and pickers (docs/DEBTS.md §2)."""
    stmt = _active(
        select(Account).where(Account.user_id == user_id), include_archived, include_debts
    )
    return list(await session.scalars(stmt.order_by(Account.sort_order, Account.id)))


async def get_account(
    session: AsyncSession, user_id: int, account_id: int, *, for_update: bool = False
) -> Account:
    stmt = select(Account).where(
        Account.id == account_id, Account.user_id == user_id, Account.deleted_at.is_(None)
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    acc = await session.scalar(stmt)
    if acc is None:
        raise NotFound("Счёт не найден", details={"account_id": account_id})
    return acc


async def resolve_account(session: AsyncSession, user_id: int, ref: str) -> Account | None:
    """Match by exact name / alias / own @handle (case-insensitive, ё=е), then unique prefix.
    None if nothing or ambiguous."""
    key = norm(ref)
    if not key:
        return None
    accounts = await list_accounts(session, user_id)
    exact = [a for a in accounts if norm(a.name) == key or key in {norm(x) for x in a.aliases}]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    handle = strip_handle(key)
    if handle:
        ma = await session.scalar(
            select(MyAccount).where(MyAccount.user_id == user_id, MyAccount.handle == handle)
        )
        if ma is not None:
            acc = next((a for a in accounts if a.id == ma.account_id), None)
            if acc is not None:
                return acc
    pref = [
        a
        for a in accounts
        if norm(a.name).startswith(key) or any(norm(x).startswith(key) for x in a.aliases)
    ]
    return pref[0] if len(pref) == 1 else None


async def default_account_for(session: AsyncSession, user_id: int, currency: str) -> Account | None:
    """Account whose default_for_currencies contains ``currency`` (VND → Карта, RUB → Карта RUB)."""
    stmt = _active(
        select(Account).where(
            Account.user_id == user_id, currency.upper() == any_(Account.default_for_currencies)
        )
    )
    return await session.scalar(stmt.order_by(Account.sort_order, Account.id).limit(1))


def supports_asset(account: Account, asset: str) -> bool:
    return account.is_multi_asset or (account.currency or "").upper() == asset.upper()


async def ensure_asset(session: AsyncSession, account: Account, asset: str) -> AccountAsset:
    """Get or create the (account, asset) row (race-safe). Single-currency + other asset → 422."""
    asset = asset.upper()
    if not supports_asset(account, asset):
        raise ValidationFailed(
            f"Счёт «{account.name}» в {account.currency}, а не в {asset}",
            details={"account_id": account.id, "asset": asset},
        )
    row = await session.scalar(
        select(AccountAsset).where(
            AccountAsset.account_id == account.id, AccountAsset.asset == asset
        )
    )
    if row is not None:
        return row
    await session.execute(
        pg_insert(AccountAsset)
        .values(user_id=account.user_id, account_id=account.id, asset=asset)
        .on_conflict_do_nothing(index_elements=["account_id", "asset"])
    )
    row = await session.scalar(
        select(AccountAsset)
        .where(AccountAsset.account_id == account.id, AccountAsset.asset == asset)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    if "assets" not in sa_inspect(account).unloaded and row not in account.assets:
        account.assets.append(row)
    return row


async def list_assets(session: AsyncSession, account_ids: list[int]) -> list[AccountAsset]:
    if not account_ids:
        return []
    return list(
        await session.scalars(
            select(AccountAsset)
            .where(AccountAsset.account_id.in_(account_ids))
            .order_by(AccountAsset.account_id, AccountAsset.id)
        )
    )


async def _check_name(
    session: AsyncSession, user_id: int, name: str, exclude_id: int | None = None
) -> None:
    stmt = select(Account.id).where(
        Account.user_id == user_id,
        func.lower(Account.name) == name.strip().lower(),
        Account.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(Account.id != exclude_id)
    existing = await session.scalar(stmt)
    if existing is not None:
        raise AlreadyExists(f"Счёт «{name}» уже есть", details={"id": existing})


async def _lock_defaults(session: AsyncSession, user_id: int) -> None:
    """Serialise «default account per currency» changes of one user: the rule is checked in the
    service (no DB constraint), so two parallel PATCHes must not both pass the check."""
    await session.execute(
        select(func.pg_advisory_xact_lock(_DEFAULTS_LOCK_NS | int(user_id)))
    )


_DEFAULTS_LOCK_NS = 0x5047 << 48


async def _check_defaults(
    session: AsyncSession, user_id: int, currencies: list[str], exclude_id: int | None = None
) -> None:
    if currencies:
        await _lock_defaults(session, user_id)
    for cur in currencies:
        stmt = select(Account).where(
            Account.user_id == user_id,
            Account.deleted_at.is_(None),
            cur == any_(Account.default_for_currencies),
        )
        if exclude_id is not None:
            stmt = stmt.where(Account.id != exclude_id)
        other = await session.scalar(stmt)
        if other is not None:
            raise ValidationFailed(
                f"{cur} уже списывается по умолчанию со счёта «{other.name}»",
                details={"currency": cur, "account_id": other.id},
            )


async def create_account(
    session: AsyncSession, principal: Principal, data: AccountCreate
) -> Account:
    user_id = principal.user_id
    name = data.name.strip()
    await check_limit(
        session,
        select(func.count()).select_from(Account).where(
            Account.user_id == user_id, Account.deleted_at.is_(None),
            Account.kind != AccountKind.debt.value,
        ),
        MAX_ACCOUNTS, "счетов",
    )
    await _check_name(session, user_id, name)
    currency = None if data.is_multi_asset else (data.currency or "").upper() or None
    if not data.is_multi_asset and currency is None:
        raise ValidationFailed("Укажите валюту счёта", details={"field": "currency"})
    if data.is_multi_asset and data.opening_balance is not None:
        raise ValidationFailed(
            "У мультивалютного кошелька остаток задаётся по каждой монете",
            details={"field": "opening_balance"},
        )
    defaults = [c.upper() for c in data.default_for_currencies]
    for c in defaults:
        if not data.is_multi_asset and c != currency:
            raise ValidationFailed(
                f"Счёт в {currency} не может быть основным для {c}",
                details={"field": "default_for_currencies"},
            )
    await _check_defaults(session, user_id, defaults)
    max_sort = await session.scalar(
        select(func.coalesce(func.max(Account.sort_order), 0)).where(Account.user_id == user_id)
    )
    check_amount(data.opening_balance, "opening_balance")
    acc = Account(
        user_id=user_id,
        name=name,
        kind=data.kind,
        currency=currency,
        is_multi_asset=data.is_multi_asset,
        aliases=norm_aliases(data.aliases),
        default_for_currencies=defaults,
        color=data.color,
        icon=data.icon or DEFAULT_ICONS.get(AccountKind(data.kind), "PiggyBank"),
        sort_order=int(max_sort or 0) + 1,
        version=1,
    )
    await session.flush()  # pending changes stay outside the savepoint
    try:
        async with session.begin_nested():
            session.add(acc)
            await session.flush()
    except IntegrityError as e:  # concurrent duplicate name (double submit)
        raise AlreadyExists(f"Счёт «{name}» уже есть") from e
    if currency:
        aa = await ensure_asset(session, acc, currency)
        if data.opening_balance is not None:
            aa.opening_balance = data.opening_balance
            aa.opening_at = now()
    await session.flush()
    await write_audit(
        session, user_id=user_id, entity_type="account", entity_id=acc.id, operation="create",
        before=None, after=snapshot(acc), source=principal.source,
    )
    await publish(session, user_id, {"accounts"}, source=principal.source)
    return acc


async def update_account(
    session: AsyncSession, principal: Principal, account_id: int, patch: AccountPatch
) -> Account:
    user_id = principal.user_id
    acc = check_not_debt(await get_account(session, user_id, account_id, for_update=True))
    if patch.version != acc.version:
        raise VersionConflict(details={"current_version": acc.version})
    before = snapshot(acc)
    fields = patch.model_fields_set
    if "name" in fields and patch.name is not None and patch.name.strip() != acc.name:
        await _check_name(session, user_id, patch.name, exclude_id=acc.id)
        acc.name = patch.name.strip()
    if "kind" in fields and patch.kind is not None:
        acc.kind = patch.kind
    if "aliases" in fields and patch.aliases is not None:
        acc.aliases = norm_aliases(patch.aliases)
    if "default_for_currencies" in fields and patch.default_for_currencies is not None:
        defaults = [c.upper() for c in patch.default_for_currencies]
        for c in defaults:
            if not acc.is_multi_asset and c != (acc.currency or ""):
                raise ValidationFailed(
                    f"Счёт в {acc.currency} не может быть основным для {c}",
                    details={"field": "default_for_currencies"},
                )
        await _check_defaults(session, user_id, defaults, exclude_id=acc.id)
        acc.default_for_currencies = defaults
    for f in ("color", "icon", "sort_order", "is_archived"):
        if f in fields and getattr(patch, f) is not None:
            setattr(acc, f, getattr(patch, f))
    if "color" in fields and patch.color is None:
        acc.color = None
    acc.version += 1
    try:
        await session.flush()
    except IntegrityError as e:  # concurrent rename to the same name
        raise AlreadyExists(f"Счёт «{acc.name}» уже есть") from e
    await write_audit(
        session, user_id=user_id, entity_type="account", entity_id=acc.id, operation="update",
        before=before, after=snapshot(acc), source=principal.source,
    )
    await publish(session, user_id, {"accounts"}, source=principal.source)
    return acc


async def account_in_use(session: AsyncSession, account_id: int) -> bool:
    used = await session.scalar(
        select(Transaction.id)
        .where(
            or_(Transaction.account_id == account_id, Transaction.counter_account_id == account_id)
        )
        .limit(1)
    )
    if used is not None:
        return True
    return (
        await session.scalar(select(MyAccount.id).where(MyAccount.account_id == account_id))
        is not None
    )


async def delete_account(session: AsyncSession, principal: Principal, account_id: int) -> None:
    """Soft delete if no transactions reference it, otherwise archive.

    Takes the per-user writer lock EXCLUSIVE first: a parallel create of an operation on this
    account (a writer, lock SHARED) either commits before — then the account is in use and is
    archived — or waits and then sees the account deleted (422). Without it the operation could
    land on a soft-deleted, invisible account."""
    await writer_lock(session, principal.user_id, exclusive=True)
    acc = check_not_debt(
        await get_account(session, principal.user_id, account_id, for_update=True)
    )
    before = snapshot(acc)
    if await account_in_use(session, acc.id):
        acc.is_archived = True
        op = "update"
    else:
        acc.deleted_at = now()
        op = "delete"
    acc.default_for_currencies = []
    acc.version += 1
    await session.flush()
    await write_audit(
        session, user_id=principal.user_id, entity_type="account", entity_id=acc.id,
        operation=op, before=before, after=snapshot(acc), source=principal.source,
    )
    await publish(session, principal.user_id, {"accounts"}, source=principal.source)


async def set_opening_balance(
    session: AsyncSession,
    principal: Principal,
    account_id: int,
    asset: str,
    amount: Decimal | None,
    opening_at: datetime | None = None,
    *,
    action: object | None = None,
) -> AccountAsset:
    """amount None → «не задан». Audit + (when ``action`` is given) an undo record."""
    from app.services.ledger.postings import lock_assets

    await writer_lock(session, principal.user_id)
    check_amount(amount, "amount")
    acc = check_not_debt(await get_account(session, principal.user_id, account_id))
    aa = await ensure_asset(session, acc, asset)
    await lock_assets(session, {(acc.id, aa.asset)}, "update")
    await session.refresh(aa)
    before = snapshot(aa)
    if opening_at is not None and opening_at.tzinfo is None:
        raise ValidationFailed("opening_at должен содержать часовой пояс")
    if opening_at is not None:
        tz = await user_tz(session, principal.user_id)
        try:
            future = local_date_of(opening_at, tz) > local_today(tz) + timedelta(days=1)
        except OverflowError as e:
            raise ValidationFailed("Дата вне допустимого диапазона",
                                   details={"field": "opening_at"}) from e
        if future:
            # a balance «as of» a future moment freezes it: every operation until then is
            # before the opening and would be ignored (reconciliation would lie)
            raise ValidationFailed("Дата остатка в будущем", details={"field": "opening_at"})
    aa.opening_balance = amount
    aa.opening_at = (opening_at or now()) if amount is not None else None
    await session.flush()
    await write_audit(
        session, user_id=principal.user_id, entity_type="account_asset", entity_id=aa.id,
        operation="update", before=before, after=snapshot(aa), source=principal.source,
        action_id=getattr(action, "id", None),
    )
    if action is not None:
        action.record("account_asset", aa.id, before, None)  # type: ignore[attr-defined]
        action.changed.add("accounts")  # type: ignore[attr-defined]
    await publish(session, principal.user_id, {"accounts"}, source=principal.source,
                  action_id=getattr(action, "id", None))
    return aa


async def list_my_accounts(session: AsyncSession, user_id: int) -> list[MyAccount]:
    return list(
        await session.scalars(
            select(MyAccount).where(MyAccount.user_id == user_id).order_by(MyAccount.id)
        )
    )


async def my_account_by_handle(
    session: AsyncSession, user_id: int, handle: str | None
) -> MyAccount | None:
    """Own @handle → MyAccount (transfers to it are internal transfers, not expenses)."""
    h = strip_handle(handle)
    if not h:
        return None
    return await session.scalar(
        select(MyAccount).where(MyAccount.user_id == user_id, MyAccount.handle == h)
    )


async def add_my_account(
    session: AsyncSession, principal: Principal, data: MyAccountCreate
) -> MyAccount:
    """Handle stored without '@'. account_id None → create multi-asset wallet named '@handle'."""
    user_id = principal.user_id
    handle = strip_handle(data.handle)
    if not handle:
        raise ValidationFailed("Укажите @ник", details={"field": "handle"})
    if await my_account_by_handle(session, user_id, handle) is not None:
        raise AlreadyExists(f"@{handle} уже в «моих аккаунтах»")
    if data.account_id is not None:
        acc = check_not_debt(await get_account(session, user_id, data.account_id))
    else:
        name = f"@{handle}"
        acc = await session.scalar(
            select(Account).where(
                Account.user_id == user_id,
                func.lower(Account.name) == name.lower(),
                Account.deleted_at.is_(None),
            )
        )
        if acc is None:
            acc = await create_account(
                session,
                principal,
                AccountCreate(
                    name=name, kind="crypto_wallet", is_multi_asset=True,
                    aliases=[name.lower(), handle.lower()], icon="Bot",
                ),
            )
    ma = MyAccount(user_id=user_id, handle=handle, label=data.label or f"@{handle}",
                   account_id=acc.id)
    await session.flush()  # pending changes stay outside the savepoint
    try:
        async with session.begin_nested():
            session.add(ma)
            await session.flush()
    except IntegrityError as e:  # unique violation from a concurrent request
        raise AlreadyExists(f"@{handle} уже в «моих аккаунтах»") from e
    await write_audit(
        session, user_id=user_id, entity_type="my_account", entity_id=ma.id, operation="create",
        before=None, after=snapshot(ma), source=principal.source,
    )
    await publish(session, user_id, {"accounts"}, source=principal.source)
    return ma


async def delete_my_account(
    session: AsyncSession, principal: Principal, my_account_id: int
) -> None:
    """Remove @handle from «мои аккаунты». The handle must stop resolving to the wallet (a
    transfer to it is an expense again): the wallet loses its @handle aliases; the
    wallet created for this handle is deleted when unused, otherwise archived (history and
    balance stay; it can be restored in «Счета»)."""
    uid = principal.user_id
    ma = await session.get(MyAccount, my_account_id)
    if ma is None or ma.user_id != uid:
        raise NotFound("Аккаунт не найден")
    before = snapshot(ma)
    handle = strip_handle(ma.handle) or ""
    account_id = ma.account_id
    await session.delete(ma)
    await session.flush()
    await write_audit(
        session, user_id=uid, entity_type="my_account", entity_id=my_account_id,
        operation="delete", before=before, after=None, source=principal.source,
    )
    acc = await session.scalar(
        select(Account)
        .where(Account.id == account_id, Account.user_id == uid, Account.deleted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    still_linked = await session.scalar(
        select(MyAccount.id).where(MyAccount.account_id == account_id).limit(1)
    )
    if acc is not None and still_linked is None:
        acc_before = snapshot(acc)
        h = handle.lower()
        acc.aliases = [a for a in (acc.aliases or []) if a.lstrip("@").lower() != h]
        if acc.name.lstrip("@").lower() == h and acc.name.startswith("@"):
            # the wallet was created for this handle
            if await account_in_use(session, acc.id):
                acc.is_archived = True
            else:
                acc.deleted_at = now()
            acc.default_for_currencies = []
        if snapshot(acc) != acc_before:
            acc.version += 1
            await session.flush()
            await write_audit(
                session, user_id=uid, entity_type="account", entity_id=acc.id,
                operation="delete" if acc.deleted_at is not None else "update",
                before=acc_before, after=snapshot(acc), source=principal.source,
            )
    await publish(session, uid, {"accounts"}, source=principal.source)


__all__ = [
    "DEBT_ACCOUNT_MESSAGE",
    "AlreadyExists",
    "AppError",
    "check_not_debt",
    "is_debt_account",
    "add_my_account",
    "create_account",
    "default_account_for",
    "delete_account",
    "delete_my_account",
    "ensure_asset",
    "get_account",
    "list_accounts",
    "list_assets",
    "list_my_accounts",
    "my_account_by_handle",
    "resolve_account",
    "set_opening_balance",
    "supports_asset",
    "update_account",
]

"""Balances = opening_balance + Σ postings since opening_at. ARCH §4.9.

A balance is shown only when the opening balance of that asset is known; otherwise it is
``None`` («остаток не задан») — never a made-up 0."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import local_today
from app.models import Account, AccountAsset, Posting
from app.models.enums import AccountKind
from app.schemas.accounts import AccountOut, AssetBalanceOut, BalancesOut
from app.services.accounts import service as accounts
from app.services.ledger.common import approx, user_tz


async def asset_balance(session: AsyncSession, aa: AccountAsset) -> Decimal | None:
    """Current balance of one (account, asset); None when the opening balance is unknown."""
    if aa.opening_balance is None:
        return None
    stmt = select(func.coalesce(func.sum(Posting.amount), 0)).where(
        Posting.account_id == aa.account_id, Posting.asset == aa.asset
    )
    if aa.opening_at is not None:
        stmt = stmt.where(Posting.occurred_at >= aa.opening_at)
    moved = await session.scalar(stmt)
    return aa.opening_balance + Decimal(moved or 0)


async def _balances_map(
    session: AsyncSession, assets: list[AccountAsset]
) -> dict[int, Decimal | None]:
    """One aggregate query for many assets."""
    known = [a for a in assets if a.opening_balance is not None]
    out: dict[int, Decimal | None] = {a.id: None for a in assets}
    if not known:
        return out
    conds = []
    for a in known:
        c = and_(Posting.account_id == a.account_id, Posting.asset == a.asset)
        if a.opening_at is not None:
            c = and_(c, Posting.occurred_at >= a.opening_at)
        conds.append(c)
    rows = (
        await session.execute(
            select(Posting.account_id, Posting.asset, func.sum(Posting.amount))
            .where(or_(*conds))
            .group_by(Posting.account_id, Posting.asset)
        )
    ).all()
    sums = {(r[0], r[1]): Decimal(r[2]) for r in rows}
    for a in known:
        assert a.opening_balance is not None
        out[a.id] = a.opening_balance + sums.get((a.account_id, a.asset), Decimal(0))
    return out


async def accounts_out(
    session: AsyncSession, user_id: int, accs: list[Account]
) -> tuple[list[AccountOut], Decimal | None, Decimal | None, int]:
    assets = await accounts.list_assets(session, [a.id for a in accs])
    bal = await _balances_map(session, assets)
    today = local_today(await user_tz(session, user_id))
    by_acc: dict[int, list[AssetBalanceOut]] = {}
    total_vnd: Decimal | None = None
    total_usd: Decimal | None = None
    unknown = 0
    # debt service accounts (shown only with include_debts) never count in the totals: money
    # lent is not on any of my accounts (docs/DEBTS.md §2)
    debt_ids = {a.id for a in accs if a.kind == AccountKind.debt}
    for aa in assets:
        b = bal.get(aa.id)
        a_usd = await approx(session, b, aa.asset, "USD", today) if b is not None else None
        a_vnd = await approx(session, b, aa.asset, "VND", today) if b is not None else None
        if aa.account_id in debt_ids:
            pass
        elif b is None:
            unknown += 1
        if a_vnd is not None and aa.account_id not in debt_ids:
            total_vnd = (total_vnd or Decimal(0)) + a_vnd
        if a_usd is not None and aa.account_id not in debt_ids:
            total_usd = (total_usd or Decimal(0)) + a_usd
        by_acc.setdefault(aa.account_id, []).append(
            AssetBalanceOut(
                asset=aa.asset,
                opening_balance=aa.opening_balance,
                opening_at=aa.opening_at,
                balance=b,
                approx_usd=a_usd,
                approx_vnd=a_vnd,
            )
        )
    outs = [
        AccountOut.model_validate(
            {
                "id": a.id, "name": a.name, "kind": a.kind, "currency": a.currency,
                "is_multi_asset": a.is_multi_asset, "aliases": list(a.aliases),
                "default_for_currencies": list(a.default_for_currencies), "color": a.color,
                "icon": a.icon, "sort_order": a.sort_order, "is_archived": a.is_archived,
                "version": a.version, "assets": by_acc.get(a.id, []),
            }
        )
        for a in accs
    ]
    return outs, total_vnd, total_usd, unknown


async def get_balances(
    session: AsyncSession, user_id: int, *, include_debts: bool = False
) -> BalancesOut:
    """All non-archived accounts with per-asset balances (None if opening unknown) and
    display-only ≈USD/≈VND via current rates. Debt service accounts only with
    ``include_debts`` (and never in the totals)."""
    accs = await accounts.list_accounts(session, user_id, include_debts=include_debts)
    outs, total_vnd, total_usd, unknown = await accounts_out(session, user_id, accs)
    return BalancesOut(
        accounts=outs, total_vnd=total_vnd, total_usd=total_usd, unknown_count=unknown
    )


async def list_accounts_out(
    session: AsyncSession,
    user_id: int,
    *,
    include_archived: bool = False,
    include_debts: bool = False,
) -> list[AccountOut]:
    accs = await accounts.list_accounts(
        session, user_id, include_archived=include_archived, include_debts=include_debts
    )
    return (await accounts_out(session, user_id, accs))[0]


async def account_out(session: AsyncSession, user_id: int, account_id: int) -> AccountOut:
    acc = await accounts.get_account(session, user_id, account_id)
    return (await accounts_out(session, user_id, [acc]))[0][0]

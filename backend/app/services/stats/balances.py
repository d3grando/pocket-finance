"""Per-asset balances (docs/ARCH.md §4.9) with ≈$/≈₫ for display.

* A balance exists only where ``account_assets.opening_balance`` is set:
  ``balance = opening_balance + Σ postings.amount WHERE occurred_at >= opening_at``.
  Otherwise ``balance = None`` («остаток не задан») — never a made-up 0.
* Crypto is shown in coins; ≈$ uses the *current* price. Price moves are not income/expense:
  nothing is revalued or written, the approximation is display only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import quantize_asset
from app.core.timeutil import DEFAULT_TZ, tz, utcnow
from app.models import Account, AccountAsset, Posting, UserSettings
from app.models.enums import AccountKind
from app.services.rates.service import get_rate


@dataclass
class AssetBalance:
    account_id: int
    account_name: str
    account_icon: str | None
    asset: str
    opening_balance: Decimal | None
    opening_at: datetime | None
    balance: Decimal | None
    approx_usd: Decimal | None
    approx_vnd: Decimal | None


async def approx(
    session: AsyncSession, amount: Decimal, asset: str, to: str, on: date
) -> Decimal | None:
    """Display-only conversion with the latest known rate (no provider call)."""
    q = await get_rate(session, asset, to, on, kind="sell", fetch_missing=False)
    if q is None:
        return None
    return quantize_asset(amount * q.rate, to)


async def posting_sums(session: AsyncSession, user_id: int) -> dict[tuple[int, str], Decimal]:
    """Σ postings per (account, asset) since the opening moment, only for assets with a set
    opening balance."""
    stmt = (
        select(Posting.account_id, Posting.asset, func.coalesce(func.sum(Posting.amount), 0))
        .join(
            AccountAsset,
            and_(
                AccountAsset.account_id == Posting.account_id, AccountAsset.asset == Posting.asset
            ),
        )
        .where(
            Posting.user_id == user_id,
            AccountAsset.opening_balance.is_not(None),
            or_(AccountAsset.opening_at.is_(None), Posting.occurred_at >= AccountAsset.opening_at),
        )
        .group_by(Posting.account_id, Posting.asset)
    )
    return {(a, s): Decimal(v) for a, s, v in (await session.execute(stmt)).all()}


async def asset_balances(
    session: AsyncSession,
    user_id: int,
    *,
    include_archived: bool = False,
    with_approx: bool = True,
    now: datetime | None = None,
) -> list[AssetBalance]:
    st = await session.get(UserSettings, user_id)
    today = (now or utcnow()).astimezone(tz((st.timezone if st else None) or DEFAULT_TZ)).date()
    # debt service accounts are not money I hold (docs/DEBTS.md §2): never in «остатки»
    q = select(Account).where(
        Account.user_id == user_id,
        Account.deleted_at.is_(None),
        Account.kind != AccountKind.debt.value,
    )
    if not include_archived:
        q = q.where(Account.is_archived.is_(False))
    accounts = (await session.scalars(q.order_by(Account.sort_order, Account.id))).all()
    sums = await posting_sums(session, user_id)
    out: list[AssetBalance] = []
    for acc in accounts:
        assets = sorted(acc.assets, key=lambda a: (a.asset != (acc.currency or ""), a.asset))
        for aa in assets:
            bal = None
            if aa.opening_balance is not None:
                bal = Decimal(aa.opening_balance) + sums.get((acc.id, aa.asset), Decimal(0))
            usd = vnd = None
            if with_approx and bal is not None:
                usd = await approx(session, bal, aa.asset, "USD", today)
                vnd = await approx(session, bal, aa.asset, "VND", today)
            out.append(
                AssetBalance(
                    account_id=acc.id,
                    account_name=acc.name,
                    account_icon=acc.icon,
                    asset=aa.asset,
                    opening_balance=aa.opening_balance,
                    opening_at=aa.opening_at,
                    balance=bal,
                    approx_usd=usd,
                    approx_vnd=vnd,
                )
            )
    return out

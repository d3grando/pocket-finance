"""Default accounts of a new user (neutral starter set, the user renames/adds their own).

Карта (bank, VND, default for VND), Наличные (cash, VND), Кошелёк (multi-asset crypto wallet),
Биржа (multi-asset exchange). Icons per DESIGN.md §4.3. Installation-specific brand aliases
belong in ``PARSER_LEXICON_EXTRA`` (parser/lexicon.py), not here.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, AccountAsset

# (name, kind, currency|None (multi-asset), default_for, icon, aliases)
DEFAULT_ACCOUNTS: list[tuple[str, str, str | None, list[str], str, list[str]]] = [
    ("Карта", "bank", "VND", ["VND"], "Landmark", ["карта", "банк", "card"]),
    (
        "Наличные", "cash", "VND", [], "Wallet",
        ["нал", "налом", "наличные", "наличка", "кэш", "cash"],
    ),
    (
        "Кошелёк", "crypto_wallet", None, [], "Wallet",
        ["кошелёк", "кошелек", "кошелька", "крипто", "крипта", "wallet"],
    ),
    ("Биржа", "exchange", None, [], "ChartCandlestick", ["биржа", "exchange"]),
]


async def seed_default_accounts(session: AsyncSession, user_id: int) -> None:
    """Idempotent: creates only missing accounts (by name, deleted ones are not resurrected)."""
    existing = {
        n.lower()
        for n in await session.scalars(
            select(func.lower(Account.name)).where(Account.user_id == user_id)
        )
    }
    for i, (name, kind, currency, defaults, icon, aliases) in enumerate(DEFAULT_ACCOUNTS, 1):
        if name.lower() in existing:
            continue
        acc = Account(
            user_id=user_id,
            name=name,
            kind=kind,
            currency=currency,
            is_multi_asset=currency is None,
            aliases=aliases,
            default_for_currencies=defaults,
            icon=icon,
            sort_order=i,
            version=1,
        )
        session.add(acc)
        await session.flush()
        if currency:
            session.add(AccountAsset(user_id=user_id, account_id=acc.id, asset=currency))
    await session.flush()

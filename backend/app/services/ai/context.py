"""Builds ParseContext from DB.

Only what the parser/LLM may know: settings, account/category names+aliases, merchant rules,
own @handles, known assets, debt counterparties (names + open currencies, docs/DEBTS.md §6) and
the dialogue context (last 10 actions within 30 minutes, shared by all clients). NEVER balances,
debt amounts or transaction history. Service accounts of debts (kind='debt') are not accounts the
user can pick: they are left out (DEBTS.md §2).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.principal import Principal
from app.core.timeutil import local_now
from app.models import Account, AccountAsset, Category, Merchant, MyAccount, UserSettings
from app.schemas.intents import CtxAccount, CtxCategory, CtxMerchant, ParseContext
from app.services.ai import compat

MERCHANTS_LIMIT = 200
#: Caps of what goes into the parser index / LLM prompt — the same as the API limits, so data
#: written before the limits existed (thousands of aliases) cannot make one message slow.
ACCOUNTS_LIMIT = 100
CATEGORIES_LIMIT = 300
ALIASES_LIMIT = 50


def _aliases(values: list[str] | None) -> list[str]:
    return [x.lower() for x in (values or [])[:ALIASES_LIMIT]]


class AIContext(ParseContext):
    """ParseContext + server-side extras that never go to the LLM as ids/balances."""

    user_id: int = 0
    source: str = "web"
    summary_currency: str = "VND"
    #: own handle (lowercase, without @) → wallet account id (my_accounts)
    my_account_ids: dict[str, int] = {}


async def build_parse_context(
    session: AsyncSession, principal: Principal, *, edit_transaction_id: int | None = None
) -> AIContext:
    """settings (tz, default currency, small thousands), accounts+aliases+assets, categories,
    merchants (top 200 by hits), my handles, known assets, recent_actions (10 / 30 min)."""
    uid = principal.user_id
    st = await session.get(UserSettings, uid)
    tz_name = (st.timezone if st else None) or get_settings().default_timezone
    now = local_now(tz_name)

    accounts = (
        await session.scalars(
            select(Account)
            .where(
                Account.user_id == uid,
                Account.deleted_at.is_(None),
                Account.is_archived.is_(False),
                Account.kind != "debt",
            )
            .order_by(Account.sort_order, Account.id)
            .limit(ACCOUNTS_LIMIT)
        )
    ).all()
    assets_rows = (
        await session.execute(
            select(AccountAsset.account_id, AccountAsset.asset).where(AccountAsset.user_id == uid)
        )
    ).all()
    assets_by_acc: dict[int, list[str]] = {}
    for acc_id, asset in assets_rows:
        assets_by_acc.setdefault(acc_id, []).append(asset.upper())

    ctx_accounts = [
        CtxAccount(
            id=a.id,
            name=a.name,
            aliases=_aliases(a.aliases),
            kind=a.kind,
            currency=a.currency,
            is_multi_asset=a.is_multi_asset,
            default_for_currencies=[c.upper() for c in (a.default_for_currencies or [])],
            assets=sorted(
                set(assets_by_acc.get(a.id, [])) | ({a.currency} if a.currency else set())
            ),
        )
        for a in accounts
    ]

    cats = (
        await session.scalars(
            select(Category)
            .where(
                Category.user_id == uid,
                Category.merged_into_id.is_(None),
                Category.is_archived.is_(False),
            )
            .order_by(Category.kind, Category.sort_order, Category.id)
            .limit(CATEGORIES_LIMIT)
        )
    ).all()
    ctx_categories = [
        CtxCategory(
            id=c.id,
            name=c.name,
            kind=c.kind,  # type: ignore[arg-type]
            aliases=_aliases(c.aliases),
            is_business=c.is_business,
        )
        for c in cats
    ]

    merchants = (
        await session.scalars(
            select(Merchant)
            .where(Merchant.user_id == uid)
            .order_by(Merchant.hits.desc(), Merchant.id)
            .limit(MERCHANTS_LIMIT)
        )
    ).all()
    ctx_merchants = [
        CtxMerchant(
            id=m.id,
            name=m.name,
            aliases=_aliases(m.aliases),
            handle=(m.handle or None),
            category_id=m.category_id,
            default_account_id=m.default_account_id,
        )
        for m in merchants
    ]

    mine = (await session.scalars(select(MyAccount).where(MyAccount.user_id == uid))).all()
    my_ids = {m.handle.lower().lstrip("@"): m.account_id for m in mine}

    known_assets = sorted(
        {a.upper() for _, a in assets_rows}
        | {a.currency.upper() for a in accounts if a.currency}
        | {"VND", "USD", "RUB", "USDT"}
    )
    recent = await compat.recent_actions(session, uid)
    debts = await compat.context_debts(session, uid)

    return AIContext(
        now=now,
        today=now.date(),
        timezone=tz_name,
        default_currency=(st.default_currency if st else "VND") or "VND",
        small_amount_thousands=st.small_amount_thousands if st else True,
        accounts=ctx_accounts,
        categories=ctx_categories,
        merchants=ctx_merchants,
        my_handles=sorted(my_ids),
        debts=debts,
        known_assets=known_assets,
        recent_actions=recent,
        edit_transaction_id=edit_transaction_id,
        user_id=uid,
        source=principal.source,
        summary_currency=(st.summary_currency if st else "VND") or "VND",
        my_account_ids=my_ids,
    )

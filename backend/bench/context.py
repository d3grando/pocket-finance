"""Fixed context for the model benchmark (same names/aliases as the production seeds).

Frozen "today" = 2026-10-27 (Tuesday) so expected dates in the dataset are stable: the dataset
stores day offsets (``date: -1`` = yesterday). No balances, no history — like production.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.schemas.intents import (
    ActionBrief,
    CtxAccount,
    CtxCategory,
    CtxDebt,
    CtxDebtOpen,
    CtxMerchant,
)
from app.services.ai.context import AIContext
from app.services.categories.seed import DEFAULT_CATEGORIES

TZ = "Asia/Ho_Chi_Minh"
TODAY = date(2026, 10, 27)
NOW = datetime(2026, 10, 27, 14, 30, tzinfo=ZoneInfo(TZ))

ACCOUNTS = [
    CtxAccount(id=1, name="Карта", kind="bank", currency="VND",
               aliases=["карта", "банк"], default_for_currencies=["VND"],
               assets=["VND"]),
    CtxAccount(id=2, name="Наличные", kind="cash", currency="VND",
               aliases=["нал", "налом", "наличные", "наличка", "кэш", "cash"], assets=["VND"]),
    CtxAccount(id=3, name="Кошелёк", kind="crypto_wallet", is_multi_asset=True,
               aliases=["кошелёк", "кошелька", "крипто"], assets=["USDT", "TON"]),
    CtxAccount(id=4, name="Копилка", kind="crypto_wallet", is_multi_asset=True,
               aliases=["копилка"], assets=["USDT"]),
    CtxAccount(id=5, name="Биржа", kind="exchange", is_multi_asset=True,
               aliases=["биржа", "exchange"], assets=["USDT", "BTC"]),
    CtxAccount(id=6, name="Сейф", kind="crypto_wallet", is_multi_asset=True,
               aliases=["сейф", "safe"], assets=["BTC", "ETH"]),
    CtxAccount(id=7, name="Карта RUB", kind="card", currency="RUB",
               aliases=["карта руб", "рублёвая карта"], default_for_currencies=["RUB"],
               assets=["RUB"]),
    CtxAccount(id=8, name="@myshop", kind="crypto_wallet", is_multi_asset=True,
               aliases=["myshop"], assets=[]),
]

CATEGORIES = [
    CtxCategory(id=i, name=name, kind=kind, is_business=biz, aliases=aliases)  # type: ignore[arg-type]
    for i, (name, kind, biz, _icon, aliases) in enumerate(DEFAULT_CATEGORIES, 1)
]


def _cat(name: str, kind: str = "expense") -> int:
    return next(c.id for c in CATEGORIES if c.name == name and c.kind == kind)


MERCHANTS = [
    CtxMerchant(id=1, name="vast.ai", aliases=["васт", "vast"], category_id=_cat("Серверы/инфра")),
    CtxMerchant(id=2, name="Highlands", aliases=["highlands", "хайлендс"],
                category_id=_cat("Кофе")),
    CtxMerchant(id=3, name="Hetzner", aliases=["hetzner", "хетцнер"],
                category_id=_cat("Серверы/инфра")),
    CtxMerchant(id=4, name="Grab", aliases=["grab", "грэб"], category_id=_cat("Такси/транспорт")),
]

RECENT = [
    ActionBrief(action_id=uuid.UUID(int=2), kind="create_transaction",
                summary="200 000 ₫ · Продукты", source="telegram",
                created_at=NOW - timedelta(minutes=3), transaction_ids=[102], tx_kind="expense",
                amount=Decimal(200000), currency="VND", category="Продукты", account="Карта"),
    ActionBrief(action_id=uuid.UUID(int=1), kind="create_transaction",
                summary="45 000 ₫ · Кофе", source="desktop",
                created_at=NOW - timedelta(minutes=12), transaction_ids=[101], tx_kind="expense",
                amount=Decimal(45000), currency="VND", category="Кофе", account="Наличные"),
]


#: debt counterparties (docs/DEBTS.md §6): names + open currencies, never amounts
DEBTS = [
    CtxDebt(id=1, name="Коля", name_key="кол", aliases=["коле", "у коли"],
            open=[CtxDebtOpen(currency="VND", direction="owed_to_me")]),
    CtxDebt(id=2, name="Петя", name_key="пет", aliases=["пете"],
            open=[CtxDebtOpen(currency="VND", direction="i_owe")]),
    CtxDebt(id=3, name="Вася", name_key="вас", handle="vasya",
            open=[CtxDebtOpen(currency="USDT", direction="owed_to_me")]),
]


def bench_context() -> AIContext:
    return AIContext(
        now=NOW, today=TODAY, timezone=TZ, default_currency="VND", small_amount_thousands=True,
        accounts=ACCOUNTS, categories=CATEGORIES, merchants=MERCHANTS,
        my_handles=["myshop"], debts=DEBTS,
        known_assets=["VND", "USD", "RUB", "USDT", "TON", "BTC", "ETH"],
        recent_actions=RECENT, user_id=0, source="web", my_account_ids={"myshop": 8},
    )

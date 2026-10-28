"""Parse contexts for parser tests (static copies of the default seeds, ids are fixed)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.schemas.intents import ActionBrief, CtxAccount, CtxCategory, CtxMerchant, ParseContext

TODAY = date(2026, 10, 27)  # Tuesday
NOW = datetime(2026, 10, 27, 3, 0, tzinfo=UTC)  # 10:00 Asia/Ho_Chi_Minh

ACCOUNTS = [
    CtxAccount(id=1, name="Карта", kind="bank", currency="VND",
               aliases=["карта", "банк"], default_for_currencies=["VND"],
               assets=["VND"]),
    CtxAccount(id=2, name="Наличные", kind="cash", currency="VND",
               aliases=["нал", "налом", "наличные", "наличка", "кэш", "cash"], assets=["VND"]),
    CtxAccount(id=3, name="Кошелёк", kind="crypto_wallet", is_multi_asset=True,
               aliases=["кошелёк", "кошелька", "крипто"], assets=["USDT", "TON"]),
    CtxAccount(id=4, name="Копилка", kind="crypto_wallet", is_multi_asset=True,
               aliases=["копилка"], assets=["TON"]),
    CtxAccount(id=5, name="Биржа", kind="exchange", is_multi_asset=True,
               aliases=["биржа", "exchange"], assets=["USDT", "BTC"]),
    CtxAccount(id=6, name="Сейф", kind="crypto_wallet", is_multi_asset=True,
               aliases=["сейф", "safe"], assets=["USDT", "ETH"]),
    CtxAccount(id=7, name="Карта RUB", kind="card", currency="RUB",
               aliases=["карта руб", "рублёвая карта", "руб карта"],
               default_for_currencies=["RUB"], assets=["RUB"]),
]

_CATS = [
    ("Еда", "expense", False, ["еда", "обед", "ужин", "завтрак", "ресторан", "food"]),
    ("Продукты", "expense", False, ["продукты", "groceries", "супермаркет"]),
    ("Кофе", "expense", False, ["кофе", "coffee", "кофейня"]),
    ("Такси/транспорт", "expense", False, ["такси", "grab", "транспорт", "taxi"]),
    ("Жильё", "expense", False, ["жильё", "жилье", "аренда", "квартира", "rent"]),
    ("Связь", "expense", False, ["связь", "интернет", "мобильный"]),
    ("Подписки", "expense", False, ["подписка", "подписки", "subscription"]),
    ("Здоровье", "expense", False, ["здоровье", "аптека", "врач"]),
    ("Покупки", "expense", False, ["покупки", "шопинг", "одежда"]),
    ("Развлечения", "expense", False, ["развлечения", "кино", "бар"]),
    ("Подарки", "expense", False, ["подарок", "подарки"]),
    ("Переводы людям", "expense", False, ["перевод", "переводы людям"]),
    ("Прочее", "expense", False, ["прочее", "другое"]),
    ("Серверы/инфра", "expense", True, ["сервер", "серверы", "хостинг", "vps", "инфра"]),
    ("Софт и AI", "expense", True, ["софт", "ai", "нейросети", "api"]),
    ("Реклама", "expense", True, ["реклама", "ads", "продвижение"]),
    ("Подрядчики", "expense", True, ["подрядчик", "подрядчики", "фриланс"]),
    ("Комиссии/обмен", "expense", True, ["комиссия", "комиссии", "fee"]),
    ("Закупка товара", "expense", True, ["закупка", "для магазина", "на продажу"]),
    ("Клиенты", "income", True, ["клиент", "клиенты", "client"]),
    ("Продажи", "income", True, ["продажи", "продажа", "магазин"]),
    ("Зарплата", "income", False, ["зарплата", "зп", "salary"]),
    ("Прочее", "income", False, ["прочее", "другое"]),
]
CATEGORIES = [
    CtxCategory(id=i + 1, name=n, kind=k, is_business=b, aliases=a)
    for i, (n, k, b, a) in enumerate(_CATS)
]
CAT_ID = {(c.name, c.kind): c.id for c in CATEGORIES}

MERCHANTS = [
    CtxMerchant(id=1, name="vast.ai", aliases=["васт", "vast"],
                category_id=CAT_ID[("Серверы/инфра", "expense")], default_account_id=1),
    CtxMerchant(id=2, name="Highlands Coffee", aliases=["хайлендс", "highlands"],
                category_id=CAT_ID[("Кофе", "expense")]),
    CtxMerchant(id=3, name="@alice", handle="alice", aliases=[],
                category_id=CAT_ID[("Софт и AI", "expense")]),
    CtxMerchant(id=4, name="Шавуха у дома", aliases=["шавуха"], category_id=None),
]


def ctx(**kw) -> ParseContext:
    base = dict(now=NOW, today=TODAY, accounts=ACCOUNTS, categories=CATEGORIES,
                merchants=MERCHANTS, known_assets=["VND", "USD", "USDT", "TON", "BTC", "ETH",
                                                   "RUB"])
    base.update(kw)
    return ParseContext(**base)


def empty_ctx(**kw) -> ParseContext:
    base = dict(now=NOW, today=TODAY)
    base.update(kw)
    return ParseContext(**base)


def recent(kind: str = "expense", amount: str = "200000", currency: str = "VND",
           category: str = "Продукты", tx_id: int = 101, minutes: int = 2) -> ActionBrief:
    return ActionBrief(action_id=uuid.uuid4(), kind="create_transaction",
                       summary=f"{category} {amount}", source="web",
                       created_at=NOW - timedelta(minutes=minutes), transaction_ids=[tx_id],
                       tx_kind=kind, amount=Decimal(amount), currency=currency,
                       category=category, account="Карта")


CTX = ctx()
CTX_EMPTY = empty_ctx()
CTX_MINE = ctx(my_handles=["myshop", "@mywallet2"])
CTX_RECENT = ctx(recent_actions=[recent()])
CTX_RECENT_USD = ctx(recent_actions=[recent(amount="15", currency="USD", category="Подписки")])
CTX_EDIT = ctx(edit_transaction_id=101, recent_actions=[recent(tx_id=101)])
CTX_RUB = ctx(default_currency="RUB")
CTX_NO_SMALL = ctx(small_amount_thousands=False)
CTXS = {"seed": CTX, "empty": CTX_EMPTY, "mine": CTX_MINE, "recent": CTX_RECENT,
        "recent_usd": CTX_RECENT_USD, "edit": CTX_EDIT, "rub": CTX_RUB,
        "nosmall": CTX_NO_SMALL}

"""Default categories (ARCH §3.3) with aliases and lucide icons (DESIGN.md §4.2)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category

# (name, kind, is_business, icon, aliases)
DEFAULT_CATEGORIES: list[tuple[str, str, bool, str, list[str]]] = [
    ("Еда", "expense", False, "UtensilsCrossed",
     ["еда", "обед", "ужин", "завтрак", "ресторан", "кафе", "food"]),
    ("Продукты", "expense", False, "ShoppingBasket", ["продукты", "groceries", "супермаркет"]),
    ("Кофе", "expense", False, "Coffee", ["кофе", "coffee", "кофейня"]),
    ("Такси/транспорт", "expense", False, "CarTaxiFront",
     ["такси", "grab", "транспорт", "taxi", "бензин"]),
    ("Жильё", "expense", False, "Building2", ["жильё", "жилье", "аренда", "квартира", "rent"]),
    ("Связь", "expense", False, "Smartphone", ["связь", "интернет", "мобильный"]),
    ("Подписки", "expense", False, "RefreshCcw", ["подписка", "подписки", "subscription"]),
    ("Здоровье", "expense", False, "HeartPulse", ["здоровье", "аптека", "врач"]),
    ("Покупки", "expense", False, "ShoppingBag", ["покупки", "шопинг", "одежда"]),
    ("Развлечения", "expense", False, "Ticket", ["развлечения", "кино", "бар"]),
    ("Подарки", "expense", False, "Gift", ["подарок", "подарки"]),
    ("Переводы людям", "expense", False, "Send", ["перевод", "переводы людям"]),
    ("Прочее", "expense", False, "Circle", ["прочее", "другое"]),
    ("Серверы/инфра", "expense", True, "Server",
     ["сервер", "серверы", "хостинг", "vps", "инфра"]),
    ("Софт и AI", "expense", True, "Bot", ["софт", "ai", "нейросети", "api"]),
    ("Реклама", "expense", True, "Megaphone", ["реклама", "ads", "продвижение"]),
    ("Подрядчики", "expense", True, "Handshake", ["подрядчик", "подрядчики", "фриланс"]),
    ("Комиссии/обмен", "expense", True, "Percent", ["комиссия", "комиссии", "fee"]),
    ("Закупка товара", "expense", True, "PackageOpen",
     ["закупка", "для магазина", "на продажу"]),
    ("Клиенты", "income", True, "BriefcaseBusiness", ["клиент", "клиенты", "client"]),
    ("Продажи", "income", True, "Store", ["продажи", "продажа", "магазин"]),
    ("Зарплата", "income", False, "Banknote", ["зарплата", "зп", "salary"]),
    ("Прочее", "income", False, "Circle", ["прочее", "другое"]),
]


async def seed_defaults(session: AsyncSession, user_id: int) -> None:
    """Idempotent: creates only missing categories (is_system=True)."""
    rows = await session.execute(
        select(Category.kind, func.lower(Category.name)).where(Category.user_id == user_id)
    )
    existing = {(k, n) for k, n in rows}
    for i, (name, kind, is_business, icon, aliases) in enumerate(DEFAULT_CATEGORIES, 1):
        if (kind, name.lower()) in existing:
            continue
        session.add(
            Category(
                user_id=user_id,
                name=name,
                kind=kind,
                is_business=is_business,
                icon=icon,
                aliases=aliases,
                is_system=True,
                is_new=False,
                sort_order=i,
                version=1,
            )
        )
    await session.flush()

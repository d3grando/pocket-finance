"""Categories: CRUD, personal/business scope, 🆕 model-created, merge with
moving operations (undoable), similarity via pg_trgm."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import func, literal, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed, VersionConflict
from app.core.principal import Principal
from app.models import Category, Merchant, RecurringTransaction, Transaction
from app.schemas.categories import (
    CategoryCreate,
    CategoryOut,
    CategoryPatch,
    CategorySuggestion,
    CategorySuggestions,
)
from app.services.ledger.audit import snapshot, write_audit
from app.services.ledger.common import check_limit, norm, norm_aliases, publish, writer_lock

#: Categories per user (archived and merged ones included: they keep their rows).
MAX_CATEGORIES = 300

SIMILARITY_MIN = 0.45

# keyword → lucide icon for categories created by the model (DESIGN.md §4.2; default Tag)
ICON_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("еда", "ресторан", "обед", "ужин", "кафе", "food"), "UtensilsCrossed"),
    (("продукт", "супермаркет", "grocer"), "ShoppingBasket"),
    (("кофе", "coffee"), "Coffee"),
    (("такси", "транспорт", "бензин", "авто", "машин", "taxi", "grab"), "CarTaxiFront"),
    (("жиль", "аренд", "квартир", "rent"), "Building2"),
    (("связ", "интернет", "телефон", "мобил"), "Smartphone"),
    (("подписк", "subscri"), "RefreshCcw"),
    (("здоров", "аптек", "врач", "лекарств", "спорт", "фитнес"), "HeartPulse"),
    (("одежд", "покупк", "шопинг"), "ShoppingBag"),
    (("развлеч", "кино", "игр", "бар"), "Ticket"),
    (("подар",), "Gift"),
    (("сервер", "хостинг", "vps", "инфра", "домен"), "Server"),
    (("софт", "ai", "нейро", "api", "gpt", "claude"), "Bot"),
    (("реклам", "продвиж", "ads"), "Megaphone"),
    (("подрядч", "фриланс"), "Handshake"),
    (("комисс", "обмен", "fee"), "Percent"),
    (("закупк", "товар"), "PackageOpen"),
    (("образован", "курс", "книг", "учеб"), "GraduationCap"),
    (("путешеств", "отел", "билет", "авиа"), "Plane"),
    (("животн", "кот", "собак", "питом"), "PawPrint"),
    (("дет",), "Baby"),
    (("красот", "салон", "парикмах"), "Sparkles"),
    (("клиент",), "BriefcaseBusiness"),
    (("продаж", "магазин"), "Store"),
    (("зарплат", "зп", "salary"), "Banknote"),
]


def icon_for(name: str) -> str:
    n = norm(name)
    for keys, icon in ICON_KEYWORDS:
        if any(k in n for k in keys):
            return icon
    return "Tag"


@dataclass
class CategoryMatch:
    category: Category | None
    exact: bool = False
    candidates: list[tuple[Category, float]] = field(default_factory=list)


class AlreadyExists(Conflict):
    code, message = "already_exists", "Уже существует"


def category_out(c: Category, tx_count: int | None = None) -> CategoryOut:
    return CategoryOut(
        id=c.id, name=c.name, kind=c.kind,  # type: ignore[arg-type]
        is_business=c.is_business, icon=c.icon, color=c.color, aliases=list(c.aliases),
        is_system=c.is_system, is_new=c.is_new, is_archived=c.is_archived,
        sort_order=c.sort_order, version=c.version, tx_count=tx_count,
    )


#: «запасные» categories: chosen when nothing else fits — never learned automatically into a
#: merchant/@handle rule (only an explicit user choice is remembered).
FALLBACK_CATEGORY_NAMES = frozenset({"Переводы людям", "Прочее"})


def is_fallback(c: Category | None) -> bool:
    return c is not None and c.is_system and c.name in FALLBACK_CATEGORY_NAMES


async def list_categories(
    session: AsyncSession,
    user_id: int,
    *,
    kind: Literal["expense", "income"] | None = None,
    include_archived: bool = False,
) -> list[Category]:
    stmt = select(Category).where(Category.user_id == user_id, Category.merged_into_id.is_(None))
    if kind:
        stmt = stmt.where(Category.kind == kind)
    if not include_archived:
        stmt = stmt.where(Category.is_archived.is_(False))
    return list(await session.scalars(stmt.order_by(Category.kind, Category.sort_order,
                                                    Category.id)))


async def tx_counts(session: AsyncSession, user_id: int) -> dict[int, int]:
    rows = await session.execute(
        select(Transaction.category_id, func.count())
        .where(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            Transaction.category_id.isnot(None),
        )
        .group_by(Transaction.category_id)
    )
    return {int(c): int(n) for c, n in rows}


async def get_category(
    session: AsyncSession, user_id: int, category_id: int, *, for_update: bool = False
) -> Category:
    stmt = select(Category).where(Category.id == category_id, Category.user_id == user_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    c = await session.scalar(stmt)
    if c is None:
        raise NotFound("Категория не найдена", details={"category_id": category_id})
    return c


async def effective_category(session: AsyncSession, user_id: int, category_id: int) -> Category:
    """Category by id, following merged_into_id to the live target."""
    c = await get_category(session, user_id, category_id)
    hops = 0
    while c.merged_into_id is not None and hops < 10:
        c = await get_category(session, user_id, c.merged_into_id)
        hops += 1
    return c


async def similar(
    session: AsyncSession,
    user_id: int,
    text_: str,
    kind: Literal["expense", "income"] | None,
    *,
    min_score: float = SIMILARITY_MIN,
    limit: int = 5,
) -> list[tuple[Category, float]]:
    """pg_trgm similarity of ``text_`` against name and aliases (best of them)."""
    key = norm(text_)
    if not key:
        return []
    alias = func.unnest(Category.aliases).column_valued("a")
    alias_best = (
        select(func.max(func.similarity(alias, literal(key))))
        .correlate(Category)
        .scalar_subquery()
    )
    score = func.greatest(
        func.similarity(func.lower(Category.name), literal(key)),
        func.coalesce(alias_best, 0),
    ).label("score")
    stmt = select(Category, score).where(
        Category.user_id == user_id,
        Category.merged_into_id.is_(None),
        Category.is_archived.is_(False),
    )
    if kind:
        stmt = stmt.where(Category.kind == kind)
    stmt = stmt.order_by(score.desc(), Category.sort_order).limit(limit)
    out = []
    for c, s in (await session.execute(stmt)).all():
        if float(s) >= min_score:
            out.append((c, round(float(s), 3)))
    return out


async def resolve_category(
    session: AsyncSession, user_id: int, ref: str, kind: Literal["expense", "income"]
) -> CategoryMatch:
    """Exact name/alias (case-insensitive, ё=е; merged → target) → exact match. Otherwise the
    most similar (pg_trgm ≥ 0.45) becomes ``category`` with exact=False and all similar ones are
    ``candidates``; nothing similar → category None (caller may create a 🆕 one)."""
    key = norm(ref)
    if not key:
        return CategoryMatch(None)
    cats = list(
        await session.scalars(
            select(Category).where(Category.user_id == user_id, Category.kind == kind)
        )
    )
    exact = [c for c in cats if norm(c.name) == key or key in {norm(a) for a in c.aliases}]
    exact.sort(key=lambda c: (c.merged_into_id is not None, c.is_archived, c.sort_order))
    if exact:
        c = exact[0]
        if c.merged_into_id is not None:
            c = await effective_category(session, user_id, c.id)
        return CategoryMatch(c, exact=True, candidates=[(c, 1.0)])
    cands = await similar(session, user_id, key, kind)
    return CategoryMatch(cands[0][0] if cands else None, exact=False, candidates=cands)


async def _check_name(
    session: AsyncSession, user_id: int, kind: str, name: str, exclude_id: int | None = None
) -> None:
    stmt = select(Category).where(
        Category.user_id == user_id,
        Category.kind == kind,
        func.lower(Category.name) == name.strip().lower(),
        Category.merged_into_id.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(Category.id != exclude_id)
    existing = await session.scalar(stmt)
    if existing is not None:
        raise AlreadyExists(
            f"Категория «{existing.name}» уже есть",
            details={"id": existing.id, "is_archived": existing.is_archived},
        )


async def create_category(
    session: AsyncSession,
    principal: Principal,
    data: CategoryCreate,
    *,
    is_new: bool = False,
    action: object | None = None,
) -> Category:
    """409 already_exists on duplicate name. Icon from keyword dictionary if not given
    (default 'Tag'). is_new=True when created by the model (🆕). With ``action`` the creation
    is undoable (action kind create_category)."""
    user_id = principal.user_id
    name = data.name.strip()
    await check_limit(
        session, select(func.count()).select_from(Category).where(Category.user_id == user_id),
        MAX_CATEGORIES, "категорий",
    )
    await _check_name(session, user_id, data.kind, name)
    max_sort = await session.scalar(
        select(func.coalesce(func.max(Category.sort_order), 0)).where(Category.user_id == user_id)
    )
    c = Category(
        user_id=user_id,
        name=name,
        kind=data.kind,
        is_business=data.is_business,
        icon=data.icon or icon_for(name),
        color=data.color,
        aliases=norm_aliases(data.aliases, name),
        is_system=False,
        is_new=is_new,
        sort_order=int(max_sort or 0) + 1,
        version=1,
    )
    await session.flush()  # pending changes stay outside the savepoint
    try:
        async with session.begin_nested():
            session.add(c)
            await session.flush()
    except IntegrityError as e:  # concurrent duplicate
        raise AlreadyExists(f"Категория «{name}» уже есть") from e
    aid = getattr(action, "id", None)
    await write_audit(
        session, user_id=user_id, entity_type="category", entity_id=c.id, operation="create",
        before=None, after=snapshot(c), source=principal.source, action_id=aid,
    )
    if action is not None:
        action.record("category", c.id, None, c.version)  # type: ignore[attr-defined]
        action.changed.add("categories")  # type: ignore[attr-defined]
    await publish(session, user_id, {"categories"}, source=principal.source, action_id=aid)
    return c


async def update_category(
    session: AsyncSession, principal: Principal, category_id: int, patch: CategoryPatch
) -> Category:
    """is_business change → update transactions.is_business of that category."""
    user_id = principal.user_id
    c = await get_category(session, user_id, category_id, for_update=True)
    if c.merged_into_id is not None:
        raise NotFound("Категория объединена с другой")
    if patch.version != c.version:
        raise VersionConflict(details={"current_version": c.version})
    before = snapshot(c)
    fields = patch.model_fields_set
    if "name" in fields and patch.name and patch.name.strip() != c.name:
        await _check_name(session, user_id, c.kind, patch.name, exclude_id=c.id)
        c.name = patch.name.strip()
    if "aliases" in fields and patch.aliases is not None:
        c.aliases = norm_aliases(patch.aliases, c.name)
    for f in ("icon", "color", "sort_order", "is_archived", "is_new"):
        if f in fields and getattr(patch, f) is not None:
            setattr(c, f, getattr(patch, f))
    if "color" in fields and patch.color is None:
        c.color = None
    if c.is_archived and c.is_system and c.name == "Прочее":
        raise ValidationFailed("Категорию «Прочее» нельзя убрать в архив")
    entities = {"categories"}
    if "is_business" in fields and patch.is_business is not None and (
        patch.is_business != c.is_business
    ):
        c.is_business = patch.is_business
        await session.execute(
            update(Transaction)
            .where(Transaction.user_id == user_id, Transaction.category_id == c.id)
            .values(is_business=patch.is_business, version=Transaction.version + 1)
            .execution_options(synchronize_session="fetch")
        )
        entities |= {"transactions"}
    c.version += 1
    await session.flush()
    await write_audit(
        session, user_id=user_id, entity_type="category", entity_id=c.id, operation="update",
        before=before, after=snapshot(c), source=principal.source,
    )
    await publish(session, user_id, entities, source=principal.source)
    return c


async def merge_categories(
    session: AsyncSession,
    principal: Principal,
    source_id: int,
    target_id: int,
    *,
    action: object | None = None,
) -> Category:
    """Move transactions/merchants/recurring to target, set source.merged_into_id, add source
    name to target aliases. Same kind only. Undoable (pass ``action`` of kind
    merge_categories; the REST route does)."""
    from app.models.enums import ActionKind
    from app.services.ledger.actions import begin_action, finish_action

    user_id = principal.user_id
    await writer_lock(session, user_id)
    if source_id == target_id:
        raise ValidationFailed("Нельзя объединить категорию саму с собой")
    first, second = sorted((source_id, target_id))
    a = await get_category(session, user_id, first, for_update=True)
    b = await get_category(session, user_id, second, for_update=True)
    src, dst = (a, b) if a.id == source_id else (b, a)
    if src.merged_into_id is not None or dst.merged_into_id is not None:
        raise ValidationFailed("Категория уже объединена с другой")
    if dst.is_archived:
        raise ValidationFailed(
            f"Категория «{dst.name}» в архиве — сначала верните её",
            details={"field": "target_id", "category_id": dst.id},
        )
    if src.kind != dst.kind:
        raise ValidationFailed("Нельзя объединить категории расходов и доходов")
    if src.is_system and src.name == "Прочее":
        raise ValidationFailed("Категорию «Прочее» нельзя влить в другую")
    own_action = action is None
    ctx = action or await begin_action(session, principal, ActionKind.merge_categories)
    aid = ctx.id  # type: ignore[attr-defined]

    txs = list(
        await session.scalars(
            select(Transaction)
            .where(Transaction.user_id == user_id, Transaction.category_id == src.id)
            .order_by(Transaction.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    for t in txs:
        before = snapshot(t)
        t.category_id = dst.id
        t.is_business = dst.is_business
        t.version += 1
        await session.flush()
        ctx.record("transaction", t.id, before, t.version)  # type: ignore[attr-defined]
        await write_audit(
            session, user_id=user_id, entity_type="transaction", entity_id=t.id,
            operation="update", before=before, after=snapshot(t), source=principal.source,
            action_id=aid,
        )
    for m in await session.scalars(
        select(Merchant).where(Merchant.user_id == user_id, Merchant.category_id == src.id)
    ):
        before = snapshot(m)
        m.category_id = dst.id
        ctx.record("merchant", m.id, before, None)  # type: ignore[attr-defined]
    for r in await session.scalars(
        select(RecurringTransaction).where(
            RecurringTransaction.user_id == user_id, RecurringTransaction.category_id == src.id
        )
    ):
        before = snapshot(r)
        r.category_id = dst.id
        r.version += 1
        ctx.record("recurring", r.id, before, r.version)  # type: ignore[attr-defined]
    src_before, dst_before = snapshot(src), snapshot(dst)
    src.merged_into_id = dst.id
    src.is_archived = True
    src.version += 1
    dst.aliases = norm_aliases(dst.aliases, *src.aliases, src.name)
    dst.version += 1
    await session.flush()
    ctx.record("category", src.id, src_before, src.version)  # type: ignore[attr-defined]
    ctx.record("category", dst.id, dst_before, dst.version)  # type: ignore[attr-defined]
    await write_audit(
        session, user_id=user_id, entity_type="category", entity_id=src.id, operation="merge",
        before=src_before, after=snapshot(src), source=principal.source, action_id=aid,
    )
    await write_audit(
        session, user_id=user_id, entity_type="category", entity_id=dst.id, operation="merge",
        before=dst_before, after=snapshot(dst), source=principal.source, action_id=aid,
    )
    ctx.changed |= {"categories", "transactions", "merchants", "recurring"}  # type: ignore
    if own_action:
        await finish_action(
            session, ctx, f"Слияние категорий: «{src.name}» → «{dst.name}»"  # type: ignore
        )
    await publish(
        session, user_id, {"categories", "transactions", "merchants", "recurring"},
        source=principal.source, action_id=aid,
    )
    return dst


async def archive_category(session: AsyncSession, principal: Principal, category_id: int) -> None:
    c = await get_category(session, principal.user_id, category_id, for_update=True)
    if c.is_system and c.name == "Прочее":
        raise ValidationFailed("Категорию «Прочее» нельзя удалить")
    if c.is_archived:
        return
    before = snapshot(c)
    c.is_archived = True
    c.version += 1
    await session.flush()
    await write_audit(
        session, user_id=principal.user_id, entity_type="category", entity_id=c.id,
        operation="delete", before=before, after=snapshot(c), source=principal.source,
    )
    await publish(session, principal.user_id, {"categories"}, source=principal.source)


async def suggest(
    session: AsyncSession, user_id: int, *, transaction_id: int | None, q: str | None
) -> CategorySuggestions:
    """«В другую»: similar to the transaction's category/merchant/description first."""
    kind: Literal["expense", "income"] | None = None
    texts: list[str] = []
    exclude: int | None = None
    if transaction_id is not None:
        tx = await session.get(Transaction, transaction_id)
        if tx is None or tx.user_id != user_id:
            raise NotFound("Операция не найдена")
        kind = "income" if tx.kind == "income" else "expense"
        exclude = tx.category_id
        if tx.description:
            texts.append(tx.description)
        if tx.merchant_id:
            m = await session.get(Merchant, tx.merchant_id)
            if m:
                texts.append(m.name)
        if tx.category_id:
            c = await session.get(Category, tx.category_id)
            if c:
                texts.append(c.name)
                if not q and tx.raw_text:
                    texts.append(tx.raw_text)
    if q:
        texts.insert(0, q)
    best: dict[int, tuple[Category, float]] = {}
    for t in texts:
        for c, s in await similar(session, user_id, t, kind, min_score=0.2, limit=8):
            if c.id == exclude:
                continue
            if c.id not in best or best[c.id][1] < s:
                best[c.id] = (c, s)
    sim = sorted(best.values(), key=lambda x: -x[1])[:5]
    all_ = await list_categories(session, user_id, kind=kind)
    return CategorySuggestions(
        similar=[CategorySuggestion(category=category_out(c), score=s) for c, s in sim],
        all=[category_out(c) for c in all_ if c.id != exclude],
    )

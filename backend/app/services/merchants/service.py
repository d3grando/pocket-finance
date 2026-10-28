"""Merchant rules: «васт» → vast.ai → Серверы/инфра; @alice → Софт и AI.

The latest manual choice wins: changing the category of a merchant's operation updates the rule.
"""

from __future__ import annotations

from sqlalchemy import any_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.principal import Principal
from app.models import Account, Category, Merchant
from app.schemas.merchants import MerchantCreate, MerchantOut, MerchantPatch
from app.services.ledger.audit import snapshot, write_audit
from app.services.ledger.common import (
    check_limit,
    like_contains,
    norm,
    norm_aliases,
    now,
    publish,
    strip_handle,
)

#: Merchant rules created by hand per user (learned ones are not capped: the AI context takes
#: only the top ``ai.context.MERCHANTS_LIMIT`` by hits anyway).
MAX_MERCHANTS = 2000


class AlreadyExists(Conflict):
    code, message = "already_exists", "Уже существует"


async def match_merchant(
    session: AsyncSession, user_id: int, *, name: str | None = None, handle: str | None = None
) -> Merchant | None:
    """By handle (exact, without @) or by name/alias (case-insensitive, ё=е)."""
    h = strip_handle(handle)
    if h:
        m = await session.scalar(
            select(Merchant).where(Merchant.user_id == user_id, Merchant.handle == h)
        )
        if m is not None:
            return m
    if name:
        n = name.strip()
        if n.startswith("@") and not h:
            return await match_merchant(session, user_id, handle=n)
        key = norm(n)
        if not key:
            return None
        lowered = n.lower()
        m = await session.scalar(
            select(Merchant)
            .where(
                Merchant.user_id == user_id,
                or_(
                    func.lower(Merchant.name) == lowered,
                    lowered == any_(Merchant.aliases),
                    func.replace(func.lower(Merchant.name), "ё", "е") == key,
                    key == any_(Merchant.aliases),
                ),
            )
            .order_by(Merchant.hits.desc(), Merchant.id)
            .limit(1)
        )
        return m
    return None


async def _check_refs(
    session: AsyncSession, user_id: int, category_id: int | None, account_id: int | None
) -> None:
    if category_id is not None:
        c = await session.get(Category, category_id)
        if c is None or c.user_id != user_id:
            raise ValidationFailed("Категория не найдена", details={"field": "category_id"})
    if account_id is not None:
        a = await session.get(Account, account_id)
        if a is None or a.user_id != user_id or a.deleted_at is not None:
            raise ValidationFailed("Счёт не найден", details={"field": "default_account_id"})
        if a.kind == "debt":  # docs/DEBTS.md §2: service accounts of debts are not pickable
            raise ValidationFailed(
                "Это счёт долга — используйте «Долги»", details={"field": "default_account_id"}
            )


async def learn(
    session: AsyncSession,
    user_id: int,
    *,
    name: str | None,
    handle: str | None,
    category_id: int | None,
    account_id: int | None = None,
    alias: str | None = None,
    count_hit: bool = True,
    auto: bool | None = None,
) -> Merchant:
    """Upsert rule; set category_id (latest user choice wins); hits += 1; last_used_at = now.
    Race-safe: a concurrent insert of the same merchant is retried as an update.

    ``auto`` (default: ``count_hit`` — learning from a newly recorded operation) never stores a
    fallback category («Переводы людям», «Прочее»): it only means «nothing better was found»
    and must not override the meaning of the next message. A user's correction
    (``count_hit=False``) is remembered whatever it is."""
    h = strip_handle(handle)
    if not name and not h:
        raise ValidationFailed("Нужно имя продавца или @ник")
    if auto is None:
        auto = count_hit
    if auto and category_id is not None:
        from app.services.categories.service import is_fallback

        if is_fallback(await session.get(Category, category_id)):
            category_id = None
    m = await match_merchant(session, user_id, name=name, handle=h)
    if m is None:
        m = Merchant(
            user_id=user_id,
            name=(name or f"@{h}").strip()[:128],
            handle=h,
            aliases=norm_aliases([alias] if alias else [], name if name and not h else None),
            category_id=category_id,
            default_account_id=account_id,
            hits=0,
        )
        await session.flush()  # pending changes stay outside the savepoint
        try:
            async with session.begin_nested():
                session.add(m)
                await session.flush()
        except IntegrityError:
            m = await match_merchant(session, user_id, name=name, handle=h)
            if m is None:
                raise
    m = await session.scalar(
        select(Merchant)
        .where(Merchant.id == m.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert m is not None
    if category_id is not None:
        m.category_id = category_id
    if account_id is not None:
        m.default_account_id = account_id
    if alias:
        m.aliases = norm_aliases(m.aliases, alias)
    if h and not m.handle:
        m.handle = h
    if count_hit:
        m.hits = (m.hits or 0) + 1
    m.last_used_at = now()
    await session.flush()
    return m


async def merchant_out(session: AsyncSession, m: Merchant) -> MerchantOut:
    cat = await session.get(Category, m.category_id) if m.category_id else None
    return MerchantOut(
        id=m.id, name=m.name, aliases=list(m.aliases), handle=m.handle,
        category_id=m.category_id, category_name=cat.name if cat else None,
        default_account_id=m.default_account_id, hits=m.hits, last_used_at=m.last_used_at,
    )


async def list_merchants(
    session: AsyncSession, user_id: int, q: str | None = None
) -> list[Merchant]:
    stmt = select(Merchant).where(Merchant.user_id == user_id)
    if q:
        like = like_contains(q)
        stmt = stmt.where(
            or_(
                func.lower(Merchant.name).like(like, escape="\\"),
                func.lower(func.coalesce(Merchant.handle, "")).like(like, escape="\\"),
                func.lower(func.array_to_string(Merchant.aliases, " ")).like(like, escape="\\"),
            )
        )
    return list(await session.scalars(stmt.order_by(Merchant.hits.desc(), Merchant.name)))


async def get_merchant(session: AsyncSession, user_id: int, merchant_id: int) -> Merchant:
    m = await session.get(Merchant, merchant_id)
    if m is None or m.user_id != user_id:
        raise NotFound("Продавец не найден")
    return m


async def _check_unique(
    session: AsyncSession, user_id: int, name: str | None, handle: str | None,
    exclude_id: int | None = None,
) -> None:
    conds = []
    if name:
        conds.append(func.lower(Merchant.name) == name.strip().lower())
    if handle:
        conds.append(Merchant.handle == handle)
    if not conds:
        return
    stmt = select(Merchant.id).where(Merchant.user_id == user_id, or_(*conds))
    if exclude_id is not None:
        stmt = stmt.where(Merchant.id != exclude_id)
    if await session.scalar(stmt) is not None:
        raise AlreadyExists("Такой продавец уже есть")


async def create_merchant(
    session: AsyncSession, principal: Principal, data: MerchantCreate
) -> Merchant:
    uid = principal.user_id
    h = strip_handle(data.handle)
    await check_limit(
        session, select(func.count()).select_from(Merchant).where(Merchant.user_id == uid),
        MAX_MERCHANTS, "продавцов",
    )
    await _check_unique(session, uid, data.name, h)
    await _check_refs(session, uid, data.category_id, data.default_account_id)
    m = Merchant(
        user_id=uid, name=data.name.strip(), handle=h, aliases=norm_aliases(data.aliases),
        category_id=data.category_id, default_account_id=data.default_account_id, hits=0,
    )
    await session.flush()  # pending changes stay outside the savepoint
    try:
        async with session.begin_nested():
            session.add(m)
            await session.flush()
    except IntegrityError as e:  # concurrent duplicate (double submit)
        raise AlreadyExists("Такой продавец уже есть") from e
    await write_audit(
        session, user_id=uid, entity_type="merchant", entity_id=m.id, operation="create",
        before=None, after=snapshot(m), source=principal.source,
    )
    await publish(session, uid, {"merchants"}, source=principal.source)
    return m


async def update_merchant(
    session: AsyncSession, principal: Principal, merchant_id: int, patch: MerchantPatch
) -> Merchant:
    uid = principal.user_id
    m = await get_merchant(session, uid, merchant_id)
    before = snapshot(m)
    f = patch.model_fields_set
    h = strip_handle(patch.handle) if "handle" in f else m.handle
    new_name = patch.name.strip() if "name" in f and patch.name else None
    await _check_unique(
        session, uid, new_name if new_name and new_name != m.name else None,
        h if h and h != m.handle else None, exclude_id=m.id,
    )
    await _check_refs(
        session, uid,
        patch.category_id if "category_id" in f else None,
        patch.default_account_id if "default_account_id" in f else None,
    )
    if new_name:
        m.name = new_name
    if "aliases" in f and patch.aliases is not None:
        m.aliases = norm_aliases(patch.aliases)
    if "handle" in f:
        m.handle = h
    if "category_id" in f:
        m.category_id = patch.category_id
    if "default_account_id" in f:
        m.default_account_id = patch.default_account_id
    try:
        await session.flush()
    except IntegrityError as e:  # concurrent rename to the same name/handle
        raise AlreadyExists("Такой продавец уже есть") from e
    await write_audit(
        session, user_id=uid, entity_type="merchant", entity_id=m.id, operation="update",
        before=before, after=snapshot(m), source=principal.source,
    )
    await publish(session, uid, {"merchants"}, source=principal.source)
    return m


async def delete_merchant(session: AsyncSession, principal: Principal, merchant_id: int) -> None:
    """Hard delete of the rule; operations keep their data (merchant_id → NULL)."""
    m = await get_merchant(session, principal.user_id, merchant_id)
    before = snapshot(m)
    await session.delete(m)
    await session.flush()
    await write_audit(
        session, user_id=principal.user_id, entity_type="merchant", entity_id=merchant_id,
        operation="delete", before=before, after=None, source=principal.source,
    )
    await publish(session, principal.user_id, {"merchants", "transactions"},
                  source=principal.source)

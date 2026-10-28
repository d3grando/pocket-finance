"""Categories REST."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from app.api.deps import PrincipalDep, SessionDep
from app.schemas.categories import (
    CategoryCreate,
    CategoryMergeIn,
    CategoryOut,
    CategoryPatch,
    CategorySuggestions,
)
from app.schemas.common import Id, OkOut
from app.services.categories import service as categories
from app.services.ledger.common import check_query_text

router = APIRouter(tags=["categories"])


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(
    principal: PrincipalDep,
    session: SessionDep,
    kind: Literal["expense", "income"] | None = None,
    include_archived: bool = False,
    with_counts: bool = False,
):
    rows = await categories.list_categories(
        session, principal.user_id, kind=kind, include_archived=include_archived
    )
    counts = await categories.tx_counts(session, principal.user_id) if with_counts else None
    return [
        categories.category_out(c, counts.get(c.id, 0) if counts is not None else None)
        for c in rows
    ]


@router.get("/categories/suggest", response_model=CategorySuggestions)
async def suggest(
    principal: PrincipalDep,
    session: SessionDep,
    transaction_id: Id | None = None,
    q: str | None = None,
):
    check_query_text(q=q)
    return await categories.suggest(
        session, principal.user_id, transaction_id=transaction_id, q=q
    )


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(body: CategoryCreate, principal: PrincipalDep, session: SessionDep):
    c = await categories.create_category(session, principal, body)
    out = categories.category_out(c)
    await session.commit()
    return out


@router.post("/categories/merge", response_model=CategoryOut)
async def merge(body: CategoryMergeIn, principal: PrincipalDep, session: SessionDep):
    c = await categories.merge_categories(session, principal, body.source_id, body.target_id)
    out = categories.category_out(c)
    await session.commit()
    return out


@router.patch("/categories/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: Id, body: CategoryPatch, principal: PrincipalDep, session: SessionDep
):
    c = await categories.update_category(session, principal, category_id, body)
    out = categories.category_out(c)
    await session.commit()
    return out


@router.delete("/categories/{category_id}", response_model=OkOut)
async def archive_category(category_id: Id, principal: PrincipalDep, session: SessionDep):
    await categories.archive_category(session, principal, category_id)
    await session.commit()
    return OkOut()

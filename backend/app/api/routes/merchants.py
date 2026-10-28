"""Merchant rules REST."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import PrincipalDep, SessionDep
from app.schemas.common import Id, OkOut
from app.schemas.merchants import MerchantCreate, MerchantOut, MerchantPatch
from app.services.ledger.common import check_query_text
from app.services.merchants import service as merchants

router = APIRouter(tags=["merchants"])


@router.get("/merchants", response_model=list[MerchantOut])
async def list_merchants(principal: PrincipalDep, session: SessionDep, q: str | None = None):
    check_query_text(q=q)
    rows = await merchants.list_merchants(session, principal.user_id, q)
    return [await merchants.merchant_out(session, m) for m in rows]


@router.post("/merchants", response_model=MerchantOut, status_code=201)
async def create_merchant(body: MerchantCreate, principal: PrincipalDep, session: SessionDep):
    m = await merchants.create_merchant(session, principal, body)
    out = await merchants.merchant_out(session, m)
    await session.commit()
    return out


@router.patch("/merchants/{merchant_id}", response_model=MerchantOut)
async def update_merchant(
    merchant_id: Id, body: MerchantPatch, principal: PrincipalDep, session: SessionDep
):
    m = await merchants.update_merchant(session, principal, merchant_id, body)
    out = await merchants.merchant_out(session, m)
    await session.commit()
    return out


@router.delete("/merchants/{merchant_id}", response_model=OkOut)
async def delete_merchant(merchant_id: Id, principal: PrincipalDep, session: SessionDep):
    await merchants.delete_merchant(session, principal, merchant_id)
    await session.commit()
    return OkOut()

"""Statistics REST. Period params: preset|date_from|date_to."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import PrincipalDep, SessionDep
from app.schemas.common import Id, PeriodPreset, PeriodSpec, SafeDate
from app.schemas.stats import (
    BusinessOut,
    ByCategoryOut,
    CompareOut,
    DailyAverageOut,
    SeriesOut,
    SummaryOut,
    TopOut,
)
from app.services.stats import service as stats

router = APIRouter(prefix="/stats", tags=["stats"])

Cur = Literal["VND", "USD"]


def period_params(
    preset: PeriodPreset = PeriodPreset.this_month,
    date_from: SafeDate | None = None,
    date_to: SafeDate | None = None,
) -> PeriodSpec:
    return PeriodSpec(preset=preset, date_from=date_from, date_to=date_to)


PeriodDep = Annotated[PeriodSpec, Depends(period_params)]


@router.get("/summary", response_model=SummaryOut)
async def summary(principal: PrincipalDep, session: SessionDep, currency: Cur = "VND"):
    return await stats.summary(session, principal.user_id, currency)


@router.get("/by-category", response_model=ByCategoryOut)
async def by_category(
    principal: PrincipalDep,
    session: SessionDep,
    period: PeriodDep,
    kind: Literal["expense", "income"] = "expense",
    currency: Cur = "USD",
    is_business: bool | None = None,
    category_id: Annotated[list[Id] | None, Query(max_length=500)] = None,
    account_id: Annotated[list[Id] | None, Query(max_length=500)] = None,
):
    return await stats.by_category(
        session,
        principal.user_id,
        period,
        kind=kind,
        currency=currency,
        is_business=is_business,
        category_ids=category_id,
        account_ids=account_id,
    )


@router.get("/series", response_model=SeriesOut)
async def series(
    principal: PrincipalDep,
    session: SessionDep,
    period: PeriodDep,
    granularity: Literal["day", "month"] = "day",
    currency: Cur = "USD",
    is_business: bool | None = None,
    category_id: Annotated[list[Id] | None, Query(max_length=500)] = None,
    account_id: Annotated[list[Id] | None, Query(max_length=500)] = None,
):
    return await stats.series(
        session,
        principal.user_id,
        period,
        granularity=granularity,
        currency=currency,
        is_business=is_business,
        category_ids=category_id,
        account_ids=account_id,
    )


@router.get("/top", response_model=TopOut)
async def top(
    principal: PrincipalDep,
    session: SessionDep,
    period: PeriodDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    currency: Cur = "USD",
    is_business: bool | None = None,
    category_id: Annotated[list[Id] | None, Query(max_length=500)] = None,
    account_id: Annotated[list[Id] | None, Query(max_length=500)] = None,
):
    return await stats.top_transactions(
        session,
        principal.user_id,
        period,
        limit=limit,
        currency=currency,
        is_business=is_business,
        category_ids=category_id,
        account_ids=account_id,
    )


@router.get("/daily-average", response_model=DailyAverageOut)
async def daily_average(
    principal: PrincipalDep,
    session: SessionDep,
    period: PeriodDep,
    currency: Cur = "USD",
    is_business: bool | None = None,
    category_id: Annotated[list[Id] | None, Query(max_length=500)] = None,
):
    return await stats.daily_average(
        session,
        principal.user_id,
        period,
        currency=currency,
        is_business=is_business,
        category_ids=category_id,
    )


@router.get("/compare", response_model=CompareOut)
async def compare(
    principal: PrincipalDep,
    session: SessionDep,
    period: PeriodDep,
    prev_date_from: SafeDate | None = None,
    prev_date_to: SafeDate | None = None,
    currency: Cur = "USD",
):
    """Without prev_* → preceding period of equal length."""
    previous = None
    if prev_date_from is not None or prev_date_to is not None:
        previous = PeriodSpec(
            preset=PeriodPreset.custom, date_from=prev_date_from, date_to=prev_date_to
        )
    return await stats.compare(session, principal.user_id, period, previous, currency=currency)


@router.get("/business", response_model=BusinessOut)
async def business(
    principal: PrincipalDep, session: SessionDep, period: PeriodDep, currency: Cur = "USD"
):
    return await stats.business(session, principal.user_id, period, currency=currency)

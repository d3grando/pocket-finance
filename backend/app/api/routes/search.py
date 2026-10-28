"""Global quick search for the command palette.

``q`` — text («highl»), or an amount expression («>100$», «150к», «до 50 usdt») parsed without
the LLM. Optional filters narrow the operations part: period (preset/date_from/date_to),
currency, category_id, account_id, min_amount/max_amount (+ amount_currency), kind, is_business.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import PrincipalDep, SessionDep
from app.core.errors import ValidationFailed
from app.schemas.common import Id, PeriodPreset, PeriodSpec, SafeDate
from app.schemas.search import SearchOut
from app.schemas.transactions import TransactionFilter
from app.services.ledger.common import check_query_text
from app.services.stats import search as svc
from app.services.stats.periods import resolve_period
from app.services.stats.service import user_ctx

router = APIRouter(tags=["search"])

KindQ = Literal["expense", "income", "refund", "transfer", "exchange", "adjustment"]


@router.get("/search", response_model=SearchOut)
async def search(
    principal: PrincipalDep,
    session: SessionDep,
    q: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    preset: PeriodPreset | None = None,
    date_from: SafeDate | None = None,
    date_to: SafeDate | None = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9]{2,16}$")] = None,
    category_id: Annotated[list[Id] | None, Query(max_length=500)] = None,
    account_id: Annotated[list[Id] | None, Query(max_length=500)] = None,
    kind: Annotated[list[KindQ] | None, Query()] = None,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
    amount_currency: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9]{2,16}$")] = None,
    is_business: bool | None = None,
):
    check_query_text(q=q)
    text = q.strip()
    has_filter = any(
        v is not None
        for v in (preset, date_from, date_to, currency, category_id, account_id, kind,
                  min_amount, max_amount, is_business)
    )  # fmt: skip
    if not text and not has_filter:
        raise ValidationFailed(
            "Введите запрос", details={"fields": [{"loc": ["query", "q"], "msg": "required"}]}
        )
    d1, d2 = date_from, date_to
    if preset is not None and preset != PeriodPreset.custom:
        u = await user_ctx(session, principal.user_id)
        rng = resolve_period(PeriodSpec(preset=preset), u.today)
        d1, d2 = rng.date_from, rng.date_to
    f = TransactionFilter(
        date_from=d1,
        date_to=d2,
        kinds=kind,
        category_ids=category_id,
        account_ids=account_id,
        currency=currency.upper() if currency else None,
        min_amount=min_amount,
        max_amount=max_amount,
        amount_currency=amount_currency.upper() if amount_currency else None,
        is_business=is_business,
        limit=limit,
    )
    hits = await svc.quick_search(session, principal.user_id, text, limit, f)
    return SearchOut(query=q, hits=hits)

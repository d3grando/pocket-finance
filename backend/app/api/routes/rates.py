"""Rates REST."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from fastapi import APIRouter

from app.api.deps import HumanPrincipalDep, PrincipalDep, SessionDep
from app.core.errors import AppError, ValidationFailed
from app.core.money import quantize_asset
from app.core.timeutil import local_today
from app.models import UserSettings
from app.schemas.common import OkOut, SafeDate
from app.schemas.rates import ConvertOut, RateOut, RatesLatestOut
from app.services.rates import service as rates

router = APIRouter(prefix="/rates", tags=["rates"])

_CODE = re.compile(r"^[A-Z0-9]{2,16}$")


class RatesUnavailable(AppError):
    status, code, message = 503, "rates_unavailable", "Курс сейчас недоступен"


def rate_out(q: rates.RateQuote) -> RateOut:
    return RateOut(
        base=q.base,
        quote=q.quote,
        rate=q.rate,
        rate_date=q.rate_date,
        kind=q.kind,
        source=q.source,
        fetched_at=q.fetched_at,
        is_fallback=q.is_fallback,
        label=rates.rate_label(q),
    )


async def _today(session, user_id: int) -> date:  # type: ignore[no-untyped-def]
    st = await session.get(UserSettings, user_id)
    return local_today(st.timezone if st else None)


@router.get("/latest", response_model=RatesLatestOut)
async def latest(principal: PrincipalDep, session: SessionDep):
    today = await _today(session, principal.user_id)
    usd_vnd = await rates.get_rate(session, "USD", "VND", today, kind="sell", fetch_missing=False)
    usd_rub = await rates.get_rate(session, "USD", "RUB", today, kind="mid", fetch_missing=False)
    crypto = await rates.latest_crypto(session)
    return RatesLatestOut(
        usd_vnd=rate_out(usd_vnd) if usd_vnd else None,
        usd_rub=rate_out(usd_rub) if usd_rub else None,
        crypto=[rate_out(q) for q in crypto],
    )


@router.get("/convert", response_model=ConvertOut)
async def convert(
    principal: PrincipalDep,
    session: SessionDep,
    amount: Decimal,
    from_: str,
    to: str,
    on: SafeDate | None = None,
):
    """Query: ?amount=15&from_=USD&to=VND&on=2026-10-27. 503 rates_unavailable if none."""
    src, dst = from_.strip().upper(), to.strip().upper()
    if not _CODE.match(src) or not _CODE.match(dst):
        raise ValidationFailed(
            "Неизвестная валюта", details={"field": "from_" if not _CODE.match(src) else "to"}
        )
    if not amount.is_finite() or abs(amount) >= Decimal("1e19"):
        raise ValidationFailed("Некорректная сумма", details={"field": "amount"})
    day = on or await _today(session, principal.user_id)
    res = await rates.convert(session, amount, src, dst, day)
    if res is None:
        raise RatesUnavailable(details={"from": src, "to": dst, "on": day.isoformat()})
    await session.commit()  # keep a rate fetched on demand
    return ConvertOut(
        amount=amount,
        currency=src,
        converted=quantize_asset(res.amount, dst),
        to=dst,
        rate=rate_out(res.quote),
    )


@router.post("/refresh", response_model=OkOut)
async def refresh(principal: HumanPrincipalDep, session: SessionDep):
    n = await rates.refresh_fiat(session)
    n += await rates.refresh_crypto(session)
    await session.commit()
    if n == 0:
        raise RatesUnavailable("Не удалось обновить курсы: источники недоступны")
    return OkOut()

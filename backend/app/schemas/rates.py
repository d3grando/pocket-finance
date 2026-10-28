"""Rates schemas."""

from __future__ import annotations

from datetime import date, datetime

from app.schemas.common import CurrencyCode, Money, Rate, Schema


class RateOut(Schema):
    base: CurrencyCode
    quote: CurrencyCode
    rate: Rate
    rate_date: date
    kind: str
    source: str
    fetched_at: datetime | None = None
    is_fallback: bool = False
    label: str = ""  # «$1 = 26 140 ₫ (VCB, 27.10)»


class ConvertOut(Schema):
    amount: Money
    currency: CurrencyCode
    converted: Money
    to: CurrencyCode
    rate: RateOut


class RatesLatestOut(Schema):
    usd_vnd: RateOut | None
    usd_rub: RateOut | None
    crypto: list[RateOut]

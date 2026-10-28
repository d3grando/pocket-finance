"""Statistics schemas. All sums exclude transfer/exchange/adjustment;
expense = expense − refund (docs/ARCH.md §4). ``currency`` is VND or USD (stored amount_vnd/usd)."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import Field

from app.schemas.common import CurrencyCode, DateRange, Money, Schema

StatsCurrency = Literal["VND", "USD"]


class KpiOut(Schema):
    value: Money | None = Field(description="null = нет данных (—)")
    currency: CurrencyCode
    approx_usd: Money | None = None
    change_ratio: Money | None = Field(
        default=None, description="vs previous comparable period, e.g. '0.12' = +12%"
    )
    sparkline: list[Money] = Field(default_factory=list, description="daily values, oldest first")


class SummaryOut(Schema):
    """Home tiles: today / month expenses, month income, net flow + «Разобрать: N»."""

    currency: CurrencyCode
    today: DateRange
    month: DateRange
    spent_today: KpiOut
    spent_month: KpiOut
    income_month: KpiOut
    net_month: KpiOut
    needs_account_count: int
    missing_rates_count: int = Field(0, description="operations without amount_usd/vnd yet")


class CategorySlice(Schema):
    category_id: int
    name: str
    icon: str | None = None
    color: str | None = None
    is_business: bool
    is_new: bool = False
    amount: Money
    share: Money = Field(description="0..1 as decimal string")
    count: int


class ByCategoryOut(Schema):
    range: DateRange
    kind: Literal["expense", "income"]
    currency: CurrencyCode
    total: Money
    items: list[CategorySlice]


class SeriesPoint(Schema):
    date: dt.date = Field(description="day, or first day of month for by_month")
    label: str
    expense: Money
    income: Money
    net: Money


class SeriesOut(Schema):
    range: DateRange
    granularity: Literal["day", "month"]
    currency: CurrencyCode
    points: list[SeriesPoint]


class TopItem(Schema):
    transaction_id: int
    occurred_on: dt.date
    title: str
    category: str | None
    amount: Money
    currency: CurrencyCode
    original_amount: Money
    original_currency: CurrencyCode


class TopOut(Schema):
    range: DateRange
    currency: CurrencyCode
    items: list[TopItem]


class DailyAverageOut(Schema):
    range: DateRange
    currency: CurrencyCode
    days: int
    total: Money
    average: Money


class CompareSide(Schema):
    range: DateRange
    expense: Money
    income: Money
    net: Money


class CompareOut(Schema):
    currency: CurrencyCode
    current: CompareSide
    previous: CompareSide
    expense_change_ratio: Money | None
    income_change_ratio: Money | None
    by_category: list[CategorySlice] = Field(default_factory=list)


class BusinessOut(Schema):
    range: DateRange
    currency: CurrencyCode
    business_income: Money
    business_expense: Money
    business_net: Money
    personal_income: Money
    personal_expense: Money
    expense_by_category: list[CategorySlice]
    income_by_category: list[CategorySlice]

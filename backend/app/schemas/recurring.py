"""Recurring reminders schemas."""

from __future__ import annotations

from datetime import date, time
from typing import Literal

from pydantic import Field

from app.schemas.common import (
    CurrencyCode,
    Id,
    Money,
    PositiveMoney,
    SafeDate,
    Schema,
    StrictInput,
)

Frequency = Literal["daily", "weekly", "monthly", "yearly"]


class RecurringOut(Schema):
    id: int
    title: str
    kind: Literal["expense", "income"]
    amount: Money | None
    currency: CurrencyCode
    account_id: int | None
    category_id: int | None
    merchant_id: int | None
    description: str | None
    frequency: Frequency
    interval: int
    day_of_month: int | None
    weekday: int | None
    month: int | None
    next_due_on: date
    remind_time: time
    is_active: bool
    last_reminded_on: date | None
    version: int


class RecurringCreate(StrictInput):
    title: str = Field(min_length=1, max_length=128)
    kind: Literal["expense", "income"] = "expense"
    amount: PositiveMoney | None = None
    currency: CurrencyCode = "VND"
    account_id: Id | None = None
    category_id: Id | None = None
    merchant_id: Id | None = None
    description: str | None = Field(default=None, max_length=500)
    frequency: Frequency = "monthly"
    interval: int = Field(default=1, ge=1, le=36)
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    weekday: int | None = Field(default=None, ge=0, le=6)
    month: int | None = Field(default=None, ge=1, le=12)
    next_due_on: SafeDate
    remind_time: time = time(10, 0)


class RecurringPatch(StrictInput):
    version: int
    title: str | None = Field(default=None, min_length=1, max_length=128)
    amount: PositiveMoney | None = None
    currency: CurrencyCode | None = None
    account_id: Id | None = None
    category_id: Id | None = None
    description: str | None = Field(default=None, max_length=500)
    frequency: Frequency | None = None
    interval: int | None = Field(default=None, ge=1, le=36)
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    weekday: int | None = Field(default=None, ge=0, le=6)
    month: int | None = Field(default=None, ge=1, le=12)
    next_due_on: SafeDate | None = None
    remind_time: time | None = None
    is_active: bool | None = None


class RecurringRecordIn(StrictInput):
    """[Записать]: optional overrides; defaults come from the reminder."""

    amount: PositiveMoney | None = None
    amount_text: str | None = Field(
        default=None, max_length=64, description="«11.5м» — parsed by parser.amounts.parse_amount"
    )
    currency: CurrencyCode | None = None
    account_id: Id | None = None
    occurred_on: SafeDate | None = None

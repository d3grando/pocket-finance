"""Shared schema primitives: Money (Decimal as JSON string), currency codes, pagination."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, TypeVar

from annotated_types import Interval, MaxLen
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    model_validator,
)

from app.core.money import AMOUNT_MAX, normalize, to_decimal


def _money_in(v: Any) -> Any:
    if isinstance(v, float):
        raise ValueError("сумму передавайте строкой, а не числом с плавающей точкой")
    if isinstance(v, (str, int, Decimal)):
        return to_decimal(v)
    return v


def _money_check(v: Decimal) -> Decimal:
    if not v.is_finite():
        raise ValueError("некорректная сумма")
    if abs(v) >= AMOUNT_MAX:
        raise ValueError("слишком большая сумма")
    # > 8 fractional digits is rejected where it is used, with its own message: the ledger
    # (422 «Не больше 8 знаков после точки», ledger.common) and the AI validator («Слишком мелкая
    # сумма»). Not here: Money also types LLM output, which the validator must explain.
    return v


def _positive(v: Decimal) -> Decimal:
    if v <= 0:
        raise ValueError("сумма должна быть больше нуля")
    return v


#: Any Decimal amount (may be negative). JSON: string "150000" / "-3.5".
Money = Annotated[
    Decimal,
    BeforeValidator(_money_in),
    AfterValidator(_money_check),
    PlainSerializer(normalize, return_type=str, when_used="json"),
]
#: Strictly positive amount.
PositiveMoney = Annotated[Money, AfterValidator(_positive)]
#: Exchange/conversion rate (Decimal as string).
Rate = Annotated[
    Decimal,
    BeforeValidator(_money_in),
    PlainSerializer(normalize, return_type=str, when_used="json"),
]


def _upper(v: Any) -> Any:
    return v.strip().upper() if isinstance(v, str) else v


# ------------------------------------------------------------------------------- input limits

#: PostgreSQL BIGINT upper bound: a larger id/offset must be a 422, not a driver error (500).
INT64_MAX = 2**63 - 1
#: Database row id in a path / query / body (1 … BIGINT max).
Id = Annotated[int, Interval(ge=1, le=INT64_MAX)]
#: Offset of a paginated list (0 … BIGINT max).
Offset = Annotated[int, Interval(ge=0, le=INT64_MAX)]

#: Aliases of an account / category / merchant / person: at most ``MAX_ALIASES`` words of at
#: most ``ALIAS_MAX_LEN`` characters each (the parser builds an index of all of them).
MAX_ALIASES = 50
ALIAS_MAX_LEN = 64
Alias = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                         max_length=ALIAS_MAX_LEN)]
Aliases = Annotated[list[Alias], MaxLen(MAX_ALIASES)]

#: Lucide icon name («UtensilsCrossed») and a #RRGGBB color.
Icon = Annotated[str, StringConstraints(max_length=64, pattern=r"^[A-Za-z0-9]+$")]
Color = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]

#: Accepted range of input dates: far outside it date arithmetic overflows (year 1 / 9999 →
#: OverflowError → 500) and nothing real happens there anyway.
DATE_MIN = date(1970, 1, 1)
DATE_MAX = date(2100, 12, 31)
_RANGE_MSG = "Дата вне допустимого диапазона (1970-01-01 … 2100-12-31)"


def _date_in_range(v: date) -> date:
    if not DATE_MIN <= v <= DATE_MAX:
        raise ValueError(_RANGE_MSG)
    return v


def _datetime_in_range(v: datetime) -> datetime:
    # the wall-clock date (no tz conversion: converting year 1 / 9999 itself overflows)
    if not DATE_MIN <= v.date() <= DATE_MAX:
        raise ValueError(_RANGE_MSG)
    return v


#: An input date / datetime limited to ``DATE_MIN`` … ``DATE_MAX``.
SafeDate = Annotated[date, AfterValidator(_date_in_range)]
SafeDateTime = Annotated[datetime, AfterValidator(_datetime_in_range)]


#: Currency / asset code: "VND", "USD", "USDT", "TON".
CurrencyCode = Annotated[
    str, BeforeValidator(_upper), Field(pattern=r"^[A-Z0-9]{2,16}$", examples=["VND"])
]


class Schema(BaseModel):
    """Base for all API schemas."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class StrictInput(Schema):
    """Base for request bodies: unknown fields are rejected."""

    model_config = ConfigDict(from_attributes=True, extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def _no_nul(self) -> Any:
        # PostgreSQL text cannot hold \x00 (CharacterNotInRepertoire → was a 500)
        if _has_nul(self.__dict__):
            raise ValueError("Недопустимый символ \\x00 в тексте")
        return self


def _has_nul(v: Any, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if isinstance(v, str):
        return "\x00" in v
    if isinstance(v, BaseModel):
        return _has_nul(v.__dict__, depth + 1)
    if isinstance(v, dict):
        return any(_has_nul(k, depth + 1) or _has_nul(x, depth + 1) for k, x in v.items())
    if isinstance(v, (list, tuple, set)):
        return any(_has_nul(x, depth + 1) for x in v)
    return False


class SourceEnum(StrEnum):
    web = "web"
    desktop = "desktop"
    telegram = "telegram"
    system = "system"


class MoneyOut(Schema):
    amount: Money
    currency: CurrencyCode


T = TypeVar("T")


class Page[T](Schema):
    items: list[T]
    total: int
    limit: int
    offset: int


class OkOut(Schema):
    ok: bool = True


class ErrorDetail(Schema):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorOut(Schema):
    error: ErrorDetail


class PeriodPreset(StrEnum):
    today = "today"
    yesterday = "yesterday"
    this_week = "this_week"
    last_week = "last_week"
    this_month = "this_month"
    last_month = "last_month"
    this_year = "this_year"
    last_year = "last_year"
    last_7_days = "last_7_days"
    last_30_days = "last_30_days"
    all_time = "all_time"
    custom = "custom"


class PeriodSpec(Schema):
    """Period. For preset=custom both dates are required (inclusive). Weeks start Monday."""

    preset: PeriodPreset = PeriodPreset.this_month
    date_from: SafeDate | None = None
    date_to: SafeDate | None = None


class DateRange(Schema):
    """Resolved period (inclusive) + Russian label, e.g. «Октябрь 2026», «20–26 окт»."""

    date_from: date
    date_to: date
    label: str

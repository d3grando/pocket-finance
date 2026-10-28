"""Transaction API schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import (
    INT64_MAX,
    CurrencyCode,
    Id,
    Money,
    MoneyOut,
    PositiveMoney,
    Rate,
    SafeDate,
    SafeDateTime,
    Schema,
    SourceEnum,
    StrictInput,
)

TxKind = Literal["expense", "income", "refund", "transfer", "exchange", "adjustment"]


def reject_nul(v: Any) -> Any:
    """PostgreSQL text cannot hold U+0000: reject it as a validation error (422), not a 500."""
    if isinstance(v, str):
        if "\x00" in v:
            raise ValueError("недопустимый символ \\x00")
    elif isinstance(v, dict):
        for x in v.values():
            reject_nul(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            reject_nul(x)
    return v


class CleanInput(StrictInput):
    """Request body of ledger endpoints: StrictInput + no NUL bytes in any string."""

    @model_validator(mode="before")
    @classmethod
    def _no_nul(cls, data: Any) -> Any:
        return reject_nul(data)
TxStatus = Literal["posted", "needs_account"]


class AccountRef(Schema):
    id: int
    name: str
    icon: str | None = None


class CategoryRef(Schema):
    id: int
    name: str
    kind: Literal["expense", "income"]
    icon: str | None = None
    color: str | None = None
    is_business: bool
    is_new: bool


class MerchantRef(Schema):
    id: int
    name: str


DebtOpKind = Literal["lend", "repay_in", "borrow", "repay_out", "forgive"]


class TxDebtRef(Schema):
    """``TransactionOut.debt`` — set for debt operations (``transactions.debt_op``,
    docs/DEBTS.md). Old clients ignore it and see a plain transfer / expense / income."""

    id: int = Field(description="debts.id")
    name: str = Field(description="«Коля»")
    op: DebtOpKind


class TransactionOut(Schema):
    id: int
    kind: TxKind
    status: TxStatus
    occurred_at: datetime
    occurred_on: date
    amount: Money
    currency: CurrencyCode
    account: AccountRef | None = None
    account_amount: Money | None = None
    account_asset: CurrencyCode | None = None
    account_rate: Rate | None = None
    is_estimated: bool = False
    counter_account: AccountRef | None = None
    counter_amount: Money | None = None
    counter_asset: CurrencyCode | None = None
    category: CategoryRef | None = None
    is_business: bool = False
    merchant: MerchantRef | None = None
    counterparty: str | None = None
    description: str | None = None
    amount_usd: Money | None = None
    amount_vnd: Money | None = None
    usd_rate: Rate | None = None
    rate_date: date | None = None
    rate_source: str | None = None
    refund_of_id: int | None = None
    recurring_id: int | None = None
    source: SourceEnum
    raw_text: str | None = None
    created_action_id: uuid.UUID | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    # Russian one-liner for lists/bot: «Кофе · Highlands», «Обмен Кошелёк → Биржа»
    title: str = ""
    #: debt operation (additive, migration 0005); null for ordinary operations
    debt: TxDebtRef | None = None


class TransactionCreate(CleanInput):
    """Manual creation (form) and the dispatcher's resolved create. kind ∈ expense|income|refund.
    account_id None + account_none=False + currency without default account → needs_account."""

    kind: Literal["expense", "income", "refund"]
    amount: PositiveMoney
    currency: CurrencyCode
    account_id: Id | None = None
    account_none: bool = False
    account_amount: PositiveMoney | None = Field(
        default=None, description="actual charge in account asset; omitted → computed by rate"
    )
    category_id: Id
    merchant_id: Id | None = None
    counterparty: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    occurred_on: SafeDate | None = None
    occurred_at: SafeDateTime | None = None
    refund_of_id: Id | None = None
    recurring_id: Id | None = None


class TransactionPatch(CleanInput):
    """PATCH: only provided fields change. ``version`` is required (optimistic locking)."""

    version: int
    kind: Literal["expense", "income", "refund"] | None = None
    amount: PositiveMoney | None = None
    currency: CurrencyCode | None = None
    account_id: Id | None = None
    account_none: bool | None = None
    account_amount: PositiveMoney | None = None
    #: exchange only: the actually received amount (account_rate = counter_amount / amount)
    counter_amount: PositiveMoney | None = None
    category_id: Id | None = None
    merchant_id: Id | None = None
    counterparty: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    occurred_on: SafeDate | None = None
    occurred_at: SafeDateTime | None = None


class AssignAccountIn(CleanInput):
    """Answer to «С какого?». account_id None → «без счёта»."""

    account_id: Id | None
    version: int | None = None


class TransactionFilter(Schema):
    """Query params of GET /transactions (also used by search and export)."""

    date_from: SafeDate | None = None
    date_to: SafeDate | None = None
    kinds: list[TxKind] | None = None
    category_ids: list[Id] | None = Field(default=None, max_length=500)
    account_ids: list[Id] | None = Field(default=None, max_length=500)
    currency: CurrencyCode | None = None
    min_amount: Money | None = None
    max_amount: Money | None = None
    amount_currency: CurrencyCode | None = None
    q: str | None = Field(default=None, max_length=200)
    is_business: bool | None = None
    status: TxStatus | None = None
    merchant_id: Id | None = None
    include_deleted: bool = False
    sort: Literal["date_desc", "date_asc", "amount_desc", "amount_asc"] = "date_desc"
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=INT64_MAX)

    @field_validator("q")
    @classmethod
    def _q_no_nul(cls, v: str | None) -> str | None:
        return reject_nul(v)


class TransactionTotals(Schema):
    """Totals of the filtered set (P&L kinds only), in VND and USD."""

    expense_vnd: Money
    income_vnd: Money
    expense_usd: Money
    income_usd: Money
    count: int


class TransactionPage(Schema):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int
    totals: TransactionTotals


class NeedsAccountCount(Schema):
    count: int


# ----------------------------------------------------------------- transfers / exchange / adjust


class TransferCreate(CleanInput):
    """Between own accounts in the same asset."""

    from_account_id: Id
    to_account_id: Id
    amount: PositiveMoney
    currency: CurrencyCode
    description: str | None = Field(default=None, max_length=500)
    occurred_on: SafeDate | None = None

    @model_validator(mode="after")
    def _diff(self) -> TransferCreate:
        if self.from_account_id == self.to_account_id:
            raise ValueError("счета перевода должны различаться")
        return self


class ExchangeCreate(CleanInput):
    """Exchange: stores the actual rate (counter_amount / amount)."""

    from_account_id: Id
    to_account_id: Id
    amount: PositiveMoney
    currency: CurrencyCode
    counter_amount: PositiveMoney
    counter_currency: CurrencyCode
    description: str | None = Field(default=None, max_length=500)
    occurred_on: SafeDate | None = None


class AdjustmentCreate(CleanInput):
    """Reconcile: the account's actual balance of ``asset`` is ``actual_balance``."""

    account_id: Id
    asset: CurrencyCode
    actual_balance: Money


class AdjustmentResult(Schema):
    kind: Literal["adjusted", "opening_set", "matches"]
    account_id: int
    asset: CurrencyCode
    previous_balance: Money | None
    balance: Money
    delta: Money | None
    transaction: TransactionOut | None = None
    action_id: uuid.UUID | None = None


class MutationResult(Schema):
    """Common response of single-object mutations: object + undo handle."""

    transaction: TransactionOut
    action_id: uuid.UUID


class TxSums(Schema):
    by_currency: list[MoneyOut]

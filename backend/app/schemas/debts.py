"""Debt API schemas (docs/DEBTS.md §7, API.md §15). The contract is frozen:
additive changes only.

Sign convention everywhere: a debt balance ``amount`` > 0 — the person owes me
(``direction="owed_to_me"``), < 0 — I owe the person (``"i_owe"``). Per currency, no conversion;
``approx_*`` are display-only (current rates, null when no rate).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import (
    Aliases,
    CurrencyCode,
    Id,
    Money,
    PositiveMoney,
    SafeDate,
    SafeDateTime,
    Schema,
)
from app.schemas.transactions import CleanInput, DebtOpKind, TransactionOut, TxDebtRef

DebtDirection = Literal["owed_to_me", "i_owe"]
DebtStatus = Literal["owed_to_me", "i_owe", "mixed", "settled"]


class DebtBalanceOut(Schema):
    currency: CurrencyCode
    amount: Money = Field(description="signed: > 0 owes me, < 0 I owe")
    direction: DebtDirection
    approx_usd: Money | None = None
    approx_vnd: Money | None = None
    open_since: date | None = Field(
        default=None, description="local date the current non-zero balance started (age)"
    )


class DebtOut(Schema):
    id: int
    name: str
    handle: str | None = None
    aliases: list[str] = Field(default_factory=list)
    note: str | None = None
    account_id: int
    is_archived: bool = False
    status: DebtStatus
    #: non-zero balances only (in the lists of DebtsOut — only the balances of that list)
    balances: list[DebtBalanceOut] = Field(default_factory=list)
    last_activity_on: date | None = None
    remind_snoozed_until: date | None = None
    version: int
    created_at: datetime


class DebtTotalsOut(Schema):
    """Home / Accounts header: «Мне должны ≈ … / Я должен ≈ …» (display only)."""

    owed_to_me_vnd: Money | None = None
    owed_to_me_usd: Money | None = None
    i_owe_vnd: Money | None = Field(default=None, description="positive number")
    i_owe_usd: Money | None = None
    owed_to_me_count: int = 0
    i_owe_count: int = 0
    #: some balance had no rate → totals are partial («≈» + hint)
    missing_rates: bool = False


class DebtsOut(Schema):
    """GET /debts. A person with debts in both directions appears in both lists."""

    owed_to_me: list[DebtOut] = Field(default_factory=list, description="«Мне должны»")
    i_owe: list[DebtOut] = Field(default_factory=list, description="«Я должен»")
    settled: list[DebtOut] = Field(
        default_factory=list, description="only with include_settled=true"
    )
    totals: DebtTotalsOut = Field(default_factory=DebtTotalsOut)


class DebtHistoryItem(Schema):
    transaction: TransactionOut
    op: DebtOpKind
    currency: CurrencyCode
    delta: Money = Field(description="signed change of the debt balance in ``currency``")
    balance_after: Money = Field(description="debt balance in ``currency`` after this operation")


class DebtDetailOut(Schema):
    """GET /debts/{id}: card of a person — balances + history (newest first)."""

    debt: DebtOut
    history: list[DebtHistoryItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0
    #: additive: «Отменить последнюю» → ``POST /actions/{last_action_id}/undo``.
    #: The newest action still in effect on the latest operation (its creation, or the newest
    #: edit when it was changed since); null — no operations / no undoable action.
    last_action_id: uuid.UUID | None = None


class DebtOpCreate(CleanInput):
    """POST /debts/operations and the dispatcher's resolved debt intent.

    Exactly one of ``debt_id`` / ``person_name``. ``person_name`` finds the person by
    ``name_key`` or creates one. ``amount`` None → the whole remaining balance in ``currency``
    (repay_in / repay_out / forgive only). Account: ``account_id`` / ``account_none`` /
    neither → default by currency (₫ → Карта) or needs_account (crypto / $ without account).
    ``forgive`` never touches a real account (account fields must be empty)."""

    op: DebtOpKind
    debt_id: Id | None = None
    person_name: str | None = Field(default=None, min_length=1, max_length=56)
    handle: str | None = Field(default=None, max_length=64, pattern=r"^@?[A-Za-z0-9_]{1,64}$")
    amount: PositiveMoney | None = None
    currency: CurrencyCode
    account_id: Id | None = None
    account_none: bool = False
    account_amount: PositiveMoney | None = Field(
        default=None, description="actual amount in a single-currency account's asset"
    )
    description: str | None = Field(default=None, max_length=500)
    occurred_on: SafeDate | None = None
    occurred_at: SafeDateTime | None = None
    #: forgive only (additive): which side is written off («простить долг Коле» → owed_to_me,
    #: «Петя простил мне долг» → i_owe); a mismatch with the balance sign → 422. None = by sign
    direction: DebtDirection | None = None

    @model_validator(mode="after")
    def _check(self) -> DebtOpCreate:
        if (self.debt_id is None) == (self.person_name is None):
            raise ValueError("укажите debt_id или person_name (одно из двух)")
        if self.amount is None and self.op in ("lend", "borrow"):
            raise ValueError("для займа нужна сумма")
        if self.op == "forgive" and (
            self.account_id is not None or self.account_none or self.account_amount is not None
        ):
            raise ValueError("списание долга не затрагивает счёт")
        return self


class DebtOpResult(Schema):
    """201 of POST /debts/operations (same shape the dispatcher uses for cards)."""

    transaction: TransactionOut
    action_id: uuid.UUID
    debt: DebtOut
    #: debt balance in the operation currency after it (signed)
    balance_after: Money
    #: repay above the remaining balance: the surplus (> 0) that flipped the balance sign
    excess: Money | None = None
    #: real account unknown (crypto / $ without account) → «С какого?/На какой счёт?»
    needs_account: bool = False


class DebtPatch(CleanInput):
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=56)
    handle: str | None = Field(default=None, max_length=64, pattern=r"^@?[A-Za-z0-9_]{0,64}$")
    aliases: Aliases | None = None
    note: str | None = Field(default=None, max_length=1000)
    is_archived: bool | None = None


class DebtSnoozeIn(CleanInput):
    days: int = Field(default=7, ge=1, le=90)


class DebtBrief(Schema):
    """Additive ``debt`` field of assistant cards (created / needs_account / updated)."""

    debt_id: int
    name: str
    op: DebtOpKind
    currency: CurrencyCode
    balance_after: Money = Field(description="signed debt balance in ``currency``")
    excess: Money | None = None


__all__ = [
    "DebtBalanceOut",
    "DebtBrief",
    "DebtDetailOut",
    "DebtDirection",
    "DebtHistoryItem",
    "DebtOpCreate",
    "DebtOpKind",
    "DebtOpResult",
    "DebtOut",
    "DebtPatch",
    "DebtSnoozeIn",
    "DebtStatus",
    "DebtTotalsOut",
    "DebtsOut",
    "TxDebtRef",
]

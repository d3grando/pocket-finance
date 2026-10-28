"""Intents — the ONLY contract between parser, LLM, validator and dispatcher.

Both ``services/parser`` and ``services/ai`` (LLM JSON output) produce ``IntentList``.
Rules (docs/ARCH.md §4–5):
* ``amount`` is the final positive Decimal; ``amount_raw`` is the token as written ("85к", "1.5м",
  "180$"). The validator recomputes ``amount`` from ``amount_raw`` with the parser's
  ``parse_amount`` — the LLM never does arithmetic.
* ``*_ref`` fields are free-text references (name or alias as the user wrote it, e.g. "Кошелёк",
  "налом", "еда"); the validator resolves them to ids. ``*_id`` fields are already resolved ids.
* Dates are concrete local dates (user timezone); ``None`` means today.
* ``currency=None`` means "not stated" → default rules (VND default, small-number thousands).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.common import (
    CurrencyCode,
    Money,
    PeriodSpec,
    PositiveMoney,
    Schema,
)

TxKindLiteral = Literal["expense", "income", "refund", "transfer", "exchange", "adjustment"]
PnlKind = Literal["expense", "income", "refund"]


class NewCategory(Schema):
    """Category the model wants to create (shown with 🆕 and a «в другую» button)."""

    name: str = Field(min_length=1, max_length=64)
    kind: Literal["expense", "income"] = "expense"
    is_business: bool = False
    icon: str | None = Field(default=None, max_length=32, description="lucide icon name")


class SearchFilter(Schema):
    period: PeriodSpec | None = None
    kinds: list[TxKindLiteral] | None = None
    category_refs: list[str] = Field(default_factory=list)
    category_ids: list[int] = Field(default_factory=list)
    account_refs: list[str] = Field(default_factory=list)
    account_ids: list[int] = Field(default_factory=list)
    currency: CurrencyCode | None = None
    min_amount: Money | None = None
    max_amount: Money | None = None
    amount_currency: CurrencyCode | None = Field(
        default=None, description="currency of min/max (e.g. 'дороже 100 долларов' → USD)"
    )
    text: str | None = Field(default=None, max_length=200)
    counterparty: str | None = Field(default=None, max_length=128)
    merchant_ref: str | None = None
    is_business: bool | None = None
    needs_account: bool | None = None
    sort: Literal["date_desc", "date_asc", "amount_desc", "amount_asc"] = "date_desc"
    limit: int = Field(default=20, ge=1, le=100)


class TargetSpec(Schema):
    """Which existing transaction(s) an edit/delete applies to.

    last          — most recent transaction from the action context (any client, ≤30 min);
    last_expense  — most recent expense; last_income — most recent income;
    context       — transaction from AssistantContext.edit_transaction_id (button «Изменить»);
    id            — explicit transaction_id;
    search        — found by filter → ALWAYS goes through preview (confirmation).
    ``position``: 1 = last, 2 = the one before («предпоследняя»).
    """

    kind: Literal["last", "last_expense", "last_income", "context", "id", "search"] = "last"
    transaction_id: int | None = None
    position: int = Field(default=1, ge=1, le=10)
    search: SearchFilter | None = None

    @model_validator(mode="after")
    def _check(self) -> TargetSpec:
        if self.kind == "id" and self.transaction_id is None:
            raise ValueError("target.kind=id requires transaction_id")
        if self.kind == "search" and self.search is None:
            raise ValueError("target.kind=search requires search")
        return self


class _AmountFields(Schema):
    amount: PositiveMoney
    amount_raw: str | None = Field(default=None, max_length=64)
    currency: CurrencyCode | None = None


class CreateTransactionIntent(_AmountFields):
    """«кофе 85к», «получил 300 USDT от клиента», «перевёл 3 тона @bob с кошелька»,
    «вернули 200к за наушники» (kind=refund)."""

    type: Literal["create_transaction"] = "create_transaction"
    kind: PnlKind = "expense"
    account_ref: str | None = None
    account_none: bool = Field(default=False, description="user explicitly said «без счёта»")
    category_ref: str | None = None
    new_category: NewCategory | None = None
    merchant_ref: str | None = Field(default=None, description="«васт», «grab», «highlands»")
    counterparty: str | None = Field(default=None, max_length=128, description="@handle or name")
    description: str | None = Field(default=None, max_length=500, description="назначение")
    occurred_on: date | None = None
    occurred_time: time | None = None
    is_business: bool | None = Field(default=None, description="hint only; category decides")
    for_resale: bool = Field(default=False, description="«для магазина», «на продажу»")


class TransferIntent(_AmountFields):
    """Between own accounts / own @handles. Exchange when counter currency differs.
    Transfer to a NOT-own @handle is converted by the validator into an expense."""

    type: Literal["transfer"] = "transfer"
    from_account_ref: str | None = None
    to_account_ref: str | None = None
    to_handle: str | None = Field(default=None, description="without @")
    counter_amount: PositiveMoney | None = None
    counter_amount_raw: str | None = None
    counter_currency: CurrencyCode | None = None
    description: str | None = Field(default=None, max_length=500)
    occurred_on: date | None = None


DebtOpLiteral = Literal["lend", "repay_in", "borrow", "repay_out", "repay", "forgive"]
DebtDirectionLiteral = Literal["owed_to_me", "i_owe"]


class DebtIntent(Schema):
    """Debt operation (docs/DEBTS.md §4–5). NOT income/expense.

    op: lend «дал в долг Коле 500к», «одолжил Коле 500к»; repay_in «Коля вернул (долг) 200к»;
    borrow «занял у Пети 1м»; repay_out «отдал Пете 500к» (when I owe Петя), «вернул Пете долг»;
    repay — direction from the balance (canonical button text «возврат долга: Коля 200к»);
    forgive «Коля не вернёт», «простить/списать долг Коли», «Петя простил мне долг».

    ``person`` — as written («Коле», «у Пети», «@vasya»); ``person_name`` — nominative
    («Коля») when the writer knows it (LLM; parser when certain), used for a NEW person only.
    ``amount`` None → the whole remaining balance (repay*/forgive only).
    ``fallback`` (docs/DEBTS.md §0 п.5): «X вернул N» / «отдал X N» without the word
    «долг» → the validator checks the open debt in that currency; none → converted to an
    ordinary operation: income (``fallback="income"``, category_ref or «Прочее») / expense
    «Переводы людям» (``fallback="expense"``), counterparty = person.
    ``direction`` — forgive/repay only; None = from the balance (both directions open in that
    currency → clarify)."""

    type: Literal["debt"] = "debt"
    op: DebtOpLiteral
    person: str = Field(min_length=1, max_length=64)
    person_name: str | None = Field(default=None, max_length=56)
    amount: PositiveMoney | None = None
    amount_raw: str | None = Field(default=None, max_length=64)
    currency: CurrencyCode | None = None
    account_ref: str | None = None
    account_none: bool = Field(default=False, description="user explicitly said «без счёта»")
    direction: DebtDirectionLiteral | None = None
    fallback: Literal["income", "expense"] | None = None
    category_ref: str | None = Field(default=None, description="for the fallback only")
    description: str | None = Field(default=None, max_length=500)
    occurred_on: date | None = None
    occurred_time: time | None = None


class DebtsQueryIntent(Schema):
    """«кто мне должен?», «мои долги», «сколько должен Коля?», bot /debts → answer card."""

    type: Literal["debts_query"] = "debts_query"
    person: str | None = Field(default=None, max_length=64)
    direction: DebtDirectionLiteral | None = None


class AdjustBalanceIntent(Schema):
    """Reconciliation: «на карте 5.2м», «в кошельке 120 usdt»."""

    type: Literal["adjust_balance"] = "adjust_balance"
    account_ref: str
    amount: Money = Field(description="actual balance, may be 0")
    amount_raw: str | None = None
    currency: CurrencyCode | None = None


class TransactionChanges(Schema):
    amount: PositiveMoney | None = None
    amount_raw: str | None = None
    currency: CurrencyCode | None = None
    account_ref: str | None = None
    account_none: bool | None = None
    account_amount: PositiveMoney | None = Field(
        default=None, description="actual charged amount in account asset («списали 21.3м»)"
    )
    account_amount_raw: str | None = None
    category_ref: str | None = None
    new_category: NewCategory | None = None
    occurred_on: date | None = None
    description: str | None = Field(default=None, max_length=500)
    counterparty: str | None = Field(default=None, max_length=128)
    kind: PnlKind | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> TransactionChanges:
        if not self.model_dump(exclude_none=True):
            raise ValueError("changes must contain at least one field")
        return self


class UpdateTransactionIntent(Schema):
    """«нет, 250к», «исправь последнюю покупку на 150к», «васт списали 21.3м»."""

    type: Literal["update_transaction"] = "update_transaction"
    target: TargetSpec = Field(default_factory=TargetSpec)
    changes: TransactionChanges


class DeleteTransactionIntent(Schema):
    """«удали последний расход», «удали все такси за вчера» (search → preview)."""

    type: Literal["delete_transaction"] = "delete_transaction"
    target: TargetSpec = Field(default_factory=TargetSpec)


class UndoIntent(Schema):
    """«отмени», «верни как было». action_id None → last undoable action."""

    type: Literal["undo"] = "undo"
    action_id: uuid.UUID | None = None


class SearchIntent(Schema):
    """«покажи траты дороже 100 долларов», «какие мои самые дорогие покупки?»."""

    type: Literal["search"] = "search"
    filter: SearchFilter = Field(default_factory=SearchFilter)


StatsMetric = Literal[
    "spent",  # сколько потратил
    "earned",  # сколько заработал (доход)
    "net",  # чистыми = доход − расход
    "by_category",  # на что ушло
    "top",  # самые крупные траты
    "daily_average",
    "compare",  # period vs compare_period
    "by_day",
    "by_month",
    "business",  # бизнес доходы/расходы
    "balance",  # остатки по счетам
]


class StatsIntent(Schema):
    """«сколько я потратил сегодня?», «сколько чистыми заработал за октябрь?»."""

    type: Literal["stats"] = "stats"
    metric: StatsMetric = "spent"
    period: PeriodSpec = Field(default_factory=PeriodSpec)
    compare_period: PeriodSpec | None = None
    category_refs: list[str] = Field(default_factory=list)
    account_refs: list[str] = Field(default_factory=list)
    is_business: bool | None = None
    display_currency: CurrencyCode | None = None
    limit: int | None = Field(default=None, ge=1, le=50)


class CreateCategoryIntent(Schema):
    type: Literal["create_category"] = "create_category"
    name: str = Field(min_length=1, max_length=64)
    kind: Literal["expense", "income"] = "expense"
    is_business: bool = False
    icon: str | None = None


class AnswerIntent(Schema):
    """General finance question with no user data needed. LLM writes the answer (RU)."""

    type: Literal["answer"] = "answer"
    text: str = Field(max_length=2000)


class ClarifyIntent(Schema):
    """Cannot act safely: ambiguous currency/target, missing amount, unknown."""

    type: Literal["clarify"] = "clarify"
    reason: Literal["ambiguous_currency", "ambiguous_target", "missing_amount", "unknown", "other"]
    question: str = Field(max_length=300)
    options: list[str] = Field(
        default_factory=list, max_length=6, description="ready-to-send replies"
    )


Intent = Annotated[
    CreateTransactionIntent
    | TransferIntent
    | AdjustBalanceIntent
    | UpdateTransactionIntent
    | DeleteTransactionIntent
    | UndoIntent
    | SearchIntent
    | StatsIntent
    | CreateCategoryIntent
    | AnswerIntent
    | ClarifyIntent
    | DebtIntent
    | DebtsQueryIntent,
    Field(discriminator="type"),
]


class IntentList(Schema):
    """Top-level JSON the LLM must return (response_format json_schema)."""

    intents: list[Intent] = Field(min_length=1, max_length=10)


# ---------------------------------------------------------------- parser context / result


class CtxAccount(Schema):
    id: int
    name: str
    aliases: list[str] = Field(default_factory=list)
    kind: str
    currency: str | None = None
    is_multi_asset: bool = False
    default_for_currencies: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)


class CtxCategory(Schema):
    id: int
    name: str
    kind: Literal["expense", "income"]
    aliases: list[str] = Field(default_factory=list)
    is_business: bool = False


class CtxMerchant(Schema):
    id: int
    name: str
    aliases: list[str] = Field(default_factory=list)
    handle: str | None = None
    category_id: int | None = None
    default_account_id: int | None = None


class CtxDebtOpen(Schema):
    currency: str
    direction: Literal["owed_to_me", "i_owe"]


class CtxDebt(Schema):
    """Known debt counterparty for the parser / LLM: name, forms, open currencies + direction.
    NO amounts (by design: the LLM gets names and currencies only)."""

    id: int
    name: str
    name_key: str = ""
    aliases: list[str] = Field(default_factory=list)
    handle: str | None = None
    open: list[CtxDebtOpen] = Field(default_factory=list, description="non-zero balances")


class ActionBrief(Schema):
    """One entry of the dialogue context (last 10 actions within 30 minutes, all clients)."""

    action_id: uuid.UUID
    kind: str
    summary: str
    source: str
    created_at: datetime
    transaction_ids: list[int] = Field(default_factory=list)
    # brief facts of the main transaction, for «нет, 250к»
    tx_kind: str | None = None
    amount: Money | None = None
    currency: str | None = None
    category: str | None = None
    account: str | None = None


class ParseContext(Schema):
    """Everything parser/LLM may know. No balances, no history beyond recent_actions."""

    now: datetime
    today: date
    timezone: str = "Asia/Ho_Chi_Minh"
    default_currency: str = "VND"
    small_amount_thousands: bool = True
    accounts: list[CtxAccount] = Field(default_factory=list)
    categories: list[CtxCategory] = Field(default_factory=list)
    merchants: list[CtxMerchant] = Field(default_factory=list)
    my_handles: list[str] = Field(default_factory=list)
    #: debt counterparties (docs/DEBTS.md §6), additive: names + open currencies, no amounts
    debts: list[CtxDebt] = Field(default_factory=list)
    known_assets: list[str] = Field(default_factory=list)
    recent_actions: list[ActionBrief] = Field(default_factory=list)
    edit_transaction_id: int | None = None


class ParseResult(Schema):
    """Parser output. needs_llm=True → pipeline calls the LLM with the original text.

    ``partial_ok``: intents are safe to execute even if the LLM is unavailable
    (e.g. a simple expense recognised, but a trailing clause was not).
    """

    intents: list[Intent] = Field(default_factory=list)
    needs_llm: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)
    partial_ok: bool = False
    reason: str | None = None
    unparsed: str | None = None


__all__ = [
    "ActionBrief",
    "AdjustBalanceIntent",
    "AnswerIntent",
    "ClarifyIntent",
    "CreateCategoryIntent",
    "CreateTransactionIntent",
    "CtxAccount",
    "CtxCategory",
    "CtxDebt",
    "CtxDebtOpen",
    "CtxMerchant",
    "DebtIntent",
    "DebtsQueryIntent",
    "DeleteTransactionIntent",
    "Intent",
    "IntentList",
    "NewCategory",
    "ParseContext",
    "ParseResult",
    "SearchFilter",
    "SearchIntent",
    "StatsIntent",
    "TargetSpec",
    "TransactionChanges",
    "TransferIntent",
    "UndoIntent",
    "UpdateTransactionIntent",
]

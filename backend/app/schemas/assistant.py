"""Assistant (AI input) request/response and result cards — shared by web, desktop and bot.

Card variants follow DESIGN.md §9.2 (field ``kind``). Texts are Russian and pre-formatted by the
backend (``title``/``subtitle``/``lines``) so all clients render the same words; structured data
is included for rich rendering. ``title`` never contains emoji except 🆕 (the bot prepends ✅ etc.).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from app.schemas.accounts import AccountOut
from app.schemas.categories import CategoryOut
from app.schemas.common import CurrencyCode, Id, Money, Schema, StrictInput
from app.schemas.debts import DebtBrief, DebtsOut
from app.schemas.intents import SearchFilter
from app.schemas.transactions import TransactionOut


class AssistantContext(StrictInput):
    edit_transaction_id: Id | None = Field(
        default=None, description="set after the user pressed «Изменить» on this transaction"
    )


class AssistantMessageIn(StrictInput):
    text: str = Field(min_length=1, max_length=2000)
    context: AssistantContext | None = None


# ------------------------------------------------------------------------------- buttons

ButtonActionKind = Literal[
    "undo",  # POST /actions/{action_id}/undo
    "edit",  # client: open inline edit of transaction_id (bot: next message edits it)
    "choose_account",  # POST /transactions/{transaction_id}/account {account_id|null}
    "later",  # needs_account: dismiss, stays in «Разобрать»
    "change_category",  # client: open category picker (GET /categories/suggest?transaction_id=)
    "set_category",  # PATCH /transactions/{transaction_id} {category_id, version}
    "confirm_preview",  # POST /assistant/previews/{preview_token}/confirm
    "cancel_preview",  # POST /assistant/previews/{preview_token}/cancel
    "send_text",  # client: send ``text`` as a new assistant message (clarify options)
    "manual",  # client: open manual add form (prefill from ``prefill``)
    "retry",  # client: resend the same message with the same Idempotency-Key
    "open",  # client: navigate to ``route`` (e.g. "/analytics?period=this_month")
    "redo",  # «Вернуть» after undo: POST /actions/{action_id}/undo (undo of the undo)
]


class ButtonAction(Schema):
    kind: ButtonActionKind
    action_id: uuid.UUID | None = None
    transaction_id: int | None = None
    account_id: int | None = None
    category_id: int | None = None
    preview_token: uuid.UUID | None = None
    text: str | None = None
    route: str | None = None
    prefill: dict[str, str] | None = None


class Button(Schema):
    id: str = Field(description="stable id within the card, e.g. 'undo', 'acc-3'")
    label: str
    style: Literal["primary", "secondary", "outline", "ghost", "destructive"] = "ghost"
    hotkey: str | None = Field(default=None, description="'1'..'9', 'Enter', 'Esc', 'Ctrl+Z'")
    action: ButtonAction


# --------------------------------------------------------------------------------- cards


class _CardBase(Schema):
    title: str
    subtitle: str | None = None
    buttons: list[Button] = Field(default_factory=list)


class CreatedCard(_CardBase):
    """(a)/(b) one operation created (expense/income/refund/transfer/exchange)."""

    kind: Literal["created"] = "created"
    action_id: uuid.UUID
    transaction: TransactionOut
    category_is_new: bool = False
    approx: str | None = Field(default=None, description="«≈ $16.20» right side")
    note: str | None = Field(default=None, description="«Запомню: @alice → Софт и AI»")
    #: debt operation (docs/DEBTS.md §8), additive: old clients render title/subtitle/note only
    debt: DebtBrief | None = None


class CreatedManyCard(_CardBase):
    """(c) several operations from one message. Undo-all = undo every action_id."""

    kind: Literal["created_many"] = "created_many"
    action_ids: list[uuid.UUID]
    transactions: list[TransactionOut]
    total: Money | None = None
    total_currency: CurrencyCode | None = None


class AccountOption(Schema):
    account_id: int | None = Field(description="null = «Без счёта»")
    label: str
    hint: str | None = None
    hotkey: str | None = None


class NeedsAccountCard(_CardBase):
    """(d) saved in expenses but not in balances until an account is chosen."""

    kind: Literal["needs_account"] = "needs_account"
    action_id: uuid.UUID
    transaction: TransactionOut
    question: str = "С какого счёта?"
    options: list[AccountOption]
    #: debt operation waiting for the real account (question «На какой счёт?» for inflows)
    debt: DebtBrief | None = None


class UpdatedCard(_CardBase):
    """Single edit applied immediately («нет, 250к»). ``changes``: «45 000 ₫ → 50 000 ₫»."""

    kind: Literal["updated"] = "updated"
    action_id: uuid.UUID
    transaction: TransactionOut
    changes: list[str] = Field(default_factory=list)
    debt: DebtBrief | None = None


class DeletedCard(_CardBase):
    kind: Literal["deleted"] = "deleted"
    action_id: uuid.UUID
    transaction: TransactionOut


class PreviewItem(Schema):
    transaction: TransactionOut
    before: str | None = Field(default=None, description="«45 000 ₫» / «Прочее»")
    after: str | None = Field(default=None, description="«50 000 ₫» / «Кофе»; null for delete")


class PreviewCard(_CardBase):
    """(e) nothing changed yet; Enter → confirm_preview, Esc → cancel_preview."""

    kind: Literal["preview"] = "preview"
    preview_token: uuid.UUID
    operation: Literal["bulk_update", "bulk_delete"]
    change_summary: str = Field(description="«категория: Прочее → Кофе»")
    items: list[PreviewItem] = Field(description="first ≤ 20")
    total_count: int
    total: str | None = Field(default=None, description="«Итого 181 000 ₫»")
    expires_at: datetime


class AppliedCard(_CardBase):
    """Preview confirmed: «Изменено 4 операции»."""

    kind: Literal["applied"] = "applied"
    action_id: uuid.UUID
    count: int


class AnswerLine(Schema):
    label: str
    value: str = Field(description="formatted, e.g. «320 000 ₫»")
    amount: Money | None = None
    currency: CurrencyCode | None = None
    share: float | None = Field(default=None, description="0..1, for bars (display only)")
    category_id: int | None = None
    transaction_id: int | None = None
    icon: str | None = None


class AnswerCard(_CardBase):
    """(f) statistics / search results / general answer. No undo/edit buttons."""

    kind: Literal["answer"] = "answer"
    answer_type: Literal["stats", "list", "compare", "balance", "text"]
    headline: str | None = Field(default=None, description="main number «−595 000 ₫»")
    headline_approx: str | None = Field(default=None, description="«≈ $22.76»")
    lines: list[AnswerLine] = Field(default_factory=list)
    transactions: list[TransactionOut] = Field(default_factory=list)
    total_count: int | None = None
    filter: SearchFilter | None = Field(default=None, description="to open in «Операции»")
    text: str | None = None
    #: «кто мне должен?» / /debts (answer_type="balance"): structured lists for new clients;
    #: old clients show ``lines`` («Коля · должен 300 000 ₫»)
    debts: DebtsOut | None = None


class BalanceCard(_CardBase):
    """Reconciliation result: «Карта: 5 200 000 ₫ · корректировка −120 000 ₫»."""

    kind: Literal["balance"] = "balance"
    action_id: uuid.UUID | None
    account: AccountOut
    result: Literal["adjusted", "opening_set", "matches"]
    transaction: TransactionOut | None = None


class CategoryCard(_CardBase):
    kind: Literal["category"] = "category"
    action_id: uuid.UUID
    category: CategoryOut


class UndoneCard(_CardBase):
    """(i) «Отменено: 150 000 ₫ · Еда» + [Вернуть] (redo = undo of undo_action_id)."""

    kind: Literal["undone"] = "undone"
    undone_action_id: uuid.UUID
    undo_action_id: uuid.UUID | None


class UnclearCard(_CardBase):
    """(g) nothing recorded; options are ready replies (send_text) + [Вручную]."""

    kind: Literal["unclear"] = "unclear"
    reason: Literal["ambiguous_currency", "ambiguous_target", "missing_amount", "unknown", "other"]
    question: str
    keep_input: bool = True


class ErrorCard(_CardBase):
    """(h) nothing recorded."""

    kind: Literal["error"] = "error"
    code: Literal[
        "ai_unavailable",
        "validation_failed",
        "not_found",
        "conflict",
        "preview_expired",
        "preview_stale",
        "nothing_to_undo",
        "internal",
    ]
    text: str | None = None
    keep_input: bool = True


Card = Annotated[
    CreatedCard
    | CreatedManyCard
    | NeedsAccountCard
    | UpdatedCard
    | DeletedCard
    | PreviewCard
    | AppliedCard
    | AnswerCard
    | BalanceCard
    | CategoryCard
    | UndoneCard
    | UnclearCard
    | ErrorCard,
    Field(discriminator="kind"),
]


class AssistantResponse(Schema):
    request_id: int | None = Field(description="ai_requests.id")
    route: Literal["parser", "llm", "parser_llm", "none"]
    cards: list[Card]
    action_ids: list[uuid.UUID] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list, description="entities for cache invalidation")
    ai_available: bool = True


class PreviewConfirmOut(AssistantResponse):
    pass


class CategoryChoiceIn(StrictInput):
    category_id: Id

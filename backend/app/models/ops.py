"""AI request log, audit log, action log (undo/context), idempotency, previews,
recurring reminders, bot notification outbox."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import MONEY, Base, CreatedMixin, TimestampMixin, UserOwnedMixin
from app.models.enums import (
    ActionKind,
    AIRoute,
    AIStatus,
    AuditOp,
    Frequency,
    IdemState,
    OutboxKind,
    OutboxStatus,
    PreviewKind,
    Source,
    check_in,
)


class AIRequest(UserOwnedMixin, CreatedMixin, Base):
    __tablename__ = "ai_requests"
    __table_args__ = (
        CheckConstraint(check_in("route", AIRoute), name="route"),
        CheckConstraint(check_in("status", AIStatus), name="status"),
        Index("ix_ai_requests_user_time", "user_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[str] = mapped_column(String(12), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    parser_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    llm_request: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    llm_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    intents: Mapped[list[Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class ActionLog(UserOwnedMixin, CreatedMixin, Base):
    """Unit of undo + dialogue context (last 10 actions within 30 minutes)."""

    __tablename__ = "action_log"
    __table_args__ = (
        CheckConstraint(check_in("kind", ActionKind), name="kind"),
        CheckConstraint(check_in("source", Source), name="source"),
        Index("ix_action_log_user_time", "user_id", text("created_at DESC")),
        Index(
            "uq_action_log_user_idem_key",
            "user_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_action_log_undone_by", "undone_by_action_id"),
        Index("ix_action_log_ai_request", "ai_request_id",
              postgresql_where=text("ai_request_id IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    input_text: Mapped[str | None] = mapped_column(Text)
    # [{"type": "transaction", "id": 123}]
    entity_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), nullable=False
    )
    # [{"type": "transaction", "id": 123, "before": {...}|null, "after_version": 2}]
    undo_data: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), nullable=False
    )
    is_undoable: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undone_by_action_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ai_request_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ai_requests.id", ondelete="SET NULL")
    )
    # Ledger-level idempotency (defence in depth under the Idempotency-Key middleware):
    # the first request with a key claims it by inserting this row; a replay returns
    # ``response`` (stored before commit) and never creates a duplicate.
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response: Mapped[Any | None] = mapped_column(JSONB)


class AuditLog(UserOwnedMixin, CreatedMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(check_in("operation", AuditOp), name="operation"),
        Index("ix_audit_logs_entity", "user_id", "entity_type", "entity_id", "created_at"),
        Index("ix_audit_logs_action", "action_id", postgresql_where=text("action_id IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    action_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("action_log.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(12), nullable=False)


class IdempotencyKey(UserOwnedMixin, CreatedMixin, Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "key", name="pk_idempotency_keys"),
        CheckConstraint(check_in("state", IdemState), name="state"),
        Index("ix_idempotency_keys_expires", "expires_at"),
    )

    key: Mapped[str] = mapped_column(String(128), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(256), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[Any | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PreviewToken(UserOwnedMixin, CreatedMixin, Base):
    """Pending bulk change awaiting confirmation (Enter)."""

    __tablename__ = "preview_tokens"
    __table_args__ = (CheckConstraint(check_in("kind", PreviewKind), name="kind"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    intent: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # [{"id": 1, "version": 3}]
    affected: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    action_source: Mapped[str] = mapped_column(String(12), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_action_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class RecurringTransaction(UserOwnedMixin, TimestampMixin, Base):
    """Reminder only: never creates a transaction without user's [Записать]."""

    __tablename__ = "recurring_transactions"
    __table_args__ = (
        CheckConstraint("kind IN ('expense','income')", name="kind"),
        CheckConstraint(check_in("frequency", Frequency), name="frequency"),
        CheckConstraint("interval >= 1", name="interval_positive"),
        CheckConstraint("amount IS NULL OR amount > 0", name="amount_positive"),
        CheckConstraint("day_of_month IS NULL OR day_of_month BETWEEN 1 AND 31", name="dom"),
        CheckConstraint("weekday IS NULL OR weekday BETWEEN 0 AND 6", name="weekday"),
        CheckConstraint("month IS NULL OR month BETWEEN 1 AND 12", name="month"),
        Index("ix_recurring_due", "user_id", "next_due_on", postgresql_where=text("is_active")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), server_default="expense", nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(16), server_default="VND", nullable=False)
    account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="SET NULL")
    )
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="SET NULL")
    )
    merchant_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("merchants.id", ondelete="SET NULL")
    )
    description: Mapped[str | None] = mapped_column(Text)
    frequency: Mapped[str] = mapped_column(String(8), nullable=False)
    interval: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)
    day_of_month: Mapped[int | None] = mapped_column(SmallInteger)
    weekday: Mapped[int | None] = mapped_column(SmallInteger)
    month: Mapped[int | None] = mapped_column(SmallInteger)
    next_due_on: Mapped[date] = mapped_column(Date, nullable=False)
    remind_time: Mapped[time] = mapped_column(Time, server_default=text("'10:00'"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    last_reminded_on: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)


class NotificationOutbox(UserOwnedMixin, CreatedMixin, Base):
    __tablename__ = "notifications_outbox"
    __table_args__ = (
        CheckConstraint(check_in("kind", OutboxKind), name="kind"),
        CheckConstraint(check_in("status", OutboxStatus), name="status"),
        Index("ix_outbox_status_available", "status", "available_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16), server_default="telegram", nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    # {"text": "<b>…</b>", "parse_mode": "HTML", "buttons": [[{"text": "…", "callback": "…"}]]}
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(12), server_default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

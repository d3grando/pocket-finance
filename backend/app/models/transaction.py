"""Transactions (user-facing operations) and postings (balance movements)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

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
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import MONEY, RATE, Base, TimestampMixin, UserOwnedMixin
from app.models.enums import DebtOp, RateSource, Source, TxKind, TxStatus, check_in


class Transaction(UserOwnedMixin, TimestampMixin, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(check_in("kind", TxKind), name="kind"),
        CheckConstraint(check_in("status", TxStatus), name="status"),
        CheckConstraint(check_in("source", Source), name="source"),
        CheckConstraint("amount > 0", name="amount_positive"),
        # needs_account = the REAL account is unknown. For debt inflows (repay_in/borrow) the
        # debt account is the transfer source (account_id) and the missing real account is
        # counter_account_id (docs/DEBTS.md §3), hence the exception.
        CheckConstraint(
            "status <> 'needs_account' OR account_id IS NULL "
            "OR debt_op IN ('repay_in','borrow')",
            name="needs_account_no_account",
        ),
        CheckConstraint(
            "debt_op IS NULL OR " + check_in("debt_op", DebtOp), name="debt_op"
        ),
        CheckConstraint(
            "kind = 'adjustment' OR account_amount IS NULL OR account_amount > 0",
            name="account_amount_positive",
        ),
        CheckConstraint(
            "rate_source IS NULL OR " + check_in("rate_source", RateSource), name="rate_source"
        ),
        Index(
            "ix_transactions_user_date",
            "user_id",
            text("occurred_on DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_transactions_user_category_date", "user_id", "category_id", "occurred_on"),
        Index("ix_transactions_user_account", "user_id", "account_id"),
        Index("ix_transactions_user_kind_date", "user_id", "kind", "occurred_on"),
        Index("ix_transactions_user_created", "user_id", text("created_at DESC")),
        Index("ix_transactions_merchant", "merchant_id"),
        # FK lookups (ON DELETE SET NULL / NO ACTION checks when parents are deleted: wipe,
        # ai_requests cleanup, delete_user) — without them every deleted row is a seq scan
        Index("ix_transactions_account", "account_id",
              postgresql_where=text("account_id IS NOT NULL")),
        Index("ix_transactions_counter_account", "counter_account_id",
              postgresql_where=text("counter_account_id IS NOT NULL")),
        Index("ix_transactions_category", "category_id",
              postgresql_where=text("category_id IS NOT NULL")),
        Index("ix_transactions_refund_of", "refund_of_id",
              postgresql_where=text("refund_of_id IS NOT NULL")),
        Index("ix_transactions_recurring", "recurring_id",
              postgresql_where=text("recurring_id IS NOT NULL")),
        Index("ix_transactions_ai_request", "ai_request_id",
              postgresql_where=text("ai_request_id IS NOT NULL")),
        Index(
            "ix_transactions_needs_account",
            "user_id",
            postgresql_where=text("status = 'needs_account' AND deleted_at IS NULL"),
        ),
        Index(
            "ix_transactions_search_trgm",
            text(
                "(coalesce(description,'') || ' ' || coalesce(counterparty,'') || ' ' || "
                "coalesce(raw_text,'')) gin_trgm_ops"
            ),
            postgresql_using="gin",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(16), server_default="posted", nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)

    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False)

    account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("accounts.id")
    )
    account_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    account_asset: Mapped[str | None] = mapped_column(String(16))
    account_rate: Mapped[Decimal | None] = mapped_column(RATE)
    is_estimated: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )

    counter_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("accounts.id")
    )
    counter_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    counter_asset: Mapped[str | None] = mapped_column(String(16))

    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("categories.id")
    )
    is_business: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    merchant_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("merchants.id", ondelete="SET NULL")
    )
    counterparty: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    #: debt operation kind (docs/DEBTS.md §3); NULL for ordinary operations (all pre-0005 rows)
    debt_op: Mapped[str | None] = mapped_column(String(12))

    amount_usd: Mapped[Decimal | None] = mapped_column(MONEY)
    amount_vnd: Mapped[Decimal | None] = mapped_column(MONEY)
    usd_rate: Mapped[Decimal | None] = mapped_column(RATE)
    rate_date: Mapped[date | None] = mapped_column(Date)
    rate_source: Mapped[str | None] = mapped_column(String(16))

    refund_of_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("transactions.id", ondelete="SET NULL")
    )
    recurring_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("recurring_transactions.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    ai_request_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ai_requests.id", ondelete="SET NULL")
    )
    created_action_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Optimistic locking: every UPDATE carries "WHERE version = <loaded>" (StaleDataError →
    # 409). The ledger bumps ``version`` exactly once per logical change (not per flush).
    __mapper_args__ = {
        "version_id_col": version,
        "version_id_generator": False,
        "eager_defaults": True,
    }


class Posting(UserOwnedMixin, Base):
    """Signed movement of one asset on one account. Rebuilt by ledger on every change.

    FKs to accounts use NO ACTION (not RESTRICT) so that deleting a user cascades cleanly."""

    __tablename__ = "postings"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="amount_nonzero"),
        Index("ix_postings_account_asset_time", "account_id", "asset", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("transactions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id"), nullable=False
    )
    asset: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

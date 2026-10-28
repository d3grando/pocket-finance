"""Categories and merchant rules."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UserOwnedMixin
from app.models.enums import CategoryKind, check_in


class Category(UserOwnedMixin, TimestampMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint(check_in("kind", CategoryKind), name="kind"),
        Index(
            "uq_categories_user_kind_name",
            "user_id",
            "kind",
            func.lower(text("name")),
            unique=True,
            postgresql_where=text("merged_into_id IS NULL"),
        ),
        Index("ix_categories_aliases", "aliases", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    is_business: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(32))
    color: Mapped[str | None] = mapped_column(String(16))
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), server_default=text("'{}'"), nullable=False
    )
    is_system: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    is_new: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    merged_into_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="SET NULL")
    )
    sort_order: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)


class Merchant(UserOwnedMixin, TimestampMixin, Base):
    """Learned rule: merchant/recipient -> category (+ optional default account)."""

    __tablename__ = "merchants"
    __table_args__ = (
        Index("uq_merchants_user_name", "user_id", func.lower(text("name")), unique=True),
        Index(
            "uq_merchants_user_handle",
            "user_id",
            "handle",
            unique=True,
            postgresql_where=text("handle IS NOT NULL"),
        ),
        Index("ix_merchants_aliases", "aliases", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), server_default=text("'{}'"), nullable=False
    )
    handle: Mapped[str | None] = mapped_column(CITEXT)
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="SET NULL")
    )
    default_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="SET NULL")
    )
    hits: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

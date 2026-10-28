"""Accounts, per-asset balances (opening), own @handles ("мои аккаунты")."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import MONEY, Base, CreatedMixin, TimestampMixin, UserOwnedMixin
from app.models.enums import AccountKind, check_in


class Account(UserOwnedMixin, TimestampMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(check_in("kind", AccountKind), name="kind"),
        CheckConstraint("is_multi_asset OR currency IS NOT NULL", name="currency_required"),
        Index(
            "uq_accounts_user_name",
            "user_id",
            func.lower(text("name")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_accounts_aliases", "aliases", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(16))
    is_multi_asset: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), server_default=text("'{}'"), nullable=False
    )
    default_for_currencies: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), server_default=text("'{}'"), nullable=False
    )
    color: Mapped[str | None] = mapped_column(String(16))
    icon: Mapped[str | None] = mapped_column(String(32))
    sort_order: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assets: Mapped[list[AccountAsset]] = relationship(
        back_populates="account", cascade="all, delete-orphan", lazy="selectin"
    )


class AccountAsset(UserOwnedMixin, TimestampMixin, Base):
    """One row per (account, asset). opening_balance NULL => balance unknown (not shown)."""

    __tablename__ = "account_assets"
    __table_args__ = (
        UniqueConstraint("account_id", "asset", name="uq_account_assets_account_asset"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    asset: Mapped[str] = mapped_column(String(16), nullable=False)
    opening_balance: Mapped[Decimal | None] = mapped_column(MONEY)
    opening_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped[Account] = relationship(back_populates="assets")


class MyAccount(UserOwnedMixin, CreatedMixin, Base):
    """Own Telegram/wallet handle: transfers to it are internal transfers, not expenses."""

    __tablename__ = "my_accounts"
    __table_args__ = (UniqueConstraint("user_id", "handle", name="uq_my_accounts_user_handle"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    handle: Mapped[str] = mapped_column(CITEXT, nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )

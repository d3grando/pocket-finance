"""Debts (docs/DEBTS.md): one row per counterparty person, bound 1:1 to a service account of
kind ``debt`` (multi-asset, opening balance 0 per asset → the balance is always known).

Debt operations are ordinary transactions on that account (transfers for lend/repay/borrow,
expense/income for forgive), marked with ``transactions.debt_op``. The debt balance is the
account balance: > 0 — the person owes me, < 0 — I owe the person (per asset, no conversion).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UserOwnedMixin


class Debt(UserOwnedMixin, TimestampMixin, Base):
    __tablename__ = "debts"
    __table_args__ = (
        Index(
            "uq_debts_user_name",
            "user_id",
            func.lower(text("name")),
            unique=True,
        ),
        Index(
            "uq_debts_user_handle",
            "user_id",
            "handle",
            unique=True,
            postgresql_where=text("handle IS NOT NULL"),
        ),
        Index("ix_debts_user_name_key", "user_id", "name_key"),
        Index("ix_debts_aliases", "aliases", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    #: service account (accounts.kind='debt', is_multi_asset, name «Долг: <name>»)
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("accounts.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    #: display name, nominative as best known («Коля»); renamable
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: app.core.person_names.name_key(name) — case/declension-insensitive match key («кол»)
    name_key: Mapped[str] = mapped_column(String(64), nullable=False)
    #: other written forms seen («коле», «у коли»), lowercase, learned on use
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), server_default=text("'{}'"), nullable=False
    )
    #: Telegram/wallet handle without «@» («vasya») — optional
    handle: Mapped[str | None] = mapped_column(CITEXT)
    note: Mapped[str | None] = mapped_column(Text)
    is_archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    #: «Напомнить позже»: no reminder before this local date
    remind_snoozed_until: Mapped[date | None] = mapped_column(Date)
    #: last local date a debt reminder was enqueued for this person
    last_reminded_on: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)

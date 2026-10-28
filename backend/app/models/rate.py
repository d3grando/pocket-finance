"""Currency/crypto rates: 1 base = rate quote on rate_date."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Identity,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import RATE, Base
from app.models.enums import RateKind, RateSource, check_in


class CurrencyRate(Base):
    __tablename__ = "currency_rates"
    __table_args__ = (
        CheckConstraint("rate > 0", name="rate_positive"),
        CheckConstraint(check_in("kind", RateKind), name="kind"),
        CheckConstraint(check_in("source", RateSource), name="source"),
        UniqueConstraint(
            "base", "quote", "rate_date", "kind", "source", name="uq_currency_rates_key"
        ),
        Index("ix_currency_rates_pair_date", "base", "quote", text("rate_date DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    base: Mapped[str] = mapped_column(String(16), nullable=False)
    quote: Mapped[str] = mapped_column(String(16), nullable=False)
    rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), server_default="mid", nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

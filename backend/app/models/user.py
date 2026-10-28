"""Users, settings, sessions, device tokens, passkeys, WebAuthn challenges."""

from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedMixin, TimestampMixin, UserOwnedMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)

    settings: Mapped[UserSettings] = relationship(back_populates="user", uselist=False)


class UserSettings(Base):
    __tablename__ = "user_settings"
    __table_args__ = (
        CheckConstraint("theme IN ('light','dark','system')", name="theme"),
        CheckConstraint("report_weekly_day BETWEEN 0 AND 6", name="report_weekly_day"),
        CheckConstraint("debt_reminder_days BETWEEN 1 AND 365", name="debt_reminder_days"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    timezone: Mapped[str] = mapped_column(
        String(64), server_default="Asia/Ho_Chi_Minh", nullable=False
    )
    default_currency: Mapped[str] = mapped_column(String(16), server_default="VND", nullable=False)
    analytics_currency: Mapped[str] = mapped_column(
        String(16), server_default="USD", nullable=False
    )
    summary_currency: Mapped[str] = mapped_column(String(16), server_default="VND", nullable=False)
    small_amount_thousands: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False
    )
    theme: Mapped[str] = mapped_column(String(8), server_default="system", nullable=False)
    desktop_hotkey: Mapped[str] = mapped_column(
        String(64), server_default="Ctrl+Alt+Space", nullable=False
    )
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    report_monthly: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    report_daily: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    report_weekly: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    #: monthly report time (on the 1st)
    report_time: Mapped[time] = mapped_column(Time, server_default=text("'10:00'"), nullable=False)
    report_daily_time: Mapped[time] = mapped_column(
        Time, server_default=text("'22:00'"), nullable=False
    )
    #: 0 = Monday … 6 = Sunday
    report_weekly_day: Mapped[int] = mapped_column(
        SmallInteger, server_default=text("6"), nullable=False
    )
    report_weekly_time: Mapped[time] = mapped_column(
        Time, server_default=text("'21:00'"), nullable=False
    )
    reminders_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False
    )
    #: «напоминать о долгах старше N дней» (docs/DEBTS.md §9), off by default (migration 0005)
    debt_reminders_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    debt_reminder_days: Mapped[int] = mapped_column(
        SmallInteger, server_default=text("30"), nullable=False
    )
    onboarding_done: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="settings")


class Session(UserOwnedMixin, CreatedMixin, Base):
    """Browser (web/PWA) session. Token and CSRF are stored as HMAC hashes."""

    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_active_user", "user_id", postgresql_where=text("revoked_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    csrf_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceToken(UserOwnedMixin, CreatedMixin, Base):
    """Electron device token (Bearer pfd_...)."""

    __tablename__ = "device_tokens"
    __table_args__ = (
        CheckConstraint("platform IN ('windows','macos','linux')", name="platform"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str | None] = mapped_column(INET)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Passkey(UserOwnedMixin, CreatedMixin, Base):
    __tablename__ = "passkeys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), nullable=False)
    transports: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), server_default=text("'{}'"), nullable=False
    )
    aaguid: Mapped[str | None] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthChallenge(CreatedMixin, Base):
    __tablename__ = "auth_challenges"
    __table_args__ = (CheckConstraint("purpose IN ('register','login')", name="purpose"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    challenge: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

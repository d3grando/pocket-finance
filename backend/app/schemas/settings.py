"""User settings schemas."""

from __future__ import annotations

from datetime import time
from typing import Literal

from pydantic import Field

from app.schemas.common import CurrencyCode, Schema, StrictInput


class SettingsOut(Schema):
    timezone: str
    default_currency: CurrencyCode
    analytics_currency: CurrencyCode
    summary_currency: CurrencyCode
    small_amount_thousands: bool
    theme: Literal["light", "dark", "system"]
    desktop_hotkey: str
    telegram_user_id: int | None
    report_monthly: bool
    report_daily: bool
    report_weekly: bool
    report_time: time
    report_daily_time: time
    report_weekly_day: int = Field(ge=0, le=6)
    report_weekly_time: time
    reminders_enabled: bool
    #: debt reminders (docs/DEBTS.md §9), additive (0005): off by default, N = 30 days
    debt_reminders_enabled: bool = False
    debt_reminder_days: int = Field(default=30, ge=1, le=365)
    onboarding_done: bool
    version: int


class ServerInfoOut(Schema):
    """Read-only server config shown in Settings (no secrets, no Telegram ids)."""

    ai_base_url: str
    ai_model: str
    #: AI_ENABLED and an API key is set (false → only the rule-based parser)
    ai_configured: bool
    telegram_bot_configured: bool
    #: TELEGRAM_BOT_USERNAME without «@» (null → the web shows no bot link)
    telegram_bot_username: str | None = None
    version: str
    public_url: str


class SettingsPatch(StrictInput):
    version: int
    timezone: str | None = Field(default=None, max_length=64)
    default_currency: CurrencyCode | None = None
    analytics_currency: CurrencyCode | None = None
    summary_currency: CurrencyCode | None = None
    small_amount_thousands: bool | None = None
    theme: Literal["light", "dark", "system"] | None = None
    desktop_hotkey: str | None = Field(default=None, max_length=64)
    telegram_user_id: int | None = None
    report_monthly: bool | None = None
    report_daily: bool | None = None
    report_weekly: bool | None = None
    report_time: time | None = None
    report_daily_time: time | None = None
    report_weekly_day: int | None = Field(default=None, ge=0, le=6)
    report_weekly_time: time | None = None
    reminders_enabled: bool | None = None
    debt_reminders_enabled: bool | None = None
    debt_reminder_days: int | None = Field(default=None, ge=1, le=365)
    onboarding_done: bool | None = None

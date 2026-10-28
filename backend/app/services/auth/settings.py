"""User settings service. Contract: API.md §2 Settings, ARCH §3.1.

``patch_settings`` is forward-compatible: any field of ``SettingsPatch`` that exists as a column
on ``UserSettings`` is applied (so new report-schedule columns work as soon as the model and
schema get them).
"""

from __future__ import annotations

from datetime import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed, VersionConflict
from app.models import UserSettings
from app.schemas.settings import ServerInfoOut, SettingsOut, SettingsPatch

#: Defaults of a fresh owner (VND input, USD analytics, Asia/Ho_Chi_Minh, monthly
#: report on the 1st at 10:00, daily/weekly off). Extra keys are applied only if the column
#: exists (daily 22:00, weekly Sunday 21:00, reminders on).
DEFAULTS: dict[str, Any] = {
    "timezone": "Asia/Ho_Chi_Minh",
    "default_currency": "VND",
    "analytics_currency": "USD",
    "summary_currency": "VND",
    "small_amount_thousands": True,
    "theme": "system",
    "desktop_hotkey": "Ctrl+Alt+Space",
    "report_monthly": True,
    "report_daily": False,
    "report_weekly": False,
    "report_time": time(10, 0),
    "onboarding_done": False,
    # forward-compatible (not in the model yet — see report to ledger/architect):
    "report_daily_time": time(22, 0),
    "report_weekly_day": 6,  # 0 = Monday … 6 = Sunday
    "report_weekly_time": time(21, 0),
    "reminders_enabled": True,
    "debt_reminders_enabled": False,  # docs/DEBTS.md §9
    "debt_reminder_days": 30,
}

SUMMARY_CURRENCIES = frozenset({"VND", "USD"})
HOTKEY_MODIFIERS = frozenset(
    {
        "ctrl",
        "control",
        "alt",
        "altgr",
        "shift",
        "cmd",
        "command",
        "commandorcontrol",
        "cmdorctrl",
        "super",
        "meta",
        "option",
    }
)


def _has_column(name: str) -> bool:
    return name in UserSettings.__table__.columns


def new_settings_row(user_id: int, **overrides: Any) -> UserSettings:
    values = {k: v for k, v in DEFAULTS.items() if _has_column(k)}
    values["timezone"] = get_settings().default_timezone or values["timezone"]
    values.update({k: v for k, v in overrides.items() if _has_column(k)})
    return UserSettings(user_id=user_id, version=1, **values)


async def load_settings(
    session: AsyncSession, user_id: int, *, for_update: bool = False
) -> UserSettings | None:
    """Fresh row (populate_existing: server defaults of a just-flushed row are loaded).

    ``for_update=True`` takes a row lock (``SELECT … FOR UPDATE``) until the caller's commit, so
    concurrent PATCHes of the settings are serialized and the version check is atomic."""
    stmt = (
        select(UserSettings)
        .where(UserSettings.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_or_create_settings(session: AsyncSession, user_id: int) -> UserSettings:
    st = await load_settings(session, user_id)
    if st is None:
        st = new_settings_row(user_id)
        session.add(st)
        await session.flush()
        st = await load_settings(session, user_id)
        assert st is not None
    return st


def _validate(field: str, value: Any) -> Any:
    if field == "timezone":
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValidationFailed("Неизвестный часовой пояс", details={"field": "timezone"}) from e
    elif field in ("analytics_currency", "summary_currency"):
        if value not in SUMMARY_CURRENCIES:
            raise ValidationFailed(
                "Сводки и аналитика бывают только в ₫ или $", details={"field": field}
            )
    elif field == "desktop_hotkey":
        parts = [p.strip() for p in str(value).split("+")]
        if (
            len(parts) < 2
            or any(not p for p in parts)
            or any(p.lower() not in HOTKEY_MODIFIERS for p in parts[:-1])
            or len(parts[-1]) > 16
            or parts[-1].lower() in HOTKEY_MODIFIERS
        ):
            raise ValidationFailed(
                "Сочетание клавиш вида Ctrl+Alt+Space", details={"field": "desktop_hotkey"}
            )
    elif field == "telegram_user_id":
        if value is not None and value not in get_settings().telegram_allowed_ids:
            raise ValidationFailed(
                "Этот Telegram ID не входит в разрешённые", details={"field": field}
            )
    elif field == "report_weekly_day":
        if not 0 <= int(value) <= 6:
            raise ValidationFailed("День недели 0–6 (пн–вс)", details={"field": field})
    return value


async def patch_settings(
    session: AsyncSession,
    user_id: int,
    patch: SettingsPatch,
    *,
    source: str | None = None,
) -> UserSettings:
    """Apply a partial update with optimistic locking (409 version_conflict). Caller commits.

    The row is read ``FOR UPDATE``: two PATCHes with the same stale ``version`` are serialized —
    the second one waits for the first commit, sees the bumped version and gets 409 (no lost
    update). ``source`` (``principal.source``: web|desktop|telegram) goes to the SSE ``change``
    event (API.md §12)."""
    st = await load_settings(session, user_id, for_update=True)
    if st is None:
        raise NotFound("Настройки не найдены")
    if patch.version != st.version:
        raise VersionConflict(details={"current_version": st.version})
    changed = False
    for field in sorted(patch.model_fields_set - {"version"}):
        if not _has_column(field):
            continue
        value = getattr(patch, field)
        if value is None and field != "telegram_user_id":
            continue  # null = "not provided" for non-nullable settings
        value = _validate(field, value)
        if getattr(st, field) == value:
            continue
        if field == "telegram_user_id" and value is not None:
            with session.no_autoflush:
                other = await session.scalar(
                    select(UserSettings.user_id).where(
                        UserSettings.telegram_user_id == value, UserSettings.user_id != user_id
                    )
                )
            if other is not None:
                raise Conflict("Этот Telegram ID уже привязан", code="already_exists")
        setattr(st, field, value)
        changed = True
    if changed:
        st.version = st.version + 1
        await session.flush()
        await _publish(session, user_id, source)
        st = await load_settings(session, user_id)
        assert st is not None
    return st


async def _publish(session: AsyncSession, user_id: int, source: str | None) -> None:
    from app.services.events import bus

    await bus.publish(session, user_id, ["settings"], source=source)


def to_out(st: UserSettings) -> SettingsOut:
    return SettingsOut.model_validate(st)


def server_info() -> ServerInfoOut:
    """GET /settings/server. No Telegram ids: any signed-in user of the install may read it."""
    s = get_settings()
    return ServerInfoOut(
        ai_base_url=s.ai_base_url,
        ai_model=s.ai_model,
        ai_configured=s.ai_enabled and bool(s.ai_api_key),
        telegram_bot_configured=bool(s.bot_service_token),
        # stored without "@" (config.py validator)
        telegram_bot_username=s.telegram_bot_username or None,
        version=s.app_version,
        public_url=s.public_url,
    )

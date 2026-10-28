"""Timezone helpers. All datetimes are tz-aware; user-local dates use the user's timezone."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Asia/Ho_Chi_Minh"


def utcnow() -> datetime:
    return datetime.now(UTC)


def tz(name: str | None) -> ZoneInfo:
    return ZoneInfo(name or DEFAULT_TZ)


def local_now(tz_name: str | None) -> datetime:
    return utcnow().astimezone(tz(tz_name))


def local_today(tz_name: str | None) -> date:
    return local_now(tz_name).date()


def local_date_of(dt: datetime, tz_name: str | None) -> date:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(tz(tz_name)).date()


def occurred_at_for(day: date, tz_name: str | None, now: datetime | None = None) -> datetime:
    """Datetime to store for an operation dated ``day``.

    Today -> current moment; another day -> 12:00 local time of that day.
    """
    z = tz(tz_name)
    now = (now or utcnow()).astimezone(z)
    if day == now.date():
        return now
    return datetime.combine(day, time(12, 0), tzinfo=z)


def month_start(day: date) -> date:
    return day.replace(day=1)


def next_month_start(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)

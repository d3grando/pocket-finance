"""Period presets → inclusive local date ranges + Russian labels (pure, no DB).

Weeks start on Monday. Labels follow DESIGN.md §3.3: «Сегодня», «Октябрь 2026», «20–26 окт»,
«29 сен – 5 окт», «2026». ``today`` is the user's local date (Asia/Ho_Chi_Minh by default).
"""

from __future__ import annotations

import calendar
import functools
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from app.core.errors import ValidationFailed
from app.schemas.common import DateRange, PeriodPreset, PeriodSpec

MONTHS_NOM = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]  # fmt: skip
MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]  # fmt: skip
MONTHS_DAT = [
    "январю", "февралю", "марту", "апрелю", "маю", "июню",
    "июлю", "августу", "сентябрю", "октябрю", "ноябрю", "декабрю",
]  # fmt: skip
MONTHS_PREP = [
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
]  # fmt: skip
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

MAX_SPAN_DAYS = 366 * 30


def _no_overflow[F: Callable[..., Any]](fn: F) -> F:
    """Date arithmetic at the edges of the calendar (year 1 − 1 day, 9999 + 1 month) raises
    OverflowError / ValueError: that is bad input (422), not a server error (500)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (OverflowError, ValueError) as e:
            raise ValidationFailed(
                "Дата вне допустимого диапазона", details={"field": "date_from"}
            ) from e

    return wrapper  # type: ignore[return-value]


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, n: int) -> date:
    """Same day ``n`` months later, clamped to the month length."""
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def month_label(d: date) -> str:
    return f"{MONTHS_NOM[d.month - 1]} {d.year}"


def day_label(d: date, today: date | None = None) -> str:
    """«27 окт», «27 окт 2025» (other year)."""
    s = f"{d.day} {MONTHS_SHORT[d.month - 1]}"
    if today is not None and d.year != today.year:
        s += f" {d.year}"
    return s


def day_label_long(d: date) -> str:
    """«27 октября»."""
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


def range_label(d1: date, d2: date, today: date) -> str:
    """«20–26 окт», «29 сен – 5 окт», «29 дек 2025 – 4 янв 2026», «Октябрь 2026», «2026»."""
    if d1 == d2:
        return day_label(d1, today)
    if d1.day == 1 and d2 == month_end(d1):
        return month_label(d1)
    if d1 == date(d1.year, 1, 1) and d2 == date(d1.year, 12, 31):
        return str(d1.year)
    other_year = d1.year != today.year or d2.year != today.year
    if d1.year == d2.year and d1.month == d2.month:
        s = f"{d1.day}–{d2.day} {MONTHS_SHORT[d1.month - 1]}"
        return s + (f" {d1.year}" if other_year else "")
    if d1.year == d2.year:
        s = f"{day_label(d1)} – {day_label(d2)}"
        return s + (f" {d1.year}" if other_year else "")
    return f"{day_label(d1)} {d1.year} – {day_label(d2)} {d2.year}"


@_no_overflow
def resolve_period(period: PeriodSpec, today: date, *, earliest: date | None = None) -> DateRange:
    """Preset → inclusive dates (local) + label. ``earliest`` is used for all_time."""
    p = PeriodPreset(period.preset)
    if p == PeriodPreset.today:
        return DateRange(date_from=today, date_to=today, label="Сегодня")
    if p == PeriodPreset.yesterday:
        y = today - timedelta(days=1)
        return DateRange(date_from=y, date_to=y, label="Вчера")
    if p in (PeriodPreset.this_week, PeriodPreset.last_week):
        start = today - timedelta(days=today.weekday())
        if p == PeriodPreset.last_week:
            start -= timedelta(days=7)
        end = start + timedelta(days=6)
        return DateRange(date_from=start, date_to=end, label=range_label(start, end, today))
    if p in (PeriodPreset.this_month, PeriodPreset.last_month):
        start = today.replace(day=1)
        if p == PeriodPreset.last_month:
            start = add_months(start, -1)
        return DateRange(date_from=start, date_to=month_end(start), label=month_label(start))
    if p in (PeriodPreset.this_year, PeriodPreset.last_year):
        y = today.year - (1 if p == PeriodPreset.last_year else 0)
        return DateRange(date_from=date(y, 1, 1), date_to=date(y, 12, 31), label=str(y))
    if p in (PeriodPreset.last_7_days, PeriodPreset.last_30_days):
        n = 7 if p == PeriodPreset.last_7_days else 30
        start = today - timedelta(days=n - 1)
        return DateRange(date_from=start, date_to=today, label=f"Последние {n} дней")
    if p == PeriodPreset.all_time:
        start = min(earliest or today, today)
        return DateRange(date_from=start, date_to=today, label="За всё время")
    # custom
    if period.date_from is None or period.date_to is None:
        raise ValidationFailed(
            "Для своего периода укажите обе даты",
            details={"fields": [{"loc": ["query", "date_from"], "msg": "required"}]},
        )
    d1, d2 = period.date_from, period.date_to
    if d1 > d2:
        raise ValidationFailed("Начало периода позже конца", details={"field": "date_from"})
    if (d2 - d1).days > MAX_SPAN_DAYS:
        raise ValidationFailed("Слишком длинный период", details={"field": "date_from"})
    return DateRange(date_from=d1, date_to=d2, label=range_label(d1, d2, today))


def effective_end(rng: DateRange, today: date) -> date:
    """Last day with data: the range end, but not after today (when the range covers today)."""
    if rng.date_from <= today <= rng.date_to:
        return today
    return rng.date_to


def elapsed_days(rng: DateRange, today: date) -> int:
    """Days of the range up to today (whole range if it is in the past; 0 if in the future)."""
    if rng.date_from > today:
        return 0
    return (effective_end(rng, today) - rng.date_from).days + 1


@_no_overflow
def previous_range(period: PeriodSpec, rng: DateRange, today: date) -> DateRange:
    """The preceding comparable period. Month/year/week presets → previous calendar unit;
    when the current one is still running (contains today) the previous one is cut to the same
    elapsed length («1–27 окт» vs «1–27 сен»). Others → shifted back by their length."""
    p = PeriodPreset(period.preset)
    d1, d2 = rng.date_from, rng.date_to
    running = d1 <= today < d2
    span_done = (today - d1).days  # offset of today inside the range
    is_month = d1.day == 1 and d2 == month_end(d1)
    is_year = d1 == date(d1.year, 1, 1) and d2 == date(d1.year, 12, 31)
    if p in (PeriodPreset.this_month, PeriodPreset.last_month) or (
        p == PeriodPreset.custom and is_month
    ):
        s = add_months(d1, -1)
        e = month_end(s)
        if running:
            e = min(e, s + timedelta(days=span_done))
    elif p in (PeriodPreset.this_year, PeriodPreset.last_year) or (
        p == PeriodPreset.custom and is_year
    ):
        s = date(d1.year - 1, 1, 1)
        e = date(d1.year - 1, 12, 31)
        if running:
            e = min(e, s + timedelta(days=span_done))
    elif p in (PeriodPreset.this_week, PeriodPreset.last_week):
        s, e = d1 - timedelta(days=7), d2 - timedelta(days=7)
        if running:
            e = s + timedelta(days=span_done)
    else:
        n = (d2 - d1).days + 1
        e = d1 - timedelta(days=1)
        s = e - timedelta(days=n - 1)
    return DateRange(date_from=s, date_to=e, label=range_label(s, e, today))


def period_phrase(period: PeriodSpec, rng: DateRange, today: date) -> str:
    """Adverbial phrase for answers: «сегодня», «в октябре», «на этой неделе», «за 20–26 окт»."""
    p = PeriodPreset(period.preset)
    if p == PeriodPreset.today:
        return "сегодня"
    if p == PeriodPreset.yesterday:
        return "вчера"
    if p == PeriodPreset.this_week:
        return "на этой неделе"
    if p == PeriodPreset.last_week:
        return "на прошлой неделе"
    d1, d2 = rng.date_from, rng.date_to
    if d1.day == 1 and d2 == month_end(d1):
        s = f"в {MONTHS_PREP[d1.month - 1]}"
        return s + (f" {d1.year}" if d1.year != today.year else "")
    if d1 == date(d1.year, 1, 1) and d2 == date(d1.year, 12, 31):
        return f"в {d1.year} году"
    if p == PeriodPreset.last_7_days:
        return "за последние 7 дней"
    if p == PeriodPreset.last_30_days:
        return "за последние 30 дней"
    if p == PeriodPreset.all_time:
        return "за всё время"
    if d1 == d2:
        return f"{d1.day} {MONTHS_GEN[d1.month - 1]}"
    return f"за {rng.label}"


def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(5, 'операция', 'операции', 'операций') → 'операций'."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def analytics_route(period: PeriodSpec, rng: DateRange) -> str:
    p = PeriodPreset(period.preset)
    if p == PeriodPreset.custom:
        return f"/analytics?period=custom&from={rng.date_from}&to={rng.date_to}"
    return f"/analytics?period={p.value}"

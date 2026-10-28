"""resolve_date / find_dates / periods. Today = Tue 2026-10-27 (never system clock)."""

from __future__ import annotations

from datetime import date

import pytest

from app.schemas.common import PeriodPreset
from app.services.parser.dates import find_dates, resolve_date, weekday_date
from app.services.parser.periods import find_periods, last_weekend

TODAY = date(2026, 10, 27)

CASES = [
    ("сегодня", "2026-10-27"), ("вчера", "2026-10-26"), ("позавчера", "2026-10-25"),
    ("позапозавчера", "2026-10-24"), ("today", "2026-10-27"), ("yesterday", "2026-10-26"),
    ("Вчера", "2026-10-26"),
    ("в пятницу", "2026-10-23"), ("в прошлую пятницу", "2026-10-23"),
    ("в понедельник", "2026-10-26"), ("в прошлый понедельник", "2026-10-19"),
    ("во вторник", "2026-10-20"), ("в среду", "2026-10-21"), ("в четверг", "2026-10-22"),
    ("в субботу", "2026-10-24"), ("в воскресенье", "2026-10-25"),
    ("в прошлое воскресенье", "2026-10-25"), ("в позапрошлую пятницу", "2026-10-16"),
    ("в пт", "2026-10-23"), ("last friday", "2026-10-23"), ("on monday", "2026-10-26"),
    ("3 дня назад", "2026-10-24"), ("день назад", "2026-10-26"),
    ("неделю назад", "2026-10-20"), ("2 недели назад", "2026-10-13"),
    ("пару дней назад", "2026-10-25"), ("месяц назад", "2026-09-27"),
    ("5 days ago", "2026-10-22"),
    ("24.10", "2026-10-24"), ("24/10", "2026-10-24"), ("01.10.2026", "2026-10-01"),
    ("1.10.26", "2026-10-01"), ("24 октября", "2026-10-24"), ("24 окт", "2026-10-24"),
    ("1-го октября", "2026-10-01"), ("15 сентября 2025", "2025-09-15"),
    ("31.12", "2025-12-31"), ("october 24", "2026-10-24"),
    ("29 декабря", "2025-12-29"),
    ("за вчера", "2026-10-26"),
]


@pytest.mark.parametrize("token,expected", CASES)
def test_resolve_date(token: str, expected: str) -> None:
    assert resolve_date(token, TODAY) == date.fromisoformat(expected)


@pytest.mark.parametrize("token", ["завтра", "на выходных", "кофе", "", "32.10", "30.02",
                                   "1.5м", "1 ноября", "30.10", "15.12", "november 1",
                                   "на прошлой неделе", "в прошлом месяце"])
def test_resolve_date_none(token: str) -> None:
    assert resolve_date(token, TODAY) is None


def test_weekend_is_flagged() -> None:
    hits = find_dates("кофе на выходных 85к", TODAY)
    assert [h.kind for h in hits] == ["weekend"]


def test_future_is_flagged() -> None:
    assert [h.kind for h in find_dates("завтра кофе", TODAY)] == ["future"]


def test_bare_dmy_needs_other_number() -> None:
    assert find_dates("такси 24.10", TODAY, other_numbers=False) == []
    assert find_dates("такси 24.10", TODAY, other_numbers=True)[0].value == date(2026, 10, 24)


@pytest.mark.parametrize("today", [date(2026, 10, d) for d in range(19, 26)])
def test_weekday_never_future(today: date) -> None:
    for wd in range(7):
        assert weekday_date(wd, today) < today
        assert weekday_date(wd, today, "прошлую") < today


PERIODS = [
    ("сегодня", "today"), ("за вчера", "yesterday"), ("на этой неделе", "this_week"),
    ("за неделю", "last_7_days"), ("за последнюю неделю", "last_7_days"),
    ("за прошлую неделю", "last_week"), ("за месяц", "this_month"),
    ("в этом месяце", "this_month"), ("в прошлом месяце", "last_month"),
    ("за последние 30 дней", "last_30_days"), ("за год", "this_year"),
    ("в прошлом году", "last_year"), ("за всё время", "all_time"), ("last week", "last_week"),
    ("this month", "this_month"),
]


@pytest.mark.parametrize("text,preset", PERIODS)
def test_period_presets(text: str, preset: str) -> None:
    hits = find_periods(text, TODAY)
    assert hits and hits[0].period.preset == PeriodPreset(preset)


CUSTOM = [
    ("за октябрь", "2026-10-01", "2026-10-31"), ("в сентябре", "2026-09-01", "2026-09-30"),
    ("за ноябрь", "2025-11-01", "2025-11-30"), ("за март 2025", "2025-03-01", "2025-03-31"),
    ("с 1 по 15 октября", "2026-10-01", "2026-10-15"),
    ("с 01.10 по 10.10", "2026-10-01", "2026-10-10"),
    ("за последние 10 дней", "2026-10-18", "2026-10-27"),
    ("за 3 дня", "2026-10-25", "2026-10-27"),
    ("на выходных", "2026-10-24", "2026-10-25"), ("за 2025 год", "2025-01-01", "2025-12-31"),
    ("позавчера", "2026-10-25", "2026-10-25"), ("в прошлую пятницу", "2026-10-23", "2026-10-23"),
    ("24.10", "2026-10-24", "2026-10-24"),
]


@pytest.mark.parametrize("text,a,b", CUSTOM)
def test_period_custom(text: str, a: str, b: str) -> None:
    p = find_periods(text, TODAY)[0].period
    assert p.preset == PeriodPreset.custom
    assert (p.date_from, p.date_to) == (date.fromisoformat(a), date.fromisoformat(b))


def test_last_weekend() -> None:
    assert last_weekend(date(2026, 10, 27)) == (date(2026, 10, 24), date(2026, 10, 25))
    assert last_weekend(date(2026, 10, 24)) == (date(2026, 10, 24), date(2026, 10, 25))
    assert last_weekend(date(2026, 10, 25)) == (date(2026, 10, 24), date(2026, 10, 25))

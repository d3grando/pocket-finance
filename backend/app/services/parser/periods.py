"""Periods for stats/search questions: «сегодня», «за неделю»,
«в прошлом месяце», «за октябрь», «с 1 по 15 октября», «за последние 10 дней», «на выходных»…
Pure functions; ``today`` is the user-local date."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.schemas.common import PeriodPreset, PeriodSpec
from app.services.parser.dates import (
    _MONTH_RE_ANY,
    _MONTH_RE_FULL,
    DateHit,
    _month_of,
    find_dates,
)
from app.services.parser.text import normalize

_B = r"(?<![\w])"
_E = r"(?![\w])"


@dataclass(slots=True)
class PeriodHit:
    start: int
    end: int
    period: PeriodSpec


def _custom(a: date, b: date) -> PeriodSpec:
    if b < a:
        a, b = b, a
    return PeriodSpec(preset=PeriodPreset.custom, date_from=a, date_to=b)


def _month_period(month: int, year: int | None, today: date) -> PeriodSpec:
    y = year if year else (today.year if month <= today.month else today.year - 1)
    last = calendar.monthrange(y, month)[1]
    return _custom(date(y, month, 1), date(y, month, last))


def last_weekend(today: date) -> tuple[date, date]:
    """Current weekend if today is Sat/Sun, otherwise the previous one."""
    wd = today.weekday()
    if wd == 5:
        return today, today + timedelta(days=1)
    if wd == 6:
        return today - timedelta(days=1), today
    sat = today - timedelta(days=wd + 2)
    return sat, sat + timedelta(days=1)


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(_B + "(?:" + pattern + ")" + _E)


_PRESETS: list[tuple[re.Pattern[str], PeriodPreset]] = [
    (_rx(r"(?:за\s+)?(?:все|всю)\s+(?:время|историю)|all\s+time|ever"),
     PeriodPreset.all_time),
    (_rx(r"(?:(?:на|за)\s+)?(?:эт(?:ой|у|а)|текущ(?:ей|ую|ая))\s+недел[еюяи]|"
                r"this\s+week|с\s+начала\s+недели"), PeriodPreset.this_week),
    (_rx(r"(?:(?:на|за)\s+)?прошл(?:ой|ую|ая)\s+недел[еюяи]|last\s+week|"
                r"previous\s+week"), PeriodPreset.last_week),
    (_rx(r"за\s+(?:последн(?:юю|ие)\s+)?(?:недел[юи]|7\s+дней|семь\s+дней)|"
                r"(?:last|past)\s+(?:7\s+days|week)|за\s+7\s+дн(?:ей)?"),
     PeriodPreset.last_7_days),
    (_rx(r"за\s+(?:последни[ей]\s+)?(?:30|тридцать)\s+дней|за\s+последний\s+месяц|"
                r"(?:last|past)\s+(?:30\s+days|month)(?!\w)"), PeriodPreset.last_30_days),
    (_rx(r"(?:(?:в|за)\s+)?прошл(?:ом|ый|ого)\s+месяц[ае]?|last\s+month|"
                r"previous\s+month"), PeriodPreset.last_month),
    (_rx(r"(?:(?:в|за)\s+)?(?:это(?:м|т|го)|текущ(?:ий|ем|его))\s+месяц[ае]?|"
                r"за\s+месяц|this\s+month|с\s+начала\s+месяца|month\s+to\s+date"),
     PeriodPreset.this_month),
    (_rx(r"(?:(?:в|за)\s+)?прошл(?:ом|ый|ого)\s+год[ау]?|last\s+year"),
     PeriodPreset.last_year),
    (_rx(r"(?:(?:в|за)\s+)?(?:это(?:м|т|го)|текущ(?:ий|ем|его))\s+год[ау]?|"
                r"за\s+год|this\s+year|с\s+начала\s+года"), PeriodPreset.this_year),
    (_rx(r"(?:за\s+)?сегодня|today|за\s+день|сегодняшн\w*"),
     PeriodPreset.today),
    (_rx(r"(?:за\s+)?вчера|yesterday|вчерашн\w*"), PeriodPreset.yesterday),
]
_LAST_N = re.compile(
    _B + r"(?:за\s+)?(?:последни[ей]\s+|last\s+|past\s+)?(?P<n>\d{1,3})\s+(?P<u>дн(?:я|ей)|день|"
    r"days?|недел[иь]|weeks?|месяц(?:а|ев)?|months?)" + _E
)
_WEEKEND = re.compile(
    _B + r"(?:(?:на|в|за|on|over)\s+)?(?:(?:прошлых|этих|the|last|this)\s+)?"
    r"(?:выходн(?:ых|ые)|weekend)" + _E
)
_RANGE = re.compile(
    _B + r"с\s+(?P<d1>[0-3]?\d)(?:[./](?P<m1>[01]?\d))?(?:\s+(?P<mw1>" + _MONTH_RE_ANY
    + r"))?\s+(?:по|до|-)\s+(?P<d2>[0-3]?\d)(?:[./](?P<m2>[01]?\d))?(?:\s+(?P<mw2>"
    + _MONTH_RE_ANY + r"))?" + _E
)
_MONTH = re.compile(
    _B + r"(?:(?:за|в|во|на|for|in)\s+)?(?P<m>" + _MONTH_RE_FULL
    + r")(?:\s+(?P<y>(?:19|20)\d{2}))?(?:\s*(?:г\.|года|году|г(?![\w])))?" + _E
)
_YEAR = re.compile(
    _B + r"(?:за|в|in|for)\s+(?P<y>(?:19|20)\d{2})(?:\s*(?:год[ау]?|г\.|г(?![\w])))?" + _E
)


def find_periods(text: str, today: date) -> list[PeriodHit]:
    """All period mentions (non-overlapping, by position). Offsets refer to ``text``."""
    norm = normalize(text)
    hits: list[PeriodHit] = []

    def add(s: int, e: int, p: PeriodSpec) -> None:
        for h in hits:
            if s < h.end and h.start < e:
                return
        hits.append(PeriodHit(s, e, p))

    for m in _RANGE.finditer(norm):
        mon2 = int(m.group("m2")) if m.group("m2") else (
            _month_of(m.group("mw2")) if m.group("mw2") else None)
        mon1 = int(m.group("m1")) if m.group("m1") else (
            _month_of(m.group("mw1")) if m.group("mw1") else mon2)
        mon2 = mon2 or mon1 or today.month
        mon1 = mon1 or mon2
        try:
            a = date(today.year, mon1, int(m.group("d1")))
            b = date(today.year, mon2, int(m.group("d2")))
        except ValueError:
            continue
        if a > today:
            a, b = a.replace(year=a.year - 1), b.replace(year=b.year - 1)
        add(m.start(), m.end(), _custom(a, b))
    for m in _WEEKEND.finditer(norm):
        a, b = last_weekend(today)
        if m.group(0).startswith(("за прошлых", "на прошлых", "в прошлых")) or "last" in m.group(0):
            if today.weekday() >= 5:
                a, b = a - timedelta(days=7), b - timedelta(days=7)
        add(m.start(), m.end(), _custom(a, b))
    for rx, preset in _PRESETS:
        for m in rx.finditer(norm):
            add(m.start(), m.end(), PeriodSpec(preset=preset))
    for m in _LAST_N.finditer(norm):
        n = int(m.group("n"))
        if n <= 0:
            continue
        u = m.group("u")
        if u.startswith(("недел", "week")):
            start = today - timedelta(days=7 * n - 1)
        elif u.startswith(("месяц", "month")):
            start = today - timedelta(days=30 * n - 1)
        else:
            start = today - timedelta(days=n - 1)
        add(m.start(), m.end(), _custom(start, today))
    # single days before whole months: «сколько потратил 26 октября» is that day, not October
    for h in find_dates(norm, today, other_numbers=True):
        day = _past_day(h, today)
        if day is not None:
            add(h.start, h.end, _custom(day, day))
    for m in _MONTH.finditer(norm):
        mon = _month_of(m.group("m"))
        if mon:
            y = int(m.group("y")) if m.group("y") else None
            add(m.start(), m.end(), _month_period(mon, y, today))
    for m in _YEAR.finditer(norm):
        y = int(m.group("y"))
        if 1990 <= y <= today.year:
            add(m.start(), m.end(), _custom(date(y, 1, 1), date(y, 12, 31) if y < today.year
                                            else today))
    hits.sort(key=lambda h: h.start)
    return hits


def _past_day(h: DateHit, today: date) -> date | None:
    """A day for a stats period: past dates as is; a near-future day without a year («сколько
    потратил 1 ноября» on 27.10) is last year's."""
    if h.value is None:
        return None
    if h.kind == "date":
        return h.value
    if h.kind == "future" and h.src in ("dm", "md", "dmy") and h.value > today:
        try:
            prev = h.value.replace(year=h.value.year - 1)
        except ValueError:
            return None
        return prev
    return None

"""Relative dates: сегодня, вчера, позавчера, в пятницу / в прошлую пятницу
(the most recent past Friday, never in the future), «3 дня назад», «24.10», «24 октября»,
yesterday, «last friday», «на выходных» (ambiguous → flag), periods for stats.

All functions are pure: ``today`` is passed in (user-local date, Asia/Ho_Chi_Minh by default).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, time, timedelta

from app.services.parser.text import normalize

_B = r"(?<![\w])"  # left word boundary (unicode)
_E = r"(?![\w])"  # right word boundary

WEEKDAY_FORMS: dict[str, int] = {}
for _i, _forms in enumerate(
    [
        ("понедельник", "понедельника", "пн", "monday", "mon"),
        ("вторник", "вторника", "вт", "tuesday", "tue"),
        ("среда", "среду", "среды", "ср", "wednesday", "wed"),
        ("четверг", "четверга", "чт", "thursday", "thu"),
        ("пятница", "пятницу", "пятницы", "пт", "friday", "fri"),
        ("суббота", "субботу", "субботы", "сб", "saturday", "sat"),
        ("воскресенье", "воскресенья", "воскресение", "вс", "sunday", "sun"),
    ]
):
    for _f in _forms:
        WEEKDAY_FORMS[_f] = _i
_SHORT_WD = {"пн", "вт", "ср", "чт", "пт", "сб", "вс", "mon", "tue", "wed", "thu", "fri", "sat",
             "sun"}

_MONTH_FULL = [
    (r"январ[ьяеюи]|january", 1), (r"феврал[ьяеюи]|february", 2), (r"март[ауе]?|march", 3),
    (r"апрел[ьяеюи]|april", 4), (r"ма[йяею]|may", 5), (r"июн[ьяеюи]|june", 6),
    (r"июл[ьяеюи]|july", 7), (r"август[ауе]?|august", 8), (r"сентябр[ьяеюи]|september", 9),
    (r"октябр[ьяеюи]|october", 10), (r"ноябр[ьяеюи]|november", 11),
    (r"декабр[ьяеюи]|december", 12),
]
_MONTH_ABBR = [
    (r"янв|jan", 1), (r"фев|feb", 2), (r"мар|mar", 3), (r"апр|apr", 4), (r"июн|jun", 6),
    (r"июл|jul", 7), (r"авг|aug", 8), (r"сент|сен|sept|sep", 9), (r"окт|oct", 10),
    (r"нояб|ноя|nov", 11), (r"дек|dec", 12),
]


def _month_of(word: str) -> int | None:
    w = word.rstrip(".")
    for pat, n in _MONTH_FULL:
        if re.fullmatch(pat, w):
            return n
    for pat, n in _MONTH_ABBR:
        if re.fullmatch(pat, w):
            return n
    return None


_MONTH_RE_FULL = "|".join(p for p, _ in _MONTH_FULL)
_MONTH_RE_ANY = _MONTH_RE_FULL + "|" + "|".join(p for p, _ in _MONTH_ABBR)
_WD_RE = "|".join(sorted(WEEKDAY_FORMS, key=len, reverse=True))
_NUM_WORDS = {"один": 1, "одну": 1, "один-два": 2, "два": 2, "две": 2, "пару": 2, "пара": 2,
              "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
              "девять": 9, "десять": 10, "несколько": 3}


@dataclass(slots=True)
class DateHit:
    start: int
    end: int
    kind: str  # date | weekend | future | vague (a period, not a day: «на прошлой неделе»)
    value: date | None = None
    # rel | ago | wd | weekend | vague | dm / md / dmy (day+month without a year) | full (with year)
    src: str = ""


# A day written without a year that is at most this many days ahead is a future date
# («1 ноября» on 27.10 → 01.11 this year, flagged future); further ahead it is last year's
# («31.12» in October → 31.12 of the previous year).
NEAR_FUTURE_DAYS = 60


def _shift_month(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _year_for(month: int, day: int, today: date) -> date | None:
    """Year for a day/month without a year: this year if past/today/near future (the caller
    flags a future one), otherwise the previous year."""
    try:
        d = date(today.year, month, day)
    except ValueError:
        try:  # 29.02 of a non-leap year: only the previous year can have it
            d = date(today.year - 1, month, day)
        except ValueError:
            return None
        return d if d <= today else None
    if d > today + timedelta(days=1) and (d - today).days > NEAR_FUTURE_DAYS:
        try:
            d = date(today.year - 1, month, day)
        except ValueError:
            return None
    return d


def _kind_of(d: date, today: date) -> str:
    return "date" if d <= today + timedelta(days=1) else "future"


def weekday_date(wd: int, today: date, modifier: str | None = None) -> date:
    """«в пятницу» → most recent Friday strictly before today; «в прошлую пятницу» → Friday of
    the previous calendar week (Mon–Sun); «в эту пятницу» → this week's Friday (may be future)."""
    mod = (modifier or "").strip()
    monday = today - timedelta(days=today.weekday())
    if mod.startswith("позапрошл"):
        return monday - timedelta(days=14) + timedelta(days=wd)
    if mod.startswith("прошл") or mod == "last":
        return monday - timedelta(days=7) + timedelta(days=wd)
    if mod.startswith("эт") or mod == "this":
        return monday + timedelta(days=wd)
    delta = (today.weekday() - wd) % 7
    return today - timedelta(days=delta or 7)


_REL = re.compile(
    _B + r"(?:(?:за|на)\s+)?(?P<w>позапозавчера|позавчера|вчера|сегодня|today|yesterday|"
    r"day\s+before\s+yesterday|завтра|послезавтра|tomorrow)" + _E
)
_WD = re.compile(
    _B + r"(?:(?P<prep>в|во|on)\s+)?(?:(?P<mod>позапрошл(?:ый|ую|ое|ой)|прошл(?:ый|ую|ое|ой)|"
    r"эт(?:от|у|о)|last|this)\s+)?(?P<wd>" + _WD_RE + r")" + _E
)
_AGO = re.compile(
    _B + r"(?:(?P<n>\d{1,3}|" + "|".join(_NUM_WORDS) + r")\s+)?(?P<u>дн(?:я|ей)|день|"
    r"недел(?:ю|и|ь)|месяц(?:а|ев)?|days?|weeks?|months?)\s+(?:назад|ago)" + _E
)
_DAY_MONTH = re.compile(
    r"(?<![\d.,\w])(?:(?:от|за|на)\s+)?(?P<d>[0-3]?\d)(?:-?(?:го|е|ого))?\s+(?P<m>"
    + _MONTH_RE_ANY
    + r")\.?(?:\s+(?P<y>(?:19|20)\d{2}))?(?:\s*(?:г\.|года|году|г(?![\w])))?" + _E
)
_MONTH_DAY_EN = re.compile(
    _B + r"(?:on\s+)?(?P<m>" + _MONTH_RE_ANY + r")\.?\s+(?P<d>[0-3]?\d)(?:st|nd|rd|th)?" + _E
)
_DMY = re.compile(
    r"(?<![\d.,/\w])(?:(?P<prep>от|за|на|с|по|до)\s+)?(?P<d>[0-3]?\d)[./](?P<m>[01]?\d)"
    r"(?:[./](?P<y>\d{4}|\d{2}))?(?![\d,/]|\.\d|[^\W\d_])"
)
_WEEKEND = re.compile(
    _B + r"(?:(?:на|в|за|on|at|over)\s+)?(?:(?:прошлых|этих|the|last|this)\s+)?"
    r"(?:выходн(?:ых|ые|ой)|weekend)" + _E
)
# A period instead of a day in an operation («на прошлой неделе такси 50к»): never «today».
_VAGUE = re.compile(
    _B + r"(?:(?:на|в|во)\s+(?P<wm>позапрошл(?:ой|ую)|прошл(?:ой|ую)|эт(?:ой|у)|текущ(?:ей|ую))"
    r"\s+недел[еию]|(?:в|во)\s+(?P<mm>позапрошл(?:ом|ый)|прошл(?:ом|ый)|эт(?:ом|от)|"
    r"текущ(?:ем|ий))\s+месяц[еа]?|(?P<days>на\s+днях)|(?P<en>(?:last|this)\s+(?:week|month)))"
    + _E
)
TIME_RE = re.compile(
    r"(?<![\d.,:])(?:(?:в|at)\s+)?(?P<h>[01]?\d|2[0-3]):(?P<mi>[0-5]\d)(?![\d:])"
)
_AFTER_NUM_MONEY = re.compile(r"^\s?(?:[кkмm](?![\w])|[$₫₽€đ]|тыс|млн|usd|usdt|руб|дол|бакс)")


def find_dates(norm: str, today: date, *, other_numbers: bool = True) -> list[DateHit]:
    """All date mentions in normalised text (non-overlapping, by position).

    ``other_numbers``: the text has another amount-like number, so a bare «24.10» is a date
    (otherwise it is left for the amount parser).
    """
    hits: list[DateHit] = []

    def add(s: int, e: int, kind: str, value: date | None, src: str) -> None:
        for h in hits:
            if s < h.end and h.start < e:
                return
        hits.append(DateHit(s, e, kind, value, src))

    for m in _WEEKEND.finditer(norm):
        add(m.start(), m.end(), "weekend", None, "weekend")
    for m in _VAGUE.finditer(norm):
        add(m.start(), m.end(), "vague", _vague_start(m, today), "vague")
    for m in _REL.finditer(norm):
        w = re.sub(r"\s+", " ", m.group("w"))
        if w in ("завтра", "послезавтра", "tomorrow"):
            add(m.start(), m.end(), "future", today + timedelta(days=1 if w != "послезавтра"
                                                                 else 2), "rel")
            continue
        back = {"сегодня": 0, "today": 0, "вчера": 1, "yesterday": 1, "позавчера": 2,
                "day before yesterday": 2, "позапозавчера": 3}[w]
        add(m.start(), m.end(), "date", today - timedelta(days=back), "rel")
    for m in _AGO.finditer(norm):
        n_raw = m.group("n")
        n = int(n_raw) if n_raw and n_raw.isdigit() else _NUM_WORDS.get(n_raw or "", 1)
        u = m.group("u")
        if u.startswith(("недел", "week")):
            d = today - timedelta(days=7 * n)
        elif u.startswith(("месяц", "month")):
            d = _shift_month(today, -n)
        else:
            d = today - timedelta(days=n)
        add(m.start(), m.end(), "date", d, "ago")
    for m in _DAY_MONTH.finditer(norm):
        mon = _month_of(m.group("m"))
        if not mon:
            continue
        day = int(m.group("d"))
        if m.group("y"):
            try:
                d2: date | None = date(int(m.group("y")), mon, day)
            except ValueError:
                d2 = None
        else:
            d2 = _year_for(mon, day, today)
        if d2:
            add(m.start(), m.end(), _kind_of(d2, today), d2, "full" if m.group("y") else "dm")
    for m in _MONTH_DAY_EN.finditer(norm):
        mon = _month_of(m.group("m"))
        if mon and m.group("m") not in ("may", "mar"):
            d3 = _year_for(mon, int(m.group("d")), today)
            if d3:
                add(m.start(), m.end(), _kind_of(d3, today), d3, "md")
    for m in _DMY.finditer(norm):
        if _AFTER_NUM_MONEY.match(norm[m.end():]):
            continue
        if not (m.group("y") or m.group("prep") or other_numbers):
            continue
        day, mon = int(m.group("d")), int(m.group("m"))
        if not (1 <= day <= 31 and 1 <= mon <= 12):
            continue
        if m.group("y"):
            y = int(m.group("y"))
            y = y + 2000 if y < 100 else y
            try:
                d4: date | None = date(y, mon, day)
            except ValueError:
                d4 = None
        else:
            d4 = _year_for(mon, day, today)
        if d4:
            add(m.start(), m.end(), _kind_of(d4, today), d4, "full" if m.group("y") else "dmy")
    for m in _WD.finditer(norm):
        wd_word = m.group("wd")
        if not (m.group("prep") or m.group("mod")):
            continue
        if wd_word in _SHORT_WD and not m.group("prep"):
            continue
        wd = WEEKDAY_FORMS[wd_word]
        d5 = weekday_date(wd, today, m.group("mod"))
        add(m.start(), m.end(), "date" if d5 <= today else "future", d5, "wd")
    hits.sort(key=lambda h: h.start)
    return hits


def _vague_start(m: re.Match[str], today: date) -> date:
    """First day of the period named by a vague phrase (an example for the clarify)."""
    monday = today - timedelta(days=today.weekday())
    word = m.group("wm") or m.group("mm") or m.group("en") or ""
    if m.group("days"):
        return today - timedelta(days=2)
    if m.group("wm") or word.endswith("week"):
        if word.startswith("позапрошл"):
            return monday - timedelta(days=14)
        if word.startswith(("прошл", "last")):
            return monday - timedelta(days=7)
        return monday
    first = today.replace(day=1)
    if word.startswith("позапрошл"):
        return _shift_month(first, -2)
    if word.startswith(("прошл", "last")):
        return _shift_month(first, -1)
    return first


def find_time(norm: str) -> tuple[time, int, int] | None:
    m = TIME_RE.search(norm)
    if not m:
        return None
    return time(int(m.group("h")), int(m.group("mi"))), m.start(), m.end()


def resolve_date(token: str, today: date) -> date | None:
    """Concrete date for a date expression («вчера», «в прошлую пятницу», «3 дня назад»,
    «24.10», «24 октября»); None if the token is not a (past or today) date."""
    norm = normalize(token).strip()
    if not norm:
        return None
    hits = find_dates(norm, today, other_numbers=True)
    for h in hits:
        if h.kind == "date" and h.value is not None:
            return h.value
    return None

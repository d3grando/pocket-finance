"""Periods, labels and day boundaries in Asia/Ho_Chi_Minh (UTC+7) while the server runs in UTC."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.core.errors import ValidationFailed
from app.schemas.common import PeriodPreset, PeriodSpec
from app.services.stats import periods as PR
from app.services.stats import service as S
from tests.analytics.conftest import at

TODAY = date(2026, 10, 27)  # Tuesday


@pytest.mark.parametrize(
    ("preset", "d1", "d2", "label"),
    [
        ("today", date(2026, 10, 27), date(2026, 10, 27), "Сегодня"),
        ("yesterday", date(2026, 10, 26), date(2026, 10, 26), "Вчера"),
        ("this_week", date(2026, 10, 26), date(2026, 11, 1), "26 окт – 1 ноя"),
        ("last_week", date(2026, 10, 19), date(2026, 10, 25), "19–25 окт"),
        ("this_month", date(2026, 10, 1), date(2026, 10, 31), "Октябрь 2026"),
        ("last_month", date(2026, 9, 1), date(2026, 9, 30), "Сентябрь 2026"),
        ("this_year", date(2026, 1, 1), date(2026, 12, 31), "2026"),
        ("last_year", date(2025, 1, 1), date(2025, 12, 31), "2025"),
        ("last_7_days", date(2026, 10, 21), date(2026, 10, 27), "Последние 7 дней"),
        ("last_30_days", date(2026, 9, 28), date(2026, 10, 27), "Последние 30 дней"),
    ],
)
def test_presets(preset, d1, d2, label):
    r = PR.resolve_period(PeriodSpec(preset=preset), TODAY)
    assert (r.date_from, r.date_to, r.label) == (d1, d2, label)


def test_custom_and_labels():
    c = PR.resolve_period(
        PeriodSpec(preset="custom", date_from=date(2025, 12, 29), date_to=date(2026, 1, 4)), TODAY
    )
    assert c.label == "29 дек 2025 – 4 янв 2026"
    assert PR.range_label(date(2026, 10, 1), date(2026, 10, 27), TODAY) == "1–27 окт"
    assert PR.range_label(date(2025, 10, 1), date(2025, 10, 5), TODAY) == "1–5 окт 2025"
    assert PR.range_label(date(2026, 2, 1), date(2026, 2, 28), TODAY) == "Февраль 2026"
    with pytest.raises(ValidationFailed):
        PR.resolve_period(PeriodSpec(preset="custom", date_from=date(2026, 1, 2)), TODAY)
    with pytest.raises(ValidationFailed):
        PR.resolve_period(
            PeriodSpec(preset="custom", date_from=date(2026, 2, 2), date_to=date(2026, 1, 1)),
            TODAY,
        )
    a = PR.resolve_period(PeriodSpec(preset="all_time"), TODAY, earliest=date(2026, 3, 5))
    assert (a.date_from, a.date_to, a.label) == (date(2026, 3, 5), TODAY, "За всё время")


def test_previous_ranges():
    m = PeriodSpec(preset="this_month")
    rng = PR.resolve_period(m, TODAY)
    p = PR.previous_range(m, rng, TODAY)
    assert (p.date_from, p.date_to, p.label) == (date(2026, 9, 1), date(2026, 9, 27), "1–27 сен")
    lm = PeriodSpec(preset="last_month")
    p = PR.previous_range(lm, PR.resolve_period(lm, TODAY), TODAY)
    assert (p.date_from, p.date_to, p.label) == (date(2026, 8, 1), date(2026, 8, 31), "Август 2026")
    w = PeriodSpec(preset="this_week")
    p = PR.previous_range(w, PR.resolve_period(w, TODAY), TODAY)
    assert (p.date_from, p.date_to) == (date(2026, 10, 19), date(2026, 10, 20))  # Mon–Tue
    d7 = PeriodSpec(preset="last_7_days")
    p = PR.previous_range(d7, PR.resolve_period(d7, TODAY), TODAY)
    assert (p.date_from, p.date_to) == (date(2026, 10, 14), date(2026, 10, 20))
    # 31 March running → February is clamped to its end
    mar = PR.resolve_period(PeriodSpec(preset="this_month"), date(2026, 3, 30))
    p = PR.previous_range(PeriodSpec(preset="this_month"), mar, date(2026, 3, 30))
    assert (p.date_from, p.date_to) == (date(2026, 2, 1), date(2026, 2, 28))


def test_phrases_and_plural():
    t = PeriodSpec(preset="today")
    assert PR.period_phrase(t, PR.resolve_period(t, TODAY), TODAY) == "сегодня"
    lm = PeriodSpec(preset="last_month")
    assert PR.period_phrase(lm, PR.resolve_period(lm, TODAY), TODAY) == "в сентябре"
    assert [PR.plural(n, "операция", "операции", "операций") for n in (1, 2, 5, 11, 21, 104)] == [
        "операция", "операции", "операций", "операций", "операция", "операции",
    ]  # fmt: skip


def test_resolve_period_uses_local_date_not_utc():
    # 2026-10-27 18:30 UTC is already 2026-10-28 01:30 in Ho Chi Minh City
    late = datetime(2026, 10, 27, 18, 30, tzinfo=UTC)
    r = S.resolve_period(PeriodSpec(preset=PeriodPreset.today), "Asia/Ho_Chi_Minh", now=late)
    assert r.date_from == date(2026, 10, 28)
    r_utc = S.resolve_period(PeriodSpec(preset=PeriodPreset.today), "UTC", now=late)
    assert r_utc.date_from == date(2026, 10, 27)


async def test_day_boundaries_in_summary(session, build):
    """An expense at 00:30 local on the 28th (= 17:30 UTC on the 27th) belongs to the 28th."""
    await build.category("Еда")
    d27, d28 = date(2026, 10, 27), date(2026, 10, 28)
    await build.tx("expense", "100000", "VND", d27, category="Еда", vnd="100000", usd="4",
                   occurred_at=at(d27, 23, 59))  # 16:59 UTC  # fmt: skip
    await build.tx("expense", "30000", "VND", d28, category="Еда", vnd="30000", usd="1.2",
                   occurred_at=at(d28, 0, 30))  # 17:30 UTC on the 27th  # fmt: skip
    before_midnight = datetime(2026, 10, 27, 16, 59, 30, tzinfo=UTC)  # 23:59:30 local
    after_midnight = datetime(2026, 10, 27, 17, 31, tzinfo=UTC)  # 00:31 local, 28th
    s1 = await S.summary(session, build.user_id, "VND", now=before_midnight)
    assert s1.today.date_from == d27 and s1.spent_today.value == Decimal(100000)
    s2 = await S.summary(session, build.user_id, "VND", now=after_midnight)
    assert s2.today.date_from == d28 and s2.spent_today.value == Decimal(30000)
    # the same UTC instant, month boundary: 31 Oct 17:30 UTC is 1 Nov local
    nov = await S.summary(session, build.user_id, "VND", now=datetime(2026, 10, 31, 17, 30,
                                                                       tzinfo=UTC))  # fmt: skip
    assert nov.month.label == "Ноябрь 2026" and nov.spent_month.value == Decimal(0)


async def test_user_timezone_setting_is_respected(session, build):
    from app.models import UserSettings

    st = await session.get(UserSettings, build.user_id)
    st.timezone = "Europe/Moscow"
    await session.flush()
    u = await S.user_ctx(session, build.user_id, datetime(2026, 10, 27, 21, 30, tzinfo=UTC))
    assert u.today == date(2026, 10, 28)  # 00:30 MSK
    u2 = await S.user_ctx(session, build.user_id, datetime(2026, 10, 27, 20, 30, tzinfo=UTC))
    assert u2.today == date(2026, 10, 27)

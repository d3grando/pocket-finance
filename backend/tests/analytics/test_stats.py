"""Accuracy of summaries on a known dataset (conftest.dataset, USD/VND = 25 000, TON = $5).

October 2026 (₫): expense = 150k + 50k + 500k (needs_account) + 250k − 50k (refund) + 100k
(merged «Старая еда» → Еда) + 375k (3 TON) = 1 375 000; income = 25 000 000 + 5 000 000.
Excluded: transfer t6, exchange t7, adjustment t8, deleted t13; t14 (40 ₽) has no rate → missing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.schemas.common import PeriodPreset, PeriodSpec
from app.services.stats import service as S
from tests.analytics.conftest import NOW

P = PeriodSpec
OCT = P(preset=PeriodPreset.this_month)


async def test_summary_vnd(session, dataset):
    s = await S.summary(session, dataset.b.user_id, "VND", now=NOW)
    assert s.today.date_from == date(2026, 10, 27) and s.today.label == "Сегодня"
    assert (s.month.date_from, s.month.date_to) == (date(2026, 10, 1), date(2026, 10, 31))
    assert s.month.label == "Октябрь 2026"
    assert s.spent_today.value == Decimal(700000)
    assert s.spent_today.approx_usd == Decimal(28)
    assert s.spent_month.value == Decimal(1375000)
    assert s.spent_month.approx_usd == Decimal(55)
    assert s.income_month.value == Decimal(30000000)
    assert s.net_month.value == Decimal(28625000)
    assert s.net_month.approx_usd == Decimal(1145)
    # vs 1–27 Sep: expense 330 000, income 5 000 000
    assert s.spent_month.change_ratio == Decimal("3.167")
    assert s.income_month.change_ratio == Decimal("5.000")
    assert s.needs_account_count == 1
    assert s.missing_rates_count == 1
    assert len(s.spent_month.sparkline) == 27
    assert s.spent_month.sparkline[9] == Decimal(475000)  # Oct 10: 100k + 375k
    assert s.spent_month.sparkline[25] == Decimal(200000)  # Oct 26: 250k − 50k refund
    assert s.spent_month.sparkline[26] == Decimal(700000)
    j = s.model_dump(mode="json")
    assert j["spent_month"]["value"] == "1375000" and j["spent_today"]["approx_usd"] == "28"


async def test_summary_usd_and_route(client, dataset):
    r = await client.get("/stats/summary", params={"currency": "USD"})
    assert r.status_code == 200, r.text
    # the route uses the real clock → only shape checks here
    assert r.json()["currency"] == "USD"
    s = await S.summary(dataset.b.session, dataset.b.user_id, "USD", now=NOW)
    assert s.spent_month.value == Decimal(55) and s.spent_month.approx_usd is None
    assert s.income_month.value == Decimal(1200)


async def test_summary_empty_user_is_real_zero(session, user):
    s = await S.summary(session, user.id, "VND", now=NOW)
    assert s.spent_today.value == Decimal(0) and s.spent_month.change_ratio is None
    assert s.needs_account_count == 0


async def test_by_category_refund_and_merge(session, dataset):
    out = await S.by_category(session, dataset.b.user_id, OCT, currency="VND", now=NOW)
    assert out.total == Decimal(1375000)
    got = [(i.name, i.amount, i.share, i.count) for i in out.items]
    assert got == [
        ("Серверы/инфра", Decimal(875000), Decimal("0.636"), 2),
        ("Еда", Decimal(450000), Decimal("0.327"), 4),  # 150k + 250k − 50k + 100k merged
        ("Кофе", Decimal(50000), Decimal("0.036"), 2),  # 50k + 40 ₽ without a rate
    ]
    assert out.items[0].is_business is True and out.items[1].icon == "Utensils"
    inc = await S.by_category(
        session, dataset.b.user_id, OCT, kind="income", currency="USD", now=NOW
    )
    assert [(i.name, i.amount) for i in inc.items] == [
        ("Клиенты", Decimal(1000)),
        ("Зарплата", Decimal(200)),
    ]
    biz = await S.by_category(
        session, dataset.b.user_id, OCT, currency="USD", is_business=True, now=NOW
    )
    assert [(i.name, i.amount) for i in biz.items] == [("Серверы/инфра", Decimal(35))]
    eda = dataset.b.cats["Еда"].id
    only = await S.by_category(
        session, dataset.b.user_id, OCT, currency="VND", category_ids=[eda], now=NOW
    )
    assert only.total == Decimal(450000)  # merged category included via the target id


async def test_series_day_and_month(session, dataset):
    s = await S.series(session, dataset.b.user_id, OCT, currency="USD", now=NOW)
    assert len(s.points) == 27  # up to today, not into the future
    p10 = s.points[9]
    assert (p10.date, p10.label, p10.expense, p10.income, p10.net) == (
        date(2026, 10, 10), "10 окт", Decimal(19), Decimal(1200), Decimal(1181),
    )  # fmt: skip
    assert s.points[25].expense == Decimal(8)  # 10 − 2 refund; transfer/exchange/adj excluded
    assert s.points[0].expense == Decimal(0)
    y = await S.series(
        session, dataset.b.user_id, P(preset=PeriodPreset.this_year), granularity="month",
        currency="USD", now=NOW,
    )  # fmt: skip
    assert [p.label for p in y.points][:2] == ["янв", "фев"] and len(y.points) == 10
    sep, octo = y.points[8], y.points[9]
    assert (sep.date, sep.expense, sep.income) == (date(2026, 9, 1), Decimal("13.20"), Decimal(200))
    assert (octo.expense, octo.income, octo.net) == (Decimal(55), Decimal(1200), Decimal(1145))


async def test_top_purchases(session, dataset):
    t = await S.top_transactions(session, dataset.b.user_id, OCT, limit=3, currency="USD", now=NOW)
    got = [(i.title, i.amount, i.original_amount, i.original_currency) for i in t.items]
    assert got == [
        ("Серверы/инфра · vast.ai", Decimal(20), Decimal(20), "USD"),
        ("Серверы/инфра · @alice", Decimal(15), Decimal(3), "TON"),
        ("Еда", Decimal(10), Decimal(250000), "VND"),
    ]


async def test_daily_average(session, dataset):
    a = await S.daily_average(session, dataset.b.user_id, OCT, currency="USD", now=NOW)
    assert (a.days, a.total, a.average) == (27, Decimal(55), Decimal("2.04"))
    sep = P(preset=PeriodPreset.last_month)
    b = await S.daily_average(session, dataset.b.user_id, sep, currency="VND", now=NOW)
    assert (b.days, b.total, b.average) == (30, Decimal(330000), Decimal(11000))


async def test_compare_running_month(session, dataset):
    c = await S.compare(session, dataset.b.user_id, OCT, currency="USD", now=NOW)
    assert (c.previous.range.date_from, c.previous.range.date_to) == (
        date(2026, 9, 1), date(2026, 9, 27),
    )  # fmt: skip
    assert c.previous.range.label == "1–27 сен"
    assert (c.current.expense, c.current.income, c.current.net) == (
        Decimal(55), Decimal(1200), Decimal(1145),
    )  # fmt: skip
    assert (c.previous.expense, c.previous.income) == (Decimal("13.20"), Decimal(200))
    assert c.expense_change_ratio == Decimal("3.167")
    assert c.income_change_ratio == Decimal("5.000")
    assert c.by_category[0].name == "Серверы/инфра"
    custom = await S.compare(
        session, dataset.b.user_id, OCT,
        P(preset=PeriodPreset.custom, date_from=date(2026, 9, 1), date_to=date(2026, 9, 30)),
        currency="VND", now=NOW,
    )  # fmt: skip
    assert custom.previous.range.label == "Сентябрь 2026"
    assert custom.previous.expense == Decimal(330000)


async def test_business_vs_personal(session, dataset):
    b = await S.business(session, dataset.b.user_id, OCT, currency="USD", now=NOW)
    assert (b.business_income, b.business_expense, b.business_net) == (
        Decimal(1000), Decimal(35), Decimal(965),
    )  # fmt: skip
    assert (b.personal_income, b.personal_expense) == (Decimal(200), Decimal(20))
    assert [s.name for s in b.expense_by_category] == ["Серверы/инфра"]
    assert [s.name for s in b.income_by_category] == ["Клиенты"]


async def test_routes(client, dataset):
    for path, params in (
        ("/stats/by-category", {"preset": "last_30_days", "currency": "USD"}),
        ("/stats/series", {"preset": "this_year", "granularity": "month"}),
        ("/stats/top", {"preset": "all_time", "limit": 5}),
        ("/stats/daily-average", {"preset": "custom", "date_from": "2026-10-01",
                                  "date_to": "2026-10-31"}),
        ("/stats/compare", {"preset": "custom", "date_from": "2026-10-01",
                            "date_to": "2026-10-31", "currency": "VND"}),
        ("/stats/business", {"preset": "custom", "date_from": "2026-10-01",
                             "date_to": "2026-10-31"}),
    ):  # fmt: skip
        r = await client.get(path, params=params)
        assert r.status_code == 200, (path, r.text)
    # a finished month (independent of the real clock): September vs August
    r = await client.get(
        "/stats/compare",
        params={"preset": "custom", "date_from": "2026-09-01", "date_to": "2026-09-30",
                "currency": "VND"},
    )  # fmt: skip
    body = r.json()
    assert body["current"]["expense"] == "330000"
    assert body["current"]["range"]["label"] == "Сентябрь 2026"
    assert body["previous"]["range"]["label"] == "Август 2026"
    assert body["previous"]["expense"] == "0" and body["expense_change_ratio"] is None
    r = await client.get(
        "/stats/compare",
        params={"preset": "custom", "date_from": "2026-10-01", "date_to": "2026-10-31",
                "prev_date_from": "2026-09-01", "prev_date_to": "2026-09-30", "currency": "VND"},
    )  # fmt: skip
    body = r.json()
    assert body["current"]["expense"] == "1375000" and body["previous"]["expense"] == "330000"
    assert body["expense_change_ratio"] == "3.167"
    r = await client.get("/stats/top", params={"preset": "all_time", "limit": 1})
    assert r.json()["items"][0]["amount"] == "20"
    r = await client.get("/stats/by-category", params={"preset": "custom"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = await client.get(
        "/stats/business",
        params={"preset": "custom", "date_from": "2026-10-01", "date_to": "2026-10-31"},
    )
    assert r.json()["business_net"] == "965"


async def test_all_time_starts_at_first_operation(session, dataset):
    s = await S.series(
        session, dataset.b.user_id, P(preset=PeriodPreset.all_time), granularity="month",
        currency="USD", now=NOW,
    )  # fmt: skip
    assert s.range.date_from == date(2026, 9, 5) and s.range.label == "За всё время"
    assert [p.date for p in s.points] == [date(2026, 9, 1), date(2026, 10, 1)]

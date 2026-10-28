"""Statistics. Semantics: docs/ARCH.md §4 (expense = expense − refund,
no transfers/exchanges/adjustments, statuses posted+needs_account, not deleted).

Every public function accepts ``now`` (aware datetime) for tests; by default the current time.
"Today" and all period bounds are local dates in the user's timezone (Asia/Ho_Chi_Minh).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import DEFAULT_TZ, tz, utcnow
from app.models import Category, UserSettings
from app.schemas.assistant import AnswerCard
from app.schemas.common import DateRange, PeriodPreset, PeriodSpec
from app.schemas.intents import ParseContext, StatsIntent
from app.schemas.stats import (
    BusinessOut,
    ByCategoryOut,
    CategorySlice,
    CompareOut,
    CompareSide,
    DailyAverageOut,
    KpiOut,
    SeriesOut,
    SeriesPoint,
    SummaryOut,
    TopItem,
    TopOut,
)
from app.services.stats import queries as Q
from app.services.stats.periods import (
    MONTHS_SHORT,
    add_months,
    day_label,
    effective_end,
    elapsed_days,
    month_end,
    month_label,
    previous_range,
)
from app.services.stats.periods import resolve_period as _resolve

Cur = Literal["VND", "USD"]
ZERO = Q.ZERO


def resolve_period(period: PeriodSpec, tz_name: str, *, now: datetime | None = None) -> DateRange:
    """Preset → inclusive dates in user tz + Russian label («Октябрь 2026», «20–26 окт»).
    all_time starts today here; services use the earliest operation date instead."""
    today = (now or utcnow()).astimezone(tz(tz_name)).date()
    return _resolve(period, today)


@dataclass
class UserCtx:
    user_id: int
    tz_name: str
    today: date
    summary_currency: str
    analytics_currency: str
    categories: dict[int, Category]

    def root(self, cid: int | None) -> int | None:
        """Follow merged_into_id to the surviving category."""
        seen = 0
        while cid is not None and seen < 10:
            c = self.categories.get(cid)
            if c is None or c.merged_into_id is None:
                return cid
            cid, seen = c.merged_into_id, seen + 1
        return cid

    def expand(self, ids: list[int] | None) -> list[int] | None:
        """Category ids + every category merged into them."""
        if ids is None:
            return None
        want = {self.root(i) for i in ids}
        return sorted(cid for cid in self.categories if self.root(cid) in want) or list(ids)


async def user_ctx(session: AsyncSession, user_id: int, now: datetime | None = None) -> UserCtx:
    st = await session.get(UserSettings, user_id)
    tz_name = (st.timezone if st else None) or DEFAULT_TZ
    today = (now or utcnow()).astimezone(tz(tz_name)).date()
    cats = (await session.scalars(select(Category).where(Category.user_id == user_id))).all()
    return UserCtx(
        user_id=user_id,
        tz_name=tz_name,
        today=today,
        summary_currency=(st.summary_currency if st else "VND") or "VND",
        analytics_currency=(st.analytics_currency if st else "USD") or "USD",
        categories={c.id: c for c in cats},
    )


async def _range(session: AsyncSession, u: UserCtx, period: PeriodSpec) -> DateRange:
    earliest = None
    if PeriodPreset(period.preset) == PeriodPreset.all_time:
        earliest = await Q.earliest_date(session, u.user_id)
    return _resolve(period, u.today, earliest=earliest)


def _scope(
    u: UserCtx,
    rng: DateRange | tuple[date, date],
    *,
    category_ids: list[int] | None = None,
    account_ids: list[int] | None = None,
    is_business: bool | None = None,
) -> Q.Scope:
    d1, d2 = (rng.date_from, rng.date_to) if isinstance(rng, DateRange) else rng
    return Q.Scope(
        user_id=u.user_id,
        date_from=d1,
        date_to=d2,
        category_ids=u.expand(category_ids),
        account_ids=account_ids,
        is_business=is_business,
    )


def _kpi(
    value, cur: str, *, approx_usd=None, change_ratio=None, sparkline=None, unknown=False
) -> KpiOut:  # type: ignore[no-untyped-def]
    return KpiOut(
        value=None if unknown else Q.q_money(value, cur),
        currency=cur,
        approx_usd=None if approx_usd is None else Q.q_money(approx_usd, "USD"),
        change_ratio=change_ratio,
        sparkline=[Q.q_money(x, cur) for x in (sparkline or [])],
    )


async def summary(
    session: AsyncSession, user_id: int, currency: Cur = "VND", *, now: datetime | None = None
) -> SummaryOut:
    """Home tiles: spent today / this month, income this month, net flow (₫ by default)."""
    u = await user_ctx(session, user_id, now)
    cur = currency
    today = u.today
    m1, m2 = today.replace(day=1), month_end(today)
    month = DateRange(date_from=m1, date_to=m2, label=month_label(m1))
    today_rng = DateRange(date_from=today, date_to=today, label="Сегодня")

    t_today = await Q.totals(session, _scope(u, today_rng), cur)
    t_month = await Q.totals(session, _scope(u, month), cur)
    # previous month, same elapsed span (1..27 Oct vs 1..27 Sep)
    p1 = add_months(m1, -1)
    p2 = min(month_end(p1), p1 + timedelta(days=(today - m1).days))
    t_prev = await Q.totals(session, _scope(u, (p1, p2)), cur)
    usd_today = usd_month = None
    if cur != "USD":
        usd_today = await Q.totals(session, _scope(u, today_rng), "USD")
        usd_month = await Q.totals(session, _scope(u, month), "USD")
    days = await Q.per_day(session, _scope(u, month), cur)
    spark_end = max(today, max(days)) if days else today  # a future-dated op is in spent_month
    spark = [
        days.get(m1 + timedelta(days=i), (ZERO, ZERO))[0] for i in range((spark_end - m1).days + 1)
    ]

    def unknown(t: Q.Totals) -> bool:
        return t.count > 0 and t.missing == t.count

    return SummaryOut(
        currency=cur,
        today=today_rng,
        month=month,
        spent_today=_kpi(
            t_today.expense,
            cur,
            approx_usd=usd_today.expense if usd_today else None,
            unknown=unknown(t_today),
        ),
        spent_month=_kpi(
            t_month.expense,
            cur,
            approx_usd=usd_month.expense if usd_month else None,
            change_ratio=Q.ratio(t_month.expense, t_prev.expense),
            sparkline=spark,
            unknown=unknown(t_month),
        ),
        income_month=_kpi(
            t_month.income,
            cur,
            approx_usd=usd_month.income if usd_month else None,
            change_ratio=Q.ratio(t_month.income, t_prev.income),
            unknown=unknown(t_month),
        ),
        net_month=_kpi(
            t_month.net,
            cur,
            approx_usd=usd_month.net if usd_month else None,
            unknown=unknown(t_month),
        ),
        needs_account_count=await Q.needs_account_count(session, user_id),
        missing_rates_count=t_month.missing,
    )


def _merged(u: UserCtx, rows: list[Q.CatRow]) -> dict[int | None, tuple[Decimal, int]]:
    """Per surviving category (merges followed): (net amount, operations)."""
    merged: dict[int | None, tuple[Decimal, int]] = {}
    for r in rows:
        k = u.root(r.category_id)
        a, n = merged.get(k, (ZERO, 0))
        merged[k] = (a + r.amount, n + r.count)
    return merged


def _slices(
    u: UserCtx, rows: list[Q.CatRow], cur: str, *, drop_zero: bool = True
) -> tuple[Decimal, list[CategorySlice]]:
    """(total, slices). ``total`` = Σ net of all categories (expense − refund). A category whose
    refunds exceed its expenses (net < 0) is not a slice: a pie has no negative sectors, and
    shares are taken over the positive slices only, so they add up to 100%
    (see ``refunds_total`` for the left-out part)."""
    merged = _merged(u, rows)
    total = sum((a for a, _ in merged.values()), ZERO)
    positive = sum((a for a, _ in merged.values() if a > 0), ZERO)
    items: list[CategorySlice] = []
    for cid, (amt, n) in merged.items():
        if amt < 0 or (drop_zero and amt == 0):
            continue
        c = u.categories.get(cid) if cid is not None else None
        items.append(
            CategorySlice(
                category_id=cid or 0,
                name=c.name if c else "Без категории",
                icon=c.icon if c else None,
                color=c.color if c else None,
                is_business=bool(c.is_business) if c else False,
                is_new=bool(c.is_new) if c else False,
                amount=Q.q_money(amt, cur),
                share=Q.share(amt, positive),
                count=n,
            )
        )
    items.sort(key=lambda s: (-s.amount, s.name))
    return Q.q_money(total, cur), items


def refunds_total(u: UserCtx, rows: list[Q.CatRow], cur: str) -> Decimal:
    """Net refunds left out of the slices (categories with refunds > expenses), as a positive
    number: total = Σ slices − refunds_total."""
    neg = sum((a for a, _ in _merged(u, rows).values() if a < 0), ZERO)
    return Q.q_money(-neg, cur)


async def by_category(
    session: AsyncSession,
    user_id: int,
    period: PeriodSpec,
    *,
    kind: Literal["expense", "income"] = "expense",
    currency: Cur = "USD",
    is_business: bool | None = None,
    category_ids: list[int] | None = None,
    account_ids: list[int] | None = None,
    now: datetime | None = None,
) -> ByCategoryOut:
    u = await user_ctx(session, user_id, now)
    rng = await _range(session, u, period)
    rows = await Q.per_category(
        session,
        _scope(u, rng, category_ids=category_ids, account_ids=account_ids, is_business=is_business),
        currency,
        kind,
    )
    total, items = _slices(u, rows, currency)
    return ByCategoryOut(range=rng, kind=kind, currency=currency, total=total, items=items)


def _series_points(
    u: UserCtx,
    data: dict[date, tuple],  # type: ignore[type-arg]
    d1: date,
    d2: date,
    granularity: str,
    cur: str,
) -> list[SeriesPoint]:
    pts: list[SeriesPoint] = []
    if granularity == "day":
        d = d1
        while d <= d2:
            e, i = data.get(d, (ZERO, ZERO))
            pts.append(
                SeriesPoint(
                    date=d,
                    label=f"{d.day} {MONTHS_SHORT[d.month - 1]}",
                    expense=Q.q_money(e, cur),
                    income=Q.q_money(i, cur),
                    net=Q.q_money(i - e, cur),
                )
            )
            d += timedelta(days=1)
        return pts
    m = d1.replace(day=1)
    while m <= d2:
        e, i = data.get(m, (ZERO, ZERO))
        label = MONTHS_SHORT[m.month - 1] + (f" {m.year}" if m.year != u.today.year else "")
        pts.append(
            SeriesPoint(
                date=m,
                label=label,
                expense=Q.q_money(e, cur),
                income=Q.q_money(i, cur),
                net=Q.q_money(i - e, cur),
            )
        )
        m = add_months(m, 1)
    return pts


async def series(
    session: AsyncSession,
    user_id: int,
    period: PeriodSpec,
    *,
    granularity: Literal["day", "month"] = "day",
    currency: Cur = "USD",
    category_ids: list[int] | None = None,
    account_ids: list[int] | None = None,
    is_business: bool | None = None,
    now: datetime | None = None,
) -> SeriesOut:
    """Expense/income/net per day or month, zero-filled up to today (not into the future)."""
    u = await user_ctx(session, user_id, now)
    rng = await _range(session, u, period)
    sc = _scope(u, rng, category_ids=category_ids, account_ids=account_ids, is_business=is_business)
    end = effective_end(rng, u.today)
    if granularity == "day":
        data = await Q.per_day(session, sc, currency)
    else:
        data = await Q.per_month(session, sc, currency)
    if data:
        # an operation dated after today (the ledger allows +1 day) is in the totals → the chart
        # must reach it too, so Σ points == totals
        end = max(end, max(data))
    pts = _series_points(u, data, rng.date_from, end, granularity, currency)
    return SeriesOut(range=rng, granularity=granularity, currency=currency, points=pts)


async def top_transactions(
    session: AsyncSession,
    user_id: int,
    period: PeriodSpec,
    *,
    limit: int = 10,
    currency: Cur = "USD",
    category_ids: list[int] | None = None,
    account_ids: list[int] | None = None,
    is_business: bool | None = None,
    now: datetime | None = None,
) -> TopOut:
    """Largest purchases (kind=expense) by amount in ``currency``."""
    u = await user_ctx(session, user_id, now)
    rng = await _range(session, u, period)
    sc = _scope(u, rng, category_ids=category_ids, account_ids=account_ids, is_business=is_business)
    rows = await Q.top_expenses(session, sc, currency, limit)
    items = []
    for r in rows:
        cname = r.category_name
        root = u.root(r.tx.category_id)
        if root is not None and root in u.categories:
            cname = u.categories[root].name
        items.append(
            TopItem(
                transaction_id=r.tx.id,
                occurred_on=r.tx.occurred_on,
                title=Q.tx_title(cname, r.merchant_name, r.tx),
                category=cname,
                amount=Q.q_money(r.amount, currency),
                currency=currency,
                original_amount=r.tx.amount,
                original_currency=r.tx.currency,
            )
        )
    return TopOut(range=rng, currency=currency, items=items)


async def daily_average(
    session: AsyncSession,
    user_id: int,
    period: PeriodSpec,
    *,
    currency: Cur = "USD",
    category_ids: list[int] | None = None,
    is_business: bool | None = None,
    now: datetime | None = None,
) -> DailyAverageOut:
    """Expense / elapsed days of the period (a running month counts days up to today)."""
    u = await user_ctx(session, user_id, now)
    rng = await _range(session, u, period)
    t = await Q.totals(
        session, _scope(u, rng, category_ids=category_ids, is_business=is_business), currency
    )
    days = elapsed_days(rng, u.today)
    avg = t.expense / days if days > 0 else ZERO
    return DailyAverageOut(
        range=rng,
        currency=currency,
        days=days,
        total=Q.q_money(t.expense, currency),
        average=Q.q_money(avg, currency),
    )


async def compare(
    session: AsyncSession,
    user_id: int,
    period: PeriodSpec,
    previous: PeriodSpec | None = None,
    *,
    currency: Cur = "USD",
    category_ids: list[int] | None = None,
    is_business: bool | None = None,
    now: datetime | None = None,
) -> CompareOut:
    """previous None → the preceding period of equal length (month → previous month; a running
    month is compared with the same days of the previous one)."""
    u = await user_ctx(session, user_id, now)
    rng = await _range(session, u, period)
    if previous is not None:
        prev = await _range(session, u, previous)
    else:
        prev = previous_range(period, rng, u.today)
    kw = {"category_ids": category_ids, "is_business": is_business}
    cur_t = await Q.totals(session, _scope(u, rng, **kw), currency)
    prev_t = await Q.totals(session, _scope(u, prev, **kw), currency)
    rows = await Q.per_category(session, _scope(u, rng, **kw), currency, "expense")
    _, slices = _slices(u, rows, currency)

    def side(r: DateRange, t: Q.Totals) -> CompareSide:
        return CompareSide(
            range=r,
            expense=Q.q_money(t.expense, currency),
            income=Q.q_money(t.income, currency),
            net=Q.q_money(t.net, currency),
        )

    return CompareOut(
        currency=currency,
        current=side(rng, cur_t),
        previous=side(prev, prev_t),
        expense_change_ratio=Q.ratio(cur_t.expense, prev_t.expense),
        income_change_ratio=Q.ratio(cur_t.income, prev_t.income),
        by_category=slices,
    )


async def business(
    session: AsyncSession,
    user_id: int,
    period: PeriodSpec,
    *,
    currency: Cur = "USD",
    now: datetime | None = None,
) -> BusinessOut:
    """Business vs personal (by category flag)."""
    u = await user_ctx(session, user_id, now)
    rng = await _range(session, u, period)
    biz = await Q.totals(session, _scope(u, rng, is_business=True), currency)
    pers = await Q.totals(session, _scope(u, rng, is_business=False), currency)
    _, exp_items = _slices(
        u,
        await Q.per_category(session, _scope(u, rng, is_business=True), currency, "expense"),
        currency,
    )
    _, inc_items = _slices(
        u,
        await Q.per_category(session, _scope(u, rng, is_business=True), currency, "income"),
        currency,
    )
    return BusinessOut(
        range=rng,
        currency=currency,
        business_income=Q.q_money(biz.income, currency),
        business_expense=Q.q_money(biz.expense, currency),
        business_net=Q.q_money(biz.net, currency),
        personal_income=Q.q_money(pers.income, currency),
        personal_expense=Q.q_money(pers.expense, currency),
        expense_by_category=exp_items,
        income_by_category=inc_items,
    )


async def answer_stats_intent(
    session: AsyncSession, user_id: int, intent: StatsIntent, ctx: ParseContext, resolved: dict
) -> AnswerCard:
    """Answer card for the assistant/bot (DESIGN.md §9.2 f). Bot/mini-window currency: ₫
    (summary_currency) with ≈$; resolved holds category_ids/account_ids from the validator."""
    from app.services.stats.answer import build_answer

    return await build_answer(session, user_id, intent, ctx, resolved or {})


__all__ = [
    "answer_stats_intent",
    "business",
    "by_category",
    "compare",
    "daily_average",
    "day_label",
    "resolve_period",
    "series",
    "summary",
    "top_transactions",
    "refunds_total",
    "user_ctx",
]

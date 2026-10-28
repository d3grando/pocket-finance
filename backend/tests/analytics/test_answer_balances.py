"""Assistant/bot answers (AnswerCard) and per-asset balances (only with an opening balance)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.schemas.common import PeriodSpec
from app.schemas.intents import ParseContext, StatsIntent
from app.services.stats import service as S
from app.services.stats.answer import fmt_percent
from app.services.stats.balances import asset_balances
from tests.analytics.conftest import NOW, sp

CTX = ParseContext(now=NOW, today=date(2026, 10, 27))


async def ask(session, dataset, **kw):  # type: ignore[no-untyped-def]
    resolved = kw.pop("resolved", {})
    return await S.answer_stats_intent(session, dataset.b.user_id, StatsIntent(**kw), CTX, resolved)


def test_fmt_percent():
    assert fmt_percent(Decimal("0.183")) == "+18%"
    assert fmt_percent(Decimal("0.045")) == "+4.5%"
    assert fmt_percent(Decimal("-0.12")) == "−12%"
    assert fmt_percent(Decimal("0")) == "0%"
    assert fmt_percent(None) is None


async def test_spent_today_card(session, dataset):
    c = await ask(session, dataset, metric="spent", period=PeriodSpec(preset="today"))
    assert c.kind == "answer" and c.answer_type == "stats"
    assert c.title == "Сегодня потрачено"
    assert sp(c.headline) == "−700 000 ₫" and sp(c.headline_approx) == "≈ $28.00"
    assert c.subtitle == "27 окт"
    assert [(ln.label, sp(ln.value)) for ln in c.lines] == [
        ("Серверы/инфра", "500 000 ₫"),
        ("Еда", "150 000 ₫"),
        ("Кофе", "50 000 ₫"),
    ]
    assert c.buttons[0].action.kind == "open"
    assert c.buttons[0].action.route == "/analytics?period=today"


async def test_spent_month_rest_line_and_usd(session, dataset):
    c = await ask(session, dataset, metric="by_category", display_currency="USD")
    assert c.title == "На что ушло в октябре"
    assert sp(c.headline) == "−$55.00" and c.headline_approx is None
    assert c.subtitle == "Октябрь 2026 · без курса: 1"
    c2 = await ask(session, dataset, metric="spent",
                   resolved={"category_ids": [dataset.b.cats["Еда"].id]})  # fmt: skip
    assert c2.title == "В октябре потрачено · Еда" and sp(c2.headline) == "−450 000 ₫"


async def test_earned_net_business_compare_top(session, dataset):
    e = await ask(session, dataset, metric="earned")
    assert sp(e.headline) == "+30 000 000 ₫" and e.lines[0].label == "Клиенты"
    n = await ask(session, dataset, metric="net")
    assert sp(n.headline) == "+28 625 000 ₫" and sp(n.headline_approx) == "≈ $1 145.00"
    b = await ask(session, dataset, metric="business", display_currency="USD")
    assert sp(b.headline) == "+$965.00"
    assert [sp(x.value) for x in b.lines] == ["+$1 000.00", "−$35.00", "+$200.00", "−$20.00"]
    cmp = await ask(session, dataset, metric="compare")
    assert cmp.answer_type == "compare" and cmp.headline == "+317%"
    assert cmp.title == "Сравнение: Октябрь 2026 и 1–27 сен"
    top = await ask(session, dataset, metric="top", limit=2, display_currency="USD")
    assert top.answer_type == "list" and top.total_count == 2
    assert [ln.transaction_id for ln in top.lines] == [dataset.tx["t3"].id, dataset.tx["t12"].id]
    assert sp(top.lines[1].value) == "$15.00 (3 TON)"
    assert top.filter.sort == "amount_desc" and top.filter.kinds == ["expense"]
    avg = await ask(session, dataset, metric="daily_average", display_currency="USD")
    assert sp(avg.headline) == "$2.04"
    by_day = await ask(session, dataset, metric="by_day")
    assert [ln.label for ln in by_day.lines] == ["10 окт", "26 окт", "27 окт"]


async def test_balances_only_with_opening(session, dataset):
    rows = await asset_balances(session, dataset.b.user_id, now=NOW)
    by = {(r.account_name, r.asset): r for r in rows}
    bank = by[("Карта", "VND")]
    # 10 000 000 − 150k − 250k + 50k (refund) − 1 000 000 (transfer) − 120k (adjustment);
    # the September posting is before opening_at and does not count
    assert bank.balance == Decimal(8_530_000)
    assert bank.approx_usd == Decimal("341.20")  # 8 530 000 / 25 000
    assert by[("Наличные", "VND")].balance is None  # opening not set → no made-up number
    ton = by[("Кошелёк", "TON")]
    assert ton.balance == Decimal(7) and ton.approx_usd == Decimal("35.00")
    assert ton.approx_vnd == Decimal(875000)
    assert by[("Кошелёк", "USDT")].balance is None
    card = await ask(session, dataset, metric="balance")
    assert card.answer_type == "balance"
    lines = {ln.label: sp(ln.value) for ln in card.lines}
    assert lines["Карта"] == "8 530 000 ₫"
    assert lines["Наличные"] == "остаток не задан"
    assert lines["Кошелёк · TON"] == "7 TON · ≈ 875 000 ₫"
    assert lines["Кошелёк · USDT"] == "остаток не задан"
    assert sp(card.headline) == "≈ 9 405 000 ₫"  # 8 530 000 + 875 000


async def test_price_change_is_not_income(session, dataset):
    """A new, higher TON price changes ≈$ of the wallet only — never income/expense."""
    from tests.analytics.conftest import add_rate

    before = await S.summary(session, dataset.b.user_id, "USD", now=NOW)
    await add_rate(session, "TON", "USD", "8", date(2026, 10, 27), "mid", "coingecko")
    after = await S.summary(session, dataset.b.user_id, "USD", now=NOW)
    assert after.income_month.value == before.income_month.value
    assert after.spent_month.value == before.spent_month.value
    rows = await asset_balances(session, dataset.b.user_id, now=NOW)
    ton = next(r for r in rows if r.asset == "TON")
    assert ton.balance == Decimal(7) and ton.approx_usd == Decimal("56.00")

"""Assistant/bot answers to statistics questions → AnswerCard (DESIGN.md §9.2 f, §12.6).

Currency: the intent's display_currency (VND/USD) or the user's summary currency (₫ by default)
with «≈ $» next to the headline. All numbers come from services/stats — never from the LLM.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from app.core.money import MINUS, format_money
from app.schemas.assistant import AnswerCard, AnswerLine, Button, ButtonAction
from app.schemas.common import DateRange, PeriodSpec
from app.schemas.intents import ParseContext, SearchFilter, StatsIntent
from app.services.stats import queries as Q
from app.services.stats.balances import asset_balances
from app.services.stats.periods import (
    analytics_route,
    day_label,
    period_phrase,
    plural,
    previous_range,
)
from app.services.stats.service import (
    UserCtx,
    _range,
    _scope,
    _slices,
    refunds_total,
    user_ctx,
)

ZERO = Decimal(0)
TOP_LINES = 4


def fmt_percent(r: Decimal | None) -> str | None:
    """«+18%», «−4.5%» (one decimal only below 10%) — DESIGN.md §3.2 formatPercent."""
    if r is None:
        return None
    pct = r * 100
    if abs(pct) < 10:
        s = f"{abs(pct).quantize(Decimal('0.1'), rounding=ROUND_HALF_EVEN):f}".rstrip("0").rstrip(
            "."
        )
    else:
        s = f"{abs(pct).quantize(Decimal(1), rounding=ROUND_HALF_EVEN):f}"
    sign = "+" if pct > 0 else (MINUS if pct < 0 else "")
    return f"{sign}{s}%"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _money(v: Decimal, cur: str, sign: str = "auto") -> str:
    return format_money(Q.q_money(v, cur), cur, sign=sign)


def _approx(v: Decimal | None, cur: str) -> str | None:
    if v is None:
        return None
    return format_money(Q.q_money(v, cur), cur, approx=True)


def _cat_lines(
    items: list, cur: str, top: int = TOP_LINES, signed: bool = False
) -> list[AnswerLine]:  # type: ignore[type-arg]
    lines = [
        AnswerLine(
            label=s.name,
            value=_money(s.amount, cur),
            amount=s.amount,
            currency=cur,
            share=float(s.share),
            category_id=s.category_id or None,
            icon=s.icon,
        )
        for s in items[:top]
    ]
    rest = items[top:]
    if rest:
        amt = sum((s.amount for s in rest), ZERO)
        n = len(rest)
        lines.append(
            AnswerLine(
                label=f"… ещё {n} {plural(n, 'категория', 'категории', 'категорий')}",
                value=_money(amt, cur),
                amount=amt,
                currency=cur,
                share=float(sum((s.share for s in rest), ZERO)),
            )
        )
    return lines


def _subtitle(rng: DateRange, u: UserCtx, t: Q.Totals | None = None) -> str:
    label = rng.label
    if label in ("Сегодня", "Вчера"):
        label = day_label(rng.date_from, u.today)
    if t is not None and t.missing:
        label += f" · без курса: {t.missing}"
    return label


async def build_answer(
    session: Any, user_id: int, intent: StatsIntent, ctx: ParseContext | None, resolved: dict
) -> AnswerCard:
    u = await user_ctx(session, user_id, ctx.now if ctx is not None else None)
    dc = (intent.display_currency or "").upper()
    cur = (
        dc
        if dc in ("VND", "USD")
        else (u.summary_currency if u.summary_currency in ("VND", "USD") else "VND")
    )
    alt = "USD" if cur != "USD" else None
    cat_ids: list[int] | None = list(resolved.get("category_ids") or []) or None
    acc_ids: list[int] | None = list(resolved.get("account_ids") or []) or None
    period: PeriodSpec = intent.period
    rng = await _range(session, u, period)
    phrase = period_phrase(period, rng, u.today)
    kw = {"category_ids": cat_ids, "account_ids": acc_ids, "is_business": intent.is_business}
    sc = _scope(u, rng, **kw)
    open_btn = Button(
        id="open",
        label="Открыть в аналитике →",
        style="ghost",
        action=ButtonAction(kind="open", route=analytics_route(period, rng)),
    )
    cat_suffix = ""
    if cat_ids:
        names = [u.categories[c].name for c in cat_ids if c in u.categories]
        if names:
            cat_suffix = " · " + ", ".join(names)
    if intent.is_business is True and not cat_suffix:
        cat_suffix = " · бизнес"
    elif intent.is_business is False and not cat_suffix:
        cat_suffix = " · личное"

    m = intent.metric
    if m == "balance":
        return await _balance(session, u, cur, acc_ids)

    t = await Q.totals(session, sc, cur)
    tu = await Q.totals(session, sc, alt) if alt else None
    card = AnswerCard(
        answer_type="stats",
        title="",
        subtitle=_subtitle(rng, u, t),
        buttons=[open_btn],
        filter=SearchFilter(period=period, category_ids=cat_ids or [], account_ids=acc_ids or []),
    )

    if m in ("spent", "by_category"):
        rows = await Q.per_category(session, sc, cur, "expense")
        _, items = _slices(u, rows, cur)
        card.title = f"{_cap(phrase)} потрачено{cat_suffix}"
        if m == "by_category":
            card.title = f"На что ушло {phrase}{cat_suffix}"
        card.headline = _money(-t.expense, cur) if t.expense else _money(ZERO, cur)
        card.headline_approx = _approx(tu.expense if tu else None, "USD") if t.expense else None
        card.lines = _cat_lines(items, cur, top=TOP_LINES if m == "spent" else 8)
        refunds = refunds_total(u, rows, cur)
        if refunds:
            # categories where refunds exceed expenses are not slices: show them as one line
            card.lines.append(
                AnswerLine(
                    label="Возвраты",
                    value=_money(refunds, cur, "always"),
                    amount=refunds,
                    currency=cur,
                )
            )
        if not sum(r.count for r in rows):  # income-only day: there were no expenses
            card.text = "Расходов нет"
        card.filter.kinds = ["expense", "refund"]
    elif m == "earned":
        rows = await Q.per_category(session, sc, cur, "income")
        _, items = _slices(u, rows, cur)
        card.title = f"{_cap(phrase)} получено{cat_suffix}"
        card.headline = _money(t.income, cur, sign="always") if t.income else _money(ZERO, cur)
        card.headline_approx = _approx(tu.income if tu else None, "USD") if t.income else None
        card.lines = _cat_lines(items, cur)
        if not t.income:
            card.text = "Доходов нет"
        card.filter.kinds = ["income"]
    elif m == "net":
        card.title = f"{_cap(phrase)} чистыми{cat_suffix}"
        card.headline = _money(t.net, cur, sign="always")
        card.headline_approx = _approx(tu.net if tu else None, "USD")
        card.lines = [
            AnswerLine(
                label="Доходы", value=_money(t.income, cur, "always"), amount=t.income, currency=cur
            ),
            AnswerLine(
                label="Расходы", value=_money(-t.expense, cur), amount=-t.expense, currency=cur
            ),
        ]
    elif m == "top":
        await _top(session, u, card, sc, cur, intent, phrase, cat_suffix)
    elif m == "daily_average":
        days = _elapsed(rng, u)
        avg = t.expense / days if days else ZERO
        avg_u = (tu.expense / days if days else ZERO) if tu else None
        card.title = f"Средний расход в день {phrase}{cat_suffix}"
        card.headline = _money(avg, cur)
        card.headline_approx = _approx(avg_u, "USD") if avg_u else None
        card.lines = [
            AnswerLine(
                label="Всего потрачено",
                value=_money(t.expense, cur),
                amount=t.expense,
                currency=cur,
            ),
            AnswerLine(label="Дней", value=str(days)),
        ]
    elif m == "compare":
        await _compare(session, u, card, rng, cur, intent, kw)
    elif m in ("by_day", "by_month"):
        await _series(session, u, card, sc, rng, cur, m, phrase, cat_suffix, t)
    elif m == "business":
        await _business(session, u, card, rng, cur, alt, phrase)
    else:  # pragma: no cover - StatsMetric is a closed Literal
        card.title = f"{_cap(phrase)}"
    return card


def _elapsed(rng: DateRange, u: UserCtx) -> int:
    from app.services.stats.periods import elapsed_days

    return elapsed_days(rng, u.today)


async def _top(session, u, card, sc, cur, intent, phrase, cat_suffix) -> None:  # type: ignore[no-untyped-def]
    limit = intent.limit or 5
    rows = await Q.top_expenses(session, sc, cur, limit)
    card.answer_type = "list"
    card.title = f"Крупнейшие покупки {phrase}{cat_suffix}"
    card.lines = []
    for r in rows:
        cname = r.category_name
        root = u.root(r.tx.category_id)
        if root in u.categories:
            cname = u.categories[root].name
        value = _money(r.amount, cur)
        if r.tx.currency != cur:
            value += f" ({format_money(r.tx.amount, r.tx.currency)})"
        when = day_label(r.tx.occurred_on, u.today)
        card.lines.append(
            AnswerLine(
                label=f"{Q.tx_title(cname, r.merchant_name, r.tx)} · {when}",
                value=value,
                amount=Q.q_money(r.amount, cur),
                currency=cur,
                transaction_id=r.tx.id,
            )
        )
    card.total_count = len(rows)
    card.filter.kinds = ["expense"]
    card.filter.sort = "amount_desc"
    card.filter.limit = max(limit, 1)
    if not rows:
        card.text = "Покупок нет"


async def _compare(session, u, card, rng, cur, intent, kw) -> None:  # type: ignore[no-untyped-def]
    if intent.compare_period is not None:
        prev = await _range(session, u, intent.compare_period)
    else:
        prev = previous_range(intent.period, rng, u.today)
    a = await Q.totals(session, _scope(u, rng, **kw), cur)
    b = await Q.totals(session, _scope(u, prev, **kw), cur)
    card.answer_type = "compare"
    card.title = f"Сравнение: {rng.label} и {prev.label}"
    r_exp = Q.ratio(a.expense, b.expense)
    card.headline = fmt_percent(r_exp) or "—"
    card.subtitle = "Изменение расходов"
    card.lines = [
        AnswerLine(
            label=f"Расходы · {rng.label}",
            value=_money(a.expense, cur),
            amount=a.expense,
            currency=cur,
        ),
        AnswerLine(
            label=f"Расходы · {prev.label}",
            value=_money(b.expense, cur),
            amount=b.expense,
            currency=cur,
        ),
    ]
    if a.income or b.income:
        r_inc = fmt_percent(Q.ratio(a.income, b.income))
        card.lines += [
            AnswerLine(
                label=f"Доходы · {rng.label}",
                value=_money(a.income, cur),
                amount=a.income,
                currency=cur,
            ),
            AnswerLine(
                label=f"Доходы · {prev.label}",
                value=_money(b.income, cur),
                amount=b.income,
                currency=cur,
            ),
        ]
        if r_inc:
            card.lines.append(AnswerLine(label="Изменение доходов", value=r_inc))


async def _series(session, u, card, sc, rng, cur, m, phrase, cat_suffix, t) -> None:  # type: ignore[no-untyped-def]
    from app.services.stats.periods import MONTHS_NOM, effective_end

    end = effective_end(rng, u.today)
    if m == "by_day":
        data = await Q.per_day(session, sc, cur)
        card.title = f"Расходы по дням {phrase}{cat_suffix}"
        days = sorted(d for d in data if rng.date_from <= d <= end)
        card.lines = [
            AnswerLine(
                label=day_label(d, u.today),
                value=_money(data[d][0], cur),
                amount=data[d][0],
                currency=cur,
            )
            for d in days[-31:]
        ]
    else:
        data = await Q.per_month(session, sc, cur)
        card.title = f"Расходы по месяцам {phrase}{cat_suffix}"
        card.lines = [
            AnswerLine(
                label=f"{MONTHS_NOM[d.month - 1]} {d.year}",
                value=_money(data[d][0], cur),
                amount=data[d][0],
                currency=cur,
            )
            for d in sorted(data)[-24:]
        ]
    card.headline = _money(-t.expense, cur) if t.expense else _money(ZERO, cur)
    if not card.lines:
        card.text = "Расходов нет"


async def _business(session, u, card, rng, cur, alt, phrase) -> None:  # type: ignore[no-untyped-def]
    biz = await Q.totals(session, _scope(u, rng, is_business=True), cur)
    pers = await Q.totals(session, _scope(u, rng, is_business=False), cur)
    card.title = f"Бизнес {phrase}"
    card.headline = _money(biz.net, cur, sign="always")
    if alt:
        bu = await Q.totals(session, _scope(u, rng, is_business=True), alt)
        card.headline_approx = _approx(bu.net, alt)
    card.lines = [
        AnswerLine(
            label="Бизнес: доходы",
            value=_money(biz.income, cur, "always"),
            amount=biz.income,
            currency=cur,
        ),
        AnswerLine(
            label="Бизнес: расходы",
            value=_money(-biz.expense, cur),
            amount=-biz.expense,
            currency=cur,
        ),
        AnswerLine(
            label="Личное: доходы",
            value=_money(pers.income, cur, "always"),
            amount=pers.income,
            currency=cur,
        ),
        AnswerLine(
            label="Личное: расходы",
            value=_money(-pers.expense, cur),
            amount=-pers.expense,
            currency=cur,
        ),
    ]


async def _balance(session, u: UserCtx, cur: str, acc_ids: list[int] | None) -> AnswerCard:  # type: ignore[no-untyped-def]
    rows = await asset_balances(session, u.user_id)
    if acc_ids:
        rows = [r for r in rows if r.account_id in acc_ids]
    lines: list[AnswerLine] = []
    total = ZERO
    known = 0
    multi = {r.account_id for r in rows if sum(1 for x in rows if x.account_id == r.account_id) > 1}
    for r in rows:
        label = r.account_name + (f" · {r.asset}" if r.account_id in multi else "")
        if r.balance is None:
            lines.append(AnswerLine(label=label, value="остаток не задан", currency=r.asset))
            continue
        value = format_money(r.balance, r.asset)
        conv = r.approx_vnd if cur == "VND" else r.approx_usd
        if r.asset != cur and conv is not None:
            value += f" · {format_money(Q.q_money(conv, cur), cur, approx=True)}"
        lines.append(AnswerLine(label=label, value=value, amount=r.balance, currency=r.asset))
        if conv is not None:
            total += conv
            known += 1
    card = AnswerCard(
        answer_type="balance",
        title="Остатки по счетам",
        subtitle=None if known else "Остатки не заданы — укажите их в «Счетах»",
        headline=format_money(Q.q_money(total, cur), cur, approx=True) if known else None,
        lines=lines,
        buttons=[
            Button(
                id="open",
                label="Открыть счета →",
                style="ghost",
                action=ButtonAction(kind="open", route="/accounts"),
            )
        ],
    )
    return card

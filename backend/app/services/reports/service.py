"""Telegram reports. Format per DESIGN.md §12 (Telegram templates).

* monthly (§12.7): sent on the 1st at report_time for the previous month; on demand for the
  current month to date. Totals in the summary currency (₫), flow ≈ $, business and the largest
  purchase in $; comparison with the same span of the previous month.
* weekly: like monthly, top-3 categories, previous week for comparison.
* daily: one line «🌙 27 октября: −595 000 ₫ · 5 операций · больше всего — Еда 320 000 ₫».
All user strings are HTML-escaped; numbers follow DESIGN.md §3 (format_money).
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from app.core.money import format_money
from app.schemas.common import DateRange
from app.schemas.reports import ReportOut
from app.services.stats import queries as Q
from app.services.stats.answer import fmt_percent
from app.services.stats.periods import (
    MONTHS_DAT,
    add_months,
    day_label_long,
    month_end,
    plural,
    range_label,
)
from app.services.stats.service import _scope, _slices, refunds_total, user_ctx

ReportKind = Literal["daily", "weekly", "monthly"]
ZERO = Decimal(0)

#: Bot category emoji (DESIGN.md §12.7); unknown/new categories → 🏷.
CATEGORY_EMOJI: dict[str, str] = {
    "Еда": "🍴",
    "Продукты": "🧺",
    "Кофе": "☕",
    "Такси/транспорт": "🚕",
    "Жильё": "🏠",
    "Связь": "📱",
    "Подписки": "🔁",
    "Здоровье": "💊",
    "Покупки": "🛍",
    "Развлечения": "🎟",
    "Подарки": "🎁",
    "Прочее": "▫️",
    "Серверы/инфра": "🖥",
    "Софт и AI": "🤖",
    "Реклама": "📣",
    "Подрядчики": "🤝",
    "Комиссии/обмен": "💱",
    "Закупка товара": "📦",
    "Переводы людям": "👤",
    "Клиенты": "💼",
    "Продажи": "🏪",
    "Зарплата": "💵",
}
_EMOJI_PREFIX = {"такси": "🚕", "сервер": "🖥", "комисс": "💱", "софт": "🤖", "жиль": "🏠"}


def category_emoji(name: str | None) -> str:
    if not name:
        return "🏷"
    if name in CATEGORY_EMOJI:
        return CATEGORY_EMOJI[name]
    low = name.lower()
    for p, e in _EMOJI_PREFIX.items():
        if low.startswith(p):
            return e
    return "🏷"


def report_range(kind: ReportKind, on: date) -> DateRange:
    """monthly: the month before ``on`` if on.day == 1 else the current month to date;
    weekly: the previous Mon–Sun if ``on`` is a Monday else the current week to date;
    daily: the day ``on``."""
    if kind == "monthly":
        if on.day == 1:
            s = add_months(on, -1)
            e = month_end(s)
        else:
            s, e = on.replace(day=1), on
    elif kind == "weekly":
        if on.weekday() == 0:
            s = on - timedelta(days=7)
            e = s + timedelta(days=6)
        else:
            s, e = on - timedelta(days=on.weekday()), on
    else:
        s = e = on
    return DateRange(date_from=s, date_to=e, label=range_label(s, e, on))


def _prev_range(kind: ReportKind, rng: DateRange) -> tuple[date, date]:
    if kind == "monthly":
        s = add_months(rng.date_from, -1)
        return s, min(month_end(s), s + (rng.date_to - rng.date_from))
    if kind == "weekly":
        return rng.date_from - timedelta(days=7), rng.date_to - timedelta(days=7)
    d = rng.date_from - timedelta(days=1)
    return d, d


def _m(v: Decimal, cur: str, sign: str = "auto") -> str:
    return html.escape(format_money(Q.q_money(v, cur), cur, sign=sign))


def html_to_plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _pad(label: str, width: int = 10) -> str:
    return label + " " * max(1, width - len(label))


async def build_report(
    session: Any,
    user_id: int,
    kind: ReportKind,
    on: date,
    *,
    now: datetime | None = None,
) -> ReportOut:
    """monthly: the month before ``on`` if on.day == 1 else the current month to date."""
    u = await user_ctx(session, user_id, now)
    cur = u.summary_currency if u.summary_currency in ("VND", "USD") else "VND"
    rng = report_range(kind, on)
    sc = _scope(u, rng)
    t = await Q.totals(session, sc, cur)
    if kind == "daily":
        text = await _daily(session, u, rng, cur, t)
    else:
        text = await _period_report(session, u, kind, rng, cur, t)
    return ReportOut(kind=kind, range=rng, text_html=text, text_plain=html_to_plain(text))


async def _daily(session: Any, u: Any, rng: DateRange, cur: str, t: Q.Totals) -> str:
    head = f"🌙 {day_label_long(rng.date_from)}"
    rows = await Q.per_category(session, _scope(u, rng), cur, "expense")
    _, items = _slices(u, rows, cur)
    n_exp = sum(r.count for r in rows)  # expense + refund operations (refund-only included)
    if t.expense == 0 and not n_exp:
        text = f"{head}: расходов нет"
    else:
        text = (
            f"{head}: <b>{_m(-t.expense, cur)}</b> · {n_exp} "
            f"{plural(n_exp, 'операция', 'операции', 'операций')}"
        )
        if items:
            top = items[0]
            text += f" · больше всего — {html.escape(top.name)} {_m(top.amount, cur)}"
    if t.income:
        text += f" · доход {_m(t.income, cur, 'always')}"
    return text


async def _period_report(
    session: Any, u: Any, kind: ReportKind, rng: DateRange, cur: str, t: Q.Totals
) -> str:
    title = rng.label if kind == "monthly" else f"Неделя {rng.label}"
    lines = [f"🗓 <b>{html.escape(title)}</b>", ""]
    p1, p2 = _prev_range(kind, rng)
    prev = await Q.totals(session, _scope(u, (p1, p2)), cur)
    exp_line = f"{_pad('Расходы')}{_m(-t.expense, cur)}"
    r = Q.ratio(t.expense, prev.expense)
    if r is not None and t.expense:
        to = f"к {MONTHS_DAT[p1.month - 1]}" if kind == "monthly" else "к прошлой неделе"
        exp_line += f"  ({fmt_percent(r)} {to})"
    lines.append(exp_line)
    lines.append(f"{_pad('Доходы')}{_m(t.income, cur, 'always')}")
    flow = f"{_pad('Поток')}<b>{_m(t.net, cur, 'always')}</b>"
    if cur != "USD":
        tu = await Q.totals(session, _scope(u, rng), "USD")
        if tu.net:
            approx = format_money(Q.q_money(tu.net, "USD"), "USD", approx=True)  # «≈\u00a0−$148.33»
            flow += f" {html.escape(approx)}"
    lines.append(flow)

    rows = await Q.per_category(session, _scope(u, rng), cur, "expense")
    _, items = _slices(u, rows, cur)  # positive slices; shares over them (Σ = 100%)
    refunds = refunds_total(u, rows, cur)
    if items:
        top_n = 4 if kind == "monthly" else 3
        lines += ["", "Куда ушло:"]
        for s in items[:top_n]:
            lines.append(
                f"{category_emoji(s.name)} {html.escape(s.name)} — {_m(s.amount, cur)} · "
                f"{fmt_percent(s.share).lstrip('+')}"  # type: ignore[union-attr]
            )
        rest = items[top_n:]
        if rest:
            amt = sum((s.amount for s in rest), ZERO)
            sh = sum((s.share for s in rest), ZERO)
            lines.append(f"… остальное — {_m(amt, cur)} · {(fmt_percent(sh) or '0%').lstrip('+')}")
        if refunds:
            lines.append(f"↩️ Возвраты — {_m(refunds, cur, 'always')}")
    elif refunds:
        lines += ["", f"↩️ Возвраты — {_m(refunds, cur, 'always')}"]
    elif not t.count:
        lines += ["", "Операций не было."]

    extra: list[str] = []
    biz = await Q.totals(session, _scope(u, rng, is_business=True), "USD")
    if biz.income or biz.expense:
        extra.append(
            f"💼 Бизнес: итог {_m(biz.net, 'USD', 'always')} "
            f"(доход {_m(biz.income, 'USD')}, расход {_m(biz.expense, 'USD')})"
        )
    top = await Q.top_expenses(session, _scope(u, rng), "USD", 1)
    if top:
        tr = top[0]
        cname = tr.category_name
        root = u.root(tr.tx.category_id)
        if root in u.categories:
            cname = u.categories[root].name
        extra.append(
            f"🔝 Крупнейшая покупка: {html.escape(Q.tx_title(cname, tr.merchant_name, tr.tx))}"
            f" — {_m(tr.amount, 'USD')}"
        )
    if kind == "monthly":
        debts_line = await _debts_line(session, u.user_id, cur)
        if debts_line:
            extra.append(debts_line)
    need = await Q.needs_account_count(session, u.user_id)
    if need:
        extra.append(f"📥 Не разобрано: {need} {plural(need, 'операция', 'операции', 'операций')}")
    if t.missing:
        extra.append(
            f"⚠️ Без курса: {t.missing} {plural(t.missing, 'операция', 'операции', 'операций')}"
            " (не вошли в суммы)"
        )
    if extra:
        lines += ["", *extra]
    return "\n".join(lines)


async def _debts_line(session: Any, user_id: int, cur: str) -> str | None:
    """Monthly report (docs/DEBTS.md §12): «🤝 Долги: мне должны ≈ 300 000 ₫, я должен ≈
    1 000 000 ₫» when not zero. Debts are never income/expense — display only, current rates."""
    from app.services.debts.service import totals as debt_totals

    t = await debt_totals(session, user_id)
    if not t.owed_to_me_count and not t.i_owe_count:
        return None
    owed = t.owed_to_me_vnd if cur == "VND" else t.owed_to_me_usd
    owe = t.i_owe_vnd if cur == "VND" else t.i_owe_usd
    parts: list[str] = []
    for count, value, label in ((t.owed_to_me_count, owed, "мне должны"),
                                (t.i_owe_count, owe, "я должен")):
        if not count:
            continue
        if value is None:
            parts.append(f"{label} — нет курса")
        else:
            money = format_money(Q.q_money(value, cur), cur, approx=True)  # «≈ 300 000 ₫»
            parts.append(f"{label} {html.escape(money)}")
    return "🤝 Долги: " + ", ".join(parts)


def report_buttons(
    kind: ReportKind, rng: DateRange, needs_account: int
) -> list[list[dict[str, str]]]:
    """Outbox buttons. ``o:<route>`` → the bot turns it into a URL button PUBLIC_URL + route."""
    route = f"/analytics?period=custom&from={rng.date_from}&to={rng.date_to}"
    row = [{"text": "📊 Подробно", "callback": f"o:{route}"}]
    if needs_account and kind != "daily":
        row.append({"text": "📥 Разобрать", "callback": "o:/operations?status=needs_account"})
    return [row]


async def report_payload(
    session: Any, user_id: int, kind: ReportKind, on: date, *, now: datetime | None = None
) -> tuple[ReportOut, dict[str, Any]]:
    """Report + outbox payload ({text, parse_mode, buttons})."""
    from app.services.events.outbox import make_payload

    rep = await build_report(session, user_id, kind, on, now=now)
    need = await Q.needs_account_count(session, user_id)
    return rep, make_payload(rep.text_html, report_buttons(kind, rep.range, need))

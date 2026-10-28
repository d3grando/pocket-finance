"""Debts in Telegram — pure views: text + keyboard, no API calls.

docs/DEBTS.md §9 (reminder buttons), §11 (bot): ``/debts`` list («Мне должны / Я должен», per
currency, ≈ $ next to non-₫), person card ``dv:{id}`` (balances + last operations, [Записать
возврат] ``dr:{id}``, [Списать остаток] → send_text «списать долг: Коля»), the «Сколько вернул?»
prompt (``dra:{id}`` = «Весь долг»). Handlers: ``handlers.py`` (cmd_debts, cb_debt_*).

Sign convention of the backend (API.md §15): balance ``amount`` > 0 — the person owes me,
< 0 — I owe the person. Currencies are never mixed or converted; ``approx_*`` is display only.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from aiogram.types import InlineKeyboardButton

from pocket_bot import callbacks as cb
from pocket_bot.fmt import (
    DEBT_HISTORY_LABELS,
    debt_state,
    esc,
    format_money,
    parse_date,
    short_date,
    to_decimal,
)
from pocket_bot.render import Rendered, Renderer, clip, debt_person_rows, markup

Btn = InlineKeyboardButton

LIST_LIMIT = 15  # persons per list in /debts
HISTORY_LIMIT = 10  # operations on the person card
SNOOZE_DAYS = 7  # «Напомнить позже»
ALL_ROUTE = "/accounts?debt=all"
EMPTY_TEXT = ("Долгов нет 👌\n"
              "Записать: <code>дал в долг Коле 500к</code> · <code>занял у Пети 1м</code>")

#: replies to «Сколько вернул?» that mean the whole remaining debt
REPAY_ALL_WORDS = frozenset({
    "весь", "всё", "все", "всю", "весь долг", "всю сумму", "всё вернул", "все вернул",
    "полностью", "целиком", "all", "full",
})
_LEADING_VERB = re.compile(r"^(вернул[аи]?|отдал[аи]?|перевёл|перевел[аи]?)\s+", re.IGNORECASE)
_AMOUNT_START = re.compile(r"^[\d$€₽₫]")


# ------------------------------------------------------------------------------ texts
def repay_text(name: str, reply: str) -> str | None:
    """Reply to «Сколько вернул Коля?» → canonical assistant text (DEBTS.md §5.2):
    «200к» → «возврат долга: Коля 200к», «весь» → «возврат долга: Коля».
    Anything that does not look like an amount → None (an ordinary message, not a return)."""
    t = " ".join(reply.split()).strip().rstrip(".!")
    low = t.lower()
    if low in REPAY_ALL_WORDS:
        return f"возврат долга: {name}"
    t = _LEADING_VERB.sub("", t)
    if not t or len(t) > 60 or not _AMOUNT_START.match(t):
        return None
    return f"возврат долга: {name} {t}"


def forgive_text(name: str) -> str:
    return f"списать долг: {name}"


def balances_of(debt: dict[str, Any]) -> list[dict[str, Any]]:
    """Non-zero balances of a DebtOut (the backend already sends only non-zero ones)."""
    out = []
    for b in debt.get("balances") or []:
        amt = to_decimal(b.get("amount"))
        if amt is not None and amt != 0 and b.get("currency"):
            out.append(b)
    return out


def balance_text(b: dict[str, Any]) -> str:
    """«300 000 ₫» / «$50.00» / «50 USDT (≈ $50.00)» — always unsigned (the list says who owes)."""
    cur = str(b.get("currency") or "").upper()
    amt = to_decimal(b.get("amount")) or to_decimal("0")
    s = format_money(abs(amt), cur)
    usd = to_decimal(b.get("approx_usd"))
    if cur not in ("VND", "USD") and usd is not None:
        s += f" ({format_money(abs(usd), 'USD', approx=True)})"
    return s


def age_days(balances: list[dict[str, Any]], today: date) -> int | None:
    """Age of the oldest open balance (``open_since``), in days."""
    days = [(today - d).days for d in (parse_date(b.get("open_since")) for b in balances) if d]
    return max(days) if days else None


def _age_suffix(balances: list[dict[str, Any]], today: date) -> str:
    n = age_days(balances, today)
    return f" · {n} дн." if n and n > 0 else ""


def person_line(debt: dict[str, Any], today: date) -> str:
    """«• Коля — 300 000 ₫, 50 USDT (≈ $50.00) · 30 дн.» (escaped HTML)."""
    bals = balances_of(debt)
    amounts = ", ".join(balance_text(b) for b in bals) or "—"
    return f"• {esc(debt.get('name'))} — {esc(amounts)}{_age_suffix(bals, today)}"


def _total_line(label: str, debts: list[dict[str, Any]], vnd: Any, usd: Any,
                missing: bool) -> str:
    """«Мне должны ≈ <b>12 300 000 ₫</b>»; exact (no ≈) when every balance is in ₫."""
    only_vnd = all(str(b.get("currency") or "").upper() == "VND"
                   for d in debts for b in balances_of(d))
    if to_decimal(vnd) is not None:
        exact = only_vnd and not missing
        money = format_money(abs(to_decimal(vnd)), "VND")
        return f"{label} {'' if exact else '≈ '}<b>{esc(money)}</b>"
    if to_decimal(usd) is not None:
        return f"{label} ≈ <b>{esc(format_money(abs(to_decimal(usd)), 'USD'))}</b>"
    return label


def _section(label: str, debts: list[dict[str, Any]], vnd: Any, usd: Any, missing: bool,
             today: date) -> list[str]:
    lines = [_total_line(label, debts, vnd, usd, missing)]
    for d in debts[:LIST_LIMIT]:
        lines.append(person_line(d, today))
    if len(debts) > LIST_LIMIT:
        lines.append(f"… и ещё {len(debts) - LIST_LIMIT}")
    return lines


def open_debts(data: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """DebtsOut → (owed_to_me, i_owe) without persons that have no open balance."""
    data = data or {}
    return ([d for d in data.get("owed_to_me") or [] if balances_of(d)],
            [d for d in data.get("i_owe") or [] if balances_of(d)])


def debt_sections(r: Renderer, data: dict[str, Any] | None) -> list[str]:
    """DebtsOut → «Мне должны ≈ …» / «Я должен ≈ …» sections, each with up to ``LIST_LIMIT``
    persons and «… и ещё N», plus the missing-rates hint (escaped HTML; every section starts
    with an empty line). Shared by ``/debts`` and the assistant's «мои долги» answer card, so a
    long «Мне должны» list never hides «Я должен». Empty list → nobody owes anything."""
    owed, owe = open_debts(data)
    if not owed and not owe:
        return []
    t = (data or {}).get("totals") or {}
    missing = bool(t.get("missing_rates"))
    today = r.today()
    lines: list[str] = []
    if owed:
        lines += [""] + _section("Мне должны", owed, t.get("owed_to_me_vnd"),
                                 t.get("owed_to_me_usd"), missing, today)
    if owe:
        lines += [""] + _section("Я должен", owe, t.get("i_owe_vnd"), t.get("i_owe_usd"),
                                 missing, today)
    if missing:
        lines += ["", "⚠️ Не для всех валют есть курс — итог ≈ неполный."]
    return lines


# ------------------------------------------------------------------------------ views
def debts_view(r: Renderer, data: dict[str, Any]) -> Rendered:
    """GET /debts (DebtsOut) → «💸 Долги» + [👤 person] dv:{id} (≤ 8) + «Открыть в Pocket»."""
    sections = debt_sections(r, data)
    if not sections:
        return Rendered(EMPTY_TEXT)
    owed, owe = open_debts(data)
    lines = ["💸 <b>Долги</b>", *sections]
    rows = debt_person_rows({"owed_to_me": owed, "i_owe": owe})
    btn = r.url_button("📂 Открыть в Pocket", ALL_ROUTE)
    if btn:
        rows.append([btn])
    return Rendered(clip("\n".join(lines)), markup(rows))


def _state_lines(debt: dict[str, Any], today: date) -> list[str]:
    name = debt.get("name")
    owed = [b for b in balances_of(debt) if (to_decimal(b.get("amount")) or 0) > 0]
    owe = [b for b in balances_of(debt) if (to_decimal(b.get("amount")) or 0) < 0]
    lines: list[str] = []
    if owed:
        lines.append(f"{esc(name)}: долг тебе {esc(' и '.join(balance_text(b) for b in owed))}"
                     f"{_age_suffix(owed, today)}")
    if owe:
        lines.append(f"Ты должен {esc(' и '.join(balance_text(b) for b in owe))}"
                     f"{_age_suffix(owe, today)}")
    return lines


def history_line(item: dict[str, Any]) -> str:
    """«20.10 · Возврат 200 000 ₫ · долг тебе 300 000 ₫» (escaped HTML)."""
    tx = item.get("transaction") or {}
    op = str(item.get("op") or (tx.get("debt") or {}).get("op") or "")
    delta = to_decimal(item.get("delta"))
    label = DEBT_HISTORY_LABELS.get(op, "Операция")
    if op == "forgive" and delta is not None and delta > 0:
        label = "Прощён"  # the person forgave my debt
    cur = item.get("currency") or tx.get("currency")
    amount = format_money(abs(delta), cur) if delta is not None else \
        format_money(tx.get("amount"), cur)
    after = debt_state(item.get("balance_after"), cur)
    return f"{short_date(tx.get('occurred_on'))} · {esc(label)} {esc(amount)} · {esc(after)}"


def debt_card_view(r: Renderer, detail: dict[str, Any]) -> Rendered:
    """GET /debts/{id} (DebtDetailOut) → person card. ``options[0]`` = «списать долг: Коля»
    (the «Списать остаток» button is a send_text t:0, DEBTS.md §11)."""
    debt = detail.get("debt") or {}
    did = debt.get("id")
    name = str(debt.get("name") or "")
    head = f"👤 <b>{esc(name)}</b>"
    if debt.get("is_archived"):
        head += " · в архиве"
    lines = [head]
    state = _state_lines(debt, r.today())
    lines += state or ["✅ Долгов нет — всё возвращено."]
    if debt.get("note"):
        lines.append(f"📝 {esc(debt['note'])}")
    history = list(detail.get("history") or [])
    if history:
        lines += ["", "<b>История</b>"]
        lines += [history_line(it) for it in history[:HISTORY_LIMIT]]
        total = int(detail.get("total") or len(history))
        shown = min(len(history), HISTORY_LIMIT)
        if total > shown:
            lines.append(f"… всего {total} — в Pocket")
    rows: list[list[InlineKeyboardButton]] = []
    options: list[str] = []
    if state and did:
        options.append(forgive_text(name))
        rows.append([Btn(text="💰 Записать возврат", callback_data=cb.debt_repay(int(did))),
                     Btn(text="🤝 Списать остаток", callback_data=cb.send_text(0))])
    btn = r.url_button("📂 Открыть в Pocket", f"/accounts?debt={did}" if did else ALL_ROUTE)
    if btn:
        rows.append([btn])
    return Rendered(clip("\n".join(lines)), markup(rows), options)


def repay_prompt(r: Renderer, debt: dict[str, Any]) -> Rendered:
    """«💰 Сколько вернул Коля? Напиши сумму…» + [✅ Весь долг] dra:{id} [✖️ Отмена] z."""
    name = str(debt.get("name") or "")
    bals = balances_of(debt)
    owed = any((to_decimal(b.get("amount")) or 0) > 0 for b in bals)
    if owed or not bals:
        ask = f"💰 Сколько вернул {esc(name)}?"
    else:
        ask = f"💰 Сколько ты вернул? Долг: {esc(name)}."
    lines = [(f"{ask} Напиши сумму — например, <code>200к</code> "
              "(можно со счётом: <code>200к налом</code>).")]
    now: list[str] = []
    for b in bals:
        amt = to_decimal(b.get("amount")) or 0
        who = f"{name}: долг тебе" if amt > 0 else "ты должен"
        now.append(f"{who} {balance_text(b)}")
    if now:
        lines.append(f"Сейчас: {esc('; '.join(now))}.")
    rows = [[Btn(text="✅ Весь долг", callback_data=cb.debt_repay_all(int(debt["id"]))),
             Btn(text="✖️ Отмена", callback_data=cb.CANCEL_PROMPT)]]
    return Rendered("\n".join(lines), markup(rows))


SETTLED_TEXT = "✅ Долг уже закрыт — записывать нечего."
SNOOZED_TEXT = f"⏰ Напомню через {SNOOZE_DAYS} дней."

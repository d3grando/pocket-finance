"""Assistant cards for debts (docs/DEBTS.md §8). Backward compatible: only the
existing card kinds and buttons; the new data rides in the additive ``debt`` / ``debts`` fields,
so Electron 1.0.0 renders title / subtitle / note / buttons as usual. Names are nominative after
a colon («Дал в долг: Коля») — no declension in titles.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal

from app.core.money import format_money
from app.schemas.assistant import (
    AccountOption,
    AnswerCard,
    AnswerLine,
    Button,
    CreatedCard,
    NeedsAccountCard,
)
from app.schemas.debts import DebtBrief, DebtOpResult, DebtOut, DebtsOut
from app.schemas.transactions import AccountRef, TransactionOut
from app.services.ai import cards, guards
from app.services.debts.totals import totals_of

OUTFLOW = ("lend", "repay_out")
INFLOW = ("repay_in", "borrow")
_TITLE = {"lend": "Дал в долг", "repay_in": "Возврат долга", "borrow": "Занял",
          "repay_out": "Вернул долг"}
_SUMMARY = {"lend": "Долг", "repay_in": "Возврат", "borrow": "Занял", "repay_out": "Вернул долг"}
_SYMBOL = {"VND": "₫", "USD": "$", "RUB": "₽", "EUR": "€"}
OPEN_ALL = "/accounts?debt=all"


def symbol(currency: str) -> str:
    return _SYMBOL.get(currency.upper(), currency.upper())


# ------------------------------------------------------------------ names


_CYR_WORD = re.compile(r"^[А-ЯЁа-яё]+$")


def _decline(name: str, case: str) -> str | None:
    """Genitive / instrumental of a one-word Russian name («Коля» → «Коли» / «Колей»); None when
    unsure (several words, Latin, @handle) — the caller then uses «…: Имя» without declension."""
    if not _CYR_WORD.match(name or ""):
        return None
    w, last = name, name[-1].lower()
    prev = name[-2].lower() if len(name) > 1 else ""
    hush = prev in "гкхжшчщ"
    if case == "gen":
        if last == "я":
            return w[:-1] + "и"
        if last == "а":
            return w[:-1] + ("и" if hush else "ы")
        if last in "йь":
            return w[:-1] + "я"
        if last in "бвгджзклмнпрстфхцчшщ":
            return w + "а"
        return None
    if last == "я":
        return w[:-1] + "ей"
    if last == "а":
        return w[:-1] + ("ей" if prev in "жшчщц" else "ой")
    if last in "йь":
        return w[:-1] + "ем"
    if last in "жшчщц":
        return w + "ем"
    if last in "бвгдзклмнпрстфх":
        return w + "ом"
    return None


def at_person(name: str) -> str:
    """«у Коли» (genitive) or «: Коля» style fallback used in «Долга у Коли нет»."""
    g = _decline(name, "gen")
    return f"у {g}" if g else f"у «{name}»"


def before_person(name: str) -> str:
    """«перед Петей» (instrumental) or a quoted fallback."""
    i = _decline(name, "ins")
    return f"перед {i}" if i else f"перед «{name}»"


# ------------------------------------------------------------------ texts


def op_label(op: str, name: str, tx_kind: str | None = None) -> str:
    if op == "forgive":
        return f"Долг прощён: {name}" if tx_kind == "income" else f"Списан долг: {name}"
    return f"{_TITLE.get(op, 'Долг')}: {name}"


def debt_title(tx: TransactionOut, op: str, name: str) -> str:
    return f"{format_money(tx.amount, tx.currency)} · {op_label(op, name, tx.kind)}"


def op_summary(op: str, name: str, amount: Decimal, currency: str,
               tx_kind: str | None = None) -> str:
    """Action-log summary (undo shows «Отменено: …»): «Долг: Коля +500 000 ₫», «Возврат: Коля
    −200 000 ₫» — the sign is the change of «Коля должен мне»."""
    if op == "forgive":
        signed = amount if tx_kind == "income" else -amount
        label = "Долг прощён" if tx_kind == "income" else "Списан долг"
    else:
        signed = amount if op in OUTFLOW else -amount
        label = _SUMMARY.get(op, "Долг")
    return f"{label}: {name} {format_money(signed, currency, sign='always')}"


def real_account(tx: TransactionOut, op: str) -> AccountRef | None:
    """The real (non-debt) side: source for lend/repay_out, recipient for repay_in/borrow."""
    if op in OUTFLOW:
        return tx.account
    if op in INFLOW:
        return tx.counter_account
    return None


def balance_text(op: str, name: str, balance: Decimal, currency: str) -> str:
    if balance == 0:
        return "долг закрыт"
    amt = format_money(abs(balance), currency)
    if op == "repay_in":
        return f"осталось {amt}" if balance > 0 else "долг закрыт"
    if op == "repay_out":
        return f"осталось {amt}" if balance < 0 else "долг закрыт"
    if op == "forgive":
        return f"осталось {amt}"
    return owed_text(name, amt) if balance > 0 else f"ты должен {amt}"


def owed_text(name: str, amt: str) -> str:
    """«Маша: долг тебе 1 000 000 ₫» — no gender agreement needed (a name ending in -а/-я can be
    Коля or Маша, «Маша должен» reads illiterate). The owner is «ты» as everywhere."""
    return f"{name}: долг тебе {amt}"


def brief(res: DebtOpResult, op: str) -> DebtBrief:
    return DebtBrief(debt_id=res.debt.id, name=res.debt.name, op=op,  # type: ignore[arg-type]
                     currency=res.transaction.currency, balance_after=res.balance_after,
                     excess=res.excess)


def _op_of(res: DebtOpResult) -> str:
    return res.transaction.debt.op if res.transaction.debt else "lend"


def excess_text(op: str, name: str, excess: Decimal, currency: str) -> str:
    amt = format_money(excess, currency)
    if op == "repay_in":  # he paid more than he owed → now I owe him the surplus
        return f"Излишек {amt} — теперь ты должен: {name}"
    return f"Излишек {amt} — теперь это долг тебе: {name}"


def excess_button(op: str, name: str, excess: Decimal, currency: str) -> Button:
    """The button names the direction and the amount: a stale «Излишек — мне» pressed after a
    new loan must not forgive what the person owes me (the validator refuses a direction
    mismatch or an amount above the balance instead of following the current sign)."""
    short = guards.short_amount(excess, currency)
    if op == "repay_in":
        return cards.btn("excess", "Излишек — мне", "send_text", style="outline",
                         text=f"списать мой долг: {name} {short}")
    return cards.btn("excess", "Излишек — расход", "send_text", style="outline",
                     text=f"простить долг: {name} {short}")


def _excess(op: str, name: str, excess: Decimal | None, currency: str
            ) -> tuple[str | None, Button | None]:
    if excess is None or excess <= 0 or op not in ("repay_in", "repay_out"):
        return None, None
    return excess_text(op, name, excess, currency), excess_button(op, name, excess, currency)


def excess_of(op: str, amount: Decimal, balance: Decimal) -> Decimal | None:
    """Surplus of a repayment whose balance crossed zero against its direction (also after an
    edit «нет, 500к»): the part of the operation beyond the debt."""
    if op == "repay_in" and balance < 0:
        return min(amount, -balance)
    if op == "repay_out" and balance > 0:
        return min(amount, balance)
    return None


# ------------------------------------------------------------------ cards


def created_card(res: DebtOpResult, action_id: uuid.UUID, *, tz_name: str, today: date,
                 op: str | None = None) -> CreatedCard:
    """«500 000 ₫ · Дал в долг: Коля» / «Карта · Коля: долг тебе 500 000 ₫» (+ undo / edit)."""
    tx, name = res.transaction, res.debt.name
    op = op or _op_of(res)
    parts: list[str] = []
    if op == "forgive":
        parts.append("Переводы людям" if tx.kind == "expense" else (
            f"{tx.category.name if tx.category else 'Прочее'} (доход)"))
    else:
        acc = real_account(tx, op)
        parts.append(acc.name if acc else "без счёта")
    parts.append(balance_text(op, name, res.balance_after, tx.currency))
    if tx.occurred_on != today:
        parts.append(cards.day_label(tx, tz_name, today))
    note, extra = _excess(op, name, res.excess, tx.currency)
    buttons = ([extra] if extra else []) + [cards.undo_btn(action_id), cards.edit_btn(tx.id)]
    return CreatedCard(
        title=debt_title(tx, op, name), subtitle=" · ".join(parts), action_id=action_id,
        transaction=tx, approx=cards.approx_of(tx), note=note, buttons=buttons,
        debt=brief(res, op),
    )


def needs_account_card(res: DebtOpResult, action_id: uuid.UUID,
                       options: list[AccountOption], *, op: str | None = None
                       ) -> NeedsAccountCard:
    """Crypto / $ without an account: the debt is recorded, the real side waits for
    «С какого счёта?» (outflow) / «На какой счёт?» (inflow)."""
    tx, name = res.transaction, res.debt.name
    op = op or _op_of(res)
    sub = [balance_text(op, name, res.balance_after, tx.currency)]
    if res.balance_after != 0 and (ap := approx_balance(tx, res.balance_after)) is not None:
        sub.append(ap)  # ≈ of the balance it stands next to, not of the operation
    buttons: list[Button] = []
    for o in options:
        if o.account_id is None:
            buttons.append(cards.btn("acc-none", o.label, "choose_account", hotkey=o.hotkey,
                                     transaction_id=tx.id, account_id=None))
        else:
            buttons.append(cards.btn(f"acc-{o.account_id}", o.label, "choose_account",
                                     style="outline", hotkey=o.hotkey, transaction_id=tx.id,
                                     account_id=o.account_id))
    buttons += [cards.btn("later", "Позже", "later"), cards.undo_btn(action_id)]
    return NeedsAccountCard(
        title=debt_title(tx, op, name), subtitle=" · ".join(sub), action_id=action_id,
        transaction=tx, question="На какой счёт?" if op in INFLOW else "С какого счёта?",
        options=options, buttons=buttons, debt=brief(res, op),
    )


def approx_balance(tx: TransactionOut, balance: Decimal) -> str | None:
    """«≈ 3 122 400 ₫» for a $120 balance, by the operation's own rate (amount_vnd / amount for
    USD, amount_usd / amount otherwise — same targets as ``cards.approx_of``)."""
    cur = tx.currency.upper()
    if cur == "VND" or not tx.amount:
        return None
    if cur == "USD":
        if not tx.amount_vnd:
            return None
        return format_money(abs(balance) * tx.amount_vnd / tx.amount, "VND", approx=True)
    if tx.amount_usd is None:
        return None
    return format_money(abs(balance) * tx.amount_usd / tx.amount, "USD", approx=True)


def _bal_str(d: DebtOut) -> str:
    return " · ".join(format_money(abs(b.amount), b.currency) for b in d.balances)


def _line(d: DebtOut, owed: bool) -> AnswerLine:
    single = d.balances[0] if len(d.balances) == 1 else None
    return AnswerLine(label=d.name, value=("долг тебе " if owed else "ты должен ") + _bal_str(d),
                      amount=abs(single.amount) if single else None,
                      currency=single.currency if single else None, icon="HandCoins")


def _vnd(v: Decimal | None) -> str | None:
    return format_money(v, "VND") if v is not None else None


def debts_answer_card(out: DebtsOut, *, direction: str | None = None) -> AnswerCard:
    """«кто мне должен?» / /debts: headline «Мне должны ≈ X ₫», lines per person."""
    owed = out.owed_to_me if direction != "i_owe" else []
    iowe = out.i_owe if direction != "owed_to_me" else []
    shown = DebtsOut(owed_to_me=owed, i_owe=iowe, totals=out.totals)
    btn = cards.btn("open", "Открыть долги →", "open", route=OPEN_ALL)
    if not owed and not iowe:
        empty = {"owed_to_me": "Тебе никто не должен",
                 "i_owe": "Ты никому не должен"}.get(direction or "", "Долгов нет")
        return AnswerCard(title="Долги", subtitle=empty, answer_type="balance", text=empty,
                          debts=shown, buttons=[btn])
    t = out.totals
    headline: str | None = None
    approx: str | None = None
    text: str | None = None
    if owed:
        v = _vnd(t.owed_to_me_vnd)
        headline = f"Мне должны ≈ {v}" if v else "Мне должны"
        approx = format_money(t.owed_to_me_usd, "USD", approx=True) if t.owed_to_me_usd else None
        if iowe:
            iv = _vnd(t.i_owe_vnd)
            text = f"Я должен ≈ {iv}" if iv else "Я тоже должен"
    else:
        iv = _vnd(t.i_owe_vnd)
        headline = f"Я должен ≈ {iv}" if iv else "Я должен"
        approx = format_money(t.i_owe_usd, "USD", approx=True) if t.i_owe_usd else None
    lines = [_line(d, True) for d in owed] + [_line(d, False) for d in iowe]
    return AnswerCard(title="Долги", answer_type="balance", headline=headline,
                      headline_approx=approx, lines=lines, text=text, debts=shown,
                      buttons=[btn])


def person_answer_card(out: DebtsOut, debt_id: int | None, name: str) -> AnswerCard:
    """«сколько должен Коля?»: the person's balances in both directions; ``debts.totals`` are this
    person's only (not every debt of the user)."""
    owed = [d for d in out.owed_to_me if d.id == debt_id]
    iowe = [d for d in out.i_owe if d.id == debt_id]
    route = f"/accounts?debt={debt_id}" if debt_id else OPEN_ALL
    btn = cards.btn("open", "Открыть →", "open", route=route)
    if not owed and not iowe:
        sub = "Долга нет" if debt_id else f"Не знаю долгов: {name}"
        return AnswerCard(title=f"Долг: {name}", subtitle=sub, answer_type="balance",
                          headline="Долга нет", text=sub,
                          debts=DebtsOut(totals=totals_of([], [])), buttons=[btn])
    lines = [_line(d, True) for d in owed] + [_line(d, False) for d in iowe]
    head = (owed_text(name, _bal_str(owed[0])) if owed
            else f"Ты должен {_bal_str(iowe[0])}")
    text = f"Ты должен {_bal_str(iowe[0])}" if owed and iowe else None
    return AnswerCard(title=f"Долг: {name}", answer_type="balance", headline=head, lines=lines,
                      text=text, debts=DebtsOut(owed_to_me=owed, i_owe=iowe,
                                     totals=totals_of(owed, iowe)),
                      buttons=[btn])


__all__ = [
    "OPEN_ALL", "approx_balance", "at_person", "balance_text", "before_person", "brief",
    "created_card", "debt_title", "debts_answer_card", "excess_button", "excess_of",
    "excess_text", "needs_account_card", "op_label", "op_summary", "owed_text",
    "person_answer_card", "real_account", "symbol",
]

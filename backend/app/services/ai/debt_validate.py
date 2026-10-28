"""Validation of debt intents (docs/DEBTS.md §5.4). Nothing is written here.

The person is resolved with ``debts.find_debt`` (declension-insensitive), balances come from
``debts.balances_of`` — the DB, not the parser's context, decides the п.5 ambiguity (DEBTS.md §0):
«Коля вернул 200к» without an open «Коля должен мне» in ₫ becomes an ordinary income
(«Прочее», counterparty Коля) with a note; an explicit «вернул долг» without such a balance is
a question with ready answers. ``repay`` gets its direction from the balance sign.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.core.money import format_money, is_fiat
from app.core.person_names import capitalize, name_key, same_person, words
from app.core.timeutil import tz
from app.models import Debt
from app.schemas.intents import CreateTransactionIntent, DebtIntent, DebtsQueryIntent
from app.services.ai import debt_cards as dc
from app.services.ai import guards
from app.services.ai.validator import ValidatedIntent
from app.services.debts import service as debts_svc
from app.services.parser import debt_words as dw
from app.services.parser.debts import is_button_text
from app.services.parser.text import normalize

if TYPE_CHECKING:
    from app.services.ai.validator import _Validator

_CANON = {"lend": "дал в долг", "borrow": "занял", "repay_in": "возврат долга",
          "repay_out": "возврат долга", "repay": "возврат долга", "forgive": "списать долг"}
_HANDLE_RE = re.compile(r"^@([A-Za-z0-9_]{1,64})$")
_OWE_RE = re.compile(r"(?<![^\W\d_])(?:должен|должна|должны|должно|owe|owes)(?![^\W\d_])")
_WORD_RE = re.compile(r"[^\W\d_]+")


@dataclass
class Person:
    debt: Debt | None
    name: str  # display name (nominative)
    handle: str | None


def _handle(ref: str) -> str | None:
    m = _HANDLE_RE.match(ref.strip())
    return m.group(1).lower() if m else None


def display_name(ref: str, person_name: str | None) -> str:
    if person_name and person_name.strip():
        return person_name.strip()[:56]
    if _handle(ref):
        return ref.strip()[:56]
    name = capitalize(" ".join(words(ref)))[:56]
    return dw.keep_yo(ref, name) or ref.strip()[:56]


def owe_statement(text: str) -> bool:
    """«Петя должен мне 300к», «я должен Пете 1м», «Bob owes me 50$»: a debt that already
    exists — no verb of money movement, so no account may be charged."""
    norm = normalize(text or "")
    return _OWE_RE.search(norm) is not None and not any(
        w in dw.VERBS for w in _WORD_RE.findall(norm))


def _generic(v: _Validator, it: DebtIntent) -> bool:
    """«другу», «у соседа», «коллеге» without a name: which one? (unless the model found the
    name in the text: «другу Саше»)."""
    if not dw.is_generic(it.person):
        return False
    pn = (it.person_name or "").strip()
    return not (pn and not dw.is_generic(pn) and in_text(v.text or "", pn))


def in_text(text: str, ref: str) -> bool:
    """The person really is in the message (the LLM must not invent a counterparty)."""
    h = _handle(ref)
    if h:
        return f"@{h}" in text.lower()
    pw = words(ref)
    if not pw:
        return False
    key = name_key(pw[0])
    return any(same_person(name_key(w), key) for w in re.findall(r"[^\W\d_]+", text.lower()))


# «10к», «1.5м», «2 тыс», «3kk» — a thousands/millions multiplier written next to the number
_MULT_RE = re.compile(r"\d\s*(к|k|тыс|тр|м|m|млн|лям|kk)(?![а-яa-z])", re.I)


def _short(amount: Decimal | None, currency: str) -> str:
    return guards.short_amount(amount, currency) if amount is not None else ""


def canon_text(op: str, name: str, amount: Decimal | None = None, currency: str = "VND") -> str:
    return " ".join(f"{_CANON[op]}: {name} {_short(amount, currency)}".split())


async def find_person(v: _Validator, it: DebtIntent | DebtsQueryIntent, ref: str,
                      person_name: str | None, *, amount: Decimal | None = None,
                      currency: str = "VND") -> Person:
    uid = int(v.uid or 0)
    hit = await debts_svc.find_debt(v.s, uid, ref)
    if hit is None and person_name and person_name.strip() and person_name != ref:
        hit = await debts_svc.find_debt(v.s, uid, person_name)
    if isinstance(hit, list):
        if len(hit) == 1:
            hit = hit[0]
        elif hit:
            names = [d.name for d in hit][:4]
            if isinstance(it, DebtIntent):
                opts = [canon_text(it.op, n, amount, currency) for n in names]
            else:
                opts = [f"долг: {n}?" for n in names]
            raise v.fail(f"Какой именно? Есть: {', '.join(names)}", "ambiguous_target", opts)
        else:
            hit = None
    name = hit.name if hit is not None else display_name(ref, person_name)
    return Person(hit, name, (hit.handle if hit is not None else None) or _handle(ref))


async def _balances(v: _Validator, debt: Debt | None) -> dict[str, Decimal]:
    if debt is None:
        return {}
    raw = await debts_svc.balances_of(v.s, debt)
    return {str(k).upper(): Decimal(str(x)) for k, x in raw.items() if Decimal(str(x)) != 0}


def _want(op: str, direction: str | None) -> int:
    """Balance sign an operation needs: +1 «должен мне», −1 «я должен», 0 any."""
    if op == "repay_in" or (op == "forgive" and direction == "owed_to_me"):
        return 1
    if op == "repay_out" or (op == "forgive" and direction == "i_owe"):
        return -1
    return 0


def _pick_currency(v: _Validator, it: DebtIntent, p: Person, bals: dict[str, Decimal],
                   acc_currency: str | None) -> str:
    """«весь долг» / «простить» without a currency: the only open one, else ask."""
    want = _want(it.op, it.direction)
    cands = [c for c, b in bals.items() if want == 0 or (b > 0) == (want > 0)]
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        op = "forgive" if it.op == "forgive" else "repay"
        opts = [canon_text(op, p.name, abs(bals[c]), c) if op == "repay"
                else f"{canon_text(op, p.name)} {c}" for c in cands[:4]]
        raise v.fail(f"В какой валюте? Открыто: {', '.join(cands)}", "ambiguous_currency", opts)
    return (acc_currency or v.ctx.default_currency or "VND").upper()


def _no_debt(v: _Validator, p: Person, op: str, currency: str, bals: dict[str, Decimal],
             amount: Decimal | None) -> Exception:
    """Explicit «вернул долг» without a balance of that sign in that currency (§5.4)."""
    pos = op == "repay_in"
    others = [c for c, b in bals.items() if (b > 0) == pos and c != currency]
    where = f" (есть: {', '.join(others)})" if others else ""
    sym = dc.symbol(currency)
    opts: list[str] = []
    short = _short(amount, currency)
    if pos:
        msg = _cap(f"{dc.at_person(p.name)} нет долга в {sym}{where}")
        if short:
            opts.append(f"{p.name} вернул(а) {short}")
    else:
        msg = _cap(f"{dc.before_person(p.name)} нет долга в {sym}{where}")
        if short:
            opts += [canon_text("lend", p.name, amount, currency), f"{short} Переводы людям"]
    opts += [canon_text("repay", p.name, abs(bals[c]), c) for c in others]
    return v.fail(msg, "other", opts[:4])


def _who(v: _Validator, it: DebtIntent, amount: Decimal | None, currency: str) -> Exception:
    """«одолжил другу 200к»: which friend? Nothing is written, no person «Другу» is created."""
    example = canon_text(it.op if it.op in _CANON else "lend", "Коля", amount, currency or "VND")
    return v.fail(f"Кто именно — «{it.person.strip()[:40]}»? Напиши имя, например: «{example}»",
                  "ambiguous_target")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _when(v: _Validator, it: DebtIntent) -> tuple[Any, datetime | None]:
    day = v.day(it.occurred_on)
    at = None
    if it.occurred_time is not None:
        day = day or v.ctx.today
        at = datetime.combine(day, it.occurred_time, tzinfo=tz(v.ctx.timezone))
        if at > v.ctx.now + timedelta(days=1):
            raise v.fail("Время операции в будущем — проверьте")
    return day, at


def _fallback(v: _Validator, it: DebtIntent, p: Person, currency: str, *,
              other_currencies: bool = False, note: bool = True) -> ValidatedIntent:
    """п.5: no open debt of that sign → the ordinary income / expense «Переводы людям»."""
    if it.amount is None:
        raise v.fail("Не хватает суммы", "missing_amount")
    income = it.fallback == "income"
    cp = f"@{p.handle}" if p.handle and p.name.startswith("@") else p.name
    ct = CreateTransactionIntent(
        kind="income" if income else "expense", amount=it.amount,
        amount_raw=it.amount_raw, currency=currency, account_ref=it.account_ref,
        account_none=it.account_none,
        category_ref=(it.category_ref or "Прочее") if income else "Переводы людям",
        counterparty=cp[:128], description=it.description, occurred_on=it.occurred_on,
        occurred_time=it.occurred_time,
    )
    vi = v.create(ct)
    if not note:
        return vi
    where = f" в {dc.symbol(currency)}" if other_currencies else ""
    if income:
        vi.resolved["note"] = f"Долга {dc.at_person(p.name)}{where} нет — записал как доход"
    else:
        vi.resolved["note"] = (f"Долга {dc.before_person(p.name)}{where} нет — записал в расходы "
                               "«Переводы людям»")
    return vi


async def validate_debt(v: _Validator, it: DebtIntent) -> ValidatedIntent:
    if v.origin == "llm" and v.text and not in_text(v.text, it.person):
        raise v.unsafe(f"Не нашёл «{it.person[:40]}» в тексте — напишите имя, например "
                       "«дал в долг Коле 500к»")
    op = it.op
    if op in ("lend", "borrow") and owe_statement(v.text) and not it.account_none:
        # «Петя должен мне 300к» states a debt: no money moved, Карта is not touched
        it = it.model_copy(update={"account_none": True, "account_ref": None})
    generic = _generic(v, it)
    if generic and it.fallback and (it.amount is not None or it.amount_raw):
        # «отдал другу 100к»: nobody is named, so no debt can be meant — the ordinary operation
        gacc = None if it.account_none else v.account(it.account_ref)
        cur = v.currency(it.currency, gacc)
        amt = v.amount(it.amount, it.amount_raw, cur)
        return _fallback(v, it.model_copy(update={"amount": amt}),
                         Person(None, it.person.strip()[:56], None), cur, note=False)
    acc = None if (op == "forgive" or it.account_none) else v.account(it.account_ref)
    has_amount = it.amount is not None or bool(it.amount_raw)
    if it.fallback and not has_amount:
        raise v.fail("Не хватает суммы", "missing_amount")
    if not has_amount and op in ("lend", "borrow"):
        example = canon_text(op, display_name(it.person, it.person_name))
        raise v.fail(f"Сколько? Например: «{example} 500к»", "missing_amount")
    amount: Decimal | None = None
    currency = ""
    if has_amount:  # the amount's currency never depends on the person's balances
        currency = v.currency(it.currency, acc)
        amount = v.amount(it.amount, it.amount_raw, currency)
    if generic and not is_button_text(v.text):
        raise _who(v, it, amount, currency)
    p = await find_person(v, it, it.person, it.person_name, amount=amount,
                          currency=currency or "VND")
    if generic and p.debt is None:
        raise _who(v, it, amount, currency)
    bals = await _balances(v, p.debt)
    if not has_amount:
        if it.currency:
            currency = v.currency(it.currency, acc, extra=tuple(bals))
        else:
            currency = _pick_currency(v, it, p, bals,
                                      acc.currency if acc and not acc.is_multi_asset else None)
    if (has_amount and not it.currency and op in ("repay", "repay_in", "repay_out", "forgive")
            and not (acc and acc.currency and not acc.is_multi_asset)
            and bals.get(currency, Decimal(0)) == 0):
        # «Дима вернул 10к» while Дима owes only in ₽ — that debt's currency, not the default ₫
        # (not a 10 000 ₫ income); the parser does the same
        want = _want(it.op, it.direction)
        cands = [c for c, b in bals.items() if b != 0 and (want == 0 or (b > 0) == (want > 0))]
        # «10к» of ₽ is normal, «100к» of USDT is not: a coin debt takes only a plain number
        if (len(cands) == 1 and cands[0] != currency
                and (is_fiat(cands[0]) or not _MULT_RE.search(it.amount_raw or ""))):
            currency = cands[0]
            amount = v.amount(it.amount, it.amount_raw, currency)
    bal = bals.get(currency, Decimal(0))
    if op in ("repay", "repay_in", "repay_out", "forgive") and p.debt is None:
        if it.fallback:
            return _fallback(v, it.model_copy(update={"amount": amount}), p, currency,
                             other_currencies=False)
        opts = [f"{p.name} вернул(а) {_short(amount, currency)}"] if op == "repay_in" and amount \
            else []
        raise v.fail(f"Не знаю долгов: {p.name}", "other", opts)
    if op == "repay":
        if bal == 0:
            raise _no_debt(v, p, "repay_in", currency, bals, amount)
        op = "repay_in" if bal > 0 else "repay_out"
    if op in ("repay_in", "repay_out") and not (bal > 0 if op == "repay_in" else bal < 0):
        if it.fallback:
            want = 1 if op == "repay_in" else -1
            return _fallback(v, it.model_copy(update={"amount": amount}), p, currency,
                             other_currencies=any((b > 0) == (want > 0) for b in bals.values()))
        raise _no_debt(v, p, op, currency, bals, amount)
    if op == "forgive":
        if bal == 0:
            raise v.fail(f"Долга нет: {p.name} — в {dc.symbol(currency)}", "other")
        sign_dir = "owed_to_me" if bal > 0 else "i_owe"
        if it.direction and it.direction != sign_dir:
            # never follow the current sign: a stale «Излишек — мне» pressed after a new loan
            # would forgive that loan as an expense (the service re-checks it: 422 direction)
            left = format_money(abs(bal), currency)
            who = dc.owed_text(p.name, left) if bal > 0 else f"Ты должен: {p.name} {left}"
            if amount is not None and is_button_text(v.text):
                raise v.fail(f"Этого излишка уже нет. Сейчас — {who}. Ничего не записал",
                             "other")
            raise v.fail(f"{who} — списать этот долг?", "other",
                         [canon_text("forgive", p.name) + f" {currency}"])
        if amount is not None and amount > abs(bal):
            raise v.fail(f"Списать можно не больше остатка: {format_money(abs(bal), currency)}")
    if (acc is not None and not acc.is_multi_asset and acc.currency
            and acc.currency.upper() != currency and not is_fiat(currency)):
        raise v.fail(f"На счёте {acc.name} нет {currency} — выберите другой счёт")
    day, at = _when(v, it)
    resolved: dict[str, Any] = {
        "debt_op": op, "debt_id": p.debt.id if p.debt is not None else None,
        "person_name": p.name, "handle": p.handle, "written_form": it.person.strip()[:64],
        "amount": amount, "currency": currency, "account_id": acc.id if acc else None,
        "account_none": bool(it.account_none) and op != "forgive", "occurred_on": day,
        "occurred_at": at, "description": it.description or None,
        # forgive: the service re-checks the direction under the lock (422 on a mismatch)
        "direction": it.direction if op == "forgive" else None,
    }
    fixed = it.model_copy(update={"op": op, "amount": amount, "currency": currency})
    return ValidatedIntent(fixed, resolved)


async def validate_query(v: _Validator, it: DebtsQueryIntent) -> ValidatedIntent:
    ref = it.person
    if not ref:
        return ValidatedIntent(it, {"debt_id": None, "name": None})
    if v.origin == "llm" and v.text and not in_text(v.text, ref):
        it = it.model_copy(update={"person": None})
        return ValidatedIntent(it, {"debt_id": None, "name": None})
    p = await find_person(v, it, ref, None)
    return ValidatedIntent(it, {"debt_id": p.debt.id if p.debt is not None else None,
                                "name": p.name})


__all__ = ["Person", "canon_text", "display_name", "find_person", "in_text", "validate_debt",
           "validate_query"]

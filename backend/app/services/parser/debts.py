"""Debt phrases → DebtIntent / DebtsQueryIntent (docs/DEBTS.md §5.2).

Deterministic, no LLM, no DB: «дал в долг Коле 500к», «Коля вернул 200к», «занял у Пети 1м»,
«отдал Пете 500к», «простить долг Коле», «Коля не вернёт», «рассчитался с Петей», button texts
(«возврат долга: Коля 200к», «списать долг: Коля»), questions («кто мне должен?»). Account
(«с кошелька», «налом»), date («вчера») and currency work as for ordinary operations, any word
order.

``try_debts`` returns None when the message has no debt meaning — the ordinary parser handles it
(«вернули 200к за наушники» stays a refund, «отдали 600к» an expense). Debt words it cannot
interpret → ``needs_llm`` (reason ``debt``), never a guess.

Rule of docs/DEBTS.md §0 п.5: «X вернул N» / «отдал X N» without the word «долг» → always a
DebtIntent with ``fallback``; the validator checks the DB and turns it into the ordinary
operation (income «Прочее» / expense «Переводы людям», counterparty = X) with the note
«Долга у X нет…» (DEBTS.md §15).
X must look like a person there (known debt, @handle or a capitalised non-vocabulary word).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.core.person_names import capitalize
from app.schemas.intents import (
    CtxDebt,
    DebtIntent,
    DebtsQueryIntent,
    ParseResult,
)
from app.services.parser import debt_words as dw
from app.services.parser import lexicon as lx
from app.services.parser.currencies import detect_currency, is_crypto
from app.services.parser.ops import (
    amount_of,
    card_fix,
    clarify_currency,
    describe,
    on_date,
    pick_account,
    resolve_currency,
    seg_date,
    seg_time,
)
from app.services.parser.segments import AccMention, Seg, analyze, split_message
from app.services.parser.state import State, done, llm
from app.services.parser.text import normalize, span_text, tokenize

_SKIP_ROLES = {"amount", "amount_sign", "qty", "time", "account", "sign", "sep"}
_RESET_ROLES = {"account", "merchant", "handle", "marker", "prep", "balance", "sign", "sep"}
_CANON_RE = re.compile(
    r"^\s*(дал в долг|занял|возврат долга|списать долг|списать мой долг|простить долг)\s*:")
#: button texts → (op, direction). «списать долг:» takes the direction from the balance sign
#: (bot «Списать остаток»); the excess buttons name it, so a stale button cannot forgive the
#: opposite debt («списать мой долг: Лёва 50к» never writes off what Лёва owes me).
_CANON_OP = {"дал в долг": ("lend", None), "занял": ("borrow", None),
             "возврат долга": ("repay", None), "списать долг": ("forgive", None),
             "списать мой долг": ("forgive", "i_owe"),
             "простить долг": ("forgive", "owed_to_me")}
_RU_PREPS = {"у", "от", "с", "со", "перед"}
_NEED = {"repay_in": "owed_to_me", "repay_out": "i_owe"}
CONF = 0.9


class _Unsure(Exception):
    def __init__(self, reason: str = "debt") -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class Clause:
    idxs: list[int]
    seg: Seg
    verb: str | None = None
    verb_k: int = -1
    extra_verb: bool = False
    person: list[int] = field(default_factory=list)
    person_k: int = -1
    person_prep: str | None = None
    handle: str | None = None
    groups: int = 0
    debt_word: bool = False
    all_word: bool = False
    me: bool = False
    u_me: bool = False
    my: bool = False
    i_word: bool = False
    neg: bool = False
    not_return: bool = False
    hint: bool = False
    back: bool = False
    purpose: list[int] = field(default_factory=list)
    currency_word: str | None = None

    @property
    def has_person(self) -> bool:
        return bool(self.person or self.handle)

    def pos(self) -> str | None:
        if not self.has_person or self.verb_k < 0:
            return None
        return "before" if self.person_k < self.verb_k else "after"


@dataclass(slots=True, frozen=True)
class Decision:
    op: str
    case: str
    fallback: str | None = None
    direction: str | None = None


@dataclass(slots=True)
class _Out:
    intent: object
    needs_llm: bool = False
    reason: str | None = None
    day: date | None = None
    conf: float = CONF


# ------------------------------------------------------------------ entry


def try_debts(st: State) -> ParseResult | None:
    if not dw.TRIGGER.search(st.norm):
        return None
    st.prepare()
    if not st.amounts:
        q = dw.query_parts(st.norm, st.orig)
        if q is not None:
            return done([DebtsQueryIntent(person=q[0], direction=q[1])], CONF)  # type: ignore[arg-type]
    try:
        m = _CANON_RE.match(st.norm)
        if m is not None:
            op, direction = _CANON_OP[m.group(1)]
            return _canonical(st, op, m.end(), direction)
        return _phrases(st)
    except _Unsure as e:
        return llm(e.reason, confidence=0.3, unparsed=st.orig)


def is_button_text(text: str) -> bool:
    """A canonical button text («возврат долга: Коля», «списать мой долг: Лёва 50к»)."""
    return _CANON_RE.match(normalize(text or "")) is not None


# ------------------------------------------------------------------ clauses


def _scan(st: State, idxs: list[int], *, canonical: bool = False) -> Clause:
    idxs = sorted(set(idxs))
    for i in idxs:
        if st.toks[i].role in _RESET_ROLES:
            st.toks[i].role = ""
    c = Clause(idxs=idxs, seg=analyze(st, idxs))
    prep: str | None = None
    in_purpose = False
    last_k = -2
    skip_next = False
    for k, i in enumerate(idxs):
        t = st.toks[i]
        if skip_next:
            skip_next = False
            continue
        role = t.role
        if role in _SKIP_ROLES or role.startswith("date"):
            prep, in_purpose = None, False
            continue
        if role == "prep":
            in_purpose = t.text in ("за", "for")
            continue
        if t.kind == "handle":
            h = t.text[1:]
            if h in st.R.my_handles:
                c.hint = True  # own wallet: a transfer, not a debt
            elif c.handle is None and not c.person:
                c.handle, c.person_k = h, k
                c.person_prep = prep if prep in _RU_PREPS else None
            else:
                c.groups += 1
            prep = None
            continue
        if t.kind != "word":
            continue
        w = t.text
        if w in dw.VERBS and not canonical:
            if c.verb is None:
                c.verb, c.verb_k = dw.VERBS[w], k
            else:
                c.extra_verb = True
            prep, in_purpose = None, False
            continue
        if w == "не":
            nxt = st.toks[idxs[k + 1]].text if k + 1 < len(idxs) else ""
            if nxt in dw.NOT_RETURN:
                c.not_return, skip_next = True, True
                if c.verb_k < 0:
                    c.verb_k = k
            else:
                c.neg = True
            continue
        if w in dw.DEBT_WORDS:
            c.debt_word, in_purpose, prep = True, False, None
            continue
        if w in dw.ALL_WORDS:
            c.all_word = True
            continue
        if w in dw.ME:
            c.me = True
            continue
        if w in dw.ME_OBJ:
            c.u_me = c.u_me or prep in ("у", "с", "со", "from", "with")
            prep = None
            continue
        if w in dw.I_WORDS:
            c.i_word = True
            continue
        if w in dw.MY:
            c.my = True
            continue
        if w in dw.PREPS:
            prep = w
            continue
        if w in dw.PURPOSE_PREPS:
            in_purpose = True
            continue
        if w in dw.HINT_WORDS:
            c.hint = True
            continue
        if w == "back":
            c.back = True
            continue
        if len(w) > 2 and (cur := detect_currency(w)):
            c.currency_word = cur
            continue
        if w in dw.NEUTRAL or w in lx.FILLERS or w in lx.BALANCE_WORDS:
            continue
        if in_purpose:
            c.purpose.append(i)
            continue
        if c.person and last_k == k - 1:
            c.person.append(i)
        elif c.person or c.handle:
            c.groups += 1
        else:
            c.person, c.person_k = [i], k
            c.person_prep = prep
        last_k = k
        prep = None
    return c


def _modifier_only(c: Clause) -> bool:
    return not (c.verb or c.has_person or c.debt_word or c.hint or c.not_return or c.neg
                or c.seg.amounts or c.purpose or c.all_word)


def _clauses(st: State) -> list[Clause]:
    out: list[Clause] = []
    pending: list[int] = []
    for idxs in split_message(st):
        c = _scan(st, pending + idxs)
        pending = []
        if _modifier_only(c):
            if out:
                out[-1] = _scan(st, out[-1].idxs + c.idxs)
            else:
                pending = c.idxs
            continue
        amount_only = not (c.verb or c.has_person or c.debt_word or c.hint or c.not_return)
        if amount_only and out and not out[-1].seg.amounts:
            out[-1] = _scan(st, out[-1].idxs + c.idxs)  # «Коля вернул долг, 200к»
            continue
        out.append(c)
    if pending:
        out.append(_scan(st, pending))
    return out


# ------------------------------------------------------------------ choices


def _form(st: State, c: Clause) -> str:
    if c.person:
        return span_text(st.orig, [st.toks[i] for i in c.person])
    return "@" + c.handle if c.handle else ""


def _latin(st: State, c: Clause) -> bool:
    return bool(c.person) and not re.search(r"[а-яё]", _form(st, c).lower())


def _name_like(st: State, c: Clause) -> bool:
    """п.5 «X вернул N»: X is a known person, an @handle or a capitalised proper name."""
    if c.handle and not c.person:
        return True
    if isinstance(dw.match_person(st.ctx, _form(st, c), c.handle), CtxDebt):
        return True
    toks = [st.toks[i] for i in c.person]
    if any(t.text in st.R.vocab or t.stem in st.R.vocab or t.text in lx.BRAND_OF
           for t in toks):
        return False
    return bool(toks) and dw.is_name_like(toks[0].raw) and all(
        t.text not in dw.NOT_PERSONS for t in toks)


def _decide(st: State, c: Clause) -> Decision | None:
    """Operation of a clause with its own verb; None = not a debt clause; _Unsure = ask LLM."""
    v, pos = c.verb, c.pos()
    form = _form(st, c)
    after_case = "nom" if _latin(st, c) else "dat"
    if c.not_return:
        if not c.has_person or c.verb is not None:
            raise _Unsure()
        return Decision("forgive", dw.case_by_ending(form), direction="owed_to_me")
    if c.neg:
        raise _Unsure()  # «Коля не вернул 200к» — a statement, not an operation
    if v is None:
        if c.debt_word or (c.hint and c.seg.amounts):
            raise _Unsure()
        return None  # «сколько я должен заплатить за аренду?» is not about a debt
    if c.extra_verb or c.groups:
        raise _Unsure()
    dat_before = pos == "before" and dw.case_by_ending(form) == "dat"
    prep_case = dw.PREPS.get(c.person_prep or "")
    if v in ("give", "lend", "take", "borrow"):
        if v in ("give", "take") and not c.debt_word:
            return None  # «дал Коле 500к», «взял кофе» — not a debt
        if not c.has_person:
            raise _Unsure()
        if prep_case is not None and c.person_prep in ("у", "от", "from"):
            if v == "give":
                raise _Unsure()
            return Decision("borrow", prep_case)
        if v == "borrow":
            if pos == "before" and c.u_me:
                return Decision("lend", "nom")  # «Коля занял у меня 500к»
            raise _Unsure()
        if v == "take":
            if pos == "before":
                return Decision("lend", "nom")  # «Коля взял в долг 500к»
            raise _Unsure()
        if pos == "before" and c.me:
            return Decision("borrow", "nom")  # «Петя дал мне в долг 1м»
        if pos == "after" and not c.me:
            return Decision("lend", after_case)
        if dat_before and not c.me:
            return Decision("lend", "dat")  # «Коле дал в долг 500к»
        raise _Unsure()
    if v in ("return", "return_pl", "payoff", "payoff_strong", "paid"):
        if v == "paid" and not c.back:
            return None  # «paid Bob 50$» is a payment
        strong = c.debt_word or v == "payoff_strong" or v == "paid"
        if c.purpose and not c.debt_word:
            if (v == "payoff" and pos == "before" and not c.i_word and not dat_before
                    and _name_like(st, c)):
                raise _Unsure()  # «Петя отдал за ужин 300к» — not my expense: the LLM decides
            return None  # «вернул 200к за наушники»
        if not c.has_person:
            if c.debt_word:
                raise _Unsure()
            return None
        if v == "return_pl" and not c.debt_word:
            return None
        if c.me or (pos == "before" and not c.i_word and not dat_before):
            d = Decision("repay_in", "nom", None if strong else "income")
        elif pos == "after" or dat_before:
            d = Decision("repay_out", after_case if pos == "after" else "dat",
                         None if strong else "expense")
        else:
            raise _Unsure()
        if d.fallback and not _name_like(st, c):
            return None
        return d
    if v == "settle":
        if c.person_prep in ("с", "со", "with") and c.person:
            return Decision("repay_out", "ins" if c.person_prep != "with" else "nom")
        if c.has_person and (c.u_me or (pos == "before" and not c.i_word)):
            return Decision("repay_in", "nom")
        if not c.has_person:
            return None  # «расплатился картой»
        raise _Unsure()
    if v == "send":
        if not (c.me and c.has_person):
            return None
        if c.purpose and not c.debt_word:
            return None  # «Коля перевёл мне 100к за пиццу» is a payment, not a repayment
        if pos == "before" or dw.case_by_ending(form) == "nom":
            d = Decision("repay_in", "nom", None if c.debt_word else "income")
            if d.fallback and not _name_like(st, c):
                return None
            return d
        return None
    if v == "forgive":
        if not c.has_person:
            raise _Unsure()
        if pos == "before" and not c.i_word and not dat_before:
            return Decision("forgive", "nom", direction="i_owe")  # «Петя простил мне долг»
        case = prep_case or ("nom" if _latin(st, c) else dw.case_by_ending(form))
        return Decision("forgive", case, direction="owed_to_me")
    if v == "writeoff":
        if not c.debt_word:
            return None  # «списали 21.3м» — an actual charge, not a debt
        if not c.has_person:
            raise _Unsure()
        case = prep_case or dw.case_by_ending(form)
        return Decision("forgive", case, direction="i_owe" if c.my else "owed_to_me")
    raise _Unsure()  # pragma: no cover - every verb class is handled above


# ------------------------------------------------------------------ intents


def _account_header(st: State, c: Clause) -> bool:
    """The clause's account is written before everything else of it («налом: Петя вернул 100к
    и 50к», «с кошелька одолжил Васе 50 usdt и Пете 20 usdt») — a header for the whole message, as
    in the ordinary parser. «Петя вернул 100к и 50к налом» — only the last part is cash."""
    acc = [i for i in c.idxs if st.toks[i].role == "account"]
    if not acc:
        return False
    meaning = set(c.person) | {i for a in c.seg.amounts for i in a.token_idx}
    if c.verb_k >= 0:
        meaning.add(c.idxs[c.verb_k])
    meaning.update(i for i in c.idxs if st.toks[i].kind == "handle"
                   or st.toks[i].text in dw.DEBT_WORDS)
    return bool(meaning) and min(acc) < min(meaning)


def _global_account(c: Clause) -> bool:
    """«…, всё налом» — an account said for the whole message."""
    return c.all_word and bool(c.seg.accounts or c.seg.no_account) and not (
        c.verb or c.has_person or c.debt_word or c.seg.amounts or c.hint or c.purpose
        or c.not_return or c.neg)


def _phrases(st: State) -> ParseResult | None:
    clauses = _clauses(st)
    header: Clause | None = None
    if len(clauses) >= 2 and _global_account(clauses[-1]):
        header = clauses.pop()
    plan: list[tuple[Clause, Decision]] = []
    none_clauses: list[Clause] = []
    prev: tuple[Clause, Decision] | None = None
    for c in clauses:
        d = _decide(st, c)
        if d is None and c.verb is None and prev is not None and not c.hint and (
                c.has_person or c.seg.amounts):
            pc, d = prev
            if not c.has_person:  # «Коля вернул 200к и ещё 100к налом»
                c.person, c.person_k, c.handle = pc.person, pc.person_k, pc.handle
                c.person_prep = pc.person_prep
            elif d.fallback and not _name_like(st, c):
                d = None
        if d is None:
            none_clauses.append(c)
            continue
        plan.append((c, d))
        prev = (c, d)
    if not plan:
        if any(c.debt_word for c in none_clauses) or (
                st.amounts and any(c.hint or c.not_return for c in none_clauses)):
            raise _Unsure()
        return None
    if none_clauses:
        raise _Unsure()  # debts mixed with other operations → the LLM splits them
    shared = header
    if shared is None and len(plan) > 1 and _account_header(st, plan[0][0]):
        shared = plan[0][0]
    outs: list[_Out] = []
    day: date | None = None
    for c, d in plan:
        inh = None
        if shared is not None and c is not shared and not (c.seg.accounts or c.seg.no_account):
            inh = shared
        o = _build(st, c, d, inh_day=day, inh_acc=inh, single=len(plan) == 1)
        outs.append(o)
        day = o.day if o.day is not None else day
    return _result(outs)


def _known_after(st: State, end: int) -> tuple[CtxDebt, int, int] | None:
    """Known person written right after the colon of a button text: the longest name / alias /
    @handle of ``ctx.debts`` («Агент 007», «Саша 2», «Bob2»), so digits of a name are never
    read as the amount. Returns (person, name start, name end) in the message."""
    rest = st.norm[end:]
    best: tuple[CtxDebt, int, int] | None = None
    for d in st.ctx.debts:
        for f in (d.name, *d.aliases, *([f"@{d.handle}"] if d.handle else [])):
            ws = normalize(f or "").split()
            if not ws:
                continue
            pat = r"\s*(" + r"\s+".join(re.escape(w) for w in ws) + r")(?=$|[\s,.;:!?])"
            m = re.match(pat, rest)
            if m and (best is None or end + m.end() > best[2]):
                best = (d, end + m.start(1), end + m.end())
    return best


def _canonical(st: State, op: str, end: int, direction: str | None = None) -> ParseResult:
    """Button texts: «дал в долг: Коля 500к», «занял: Петя 1м», «возврат долга: Коля [200к]»,
    «списать долг: Коля [50 USDT]», «списать мой долг: Коля 50к». The name after the colon is
    nominative already."""
    d = Decision(op, "nom", direction=direction)
    known = _known_after(st, end)
    if known is not None:  # the rest (amount, currency, account, date) is read without the name
        person, s, e = known
        sub_orig, sub_norm = st.orig[e:], st.norm[e:]
        sub = State(orig=sub_orig, norm=sub_norm, toks=tokenize(sub_norm, sub_orig), ctx=st.ctx,
                    R=st.R)
        sub.prepare()
        c = _scan(sub, [i for i, t in enumerate(sub.toks) if t.kind != "punct"], canonical=True)
        if c.has_person or c.groups or c.neg or c.not_return:
            raise _Unsure()
        if op in ("lend", "borrow") and not c.seg.amounts:
            raise _Unsure("no_amount")
        return _result([_build(sub, c, d, single=True, canonical=True,
                               known=(person, st.orig[s:e]))])
    idxs = [i for i, t in enumerate(st.toks) if t.start >= end and t.kind != "punct"]
    c = _scan(st, idxs, canonical=True)
    if not c.has_person or c.groups or c.neg or c.not_return:
        raise _Unsure()
    if op in ("lend", "borrow") and not c.seg.amounts:
        raise _Unsure("no_amount")
    return _result([_build(st, c, d, single=True, canonical=True)])


def _result(outs: list[_Out]) -> ParseResult:
    intents = [o.intent for o in outs]
    conf = min(o.conf for o in outs)
    if any(o.needs_llm for o in outs):
        reason = next(o.reason for o in outs if o.needs_llm)
        return llm(reason or "debt", confidence=min(conf, 0.5), intents=intents,
                   partial_ok=True)
    return done(intents, conf)


def _build(st: State, c: Clause, d: Decision, *, inh_day: date | None = None,
           inh_acc: Clause | None = None, single: bool = True,
           canonical: bool = False, known: tuple[CtxDebt, str] | None = None) -> _Out:
    # ---- person
    match: CtxDebt | list[CtxDebt] | None
    if known is not None:
        match, form = known
    else:
        form = _form(st, c)
        match = dw.match_person(st.ctx, form, c.handle)
    if c.person and match is None and dw.is_generic(form):
        raise _Unsure()  # «одолжил другу 500к» — which friend? the LLM asks
    if isinstance(match, CtxDebt):
        name, certain = match.name, True
    elif c.person and canonical:
        name, certain = capitalize(form)[:56], True  # orig text: «ё» is kept
    elif c.person:
        name, certain = dw.guess_name(form, d.case)
    else:
        name, certain = None, True  # a new @handle: the validator names it
    written = form
    if c.person and c.person_prep in _RU_PREPS:
        written = f"{c.person_prep} {form}"
    # ---- account
    acc_m: AccMention | None = None
    no_account = False
    if d.op != "forgive":
        acc_m, amb = pick_account(c.seg)
        no_account = c.seg.no_account
        if amb or (no_account and acc_m is not None):
            raise _Unsure("multiple_accounts")
        if acc_m is None and not no_account and inh_acc is not None:
            acc_m, _ = pick_account(inh_acc.seg)
            no_account = inh_acc.seg.no_account
    elif c.seg.accounts:
        raise _Unsure()  # «списать долг … с кошелька» — forgiving never touches an account
    # ---- amount / currency
    if len(c.seg.amounts) > 1:
        raise _Unsure("multiple_amounts")
    need = _NEED.get(d.op) or d.direction
    amount = raw = None
    acc_ref = acc_m.ref if acc_m else None
    if c.seg.amounts:
        a = c.seg.amounts[0]
        # «Дима вернул 10к» while Дима owes only in ₽: the currency of that debt, not the default ₫
        # (not a 10 000 ₫ income). Only for returns/forgiving — a new loan keeps
        # the usual rules — and only when neither the amount nor the account names a currency.
        debt_cur = None
        if (d.op not in ("lend", "borrow") and a.currency is None and not c.currency_word
                and not (acc_ref and acc_ref.currency)):
            opens = sorted(set(dw.open_currencies(match, need)))
            debt_cur = opens[0] if len(opens) == 1 else None
            # «10к» of ₽ is normal, «100к» of USDT is not: a coin debt takes only a plain number
            # («Вася вернул 20» → 20 USDT); «Вася вернул 100к» stays ₫ with the «нет долга» note
            if debt_cur is not None and is_crypto(debt_cur) and a.mult is not None:
                debt_cur = None
        if debt_cur is not None:
            cur, prob = debt_cur, None
        else:
            cur, prob = resolve_currency(st, a, acc_ref, crypto_ctx=bool(c.handle))
        if prob == "ambiguous_currency" and single:
            return _Out(clarify_currency(st, a, acc_ref), True, prob, conf=0.4)
        if prob or cur is None:
            raise _Unsure(prob or "ambiguous_currency")
        acc_ref = card_fix(st, acc_m, cur)
        amount, aprob = amount_of(st, a, cur)
        if amount is None:
            raise _Unsure(aprob or "bad_amount")
        raw = a.raw[:64] or None
    else:
        if d.op in ("lend", "borrow") or d.fallback:
            raise _Unsure("no_amount")
        cur = c.currency_word or (acc_ref.currency if acc_ref and acc_ref.currency else None)
        if cur is None:
            opens = dw.open_currencies(match, need)
            cur = opens[0] if len(opens) == 1 else None
    # ---- date
    day, prob = seg_date(st, c.seg, inh_day)
    if prob:
        raise _Unsure(prob)
    description = describe(st, [st.toks[i] for i in c.purpose]) if c.purpose else None
    new_person = not isinstance(match, CtxDebt) and not isinstance(match, list)
    # п.5 «X вернул N» / «отдал X N»: always a DebtIntent with ``fallback`` — the validator
    # decides by the DB and, when there is no such debt, writes the ordinary operation with
    # the note «Долга у X нет — записал как доход» (DEBTS.md §15). No LLM is needed for it.
    no_debt = bool(d.fallback) and not dw.has_open(match, cur, need or "")
    intent = DebtIntent(
        op=d.op,  # type: ignore[arg-type]
        person=written[:64],
        person_name=name[:56] if name else None,
        amount=amount,
        amount_raw=raw,
        currency=cur,
        account_ref=acc_ref.name if acc_ref else None,
        account_none=no_account,
        direction=d.direction,  # type: ignore[arg-type]
        fallback=d.fallback,  # type: ignore[arg-type]
        description=description,
        occurred_on=on_date(day, st),
        occurred_time=seg_time(st, c.seg),
    )
    unsure_name = new_person and not certain and (d.op in ("lend", "borrow") or no_debt)
    return _Out(intent, unsure_name, "debt_name" if unsure_name else None, day,
                0.85 if no_debt else CONF)


__all__ = ["Clause", "Decision", "is_button_text", "try_debts"]

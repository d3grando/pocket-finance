"""Segments → operation intents: expense / income / refund, transfers, exchanges,
balance reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.schemas.intents import (
    AdjustBalanceIntent,
    ClarifyIntent,
    CreateTransactionIntent,
    TransferIntent,
)
from app.services.parser import lexicon as lx
from app.services.parser.amounts import AmountTok
from app.services.parser.currencies import is_crypto
from app.services.parser.refs import AccRef, CatRef, MerchRef
from app.services.parser.segments import AccMention, Seg, content_idx
from app.services.parser.state import State
from app.services.parser.text import Token

CONF_OK = 0.95


@dataclass(slots=True)
class SegOut:
    intent: object | None
    confidence: float
    needs_llm: bool = False
    safe: bool = True  # may be executed without the LLM
    reason: str | None = None
    kind: str | None = None
    day: date | None = None


def fail(reason: str, conf: float = 0.3) -> SegOut:
    return SegOut(None, conf, needs_llm=True, safe=False, reason=reason)


# ------------------------------------------------------------------ helpers


def seg_date(st: State, seg: Seg, inherited: date | None) -> tuple[date | None, str | None]:
    """(date, problem). Problems: ambiguous_date (weekend, «на прошлой неделе»), future_date,
    multiple_dates."""
    if not seg.dates:
        return inherited, None
    if any(h.kind in ("weekend", "vague") for h in seg.dates):
        return None, "ambiguous_date"
    if any(h.kind == "future" for h in seg.dates):
        return None, "future_date"
    vals = {h.value for h in seg.dates}
    if len(vals) > 1:
        return None, "multiple_dates"
    return seg.dates[0].value, None


def on_date(d: date | None, st: State) -> date | None:
    return None if d is None or d == st.today else d


def seg_time(st: State, seg: Seg):
    return st.time if any(st.toks[i].role == "time" for i in seg.idxs) else None


def resolve_currency(st: State, a: AmountTok, acc: AccRef | None, *, crypto_ctx: bool = False
                     ) -> tuple[str | None, str | None]:
    """(currency, problem). Never guesses: a multi-asset account without a currency is ambiguous.
    An unknown lower-case ticker («300 abcd») is accepted only in a crypto context (multi-asset
    account, @handle, transfer/exchange); upper-case («300 ABCD») is always a coin candidate."""
    if a.currency:
        if a.coin_candidate:
            tok = st.toks[a.token_idx[-1]]
            crypto = crypto_ctx or (acc is not None and acc.multi)
            if not (tok.raw.isupper() or a.currency in st.R.known_assets or crypto):
                return None, "unknown_currency"
        return a.currency, None
    if acc is not None:
        if acc.currency:
            return acc.currency, None
        if acc.multi:
            return None, "ambiguous_currency"
    return st.ctx.default_currency.upper(), None


def amount_of(st: State, a: AmountTok, cur: str, *, allow_zero: bool = False
              ) -> tuple[Decimal | None, str | None]:
    try:
        if a.ambiguous(cur, st.ctx.default_currency):
            return None, "ambiguous_amount"
        v = a.value(cur, st.ctx.small_amount_thousands, st.ctx.default_currency)
    except ValueError:
        return None, "bad_amount"
    if v < 0 or (v == 0 and not allow_zero):
        return None, "bad_amount"
    if v >= Decimal("1e19"):
        return None, "bad_amount"
    return v, None


_EDGE_WORDS = {"на", "в", "во", "за", "с", "со", "по", "для", "к", "до", "от", "из", "и", "а",
               "у", "о", "об", "при", "через", "for", "on", "in", "at", "to", "from", "and"}


def describe(st: State, toks: list[Token]) -> str | None:
    """Original text of content tokens (amount/date/account tokens dropped; quantities kept:
    «интернет на 3 месяца»), without dangling prepositions at the edges."""
    if not toks:
        return None
    skip = {"amount", "account", "time", "handle", "marker", "sign", "sep", "balance"}
    first = min(t.start for t in toks)
    last = max(t.end for t in toks)
    kept = [t for t in st.toks
            if first <= t.start and t.end <= last and t.kind in ("word", "num")
            and t.role.split(":")[0] not in skip and not t.role.startswith("date")]
    while kept and kept[-1].kind == "word" and kept[-1].text in _EDGE_WORDS:
        kept.pop()
    while kept and kept[0].kind == "word" and kept[0].text in _EDGE_WORDS:
        kept.pop(0)
    while kept and kept[-1].kind == "num":  # a quantity without its unit
        kept.pop()
    text = " ".join(t.raw for t in kept).strip(" ,.;:-")
    return text[:500] or None


def clarify_currency(st: State, a: AmountTok, acc: AccRef | None) -> ClarifyIntent:
    codes = [c for c in (acc.assets if acc else ()) if c not in ("VND",)][:4] or ["USDT", "TON"]
    opts = []
    for c in codes:
        opts.append((st.orig[: a.end] + " " + c + st.orig[a.end :]).strip()[:300])
    q = f"Не знаю валюту «{a.raw}». Напишите, например, «{a.raw} {codes[0]}»"
    if len(codes) > 1:
        q += f" или «{a.raw} {codes[1]}»"
    return ClarifyIntent(reason="ambiguous_currency", question=q[:300], options=opts[:6])


def _replace_span(st: State, s: int, e: int, repl: str) -> str:
    return " ".join((st.orig[:s] + " " + repl + " " + st.orig[e:]).split())[:300]


def clarify_weekend(st: State, seg: Seg) -> ClarifyIntent:
    h = next(h for h in seg.dates if h.kind == "weekend")
    opts = [_replace_span(st, h.start, h.end, w) for w in ("в субботу", "в воскресенье")]
    return ClarifyIntent(reason="other", question="«На выходных» — это суббота или воскресенье?",
                         options=opts)


def clarify_vague(st: State, seg: Seg) -> ClarifyIntent:
    """«на прошлой неделе такси 50к»: a period, not a day — ask for the day (never «today»)."""
    h = next(h for h in seg.dates if h.kind == "vague")
    phrase = " ".join(st.orig[h.start:h.end].split())
    sample = h.value or (st.today - timedelta(days=1))
    example = _replace_span(st, h.start, h.end, f"{sample:%d.%m}")
    q = f"«{phrase}» — какого числа? Напишите дату, например: «{example}»"
    return ClarifyIntent(reason="other", question=q[:300], options=[])


def clarify_future(st: State, seg: Seg) -> ClarifyIntent | None:
    """«1 ноября аренда 12м» written on 27.10: a near-future date is not silently moved to
    the previous year — ask (last year's date or today). None for «завтра» etc. (LLM)."""
    h = next((h for h in seg.dates if h.kind == "future"), None)
    if h is None or h.value is None or h.src not in ("dm", "md", "dmy"):
        return None
    d = h.value
    try:
        prev: date | None = d.replace(year=d.year - 1)
    except ValueError:  # 29.02
        prev = None
    opts = []
    if prev is not None:
        opts.append(_replace_span(st, h.start, h.end, f"{prev:%d.%m.%Y}"))
    opts.append(_replace_span(st, h.start, h.end, "сегодня"))
    q = f"Дата {d:%d.%m.%Y} ещё не наступила. "
    q += f"Записать на {prev:%d.%m.%Y} или на сегодня?" if prev else "Записать на сегодня?"
    return ClarifyIntent(reason="other", question=q[:300], options=opts)


def pick_account(seg: Seg) -> tuple[AccMention | None, bool]:
    """The single distinct account mentioned in the segment.
    Second value: ambiguous (2+ accounts)."""
    uniq: list[AccMention] = []
    for m in seg.accounts:
        if all(u.ref != m.ref for u in uniq):
            uniq.append(m)
    if not uniq:
        return None, False
    if len(uniq) > 1:
        return None, True
    return uniq[0], False


def card_fix(st: State, m: AccMention | None, cur: str | None) -> AccRef | None:
    """«картой» means the card that is default for the currency (₽ → Карта RUB)."""
    if m is None:
        return None
    if m.generic_card and cur and cur not in (m.ref.currency or ""):
        alt = st.R.default_account_for(cur, card_only=True)
        if alt is not None:
            return alt
    return m.ref


def best_category(matches) -> tuple[CatRef | None, bool, float]:
    """(category, ambiguous, priority) from matches [(Match, idxs)]."""
    if not matches:
        return None, False, 0
    top = max(m.priority for m, _ in matches)
    cats = []
    for m, _ in matches:
        if m.priority == top and m.ref not in cats:
            cats.append(m.ref)
    return cats[0], len(cats) > 1, top


def _meaning_category(st: State, seg: Seg, use_inc: bool) -> bool:
    """True when the text names a category by its own words (keyword priority ≥ 2), not only
    through merchant / brand tokens — such a category beats a learned merchant rule."""
    skip = seg.brand_idx | {i for i in seg.idxs if st.toks[i].role == "merchant"}
    for m, idxs in (seg.cat_inc if use_inc else seg.cat_exp):
        if m.priority >= 2 and not set(idxs) <= skip:
            return True
    return False


def covered(matches) -> set[int]:
    return {i for _, idxs in matches for i in idxs}


# ------------------------------------------------------------------ P&L operations


def interpret(st: State, seg: Seg, inh_date: date | None, inh_kind: str | None,
              single: bool, inh_acc: AccRef | None = None) -> SegOut:
    """One segment → intent. ``inh_acc``: account written at the very start of the message
    («налом: кофе 40к, такси 50к») — applies to segments without their own account."""
    for flag in ("negation", "debt", "future"):
        if flag in seg.flags:
            return fail(flag)
    day, problem = seg_date(st, seg, inh_date)
    if problem == "ambiguous_date":
        if single and len(seg.amounts) == 1:
            if any(h.kind == "weekend" for h in seg.dates):
                return SegOut(clarify_weekend(st, seg), 0.4, needs_llm=True, safe=True,
                              reason=problem)
            return SegOut(clarify_vague(st, seg), 0.8, safe=True, reason=problem)
        return fail(problem)
    if problem == "future_date" and single and len(seg.amounts) == 1:
        fc = clarify_future(st, seg)
        if fc is not None:
            return SegOut(fc, 0.8, safe=True, reason=problem)
    if problem:
        return fail(problem)
    verbs = set(seg.verbs)
    if "exchange" in verbs or (verbs & {"buy", "sell"} and len(seg.amounts) == 2):
        return exchange(st, seg, day)
    paid_from_own = "transfer" in verbs and _paid_from_own(seg)
    if "transfer" in verbs and not verbs & {"income", "refund"} and not paid_from_own:
        return transfer(st, seg, day)
    if "withdraw" in verbs and not verbs & {"income", "refund", "expense"}:
        return withdraw(st, seg, day)
    if len(seg.amounts) != 1:
        return fail("multiple_amounts" if seg.amounts else "no_amount")
    a = seg.amounts[0]
    if any(st.toks[i].role == "qty" for i in seg.idxs) and a.token_idx[0] > 0 and st.toks[
            a.token_idx[0] - 1].text in ("по", "each", "x", "х", "*"):
        return fail("per_unit")  # «2 кофе по 40к» — total is not written
    kind: str | None = None
    if "refund" in verbs:
        kind = "refund"
    elif "income" in verbs or (a.sign == "+" and seg.idxs and a.sign_idx == seg.idxs[0]):
        kind = "income"
    elif "sell" in verbs:
        # «продал наушники за 500к» — money came in (never an expense); «продал 100 usdt»
        # without the received amount is an exchange → LLM
        if a.currency and is_crypto(a.currency) and not content_idx(st, seg):
            return fail("exchange_amounts")
        kind = "income"
    elif "expense" in verbs or "buy" in verbs or a.sign == "-" or paid_from_own:
        kind = "expense"
    if kind is None and is_adjust(st, seg):
        return adjust(st, seg)
    if seg.handles and kind != "income" and kind != "refund":
        h = seg.handles[0].lower()
        if h in st.R.my_handles:
            return transfer(st, seg, day)
    # category & kind
    exp_cat, exp_amb, exp_prio = best_category(seg.cat_exp)
    inc_cat, inc_amb, inc_prio = best_category(seg.cat_inc)
    if kind is None:
        strong_inc = any(m.strong_income for m, _ in seg.cat_inc)
        if exp_cat is None and inc_cat is not None and strong_inc:
            kind = "income"
        elif exp_cat is None and inc_cat is not None and inh_kind == "income":
            kind = "income"
        else:
            kind = "expense"
    use_inc = kind == "income"
    cat, amb, prio = (inc_cat, inc_amb, inc_prio) if use_inc else (exp_cat, exp_amb, exp_prio)
    cat_cover = covered(seg.cat_inc if use_inc else seg.cat_exp)
    merchant: MerchRef | None = seg.merchants[0] if seg.merchants else None
    if merchant is not None and use_inc and seg.brand_idx:
        merchant = None  # a brand keyword («cursor») names a shop to pay, not a payer
    conf = CONF_OK
    if use_inc and "sell" in verbs and cat is None:
        cat = st.R.category(lx.SALES_CATEGORY, "income") or CatRef(lx.SALES_CATEGORY, "income",
                                                                   True)
        amb, prio, conf = False, 2, 0.85
    if merchant is not None and merchant.category is not None and (
        merchant.category.kind == ("income" if use_inc else "expense")
    ) and not _meaning_category(st, seg, use_inc):
        # the learned rule is used only when the text has no meaning words of its own:
        # «@alice такси 50к» stays «Такси» even if @alice is remembered as «Софт и AI»
        cat, amb, prio = merchant.category, False, 5
    for_resale = seg.resale and not use_inc
    if for_resale:
        resale_cat = st.R.category("Закупка товара", "expense")
        if resale_cat is not None:
            cat, amb = resale_cat, False
    # account & currency
    acc_m, acc_amb = pick_account(seg)
    if acc_amb or (seg.no_account and acc_m is not None):
        return fail("multiple_accounts")
    if acc_m is None and inh_acc is not None and not seg.no_account:
        acc_m = AccMention(inh_acc, "from", False, inh_acc.name)
    acc = acc_m.ref if acc_m else None
    cur, cproblem = resolve_currency(st, a, acc, crypto_ctx=bool(seg.handles))
    if cproblem == "ambiguous_currency":
        if single:
            return SegOut(clarify_currency(st, a, acc), 0.4, needs_llm=True, safe=True,
                          reason=cproblem)
        return fail(cproblem)
    if cproblem or cur is None:
        return fail(cproblem or "ambiguous_currency")
    acc = card_fix(st, acc_m, cur)
    amount, aproblem = amount_of(st, a, cur)
    if aproblem or amount is None:
        return fail(aproblem or "bad_amount")
    if a.coin_candidate:
        conf = min(conf, 0.8)
    # words
    word_idx = content_idx(st, seg)
    words = [st.toks[i] for i in word_idx]
    unknown = [st.toks[i] for i in word_idx
               if i not in cat_cover and st.toks[i].role != "merchant"]
    counterparty = "@" + seg.handles[0] if seg.handles else None
    if cat is None and counterparty and not use_inc:
        cat = st.R.category(lx.TRANSFER_CATEGORY, "expense") or CatRef(lx.TRANSFER_CATEGORY,
                                                                        "expense")
        conf = min(conf, 0.85)
    description = None
    if seg.purpose:
        if any(st.toks.index(t) not in cat_cover for t in seg.purpose):
            description = describe(st, seg.purpose)
    elif unknown and (cat is not None or merchant is not None):
        description = describe(st, words)
    out_reason = None
    needs = False
    if amb:
        needs, out_reason, conf = True, "ambiguous_category", 0.5
    elif cat is None and merchant is None and unknown:
        needs, out_reason, conf = True, "unknown_words", 0.5
        description = describe(st, unknown)
    elif cat is None and merchant is not None:
        needs, out_reason, conf = True, "merchant_without_category", 0.6
    elif cat is None and merchant is None:
        conf = min(conf, 0.7)
    elif prio <= 1:
        conf = min(conf, 0.85)
    if len(unknown) >= 4:
        needs, out_reason, conf = True, "complex", min(conf, 0.5)
    if not words and merchant is None and not counterparty and not verbs and a.sign is None:
        # «250к», «3 тона», «налом 2м»: new expense? correction? balance? — ask the LLM
        needs, out_reason, conf = True, "amount_only", min(conf, 0.5)
    if cat is None:
        cat = st.R.category(lx.OTHER_CATEGORY, "income" if use_inc else "expense") or CatRef(
            lx.OTHER_CATEGORY, "income" if use_inc else "expense")
    intent = CreateTransactionIntent(
        kind=kind,
        amount=amount,
        amount_raw=a.raw[:64] or None,
        currency=cur,
        account_ref=acc.name if acc else None,
        account_none=seg.no_account,
        category_ref=cat.name if cat else None,
        merchant_ref=merchant.name if merchant else None,
        counterparty=counterparty,
        description=description,
        occurred_on=on_date(day, st),
        occurred_time=seg_time(st, seg),
        for_resale=for_resale,
    )
    return SegOut(intent, conf, needs_llm=needs, safe=True, reason=out_reason, kind=kind,
                  day=day)


def is_adjust(st: State, seg: Seg) -> bool:
    if len(seg.amounts) != 1 or seg.handles or seg.merchants or seg.verbs:
        return False
    if len(seg.accounts) != 1:
        return False
    m = seg.accounts[0]
    if not (m.role == "to" or seg.balance_word):
        return False
    return not content_idx(st, seg)


def adjust(st: State, seg: Seg) -> SegOut:
    a = seg.amounts[0]
    acc = seg.accounts[0].ref
    cur, problem = resolve_currency(st, a, acc)
    if problem == "ambiguous_currency":
        return SegOut(clarify_currency(st, a, acc), 0.4, needs_llm=True, safe=True,
                      reason=problem)
    if problem or cur is None:
        return fail(problem or "ambiguous_currency")
    amount, aproblem = amount_of(st, a, cur, allow_zero=True)
    if amount is None:
        return fail(aproblem or "bad_amount")
    intent = AdjustBalanceIntent(account_ref=acc.name, amount=amount, amount_raw=a.raw[:64],
                                 currency=cur)
    return SegOut(intent, 0.9, kind="adjustment")


# ------------------------------------------------------------------ transfers / exchanges


def _paid_from_own(seg: Seg) -> bool:
    """«с сейфа перевёл 20 usdt на покупку лицензии», «перевёл 500к с карты за ремонт» — a payment
    for something from an own account: no destination account or @handle, but a purpose.
    That is an expense, not a transfer between own accounts."""
    if not seg.purpose or seg.handles or len(seg.amounts) != 1:
        return False
    _, dst, amb = _roles(seg, "from")
    return dst is None and not amb


def _roles(seg: Seg, plain_as: str) -> tuple[AccRef | None, AccRef | None, bool]:
    """(from, to, ambiguous) from account mentions."""
    src: list[AccRef] = []
    dst: list[AccRef] = []
    for m in seg.accounts:
        role = m.role if m.role != "plain" else plain_as
        lst = src if role == "from" else dst
        if m.ref not in lst:
            lst.append(m.ref)
    if len(src) > 1 or len(dst) > 1:
        return None, None, True
    return (src[0] if src else None), (dst[0] if dst else None), False


def transfer(st: State, seg: Seg, day: date | None) -> SegOut:
    """«перевёл 500 usdt с биржи на сейф», «перевёл 3 тона @carol с кошелька»,
    «перевёл 3 тона @myshop» (own handle → transfer)."""
    if not seg.amounts or len(seg.amounts) > 2:
        return fail("no_amount" if not seg.amounts else "multiple_amounts")
    plain_as = "to" if any(st.toks[i].text.startswith("пополнил") for i in seg.idxs) else "from"
    src, dst, amb = _roles(seg, plain_as)
    if amb:
        return fail("multiple_accounts")
    a = seg.amounts[0]
    cur, problem = resolve_currency(st, a, src or dst, crypto_ctx=True)
    if problem or cur is None:
        return fail(problem or "ambiguous_currency")
    amount, aproblem = amount_of(st, a, cur)
    if amount is None:
        return fail(aproblem or "bad_amount")
    handle = seg.handles[0].lower() if seg.handles else None
    if handle and handle not in st.R.my_handles:
        if len(seg.amounts) != 1 or dst is not None:
            return fail("complex")
        return _pay_person(st, seg, a, amount, cur, src, handle, day)
    counter_amount = counter_cur = counter_raw = None
    if len(seg.amounts) == 2:
        b = seg.amounts[1]
        counter_cur = b.currency or (dst.currency if dst and dst.currency else None)
        if counter_cur is None or counter_cur == cur:
            return fail("ambiguous_currency")
        counter_amount, bproblem = amount_of(st, b, counter_cur)
        if counter_amount is None:
            return fail(bproblem or "bad_amount")
        counter_raw = b.raw[:64]
    conf = CONF_OK if (src and (dst or handle)) else 0.75
    if not src and not dst and not handle:
        # «перевёл маме 500к» — money to a person without a handle
        words = [st.toks[i] for i in content_idx(st, seg)]
        cat = st.R.category(lx.TRANSFER_CATEGORY, "expense") or CatRef(lx.TRANSFER_CATEGORY,
                                                                        "expense")
        intent = CreateTransactionIntent(
            kind="expense", amount=amount, amount_raw=a.raw[:64], currency=cur,
            category_ref=cat.name, description=describe(st, words),
            occurred_on=on_date(day, st), occurred_time=seg_time(st, seg))
        return SegOut(intent, 0.5, needs_llm=True, safe=True, reason="transfer_target",
                      kind="expense", day=day)
    intent = TransferIntent(
        amount=amount, amount_raw=a.raw[:64], currency=cur,
        from_account_ref=src.name if src else None,
        to_account_ref=dst.name if dst else None,
        to_handle=handle,
        counter_amount=counter_amount, counter_amount_raw=counter_raw,
        counter_currency=counter_cur,
        description=describe(st, seg.purpose) if seg.purpose else None,
        occurred_on=on_date(day, st),
    )
    return SegOut(intent, conf, kind="transfer", day=day)


def _pay_person(st: State, seg: Seg, a: AmountTok, amount: Decimal, cur: str,
                src: AccRef | None, handle: str, day: date | None) -> SegOut:
    """Transfer to a foreign @handle = expense with counterparty."""
    merchant = st.R.merch_by_handle.get(handle)
    cat: CatRef | None = None
    conf = 0.9
    if merchant is not None and merchant.category is not None and not _meaning_category(
        st, seg, False
    ):
        cat = merchant.category
    if cat is None and seg.resale:
        cat = st.R.category("Закупка товара", "expense")
    if cat is None:
        c, amb, _ = best_category(seg.cat_exp)
        if c is not None and not amb:
            cat = c
    if cat is None:
        cat = st.R.category(lx.TRANSFER_CATEGORY, "expense") or CatRef(lx.TRANSFER_CATEGORY,
                                                                        "expense")
        conf = 0.85 if not seg.purpose else 0.7
    intent = CreateTransactionIntent(
        kind="expense", amount=amount, amount_raw=a.raw[:64], currency=cur,
        account_ref=src.name if src else None, category_ref=cat.name,
        merchant_ref=merchant.name if merchant else None,
        counterparty="@" + handle,
        description=describe(st, seg.purpose) if seg.purpose else None,
        occurred_on=on_date(day, st), occurred_time=seg_time(st, seg),
        for_resale=seg.resale,
    )
    return SegOut(intent, conf, kind="expense", day=day)


def exchange(st: State, seg: Seg, day: date | None) -> SegOut:
    """«поменял 300 usdt на 7.6м», «купил 0.1 eth за 300 usdt», «продал 100 usdt за 2.6м»."""
    if len(seg.amounts) != 2:
        return fail("exchange_amounts")
    verbs = set(seg.verbs)
    a1, a2 = seg.amounts
    if "exchange" not in verbs and "buy" in verbs:
        sold, bought = a2, a1
    else:
        sold, bought = a1, a2
    if "exchange" not in verbs:
        # buy/sell is an exchange only between currencies («купил iphone 15 за 25м» is not)
        if not (sold.currency and bought.currency) and not (
            (sold.currency and is_crypto(sold.currency))
            or (bought.currency and is_crypto(bought.currency))
        ):
            return fail("multiple_amounts")
        if content_idx(st, seg):
            return fail("multiple_amounts")
    src, dst, amb = _roles(seg, "from")
    if amb:
        return fail("multiple_accounts")
    if len({m.ref for m in seg.accounts}) == 1 and src is None:
        src, dst = dst, None  # «поменял … на бирже» — one account is where it happened
    default = st.ctx.default_currency.upper()
    c1 = sold.currency or (src.currency if src and src.currency else None)
    c2 = bought.currency or (dst.currency if dst and dst.currency else None)
    if c1 is None and c2 is None:
        return fail("ambiguous_currency")
    if c1 is None:
        c1 = default if c2 != default else None
    if c2 is None:
        c2 = default if c1 != default else None
    if c1 is None or c2 is None or c1 == c2:
        return fail("ambiguous_currency")
    v1, p1 = amount_of(st, sold, c1)
    v2, p2 = amount_of(st, bought, c2)
    if v1 is None or v2 is None:
        return fail(p1 or p2 or "bad_amount")
    intent = TransferIntent(
        amount=v1, amount_raw=sold.raw[:64], currency=c1,
        from_account_ref=src.name if src else None,
        to_account_ref=dst.name if dst else None,
        counter_amount=v2, counter_amount_raw=bought.raw[:64], counter_currency=c2,
        occurred_on=on_date(day, st),
    )
    conf = CONF_OK if (sold.currency and bought.currency) else 0.85
    return SegOut(intent, conf, kind="exchange", day=day)


def withdraw(st: State, seg: Seg, day: date | None) -> SegOut:
    """«снял 2м с карты», «снял наличные 2м» → transfer to the cash account."""
    if len(seg.amounts) != 1:
        return fail("multiple_amounts")
    cash = next((x for x in st.R.accounts if x.kind == "cash"), None)
    if cash is None:
        return fail("no_cash_account")
    srcs = [m.ref for m in seg.accounts if m.ref != cash]
    if len({s.name for s in srcs}) > 1:
        return fail("multiple_accounts")
    src = srcs[0] if srcs else None
    a = seg.amounts[0]
    cur, problem = resolve_currency(st, a, src or cash)
    if problem or cur is None:
        return fail(problem or "ambiguous_currency")
    amount, aproblem = amount_of(st, a, cur)
    if amount is None:
        return fail(aproblem or "bad_amount")
    if src is None:
        src = st.R.default_account_for(cur)
    intent = TransferIntent(amount=amount, amount_raw=a.raw[:64], currency=cur,
                            from_account_ref=src.name if src else None, to_account_ref=cash.name,
                            occurred_on=on_date(day, st))
    return SegOut(intent, 0.9 if src else 0.75, kind="transfer", day=day)

"""Commands: undo, delete, edit, corrections «нет, 250к», «васт списали
21.3м», create category, edits after the «Изменить» button (ctx.edit_transaction_id)."""

from __future__ import annotations

import re
from decimal import Decimal

from pydantic import ValidationError

from app.core.money import format_money
from app.schemas.intents import (
    ActionBrief,
    ClarifyIntent,
    CreateCategoryIntent,
    DeleteTransactionIntent,
    ParseResult,
    SearchFilter,
    TargetSpec,
    TransactionChanges,
    UndoIntent,
    UpdateTransactionIntent,
)
from app.services.parser.ops import amount_of, best_category, seg_date
from app.services.parser.periods import find_periods
from app.services.parser.segments import analyze, content_idx
from app.services.parser.state import State, done, llm

UNDO_RE = re.compile(
    r"^(?:пожалуйста\s+)?(?:отмени(?:ть|те)?|отмена|undo|откати(?:ть)?|верни(?:те)?\s+"
    r"(?:как\s+было|назад|обратно))(?:\s+(?:последнее(?:\s+действие)?|это|действие|что\s+было|"
    r"пожалуйста|плиз|pls|please|last))*$"
)
DELETE_VERBS = {"удали", "удалить", "удалите", "сотри", "стереть", "сотрите", "убери", "убрать",
                "delete", "remove", "del", "отмени", "отменить"}
EDIT_VERBS = {"исправь", "исправить", "исправьте", "измени", "изменить", "измените", "поменяй",
              "поменять", "поменяйте", "замени", "заменить", "поправь", "поправить", "edit",
              "change", "fix", "correct", "update", "переименуй"}
CORRECTION_START = re.compile(
    r"^(?:(?:а|ой|ой,|ах)\s+)?(?:нет|неа|не\s+так|no|nope|точнее|вернее|поправка|стоп)(?=$|[\s,.!:-])"
)
NOT_X_BUT_Y = re.compile(r"^не\s+(?P<x>[^,]+?),?\s+а\s+(?P<y>.+)$")
CHARGED_WORDS = {"списали", "списалось", "списано", "сняли", "списала", "списал"}
# «пришло 2.6м» / «зачислили 2.6м» right after «+100$ от клиента на Карта» → actual credited
# amount of that estimated income (only then; otherwise it is a new income)
CREDITED_WORDS = {"пришло", "пришли", "пришла", "пришел", "пришёл", "зачислили", "зачислено",
                  "зачислилось", "поступило", "поступили"}
CHARGED_HINTS = {"факту", "фактически", "реально", "самом", "деле", "итого", "итоге"}
CREATE_CAT_RE = re.compile(
    r"^(?:(?:создай|создать|добавь|добавить|заведи|сделай|new)\s+(?:нов(?:ую|ая)\s+)?"
    r"|нов(?:ую|ая)\s+|new\s+)"
    r"(?:(?P<biz>бизнес[- ]?)\s*)?(?:категори[юя]|category)\s+(?P<rest>.+)$"
)
_EXPENSE_NOUNS = ("расход", "трат", "покупк", "expense", "оплат")
_INCOME_NOUNS = ("доход", "поступлен", "income", "зачислен")
_ANY_NOUNS = ("операци", "запис", "транзакци", "перевод", "transaction", "entry")
_EDIT_NOUNS = {"сумму", "сумма", "суммы", "категорию", "категория", "категории", "дату", "дата",
               "даты", "счет", "счета", "валюту", "валюта", "описание", "amount", "category",
               "date", "account", "currency", "поле"}


def _words(st: State) -> list[str]:
    return [t.text for t in st.toks if t.kind == "word"]


def _target_of(st: State, idxs: list[int]) -> tuple[TargetSpec, list[int]]:
    """Find «последний расход» / «предпоследнюю» / «это» → TargetSpec; returns remaining idxs."""
    pos = 1
    kind = None
    rest: list[int] = []
    seen_last = False
    k = 0
    while k < len(idxs):
        t = st.toks[idxs[k]]
        w = t.text if t.kind == "word" else ""
        if w.startswith(("последн", "предпоследн", "last", "previous")):
            seen_last = True
            if w.startswith("предпоследн") or w == "previous":
                pos = 2
            t.role = "target"
            # noun right after («последнюю покупку»), allowing one edit-noun in between
            for j in range(k + 1, min(k + 3, len(idxs))):
                t2 = st.toks[idxs[j]]
                if t2.kind != "word":
                    break
                if t2.text.startswith(_EXPENSE_NOUNS):
                    kind, t2.role = "last_expense", "target"
                    break
                if t2.text.startswith(_INCOME_NOUNS):
                    kind, t2.role = "last_income", "target"
                    break
                if t2.text.startswith(_ANY_NOUNS):
                    t2.role = "target"
                    break
        elif w in ("это", "эту", "этот", "её", "ее", "его", "this", "it") and not seen_last:
            t.role = "target"
        elif w in _EDIT_NOUNS:
            t.role = "target"
        k += 1
    rest = [i for i in idxs if st.toks[i].role != "target"]
    if not seen_last and st.ctx.edit_transaction_id is not None:
        return TargetSpec(kind="context"), rest
    return TargetSpec(kind=kind or "last", position=pos), rest


def _base_action(st: State, target: TargetSpec) -> ActionBrief | None:
    """Dialogue-context facts of the operation a correction applies to (if known)."""
    acts = st.ctx.recent_actions
    if target.kind == "context" and st.ctx.edit_transaction_id is not None:
        for a in acts:
            if st.ctx.edit_transaction_id in a.transaction_ids and a.currency:
                return a
        return None
    if target.kind not in ("last", "last_expense", "last_income"):
        return None
    want = {"last_expense": "expense", "last_income": "income"}.get(target.kind)
    n = 0
    seen: set[int] = set()
    for a in acts:
        if not a.tx_kind or a.kind in ("delete_transaction", "bulk_delete", "undo"):
            continue
        if a.transaction_ids and a.transaction_ids[0] in seen:
            continue
        seen.update(a.transaction_ids[:1])
        if want and a.tx_kind != want:
            continue
        n += 1
        if n == target.position:
            return a
    return None


def _base_currency(st: State, target: TargetSpec) -> str | None:
    a = _base_action(st, target)
    return a.currency.upper() if a is not None and a.currency else None


_CUR_MARK = {"VND": "₫", "USD": "$", "RUB": "₽", "EUR": "€"}
RATIO_LIMIT = Decimal(100)


def _with_currency(st: State, end: int, code: str) -> str:
    mark = _CUR_MARK.get(code, code)
    return " ".join((st.orig[:end] + " " + mark + " " + st.orig[end:]).split())[:300]


def _amount_guard(st: State, a, target: TargetSpec, value: Decimal, cur: str
                  ) -> ClarifyIntent | None:
    """«нет, 250к» on a $15 / 2 TON operation: «к/м» are ₫ thousands/millions by habit,
    so 250 000 in the operation's currency is never applied silently — ask.
    Also asks when the new amount (currency inherited from a $/crypto operation) is more
    than 100× bigger or smaller than the old one («нет, 2000» on $15)."""
    if a.currency:
        return None  # the currency is written: «нет, 250к $» means it
    base = _base_action(st, target)
    default = st.ctx.default_currency.upper()
    if base is None or not base.currency or base.currency.upper() == default:
        return None
    bcur = base.currency.upper()
    if cur != bcur:
        return None
    old = base.amount
    if a.mult is None and (old is None or old <= 0 or (
            value / old <= RATIO_LIMIT and old / value <= RATIO_LIMIT)):
        return None
    try:
        v_def = a.value(default, st.ctx.small_amount_thousands, default)
    except ValueError:
        v_def = None
    opts = []
    if v_def is not None:
        opts.append(_with_currency(st, a.end, default))
    opts.append(_with_currency(st, a.end, bcur))
    shown_new = format_money(value, bcur)
    if v_def is not None:
        q = f"«{a.raw}» — это {format_money(v_def, default)} или {shown_new}?"
    else:
        q = f"Точно {shown_new}?"
    if old is not None and old > 0:
        q += f" Сейчас в операции {format_money(old, bcur)}."
    acc = st.R.account_by_name(base.account) if base.account else None
    if (v_def is not None and a.mult is not None and acc is not None
            and acc.currency == default and target.kind in ("last", "context")
            and target.position == 1):
        opts.insert(1, " ".join(("по факту списали " + a.raw).split()))  # actual ₫ charge
    return ClarifyIntent(reason="ambiguous_currency", question=q[:300], options=opts[:6])


def extract_changes(st: State, idxs: list[int], target: TargetSpec
                    ) -> tuple[TransactionChanges | ClarifyIntent | None, str | None]:
    """Changes from free text («на 150к», «такси», «вчера», «налом», «это доход»).
    A ``ClarifyIntent`` instead of changes: the amount is suspicious (see ``_amount_guard``)."""
    seg = analyze(st, idxs)
    ch: dict = {}
    if len(seg.amounts) > 1:
        return None, "multiple_amounts"
    acc = seg.accounts[0].ref if len(seg.accounts) == 1 else None
    if len(seg.accounts) > 1:
        return None, "multiple_accounts"
    if seg.amounts:
        a = seg.amounts[0]
        cur = a.currency or _base_currency(st, target) or (acc.currency if acc else None)
        cur = cur or st.ctx.default_currency.upper()
        v, problem = amount_of(st, a, cur)
        if v is None:
            return None, problem
        guard = _amount_guard(st, a, target, v, cur)
        if guard is not None:
            return guard, None
        ch["amount"], ch["amount_raw"] = v, a.raw[:64]
        if a.currency:
            ch["currency"] = a.currency
    else:
        from app.services.parser.currencies import detect_currency

        for i in seg.idxs:
            t = st.toks[i]
            if t.kind == "word" and t.free and (c := detect_currency(t.text)):
                if len(t.text) > 2:
                    ch["currency"] = c
                    t.role = "currency"
    if seg.dates:
        d, problem = seg_date(st, seg, None)
        if problem or d is None:
            return None, problem or "ambiguous_date"
        ch["occurred_on"] = d
    if acc is not None:
        ch["account_ref"] = acc.name
    words = _words_in(st, seg.idxs)
    if "без" in words and ("счета" in words or "счет" in words):
        ch["account_none"] = True
        for i in seg.idxs:
            if st.toks[i].text in ("без", "счета", "счет"):
                st.toks[i].role = "target"
    verbs = set(seg.verbs)
    if "refund" in verbs:
        ch["kind"] = "refund"
    elif "income" in verbs and not seg.amounts:
        ch["kind"] = "income"
    elif verbs & {"expense"} and any(st.toks[i].text.startswith("расход") for i in seg.idxs):
        ch["kind"] = "expense"
    want_inc = ch.get("kind") == "income" or target.kind == "last_income"
    cats = seg.cat_inc if want_inc else seg.cat_exp
    cat, amb, _ = best_category(cats)
    if seg.merchants and seg.merchants[0].category is not None:
        cat, amb = seg.merchants[0].category, False
    if amb:
        return None, "ambiguous_category"
    cover = {i for _, ix in cats for i in ix}
    unknown = [i for i in content_idx(st, seg) if i not in cover and st.toks[i].role == ""]
    if cat is not None:
        ch["category_ref"] = cat.name
    if unknown:
        return None, "unknown_words"
    if not ch:
        return None, "no_changes"
    try:
        return TransactionChanges(**ch), None
    except ValidationError:
        return None, "invalid_changes"


def _words_in(st: State, idxs: list[int]) -> list[str]:
    return [st.toks[i].text for i in idxs if st.toks[i].kind == "word"]


# ------------------------------------------------------------------ handlers


def _plain(st: State) -> str:
    return " ".join(re.sub(r"[^\w\s@.+-]", " ", st.norm).split())


def _lead(st: State) -> tuple[str, list[int]]:
    """First meaningful word and the token indices after it."""
    idxs = [i for i, t in enumerate(st.toks) if t.kind != "punct"]
    k = 0
    while k < len(idxs) and st.toks[idxs[k]].text in ("пожалуйста", "плиз", "pls", "please",
                                                       "а", "ну", "так", "давай"):
        k += 1
    if k >= len(idxs):
        return "", []
    return st.toks[idxs[k]].text, idxs[k + 1 :]


def try_commands(st: State) -> ParseResult | None:
    plain = _plain(st)
    if UNDO_RE.match(plain):
        return done([UndoIntent()], 0.95)
    first, rest = _lead(st)
    if first in DELETE_VERBS:
        return _delete(st, first, rest)
    if first in EDIT_VERBS:
        return _edit(st, rest)
    m = CREATE_CAT_RE.match(plain)
    if m:
        return _create_category(st, m)
    if NOT_X_BUT_Y.match(plain):
        return _correction(st, not_x_but_y=True)
    if CORRECTION_START.match(plain):
        return _correction(st)
    words = set(_words(st))
    if words & CHARGED_WORDS:
        return _charged(st)
    if words & CREDITED_WORDS:
        return _credited(st)
    return None


def _delete(st: State, verb: str, rest: list[int]) -> ParseResult:
    st.prepare()
    if not rest:
        if verb.startswith("отмен"):
            return done([UndoIntent()], 0.95)
        target = TargetSpec(kind="context") if st.ctx.edit_transaction_id else TargetSpec()
        return done([DeleteTransactionIntent(target=target)], 0.9)
    words = [st.toks[i].text for i in rest if st.toks[i].kind == "word"]
    bulk = any(w in ("все", "всё", "all", "every") for w in words)
    for i in rest:
        if st.toks[i].text in ("все", "всё", "all", "every"):
            st.toks[i].role = "target"
    if any(st.toks[i].kind == "num" and st.toks[i].role in ("amount", "") for i in rest):
        return llm("bulk_delete", confidence=0.3)
    target, rest2 = _target_of(st, rest)
    has_last = any(w.startswith(("последн", "предпоследн", "last")) for w in words)
    periods = find_periods(st.norm, st.today)
    for h in periods:
        for i in rest2:
            t = st.toks[i]
            if t.start >= h.start and t.end <= h.end:
                t.role = "period"
    rest3 = [i for i in rest2 if st.toks[i].role != "period"]
    # «все расходы на кофе», «все покупки за октябрь»: service nouns are the kind, not text
    noun_kind = None
    for i in rest3:
        t = st.toks[i]
        if t.kind != "word" or not t.free:
            continue
        if t.text.startswith(_EXPENSE_NOUNS):
            t.role, noun_kind = "target", noun_kind or "expense"
        elif t.text.startswith(_INCOME_NOUNS):
            t.role, noun_kind = "target", noun_kind or "income"
        elif t.text.startswith(_ANY_NOUNS) and not t.text.startswith("перевод"):
            t.role = "target"
    seg = analyze(st, rest3)
    learned = [m for m in seg.merchants if not m.builtin]
    cat_matches = seg.cat_exp or seg.cat_inc
    if learned:
        # the merchant filter decides; its keyword's default category may be outdated
        m_idx = {i for i in rest3 if st.toks[i].role == "merchant"} | seg.brand_idx
        cat_matches = [(m, ix) for m, ix in cat_matches if not set(ix) <= m_idx]
    cat, amb, _ = best_category(cat_matches)
    cover = {i for _, ix in seg.cat_exp + seg.cat_inc for i in ix}
    unknown = [st.toks[i] for i in content_idx(st, seg) if i not in cover]
    if has_last and not (periods or cat or learned or seg.handles or unknown):
        if bulk:
            return llm("bulk_delete", confidence=0.3)
        return done([DeleteTransactionIntent(target=target)], 0.95)
    if not has_last and not (periods or cat or learned or seg.handles or unknown):
        if bulk:
            return llm("bulk_delete_unfiltered", confidence=0.2)
        return done([DeleteTransactionIntent(target=target)], 0.9)
    if amb:
        return llm("ambiguous_category", confidence=0.3)
    kinds = None
    if cat is not None:
        kinds = [cat.kind]
    elif target.kind == "last_expense":
        kinds = ["expense"]
    elif target.kind == "last_income":
        kinds = ["income"]
    elif noun_kind:
        kinds = [noun_kind]
    f = SearchFilter(
        period=periods[0].period if periods else None,
        kinds=kinds,
        category_refs=[cat.name] if cat else [],
        account_refs=[m.ref.name for m in seg.accounts],
        merchant_ref=learned[0].name if learned else None,
        counterparty=("@" + seg.handles[0]) if seg.handles else None,
        text=" ".join(t.raw for t in unknown)[:200] or None,
        sort="date_desc",
        limit=100 if bulk else 1,
    )
    conf = 0.85 if not unknown else 0.6
    return done([DeleteTransactionIntent(target=TargetSpec(kind="search", search=f))], conf)


def _update(target: TargetSpec, changes: TransactionChanges | ClarifyIntent,
            conf: float) -> ParseResult:
    if isinstance(changes, ClarifyIntent):
        return done([changes], 0.8, reason="suspicious_amount")
    return done([UpdateTransactionIntent(target=target, changes=changes)], conf)


def _edit(st: State, rest: list[int]) -> ParseResult:
    st.prepare()
    target, rest2 = _target_of(st, rest)
    changes, problem = extract_changes(st, rest2, target)
    if changes is None:
        return llm(problem or "edit", confidence=0.3)
    return _update(target, changes, 0.9)


def _correction(st: State, *, not_x_but_y: bool = False) -> ParseResult:
    """«нет, 250к», «не 200, а 250к», «нет, это такси», «нет, вчера», «нет, это доход»."""
    st.prepare()
    idxs = [i for i, t in enumerate(st.toks) if t.kind != "punct"]
    if not_x_but_y:
        # keep only the part after «а»
        k = next((n for n, i in enumerate(idxs) if st.toks[i].text == "а" and n > 1), None)
        if k is None:
            return llm("correction", confidence=0.3)
        for i in idxs[: k + 1]:
            st.toks[i].role = "ignored"
        idxs = idxs[k + 1 :]
    else:
        lead = 0
        while lead < len(idxs) and st.toks[idxs[lead]].text in (
                "а", "ой", "ах", "нет", "неа", "не", "так", "no", "nope", "точнее", "вернее",
                "поправка", "стоп"):
            st.toks[idxs[lead]].role = "ignored"
            lead += 1
        idxs = idxs[lead:]
    if not idxs:
        return llm("correction", confidence=0.3)
    # «нет, 250к, а не 200»
    for n, i in enumerate(idxs):
        if st.toks[i].text == "а" and n + 1 < len(idxs) and st.toks[idxs[n + 1]].text == "не":
            for j in idxs[n:]:
                st.toks[j].role = "ignored"
            idxs = idxs[:n]
            break
    for i in idxs:
        if st.toks[i].text in ("это", "было", "была", "был", "там", "на", "в"):
            if st.toks[i].text in ("это", "было", "была", "был", "там"):
                st.toks[i].role = "ignored"
    target = TargetSpec(kind="context") if st.ctx.edit_transaction_id else TargetSpec()
    if not st.ctx.recent_actions and st.ctx.edit_transaction_id is None:
        conf = 0.7
    else:
        conf = 0.9
    changes, problem = extract_changes(st, idxs, target)
    if changes is None:
        return llm(problem or "correction", confidence=0.3)
    return _update(target, changes, conf)


def _charged(st: State) -> ParseResult | None:
    """«васт списали 21.3м» → actual charged amount of the last estimated operation."""
    st.prepare()
    if len(st.amounts) != 1:
        return None
    idxs = [i for i, t in enumerate(st.toks) if t.kind != "punct"]
    seg = analyze(st, idxs)
    words = set(_words(st))
    if seg.handles or len(seg.merchants) > 1:
        return None
    merchant_idx = {i for i in idxs if st.toks[i].role == "merchant"} | seg.brand_idx
    cats = [(m, ix) for m, ix in seg.cat_exp + seg.cat_inc if not set(ix) <= merchant_idx]
    m0 = seg.merchants[0] if seg.merchants else None
    merchant_ref = None
    if m0 is not None:
        if m0.builtin:
            # brand without a learned rule yet: the typed word finds older operations by text
            merchant_ref = " ".join(st.toks[i].raw for i in sorted(seg.brand_idx))
        else:
            merchant_ref = m0.name  # canonical: the validator applies it at once (ARCH §7)
        for i in seg.brand_idx:
            st.toks[i].role = "merchant"
    elif cats and len(cats) == 1 and cats[0][0].priority >= 3:
        # built-in category keyword of priority 3 without a learned merchant rule
        merchant_ref = " ".join(st.toks[i].raw for i in cats[0][1])
        for i in cats[0][1]:
            st.toks[i].role = "merchant"
        cats = []
    if cats:
        return None
    hint = bool(words & CHARGED_HINTS)
    if [i for i in content_idx(st, seg) if st.toks[i].text not in CHARGED_HINTS]:
        return None
    a = st.amounts[0]
    acc = seg.accounts[0].ref if len(seg.accounts) == 1 else None
    target: TargetSpec | None = None
    if merchant_ref:
        target = TargetSpec(kind="search", search=SearchFilter(
            merchant_ref=merchant_ref, sort="date_desc", limit=1))
    elif st.ctx.edit_transaction_id is not None:
        target = TargetSpec(kind="context")
    elif hint:
        target = TargetSpec()
    else:
        # «сервер 50$ с карты» … «списали 1.31м»: the estimated charge from the dialogue
        est = _estimated_in_context(st, a.currency)
        if est is None:
            return None
        tx_id, est_acc = est
        target = TargetSpec(kind="id", transaction_id=tx_id)
        acc = acc or est_acc
    cur = a.currency or (acc.currency if acc and acc.currency else None) or (
        st.ctx.default_currency.upper())
    v, problem = amount_of(st, a, cur)
    if v is None:
        return llm(problem or "bad_amount", confidence=0.3)
    ch = TransactionChanges(account_amount=v, account_amount_raw=a.raw[:64])
    return done([UpdateTransactionIntent(target=target, changes=ch)], 0.85)


def _credited(st: State) -> ParseResult | None:
    """«пришло 2.6м» → account_amount of the newest estimated income of the dialogue context.
    Anything else (other words, no such income, amount in the operation's own currency) → None,
    and the text is parsed as a new income."""
    st.prepare()
    if len(st.amounts) != 1:
        return None
    idxs = [i for i, t in enumerate(st.toks) if t.kind != "punct"]
    seg = analyze(st, idxs)
    if seg.handles or seg.merchants or seg.cat_exp or seg.cat_inc:
        return None
    if [i for i in content_idx(st, seg)
            if st.toks[i].text not in CHARGED_HINTS | CREDITED_WORDS]:
        return None
    a = st.amounts[0]
    est = _estimated_in_context(st, a.currency, kinds=("income",))
    if est is None:
        return None
    tx_id, acc = est
    cur = a.currency or acc.currency or st.ctx.default_currency.upper()
    v, problem = amount_of(st, a, cur)
    if v is None:
        return llm(problem or "bad_amount", confidence=0.3)
    ch = TransactionChanges(account_amount=v, account_amount_raw=a.raw[:64])
    return done([UpdateTransactionIntent(target=TargetSpec(kind="id", transaction_id=tx_id),
                                         changes=ch)], 0.85)


def _estimated_in_context(st: State, written: str | None,
                          kinds: tuple[str, ...] = ("expense", "income", "refund")):
    """Newest operation of the dialogue context charged in another currency than its
    single-currency account («$50 с Карта» → ≈ ₫): (transaction id, account) or None."""
    seen: set[int] = set()
    for act in st.ctx.recent_actions:
        if act.kind in ("delete_transaction", "bulk_delete", "undo") or not act.transaction_ids:
            continue
        tid = act.transaction_ids[0]
        if tid in seen:
            continue
        seen.add(tid)
        if act.tx_kind not in kinds or not act.currency:
            continue
        acc = st.R.account_by_name(act.account) if act.account else None
        if acc is None or acc.multi or not acc.currency or acc.currency == act.currency.upper():
            continue
        if written and written != acc.currency:
            continue
        return tid, acc
    return None


def _create_category(st: State, m: re.Match) -> ParseResult:
    rest = m.group("rest")
    is_biz = bool(m.group("biz")) or "бизнес" in rest
    kind = "income" if re.search(r"(?<!\w)доход", rest) else "expense"
    # original-cased name: the original text after the word «категорию»
    m2 = re.search(r"(?:категори[юя]|category)\s+", st.norm)
    tail = st.orig[m2.end():] if m2 else rest
    name = re.sub(r"^(?:для\s+)?(?:расходов|доходов|расход|доход)\s+", "", tail, flags=re.I)
    name = re.sub(r"\s*[\(\[].*$", "", name)
    name = re.sub(r"\s+(?:для\s+бизнеса|бизнес|\(бизнес\))$", "", name, flags=re.I)
    name = name.strip(" «»\"'.,!:")
    if not name or len(name) > 64:
        return llm("create_category", confidence=0.3)
    name = name[0].upper() + name[1:]
    intent = CreateCategoryIntent(name=name, kind=kind, is_business=is_biz)
    return done([intent], 0.9)


def try_edit_context(st: State) -> ParseResult | None:
    """After «Изменить»: the whole message is a patch for ctx.edit_transaction_id."""
    if st.ctx.edit_transaction_id is None:
        return None
    st.prepare()
    idxs = [i for i, t in enumerate(st.toks) if t.kind != "punct"]
    target = TargetSpec(kind="context")
    changes, problem = extract_changes(st, idxs, target)
    if changes is None:
        return llm(problem or "edit_context", confidence=0.3)
    return _update(target, changes, 0.9)

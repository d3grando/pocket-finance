"""Message → segments (one operation each) and per-segment token analysis."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.parser import lexicon as lx
from app.services.parser.amounts import AmountTok
from app.services.parser.dates import DateHit
from app.services.parser.refs import AccRef, Match, MerchRef
from app.services.parser.state import State
from app.services.parser.text import Token

HARD_SEPARATORS = {",", ";", "\n", ".", "!", "?"}
SOFT_SEPARATORS = {"и", "а", "потом", "затем", "плюс", "and", "then", "также", "ещё", "еще",
                   "+", "а ещё", "а еще"}
FROM_PREPS = {"с", "со", "из", "from", "через", "via", "от"}
TO_PREPS = {"на", "в", "во", "to", "into", "on", "in", "к"}
INSTRUMENTAL_FROM = ("ой", "ом", "ем", "ами", "ыми", "ими", "ью")


def verb_class(w: str) -> str | None:
    if w in lx.INCOME_WORDS or w.startswith(lx.INCOME_PREFIXES):
        return "income"
    if w in lx.REFUND_WORDS or w.startswith(lx.REFUND_PREFIXES):
        return "refund"
    if w in lx.EXCHANGE_WORDS or w.startswith(lx.EXCHANGE_PREFIXES):
        return "exchange"
    if w in lx.WITHDRAW_WORDS:
        return "withdraw"
    if w in lx.BUY_SELL_WORDS:
        return lx.BUY_SELL_WORDS[w]
    if w in lx.TRANSFER_WORDS or w.startswith(lx.TRANSFER_PREFIXES):
        return "transfer"
    if w in lx.EXPENSE_WORDS:
        return "expense"
    return None


@dataclass(slots=True)
class AccMention:
    ref: AccRef
    role: str  # from | to | plain
    generic_card: bool
    keyword: str
    idx: int = -1  # message token index of the first alias token


@dataclass(slots=True)
class Seg:
    idxs: list[int]
    amounts: list[AmountTok] = field(default_factory=list)
    dates: list[DateHit] = field(default_factory=list)
    accounts: list[AccMention] = field(default_factory=list)
    merchants: list[MerchRef] = field(default_factory=list)
    handles: list[str] = field(default_factory=list)
    verbs: list[str] = field(default_factory=list)
    purpose: list[Token] = field(default_factory=list)
    purchase_on: bool = False  # purpose came from «на покупку/оплату/подписку …»
    resale: bool = False
    balance_word: bool = False
    no_account: bool = False  # «без счёта»
    brand_idx: set[int] = field(default_factory=set)  # tokens of a built-in brand keyword
    flags: set[str] = field(default_factory=set)
    cat_exp: list[tuple[Match, list[int]]] = field(default_factory=list)
    cat_inc: list[tuple[Match, list[int]]] = field(default_factory=list)

    def toks(self, st: State) -> list[Token]:
        return [st.toks[i] for i in self.idxs]


def split_message(st: State) -> list[list[int]]:
    """Token indices per segment: hard split on , ; . ! ? newline, soft split on «и»/«потом»/«+»
    only when both sides carry an amount."""
    amount_idx = {i for a in st.amounts for i in a.token_idx}
    hard: list[list[int]] = [[]]
    for i, t in enumerate(st.toks):
        if t.kind == "punct" and t.text in HARD_SEPARATORS:
            if hard[-1]:
                hard.append([])
            continue
        if t.kind == "punct":
            continue
        hard[-1].append(i)
    if not hard[-1]:
        hard.pop()
    out: list[list[int]] = []
    for seg in hard:
        out.extend(_soft_split(st, seg, amount_idx))
    return out


def _soft_split(st: State, seg: list[int], amount_idx: set[int]) -> list[list[int]]:
    seps = [p for p, i in enumerate(seg) if _is_soft(st, i)]
    if not seps:
        return [seg]
    parts: list[list[int]] = []
    cur_start = 0
    bounds = seps + [len(seg)]
    for k, p in enumerate(seps):
        left = seg[cur_start:p]
        right = seg[p + 1 : bounds[k + 1]]
        if any(i in amount_idx for i in left) and any(i in amount_idx for i in right):
            parts.append(left)
            st.toks[seg[p]].role = "sep"
            cur_start = p + 1
    parts.append(seg[cur_start:])
    return [p for p in parts if p]


def _is_soft(st: State, i: int) -> bool:
    t = st.toks[i]
    if not t.free:
        return False
    if t.kind == "sym" and t.text == "+":
        return i > 0
    return t.kind == "word" and t.text in SOFT_SEPARATORS


def analyze(st: State, idxs: list[int]) -> Seg:
    """Consume accounts, merchants, handles, markers inside a segment."""
    seg = Seg(idxs=idxs)
    seg.amounts = st.amounts_in(idxs)
    seg.dates = st.dates_in(idxs)
    sub = [st.toks[i] for i in idxs]
    R = st.R
    for k, t in enumerate(sub[:-1]):
        # «без счёта» (explicit: no account) — not an account mention, not description
        if t.free and t.text == "без" and sub[k + 1].free and sub[k + 1].text in NO_ACCOUNT_WORDS:
            t.role = sub[k + 1].role = "account"
            seg.no_account = True
    for m in R.match_accounts(sub):
        for k in range(m.i, m.j):
            sub[k].role = "account"
        seg.accounts.append(AccMention(m.ref, _acc_role(sub, m), m.generic_card,  # type: ignore[arg-type]
                                       m.keyword, idxs[m.i]))
    for t in sub:
        if not t.free:
            continue
        if t.kind == "handle":
            t.role = "handle"
            seg.handles.append(t.text[1:])
            mh = R.merch_by_handle.get(t.text[1:])
            if mh is not None and mh not in seg.merchants:
                seg.merchants.append(mh)
        elif t.kind == "word":
            vc = verb_class(t.text)
            if vc:
                t.role = "marker"
                seg.verbs.append(vc)
            elif t.text in lx.NEGATION_WORDS:
                seg.flags.add("negation")
            elif t.text in lx.FUTURE_WORDS:
                seg.flags.add("future")
            elif t.text in lx.DEBT_WORDS or t.text.startswith(("долг", "займ", "заня", "одолж")):
                seg.flags.add("debt")
            if t.text in lx.BALANCE_WORDS:
                seg.balance_word = True
                t.role = "balance"
        elif t.kind == "sym" and t.text in "+-":
            t.role = "sign"
    for kind, store in (("expense", seg.cat_exp), ("income", seg.cat_inc)):
        for m in R.match_categories(sub, (kind,)):
            store.append((m, [idxs[k] for k in range(m.i, m.j)]))
    for m in R.match_merchants(sub):
        for k in range(m.i, m.j):
            sub[k].role = "merchant"
        if m.ref not in seg.merchants:
            seg.merchants.insert(0, m.ref)
    if not seg.merchants:
        _brands(st, seg)
    text = " ".join(t.text for t in sub)
    seg.resale = any(p in text for p in lx.RESALE_PHRASES)
    if "мне" in text.split() and "transfer" in seg.verbs:
        seg.verbs = ["income" if v == "transfer" else v for v in seg.verbs]
    # purpose: words after «за» up to the next consumed token
    for k, t in enumerate(sub):
        if t.kind == "word" and t.text in ("за", "for") and t.free:
            purpose: list[Token] = []
            for t2 in sub[k + 1 :]:
                if t2.kind == "num" and t2.role == "qty":
                    purpose.append(t2)  # «за интернет на 3 месяца»
                    continue
                if t2.kind != "word" or (not t2.free and t2.role not in ("merchant",)):
                    break
                purpose.append(t2)
            if purpose:
                t.role = "prep"
                seg.purpose = purpose
                break
    if not seg.purpose:
        _purpose_on(sub, seg)
    return seg


def _purpose_on(sub: list[Token], seg: Seg) -> None:
    """«на покупку лицензии майнкрафт», «на оплату сервера», «на подписку netflix» — the purpose of
    a payment, the same as «за X». The generic noun («покупку», «оплату») is dropped."""
    for k, t in enumerate(sub[:-1]):
        nxt = sub[k + 1]
        if not (t.kind == "word" and t.text == "на" and t.free and nxt.kind == "word" and nxt.free
                and nxt.text.startswith(lx.PURPOSE_ON_PREFIXES)):
            continue
        start = k + 2 if nxt.text.startswith(lx.PURPOSE_ON_GENERIC) else k + 1
        purpose: list[Token] = []
        for t2 in sub[start:]:
            if t2.kind != "word" or (not t2.free and t2.role not in ("merchant",)):
                break
            purpose.append(t2)
        if not purpose:
            purpose = [nxt]
        t.role = "prep"
        nxt.role = "prep" if start == k + 2 else nxt.role
        seg.purpose = purpose
        seg.purchase_on = True
        return


NO_ACCOUNT_WORDS = {"счета", "счет", "счету"}


def _brands(st: State, seg: Seg) -> None:
    """A single built-in brand keyword («васт», «grab», «highlands») → merchant reference with
    the canonical name: the learned rule if the context has it, else a built-in one that the
    dispatcher learns («васт» → vast.ai → Серверы/инфра, updated on correction)."""
    found: list[MerchRef] = []
    idx: set[int] = set()
    for m, ix in seg.cat_exp:
        b = st.R.brand(m.keyword)
        if b is None:
            continue
        idx.update(ix)
        if b not in found:
            found.append(b)
    if len(found) == 1:
        seg.merchants.append(found[0])
        seg.brand_idx = idx


_POSSESSIVE = {"мой", "мою", "моей", "моя", "свой", "свою", "своей", "своя", "мои", "свои"}


def _acc_role(sub: list[Token], m: Match) -> str:
    k = m.i - 1
    while k >= 0 and sub[k].kind == "word" and sub[k].text in _POSSESSIVE:
        k -= 1
    if k >= 0 and sub[k].kind == "word" and sub[k].free:
        p = sub[k].text
        if p in FROM_PREPS:
            sub[k].role = "prep"
            return "from"
        if p in TO_PREPS:
            sub[k].role = "prep"
            return "to"
    kw = " ".join(sub[k].text for k in range(m.i, m.j))
    if kw.endswith(INSTRUMENTAL_FROM) or kw in ("нал", "кэш", "кеш", "cash", "qr", "по qr"):
        return "from"
    return "plain"


def content_idx(st: State, seg: Seg) -> list[int]:
    """Indices of free word tokens that carry meaning (not fillers / markers / prepositions)."""
    out = []
    for i in seg.idxs:
        t = st.toks[i]
        if t.kind != "word" or not t.free:
            continue
        if t.text in lx.FILLERS or t.text in lx.BALANCE_WORDS or t.text in SOFT_SEPARATORS:
            continue
        if t.text in lx.NEGATION_WORDS:
            continue
        out.append(i)
    return out

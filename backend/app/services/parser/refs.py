"""Reference resolver: accounts, categories, merchants, handles.

Built from ``ParseContext`` (user aliases, learned merchants, own @handles) plus built-in
vocabulary (``lexicon``). Pure: no DB. Matching is by stems over consecutive word tokens,
longest phrase first.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from app.schemas.intents import ParseContext
from app.services.parser import lexicon as lx
from app.services.parser.text import Token, normalize, phrase_forms, phrase_stems


@dataclass(slots=True, frozen=True)
class AccRef:
    name: str
    currency: str | None
    multi: bool
    kind: str
    default_for: tuple[str, ...] = ()
    assets: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class CatRef:
    name: str
    kind: str  # expense | income
    is_business: bool = False


@dataclass(slots=True, frozen=True)
class MerchRef:
    name: str
    category: CatRef | None = None
    default_account: AccRef | None = None
    handle: str | None = None
    builtin: bool = False  # brand from the built-in lexicon, not (yet) a learned rule


@dataclass(slots=True)
class Match:
    i: int  # first token index
    j: int  # last token index + 1
    ref: object  # AccRef | CatRef | MerchRef
    keyword: str
    priority: float
    generic_card: bool = False
    strong_income: bool = False


_CARD_STEMS = {phrase_stems(w) for w in lx.CARD_WORDS}
_STRONG_STEMS = {phrase_stems(w) for w in lx.INCOME_STRONG}
# pre-stemmed built-ins (computed once)
_BUILTIN_ACC = [
    (canon, cur, kind, [(a, phrase_stems(a)) for a in aliases if not a.startswith("@")],
     [a[1:] for a in aliases if a.startswith("@")])
    for canon, cur, kind, aliases in lx.BUILTIN_ACCOUNTS
]
_BUILTIN_CAT = [
    (name, kind, biz, prio, [(k, phrase_stems(k)) for k in kws])
    for name, kind, biz, prio, kws in lx.BUILTIN_CATEGORIES
]


def _key(s: str) -> str:
    return normalize(s).strip()


class _Index(dict):  # type: ignore[type-arg]
    """First-word key → candidate phrases. ``seen`` holds the (key, stems, kind) already
    registered, so a duplicate check is O(1) — the list scan made building the index
    O(n²) in the number of aliases (100k aliases hung the API for minutes)."""

    __slots__ = ("seen",)

    def __init__(self) -> None:
        super().__init__()
        self.seen: set[tuple[str, tuple[str, ...], object]] = set()


@dataclass(slots=True)
class Resolver:
    accounts: list[AccRef] = field(default_factory=list)
    acc_index: dict[str, list[tuple]] = field(default_factory=_Index)
    acc_handles: dict[str, AccRef] = field(default_factory=dict)
    cat_index: dict[str, list[tuple]] = field(default_factory=_Index)
    merch_index: dict[str, list[tuple]] = field(default_factory=_Index)
    merch_by_handle: dict[str, MerchRef] = field(default_factory=dict)
    merch_by_key: dict[str, MerchRef] = field(default_factory=dict)  # name/alias → learned rule
    my_handles: set[str] = field(default_factory=set)
    known_assets: set[str] = field(default_factory=set)
    categories: list[CatRef] = field(default_factory=list)
    vocab: set[str] = field(default_factory=set)  # words of all aliases (not coin tickers)

    # ------------------------------------------------------------ lookup helpers
    def account_by_name(self, name: str) -> AccRef | None:
        k = _key(name)
        for a in self.accounts:
            if _key(a.name) == k:
                return a
        return None

    def default_account_for(self, currency: str, *, card_only: bool = False) -> AccRef | None:
        for a in self.accounts:
            if currency in a.default_for and (not card_only or a.kind in ("card", "bank")):
                return a
        return None

    def category(self, name: str, kind: str) -> CatRef | None:
        k = _key(name)
        for c in self.categories:
            if c.kind == kind and _key(c.name) == k:
                return c
        return None

    def brand(self, keyword: str) -> MerchRef | None:
        """Brand keyword («васт») → the learned merchant rule with the canonical name (so a
        corrected category wins) or a built-in reference (``builtin=True``) to be learned."""
        canon = lx.BRAND_OF.get(_key(keyword))
        if canon is None:
            return None
        return self.merch_by_key.get(_key(canon)) or MerchRef(canon, builtin=True)

    # ------------------------------------------------------------ matching
    def match_accounts(self, toks: list[Token]) -> list[Match]:
        out = _match(toks, self.acc_index)
        for t_i, t in enumerate(toks):
            if t.kind == "handle" and t.free and t.text[1:] in self.acc_handles:
                out.append(Match(t_i, t_i + 1, self.acc_handles[t.text[1:]], t.text, 5))
        for m in out:
            m.generic_card = phrase_stems(m.keyword) in _CARD_STEMS
        return sorted(out, key=lambda m: m.i)

    def match_categories(self, toks: list[Token], kinds: tuple[str, ...]) -> list[Match]:
        out = []
        for m in _match(toks, self.cat_index, kinds=kinds):
            m.strong_income = phrase_stems(m.keyword) in _STRONG_STEMS
            out.append(m)
        return out

    def match_merchants(self, toks: list[Token]) -> list[Match]:
        return _match(toks, self.merch_index)


def _best_at(toks: list[Token], i: int, index: dict, kinds: tuple[str, ...] | None
             ) -> Match | None:
    t = toks[i]
    if t.kind != "word" or not t.free:
        return None
    cands = index.get(t.stem, [])
    if t.text != t.stem:
        more = index.get(t.text)
        if more:
            cands = cands + more
    n = len(toks)
    best: Match | None = None
    for stems, forms, ref, kw, prio in cands:
        if kinds is not None and getattr(ref, "kind", None) not in kinds:
            continue
        L = len(stems)
        if i + L > n:
            continue
        ok = True
        for off in range(1, L):
            tt = toks[i + off]
            if tt.kind != "word" or not tt.free or (
                tt.stem != stems[off] and tt.stem != forms[off] and tt.text != forms[off]
            ):
                ok = False
                break
        if ok and (best is None or L > best.j - best.i or (
                L == best.j - best.i and prio > best.priority)):
            best = Match(i, i + L, ref, kw, prio)
    return best


def _match(toks: list[Token], index: dict, kinds: tuple[str, ...] | None = None) -> list[Match]:
    """Longest-phrase matching over free word tokens: the longest (then highest-priority, then
    leftmost) candidate wins over overlapping ones, wherever it starts — «с карты руб» is the
    alias «карта руб», not the generic «с карты» + a stray «руб».

    A token matches a phrase word if its stem equals the word's stem or its full form
    («копилки» → stem «копилк» = stem of «копилка»; «сейфа» → stem «сейф» = form «сейф»)."""
    cands = [m for i in range(len(toks)) if (m := _best_at(toks, i, index, kinds)) is not None]
    if len(cands) < 2:
        return cands
    cands.sort(key=lambda m: (-(m.j - m.i), -m.priority, m.i))
    taken: set[int] = set()
    out: list[Match] = []
    for m in cands:
        span = range(m.i, m.j)
        if any(k in taken for k in span):
            continue
        taken.update(span)
        out.append(m)
    out.sort(key=lambda m: m.i)
    return out


def _add(index: dict, stems: tuple[str, ...], ref: object, kw: str, prio: float,
         forms: tuple[str, ...] | None = None) -> None:
    if not stems:
        return
    forms = forms if forms and len(forms) == len(stems) else phrase_forms(kw)
    if len(forms) != len(stems):
        forms = stems
    entry = (stems, forms, ref, kw, prio)
    kind = getattr(ref, "kind", None)
    seen = getattr(index, "seen", None)
    for key in {stems[0], forms[0]}:
        if seen is not None:
            sig = (key, stems, kind)
            if sig in seen:
                continue  # first (higher-priority) registration wins
            seen.add(sig)
        elif any(s == stems and getattr(r, "kind", None) == kind
                 for s, _, r, _, _ in index.get(key, ())):
            continue
        index.setdefault(key, []).append(entry)


def _build(ctx: ParseContext) -> Resolver:
    r = Resolver()
    r.my_handles = {h.lower().lstrip("@") for h in ctx.my_handles}
    r.known_assets = {a.upper() for a in ctx.known_assets}
    # ---- accounts
    ctx_acc: list[tuple[AccRef, set[str]]] = []
    for a in ctx.accounts:
        acc_ref = AccRef(a.name, (a.currency or "").upper() or None, a.is_multi_asset, a.kind,
                         tuple(c.upper() for c in a.default_for_currencies),
                         tuple(x.upper() for x in a.assets))
        r.accounts.append(acc_ref)
        acc_aliases = {_key(a.name)} | {_key(x) for x in a.aliases}
        ctx_acc.append((acc_ref, acc_aliases))
        for al in acc_aliases:
            # an own @handle (my_accounts wallet alias) is a transfer recipient, never an
            # account mention: «перевёл 3 тона @myshop с кошелька» → to_handle, from Кошелёк
            if al.lstrip("@") in r.my_handles:
                continue
            if al.startswith("@"):
                r.acc_handles[al[1:]] = acc_ref
            else:
                _add(r.acc_index, phrase_stems(al), acc_ref, al, 3)
    for canon, cur, kind, b_aliases, b_handles in _BUILTIN_ACC:
        target: AccRef | None = None
        if ctx.accounts:
            ck = _key(canon)
            for acc_ref, acc_aliases in ctx_acc:
                if _key(acc_ref.name) == ck or any(_key(x) in acc_aliases for x, _ in b_aliases):
                    target = acc_ref
                    break
            if target is None:
                continue
        else:
            target = AccRef(canon, cur, cur is None, kind,
                            (cur,) if cur and kind in ("bank", "card") else ())
            r.accounts.append(target)
        _add(r.acc_index, phrase_stems(canon), target, canon, 2)
        for al, al_stems in b_aliases:
            _add(r.acc_index, al_stems, target, al, 2)
        for h in b_handles:
            if h not in r.my_handles:
                r.acc_handles.setdefault(h, target)
    card = r.default_account_for("VND", card_only=True)
    if card is not None:
        for w in lx.CARD_WORDS:
            _add(r.acc_index, phrase_stems(w), card, w, 1)
    # ---- categories
    by_id: dict[int, CatRef] = {}
    for c in ctx.categories:
        cat_ref = CatRef(c.name, c.kind, c.is_business)
        by_id[c.id] = cat_ref
        r.categories.append(cat_ref)
        for al in [c.name, *c.aliases]:
            _add(r.cat_index, phrase_stems(al), cat_ref, _key(al), 2)
    for name, ckind, biz, prio, kws in _BUILTIN_CAT:
        target_c: CatRef | None
        if ctx.categories:
            target_c = r.category(name, ckind)
            if target_c is None:
                continue
        else:
            target_c = CatRef(name, ckind, biz)
            r.categories.append(target_c)
        for kw, kw_stems in kws:
            _add(r.cat_index, kw_stems, target_c, kw, prio)
    # ---- merchants (learned rules have the highest priority)
    acc_by_id = {a.id: r.account_by_name(a.name) for a in ctx.accounts}
    for m in ctx.merchants:
        mref = MerchRef(m.name, by_id.get(m.category_id) if m.category_id else None,
                        acc_by_id.get(m.default_account_id) if m.default_account_id else None,
                        m.handle.lower().lstrip("@") if m.handle else None)
        if mref.handle:
            r.merch_by_handle[mref.handle] = mref
        for al in [m.name, *m.aliases]:
            if al.startswith("@"):
                r.merch_by_handle.setdefault(al[1:].lower(), mref)
            else:
                _add(r.merch_index, phrase_stems(al), mref, _key(al), 4)
                r.merch_by_key.setdefault(_key(al), mref)
    for index in (r.acc_index, r.cat_index, r.merch_index):
        for entries in index.values():
            for _, forms, _, _, _ in entries:
                r.vocab.update(forms)
    return r


_CACHE: OrderedDict[tuple, Resolver] = OrderedDict()
#: The parser runs in worker threads (``asyncio.to_thread``): the LRU is shared between them.
_CACHE_LOCK = threading.Lock()


def _fingerprint(ctx: ParseContext) -> tuple:
    return (
        tuple((a.id, a.name, a.kind, a.currency, a.is_multi_asset, tuple(a.aliases),
               tuple(a.default_for_currencies), tuple(a.assets)) for a in ctx.accounts),
        tuple((c.id, c.name, c.kind, c.is_business, tuple(c.aliases)) for c in ctx.categories),
        tuple((m.id, m.name, tuple(m.aliases), m.handle, m.category_id, m.default_account_id)
              for m in ctx.merchants),
        tuple(ctx.my_handles),
        tuple(ctx.known_assets),
    )


def get_resolver(ctx: ParseContext) -> Resolver:
    """Resolver for this context (small LRU cache keyed by the dictionaries' content)."""
    key = _fingerprint(ctx)
    with _CACHE_LOCK:
        r = _CACHE.get(key)
        if r is not None:
            _CACHE.move_to_end(key)
            return r
    r = _build(ctx)  # outside the lock: a slow build must not block other users' messages
    with _CACHE_LOCK:
        _CACHE[key] = r
        _CACHE.move_to_end(key)
        while len(_CACHE) > 16:
            _CACHE.popitem(last=False)
    return r

"""Reference resolution against ParseContext. Pure: no DB, no network.

Accounts/categories/merchants are matched by normalised name/alias (ё=е, case, punctuation),
then crude stems, then synonyms (categories) and similarity. Category de-duplication for
model-proposed new categories lives here too (normalisation + similarity + synonyms).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.schemas.intents import CtxAccount, CtxCategory, CtxMerchant, ParseContext
from app.services.ai import textnorm as tn

CatKind = Literal["expense", "income"]

#: similarity at/above which a proposed category is considered a duplicate of an existing one
DEDUP_THRESHOLD = 0.75
MERCHANT_THRESHOLD = 0.86


class Ambiguous(Exception):
    def __init__(self, candidates: list[str]) -> None:
        super().__init__(", ".join(candidates))
        self.candidates = candidates


def _names(*parts: object) -> list[str]:
    out: list[str] = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, list):
            out.extend(x for x in p if isinstance(x, str))
    return [x for x in out if x]


def resolve_account(ctx: ParseContext, ref: str | None) -> CtxAccount | None:
    """Exact name/alias → stem → unique prefix. Raises Ambiguous if several match equally."""
    if not ref:
        return None
    r = tn.strip_preposition(tn.norm(ref)).lstrip("@")
    if not r:
        return None
    accs = ctx.accounts

    def pick(pred) -> list[CtxAccount]:
        return [a for a in accs if any(pred(n) for n in _names(a.name, a.aliases))]

    for pred in (
        lambda n: tn.norm(n) == r,
        lambda n: tn.stem_phrase(n) == tn.stem_phrase(r),
        lambda n: len(r) >= 3 and tn.norm(n).startswith(r),
    ):
        found = pick(pred)
        uniq = {a.id: a for a in found}
        if len(uniq) == 1:
            return next(iter(uniq.values()))
        if len(uniq) > 1:
            raise Ambiguous([a.name for a in uniq.values()])
    return None


def account_by_id(ctx: ParseContext, account_id: int | None) -> CtxAccount | None:
    return next((a for a in ctx.accounts if a.id == account_id), None)


def default_account(ctx: ParseContext, currency: str) -> CtxAccount | None:
    c = currency.upper()
    return next((a for a in ctx.accounts if c in a.default_for_currencies), None)


@dataclass
class CategoryResolution:
    category: CtxCategory | None
    score: float
    how: str  # exact | stem | synonym | similar | none


def find_category(ctx: ParseContext, ref: str | None, kind: CatKind) -> CategoryResolution:
    """Existing category for a reference/proposed name (dedup logic), or none."""
    if not ref or not tn.norm(ref):
        return CategoryResolution(None, 0.0, "none")
    cats = [c for c in ctx.categories if c.kind == kind]
    r = tn.norm(ref)
    for c in cats:
        if any(tn.norm(n) == r for n in _names(c.name, c.aliases)):
            return CategoryResolution(c, 1.0, "exact")
    rs = tn.stem_phrase(r)
    for c in cats:
        if any(tn.stem_phrase(n) == rs for n in _names(c.name, c.aliases)):
            return CategoryResolution(c, 0.95, "stem")
    syn = tn.synonym_category(ref, kind)
    if syn:
        for c in cats:
            if tn.norm(c.name) == tn.norm(syn):
                return CategoryResolution(c, 0.9, "synonym")
    best: tuple[object, float] = tn.best_match(ref, ((c, _names(c.name, c.aliases)) for c in cats))
    cat, score = best
    if isinstance(cat, CtxCategory) and score > 0:
        return CategoryResolution(cat, score, "similar")
    return CategoryResolution(None, 0.0, "none")


def category_by_name(ctx: ParseContext, name: str, kind: CatKind) -> CtxCategory | None:
    n = tn.norm(name)
    return next((c for c in ctx.categories if c.kind == kind and tn.norm(c.name) == n), None)


def category_by_id(ctx: ParseContext, category_id: int | None) -> CtxCategory | None:
    return next((c for c in ctx.categories if c.id == category_id), None)


def resolve_merchant(
    ctx: ParseContext, *, name: str | None = None, handle: str | None = None
) -> CtxMerchant | None:
    """By handle (exact, without @) or by name/alias (exact → stem → similarity ≥ 0.86)."""
    if handle:
        h = handle.lstrip("@").lower()
        m = next((m for m in ctx.merchants if (m.handle or "").lower() == h), None)
        if m:
            return m
        m = next(
            (m for m in ctx.merchants if tn.norm(m.name).lstrip("@") == h or h in m.aliases), None
        )
        if m:
            return m
    if name:
        r = tn.norm(name)
        if not r:
            return None
        for m in ctx.merchants:
            if any(tn.norm(n) == r for n in _names(m.name, m.aliases)):
                return m
        rs = tn.stem_phrase(r)
        for m in ctx.merchants:
            if any(tn.stem_phrase(n) == rs for n in _names(m.name, m.aliases)):
                return m
        found, score = tn.best_match(name, ((m, _names(m.name, m.aliases)) for m in ctx.merchants))
        if isinstance(found, CtxMerchant) and score >= MERCHANT_THRESHOLD:
            return found
    return None


def recent_transaction_ids(ctx: ParseContext) -> list[int]:
    """Transaction ids from the dialogue context, newest first, unique."""
    seen: list[int] = []
    for a in ctx.recent_actions:
        if a.kind in ("delete_transaction", "bulk_delete", "undo"):
            continue
        for t in a.transaction_ids:
            if t not in seen:
                seen.append(t)
    return seen


def allowed_transaction_ids(ctx: ParseContext) -> set[int]:
    ids = {t for a in ctx.recent_actions for t in a.transaction_ids}
    if ctx.edit_transaction_id:
        ids.add(ctx.edit_transaction_id)
    return ids

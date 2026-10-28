"""Driver for operation phrases: split a message into segments, merge
modifier-only pieces («вчера, …», «…, налом»), split «такси 45к кофе 30к», interpret each,
aggregate into one ParseResult."""

from __future__ import annotations

from app.schemas.intents import ClarifyIntent, ParseResult
from app.services.parser.ops import SegOut, fail, interpret
from app.services.parser.refs import AccRef
from app.services.parser.segments import Seg, analyze, content_idx, split_message
from app.services.parser.state import State, done, llm

_ANALYZE_ROLES = {"account", "merchant", "handle", "marker", "prep", "balance", "sign", "sep"}


def _reset(st: State, idxs: list[int]) -> None:
    for i in idxs:
        if st.toks[i].role in _ANALYZE_ROLES:
            st.toks[i].role = ""


def _has_meaning(st: State, seg: Seg) -> bool:
    return bool(content_idx(st, seg) or seg.handles or seg.merchants or seg.purpose)


def _merge_orphans(st: State, segs: list[Seg]) -> tuple[list[Seg], str | None]:
    """Attach amount-less pieces to neighbours. Returns (segments, problem)."""
    out: list[Seg] = []
    pending: list[int] = []
    problem = None
    for k, seg in enumerate(segs):
        if seg.amounts:
            if pending:
                seg = _reanalyze(st, pending + seg.idxs)
                pending = []
            out.append(seg)
            continue
        nxt = segs[k + 1] if k + 1 < len(segs) else None
        if not _has_meaning(st, seg):
            # «вчера,» / «, налом» / «с кошелька:» — modifier only
            if nxt is not None:
                pending.extend(seg.idxs)
            elif out:
                out[-1] = _reanalyze(st, out[-1].idxs + seg.idxs)
            else:
                pending.extend(seg.idxs)
            continue
        if nxt is not None and nxt.amounts and not _has_meaning(st, nxt):
            pending.extend(seg.idxs)  # «кофе, 85к»
            continue
        problem = "clause_without_amount"
        pending.extend(seg.idxs)
    if pending and out:
        problem = problem or "clause_without_amount"
    return out, problem


def _reanalyze(st: State, idxs: list[int]) -> Seg:
    idxs = sorted(set(idxs))
    _reset(st, idxs)
    return analyze(st, idxs)


def _split_by_amounts(st: State, seg: Seg) -> list[Seg] | None:
    """«такси 45к кофе 30к» → two segments. None if the pattern is not clean."""
    lead = [i for i in seg.idxs if st.toks[i].kind in ("word", "num")
            and not st.toks[i].role.startswith("date") and st.toks[i].role != "marker"]
    if not lead:
        return None
    amount_first = st.toks[lead[0]].role == "amount"
    bounds: list[int] = []
    if amount_first:
        for a in seg.amounts[1:]:
            bounds.append(seg.idxs.index(a.token_idx[0]))
    else:
        for a in seg.amounts[:-1]:
            bounds.append(seg.idxs.index(a.token_idx[-1]) + 1)
    parts: list[list[int]] = []
    prev = 0
    for b in bounds:
        parts.append(seg.idxs[prev:b])
        prev = b
    parts.append(seg.idxs[prev:])
    _reset(st, seg.idxs)
    out = [analyze(st, p) for p in parts]
    for s in out:
        if len(s.amounts) != 1 or not _has_meaning(st, s):
            return None
    return out


def _lead_account(st: State, seg: Seg) -> AccRef | None:
    """Account written before everything else of the first operation («налом: кофе 40к, …»,
    «с кошелька: 10 usdt впн, 20 usdt gpt») — a header for the whole message, like a leading
    date."""
    refs = {m.ref for m in seg.accounts}
    if len(refs) != 1 or seg.no_account:
        return None
    pos = min(m.idx for m in seg.accounts)
    if pos < 0:
        return None
    meaning = set(content_idx(st, seg))
    meaning.update(i for a in seg.amounts for i in a.token_idx)
    meaning.update(i for i in seg.idxs if st.toks[i].role in ("merchant", "handle"))
    if any(i < pos for i in meaning):
        return None
    return next(iter(refs))


def parse_transactions(st: State) -> ParseResult | None:
    st.prepare()
    if not st.amounts:
        return None
    segs = [analyze(st, idxs) for idxs in split_message(st)]
    segs, problem = _merge_orphans(st, segs)
    if not segs:
        return None
    final: list[Seg] = []
    for seg in segs:
        verbs = set(seg.verbs)
        if len(seg.amounts) >= 2 and not verbs & {"exchange", "transfer", "buy", "sell"}:
            parts = _split_by_amounts(st, seg)
            if parts is None:
                return llm("multiple_amounts", confidence=0.3,
                           unparsed=st.orig)
            final.extend(parts)
        else:
            final.append(seg)
    single = len(final) == 1 and problem is None
    outs: list[SegOut] = []
    inh_date = None
    inh_kind = None
    lead_acc = _lead_account(st, final[0]) if len(final) > 1 else None
    for n, seg in enumerate(final):
        if len(final) > 10:
            outs.append(fail("too_many"))
            break
        o = interpret(st, seg, inh_date, inh_kind, single, inh_acc=lead_acc if n else None)
        outs.append(o)
        if o.day is not None:
            inh_date = o.day
        if o.kind:
            inh_kind = o.kind
    intents = [o.intent for o in outs if o.intent is not None]
    conf = min((o.confidence for o in outs), default=0.3)
    if problem is None and all(not o.needs_llm for o in outs) and intents:
        return done(intents, conf)
    reason = problem or next((o.reason for o in outs if o.needs_llm), None) or "unclear"
    has_clarify = any(isinstance(i, ClarifyIntent) for i in intents)
    partial = (
        problem is None
        and all(o.safe and o.intent is not None for o in outs)
        and (not has_clarify or len(intents) == 1)
    )
    unparsed = None if partial else st.orig
    return llm(reason, confidence=min(conf, 0.5), intents=intents, partial_ok=partial,
               unparsed=unparsed)

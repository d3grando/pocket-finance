"""Scoring of model output against the dataset (pure, no network/DB).

Model intents are canonicalised with the production validator's own (synchronous) resolution
code, so the score reflects what would actually be recorded: amounts recomputed from
``amount_raw``, categories de-duplicated, own/foreign @handles converted, refs → names.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from app.core.money import normalize
from app.schemas.intents import (
    AdjustBalanceIntent,
    ClarifyIntent,
    CreateCategoryIntent,
    CreateTransactionIntent,
    CtxDebt,
    DebtIntent,
    DebtsQueryIntent,
    DeleteTransactionIntent,
    Intent,
    ParseContext,
    SearchIntent,
    StatsIntent,
    TargetSpec,
    TransferIntent,
    UpdateTransactionIntent,
)
from app.services.ai import compat
from app.services.ai import resolve as rs
from app.services.ai.debt_validate import display_name, in_text, owe_statement
from app.services.ai.validator import _Fail, _Validator
from app.services.parser import debt_words as dw


def _acc_name(ctx: ParseContext, acc_id: int | None) -> str | None:
    a = rs.account_by_id(ctx, acc_id)
    return a.name if a else None


def _cat_from(ctx: ParseContext, res: dict[str, Any]) -> str | None:
    if res.get("category_id"):
        c = rs.category_by_id(ctx, res["category_id"])
        return c.name if c else None
    if res.get("new_category") is not None:
        return "NEW:" + res["new_category"].name
    return None


def _money(v: Decimal | None) -> str | None:
    return normalize(v) if v is not None else None


def _eff(amount: Decimal | None, raw: str | None, cur: str | None) -> Decimal | None:
    if raw:
        try:
            return compat.parse_amount(raw, cur or "VND", small_thousands=True)
        except (ValueError, ArithmeticError):
            pass
    return amount


def _target(t: TargetSpec, v: _Validator, ctx: ParseContext) -> dict[str, Any]:
    out: dict[str, Any] = {"target": t.kind}
    if t.kind in ("last", "last_expense", "last_income"):
        out["position"] = t.position
    if t.kind == "id":
        out["target_id"] = t.transaction_id
    if t.kind == "search" and t.search is not None:
        sf = v.search_filter(t.search)
        out["categories"] = sorted(
            c.name for c in ctx.categories if c.id in set(sf.category_ids or [])
        )
        out["period"] = sf.period.preset.value if sf.period else None
        if sf.merchant_ref:
            out["merchant"] = sf.merchant_ref
    return out


def _person(ctx: ParseContext, ref: str | None, name: str | None) -> tuple[Any, str | None]:
    if not ref:
        return None, None
    m = dw.match_person(ctx, ref, ref[1:] if ref.startswith("@") else None)
    if not isinstance(m, CtxDebt) and name:
        m = dw.match_person(ctx, name)
    if isinstance(m, CtxDebt):
        return m, m.name
    return None, display_name(ref, name)


def _debt(it: DebtIntent, v: _Validator, ctx: ParseContext, text: str) -> dict[str, Any]:
    """What production would record for a debt intent, with ``ctx.debts`` standing in for the
    DB balances (п.5 fallback → the ordinary income / expense, ``repay`` → by direction)."""
    if not in_text(text, it.person):
        return {"type": "debt", "error": f"unsafe: person «{it.person}» not in the text"}
    if dw.is_generic(it.person) and not it.fallback and not (
            it.person_name and not dw.is_generic(it.person_name)
            and in_text(text, it.person_name)):
        return {"type": "clarify", "reason": "ambiguous_target"}  # «другу» — which friend?
    if it.op in ("lend", "borrow") and owe_statement(text):
        it = it.model_copy(update={"account_none": True, "account_ref": None})
    acc = None if (it.op == "forgive" or it.account_none) else v.account(it.account_ref)
    has_amount = it.amount is not None or bool(it.amount_raw)
    m, name = _person(ctx, it.person, it.person_name)
    cur = v.currency(it.currency, acc) if (it.currency or has_amount) else None
    amount = v.amount(it.amount, it.amount_raw, cur) if has_amount and cur else None
    op = it.op
    need = {"repay_in": "owed_to_me", "repay_out": "i_owe"}.get(op) or it.direction
    if cur is None:
        opens = dw.open_currencies(m, need)
        cur = opens[0] if len(opens) == 1 else None
    if op == "repay":
        op = "repay_in" if dw.has_open(m, cur, "owed_to_me") else "repay_out"
    if op in ("repay_in", "repay_out") and it.fallback and not dw.has_open(m, cur, need or ""):
        income = it.fallback == "income"
        return {
            "type": "create_transaction", "kind": "income" if income else "expense",
            "amount": _money(amount), "currency": cur,
            "category": (it.category_ref or "Прочее") if income else "Переводы людям",
            "account": acc.name if acc else None, "counterparty": (name or "").lower() or None,
            "date": (it.occurred_on - ctx.today).days if it.occurred_on else 0,
        }
    return {
        "type": "debt", "op": op, "person": name, "amount": _money(amount), "currency": cur,
        "account": acc.name if acc else None, "direction": it.direction,
        "date": (it.occurred_on - ctx.today).days if it.occurred_on else 0,
    }


def canon(it: Intent, ctx: ParseContext, text: str) -> dict[str, Any]:
    """Comparable dict of what would be executed for this intent."""
    v = _Validator(None, ctx, text, "llm")  # type: ignore[arg-type]
    try:
        if isinstance(it, (CreateTransactionIntent, TransferIntent)):
            vi = v.create(it) if isinstance(it, CreateTransactionIntent) else v.transfer(it)
            r = vi.resolved
            if isinstance(vi.intent, TransferIntent):
                to = (("@" + r["to_handle"]) if r.get("to_handle")
                      else _acc_name(ctx, r["to_account_id"]))
                return {
                    "type": "transfer", "from": _acc_name(ctx, r["from_account_id"]), "to": to,
                    "amount": _money(r["amount"]), "currency": r["currency"],
                    "counter_amount": _money(r.get("counter_amount")),
                    "counter_currency": r.get("counter_currency"),
                    "date": (r["occurred_on"] - ctx.today).days if r.get("occurred_on") else 0,
                }
            return {
                "type": "create_transaction", "kind": r["kind"], "amount": _money(r["amount"]),
                "currency": r["currency"], "category": _cat_from(ctx, r),
                "account": _acc_name(ctx, r.get("account_id")),
                "counterparty": (r.get("counterparty") or None) and r["counterparty"].lower(),
                "date": (r["occurred_on"] - ctx.today).days if r.get("occurred_on") else 0,
            }
        if isinstance(it, AdjustBalanceIntent):
            r = v.adjust(it).resolved
            return {"type": "adjust_balance", "account": _acc_name(ctx, r["account_id"]),
                    "amount": _money(r["amount"]), "currency": r["asset"]}
        if isinstance(it, UpdateTransactionIntent):
            ch = it.changes
            out = {"type": "update_transaction", **_target(it.target, v, ctx)}
            cur = ch.currency or "VND"
            if ch.amount is not None or ch.amount_raw:
                out["amount"] = _money(_eff(ch.amount, ch.amount_raw, cur))
            if ch.account_amount is not None or ch.account_amount_raw:
                out["account_amount"] = _money(_eff(ch.account_amount, ch.account_amount_raw,
                                                    "VND"))
            if ch.category_ref or ch.new_category:
                out["category"] = _cat_from(ctx, v.category("expense", ref=ch.category_ref,
                                                            new=ch.new_category))
            if ch.occurred_on is not None:
                out["date"] = (ch.occurred_on - ctx.today).days
            if ch.account_ref:
                try:
                    acc = rs.resolve_account(ctx, ch.account_ref)
                except rs.Ambiguous:
                    acc = None
                out["account"] = acc.name if acc else f"?{ch.account_ref}"
            return out
        if isinstance(it, DeleteTransactionIntent):
            return {"type": "delete_transaction", **_target(it.target, v, ctx)}
        if isinstance(it, SearchIntent):
            sf = v.search_filter(it.filter)
            return {
                "type": "search", "kinds": sorted(sf.kinds or []),
                "min_amount": _money(sf.min_amount), "max_amount": _money(sf.max_amount),
                "amount_currency": sf.amount_currency,
                "categories": sorted(c.name for c in ctx.categories
                                     if c.id in set(sf.category_ids or [])),
                "period": sf.period.preset.value if sf.period else None, "sort": sf.sort,
                "merchant": sf.merchant_ref,
            }
        if isinstance(it, StatsIntent):
            r = v.stats(it).resolved
            return {"type": "stats", "metric": it.metric, "period": it.period.preset.value,
                    "categories": sorted(c.name for c in ctx.categories
                                         if c.id in set(r["category_ids"]))}
        if isinstance(it, CreateCategoryIntent):
            return {"type": "create_category", "name": it.name.strip().lower()}
        if isinstance(it, ClarifyIntent):
            return {"type": "clarify", "reason": it.reason}
        if isinstance(it, DebtIntent):
            return _debt(it, v, ctx, text)
        if isinstance(it, DebtsQueryIntent):
            _, name = _person(ctx, it.person, None)
            return {"type": "debts_query", "person": name, "direction": it.direction}
        return {"type": it.type}
    except _Fail as f:
        return {"type": it.type, "error": f"{f.failure.reason}: {f.failure.message}"}


def _match(expected: Any, got: Any) -> bool:
    if isinstance(expected, dict) and "$any" in expected:
        return any(_match(e, got) for e in expected["$any"])
    if isinstance(expected, str) and isinstance(got, str):
        return expected.strip().lower() == got.strip().lower()
    if isinstance(expected, (int, float)) and isinstance(got, str):
        try:
            return Decimal(str(expected)) == Decimal(got)
        except ArithmeticError:
            return False
    if isinstance(expected, str) and got is not None and not isinstance(got, str):
        return expected == str(got)
    return expected == got


def score_case(expected: list[dict[str, Any]], got: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Per-field results for one phrase. ``got`` None = the model call failed."""
    fields: dict[str, bool] = {}
    if got is None:
        for i, e in enumerate(expected):
            for k in e:
                fields[f"{i}.{k}"] = False
        return {"exact": False, "count_ok": False, "fields": fields}
    count_ok = len(got) == len(expected)
    for i, e in enumerate(expected):
        g = got[i] if i < len(got) else {}
        for k, ev in e.items():
            fields[f"{i}.{k}"] = _match(ev, g.get(k)) and "error" not in g
    return {"exact": count_ok and all(fields.values()), "count_ok": count_ok, "fields": fields}


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))
    return s[k]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """rows: [{"status", "latency_ms", "score", "prompt_tokens", "completion_tokens", "tags"}]"""
    n = len(rows)
    by_field: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        for k, ok in r["score"]["fields"].items():
            by_field[k.split(".", 1)[1]].append(ok)
    lat = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    by_tag: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        for t in r.get("tags", []):
            by_tag[t].append(r["score"]["exact"])
    return {
        "n": n,
        "exact": round(sum(r["score"]["exact"] for r in rows) / n, 4) if n else None,
        "valid_json": round(sum(r["status"] == "ok" for r in rows) / n, 4) if n else None,
        "count_ok": round(sum(r["score"]["count_ok"] for r in rows) / n, 4) if n else None,
        "fields": {k: round(sum(v) / len(v), 4) for k, v in sorted(by_field.items())},
        "tags": {k: round(sum(v) / len(v), 4) for k, v in sorted(by_tag.items())},
        "status": dict(Counter(r["status"] for r in rows)),
        "latency_ms": {
            "p50": _pct(lat, 50), "p95": _pct(lat, 95),
            "mean": round(statistics.mean(lat)) if lat else None,
        },
        "tokens_mean": {
            "prompt": round(statistics.mean([r["prompt_tokens"] or 0 for r in rows])) if n else 0,
            "completion": round(statistics.mean([r["completion_tokens"] or 0 for r in rows]))
            if n else 0,
        },
    }

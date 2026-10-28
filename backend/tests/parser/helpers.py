"""Spec builders and a subset-checker for table-driven parser tests."""

from __future__ import annotations

from typing import Any

from app.schemas.intents import ParseResult


def E(amount: str, cur: str, cat: str | None = None, **kw: Any) -> dict:
    """Expense spec."""
    d = {"type": "create_transaction", "kind": "expense", "amount": amount, "currency": cur}
    if cat is not None:
        d["category_ref"] = cat
    d.update(kw)
    return d


def I(amount: str, cur: str, cat: str | None = None, **kw: Any) -> dict:  # noqa: E743
    d = E(amount, cur, cat, **kw)
    d["kind"] = "income"
    return d


def R(amount: str, cur: str, cat: str | None = None, **kw: Any) -> dict:
    d = E(amount, cur, cat, **kw)
    d["kind"] = "refund"
    return d


def T(amount: str, cur: str, frm: str | None = None, to: str | None = None, **kw: Any) -> dict:
    d = {"type": "transfer", "amount": amount, "currency": cur, "from_account_ref": frm,
         "to_account_ref": to}
    d.update(kw)
    return d


def X(amount: str, cur: str, camount: str, ccur: str, **kw: Any) -> dict:
    d = {"type": "transfer", "amount": amount, "currency": cur, "counter_amount": camount,
         "counter_currency": ccur}
    d.update(kw)
    return d


def ADJ(acc: str, amount: str, cur: str) -> dict:
    return {"type": "adjust_balance", "account_ref": acc, "amount": amount, "currency": cur}


def UPD(target: dict | None = None, **changes: Any) -> dict:
    return {"type": "update_transaction", "target": target or {"kind": "last"},
            "changes": changes}


def DEL(kind: str = "last", **kw: Any) -> dict:
    t = {"kind": kind}
    t.update(kw)
    return {"type": "delete_transaction", "target": t}


def ST(metric: str = "spent", period: dict | None = None, **kw: Any) -> dict:
    d = {"type": "stats", "metric": metric}
    if period is not None:
        d["period"] = period
    d.update(kw)
    return d


def SR(**flt: Any) -> dict:
    return {"type": "search", "filter": flt}


def P(preset: str) -> dict:
    return {"preset": preset}


def PC(a: str, b: str) -> dict:
    return {"preset": "custom", "date_from": a, "date_to": b}


UNDO = {"type": "undo", "action_id": None}


def CL(reason: str) -> dict:
    return {"type": "clarify", "reason": reason}


def subset(expected: Any, got: Any, path: str = "") -> list[str]:
    errs: list[str] = []
    if isinstance(expected, dict):
        if not isinstance(got, dict):
            return [f"{path}: expected dict, got {got!r}"]
        for k, v in expected.items():
            if k not in got:
                errs.append(f"{path}.{k}: missing (got keys {sorted(got)})")
                continue
            errs.extend(subset(v, got[k], f"{path}.{k}"))
    elif isinstance(expected, list) and expected and isinstance(expected[0], dict):
        if not isinstance(got, list) or len(got) != len(expected):
            return [f"{path}: expected {len(expected)} items, got {got!r}"]
        for n, (e, g) in enumerate(zip(expected, got, strict=True)):
            errs.extend(subset(e, g, f"{path}[{n}]"))
    elif expected != got:
        errs.append(f"{path}: expected {expected!r}, got {got!r}")
    return errs


def check(res: ParseResult, spec: dict | list) -> list[str]:
    """spec: list of intent specs (→ needs_llm False) or dict with keys
    i (intents), llm (needs_llm), partial (partial_ok), reason, n (intent count), minconf."""
    if isinstance(spec, list):
        spec = {"i": spec}
    data = res.model_dump(mode="json")
    errs: list[str] = []
    want_llm = spec.get("llm", False)
    if data["needs_llm"] != want_llm:
        errs.append(f"needs_llm: expected {want_llm}, got {data['needs_llm']} "
                    f"(reason={data['reason']})")
    if "partial" in spec and data["partial_ok"] != spec["partial"]:
        errs.append(f"partial_ok: expected {spec['partial']}, got {data['partial_ok']}")
    if "reason" in spec and data["reason"] != spec["reason"]:
        errs.append(f"reason: expected {spec['reason']!r}, got {data['reason']!r}")
    if "n" in spec and len(data["intents"]) != spec["n"]:
        errs.append(f"intents: expected {spec['n']}, got {len(data['intents'])}")
    if "i" in spec:
        errs.extend(subset(spec["i"], data["intents"], "intents"))
    if "minconf" in spec and data["confidence"] < spec["minconf"]:
        errs.append(f"confidence {data['confidence']} < {spec['minconf']}")
    if not want_llm and data["confidence"] < 0.6:
        errs.append(f"confident result expected, confidence={data['confidence']}")
    return errs

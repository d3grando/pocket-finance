"""Table-driven tests of the debt parser (docs/DEBTS.md §5.2): every phrase of the spec table in
RU + EN, declensions, @handles, accounts, dates, the п.5 ambiguity, button texts,
questions, and phrases that must stay ordinary operations."""

from __future__ import annotations

import time

import pytest

from app.core.person_names import name_key, same_person
from app.services.parser import parse

from .cases_debts import CASES, CTXS
from .helpers import check


def _id(case: tuple) -> str:
    return f"{case[0][:40]} [{case[2]}]"


@pytest.mark.parametrize("case", CASES, ids=[_id(c) for c in CASES])
def test_debt_parse(case: tuple) -> None:
    text, spec, ctx_name = case
    res = parse(text, CTXS[ctx_name])
    errs = check(res, spec)
    assert not errs, f"{text!r}:\n  " + "\n  ".join(errs) + "\n  got: " + res.model_dump_json(
        exclude_defaults=True)


def test_case_count() -> None:
    assert len(CASES) >= 60


@pytest.mark.parametrize(
    ("text", "ctx_name"),
    [("дал в долг Коле 500к", "debts"), ("Коля вернул 200к", "debts"),
     ("занял у Пети 1м", "seed"), ("отдал Пете 500к", "debts")],
)
def test_parser_and_service_see_one_person(text: str, ctx_name: str) -> None:
    """«Коле», «у Пети», «Коля» match the stored name with the shared person_names key."""
    [it] = parse(text, CTXS[ctx_name]).intents
    assert same_person(name_key(it.person), name_key(it.person_name or ""))


def test_debt_phrases_are_fast() -> None:
    ctx = CTXS["debts"]
    texts = [c[0] for c in CASES]
    t0 = time.perf_counter()
    for _ in range(5):
        for t in texts:
            parse(t, ctx)
    per = (time.perf_counter() - t0) / (5 * len(texts))
    assert per < 0.005, f"{per * 1000:.2f} ms per phrase"


@pytest.mark.parametrize("text", ["Коля перевёл мне 100к за пиццу",
                                  "@vasya скинул мне 5 USDT за подписку",
                                  "Коля скинул мне 50к за кино"])
def test_paid_me_for_something_is_not_a_repayment(text: str) -> None:
    """«X перевёл мне N за <что-то>» is a payment: never a DebtIntent (DEBTS §5.2)."""
    res = parse(text, CTXS["debts"])
    assert not any(i.type == "debt" for i in res.intents), res.model_dump_json()

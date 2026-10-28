"""Contract: parser output is the same Pydantic intents the LLM returns (ARCH §5)."""

from __future__ import annotations

import json
import random
from decimal import Decimal

import pytest

from app.schemas.intents import IntentList, ParseResult
from app.services.parser import parse
from app.services.parser.amounts import parse_amount

from . import cases_cmd, cases_more, cases_ops
from .ctxs import CTX, CTX_EMPTY, CTXS

TEXTS = [c[0] for c in cases_ops.ALL + cases_cmd.ALL + cases_more.MORE]


@pytest.mark.parametrize("ctx_name", ["seed", "empty", "edit", "recent"])
def test_intents_roundtrip_as_llm_json(ctx_name: str) -> None:
    """Every parser result validates as the LLM IntentList JSON and round-trips exactly."""
    ctx = CTXS[ctx_name]
    for text in TEXTS:
        res = parse(text, ctx)
        assert isinstance(res, ParseResult)
        dumped = res.model_dump_json()
        again = ParseResult.model_validate_json(dumped)
        assert again == res, text
        if res.intents:
            il = IntentList.model_validate({"intents": json.loads(dumped)["intents"]})
            assert len(il.intents) == len(res.intents)


def test_amount_raw_recomputes_to_amount() -> None:
    """Validator rule: amount = parse_amount(amount_raw, currency) — parser must agree."""
    checked = 0
    for text in TEXTS:
        for ctx in (CTX, CTX_EMPTY):
            res = parse(text, ctx)
            for it in res.intents:
                raw = getattr(it, "amount_raw", None)
                if raw and getattr(it, "amount", None) is not None and it.type != "adjust_balance":
                    small = ctx.small_amount_thousands
                    assert parse_amount(raw, it.currency, small_thousands=small) == it.amount, (
                        text, raw)
                    checked += 1
                craw = getattr(it, "counter_amount_raw", None)
                if craw:
                    assert parse_amount(craw, it.counter_currency) == it.counter_amount, text
    assert checked > 150


def test_money_is_decimal_and_json_strings() -> None:
    res = parse("кофе 85к, такси 0.5 usdt с кошелька", CTX)
    for it in res.intents:
        assert isinstance(it.amount, Decimal)
    data = json.loads(res.model_dump_json())
    assert all(isinstance(i["amount"], str) for i in data["intents"])


def test_confident_results_do_not_need_llm() -> None:
    for text in TEXTS:
        res = parse(text, CTX)
        if not res.needs_llm:
            assert res.intents, text
            assert res.confidence >= 0.6, text
        if res.partial_ok:
            assert res.needs_llm and res.intents, text


def test_ambiguous_currency_is_never_guessed() -> None:
    for text in ["в кошельке 120", "сервер 100 с кошелька", "перевёл 100 с биржи на сейф",
                 "оплатил 50 с копилки"]:
        res = parse(text, CTX)
        assert res.needs_llm, text
        for it in res.intents:
            assert it.type == "clarify", text


def test_usdt_is_not_usd() -> None:
    a = parse("сервер 100 usdt", CTX).intents[0]
    b = parse("сервер 100 usd", CTX).intents[0]
    assert (a.currency, b.currency) == ("USDT", "USD")


def test_parse_is_pure_and_deterministic() -> None:
    for text in TEXTS[:80]:
        assert parse(text, CTX) == parse(text, CTX)


def test_context_is_not_mutated() -> None:
    before = CTX.model_dump_json()
    for text in TEXTS:
        parse(text, CTX)
    assert CTX.model_dump_json() == before


_ALPHABET = list("кофе такси 85к 1.5м $ usdt @carol ,.;?!\n+-вчера налом с на в 0 12 тона ₫đ€")


def test_fuzz_never_raises() -> None:
    rnd = random.Random(20261027)
    for _ in range(1500):
        text = "".join(rnd.choice(_ALPHABET) for _ in range(rnd.randint(0, 40)))
        res = parse(text, CTX)
        assert isinstance(res, ParseResult)
        assert res.reason != "parser_error", text


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_empty(text: str) -> None:
    res = parse(text, CTX)
    assert res.intents == [] and not res.needs_llm and res.reason == "empty"

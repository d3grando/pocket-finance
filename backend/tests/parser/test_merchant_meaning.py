"""Regressions: merchant rule vs meaning words, «за аккаунт», «пришло X»."""

from __future__ import annotations

from app.schemas.intents import CreateTransactionIntent, UpdateTransactionIntent
from app.services.parser import parse
from tests.parser.ctxs import CTX, ctx, recent


def _create(text: str, c=CTX) -> CreateTransactionIntent:
    res = parse(text, c)
    assert len(res.intents) == 1 and not res.needs_llm, res.model_dump_json(exclude_defaults=True)
    it = res.intents[0]
    assert isinstance(it, CreateTransactionIntent), it
    return it


def test_handle_rule_does_not_beat_keyword_category() -> None:
    # @alice is remembered as «Софт и AI», but the text says «такси»
    assert _create("перевёл 50к @alice за такси").category_ref == "Такси/транспорт"
    # no meaning words → the remembered rule
    assert _create("перевёл 3 тона @alice с кошелька").category_ref == "Софт и AI"


def test_brand_rule_still_wins_over_brand_keyword() -> None:
    assert _create("васт 21$").category_ref == "Серверы/инфра"


def test_za_akkaunt_is_software() -> None:
    it = _create("перевёл 3 тона @someone за аккаунт с кошелька")
    assert it.category_ref == "Софт и AI" and it.counterparty == "@someone"


def test_credited_corrects_estimated_income() -> None:
    est = recent(kind="income", amount="100", currency="USD", category="Клиенты", tx_id=901)
    res = parse("пришло 2.6м", ctx(recent_actions=[est]))
    [it] = res.intents
    assert isinstance(it, UpdateTransactionIntent), it
    assert it.target.kind == "id" and it.target.transaction_id == 901
    assert str(it.changes.account_amount) == "2600000"


def test_credited_without_estimated_income_is_new_income() -> None:
    it = _create("пришло 2.6м")
    assert it.kind == "income"

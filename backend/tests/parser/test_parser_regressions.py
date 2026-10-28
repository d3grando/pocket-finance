"""Parser regression tests. Today = Tue 2026-10-27."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.schemas.intents import (
    ClarifyIntent,
    CreateTransactionIntent,
    CtxAccount,
    CtxMerchant,
    StatsIntent,
    TransferIntent,
    UpdateTransactionIntent,
)
from app.services.parser import parse
from app.services.parser.lexicon import BRAND_OF, BUILTIN_CATEGORIES
from app.services.parser.text import normalize

from .ctxs import ACCOUNTS, CAT_ID, CTX, MERCHANTS, ctx, recent
from .helpers import DEL, PC, SR, ST, E, I, check

OCT = PC("2026-10-01", "2026-10-31")


def _one(text: str, c=CTX):
    res = parse(text, c)
    assert len(res.intents) == 1, res.model_dump_json(exclude_defaults=True)
    return res, res.intents[0]


# ------------------------------------------------------------------ «нет, 250к» on $ / TON

CTX_USD_CARD = ctx(recent_actions=[recent(amount="15", currency="USD", category="Подписки",
                                          tx_id=674)])
CTX_TON_KB = ctx(recent_actions=[recent(amount="2", currency="TON", category="Еда", tx_id=688)])
_TON_KB = CTX_TON_KB.recent_actions[0].model_copy(update={"account": "Кошелёк"})
CTX_TON_KB = ctx(recent_actions=[_TON_KB])


@pytest.mark.parametrize("text,c", [
    ("нет, 250к", CTX_USD_CARD),
    ("исправь последнюю покупку на 150к", CTX_USD_CARD),
    ("нет, 1.5м", CTX_USD_CARD),
    ("нет, 250к", CTX_TON_KB),
])
def test_correction_k_m_on_foreign_currency_asks(text: str, c) -> None:
    res, it = _one(text, c)
    assert not res.needs_llm
    assert isinstance(it, ClarifyIntent) and it.reason == "ambiguous_currency"
    assert " ₫" in it.options[0]
    # every option is an explicit-currency reply that parses into an update
    for opt in it.options:
        r2 = parse(opt, c)
        assert isinstance(r2.intents[0], UpdateTransactionIntent), (opt, r2)


def test_correction_options_resolve_currency() -> None:
    _, it = _one("нет, 250к", CTX_USD_CARD)
    assert it.options[0] == "нет, 250к ₫"
    assert it.options[-1] == "нет, 250к $"
    assert "по факту списали 250к" in it.options  # Карта is a ₫ account: actual charge
    r = parse("нет, 250к ₫", CTX_USD_CARD).intents[0]
    assert r.changes.amount == Decimal("250000") and r.changes.currency == "VND"
    r = parse("нет, 250к $", CTX_USD_CARD).intents[0]
    assert r.changes.amount == Decimal("250000") and r.changes.currency == "USD"
    _, it = _one("нет, 250к", CTX_TON_KB)
    assert it.options[-1] == "нет, 250к TON"
    assert not any("списали" in o for o in it.options)  # Кошелёк is multi-asset


def test_correction_ratio_guard_and_normal_cases() -> None:
    _, it = _one("нет, 2000", CTX_USD_CARD)  # 133× of $15
    assert isinstance(it, ClarifyIntent)
    for text, amount in (("нет, 20", "20"), ("нет, 12.5", "12.5"), ("нет, 25$", "25")):
        res, it = _one(text, CTX_USD_CARD)
        assert isinstance(it, UpdateTransactionIntent) and not res.needs_llm
        assert it.changes.amount == Decimal(amount)
    # ₫ operations keep the habit «нет, 250к» → 250 000 ₫ (also big jumps: «аренда 12» → 12м)
    _, it = _one("нет, 12м", ctx(recent_actions=[recent(amount="12000", category="Жильё")]))
    assert isinstance(it, UpdateTransactionIntent) and it.changes.amount == Decimal("12000000")


# ------------------------------------------------------------------ «с карты руб»

@pytest.mark.parametrize("text", ["заплатил 10$ с карты руб", "такси 300 руб с карты руб",
                                  "подписка 10$ картой руб", "софт 10$ с карты rub"])
def test_multiword_alias_beats_generic_card(text: str) -> None:
    _, it = _one(text)
    assert it.account_ref == "Карта RUB"


def test_generic_card_still_bank() -> None:
    _, it = _one("кофе 85к с карты")
    assert it.account_ref == "Карта"


# ------------------------------------------------------------------ «списали 1.31м»

CTX_EST = ctx(recent_actions=[recent(amount="50", currency="USD", category="Серверы/инфра",
                                     tx_id=148)])


def test_charged_without_merchant_targets_estimated_op() -> None:
    res, it = _one("списали 1.31м", CTX_EST)
    assert isinstance(it, UpdateTransactionIntent) and not res.needs_llm
    assert it.target.kind == "id" and it.target.transaction_id == 148
    assert it.changes.account_amount == Decimal("1310000")
    # ₫ operation in context: nothing estimated → stays an ordinary phrase
    _, it = _one("списали 1.31м", ctx(recent_actions=[recent()]))
    assert isinstance(it, CreateTransactionIntent)


# ------------------------------------------------------------------ «продал …»

def test_sell_is_income() -> None:
    res, it = _one("продал наушники за 500к")
    assert isinstance(it, CreateTransactionIntent) and not res.needs_llm
    assert it.kind == "income" and it.category_ref == "Продажи"
    assert it.description == "наушники"
    assert parse("продал 100 usdt", CTX).needs_llm  # exchange without the received amount
    assert check(parse("продал 100 usdt за 2.6м", CTX),
                 [{"type": "transfer", "currency": "USDT", "counter_currency": "VND"}]) == []


# ------------------------------------------------------------------ dates

@pytest.mark.parametrize("text,opt", [("1 ноября аренда 12м", "01.11.2025 аренда 12м"),
                                      ("30.10 кофе 50к", "30.10.2025 кофе 50к"),
                                      ("кофе 50к 15.12", "кофе 50к 15.12.2025")])
def test_near_future_date_asks(text: str, opt: str) -> None:
    res, it = _one(text)
    assert isinstance(it, ClarifyIntent) and not res.needs_llm
    assert it.options[0] == opt
    assert it.options[1].startswith("сегодня") or it.options[1].endswith("сегодня")
    r = parse(it.options[0], CTX)
    assert r.intents[0].occurred_on.year == 2025


def test_far_date_is_last_year() -> None:
    _, it = _one("31.12 кофе 50к")
    assert it.occurred_on.isoformat() == "2025-12-31"


@pytest.mark.parametrize("text", ["на прошлой неделе такси 50к", "в прошлом месяце аренда 12м",
                                  "на этой неделе кофе 85к", "на днях такси 50к"])
def test_vague_period_asks(text: str) -> None:
    res, it = _one(text)
    assert isinstance(it, ClarifyIntent) and not res.needs_llm
    assert "какого числа" in it.question


def test_vague_period_multi_goes_to_llm() -> None:
    res = parse("на прошлой неделе такси 50к, кофе 30к", CTX)
    assert res.needs_llm and res.reason == "ambiguous_date"


@pytest.mark.parametrize("text,day", [
    ("сколько я потратил 26 октября", "2026-10-26"),
    ("покажи траты за 26 октября", "2026-10-26"),
    ("сколько потратил 1 октября", "2026-10-01"),
    ("сколько потратил 30 сентября", "2026-09-30"),
    ("траты за 26.10", "2026-10-26"),
    ("сколько потратил 1 ноября", "2025-11-01"),
])
def test_stats_single_day(text: str, day: str) -> None:
    assert check(parse(text, CTX), [ST("spent", PC(day, day))]) == []


def test_stats_month_still_month() -> None:
    assert check(parse("сколько потратил в октябре", CTX), [ST("spent", OCT)]) == []


# ------------------------------------------------------------------ account header

@pytest.mark.parametrize("text,acc", [
    ("налом: кофе 40к, такси 50к", "Наличные"),
    ("с кошелька: 10 usdt впн, 20 usdt gpt", "Кошелёк"),
    ("налом кофе 40к, такси 50к", "Наличные"),
])
def test_leading_account_applies_to_all(text: str, acc: str) -> None:
    res = parse(text, CTX)
    assert not res.needs_llm and len(res.intents) == 2
    assert [i.account_ref for i in res.intents] == [acc, acc]


def test_trailing_or_own_account_not_inherited() -> None:
    res = parse("кофе 85к налом, такси 45к", CTX)
    assert [i.account_ref for i in res.intents] == ["Наличные", None]
    res = parse("налом: кофе 40к, такси 50к с карты", CTX)
    assert [i.account_ref for i in res.intents] == ["Наличные", "Карта"]


# ------------------------------------------------------------------ brands → merchant rules

def test_brand_keywords_are_category_keywords() -> None:
    kws = {normalize(k) for _, _, _, _, ks in BUILTIN_CATEGORIES for k in ks}
    assert set(BRAND_OF) <= kws


CTX_NO_MERCH = ctx(merchants=[m for m in MERCHANTS if m.name not in ("vast.ai",
                                                                     "Highlands Coffee")])
# learned from a parser create (validator: name=merchant_ref, alias=norm(name)) + corrected
CTX_VAST_SOFT = ctx(merchants=[*CTX_NO_MERCH.merchants, CtxMerchant(
    id=9, name="vast.ai", aliases=["vast.ai"], category_id=CAT_ID[("Софт и AI", "expense")])])


@pytest.mark.parametrize("text,canon,cat", [
    ("васт 815$ с карты", "vast.ai", "Серверы/инфра"),
    ("заплатил васту 50$ с карты", "vast.ai", "Серверы/инфра"),
    ("хайлендс 65к", "Highlands Coffee", "Кофе"),
    ("grab 45к", "Grab", "Такси/транспорт"),
    ("hetzner 45 евро", "Hetzner", "Серверы/инфра"),
    ("openrouter 10$", "OpenRouter", "Софт и AI"),
])
def test_brand_gives_canonical_merchant(text: str, canon: str, cat: str) -> None:
    res, it = _one(text, CTX_NO_MERCH)
    assert not res.needs_llm
    assert it.merchant_ref == canon and it.category_ref == cat


def test_learned_brand_rule_wins_over_keyword() -> None:
    _, it = _one("васт 5$ с карты", CTX_VAST_SOFT)
    assert it.merchant_ref == "vast.ai" and it.category_ref == "Софт и AI"


def test_brand_charged_uses_canonical_when_learned() -> None:
    _, it = _one("васт списали 1.3м", CTX_VAST_SOFT)
    assert it.target.kind == "search" and it.target.search.merchant_ref == "vast.ai"
    assert it.target.search.limit == 1
    assert it.changes.account_amount == Decimal("1300000")
    # no rule yet: the typed word (text search over older operations)
    _, it = _one("васт списали 1.3м", CTX_NO_MERCH)
    assert it.target.search.merchant_ref == "васт"


def test_brand_search_and_delete_use_category_until_learned() -> None:
    assert check(parse("удали grab", CTX_NO_MERCH), [DEL("search", search={
        "category_refs": ["Такси/транспорт"], "merchant_ref": None})]) == []
    assert check(parse("удали васт", CTX_VAST_SOFT), [DEL("search", search={
        "category_refs": [], "merchant_ref": "vast.ai"})]) == []
    assert check(parse("найди васт", CTX_VAST_SOFT), [SR(merchant_ref="vast.ai",
                                                         category_refs=[])]) == []


def test_two_brands_no_merchant() -> None:
    _, it = _one("claude и cursor 40$", CTX_NO_MERCH)
    assert it.merchant_ref is None and it.category_ref == "Софт и AI"


# ------------------------------------------------------------------ small things

def test_small_parser_fixes() -> None:
    assert check(parse("покупка 15$", CTX), [E("15", "USD", "Покупки")]) == []
    assert check(parse("кофе 85 тыс. донгов", CTX), [E("85000", "VND", "Кофе")]) == []
    assert check(parse("кофе 85 тыс.", CTX), [E("85000", "VND", "Кофе")]) == []
    assert check(parse("500к за интернет на 3 месяца", CTX),
                 [E("500000", "VND", "Связь", description="интернет на 3 месяца")]) == []
    assert check(parse("кофе 85 тыс. такси 50к", CTX),
                 [E("85000", "VND", "Кофе"), E("50000", "VND", "Такси/транспорт")]) == []


def test_top_questions_are_top_stats() -> None:
    assert check(parse("топ 3 покупки за октябрь", CTX), [ST("top", OCT, limit=3)]) == []
    assert check(parse("самые дорогие покупки в октябре", CTX), [ST("top", OCT)]) == []
    _, it = _one("какие мои самые дорогие покупки?")
    assert isinstance(it, StatsIntent) and it.metric == "top" and it.limit is None
    # income / cheapest stay a search list
    assert check(parse("топ 5 доходов за месяц", CTX), [SR(kinds=["income"])]) == []


def test_delete_service_nouns() -> None:
    assert check(parse("удали все расходы на кофе за октябрь", CTX), [DEL("search", search={
        "kinds": ["expense"], "category_refs": ["Кофе"], "period": OCT, "text": None,
        "limit": 100})]) == []
    assert check(parse("удали все траты за вчера", CTX), [DEL("search", search={
        "kinds": ["expense"], "text": None, "limit": 100})]) == []


# ------------------------------------------------------------------ own @handles

_WALLET = CtxAccount(id=8, name="@myalt1", kind="crypto_wallet", is_multi_asset=True,
                     aliases=["@myalt1", "myalt1"], assets=["TON"])
_SHOP = CtxAccount(id=9, name="@myshop", kind="crypto_wallet", is_multi_asset=True,
                   aliases=["@myshop", "myshop"], assets=["TON"])
CTX_OWN = ctx(accounts=[*ACCOUNTS, _WALLET, _SHOP], my_handles=["myalt1", "myshop"])


@pytest.mark.parametrize("text,src,dst", [
    ("перевёл 1 тон @myalt1", None, "myalt1"),
    ("перевёл 3 тона @myshop с кошелька", "Кошелёк", "myshop"),
    ("перевёл 1 тон @myalt1 с кошелька", "Кошелёк", "myalt1"),
    ("перевёл 1 тон с кошелька на @myshop", "Кошелёк", "myshop"),
    ("3 тона @myshop с копилки", "Копилка", "myshop"),
])
def test_own_handle_is_recipient(text: str, src: str | None, dst: str) -> None:
    res, it = _one(text, CTX_OWN)
    assert not res.needs_llm, res.reason
    assert isinstance(it, TransferIntent)
    assert it.from_account_ref == src and it.to_handle == dst and it.to_account_ref is None


def test_foreign_handle_still_expense() -> None:
    _, it = _one("перевёл 3 тона @bob с кошелька", CTX_OWN)
    assert isinstance(it, CreateTransactionIntent) and it.counterparty == "@bob"


# ------------------------------------------------------------------ «без счёта»

def test_no_account_phrase() -> None:
    res, it = _one("подписка 7 usdt без счёта")
    assert not res.needs_llm
    assert it.account_none is True and it.account_ref is None and it.description is None
    assert check(parse("кофе 85к без счета", CTX), [E("85000", "VND", "Кофе",
                                                      account_none=True)]) == []
    assert parse("кофе 85к налом без счёта", CTX).needs_llm  # contradictory


def test_income_with_sell_and_brand() -> None:
    assert check(parse("получил 300 usdt от клиента", CTX),
                 [I("300", "USDT", "Клиенты", merchant_ref=None)]) == []

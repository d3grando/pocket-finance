"""Table cases: debts (docs/DEBTS.md §5.2). Today is Tue 2026-10-27. Format: (text, spec, ctx).

ctx «debts»: Коля (aliases коле, у коли) — должен мне VND; Петя — я должен VND; Вася @vasya —
должен мне USDT. ctx «seed»: no known debts. ctx «recent_debt»: the last action is a debt."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from app.schemas.intents import ActionBrief, CtxDebt, CtxDebtOpen

from .ctxs import NOW, ctx
from .helpers import CL, UPD, E, R

DEBTS = [
    CtxDebt(id=1, name="Коля", name_key="кол", aliases=["коле", "у коли"],
            open=[CtxDebtOpen(currency="VND", direction="owed_to_me")]),
    CtxDebt(id=2, name="Петя", name_key="пет",
            open=[CtxDebtOpen(currency="VND", direction="i_owe")]),
    CtxDebt(id=3, name="Вася", name_key="вас", handle="vasya",
            open=[CtxDebtOpen(currency="USDT", direction="owed_to_me")]),
    # owes only in ₽ — «Дима вернул 10к» must be a ₽ return, not a 10 000 ₫ income
    CtxDebt(id=12, name="Дима", name_key="дим",
            open=[CtxDebtOpen(currency="RUB", direction="owed_to_me")]),
]
NAMED = [  # names with digits / ё (created through REST, the web form) and a bad «Другу» row
    *DEBTS,
    CtxDebt(id=7, name="Агент 007", name_key="агент 007",
            open=[CtxDebtOpen(currency="VND", direction="owed_to_me")]),
    CtxDebt(id=8, name="Саша 2", name_key="саш 2",
            open=[CtxDebtOpen(currency="VND", direction="owed_to_me")]),
    CtxDebt(id=9, name="Bob2", name_key="bob2",
            open=[CtxDebtOpen(currency="VND", direction="owed_to_me")]),
    CtxDebt(id=10, name="Лёва", name_key="лев",
            open=[CtxDebtOpen(currency="VND", direction="i_owe")]),
    CtxDebt(id=11, name="Другу", name_key="друг",
            open=[CtxDebtOpen(currency="VND", direction="owed_to_me")]),
]
RECENT_DEBT = ActionBrief(
    action_id=uuid.uuid4(), kind="debt", summary="Долг: Коля +500 000 ₫", source="telegram",
    created_at=NOW - timedelta(minutes=1), transaction_ids=[201], tx_kind="transfer",
    amount=Decimal(500000), currency="VND", account="Карта")
CTXS = {"seed": ctx(), "debts": ctx(debts=DEBTS), "named": ctx(debts=NAMED),
        "recent_debt": ctx(debts=DEBTS, recent_actions=[RECENT_DEBT])}
D26 = "2026-10-26"


def DB(op: str, person: str, **kw: Any) -> dict:
    d = {"type": "debt", "op": op, "person": person}
    d.update(kw)
    return d


def Q(**kw: Any) -> dict:
    return {"type": "debts_query", **kw}


LLM_DEBT = {"llm": True, "reason": "debt", "partial": False, "n": 0}

LEND = [
    ("дал в долг Коле 500к", [DB("lend", "Коле", person_name="Коля", amount="500000",
                                 amount_raw="500к", currency="VND", fallback=None)], "debts"),
    ("дал Коле в долг 500к", [DB("lend", "Коле", person_name="Коля", amount="500000")], "debts"),
    ("одолжил Коле 300к налом", [DB("lend", "Коле", amount="300000",
                                    account_ref="Наличные")], "debts"),
    ("дал взаймы Маше 1м", [DB("lend", "Маше", person_name="Маша", amount="1000000")], "seed"),
    ("Коля взял в долг 200к", [DB("lend", "Коля", person_name="Коля", amount="200000")], "seed"),
    ("Коле дал в долг 500к", [DB("lend", "Коле", person_name="Коля")], "debts"),
    ("Коля занял у меня 500к", [DB("lend", "Коля", amount="500000")], "seed"),
    ("lent Bob 50$", [DB("lend", "Bob", person_name="Bob", amount="50", currency="USD")], "seed"),
    ("вчера дал в долг Коле 500к", [DB("lend", "Коле", occurred_on=D26)], "debts"),
    ("дал в долг Коле 500к без счёта", [DB("lend", "Коле", account_none=True,
                                           account_ref=None)], "debts"),
    ("дал в долг @vasya 50 usdt с кошелька", [DB("lend", "@vasya", person_name="Вася", amount="50",
                                           currency="USDT", account_ref="Кошелёк")], "debts"),
    ("дал в долг Ивану Петрову 2м", [DB("lend", "Ивану Петрову", person_name="Иван Петров",
                                        amount="2000000")], "seed"),
    ("дал в долг Коле 500к на ремонт", [DB("lend", "Коле", description="ремонт")], "debts"),
    ("дал в долг коле 500к", [DB("lend", "коле", person_name="Коля")], "debts"),
    ("одолжил Лене 300к", {"llm": True, "partial": True, "reason": "debt_name",
                           "i": [DB("lend", "Лене", person_name="Леня", amount="300000")]},
     "seed"),
]
REPAY_IN = [
    ("Коля вернул долг 200к", [DB("repay_in", "Коля", amount="200000", fallback=None)], "debts"),
    ("Коля вернул 200к долга", [DB("repay_in", "Коля", amount="200000")], "debts"),
    ("Коля отдал долг 100к", [DB("repay_in", "Коля", amount="100000")], "debts"),
    ("Коля погасил долг 300к", [DB("repay_in", "Коля", amount="300000")], "debts"),
    ("Коля вернул весь долг", [DB("repay_in", "Коля", amount=None, currency="VND")], "debts"),
    ("Коля рассчитался", [DB("repay_in", "Коля", amount=None, currency="VND")], "debts"),
    ("Коля вернул 200к", [DB("repay_in", "Коля", person_name="Коля", amount="200000",
                             fallback="income")], "debts"),
    ("коля вернул 200к", [DB("repay_in", "коля", fallback="income")], "debts"),
    ("Коля вернул мне 200к налом", [DB("repay_in", "Коля", fallback="income",
                                       account_ref="Наличные")], "debts"),
    ("@vasya вернул 20 usdt", [DB("repay_in", "@vasya", person_name="Вася", amount="20",
                                  currency="USDT", fallback="income")], "debts"),
    ("Коля перевёл мне 300к", [DB("repay_in", "Коля", amount="300000", fallback="income")],
     "debts"),
    ("Bob paid me back 50$", [DB("repay_in", "Bob", amount="50", currency="USD",
                                 fallback=None)], "seed"),
    ("Вася вернул весь долг", [DB("repay_in", "Вася", amount=None, currency="USDT")], "debts"),
    # no currency written and the person owes in one currency only → that currency
    ("Дима вернул 10к", [DB("repay_in", "Дима", amount="10000", currency="RUB",
                            fallback="income")], "debts"),
    ("Дима вернул долг 10к", [DB("repay_in", "Дима", amount="10000", currency="RUB")], "debts"),
    ("Вася вернул 20", [DB("repay_in", "Вася", amount="20", currency="USDT", fallback="income")],
     "debts"),
    # «100к» of a coin is implausible: the usual ₫ (the validator then notes «нет долга в ₫»)
    ("Вася вернул 100к", [DB("repay_in", "Вася", amount="100000", currency="VND",
                             fallback="income")], "debts"),
    # a currency named by the text or by the account wins
    ("Дима вернул 10к ₫", [DB("repay_in", "Дима", amount="10000", currency="VND")], "debts"),
    ("Дима вернул 10к на карту", [DB("repay_in", "Дима", amount="10000", currency="VND",
                                    account_ref="Карта")], "debts"),
    # a new loan keeps the usual default
    ("дал в долг Диме 10к", [DB("lend", "Диме", amount="10000", currency="VND")], "debts"),
    ("Коля вернул долг, 200к", [DB("repay_in", "Коля", amount="200000")], "debts"),
    ("вчера Коля вернул долг 100к", [DB("repay_in", "Коля", occurred_on=D26)], "debts"),
]
FALLBACK = [  # п.5 (DEBTS §15): always a DebtIntent with fallback — the validator converts it
    ("Петя вернул 200к", [DB("repay_in", "Петя", person_name="Петя", amount="200000",
                             fallback="income")], "debts"),
    ("Маша вернула 150к", [DB("repay_in", "Маша", person_name="Маша", fallback="income")],
     "seed"),
    ("Коля вернул 50 usdt", [DB("repay_in", "Коля", amount="50", currency="USDT",
                                fallback="income")], "debts"),
    ("отдал Коле 500к", [DB("repay_out", "Коле", person_name="Коля", fallback="expense")],
     "debts"),
    ("отдал Маше 200к", [DB("repay_out", "Маше", person_name="Маша", fallback="expense")],
     "seed"),
    ("Lena returned 20$", [DB("repay_in", "Lena", person_name="Lena", currency="USD",
                              fallback="income")], "seed"),
    ("отдал Пете 500к", {"llm": True, "partial": True, "reason": "debt_name",
                         "i": [DB("repay_out", "Пете", person_name="Петя",
                                  fallback="expense")]}, "seed"),
]
BORROW = [
    ("занял у Пети 1м", [DB("borrow", "у Пети", person_name="Петя", amount="1000000")], "seed"),
    ("взял в долг у Пети 2м", [DB("borrow", "у Пети", amount="2000000")], "seed"),
    ("одолжил у Маши 500к", [DB("borrow", "у Маши", person_name="Маша")], "seed"),
    ("Петя одолжил мне 1м", [DB("borrow", "Петя", person_name="Петя")], "seed"),
    ("Петя дал мне в долг 1м", [DB("borrow", "Петя", amount="1000000")], "seed"),
    ("borrowed 100 usdt from Alice", [DB("borrow", "Alice", person_name="Alice", amount="100",
                                         currency="USDT")], "seed"),
    ("занял у Андрея 300$", [DB("borrow", "у Андрея", person_name="Андрей", currency="USD")],
     "seed"),
    ("занял у Пети 50 usdt на кошелёк", [DB("borrow", "у Пети", currency="USDT",
                                       account_ref="Кошелёк")], "seed"),
]
REPAY_OUT = [
    ("вернул Пете долг 500к", [DB("repay_out", "Пете", person_name="Петя", fallback=None)],
     "debts"),
    ("вернул долг Пете 500к", [DB("repay_out", "Пете", amount="500000")], "debts"),
    ("отдал долг Пете 500к", [DB("repay_out", "Пете", amount="500000")], "debts"),
    ("погасил долг Пете 500к", [DB("repay_out", "Пете")], "debts"),
    ("отдал Пете 500к", [DB("repay_out", "Пете", person_name="Петя", fallback="expense")],
     "debts"),
    ("вернул Пете весь долг", [DB("repay_out", "Пете", amount=None, currency="VND")], "debts"),
    ("рассчитался с Петей", [DB("repay_out", "с Петей", person_name="Петя", amount=None)],
     "debts"),
    ("repaid Alice 100$", [DB("repay_out", "Alice", amount="100", currency="USD")], "seed"),
    ("заплатил долг Пете 100к", [DB("repay_out", "Пете", amount="100000")], "debts"),
]
FORGIVE = [
    ("простить долг Коле", [DB("forgive", "Коле", person_name="Коля", direction="owed_to_me",
                               amount=None, currency="VND")], "debts"),
    ("простил Коле долг", [DB("forgive", "Коле", direction="owed_to_me")], "debts"),
    ("списать долг Коли", [DB("forgive", "Коли", person_name="Коля",
                              direction="owed_to_me")], "debts"),
    ("Коля не вернёт", [DB("forgive", "Коля", direction="owed_to_me", amount=None)], "debts"),
    ("забудь долг Коли", [DB("forgive", "Коли", direction="owed_to_me")], "debts"),
    ("Петя простил мне долг", [DB("forgive", "Петя", direction="i_owe")], "debts"),
    ("списать мой долг Пете", [DB("forgive", "Пете", direction="i_owe")], "debts"),
    ("простить Коле 100к", [DB("forgive", "Коле", amount="100000", direction="owed_to_me")],
     "debts"),
]
CANONICAL = [  # bot / card buttons
    ("дал в долг: Коля 500к", [DB("lend", "Коля", person_name="Коля", amount="500000")],
     "seed"),
    ("занял: Петя 1м", [DB("borrow", "Петя", person_name="Петя", amount="1000000")], "seed"),
    ("возврат долга: Коля 200к", [DB("repay", "Коля", amount="200000", direction=None)],
     "debts"),
    ("возврат долга: Коля", [DB("repay", "Коля", amount=None, currency="VND")], "debts"),
    ("списать долг: Коля", [DB("forgive", "Коля", direction=None, amount=None)], "debts"),
    ("списать долг: Вася 50 USDT", [DB("forgive", "Вася", amount="50", currency="USDT",
                                       direction=None)], "debts"),
]
QUERIES = [
    ("кто мне должен?", [Q(direction="owed_to_me", person=None)], "debts"),
    ("кому я должен", [Q(direction="i_owe", person=None)], "debts"),
    ("мои долги", [Q(direction=None, person=None)], "debts"),
    ("долги", [Q(direction=None)], "debts"),
    ("/debts", [Q(direction=None)], "debts"),
    ("сколько должен Коля?", [Q(person="Коля", direction="owed_to_me")], "debts"),
    ("сколько я должен Пете?", [Q(person="Пете", direction="i_owe")], "debts"),
    ("какой долг у Коли?", [Q(person="у Коли")], "debts"),
    ("who owes me", [Q(direction="owed_to_me")], "seed"),
]
NOT_DEBTS = [
    ("вернули 200к за наушники", [R("200000", "VND", "Покупки")], "debts"),
    ("вернул 200к за наушники", [R("200000", "VND", "Покупки")], "debts"),
    ("Магазин вернул 200к", {"llm": True, "reason": "unknown_words",
                             "i": [R("200000", "VND")]}, "seed"),
    ("вчера мы сходили в ресторан, отдали 600к, потом ещё 150к на такси",
     [E("600000", "VND", "Еда", occurred_on=D26),
      E("150000", "VND", "Такси/транспорт", occurred_on=D26)], "debts"),
    ("перевёл маме 500к", {"llm": True, "reason": "transfer_target"}, "seed"),
    ("дал Коле 500к", {"llm": True, "reason": "unknown_words"}, "debts"),
    ("сколько я должен заплатить за аренду?", {"llm": True, "reason": "unknown_words"}, "seed"),
    ("одолжил другу 500к", LLM_DEBT, "seed"),
    ("кофе 85к и дал в долг Коле 500к", LLM_DEBT, "debts"),
    ("Коля не вернул 200к", LLM_DEBT, "debts"),
    ("отдал Dan 200к за ужин, он вернёт потом", LLM_DEBT, "seed"),
    ("Коля мне должен 500к", LLM_DEBT, "debts"),
    ("вернул долг 200к", LLM_DEBT, "debts"),
    ("дал в долг Коле 50 с кошелька", {"llm": True, "partial": True, "reason": "ambiguous_currency",
                                 "i": [CL("ambiguous_currency")]}, "debts"),
]
MULTI = [
    ("дал в долг Коле 500к и Пете 200к",
     [DB("lend", "Коле", person_name="Коля", amount="500000"),
      DB("lend", "Пете", person_name="Петя", amount="200000")], "debts"),
    ("Коля вернул 200к и Петя 100к",  # both п.5; the validator turns Петя's into income
     [DB("repay_in", "Коля", fallback="income"),
      DB("repay_in", "Петя", amount="100000", fallback="income")], "debts"),
    ("нет, 300к", [UPD(amount="300000")], "recent_debt"),
]

REGRESSIONS = [  # debts regressions
    # the account belongs to its own part; shared only as a header or «всё налом»
    ("Коля вернул 200к и 100к налом",
     [DB("repay_in", "Коля", amount="200000", account_ref=None),
      DB("repay_in", "Коля", amount="100000", account_ref="Наличные")], "debts"),
    ("Коля вернул 100к и ещё 50к налом",
     [DB("repay_in", "Коля", account_ref=None),
      DB("repay_in", "Коля", amount="50000", account_ref="Наличные")], "debts"),
    ("налом: Коля вернул 100к и 50к",
     [DB("repay_in", "Коля", account_ref="Наличные"),
      DB("repay_in", "Коля", amount="50000", account_ref="Наличные")], "debts"),
    ("Коля вернул 100к и 50к, всё налом",
     [DB("repay_in", "Коля", account_ref="Наличные"),
      DB("repay_in", "Коля", amount="50000", account_ref="Наличные")], "debts"),
    ("дал в долг Коле 50 usdt с кошелька и Пете 200к налом",
     [DB("lend", "Коле", account_ref="Кошелёк"), DB("lend", "Пете", account_ref="Наличные")],
     "debts"),
    # «за <что-то>»: a payment, not a repayment
    ("Петя отдал за ужин 300к", LLM_DEBT, "debts"),
    ("Петя отдал мне за ужин 300к", LLM_DEBT, "debts"),
    # «должок» is the word «долг»
    ("вернул Пете должок 100к", [DB("repay_out", "Пете", person_name="Петя", amount="100000",
                                    fallback=None)], "debts"),
    ("Коля вернул должок 100к", [DB("repay_in", "Коля", amount="100000", fallback=None)],
     "debts"),
    # «ё» survives in new names
    ("одолжил Алёше 100к", [DB("lend", "Алёше", person_name="Алёша")], "seed"),
    ("одолжил Серёже 2 тона с копилки", [DB("lend", "Серёже", person_name="Серёжа",
                                           currency="TON", account_ref="Копилка")], "seed"),
    ("Лёша вернул 100к", [DB("repay_in", "Лёша", person_name="Лёша", fallback="income")],
     "seed"),
    ("дал в долг: Тёма 100к", [DB("lend", "Тёма", person_name="Тёма")], "seed"),
    # button texts: a known name is read first, its digits are not the amount
    ("возврат долга: Агент 007", [DB("repay", "Агент 007", person_name="Агент 007",
                                     amount=None, currency="VND")], "named"),
    ("списать долг: Агент 007", [DB("forgive", "Агент 007", amount=None, direction=None)],
     "named"),
    ("возврат долга: Агент 007 5к", [DB("repay", "Агент 007", amount="5000")], "named"),
    ("возврат долга: Саша 2 5к", [DB("repay", "Саша 2", person_name="Саша 2",
                                     amount="5000")], "named"),
    ("списать долг: Bob2", [DB("forgive", "Bob2", person_name="Bob2", amount=None)], "named"),
    ("возврат долга: Лёва", [DB("repay", "Лёва", person_name="Лёва", amount=None)], "named"),
    # the excess buttons name the direction and the sum
    ("списать мой долг: Лёва 50к", [DB("forgive", "Лёва", amount="50000", currency="VND",
                                       direction="i_owe")], "named"),
    ("простить долг: Коля 50к", [DB("forgive", "Коля", amount="50000",
                                    direction="owed_to_me")], "debts"),
    ("списать мой долг: Вася 5 usdt", [DB("forgive", "Вася", amount="5", currency="USDT",
                                          direction="i_owe")], "debts"),
    # common nouns are not persons, not even a «Другу» saved by mistake earlier
    ("одолжил другу 200к", LLM_DEBT, "named"),
    ("одолжил коллеге 300к", LLM_DEBT, "debts"),
    ("дал в долг соседу 500к", LLM_DEBT, "debts"),
    ("возврат долга: Другу", [DB("repay", "Другу", amount=None)], "named"),
]

CASES = LEND + REPAY_IN + FALLBACK + BORROW + REPAY_OUT + FORGIVE + CANONICAL + QUERIES \
    + NOT_DEBTS + MULTI + REGRESSIONS

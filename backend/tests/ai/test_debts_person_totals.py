"""«сколько должен Коля?» — the person card's ``debts.totals`` are that person's
only (not every debt of the user); the whole-list card keeps the global totals."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.schemas.debts import DebtBalanceOut, DebtOut, DebtsOut
from app.services.ai import debt_cards as dc
from app.services.debts.totals import totals_of

D = Decimal


def _debt(i: int, name: str, amount: str, cur: str = "VND", vnd: str | None = None,
          usd: str | None = None) -> DebtOut:
    a = D(amount)
    return DebtOut(
        id=i, name=name, account_id=100 + i, version=1, created_at=datetime.now(UTC),
        status="owed_to_me" if a > 0 else "i_owe",
        balances=[DebtBalanceOut(currency=cur, amount=a,
                                 direction="owed_to_me" if a > 0 else "i_owe",
                                 approx_vnd=D(vnd) if vnd else None,
                                 approx_usd=D(usd) if usd else None)],
    )


def test_person_card_totals_are_the_persons_only():
    kolya = _debt(1, "Коля", "300000", vnd="300000", usd="11.48")
    vasya = _debt(2, "Вася", "50", "USDT", vnd="1300000", usd="50")
    petya = _debt(3, "Петя", "-1000000", vnd="1000000", usd="38.26")
    out = DebtsOut(owed_to_me=[vasya, kolya], i_owe=[petya],
                   totals=totals_of([vasya, kolya], [petya]))
    assert out.totals.owed_to_me_vnd == D(1600000) and out.totals.owed_to_me_count == 2

    card = dc.person_answer_card(out, 1, "Коля")
    assert card.debts is not None
    t = card.debts.totals
    assert (t.owed_to_me_vnd, t.owed_to_me_usd, t.owed_to_me_count) == (D(300000), D("11.48"), 1)
    assert (t.i_owe_vnd, t.i_owe_count) == (D(0), 0)
    assert card.headline == "Коля: долг тебе 300\u00a0000\u00a0₫"

    none = dc.person_answer_card(out, 9, "Зина")
    assert none.debts is not None and none.debts.totals.owed_to_me_count == 0

    full = dc.debts_answer_card(out)
    assert full.debts is not None and full.debts.totals.owed_to_me_vnd == D(1600000)


def test_totals_of_missing_rate():
    eth = _debt(4, "Лёша", "0.5", "ETH")
    t = totals_of([eth], [])
    assert t.missing_rates and t.owed_to_me_vnd is None and t.owed_to_me_count == 1

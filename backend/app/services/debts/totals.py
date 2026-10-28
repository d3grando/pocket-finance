"""Debt totals of DebtOut lists (pure, no DB): «Мне должны ≈ / Я должен ≈»."""

from __future__ import annotations

from decimal import Decimal

from app.core.money import quantize_asset
from app.schemas.debts import DebtOut, DebtTotalsOut

ZERO = Decimal(0)


def totals_of(owed: list[DebtOut], owe: list[DebtOut]) -> DebtTotalsOut:
    """«Мне должны ≈ / Я должен ≈» of the given lists (≈ ₫ / ≈ $ of their balances). Also used for
    one person's card (``ai/debt_cards.person_answer_card``), so its totals are that person's only.
    A side with no balance at all → 0; a side where no balance had a rate → None."""
    tot = {"ov": ZERO, "ou": ZERO, "iv": ZERO, "iu": ZERO}
    seen = {"ov": False, "ou": False, "iv": False, "iu": False}
    missing = False
    for lst, pre in ((owed, "o"), (owe, "i")):
        for o in lst:
            for b in o.balances:
                for suf, v in (("v", b.approx_vnd), ("u", b.approx_usd)):
                    if v is None:
                        missing = True
                    else:
                        tot[pre + suf] += abs(v)
                        seen[pre + suf] = True

    def total(key: str, n: int) -> Decimal | None:
        if not n:
            return ZERO
        return quantize_asset(tot[key], "VND" if key.endswith("v") else "USD") if seen[key] \
            else None

    return DebtTotalsOut(
        owed_to_me_vnd=total("ov", len(owed)),
        owed_to_me_usd=total("ou", len(owed)),
        i_owe_vnd=total("iv", len(owe)),
        i_owe_usd=total("iu", len(owe)),
        owed_to_me_count=len(owed),
        i_owe_count=len(owe),
        missing_rates=missing,
    )


__all__ = ["totals_of"]

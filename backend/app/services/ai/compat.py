"""Thin adapters from the assistant to other modules' functions (parser, ledger, debts)."""

from __future__ import annotations

import logging
import re
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.intents import ActionBrief, CtxDebt, ParseContext, ParseResult

log = logging.getLogger("pocket.ai")


def parse_amount(
    raw: str, currency: str | None, *, small_thousands: bool = True, allow_zero: bool = False
) -> Decimal:
    """parser ``parse_amount`` (exact Decimal)."""
    from app.services.parser import amounts

    return amounts.parse_amount(raw, currency, small_thousands=small_thousands,
                                allow_zero=allow_zero)


_CUR_TOKEN_RE = re.compile(r"[$€₽₫đ]|[^\W\d_]+", re.U)


def currencies_in_text(text: str) -> set[str]:
    """Currencies/assets the user actually named («$», «баксов», «usdt», «3 тона», «₫»)."""
    from app.services.parser import currencies

    out: set[str] = set()
    for tok in _CUR_TOKEN_RE.findall(text or ""):
        try:
            c = currencies.detect_currency(tok)
        except Exception:  # pragma: no cover - defensive
            c = None
        if c:
            out.add(c.upper())
    return out


def run_parser(text: str, ctx: ParseContext) -> tuple[ParseResult, str | None]:
    """Parser never blocks the LLM path: any crash → needs_llm with the error noted."""
    from app.services import parser

    try:
        pr = parser.parse(text, ctx)
        if not isinstance(pr, ParseResult):
            pr = ParseResult.model_validate(pr)
        return pr, None
    except Exception as e:  # pragma: no cover - defensive
        log.exception("parser crashed")
        return ParseResult(needs_llm=True, confidence=0, reason="parser_error"), repr(e)[:300]


async def recent_actions(session: AsyncSession, user_id: int) -> list[ActionBrief]:
    """ledger ``recent_actions`` (10 / 30 min, all clients)."""
    from app.services.ledger import actions

    return await actions.recent_actions(session, user_id, limit=10, minutes=30)


async def context_debts(session: AsyncSession, user_id: int) -> list[CtxDebt]:
    """debts service ``context_debts`` (names, aliases, open currencies + direction — NO
    amounts)."""
    from app.services.debts import service as debts

    return await debts.context_debts(session, user_id)

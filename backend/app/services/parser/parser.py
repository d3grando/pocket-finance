"""Main parser entry. Contract: docs/ARCH.md §5, app/schemas/intents.py.

Handles RU/EN simple phrases fast (< 2 ms): «кофе 85к», «вчера такси 70к», «еда 120к, такси 45к
и кофе 30к», «получил 300 USDT от клиента», «3 тона @bob с кошелька», «на карте 5.2м»,
«нет, 250к», «удали последний расход», «сколько потратил сегодня», aliases and learned merchants.
Vietnamese / mixed / complex → needs_llm=True. Never guesses an ambiguous currency.

Order: commands (undo / delete / edit / correction / «списали» / new category) → debts
(docs/DEBTS.md §5.2) → questions (stats / search) → edit-after-«Изменить» patch → operations.
Pure: no DB, no network.
"""

from __future__ import annotations

import hashlib
import logging

from app.schemas.intents import ParseContext, ParseResult
from app.services.parser.commands import try_commands, try_edit_context
from app.services.parser.debts import try_debts
from app.services.parser.questions import parse_question
from app.services.parser.refs import get_resolver
from app.services.parser.state import State, llm
from app.services.parser.text import clean_original, has_vietnamese, normalize, tokenize
from app.services.parser.transactions import parse_transactions

log = logging.getLogger(__name__)

MAX_LEN = 600


def redact(text: object) -> str:
    """Log-safe stand-in for a user message (financial text is personal data): length and a
    short hash, enough to match a report with the ai_requests row, never the text itself."""
    if not isinstance(text, str):
        return f"<{type(text).__name__}>"
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:8]
    return f"<text len={len(text)} sha256={digest}>"


def parse(text: str, ctx: ParseContext) -> ParseResult:
    """Rule-based parse of one user message. Never raises: on an internal error the message
    goes to the LLM (``needs_llm=True``, reason ``parser_error``)."""
    try:
        return _parse(text, ctx)
    except Exception:  # pragma: no cover - defensive: parser bugs must not break the pipeline
        log.exception("parser failed on %s", redact(text))
        return llm("parser_error", confidence=0.0, unparsed=text if isinstance(text, str)
                   else None)


def _parse(text: str, ctx: ParseContext) -> ParseResult:
    raw = (text or "").strip()
    if not raw:
        return ParseResult(intents=[], needs_llm=False, confidence=0.0, reason="empty")
    if len(raw) > MAX_LEN:
        return llm("too_long", confidence=0.2, unparsed=raw)
    orig = clean_original(raw)
    norm = normalize(raw)
    if has_vietnamese(norm):
        return llm("vietnamese", confidence=0.2, unparsed=raw)
    toks = tokenize(norm, orig)
    if not any(t.kind in ("word", "num", "handle") for t in toks):
        return llm("unknown", confidence=0.1, unparsed=raw)
    st = State(orig=orig, norm=norm, toks=toks, ctx=ctx, R=get_resolver(ctx))
    for handler in (try_commands, try_debts, parse_question, try_edit_context, parse_transactions):
        res = handler(st)
        if res is not None:
            return res
        st.reset()
    if not st.amounts:
        return llm("no_amount", confidence=0.2, unparsed=raw)
    return llm("unknown", confidence=0.2, unparsed=raw)

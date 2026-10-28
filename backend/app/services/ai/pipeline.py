"""Assistant entry point: parser → (needs_llm) → LLM → validate → dispatch.
docs/ARCH.md §5. Writes ai_requests; never lets an LLM error change data.

* The parser runs first (with merchants/aliases from the DB in the context). The LLM is called
  only when ``needs_llm`` (or the parser produced nothing).
* LLM failure (timeout, garbage JSON, invented ids, huge amounts, injection) → nothing written,
  honest card «AI недоступен, напиши проще: „еда 150к“». Parser intents with ``partial_ok`` are
  still executed when the LLM is down.
* All writes of one message run in one SAVEPOINT: any error → rolled back, the ai_requests row
  (no secrets) is kept with the error.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.principal import Principal
from app.models import AIRequest, PreviewToken
from app.schemas.assistant import AssistantMessageIn, AssistantResponse
from app.services.ai import cards, compat, guards
from app.services.ai.client import LLMResult, get_llm_client
from app.services.ai.context import build_parse_context
from app.services.ai.dispatcher import dispatch
from app.services.ai.validator import ValidationFailure, validate
from app.services.events import bus
from app.services.ledger import bulk as ledger_bulk

log = logging.getLogger("pocket.ai")

_ERROR_CODES = {
    "not_found": "not_found",
    "version_conflict": "conflict",
    "conflict": "conflict",
    "undo_conflict": "conflict",
    "already_exists": "conflict",
    "preview_expired": "preview_expired",
    "preview_stale": "preview_stale",
    "gone": "preview_expired",
}


def _jsonable(obj: Any) -> Any:
    try:
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
    except Exception:  # pragma: no cover - logging must never break the request
        return None
    return obj


_UNCLEAR = ("ambiguous_currency", "ambiguous_target", "missing_amount", "unknown", "other")


def _failure_response(fail: ValidationFailure, text: str) -> tuple[Any, str]:
    """ValidationFailure → (card, ai_requests.status). An LLM answer rejected by a safety check
    is shown with its REAL reason («Не нашёл эту сумму в тексте», «Слишком большая сумма») —
    the AI did answer, so «AI недоступен» would be a lie."""
    if fail.reason == "unsafe":
        if fail.card_reason in _UNCLEAR:
            return cards.unclear_card(fail.card_reason or "other", fail.message, fail.options,
                                      input_text=text), "invalid"
        return cards.error_card("validation_failed", "Не записал", fail.message,
                                input_text=text), "invalid"
    if fail.reason in _UNCLEAR:
        return cards.unclear_card(fail.reason, fail.message, fail.options, input_text=text), (
            "clarify"
        )
    if fail.reason == "not_found":
        return cards.error_card("not_found", fail.message, input_text=text), "invalid"
    return cards.error_card("validation_failed", "Не записал", fail.message, input_text=text), (
        "invalid"
    )


async def handle_message(
    session: AsyncSession, principal: Principal, req: AssistantMessageIn
) -> AssistantResponse:
    t0 = time.monotonic()
    text = req.text.strip()
    edit_id = req.context.edit_transaction_id if req.context else None
    ctx = await build_parse_context(session, principal, edit_transaction_id=edit_id)

    log_row = AIRequest(
        user_id=principal.user_id, source=principal.source, input_text=text[:2000],
        route="none", status="ok",
    )
    session.add(log_row)
    await session.flush()
    log_id = log_row.id  # captured: the row may be expired after a SAVEPOINT rollback

    def done(resp: AssistantResponse, status: str, error: str | None = None) -> AssistantResponse:
        log_row.status = status
        log_row.route = resp.route
        if error:
            log_row.error = error[:2000]
        log_row.latency_ms = int((time.monotonic() - t0) * 1000)
        resp.request_id = log_id
        return resp

    # 1. parser (pure, < 5 ms; CPU-bound: in a worker thread, so a huge dictionary of one user
    #    can never stall the event loop — every other request of every user)
    pr, parser_error = await asyncio.to_thread(compat.run_parser, text, ctx)
    log_row.parser_result = _jsonable(pr)
    intents = list(pr.intents)
    origin = "parser"
    route = "parser"
    ai_available = True
    llm: LLMResult | None = None

    # 2. LLM only when needed
    injection = False
    if pr.needs_llm or not intents:
        partial_safe = bool(pr.intents) and pr.partial_ok and not guards.partial_unsafe(
            text, pr.reason)
        if guards.looks_like_injection(text):
            # never sent to the model; the safe parser part (if any) still works
            injection = True
            if not partial_safe:
                card = cards.unclear_card(
                    "other", "Похоже на команду для AI, а не на запись. Перефразируйте, "
                    "например: «еда 150к»", [], input_text=text)
                resp = AssistantResponse(request_id=None, route="none", cards=[card],
                                         ai_available=True)
                return done(resp, "invalid", "prompt injection suspected; LLM not called")
        else:
            llm = await get_llm_client().extract_intents(text, ctx)
            log_row.model = (llm.model or "")[:64] or None
            log_row.llm_request = llm.request_log or None
            log_row.llm_response = llm.response_log or None
            log_row.prompt_tokens = llm.prompt_tokens
            log_row.completion_tokens = llm.completion_tokens
        if llm is not None and llm.ok and llm.intents is not None:
            intents = list(llm.intents.intents)
            origin = "llm"
            route = "parser_llm" if pr.intents else "llm"
            dropped = _repair_dropped(llm)
            if dropped:
                card = cards.unclear_card("other", dropped, [], input_text=text)
                resp = AssistantResponse(request_id=None, route=route,  # type: ignore[arg-type]
                                         cards=[card], ai_available=True)
                return done(resp, "clarify", f"repair dropped intents: {llm.repair_error}")
        elif injection:
            pass  # safe parser part only (checked above)
        elif partial_safe:
            ai_available = False  # execute the safe parser part, say AI is down for the rest
        else:
            assert llm is not None
            resp = AssistantResponse(request_id=None, route="llm" if not pr.intents else
                                     "parser_llm", cards=[cards.ai_unavailable_card(text)],
                                     ai_available=False)
            return done(resp, llm.status if llm.status != "ok" else "error",
                        llm.error or parser_error)

    # 3. validation (nothing is written if any intent fails)
    vres = await validate(session, intents, ctx, text=text,  # type: ignore[arg-type]
                          origin=origin, parse_result=pr)
    if isinstance(vres, ValidationFailure):
        card, status = _failure_response(vres, text)
        resp = AssistantResponse(request_id=None, route=route, cards=[card],  # type: ignore[arg-type]
                                 ai_available=ai_available)
        return done(resp, status, f"validation: {vres.reason}: {vres.message}")
    log_row.intents = [_jsonable(v.intent) for v in vres]

    # 4. dispatch — all-or-nothing
    try:
        async with session.begin_nested():
            resp = await dispatch(session, principal, vres, ctx, raw_text=text,
                                  ai_request_id=log_id, route=route)
    except AppError as e:
        code = _ERROR_CODES.get(e.code, "validation_failed")
        card = cards.error_card(code, "Не записал", e.message, input_text=text)
        resp = AssistantResponse(request_id=None, route=route, cards=[card],  # type: ignore[arg-type]
                                 ai_available=ai_available)
        return done(resp, "error", f"{e.code}: {e.message}")
    except ValidationError as e:
        card = cards.error_card("validation_failed", "Не записал",
                                "Проверка данных не прошла. Ничего не записано", input_text=text)
        resp = AssistantResponse(request_id=None, route=route, cards=[card],  # type: ignore[arg-type]
                                 ai_available=ai_available)
        return done(resp, "error", f"dispatch validation: {e}"[:2000])
    except Exception as e:
        log.exception("assistant dispatch failed")
        card = cards.error_card("internal", "Не удалось сохранить",
                                "Ничего не записано", input_text=text)
        resp = AssistantResponse(request_id=None, route=route, cards=[card],  # type: ignore[arg-type]
                                 ai_available=ai_available)
        return done(resp, "error", f"{type(e).__name__}: {e}"[:2000])

    if (not ai_available or injection) and resp.action_ids:
        # Part of the message IS recorded: no «Повторить» (it would re-send the whole text) and
        # the input is cleared — the rest is written again by hand / when the AI is back.
        rest = (pr.unparsed or "").strip()
        head = (f"Записал то, что понял. Не разобрал: «{rest[:120]}». " if rest
                else "Записал то, что понял, остальное допишите отдельно. ")
        if injection:
            resp.cards.append(cards.error_card(
                "validation_failed", "Записал не всё",
                head + "Часть текста похожа на команду для AI — её не выполняю",
                input_text=rest or None, retry=False, keep_input=False,
            ))
        else:
            resp.cards.append(cards.error_card(
                "ai_unavailable", cards.AI_UNAVAILABLE_TITLE, head + cards.AI_UNAVAILABLE_TEXT,
                input_text=rest or None, retry=False, keep_input=False,
            ))
    resp.ai_available = ai_available
    status = "clarify" if all(c.kind == "unclear" for c in resp.cards) else (
        "unavailable" if not ai_available else "invalid" if injection else "ok"
    )
    return done(resp, status, (llm.error if llm and not llm.ok else None) or (
        "prompt injection suspected; parser part only" if injection else None))


def _repair_dropped(llm: LLMResult) -> str | None:
    """The first LLM answer failed validation and the repaired one has FEWER intents: part of
    the message was silently dropped («еда 100к, кофе 0» → only «еда»). Nothing is written."""
    first = getattr(llm, "first_attempt_intents", None)
    if not llm.repair_error or not first or llm.intents is None:
        return None
    if len(llm.intents.intents) >= first:
        return None
    why = llm.repair_error.split(";")[0].split(": ", 1)[-1].strip()
    return ("Ничего не записал: часть сообщения не прошла проверку"
            + (f" ({why})" if why else "") + ". Исправьте и отправьте снова")


async def confirm_preview(
    session: AsyncSession, principal: Principal, token: uuid.UUID
) -> AssistantResponse:
    """Enter on a preview → AppliedCard; expired/stale/missing → ErrorCard (200)."""
    pt = await session.get(PreviewToken, token)
    if pt is None or pt.user_id != principal.user_id:
        card = cards.error_card("not_found", "Превью не найдено", "Повторите запрос")
        return AssistantResponse(request_id=None, route="none", cards=[card])
    kind, intent_json = pt.kind, dict(pt.intent or {})
    try:
        async with session.begin_nested():
            action_id, count = await ledger_bulk.confirm_preview(session, principal, token)
            changed = ["transactions", "accounts", "stats"]
            if (intent_json.get("changes") or {}).get("new_category"):
                changed.insert(2, "categories")
            await bus.publish(session, principal.user_id, changed, action_id=action_id,
                              source=principal.source)
    except AppError as e:
        code = _ERROR_CODES.get(e.code)
        if code is None:
            raise
        text = "Превью устарело — повторите запрос" if code.startswith("preview") else e.message
        card = cards.error_card(code, text)
        return AssistantResponse(request_id=None, route="none", cards=[card])
    card = cards.applied_card(action_id, count, kind)
    return AssistantResponse(request_id=None, route="none", cards=[card], action_ids=[action_id],
                             changed=changed)


async def cancel_preview(session: AsyncSession, principal: Principal, token: uuid.UUID) -> None:
    await ledger_bulk.cancel_preview(session, principal, token)

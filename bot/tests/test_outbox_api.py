"""Outbox delivery (claim → send → ack) and the HTTP client contract (headers, errors)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from pocket_bot.api import ApiError, PocketApi
from pocket_bot.outbox import deliver, payload_markup, process_once, run_outbox_loop
from tests.conftest import API_BASE, OWNER, SERVICE_TOKEN, FakeBackend, fx, kb_rows


async def test_outbox_process_once(bot, session, backend: FakeBackend, api, renderer):
    backend.on("POST", "/bot/outbox/claim", fx("outbox_claim"))
    backend.on("POST", "/bot/outbox/{id}/ack", {"ok": True})
    n = await process_once(bot, api, allowed_ids=[OWNER], renderer=renderer, limit=20)
    assert n == 3
    claim = backend.last("POST", "/bot/outbox/claim")
    assert claim.url.params["limit"] == "20"
    assert claim.headers["Authorization"] == f"Bearer {SERVICE_TOKEN}"
    assert "X-Telegram-User-Id" not in claim.headers  # bot-svc auth
    sent = session.of(SendMessage)
    assert len(sent) == 2  # the stranger's item is not delivered
    reminder, report = sent
    assert reminder.chat_id == OWNER and reminder.parse_mode == "HTML"
    assert kb_rows(reminder) == [[("✅ Записать", "r:2"), ("⏭ Пропустить", "k:2")],
                                 [("✏️ Другая сумма", "m:2")]]
    assert kb_rows(report) == [[("📊 Подробно", "https://pocket.test/analytics?period=last_month"),
                                ("📥 Разобрать", "i")]]
    acks = {r.url.path.split("/")[-2]: backend.body(r)
            for r in backend.find("POST", "/bot/outbox/") if r.url.path.endswith("/ack")}
    assert acks["11"] == {"ok": True, "error": None, "retry": True}
    assert acks["12"]["ok"] is True
    assert acks["13"]["ok"] is False and acks["13"]["retry"] is False


@pytest.mark.parametrize(
    ("exc", "retry"),
    [
        (lambda m: TelegramForbiddenError(m, "Forbidden: bot was blocked by the user"), False),
        (lambda m: TelegramRetryAfter(m, "Too Many Requests", retry_after=5), True),
        (lambda m: TelegramBadRequest(m, "Bad Request: chat not found"), False),
    ],
)
async def test_outbox_telegram_errors_ack(bot, session, backend, api, renderer, exc, retry):
    backend.on("POST", "/bot/outbox/{id}/ack", {"ok": True})
    session.fail(exc, on=SendMessage)
    item = fx("outbox_claim")["items"][0]
    ok = await deliver(bot, api, item, allowed_ids=[OWNER], renderer=renderer)
    assert ok is False
    body = backend.body(backend.last("POST", "/bot/outbox/11/ack"))
    assert body["ok"] is False and body["retry"] is retry and body["error"]


async def test_outbox_bad_html_is_sent_as_plain(bot, session, backend, api, renderer):
    backend.on("POST", "/bot/outbox/{id}/ack", {"ok": True})
    session.fail(lambda m: TelegramBadRequest(m, "Bad Request: can't parse entities"),
                 on=SendMessage)
    item = fx("outbox_claim")["items"][0]
    assert await deliver(bot, api, item, allowed_ids=[OWNER], renderer=renderer) is True
    last = session.of(SendMessage)[-1]
    assert last.parse_mode is None and "<b>" not in last.text and "Аренда" in last.text


async def test_outbox_loop_survives_backend_errors(bot, session, backend, api, renderer):
    calls = {"n": 0}
    stop = asyncio.Event()

    def claim(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": {"code": "internal", "message": "x"}})
        if calls["n"] == 2:
            return httpx.Response(200, json={"items": fx("outbox_claim")["items"][:1]})
        stop.set()
        return httpx.Response(200, json={"items": []})

    backend.routes.insert(0, ("POST", __import__("re").compile("^/bot/outbox/claim$"), claim))
    backend.on("POST", "/bot/outbox/{id}/ack", {"ok": True})
    await asyncio.wait_for(run_outbox_loop(bot, api, 0.01, allowed_ids=[OWNER],
                                           renderer=renderer, stop=stop), timeout=5)
    assert calls["n"] >= 3
    assert len(session.of(SendMessage)) == 1


def test_payload_markup_drops_invalid(renderer):
    kb = payload_markup([[{"text": "a", "callback": "x" * 70}, {"text": "", "callback": "r:1"},
                          {"text": "b", "callback": "o:unknown_key"}],
                         [{"text": "c", "callback": "o:/settings"}]], renderer)
    assert kb is not None
    assert [[(b.text, b.url) for b in r] for r in kb.inline_keyboard] == [
        [("c", "https://pocket.test/settings")]]
    assert payload_markup([], renderer) is None


# ------------------------------------------------------------------------ http client
async def test_api_error_parsing():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/messages"):
            return httpx.Response(409, json={"error": {"code": "idempotency_mismatch",
                                                       "message": "другое тело",
                                                       "details": {"a": 1},
                                                       "request_id": "rq-1"}})
        if req.url.path.endswith("/summary"):
            return httpx.Response(429, json={"error": {"code": "rate_limited", "message": "x"}},
                                  headers={"Retry-After": "12"})
        return httpx.Response(502, text="<html>bad gateway</html>")

    api = PocketApi(API_BASE, SERVICE_TOKEN, transport=httpx.MockTransport(handler))
    with pytest.raises(ApiError) as e:
        await api.assistant(OWNER, "x", idem_key="k")
    assert (e.value.status, e.value.code, e.value.message) == (409, "idempotency_mismatch",
                                                               "другое тело")
    assert e.value.details == {"a": 1} and e.value.request_id == "rq-1"
    with pytest.raises(ApiError) as e:
        await api.summary(OWNER)
    assert e.value.retry_after == 12
    with pytest.raises(ApiError) as e:
        await api.get_settings(OWNER)
    assert e.value.status == 502 and e.value.is_server
    await api.aclose()


async def test_api_request_shape(backend, api):
    backend.on("POST", "/assistant/messages", fx("created"))
    backend.on("POST", "/actions/{id}/undo", fx("undo_result"))
    backend.on("PATCH", "/transactions/{id}", fx("mutation_category"))
    await api.assistant(OWNER, "кофе 85", idem_key="tg:1:2", edit_transaction_id=7)
    r = backend.last("POST", "/assistant/messages")
    assert r.headers["Idempotency-Key"] == "tg:1:2"
    assert backend.body(r) == {"text": "кофе 85", "context": {"edit_transaction_id": 7}}
    await api.undo(OWNER, "6b0c0d3e-1111-4a2b-9c3d-000000000001", idem_key="tgcb:9")
    r = backend.last("POST", "/actions/")
    assert r.headers["Idempotency-Key"] == "tgcb:9"
    await api.patch_transaction(OWNER, 5, {"version": 1, "category_id": 3})
    r = backend.last("PATCH", "/transactions/5")
    assert "Idempotency-Key" not in r.headers
    assert all(c.headers["X-Telegram-User-Id"] == str(OWNER) for c in backend.calls)

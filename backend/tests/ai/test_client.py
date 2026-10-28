"""LLMClient against the fake OpenAI-compatible server: scenario and broken answers."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.schemas.intents import CreateTransactionIntent, ParseContext
from app.services.ai import client as ai_client
from app.services.ai.client import LLMClient, extract_json, parse_intents
from tests.ai.conftest import API_KEY
from tests.ai.fake_llm import Scenario

CTX = ParseContext(now=datetime(2026, 10, 27, 7, 0, tzinfo=UTC), today=date(2026, 10, 27))
OK = {"intents": [{"type": "create_transaction", "amount": "150000", "amount_raw": "150к",
                   "category_ref": "Еда"}]}


async def test_json_schema_success_and_no_secret_in_log(llm):
    llm.reply(OK)
    res = await ai_client.get_llm_client().extract_intents("еда 150к", CTX)
    assert res.ok and res.status == "ok"
    it = res.intents.intents[0]
    assert isinstance(it, CreateTransactionIntent) and it.amount == Decimal("150000")
    req = llm.requests[0]
    assert req["headers"]["authorization"] == f"Bearer {API_KEY}"
    assert req["body"]["response_format"]["type"] == "json_schema"
    assert req["body"]["temperature"] == 0
    assert "intents" in json.dumps(req["body"]["response_format"]["json_schema"]["schema"])
    assert "<<<USER>>>\nеда 150к\n<<<END>>>" in req["body"]["messages"][1]["content"]
    logged = json.dumps([res.request_log, res.response_log], ensure_ascii=False, default=str)
    assert API_KEY not in logged and "Bearer" not in logged
    assert res.prompt_tokens == 120 and res.completion_tokens == 30
    assert res.latency_ms is not None


async def test_fenced_and_prose_json(llm):
    llm.reply("Конечно! Вот ответ:\n```json\n" + json.dumps(OK) + "\n```\nУдачи")
    res = await ai_client.get_llm_client().extract_intents("еда 150к", CTX)
    assert res.ok, res.error


async def test_bare_list_and_float_amounts_are_accepted():
    il = parse_intents('[{"type": "create_transaction", "amount": 1.5, "amount_raw": "1.5$",'
                       ' "currency": "usd", "occurred_on": null}]')
    it = il.intents[0]
    assert it.amount == Decimal("1.5") and it.currency == "USD"
    with pytest.raises(ValueError):
        extract_json("no json here")


async def test_response_format_degrades_and_is_cached(llm):
    llm.push(Scenario(content=json.dumps(OK), reject_formats=("json_schema", "json_object")))
    c = ai_client.get_llm_client()
    res = await c.extract_intents("еда 150к", CTX)
    assert res.ok, res.error
    modes = [(r["body"].get("response_format") or {}).get("type") for r in llm.requests]
    assert modes == ["json_schema", "json_object", None]
    llm.reply(OK)
    res2 = await c.extract_intents("еда 150к", CTX)
    assert res2.ok
    assert "response_format" not in llm.requests[-1]["body"]  # remembered: no more 400s


async def test_repair_retry_after_invalid_json(llm):
    llm.reply("это не json вообще")
    llm.reply(OK)
    res = await ai_client.get_llm_client().extract_intents("еда 150к", CTX)
    assert res.ok and len(llm.requests) == 2
    repair = llm.requests[1]["body"]["messages"][-1]["content"]
    assert "не прошёл проверку" in repair


async def test_schema_violation_twice_is_invalid(llm):
    bad = {"intents": [{"type": "create_transaction", "amount": "-5"}]}
    llm.reply(bad)
    llm.reply({"intents": [{"type": "delete_everything"}]})
    res = await ai_client.get_llm_client().extract_intents("еда", CTX)
    assert not res.ok and res.status == "invalid" and res.intents is None
    assert len(llm.requests) == 2


async def test_timeout_is_reported_quickly(fake_llm_server):
    fake_llm_server.reset()
    fake_llm_server.push(Scenario(content=json.dumps(OK), delay=3))
    c = LLMClient(base_url=fake_llm_server.url, api_key="k", model="m", timeout=0.8, retries=1)
    t0 = time.monotonic()
    res = await c.extract_intents("еда 150к", CTX)
    assert not res.ok and res.status == "timeout"
    assert time.monotonic() - t0 < 2.5
    await c.aclose()


async def test_server_error_then_success_is_retried(llm):
    llm.push(Scenario(status=502))
    llm.reply(OK)
    res = await ai_client.get_llm_client().extract_intents("еда 150к", CTX)
    assert res.ok and len(llm.requests) == 2


async def test_auth_error_is_unavailable_without_retry(llm):
    llm.push(Scenario(status=401, body={"error": {"message": "bad key"}}))
    res = await ai_client.get_llm_client().extract_intents("еда 150к", CTX)
    assert not res.ok and res.status == "unavailable" and len(llm.requests) == 1


async def test_provider_error_payload_and_garbage_body(llm):
    llm.push(Scenario(body={"error": {"message": "overloaded"}}),
             Scenario(body={"choices": []}))
    res = await ai_client.get_llm_client().extract_intents("еда 150к", CTX)
    assert not res.ok and res.status in ("unavailable", "error")


async def test_not_configured_makes_no_request(llm):
    c = LLMClient(base_url=llm.url, api_key="", model="m")
    res = await c.extract_intents("еда", CTX)
    assert res.status == "unavailable" and llm.requests == []


async def test_concurrency_is_limited(fake_llm_server):
    fake_llm_server.reset()
    for _ in range(4):
        fake_llm_server.push(Scenario(content=json.dumps(OK), delay=0.3))
    c = LLMClient(base_url=fake_llm_server.url, api_key="k", model="m", timeout=5,
                  max_concurrency=2)
    res = await asyncio.gather(*[c.extract_intents("еда 150к", CTX) for _ in range(4)])
    assert all(r.ok for r in res)
    assert fake_llm_server.max_in_flight <= 2
    await c.aclose()

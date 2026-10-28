"""Regression tests (bot): a double tap on an option button must not create
a duplicate operation; a webhook is dropped before polling and a second polling instance stops
the bot with exit code 1; one failing outbox item / ack never cuts the batch; logs carry no
user data (no Telegram ids, no request URLs with the user's text)."""

from __future__ import annotations

import asyncio
import copy
import logging

import pytest
from aiogram.exceptions import TelegramConflictError
from aiogram.methods import DeleteWebhook, GetMe, GetUpdates, SendMessage
from aiogram.types import User

from pocket_bot import __main__ as entry
from pocket_bot.app import create_bot
from pocket_bot.config import BotSettings
from pocket_bot.outbox import process_once
from pocket_bot.polling import (
    CONFLICT_LIMIT,
    SECOND_INSTANCE,
    WEBHOOK_SET,
    PollingConflictGuard,
    prepare_polling,
)
from tests.conftest import OWNER, STRANGER, FakeBackend, callback_update, fx, message_update


# ------------------------------------------------------------------ double tap on «t:{n}»
async def test_option_double_tap_uses_one_idempotency_key(feed, backend: FakeBackend):
    backend.on("POST", "/assistant/messages", fx("unclear"))
    await feed(message_update("перевел вчера"))
    card = 9000  # the clarify card: MockedSession numbers sent messages from 9000
    backend.on("POST", "/assistant/messages", fx("created"))
    # a double tap = two callback queries (different ids) for the same card and button, in flight
    # at the same time — both reach the backend, with one key the second one is a replay
    first = callback_update("t:0", message_id=card, text="🤔 Не понял, что записать")
    second = callback_update("t:0", message_id=card, text="🤔 Не понял, что записать")
    assert first.callback_query.id != second.callback_query.id
    await asyncio.gather(feed(first), feed(second))
    posts = backend.find("POST", "/assistant/messages")[1:]  # [0] is the original message
    assert [p.headers["Idempotency-Key"] for p in posts] == [f"tgt:{OWNER}:{card}:0"] * 2
    # another option of the card is another action
    await feed(callback_update("t:1", message_id=card, text="🤔 Не понял, что записать"))
    assert backend.last("POST", "/assistant/messages").headers["Idempotency-Key"] == \
        f"tgt:{OWNER}:{card}:1"


# ------------------------------------------------------------------ polling hygiene
def _conflict(method):
    return TelegramConflictError(method, "Conflict: terminated by other getUpdates request")


async def test_conflict_guard_counts_consecutive_409_on_get_updates(bot, session):
    stopped: list[int] = []
    guard = PollingConflictGuard(lambda: stopped.append(1))
    bot.session.middleware(guard)
    # one 409 (the previous container's request during a restart) is tolerated, success resets
    session.fail(_conflict, on=GetUpdates)
    with pytest.raises(TelegramConflictError):
        await bot(GetUpdates(timeout=0))
    await bot(DeleteWebhook())  # other methods do not count
    assert guard.streak == 1 and not guard.conflict
    for _ in range(CONFLICT_LIMIT):
        session.fail(_conflict, on=GetUpdates)
        with pytest.raises(TelegramConflictError):
            await bot(GetUpdates(timeout=0))
    assert guard.conflict and stopped == [1]
    session.fail(_conflict, on=GetUpdates)
    with pytest.raises(TelegramConflictError):
        await bot(GetUpdates(timeout=0))
    assert stopped == [1]  # stop is requested once


def _entry_settings(**kw) -> BotSettings:
    base: dict[str, object] = {
        "telegram_bot_token": "42:TEST-TOKEN", "api_base_url": "http://api.test/api/v1",
        "bot_service_token": "svc", "telegram_allowed_ids": [OWNER, STRANGER],
        "public_url": "https://pocket.test"}
    base.update(kw)
    return BotSettings(_env_file=None, **base)


@pytest.fixture
def quiet_entry(monkeypatch, session):
    """amain() wired to the mocked Bot API session, no outbox network, no backoff sleeps."""
    async def idle_outbox(*a, **kw):
        await asyncio.Event().wait()

    async def no_sleep(self):
        await asyncio.sleep(0)

    plain = session.make_request

    async def make_request(bot, method, timeout=None):
        if isinstance(method, GetMe):  # polling starts with getMe
            session.requests.append(method)
            return User(id=42, is_bot=True, first_name="Pocket", username="pocket_test_bot")
        result = await plain(bot, method, timeout)  # raises the queued 409s
        if isinstance(method, GetUpdates):
            await asyncio.sleep(0.01)  # a (very short) long poll without updates
            return []
        return result

    monkeypatch.setattr(session, "make_request", make_request)
    monkeypatch.setattr(entry, "run_outbox_loop", idle_outbox)
    monkeypatch.setattr(entry, "create_bot", lambda s: create_bot(s, session=session))
    monkeypatch.setattr("aiogram.utils.backoff.Backoff.asleep", no_sleep)


async def test_second_instance_exits_with_code_1(monkeypatch, session, quiet_entry, caplog):
    monkeypatch.setattr(entry, "get_settings", lambda: _entry_settings())
    for _ in range(CONFLICT_LIMIT + 2):
        session.fail(_conflict, on=GetUpdates)
    caplog.set_level(logging.INFO)
    with pytest.raises(SystemExit) as exc:
        await asyncio.wait_for(entry.amain(), timeout=20)
    assert exc.value.code == 1
    # the webhook is removed (pending updates kept) before the first getUpdates
    kinds = [type(r) for r in session.requests if isinstance(r, DeleteWebhook | GetUpdates)]
    assert kinds[0] is DeleteWebhook
    (dw,) = session.of(DeleteWebhook)
    assert dw.drop_pending_updates is False
    crit = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert [r.getMessage() for r in crit] == [SECOND_INSTANCE]
    # the allowed ids are counted, never printed
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "2 allowed Telegram id(s)" in text
    assert str(OWNER) not in text and str(STRANGER) not in text


async def test_empty_allowed_ids_warns_and_libraries_are_quiet(monkeypatch, session, quiet_entry,
                                                              caplog):
    monkeypatch.setattr(entry, "get_settings", lambda: _entry_settings(telegram_allowed_ids=[]))
    for _ in range(CONFLICT_LIMIT):
        session.fail(_conflict, on=GetUpdates)
    caplog.set_level(logging.INFO)
    with pytest.raises(SystemExit):
        await asyncio.wait_for(entry.amain(), timeout=20)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
                and r.name == "pocket_bot"]
    assert entry.EMPTY_ALLOWED_IDS in warnings
    # httpx logs request URLs (the search text is in q=) — only warnings and errors stay
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("aiogram.event").getEffectiveLevel() >= logging.WARNING


# ------------------------------------------------------------------ outbox batch
async def test_failed_ack_of_a_foreign_item_does_not_cut_the_batch(bot, session, backend, api,
                                                                    renderer):
    items = copy.deepcopy(fx("outbox_claim")["items"])
    stranger = next(i for i in items if i["telegram_user_id"] != OWNER)
    owners = [i for i in items if i["telegram_user_id"] == OWNER]
    backend.on("POST", "/bot/outbox/claim", {"items": [stranger, *owners]})
    backend.on("POST", "/bot/outbox/{id}/ack", {"ok": True})
    backend.error("POST", f"/bot/outbox/{stranger['id']}/ack", 500, "internal", "boom")
    n = await process_once(bot, api, allowed_ids=[OWNER], renderer=renderer, limit=20)
    assert n == len(owners) + 1
    assert len(session.of(SendMessage)) == len(owners)  # everyone after the failed ack got theirs
    acked = {r.url.path.split("/")[-2] for r in backend.find("POST", "/bot/outbox/")
             if r.url.path.endswith("/ack")}
    assert {str(i["id"]) for i in owners} <= acked
    # the backend's error column does not get the foreign Telegram id either
    stranger_ack = next(r for r in backend.find("POST", f"/bot/outbox/{stranger['id']}/ack"))
    assert str(stranger["telegram_user_id"]) not in stranger_ack.content.decode()


async def test_a_broken_item_does_not_cut_the_batch(bot, session, backend, api, renderer):
    items = copy.deepcopy(fx("outbox_claim")["items"])
    owners = [i for i in items if i["telegram_user_id"] == OWNER]
    broken = {"kind": "report_monthly", "telegram_user_id": OWNER, "payload": {"text": "x"}}  # no id
    backend.on("POST", "/bot/outbox/claim", {"items": [owners[0], broken, *owners[1:]]})
    backend.on("POST", "/bot/outbox/{id}/ack", {"ok": True})
    n = await process_once(bot, api, allowed_ids=[OWNER], renderer=renderer, limit=20)
    assert n == len(owners) + 1
    assert len(session.of(SendMessage)) == len(owners)


async def test_webhook_left_on_the_token(bot, session, caplog):
    """deleteWebhook failing at start is not fatal; a webhook that stays is named in the log."""
    session.fail(lambda m: RuntimeError("network down"), on=DeleteWebhook)
    caplog.set_level(logging.WARNING)
    await prepare_polling(bot)  # no exception
    assert any("deleteWebhook failed" in r.getMessage() for r in caplog.records)
    guard = PollingConflictGuard(lambda: None)
    bot.session.middleware(guard)
    for _ in range(CONFLICT_LIMIT):
        session.fail(lambda m: TelegramConflictError(
            m, "Conflict: can't use getUpdates method while webhook is active"), on=GetUpdates)
        with pytest.raises(TelegramConflictError):
            await bot(GetUpdates(timeout=0))
    assert guard.conflict
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL] == [WEBHOOK_SET]

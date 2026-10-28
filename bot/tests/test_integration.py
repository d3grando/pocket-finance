"""Live run against a running backend (skipped unless PF_BACKEND_URL is set).

    PF_BACKEND_URL=http://127.0.0.1:8000/api/v1 PF_BOT_SERVICE_TOKEN=... uv run pytest \
        tests/test_integration.py

The backend needs an owner bound to the test Telegram id: start it with
TELEGRAM_ALLOWED_IDS=123456789 and run `cli create-owner` once (otherwise the debts flow is skipped).

Telegram stays mocked (MockedSession) — only the backend is real. The checks are tolerant to the
backend's development stage: every update must produce exactly one honest reply (a rendered card
or a short ⚠️ text), never a crash or silence; with a fully working backend the text input must
come back as a card and the outbox claim must answer 200.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

from pocket_bot.api import ApiError, PocketApi
from pocket_bot.app import build_dispatcher, create_bot
from pocket_bot.config import BotSettings
from pocket_bot.render import Renderer
from pocket_bot.stash import Stash
from tests.conftest import OWNER, TODAY, MockedSession, callback_update, message_update

URL = os.environ.get("PF_BACKEND_URL")
TOKEN = os.environ.get("PF_BOT_SERVICE_TOKEN") or os.environ.get("BOT_SERVICE_TOKEN", "")

pytestmark = pytest.mark.skipif(not URL, reason="PF_BACKEND_URL is not set")

# Leading glyphs of every honest bot reply: rendered cards (/last 🧾, /inbox 📥, /settings ⚙️, …)
# and short status texts (⚠️, ⏳, ⌛), plus the plain-text empty states (DESIGN.md copy) that a
# fresh database produces: no debts, no operations yet.
MARKERS = ("✅", "💸", "🤔", "⚠️", "📊", "🗓", "🔎", "🗑", "↩️", "🏦", "🟰", "🏷", "ℹ️", "⏳",
           "⌛", "🧾", "📥", "⚙️", "👤", "💰", "Долгов нет", "Пока ни одной операции")


@pytest.fixture
async def live():
    settings = BotSettings(_env_file=None, telegram_bot_token="42:LIVE-TEST", api_base_url=URL,
                           bot_service_token=TOKEN, telegram_allowed_ids=[OWNER],
                           public_url="https://pocket.test")
    api = PocketApi(URL, TOKEN, timeout=30)
    stash = Stash()
    dp = build_dispatcher(settings, api, stash=stash,
                          renderer=Renderer(settings.public_url, stash, today=lambda: TODAY),
                          catch_errors=False)
    session = MockedSession()
    bot = create_bot(settings, session=session)
    yield dp, bot, session, api
    await api.aclose()


async def test_backend_health():
    async with httpx.AsyncClient() as c:
        r = await c.get(URL.rstrip("/") + "/health", timeout=10)
    assert r.status_code == 200
    assert r.json().get("status") in ("ok", "degraded")


@pytest.mark.parametrize("text", ["/today", "/month", "/last", "/inbox", "/settings",
                                  "/report", "/debts", "кофе 85", "сколько потратил сегодня",
                                  "кто мне должен"])
async def test_every_input_gets_an_honest_reply(live, text):
    dp, bot, session, _ = live
    await dp.feed_update(bot, message_update(text))
    sent = session.of(SendMessage)
    assert sent, f"silence for {text!r}"
    assert all(m.text.strip() for m in sent)
    assert any(m.text.startswith(MARKERS) for m in sent), [m.text for m in sent]


async def test_outbox_claim_reachable(live):
    _, _, _, api = live
    try:
        items = await api.outbox_claim(limit=1)
    except ApiError as e:
        assert e.status in (401, 403, 501), e  # auth/outbox not implemented yet → still honest
        return
    assert isinstance(items, list)
    for it in items:  # give a claimed item back untouched (no delivery from tests)
        await api.outbox_ack(int(it["id"]), ok=False, error="integration probe", retry=True)


# ------------------------------------------------------------------------------ debts (live)
async def _debts_ready(api: PocketApi) -> str | None:
    """None when the debts flow can run, else the reason to skip it."""
    try:
        await api.list_debts(OWNER)
    except ApiError as e:
        if e.status == 501:  # debts service not deployed on this backend yet (stubs)
            return "debts are not implemented on this backend (501)"
        if e.status in (401, 403, 404):
            return (f"no owner bound to Telegram id {OWNER} on this backend ({e.status}): "
                    "set TELEGRAM_ALLOWED_IDS and run `cli create-owner`")
        raise
    return None


@pytest.mark.parametrize("data", ["dv:999999999", "dr:999999999", "dra:999999999",
                                  "dz:999999999"])
async def test_debt_buttons_answer_honestly(live, data):
    """Unknown person / stub backend: the button is answered with a short ⚠️ — as a toast
    (5xx: on_callback answers the query only) and/or a message (4xx)."""
    dp, bot, session, _ = live
    await dp.feed_update(bot, callback_update(data, message_id=700, text="⏰ напоминание"))
    toasts = [a.text or "" for a in session.of(AnswerCallbackQuery)]
    assert toasts, "callback not answered"
    texts = [m.text for m in session.requests if isinstance(m, SendMessage | EditMessageText)]
    assert texts or any(t.startswith("⚠️") for t in toasts), (data, toasts)
    assert all(t.startswith(MARKERS) for t in texts), texts


async def test_debts_full_flow(live):
    """With debts service deployed: lend → /debts → card → snooze → «Весь долг» → undo all."""
    dp, bot, session, api = live
    if (reason := await _debts_ready(api)) is not None:
        pytest.skip(reason)
    # letters only: digits inside a name would be read as part of the amount
    name = "Тест" + "".join(chr(ord("а") + int(ch, 16)) for ch in uuid.uuid4().hex[:6])
    # unique message ids per run: the bot's idempotency key embeds the message id
    # (tgdra:<user>:<message_id>), so a fixed id collides with a previous run's text (409)
    mid = 100_000 + uuid.uuid4().int % 1_000_000_000
    created = await api.assistant(OWNER, f"дал в долг: {name} 100к",
                                  idem_key=f"it:{uuid.uuid4().hex}")
    undo_ids = list(created.get("action_ids") or [])
    try:
        card = (created.get("cards") or [{}])[0]
        assert card.get("kind") == "created", card
        debt_id = int((card.get("debt") or {}).get("debt_id")
                      or ((card.get("transaction") or {}).get("debt") or {})["id"])
        await dp.feed_update(bot, message_update("/debts"))
        lst = session.of(SendMessage)[-1]
        assert lst.text.startswith("💸") and name in lst.text, lst.text
        assert any(b.callback_data == f"dv:{debt_id}"
                   for row in lst.reply_markup.inline_keyboard for b in row)
        await dp.feed_update(bot, callback_update(f"dv:{debt_id}"))
        assert session.of(SendMessage)[-1].text.startswith(f"👤 <b>{name}</b>")
        await dp.feed_update(bot, callback_update(f"dz:{debt_id}", message_id=mid,
                                                  text="⏰ напоминание"))
        assert "Напомню через 7 дней" in session.last_text()
        await dp.feed_update(bot, callback_update(f"dr:{debt_id}", message_id=mid + 1,
                                                  text="⏰ напоминание"))
        assert session.of(SendMessage)[-1].text.startswith(f"💰 Сколько вернул {name}?")
        await dp.feed_update(bot, callback_update(f"dra:{debt_id}", message_id=mid + 2,
                                                  text="💰 Сколько вернул"))
        assert "Возврат долга" in session.last_text(), session.last_text()
        detail = await api.get_debt(OWNER, debt_id, limit=5)
        assert not (detail.get("debt") or {}).get("balances"), detail
        for item in detail.get("history") or []:
            aid = (item.get("transaction") or {}).get("created_action_id")
            if aid and aid not in undo_ids:
                undo_ids.append(aid)
    finally:
        for aid in reversed(undo_ids):  # leave the test server as it was
            try:
                await api.undo(OWNER, str(aid), idem_key=f"it:{uuid.uuid4().hex}")
            except ApiError:
                pass

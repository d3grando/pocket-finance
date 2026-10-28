"""Test harness: mocked Telegram Bot API session (records every method call, never touches the
network), a fake backend (httpx.MockTransport serving contract fixtures generated from the real
backend schemas — see tests/contract/gen_fixtures.py) and update builders for
``Dispatcher.feed_update``. No polling, no real token."""

from __future__ import annotations

import copy
import itertools
import json
import os
import re
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
    TelegramMethod,
)
from aiogram.types import InlineKeyboardMarkup, Message, Update

from pocket_bot.api import PocketApi
from pocket_bot.app import build_dispatcher, create_bot
from pocket_bot.config import BotSettings
from pocket_bot.render import Renderer
from pocket_bot.stash import Stash

OWNER = 123456789
STRANGER = 111222333
# The bot defaults to "answer nobody"; settings built from the environment act for the owner.
os.environ.setdefault("TELEGRAM_ALLOWED_IDS", str(OWNER))
TODAY = date(2026, 10, 27)
SERVICE_TOKEN = "svc-test-token"
API_BASE = "http://api.test/api/v1"
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "contract.json").read_text())


def fx(name: str) -> Any:
    """Deep copy of a contract fixture (safe to mutate in a test)."""
    return copy.deepcopy(FIXTURES[name])


# ------------------------------------------------------------------------- Bot API mock
class MockedSession(BaseSession):
    """Records requests; answers SendMessage/Edit* with a Message, everything else with True.
    ``fail(factory)`` makes the next call raise ``factory(method)``."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self._ids = itertools.count(9000)
        self._fail: list[tuple[type | None, Callable[[TelegramMethod[Any]], Exception]]] = []

    def fail(self, factory: Callable[[TelegramMethod[Any]], Exception],
             on: type | None = None) -> None:
        """Make the next call (of type ``on``, if given) raise ``factory(method)``."""
        self._fail.append((on, factory))

    async def make_request(self, bot: Bot, method: TelegramMethod[Any],
                           timeout: int | None = None) -> Any:
        self.requests.append(method)
        for i, (on, factory) in enumerate(self._fail):
            if on is None or isinstance(method, on):
                self._fail.pop(i)
                raise factory(method)
        if isinstance(method, SendMessage | EditMessageText | EditMessageReplyMarkup):
            msg_id = getattr(method, "message_id", None) or next(self._ids)
            text = getattr(method, "text", None) or "…"
            data = {
                "message_id": msg_id,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": int(method.chat_id or OWNER), "type": "private"},
                "from": {"id": 42, "is_bot": True, "first_name": "Pocket"},
                "text": text,
            }
            m = Message.model_validate(data, context={"bot": bot})
            if isinstance(method.reply_markup, InlineKeyboardMarkup):
                m = m.model_copy(update={"reply_markup": method.reply_markup})
            return m
        return True

    async def close(self) -> None:  # pragma: no cover
        pass

    async def stream_content(self, *a: Any, **kw: Any):  # pragma: no cover
        yield b""

    # helpers ------------------------------------------------------------------
    def of(self, kind: type) -> list[Any]:
        return [r for r in self.requests if isinstance(r, kind)]

    @property
    def sent(self) -> list[SendMessage]:
        return self.of(SendMessage)

    @property
    def edits(self) -> list[EditMessageText]:
        return self.of(EditMessageText)

    def last_text(self) -> str:
        texts = [r for r in self.requests if isinstance(r, SendMessage | EditMessageText)]
        assert texts, "bot sent nothing"
        return texts[-1].text

    def clear(self) -> None:
        self.requests.clear()


def kb_rows(method: Any) -> list[list[tuple[str, str]]]:
    """[(text, callback_data or url)] rows of a SendMessage/Edit reply_markup."""
    rm = getattr(method, "reply_markup", None)
    if not isinstance(rm, InlineKeyboardMarkup):
        return []
    return [[(b.text, b.callback_data or b.url or "") for b in row] for row in rm.inline_keyboard]


def kb_flat(method: Any) -> list[tuple[str, str]]:
    return [b for row in kb_rows(method) for b in row]


# ------------------------------------------------------------------------- fake backend
Handler = Callable[[httpx.Request], httpx.Response]


class FakeBackend:
    """Route table: (METHOD, path-regex) → JSON / (status, JSON) / callable(request)."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, re.Pattern[str], Any]] = []
        self.calls: list[httpx.Request] = []
        self.unmocked: list[str] = []

    def on(self, method: str, path: str, response: Any = None, *, status: int = 200) -> None:
        pattern = re.compile("^" + re.sub(r"\{[a-z_]+\}", r"[^/]+", path) + "$")
        self.routes.insert(0, (method.upper(), pattern, (status, response)))

    def error(self, method: str, path: str, status: int, code: str, message: str = "ошибка",
              headers: dict[str, str] | None = None) -> None:
        body = {"error": {"code": code, "message": message, "details": {}, "request_id": "r1"}}
        pattern = re.compile("^" + re.sub(r"\{[a-z_]+\}", r"[^/]+", path) + "$")
        self.routes.insert(0, (method.upper(), pattern,
                               lambda req: httpx.Response(status, json=body,
                                                          headers=headers or {})))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path.removeprefix("/api/v1")
        for method, pattern, resp in self.routes:
            if method == request.method and pattern.match(path):
                if callable(resp):
                    return resp(request)
                status, body = resp
                return httpx.Response(status, json=body)
        self.unmocked.append(f"{request.method} {path}")
        return httpx.Response(418, json={"error": {"code": "unmocked", "message": path}})

    # helpers ------------------------------------------------------------------
    def find(self, method: str, path_prefix: str) -> list[httpx.Request]:
        return [c for c in self.calls if c.method == method
                and c.url.path.removeprefix("/api/v1").startswith(path_prefix)]

    def last(self, method: str, path_prefix: str) -> httpx.Request:
        found = self.find(method, path_prefix)
        assert found, f"no {method} {path_prefix} call; calls: {[str(c.url) for c in self.calls]}"
        return found[-1]

    @staticmethod
    def body(req: httpx.Request) -> Any:
        return json.loads(req.content) if req.content else None


# ------------------------------------------------------------------------- updates
_uid = itertools.count(1)


def message_update(text: str | None, *, user_id: int = OWNER, chat_type: str = "private",
                   message_id: int | None = None, chat_id: int | None = None,
                   edited: bool = False, sticker: bool = False) -> Update:
    mid = message_id or next(_uid) + 100
    msg: dict[str, Any] = {
        "message_id": mid,
        "date": int(datetime(2026, 10, 27, 7, 30, tzinfo=UTC).timestamp()),
        "chat": {"id": chat_id or user_id, "type": chat_type},
        "from": {"id": user_id, "is_bot": False, "first_name": "Owner"},
    }
    if text is not None:
        msg["text"] = text
        if text.startswith("/"):
            cmd = text.split()[0]
            msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(cmd)}]
    if sticker:
        msg["sticker"] = {"file_id": "x", "file_unique_id": "y", "type": "regular",
                          "width": 512, "height": 512, "is_animated": False, "is_video": False}
    if edited:
        msg["edit_date"] = msg["date"] + 5
        return Update.model_validate({"update_id": next(_uid), "edited_message": msg})
    return Update.model_validate({"update_id": next(_uid), "message": msg})


def callback_update(data: str, *, user_id: int = OWNER, message_id: int = 555,
                    text: str = "✅ 150 000 ₫ · Еда",
                    reply_markup: dict[str, Any] | None = None,
                    reply_to_text: str | None = None) -> Update:
    msg: dict[str, Any] = {
        "message_id": message_id,
        "date": int(datetime(2026, 10, 27, 7, 31, tzinfo=UTC).timestamp()),
        "chat": {"id": user_id, "type": "private"},
        "from": {"id": 42, "is_bot": True, "first_name": "Pocket"},
        "text": text,
    }
    if reply_markup:
        msg["reply_markup"] = reply_markup
    if reply_to_text:
        msg["reply_to_message"] = {
            "message_id": message_id - 1, "date": msg["date"] - 5,
            "chat": msg["chat"], "from": {"id": user_id, "is_bot": False, "first_name": "O"},
            "text": reply_to_text}
    cq = {"id": f"cq{next(_uid)}", "from": {"id": user_id, "is_bot": False, "first_name": "O"},
          "chat_instance": "ci", "data": data, "message": msg}
    return Update.model_validate({"update_id": next(_uid), "callback_query": cq})


# ------------------------------------------------------------------------- fixtures
@pytest.fixture
def settings() -> BotSettings:
    return BotSettings(_env_file=None, telegram_bot_token="42:TEST-TOKEN",
                       api_base_url=API_BASE, bot_service_token=SERVICE_TOKEN,
                       telegram_allowed_ids=[OWNER], public_url="https://pocket.test")


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
async def api(backend: FakeBackend) -> AsyncGenerator[PocketApi]:
    client = PocketApi(API_BASE, SERVICE_TOKEN, timeout=5,
                       transport=httpx.MockTransport(backend.handle))
    yield client
    await client.aclose()


@pytest.fixture
def session() -> MockedSession:
    return MockedSession()


@pytest.fixture
def bot(settings: BotSettings, session: MockedSession) -> Bot:
    return create_bot(settings, session=session)


@pytest.fixture
def stash() -> Stash:
    return Stash()


@pytest.fixture
def renderer(stash: Stash) -> Renderer:
    return Renderer("https://pocket.test", stash, today=lambda: TODAY)


@pytest.fixture
def dp(settings: BotSettings, api: PocketApi, stash: Stash, renderer: Renderer) -> Dispatcher:
    return build_dispatcher(settings, api, stash=stash, renderer=renderer, catch_errors=False)


@pytest.fixture
def feed(dp: Dispatcher, bot: Bot, backend: FakeBackend):
    async def _feed(update: Update) -> None:
        await dp.feed_update(bot, update)

    yield _feed
    assert not backend.unmocked, f"unmocked backend calls: {backend.unmocked}"

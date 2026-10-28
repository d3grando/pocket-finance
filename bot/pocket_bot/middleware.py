"""Owner-only gate: updates from anyone outside TELEGRAM_ALLOWED_IDS (or from non-private
chats) are dropped silently — the bot does not reveal that it is alive (DESIGN.md §12.9)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Chat, TelegramObject, User

log = logging.getLogger(__name__)


class OwnerOnlyMiddleware(BaseMiddleware):
    def __init__(self, allowed_ids: Iterable[int]) -> None:
        self.allowed = frozenset(int(x) for x in allowed_ids)

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user: User | None = data.get("event_from_user")
        chat: Chat | None = data.get("event_chat")
        if user is None or user.id not in self.allowed:
            log.info("dropped update from foreign user %s", getattr(user, "id", None))
            return None
        if chat is not None and chat.type != "private":
            log.info("dropped update from non-private chat %s", chat.id)
            return None
        return await handler(event, data)

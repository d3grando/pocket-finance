"""Long-polling hygiene: one bot token = one polling process, logs without user data.

* A webhook left on the token (another deployment, a test) makes getUpdates fail forever —
  ``prepare_polling`` drops it first, keeping the pending updates.
* Two processes polling the same token steal each other's updates (Telegram answers 409 Conflict
  to the older getUpdates). aiogram retries such errors forever, so ``PollingConflictGuard`` (a
  Bot API request middleware) counts consecutive 409s on getUpdates and stops polling after
  ``CONFLICT_LIMIT`` of them; ``__main__`` then exits with code 1. A single 409 (the previous
  container's request still open during a restart) is tolerated.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import TelegramConflictError
from aiogram.methods import GetUpdates, TelegramMethod
from aiogram.methods.base import Response

log = logging.getLogger("pocket_bot")

CONFLICT_LIMIT = 3
SECOND_INSTANCE = "another bot instance is polling with this TELEGRAM_BOT_TOKEN — stopping"
WEBHOOK_SET = "a webhook is set on this TELEGRAM_BOT_TOKEN and could not be removed — stopping"


class PollingConflictGuard(BaseRequestMiddleware):
    def __init__(self, on_conflict: Callable[[], None], limit: int = CONFLICT_LIMIT) -> None:
        self.on_conflict = on_conflict
        self.limit = limit
        self.streak = 0
        self.conflict = False

    async def __call__(self, make_request: NextRequestMiddlewareType[Any], bot: Bot,
                       method: TelegramMethod[Any]) -> Response[Any]:
        if not isinstance(method, GetUpdates):
            return await make_request(bot, method)
        try:
            response = await make_request(bot, method)
        except TelegramConflictError as e:
            self.streak += 1
            if self.streak >= self.limit and not self.conflict:
                self.conflict = True
                # «terminated by other getUpdates request» vs «can't use getUpdates … webhook is active»
                log.critical(WEBHOOK_SET if "webhook" in str(e.message).lower() else SECOND_INSTANCE)
                self.on_conflict()
            raise
        self.streak = 0
        return response


async def prepare_polling(bot: Bot) -> None:
    """Polling and a webhook exclude each other: remove a webhook, keep the pending updates.
    Telegram unreachable at start is not fatal (polling retries); a webhook that really stays
    makes getUpdates answer 409, and PollingConflictGuard stops the bot with a clear message."""
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as e:  # noqa: BLE001 — network / Telegram error: polling decides
        log.warning("deleteWebhook failed: %s", type(e).__name__)


def quiet_library_logs() -> None:
    """httpx logs every request URL at INFO (search queries carry the user's text in ``q=``);
    aiogram.event logs every handled update at INFO. Keep only warnings and errors."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

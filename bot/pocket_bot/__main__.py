"""Entry: ``python -m pocket_bot`` — long polling + outbox loop.
Never run with the real token in tests (tests feed updates into the dispatcher directly)."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from pocket_bot.api import PocketApi
from pocket_bot.app import build_dispatcher, create_bot, set_commands
from pocket_bot.config import get_settings
from pocket_bot.outbox import run_outbox_loop
from pocket_bot.polling import PollingConflictGuard, prepare_polling, quiet_library_logs

log = logging.getLogger("pocket_bot")

EMPTY_ALLOWED_IDS = "TELEGRAM_ALLOWED_IDS is empty: the bot answers nobody"


async def amain() -> None:
    s = get_settings()
    logging.basicConfig(level=s.log_level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    quiet_library_logs()
    if not s.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    if not s.bot_service_token:
        raise SystemExit("BOT_SERVICE_TOKEN is not set")
    if not s.telegram_allowed_ids:
        log.warning(EMPTY_ALLOWED_IDS)
    bot = create_bot(s)
    api = PocketApi(s.api_base_url, s.bot_service_token, timeout=s.api_timeout)
    dp = build_dispatcher(s, api)
    renderer = dp["renderer"]
    stopping: set[asyncio.Task[None]] = set()
    guard = PollingConflictGuard(lambda: stopping.add(asyncio.create_task(dp.stop_polling())))
    bot.session.middleware(guard)
    outbox: asyncio.Task[None] | None = None
    try:
        await prepare_polling(bot)
        try:
            await set_commands(bot)
        except Exception:  # menu is cosmetic
            log.warning("setMyCommands failed", exc_info=True)
        outbox = asyncio.create_task(
            run_outbox_loop(bot, api, s.outbox_poll_seconds, allowed_ids=s.telegram_allowed_ids,
                            renderer=renderer, limit=s.outbox_batch),
            name="outbox")
        log.info("starting long polling (%d allowed Telegram id(s))", len(s.telegram_allowed_ids))
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if outbox is not None:
            outbox.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await outbox
        for t in stopping:
            with contextlib.suppress(Exception):
                await t
        await api.aclose()
        await bot.session.close()
    if guard.conflict:
        raise SystemExit(1)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()

"""Composition root: Bot + Dispatcher wiring shared by ``__main__`` and the tests."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from pocket_bot.api import PocketApi
from pocket_bot.config import BotSettings
from pocket_bot.handlers import on_error, setup_routers
from pocket_bot.middleware import OwnerOnlyMiddleware
from pocket_bot.render import Renderer
from pocket_bot.stash import Stash
from pocket_bot.views import COMMANDS


def create_bot(settings: BotSettings, session: BaseSession | None = None) -> Bot:
    return Bot(token=settings.telegram_bot_token, session=session,
               default=DefaultBotProperties(parse_mode="HTML"))


def make_today(tz_name: str):
    tz = ZoneInfo(tz_name)

    def today() -> date:
        return datetime.now(tz).date()

    return today


def build_dispatcher(settings: BotSettings, api: PocketApi, *, stash: Stash | None = None,
                     renderer: Renderer | None = None, catch_errors: bool = True) -> Dispatcher:
    if stash is None:  # NB: an empty Stash is falsy (__len__) — never use `stash or ...`
        stash = renderer.stash if renderer is not None else Stash()
    if renderer is None:
        renderer = Renderer(settings.public_url, stash, today=make_today(settings.timezone))
    dp = Dispatcher(storage=MemoryStorage(), api=api, renderer=renderer, stash=stash,
                    settings=settings)
    dp.update.outer_middleware(OwnerOnlyMiddleware(settings.telegram_allowed_ids))
    dp.include_router(setup_routers())
    if catch_errors:
        dp.errors.register(on_error)
    return dp


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])

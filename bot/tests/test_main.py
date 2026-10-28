"""Entry-point wiring (without polling): settings guard, command menu, dispatcher data."""

from __future__ import annotations

import pytest
from aiogram.methods import SetMyCommands

from pocket_bot import __main__ as entry
from pocket_bot.app import build_dispatcher, set_commands
from pocket_bot.config import BotSettings


async def test_amain_refuses_without_tokens(monkeypatch):
    monkeypatch.setattr(entry, "get_settings",
                        lambda: BotSettings(_env_file=None, telegram_bot_token=""))
    with pytest.raises(SystemExit):
        await entry.amain()
    monkeypatch.setattr(entry, "get_settings",
                        lambda: BotSettings(_env_file=None, telegram_bot_token="42:X",
                                            bot_service_token=""))
    with pytest.raises(SystemExit):
        await entry.amain()


async def test_set_commands_menu(bot, session):
    await set_commands(bot)
    (req,) = session.of(SetMyCommands)
    names = [c.command for c in req.commands]
    assert names[:3] == ["today", "month", "report"]
    assert {"inbox", "last", "debts", "undo", "settings", "help"} <= set(names)


def test_dispatcher_exposes_renderer_and_update_types(settings, api):
    dp = build_dispatcher(settings, api)
    assert dp["renderer"].public_url == "https://pocket.test"
    assert dp["stash"] is dp["renderer"].stash
    assert set(dp.resolve_used_update_types()) >= {"message", "edited_message", "callback_query"}

"""Send/edit helpers: HTML parse mode, link previews off, graceful fallbacks
(«message is not modified», broken HTML → plain text, uneditable message → new message)."""

from __future__ import annotations

import html
import logging
import re

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions, Message

log = logging.getLogger(__name__)

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
_TAG = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    return html.unescape(_TAG.sub("", text))


def _is_parse_error(e: TelegramBadRequest) -> bool:
    s = str(e).lower()
    return "can't parse entities" in s or "unsupported start tag" in s or "can't find end" in s


async def safe_send(bot: Bot, chat_id: int, text: str,
                    reply_markup: InlineKeyboardMarkup | None = None) -> Message:
    try:
        return await bot.send_message(chat_id, text, reply_markup=reply_markup,
                                      parse_mode="HTML", link_preview_options=NO_PREVIEW)
    except TelegramBadRequest as e:
        if not _is_parse_error(e):
            raise
        log.warning("HTML rejected by Telegram (%s) — sending as plain text", e)
        return await bot.send_message(chat_id, strip_html(text), reply_markup=reply_markup,
                                      parse_mode=None, link_preview_options=NO_PREVIEW)


async def safe_edit(msg: Message, text: str,
                    reply_markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await msg.edit_text(text, reply_markup=reply_markup, parse_mode="HTML",
                            link_preview_options=NO_PREVIEW)
    except TelegramBadRequest as e:
        s = str(e).lower()
        if "message is not modified" in s:
            return
        if _is_parse_error(e):
            await msg.edit_text(strip_html(text), reply_markup=reply_markup, parse_mode=None,
                                link_preview_options=NO_PREVIEW)
            return
        log.info("edit failed (%s) — sending a new message", e)
        await safe_send(msg.bot, msg.chat.id, text, reply_markup)


async def safe_edit_markup(msg: Message, reply_markup: InlineKeyboardMarkup | None) -> None:
    """Swap only the keyboard; «not modified» and uneditable messages are not errors."""
    try:
        await msg.edit_reply_markup(reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            log.info("keyboard edit failed: %s", e)

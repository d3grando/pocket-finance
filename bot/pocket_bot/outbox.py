"""Outbox poller: claim → send → ack, every OUTBOX_POLL_SECONDS.

Backend contract (API.md §13): ``POST /bot/outbox/claim?limit=N`` → items
``{id, kind, telegram_user_id, payload: {text, parse_mode, buttons: [[{text, callback}]]}}``;
``POST /bot/outbox/{id}/ack {ok, error, retry}``. Monthly report, recurring reminders
([✅ Записать] r:{id} / [⏭ Пропустить] k:{id} / [✏️ Другая сумма] m:{id}), daily/weekly reports,
debt reminders ([Записать возврат] dr:{debt} / [Напомнить позже] dz:{debt}, DEBTS.md §9).
Callbacks ``o:{route_key}`` become URL buttons to PUBLIC_URL.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pocket_bot import callbacks as cb
from pocket_bot.api import ApiError, PocketApi
from pocket_bot.render import Renderer
from pocket_bot.sending import safe_send

log = logging.getLogger(__name__)

MAX_BACKOFF = 60.0


def payload_markup(buttons: Any, renderer: Renderer | None) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    for row in buttons or []:
        out: list[InlineKeyboardButton] = []
        for b in row or []:
            text = str((b or {}).get("text") or "").strip()
            data = str((b or {}).get("callback") or "")
            if not text or not data:
                continue
            if data.startswith("o:"):
                route = cb.route_for_key(data[2:])
                url = renderer.url(route) if (renderer and route) else None
                if url:
                    out.append(InlineKeyboardButton(text=text, url=url))
                continue
            if len(data.encode()) > cb.MAX_LEN:
                log.warning("outbox button callback too long, dropped: %r", data)
                continue
            out.append(InlineKeyboardButton(text=text, callback_data=data))
        if out:
            rows.append(out)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def _ack(api: PocketApi, item_id: int, *, ok: bool, error: str | None, retry: bool) -> None:
    """Ack never raises: a failed ack leaves the item claimed (the backend re-offers it later)
    and must not stop the rest of the batch."""
    try:
        await api.outbox_ack(item_id, ok=ok, error=error, retry=retry)
    except ApiError as e:
        log.error("outbox ack %s failed: %s", item_id, e)


async def deliver(bot: Bot, api: PocketApi, item: dict[str, Any], *, allowed_ids: Iterable[int],
                  renderer: Renderer | None) -> bool:
    """Send one outbox item and ack it. Returns True when Telegram accepted the message."""
    item_id = int(item["id"])
    uid = int(item.get("telegram_user_id") or 0)
    if uid not in set(allowed_ids):
        await _ack(api, item_id, ok=False, error="telegram_user_id is not in TELEGRAM_ALLOWED_IDS",
                   retry=False)
        return False
    payload = item.get("payload") or {}
    text = str(payload.get("text") or "").strip()
    if not text:
        await _ack(api, item_id, ok=False, error="empty text", retry=False)
        return False
    kb = payload_markup(payload.get("buttons"), renderer)
    ok, error, retry = False, None, True
    try:
        await safe_send(bot, uid, text, kb)
        ok = True
    except TelegramRetryAfter as e:
        error, retry = f"telegram retry_after={e.retry_after}", True
    except TelegramForbiddenError as e:
        error, retry = f"forbidden: {e.message}", False
    except TelegramBadRequest as e:
        error, retry = f"bad request: {e.message}", False
    except (TelegramNetworkError, TelegramServerError) as e:
        error, retry = f"network: {e}", True
    except Exception as e:  # never kill the loop; backend decides on retries
        log.exception("outbox send failed")
        error, retry = f"{type(e).__name__}: {e}", True
    await _ack(api, item_id, ok=ok, error=error, retry=retry)
    if not ok:
        log.warning("outbox item %s not delivered: %s (retry=%s)", item_id, error, retry)
    return ok


async def process_once(bot: Bot, api: PocketApi, *, allowed_ids: Iterable[int],
                       renderer: Renderer | None, limit: int = 20) -> int:
    items = await api.outbox_claim(limit=limit)
    ids = list(allowed_ids)
    for item in items:
        # one broken item (bad shape, unexpected error) must not cut the rest of the batch
        try:
            await deliver(bot, api, item, allowed_ids=ids, renderer=renderer)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("outbox item %s skipped", (item or {}).get("id") if isinstance(item, dict) else "?")
    return len(items)


async def run_outbox_loop(bot: Bot, api: PocketApi, interval: float, *,
                          allowed_ids: Iterable[int] = (), renderer: Renderer | None = None,
                          limit: int = 20, stop: asyncio.Event | None = None) -> None:
    ids = list(allowed_ids)
    delay = interval
    while stop is None or not stop.is_set():
        try:
            n = await process_once(bot, api, allowed_ids=ids, renderer=renderer, limit=limit)
            delay = interval
            if n >= limit:
                continue  # backlog: drain without sleeping
        except ApiError as e:
            delay = min(max(delay, interval) * 2, MAX_BACKOFF)
            log.warning("outbox claim failed: %s (next try in %.0fs)", e, delay)
        except asyncio.CancelledError:
            raise
        except Exception:
            delay = min(max(delay, interval) * 2, MAX_BACKOFF)
            log.exception("outbox loop error (next try in %.0fs)", delay)
        if stop is not None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
        else:
            await asyncio.sleep(delay)

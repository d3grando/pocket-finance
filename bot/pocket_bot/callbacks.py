"""Inline callback_data codec (≤ 64 bytes). Contract table: docs/API.md §13.

Backend-defined prefixes (also used by outbox payloads):
  u:{action_hex}            undo / redo → POST /actions/{uuid}/undo
  e:{tx}                    «Изменить» → next message edits tx (context.edit_transaction_id)
  a:{tx}:{account|0}        «С какого?» → POST /transactions/{tx}/account (0 = «Без счёта»)
  l:{tx}                    «Позже» → drop keyboard
  c:{tx}                    «В другую» → category picker (GET /categories/suggest)
  s:{tx}:{category}         set category → PATCH /transactions/{tx}
  p:{token_hex} / x:{hex}   confirm / cancel preview
  t:{n}                     send_text option n of the last card (stored in FSM)
  r:{rid} / k:{rid}         recurring «Записать» / «Пропустить»
  o:{route_key}             «Открыть в Pocket» — rendered as a URL button, never a callback
  dr:{debt} / dra:{debt}    debts «Записать возврат» (wait for the amount) / «Весь долг»
  dz:{debt}                 debt reminder «Напомнить позже» → POST /debts/{id}/snooze {days:7}
  dv:{debt}                 person card → GET /debts/{id} (docs/DEBTS.md §9, §11)

Bot-local prefixes (never sent by the backend; documented in bot/README.md):
  m:{rid}                   recurring «Другая сумма» → wait for amount text
  n:{tx}                    category picker «Другая…» → wait for category name
  b:{tx}                    category picker «‹ Назад» → restore the previous keyboard
  y:{msg_id}:{s|n}          «Повторить» (s = same Idempotency-Key, n = new key)
  U:{key}                   «Отменить все» (several action ids stashed under key)
  M:{key}                   created_many «Изменить» → per-operation edit buttons
  g:{m|d|w|b}               /settings: toggle monthly/daily/weekly report, b = debt reminders
  i                         show «Разобрать» (/inbox)
  z                         cancel the pending «Изменить»/«Другая сумма» prompt
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

MAX_LEN = 64

#: route keys for o:{route_key} → web routes (ARCH §13, API.md §13)
ROUTE_KEYS: dict[str, str] = {
    "home": "/",
    "ops": "/operations",
    "inbox": "/operations?status=needs_account",
    "accounts": "/accounts",
    "settings": "/settings",
    "analytics": "/analytics",
}
PERIOD_PRESETS = {
    "today", "yesterday", "this_week", "last_week", "this_month", "last_month",
    "this_year", "last_year", "last_7_days", "last_30_days", "all_time",
}


@dataclass(frozen=True, slots=True)
class Cb:
    op: str
    args: tuple[str, ...] = ()

    def int_arg(self, i: int) -> int:
        return int(self.args[i])


def _hex(value: str | uuid.UUID) -> str:
    return uuid.UUID(str(value)).hex


def hex_id(value: str | uuid.UUID) -> str:
    """UUID (any form) → 32-char hex used inside callback_data."""
    return _hex(value)


def uuid_from_hex(value: str) -> str:
    return str(uuid.UUID(hex=value))


def pack(op: str, *args: object) -> str:
    data = ":".join([op, *(str(a) for a in args)])
    if len(data.encode()) > MAX_LEN:
        raise ValueError(f"callback_data too long: {data!r}")
    return data


def undo(action_id: str | uuid.UUID) -> str:
    return pack("u", _hex(action_id))


def edit(tx_id: int) -> str:
    return pack("e", int(tx_id))


def choose_account(tx_id: int, account_id: int | None) -> str:
    return pack("a", int(tx_id), int(account_id) if account_id else 0)


def later(tx_id: int) -> str:
    return pack("l", int(tx_id))


def change_category(tx_id: int) -> str:
    return pack("c", int(tx_id))


def set_category(tx_id: int, category_id: int) -> str:
    return pack("s", int(tx_id), int(category_id))


def confirm_preview(token: str | uuid.UUID) -> str:
    return pack("p", _hex(token))


def cancel_preview(token: str | uuid.UUID) -> str:
    return pack("x", _hex(token))


def send_text(n: int) -> str:
    return pack("t", int(n))


def recurring_record(rid: int) -> str:
    return pack("r", int(rid))


def recurring_skip(rid: int) -> str:
    return pack("k", int(rid))


def debt_view(debt_id: int) -> str:
    return pack("dv", int(debt_id))


def debt_repay(debt_id: int) -> str:
    return pack("dr", int(debt_id))


def debt_repay_all(debt_id: int) -> str:
    return pack("dra", int(debt_id))


def debt_snooze(debt_id: int) -> str:
    return pack("dz", int(debt_id))


def recurring_amount(rid: int) -> str:
    return pack("m", int(rid))


def category_other(tx_id: int) -> str:
    return pack("n", int(tx_id))


def category_back(tx_id: int) -> str:
    return pack("b", int(tx_id))


def retry(msg_id: int, same_key: bool) -> str:
    return pack("y", int(msg_id), "s" if same_key else "n")


def undo_many(key: str) -> str:
    return pack("U", key)


def edit_many(key: str) -> str:
    return pack("M", key)


def settings_toggle(field: str) -> str:
    return pack("g", field)


INBOX = "i"
CANCEL_PROMPT = "z"


def parse(data: str | None) -> Cb | None:
    """Split callback_data; returns None for empty/garbage (handler answers «устарела»)."""
    if not data or len(data.encode()) > MAX_LEN:
        return None
    op, *args = data.split(":")
    if not op:
        return None
    return Cb(op=op, args=tuple(args))


def route_for_key(route_key: str) -> str | None:
    """o:{route_key} → web route. Accepts a raw route ('/analytics?period=today'), a key from
    ROUTE_KEYS, or 'analytics_<preset>' / 'analytics:<preset>'."""
    key = route_key.strip()
    if not key:
        return None
    if key.startswith("/"):
        return key
    if key in ROUTE_KEYS:
        return ROUTE_KEYS[key]
    for sep in ("_", ":"):
        prefix = "analytics" + sep
        if key.startswith(prefix) and key[len(prefix):] in PERIOD_PRESETS:
            return f"/analytics?period={key[len(prefix):]}"
    return None

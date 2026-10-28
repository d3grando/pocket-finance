"""aiogram routers: owner-only filter, text → assistant, callbacks → API.

Contract: docs/API.md §13 (callback table, idempotency keys), DESIGN.md §12 (texts),
docs/DEBTS.md §9, §11 (/debts, dv:/dr:/dra:/dz:; views in ``debts.py``).
Injected via Dispatcher workflow data: ``api`` (PocketApi), ``renderer`` (Renderer),
``stash`` (Stash), ``settings`` (BotSettings).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from pocket_bot import callbacks as cb
from pocket_bot import debts as debts_v
from pocket_bot import views
from pocket_bot.api import ApiError, PocketApi
from pocket_bot.config import BotSettings
from pocket_bot.fmt import esc, human_date, ops_word
from pocket_bot.render import (
    Rendered,
    Renderer,
    action_is_redo,
    markup,
    undo_summary_parts,
)
from pocket_bot.sending import safe_edit, safe_edit_markup, safe_send
from pocket_bot.stash import Stash

log = logging.getLogger(__name__)

PROMPT_TTL = 600.0  # «Изменить» waits for the next message 10 minutes (API.md §13)
RETRY_TEXTS_KEEP = 20
OPTIONS_KEEP = 20  # send_text options remembered per message (t:{n} of an older card)
RECORDED_KINDS = frozenset({"created", "created_many", "needs_account"})

Btn = InlineKeyboardButton


class Prompt(StatesGroup):
    edit = State()  # data: edit_tx — next text edits this transaction
    category = State()  # data: cat_tx — next text is a category name
    amount = State()  # data: rec_id, rec_msg — next text is the amount for a recurring payment
    debt = State()  # data: debt_id, debt_name, debt_src, debt_prompt — «Сколько вернул Коля?»


CANCEL_KB = markup([[Btn(text="✖️ Отмена", callback_data=cb.CANCEL_PROMPT)]])


# ------------------------------------------------------------------------------ helpers
def _msg_key(message: Message) -> str:
    return f"tg:{message.chat.id}:{message.message_id}"


async def _store_options(state: FSMContext, msg_id: int | None, options: list[str]) -> None:
    """send_text texts of a card: ``options`` (the last card, legacy) + ``msg_options[msg_id]``
    so t:{n} under an older card (a person card, a clarify) still sends its own text."""
    if not options:
        return
    upd: dict[str, Any] = {"options": options}
    if msg_id is not None:
        by_msg: dict[str, Any] = dict((await state.get_data()).get("msg_options") or {})
        by_msg[str(msg_id)] = options
        if len(by_msg) > OPTIONS_KEEP:
            for k in sorted(by_msg, key=int)[:-OPTIONS_KEEP]:
                by_msg.pop(k, None)
        upd["msg_options"] = by_msg
    await state.update_data(**upd)


async def _send(message: Message, rd: Rendered, state: FSMContext | None = None) -> Message | None:
    sent = await safe_send(message.bot, message.chat.id, rd.text, rd.markup)
    if rd.options and state is not None:
        await _store_options(state, sent.message_id if sent else None, rd.options)
    return sent


async def _strip_markup(bot: Bot, chat_id: int, msg_id: Any) -> None:
    """Drop the keyboard of an older message (a consumed prompt / an answered reminder)."""
    if not msg_id:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=int(msg_id),
                                            reply_markup=None)
    except TelegramBadRequest:
        pass


def _recorded(resp: dict[str, Any] | None) -> bool:
    return any((c or {}).get("kind") in RECORDED_KINDS for c in (resp or {}).get("cards") or [])


async def _typing(message: Message) -> None:
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
    except TelegramAPIError as e:  # cosmetic only
        log.debug("typing action failed: %s", e)


async def _clear_prompt(state: FSMContext) -> None:
    if await state.get_state() is not None:
        await state.set_state(None)
    await state.update_data(edit_tx=None, cat_tx=None, rec_id=None, rec_msg=None,
                            rec_due=None, debt_id=None, debt_name=None, debt_src=None,
                            debt_prompt=None, prompt_until=None)


async def _remember_text(state: FSMContext, msg_id: int, text: str,
                         edit_tx: int | None) -> None:
    data = await state.get_data()
    texts: dict[str, Any] = dict(data.get("texts") or {})
    texts[str(msg_id)] = {"text": text, "edit": edit_tx}
    if len(texts) > RETRY_TEXTS_KEEP:
        for k in sorted(texts, key=int)[:-RETRY_TEXTS_KEEP]:
            texts.pop(k, None)
    await state.update_data(texts=texts)


async def _send_error(message: Message, err: ApiError, *, mutation: bool,
                      retry_msg_id: int | None = None) -> None:
    kb = None
    if retry_msg_id is not None and (err.is_server or err.status == 429
                                     or err.code == "idempotency_in_progress"):
        kb = views.retry_markup(retry_msg_id, same_key=True)
    await safe_send(message.bot, message.chat.id, views.api_error_text(err, mutation=mutation),
                    kb)


async def run_assistant(message: Message, state: FSMContext, api: PocketApi,
                        renderer: Renderer, text: str, *,
                        edit_tx: int | None = None) -> dict[str, Any] | None:
    """Text → POST /assistant/messages (key tg:{chat}:{msg}) → cards as messages.
    Returns the AssistantResponse (None when the request failed — the error is already sent)."""
    uid = message.from_user.id
    await _remember_text(state, message.message_id, text, edit_tx)
    await _typing(message)
    try:
        resp = await api.assistant(uid, text, idem_key=_msg_key(message),
                                   edit_transaction_id=edit_tx)
    except ApiError as e:
        await _send_error(message, e, mutation=True, retry_msg_id=message.message_id)
        return None
    await send_response(message, state, renderer, resp, msg_id=message.message_id)
    return resp


async def send_response(message: Message, state: FSMContext, renderer: Renderer,
                        resp: dict[str, Any], *, msg_id: int | None,
                        edit_first: Message | None = None) -> None:
    rendered = renderer.render_response(resp, msg_id=msg_id)
    for i, rd in enumerate(rendered):
        if i == 0 and edit_first is not None:
            await safe_edit(edit_first, rd.text, rd.markup)
            sent_id: int | None = edit_first.message_id
        else:
            sent = await safe_send(message.bot, message.chat.id, rd.text, rd.markup)
            sent_id = sent.message_id if sent else None
        if rd.options:
            await _store_options(state, sent_id, rd.options)


# ------------------------------------------------------------------------------ commands
async def cmd_start(message: Message, state: FSMContext) -> None:
    await _clear_prompt(state)
    await safe_send(message.bot, message.chat.id, views.START_TEXT, None)


async def cmd_help(message: Message, state: FSMContext) -> None:
    await _clear_prompt(state)
    await safe_send(message.bot, message.chat.id, views.HELP_TEXT, None)


async def _stats(message: Message, state: FSMContext, api: PocketApi, renderer: Renderer,
                 preset: str) -> None:
    await _clear_prompt(state)
    uid = message.from_user.id
    try:
        summary = await api.summary(uid, "VND")
    except ApiError as e:
        await _send_error(message, e, mutation=False)
        return
    try:
        by_cat = await api.by_category(uid, preset, kind="expense", currency="VND")
    except ApiError as e:
        log.warning("by-category failed: %s", e)
        by_cat = None
    view = views.today_view if preset == "today" else views.month_view
    await _send(message, view(renderer, summary, by_cat))


async def cmd_today(message: Message, state: FSMContext, api: PocketApi,
                    renderer: Renderer) -> None:
    await _stats(message, state, api, renderer, "today")


async def cmd_month(message: Message, state: FSMContext, api: PocketApi,
                    renderer: Renderer) -> None:
    await _stats(message, state, api, renderer, "this_month")


def report_args(arg: str | None, today: date) -> tuple[str, date, str]:
    """/report [прошлый|неделя|день] → (kind, on, analytics preset)."""
    a = (arg or "").strip().lower()
    if a in ("прошлый", "прошлого", "прошлый месяц", "prev", "last", "previous"):
        return "monthly", today.replace(day=1) - timedelta(days=1), "last_month"
    if a in ("неделя", "неделю", "week", "weekly", "нед"):
        return "weekly", today, "this_week"
    if a in ("день", "сегодня", "day", "daily", "today"):
        return "daily", today, "today"
    return "monthly", today, "this_month"


async def cmd_report(message: Message, command: CommandObject, state: FSMContext,
                     api: PocketApi, renderer: Renderer) -> None:
    await _clear_prompt(state)
    kind, on, preset = report_args(command.args, renderer.today())
    try:
        rep = await api.report(message.from_user.id, kind, on)
    except ApiError as e:
        await _send_error(message, e, mutation=False)
        return
    text = rep.get("text_html") or ""
    if not text:
        text = esc(rep.get("text_plain") or "Отчёт пуст.")
    rows: list[list[InlineKeyboardButton]] = []
    b = renderer.url_button("📊 Подробно", f"/analytics?period={preset}")
    row = [b] if b else []
    row.append(Btn(text="📥 Разобрать", callback_data=cb.INBOX))
    rows.append(row)
    await safe_send(message.bot, message.chat.id, text, markup(rows))


async def show_inbox(bot: Bot, chat_id: int, uid: int, api: PocketApi,
                     renderer: Renderer) -> None:
    try:
        page = await api.list_transactions(uid, status="needs_account", limit=10,
                                           sort="date_desc")
        items = list(page.get("items") or [])
        accounts = await api.list_accounts(uid) if items else []
    except ApiError as e:
        await safe_send(bot, chat_id, views.api_error_text(e, mutation=False), None)
        return
    if not items:
        await safe_send(bot, chat_id, "📥 Разбирать нечего — у всех операций есть счёт.", None)
        return
    total = int(page.get("total") or len(items))
    await safe_send(bot, chat_id, f"📥 <b>Разобрать: {total}</b>\nВыберите счёт для каждой "
                                  "операции.", None)
    for tx in items:
        rd = renderer.needs_account_from_tx(tx, accounts)
        await safe_send(bot, chat_id, rd.text, rd.markup)
    if total > len(items):
        b = renderer.url_button("📋 Все в Pocket", "/operations?status=needs_account")
        await safe_send(bot, chat_id, f"… и ещё {total - len(items)}",
                        markup([[b]] if b else []))


async def cmd_inbox(message: Message, state: FSMContext, api: PocketApi,
                    renderer: Renderer) -> None:
    await _clear_prompt(state)
    await show_inbox(message.bot, message.chat.id, message.from_user.id, api, renderer)


async def cmd_last(message: Message, state: FSMContext, api: PocketApi,
                   renderer: Renderer) -> None:
    await _clear_prompt(state)
    try:
        page = await api.list_transactions(message.from_user.id, limit=10, sort="date_desc")
    except ApiError as e:
        await _send_error(message, e, mutation=False)
        return
    await _send(message, views.last_view(renderer, page))


async def cmd_undo(message: Message, state: FSMContext, api: PocketApi,
                   renderer: Renderer) -> None:
    await _clear_prompt(state)
    uid = message.from_user.id
    try:
        actions = await api.recent_actions(uid)
    except ApiError as e:
        await _send_error(message, e, mutation=False)
        return
    # Only the very last action counts (recent=true lists non-undone actions, newest first):
    # never fall through to an older operation the user did not mean.
    if not actions:
        await safe_send(message.bot, message.chat.id,
                        "Нечего отменять — за последние 30 минут действий не было.", None)
        return
    last = actions[0]
    if last.get("undone_at"):
        await safe_send(message.bot, message.chat.id, "↩️ Последнее действие уже отменено.", None)
        return
    if last.get("kind") == "undo" and not action_is_redo(last):
        # the last action is itself a cancel → nothing to undo; offer «Вернуть» like the button
        _, base = undo_summary_parts(last.get("summary"))
        text = "↩️ Последнее действие уже отменено" + (f": {esc(base)}" if base else ".")
        kb = None
        if last.get("is_undoable") and last.get("id"):
            renderer.mark_redo(last["id"])
            kb = markup([[Btn(text="↪️ Вернуть", callback_data=cb.undo(str(last["id"])))]])
        await safe_send(message.bot, message.chat.id, text, kb)
        return
    if not last.get("is_undoable"):
        summary = str(last.get("summary") or "").strip()
        await safe_send(message.bot, message.chat.id,
                        "⚠️ Последнее действие отменить нельзя"
                        + (f": {esc(summary)}" if summary else "."), None)
        return
    # an ordinary operation, or a «Вернуть» — undo it exactly as the ↩️ button would
    try:
        res = await api.undo(uid, str(last["id"]), idem_key=_msg_key(message))
    except ApiError as e:
        await _send_error(message, e, mutation=True, retry_msg_id=None)
        return
    await _send(message, renderer.undo_result(res))


async def cmd_settings(message: Message, state: FSMContext, api: PocketApi,
                       renderer: Renderer) -> None:
    await _clear_prompt(state)
    uid = message.from_user.id
    try:
        st = await api.get_settings(uid)
    except ApiError as e:
        await _send_error(message, e, mutation=False)
        return
    try:
        rec: list[dict[str, Any]] | None = await api.list_recurring(uid)
    except ApiError:
        rec = None
    await _send(message, views.settings_view(renderer, st, rec))


async def cmd_debts(message: Message, state: FSMContext, api: PocketApi,
                    renderer: Renderer) -> None:
    """/debts → GET /debts: «Мне должны / Я должен» + person buttons dv:{id} (DEBTS.md §11)."""
    await _clear_prompt(state)
    try:
        data = await api.list_debts(message.from_user.id)
    except ApiError as e:
        await _send_error(message, e, mutation=False)
        return
    await _send(message, debts_v.debts_view(renderer, data), state)


async def cmd_unknown(message: Message, state: FSMContext) -> None:
    await _clear_prompt(state)
    await safe_send(message.bot, message.chat.id, "Не знаю такой команды. Список — /help.", None)


# ------------------------------------------------------------------------------ text input
async def on_text(message: Message, state: FSMContext, api: PocketApi, renderer: Renderer,
                  settings: BotSettings) -> None:
    text = (message.text or "").strip()
    if not text:
        return
    if len(text) > settings.max_text_len:
        await safe_send(message.bot, message.chat.id, views.TOO_LONG, None)
        return
    st = await state.get_state()
    data = await state.get_data()
    if st is not None and (data.get("prompt_until") or 0) < time.time():
        await _clear_prompt(state)  # prompt expired → ordinary message
        st = None
    if st == Prompt.amount.state and data.get("rec_id"):
        await _clear_prompt(state)
        await _record_recurring_amount(message, api, renderer, int(data["rec_id"]), text,
                                       data.get("rec_msg"), data.get("rec_due"))
        return
    if st == Prompt.category.state and data.get("cat_tx"):
        await _clear_prompt(state)
        await _category_by_name(message, state, api, renderer, int(data["cat_tx"]), text)
        return
    if st == Prompt.debt.state and data.get("debt_id"):
        await _clear_prompt(state)
        await _strip_markup(message.bot, message.chat.id, data.get("debt_prompt"))
        composed = debts_v.repay_text(str(data.get("debt_name") or ""), text)
        if composed is not None:  # «200к» → «возврат долга: Коля 200к»
            resp = await run_assistant(message, state, api, renderer, composed)
            if _recorded(resp):
                await _strip_markup(message.bot, message.chat.id, data.get("debt_src"))
            return
        # not an amount («кофе 85к») → the prompt is dropped, an ordinary message
    edit_tx = int(data["edit_tx"]) if st == Prompt.edit.state and data.get("edit_tx") else None
    if edit_tx is not None:
        await _clear_prompt(state)
    await run_assistant(message, state, api, renderer, text, edit_tx=edit_tx)


async def _record_recurring_amount(message: Message, api: PocketApi, renderer: Renderer,
                                   rid: int, text: str, rec_msg: int | None,
                                   due_on: str | None = None) -> None:
    await _typing(message)
    uid = message.from_user.id
    try:
        res = await api.recurring_record(uid, rid, idem_key=_msg_key(message), amount_text=text,
                                         due_on=due_on)
    except ApiError as e:
        if e.status == 409 and e.code == "already_exists":
            await safe_send(message.bot, message.chat.id, "Уже записано.", None)
            return
        await _send_error(message, e, mutation=True)
        return
    rd = await _recorded_view(api, renderer, uid, res)
    await safe_send(message.bot, message.chat.id, rd.text, rd.markup)
    if rec_msg:
        try:
            await message.bot.edit_message_reply_markup(chat_id=message.chat.id,
                                                        message_id=int(rec_msg),
                                                        reply_markup=None)
        except TelegramBadRequest:
            pass


async def _recorded_view(api: PocketApi, renderer: Renderer, uid: int,
                         res: dict[str, Any]) -> Rendered:
    """Recurring «Записать» result: ✅ card, or «С какого счёта?» buttons when the payment has
    no account yet ($ / crypto without account → status needs_account)."""
    tx = res.get("transaction") or {}
    if tx.get("status") == "needs_account" and tx.get("id"):
        try:
            accounts = await api.list_accounts(uid)
        except ApiError as e:
            log.warning("accounts for needs_account card failed: %s", e)
            accounts = []
        return renderer.needs_account_from_tx(tx, accounts, action_id=res.get("action_id"),
                                              recorded=True)
    return renderer.tx_done(tx, res.get("action_id"))


async def _apply_category(bot: Bot, chat_id: int, uid: int, api: PocketApi, tx_id: int,
                          category_id: int, idem_key: str) -> dict[str, Any]:
    """GET /transactions/{id} (version) → PATCH category. Returns MutationResult."""
    tx = await api.get_transaction(uid, tx_id)
    return await api.patch_transaction(uid, tx_id, {"category_id": category_id,
                                                    "version": tx.get("version")},
                                       idem_key=idem_key)


def _learn_note(tx: dict[str, Any]) -> str | None:
    who = (tx.get("merchant") or {}).get("name") or tx.get("counterparty")
    cat = (tx.get("category") or {}).get("name")
    return f"Запомню: {who} → {cat}" if who and cat else None


async def _category_by_name(message: Message, state: FSMContext, api: PocketApi,
                            renderer: Renderer, tx_id: int, name: str) -> None:
    uid = message.from_user.id
    try:
        sug = await api.suggest_categories(uid, tx_id, q=name)
    except ApiError as e:
        await _send_error(message, e, mutation=False)
        return
    cats = [s.get("category") or {} for s in sug.get("similar") or []] + list(sug.get("all") or [])
    exact = next((c for c in cats if str(c.get("name", "")).strip().lower()
                  == name.strip().lower() and c.get("id")), None)
    if exact is not None:
        try:
            res = await _apply_category(message.bot, message.chat.id, uid, api, tx_id,
                                        int(exact["id"]), _msg_key(message))
        except ApiError as e:
            await _send_error(message, e, mutation=True)
            return
        tx = res.get("transaction") or {}
        await _send(message, renderer.tx_done(tx, res.get("action_id"), note=_learn_note(tx)))
        return
    if sug.get("similar"):
        kb = renderer.category_picker(tx_id, {"similar": sug.get("similar"), "all": []})
        await safe_send(message.bot, message.chat.id,
                        f"🔍 Точного совпадения с «{esc(name)}» нет. Похожие:", kb)
        return
    # unknown name → let the assistant decide (may create a 🆕 category) in edit context
    await run_assistant(message, state, api, renderer, f"категория {name}", edit_tx=tx_id)


async def on_non_text(message: Message) -> None:
    await safe_send(message.bot, message.chat.id, views.NON_TEXT, None)


async def on_edited(message: Message) -> None:
    await safe_send(message.bot, message.chat.id, views.EDITED_HINT, None)


# ------------------------------------------------------------------------------ callbacks
async def on_callback(query: CallbackQuery, state: FSMContext, api: PocketApi,
                      renderer: Renderer, stash: Stash) -> None:
    parsed = cb.parse(query.data)
    msg = query.message if isinstance(query.message, Message) else None
    handler = _CALLBACKS.get(parsed.op) if parsed else None
    if parsed is None or handler is None or msg is None:
        await query.answer(views.STALE_BUTTON, show_alert=False)
        return
    try:
        await handler(query, msg, parsed, state, api, renderer, stash)
    except ApiError as e:
        text = views.api_error_text(e, mutation=parsed.op not in READ_ONLY_CALLBACKS)
        await _answer(query, _plain(text)[:190])
        if e.code in ("preview_expired", "preview_stale", "gone"):
            await safe_edit(msg, msg.html_text + "\n\n" + text, None)
        elif e.status not in (0, 429) and e.status < 500 and e.code != "idempotency_in_progress":
            await safe_send(msg.bot, msg.chat.id, text, None)
    except (ValueError, IndexError):
        await _answer(query, views.STALE_BUTTON)


READ_ONLY_CALLBACKS = frozenset({"c", "b", "i", "g", "dv", "dr"})


def _plain(html_text: str) -> str:
    return re.sub(r"<[^>]+>", "", html_text)


async def _answer(query: CallbackQuery, text: str | None = None, alert: bool = False) -> None:
    try:
        await query.answer(text, show_alert=alert)
    except TelegramBadRequest:
        pass  # query too old — nothing to do


async def cb_undo(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext, api: PocketApi,
                  r: Renderer, stash: Stash) -> None:
    action_hex = c.args[0]
    remembered = r.is_redo(action_hex)
    res = await api.undo(q.from_user.id, cb.uuid_from_hex(action_hex), idem_key=f"tgcb:{q.id}")
    is_redo = r.undo_is_redo(res, redo=remembered)
    rd = r.undo_result(res, redo=remembered)
    await _answer(q, "Возвращено" if is_redo else "Отменено")
    await safe_edit(msg, rd.text, rd.markup)


async def cb_undo_many(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                       api: PocketApi, r: Renderer, stash: Stash) -> None:
    key = c.args[0]
    ids = stash.get(key)
    if not ids:
        await _answer(q, views.STALE_BUTTON)
        return
    is_redo = stash.get(f"redoU:{key}") is not None
    new_ids: list[str] = []
    errors: list[str] = []
    for i, aid in enumerate(ids):
        try:
            res = await api.undo(q.from_user.id, str(aid), idem_key=f"tgcb:{q.id}:{i}")
            if res.get("undo_action_id"):
                new_ids.append(str(res["undo_action_id"]))
        except ApiError as e:
            errors.append(_plain(views.api_error_text(e)))
    done = len(ids) - len(errors)
    if is_redo:
        text = f"↪️ Вернул {ops_word(done)}"
        label = "↩️ Отменить все"
    else:
        text = f"↩️ Отменено: {ops_word(done)}"
        label = "↪️ Вернуть все"
    if errors:
        text += "\n" + esc(errors[0])
    kb = None
    if new_ids:
        new_key = stash.put(new_ids)
        if not is_redo:
            stash.put(True, key=f"redoU:{new_key}")
        kb = markup([[Btn(text=label, callback_data=cb.undo_many(new_key))]])
    await _answer(q, "Готово" if not errors else "Не всё получилось")
    await safe_edit(msg, text, kb)


async def _start_prompt(q: CallbackQuery, msg: Message, state: FSMContext, st: State,
                        text: str, **data: Any) -> None:
    await _clear_prompt(state)
    await state.set_state(st)
    await state.update_data(prompt_until=time.time() + PROMPT_TTL, **data)
    await _answer(q)
    await safe_send(msg.bot, msg.chat.id, text, CANCEL_KB)


async def cb_edit(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext, api: PocketApi,
                  r: Renderer, stash: Stash) -> None:
    await _start_prompt(q, msg, state, Prompt.edit, views.EDIT_PROMPT, edit_tx=c.int_arg(0))


async def cb_edit_many(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                       api: PocketApi, r: Renderer, stash: Stash) -> None:
    items = stash.get(c.args[0])
    if not items:
        await _answer(q, views.STALE_BUTTON)
        return
    if msg.reply_markup is not None:
        stash.put(msg.reply_markup, key=f"kb:{msg.chat.id}:{msg.message_id}")
    rows = [[Btn(text=f"✏️ {label}", callback_data=cb.edit(int(tx_id)))]
            for tx_id, label in items[:10]]
    rows.append([Btn(text="‹ Назад", callback_data=cb.category_back(0))])
    await _answer(q, "Какую изменить?")
    await safe_edit_markup(msg, markup(rows))


async def cb_account(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                     api: PocketApi, r: Renderer, stash: Stash) -> None:
    tx_id, acc = c.int_arg(0), c.int_arg(1)
    try:
        res = await api.assign_account(q.from_user.id, tx_id, acc or None,
                                       idem_key=f"tgcb:{q.id}")
    except ApiError as e:
        if e.status == 409 and e.code == "version_conflict":
            await _answer(q, "Счёт уже выбран")
            return
        raise
    tx = res.get("transaction") or {}
    rd = r.tx_done(tx, res.get("action_id"))
    acc_name = r.real_side(tx)[0] or "без счёта"  # debt inflow: counter_account
    await _answer(q, f"Счёт: {acc_name}")
    await safe_edit(msg, rd.text, rd.markup)


async def cb_later(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                   api: PocketApi, r: Renderer, stash: Stash) -> None:
    try:
        n = await api.needs_account_count(q.from_user.id)
        tail = f"📥 Отложил. Разобрать: {n} — /inbox"
    except ApiError:
        tail = "📥 Отложил — /inbox"
    await _answer(q, "Отложил")
    await safe_edit(msg, msg.html_text + "\n\n" + tail, None)


async def cb_change_category(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                             api: PocketApi, r: Renderer, stash: Stash) -> None:
    tx_id = c.int_arg(0)
    sug = await api.suggest_categories(q.from_user.id, tx_id)
    if msg.reply_markup is not None:
        stash.put(msg.reply_markup, key=f"kb:{msg.chat.id}:{msg.message_id}")
    await _answer(q)
    await safe_edit_markup(msg, r.category_picker(tx_id, sug))


async def cb_set_category(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                          api: PocketApi, r: Renderer, stash: Stash) -> None:
    tx_id, cat_id = c.int_arg(0), c.int_arg(1)
    res = await _apply_category(msg.bot, msg.chat.id, q.from_user.id, api, tx_id, cat_id,
                                f"tgcb:{q.id}")
    tx = res.get("transaction") or {}
    rd = r.tx_done(tx, res.get("action_id"), note=_learn_note(tx))
    await _answer(q, f"Категория: {(tx.get('category') or {}).get('name') or 'изменена'}")
    await safe_edit(msg, rd.text, rd.markup)


async def cb_category_other(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                            api: PocketApi, r: Renderer, stash: Stash) -> None:
    await _start_prompt(q, msg, state, Prompt.category, views.CATEGORY_PROMPT,
                        cat_tx=c.int_arg(0))


async def cb_back(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext, api: PocketApi,
                  r: Renderer, stash: Stash) -> None:
    kb = stash.get(f"kb:{msg.chat.id}:{msg.message_id}")
    tx_id = c.int_arg(0)
    if kb is None and tx_id:
        tx = await api.get_transaction(q.from_user.id, tx_id)
        kb = r.tx_done(tx, tx.get("created_action_id")).markup
    await _answer(q)
    await safe_edit_markup(msg, kb)


async def cb_preview(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                     api: PocketApi, r: Renderer, stash: Stash) -> None:
    token = cb.uuid_from_hex(c.args[0])
    if c.op == "x":
        try:
            await api.cancel_preview(q.from_user.id, token)
        except ApiError as e:
            if e.status not in (404, 409, 410):
                raise
        await _answer(q, "Отменено")
        await safe_edit(msg, msg.html_text + "\n\n✖️ Отменено — ничего не изменено.", None)
        return
    await _answer(q, "Применяю…")
    resp = await api.confirm_preview(q.from_user.id, token, idem_key=f"tgcb:{q.id}")
    await send_response(msg, state, r, resp, msg_id=None, edit_first=msg)


async def cb_send_text(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                       api: PocketApi, r: Renderer, stash: Stash) -> None:
    data = await state.get_data()
    options = (data.get("msg_options") or {}).get(str(msg.message_id)) \
        or data.get("options") or []
    n = c.int_arg(0)
    if n >= len(options):
        await _answer(q, views.STALE_BUTTON)
        return
    text = str(options[n])
    await _answer(q)
    await safe_edit(msg, msg.html_text + f"\n→ <i>{esc(text)}</i>", None)
    # one key per (message, option): a double tap arrives as two callback queries with different
    # ids — keyed by the button itself, the second one replays instead of creating a duplicate
    key = f"tgt:{msg.chat.id}:{msg.message_id}:{n}"
    try:
        resp = await api.assistant(q.from_user.id, text, idem_key=key)
    except ApiError as e:
        await safe_send(msg.bot, msg.chat.id, views.api_error_text(e), None)
        return
    await send_response(msg, state, r, resp, msg_id=None)


async def cb_retry(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                   api: PocketApi, r: Renderer, stash: Stash) -> None:
    orig_id = c.int_arg(0)
    same_key = c.args[1] == "s" if len(c.args) > 1 else True
    saved = ((await state.get_data()).get("texts") or {}).get(str(orig_id))
    if not saved and msg.reply_to_message is not None and msg.reply_to_message.text:
        saved = {"text": msg.reply_to_message.text, "edit": None}
    if not saved:
        await _answer(q, views.STALE_BUTTON)
        return
    await _answer(q, "Повторяю…")
    key = f"tg:{msg.chat.id}:{orig_id}" if same_key else f"tgcb:{q.id}"
    try:
        resp = await api.assistant(q.from_user.id, saved["text"], idem_key=key,
                                   edit_transaction_id=saved.get("edit"))
    except ApiError as e:
        kb = views.retry_markup(orig_id, same_key=same_key) if e.is_server else None
        await safe_edit(msg, views.api_error_text(e), kb)
        return
    await send_response(msg, state, r, resp, msg_id=orig_id, edit_first=msg)


def _due_arg(raw: str | None) -> str | None:
    """«20261101» from the reminder button → «2026-11-01» (anything else → None)."""
    if not raw or len(raw) != 8 or not raw.isdigit():
        return None
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


async def cb_recurring(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                       api: PocketApi, r: Renderer, stash: Stash) -> None:
    rid = c.int_arg(0)
    due = _due_arg(c.args[1] if len(c.args) > 1 else None)
    if c.op == "m":
        await _start_prompt(q, msg, state, Prompt.amount, views.AMOUNT_PROMPT, rec_id=rid,
                            rec_msg=msg.message_id, rec_due=due)
        return
    # one key per reminder message: a double tap on «Записать» replays instead of duplicating
    key = f"tgrec:{msg.chat.id}:{msg.message_id}"
    if c.op == "k":
        try:
            rec = await api.recurring_skip(q.from_user.id, rid, due_on=due, idem_key=f"{key}:k")
        except ApiError as e:
            if e.status == 409 and e.code == "already_exists":
                await _answer(q, "Уже записано или пропущено")
                return
            raise
        nxt = rec.get("next_due_on")
        tail = f" Следующее напоминание — {human_date(nxt)}." if nxt else ""
        await _answer(q, "Пропущено")
        await safe_edit(msg, msg.html_text + f"\n\n⏭ Пропущено.{tail}", None)
        return
    try:
        res = await api.recurring_record(q.from_user.id, rid, idem_key=key, due_on=due)
    except ApiError as e:
        if e.status == 409 and e.code == "already_exists":
            await _answer(q, "Уже записано или пропущено")
            return
        raise
    rd = await _recorded_view(api, r, q.from_user.id, res)
    await _answer(q, "Записал")
    await safe_edit(msg, rd.text, rd.markup)


async def cb_settings(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                      api: PocketApi, r: Renderer, stash: Stash) -> None:
    field = views.SETTINGS_FIELDS.get(c.args[0] if c.args else "")
    if field is None:
        await _answer(q, views.STALE_BUTTON)
        return
    uid = q.from_user.id
    st = await api.get_settings(uid)
    st = await api.patch_settings(uid, {"version": st.get("version"), field: not st.get(field)})
    try:
        rec: list[dict[str, Any]] | None = await api.list_recurring(uid)
    except ApiError:
        rec = None
    rd = views.settings_view(r, st, rec)
    await _answer(q, "Включено" if st.get(field) else "Выключено")
    await safe_edit(msg, rd.text, rd.markup)


async def cb_inbox(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                   api: PocketApi, r: Renderer, stash: Stash) -> None:
    await _answer(q)
    await show_inbox(msg.bot, msg.chat.id, q.from_user.id, api, r)


async def cb_cancel_prompt(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                           api: PocketApi, r: Renderer, stash: Stash) -> None:
    await _clear_prompt(state)
    await _answer(q, "Отменено")
    await safe_edit(msg, "✖️ Отменено.", None)


# ------------------------------------------------------------------------------ debts (§9, §11)
async def cb_debt_view(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                       api: PocketApi, r: Renderer, stash: Stash) -> None:
    """dv:{id} → GET /debts/{id}: a new message with the person card (the list stays)."""
    detail = await api.get_debt(q.from_user.id, c.int_arg(0), limit=debts_v.HISTORY_LIMIT)
    rd = debts_v.debt_card_view(r, detail)
    await _answer(q)
    sent = await safe_send(msg.bot, msg.chat.id, rd.text, rd.markup)
    await _store_options(state, sent.message_id if sent else None, rd.options)


async def cb_debt_repay(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                        api: PocketApi, r: Renderer, stash: Stash) -> None:
    """dr:{id} «Записать возврат» → «Сколько вернул Коля?» (the name comes from GET /debts/{id});
    the next text goes to the assistant as «возврат долга: Коля <текст>»."""
    did = c.int_arg(0)
    detail = await api.get_debt(q.from_user.id, did, limit=1)
    debt = dict(detail.get("debt") or {}, id=did)
    if not debt.get("name"):
        await _answer(q, views.STALE_BUTTON)
        return
    if not debts_v.balances_of(debt):
        await _answer(q, "Долг уже закрыт")
        await safe_edit(msg, msg.html_text + "\n\n" + debts_v.SETTLED_TEXT, None)
        return
    await _clear_prompt(state)
    await state.set_state(Prompt.debt)
    await _answer(q)
    rd = debts_v.repay_prompt(r, debt)
    sent = await safe_send(msg.bot, msg.chat.id, rd.text, rd.markup)
    await state.update_data(prompt_until=time.time() + PROMPT_TTL, debt_id=did,
                            debt_name=str(debt["name"]), debt_src=msg.message_id,
                            debt_prompt=sent.message_id if sent else None)


async def cb_debt_repay_all(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                            api: PocketApi, r: Renderer, stash: Stash) -> None:
    """dra:{id} «Весь долг» → assistant «возврат долга: Коля». One key per prompt message
    (``tgdra:{chat}:{msg}``): a double tap replays the answer instead of a second return."""
    did = c.int_arg(0)
    uid = q.from_user.id
    data = await state.get_data()
    same = await state.get_state() == Prompt.debt.state and data.get("debt_id") == did
    name = str(data.get("debt_name") or "") if same else ""
    src = data.get("debt_src") if same else None
    if not name:
        detail = await api.get_debt(uid, did, limit=1)
        name = str((detail.get("debt") or {}).get("name") or "")
    if not name:
        await _answer(q, views.STALE_BUTTON)
        return
    if same:
        await _clear_prompt(state)
    await _answer(q, "Записываю…")
    text = debts_v.repay_text(name, "весь") or f"возврат долга: {name}"
    try:
        resp = await api.assistant(uid, text, idem_key=f"tgdra:{msg.chat.id}:{msg.message_id}")
    except ApiError as e:
        await safe_send(msg.bot, msg.chat.id, views.api_error_text(e), None)
        return
    await send_response(msg, state, r, resp, msg_id=None, edit_first=msg)
    if _recorded(resp) and src and int(src) != msg.message_id:
        await _strip_markup(msg.bot, msg.chat.id, src)


def _without_prefix(kb: InlineKeyboardMarkup | None, prefix: str) -> InlineKeyboardMarkup | None:
    if kb is None:
        return None
    rows = [[b for b in row if not (b.callback_data or "").startswith(prefix)]
            for row in kb.inline_keyboard]
    return markup(rows)


async def cb_debt_snooze(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                         api: PocketApi, r: Renderer, stash: Stash) -> None:
    """dz:{id} «Напомнить позже» → POST /debts/{id}/snooze {days: 7} → «Напомню через 7 дней»
    (the reminder keeps «Записать возврат»)."""
    await api.snooze_debt(q.from_user.id, c.int_arg(0), debts_v.SNOOZE_DAYS)
    await _answer(q, f"Напомню через {debts_v.SNOOZE_DAYS} дней")
    await safe_edit(msg, msg.html_text + "\n\n" + debts_v.SNOOZED_TEXT,
                    _without_prefix(msg.reply_markup, "dz:"))


async def cb_open(q: CallbackQuery, msg: Message, c: cb.Cb, state: FSMContext,
                  api: PocketApi, r: Renderer, stash: Stash) -> None:
    route = cb.route_for_key(":".join(c.args)) or "/"
    await _answer(q, f"Откройте Pocket: {r.url(route) or route}", alert=True)


_CALLBACKS = {
    "u": cb_undo,
    "U": cb_undo_many,
    "e": cb_edit,
    "M": cb_edit_many,
    "a": cb_account,
    "l": cb_later,
    "c": cb_change_category,
    "s": cb_set_category,
    "n": cb_category_other,
    "b": cb_back,
    "p": cb_preview,
    "x": cb_preview,
    "t": cb_send_text,
    "y": cb_retry,
    "r": cb_recurring,
    "k": cb_recurring,
    "m": cb_recurring,
    "g": cb_settings,
    "i": cb_inbox,
    "z": cb_cancel_prompt,
    "o": cb_open,
    "dv": cb_debt_view,
    "dr": cb_debt_repay,
    "dra": cb_debt_repay_all,
    "dz": cb_debt_snooze,
}


# ------------------------------------------------------------------------------ wiring
def setup_routers() -> Router:
    """Fresh router (a Router can be attached to one dispatcher only). Owner-only access is
    enforced by ``OwnerOnlyMiddleware`` on the dispatcher (see app.build_dispatcher)."""
    router = Router(name="pocket")
    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_help, Command("help"))
    router.message.register(cmd_today, Command("today"))
    router.message.register(cmd_month, Command("month"))
    router.message.register(cmd_report, Command("report"))
    router.message.register(cmd_inbox, Command("inbox"))
    router.message.register(cmd_last, Command("last"))
    router.message.register(cmd_undo, Command("undo"))
    router.message.register(cmd_settings, Command("settings"))
    router.message.register(cmd_debts, Command("debts"))
    router.message.register(cmd_unknown, F.text.regexp(r"^/[A-Za-z0-9_]+(@\w+)?(\s|$)"))
    router.message.register(on_text, F.text)
    router.message.register(on_non_text)
    router.edited_message.register(on_edited)
    router.callback_query.register(on_callback)
    return router


async def on_error(event: ErrorEvent, bot: Bot) -> bool:
    """Last-resort handler: log + short honest reply instead of silence."""
    log.error("unhandled error in update %s", event.update.update_id,
              exc_info=event.exception)
    upd = event.update
    try:
        if upd.callback_query is not None:
            await upd.callback_query.answer("⚠️ Внутренняя ошибка бота", show_alert=False)
        chat = (upd.message or upd.edited_message
                or (upd.callback_query.message if upd.callback_query else None))
        if chat is not None:
            await safe_send(bot, chat.chat.id, "⚠️ Внутренняя ошибка бота. Проверьте /last — "
                                               "записалось ли.", None)
    except Exception:  # reporting must not raise
        log.warning("could not report the error to the user", exc_info=True)
    return True

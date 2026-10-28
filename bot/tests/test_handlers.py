"""Dispatcher-level tests: fake updates via ``Dispatcher.feed_update`` + mocked Bot API session +
fake backend serving contract fixtures. Covers access control, text → assistant, every
callback from API.md §13, edit flow, errors and commands."""

from __future__ import annotations

import httpx
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageReplyMarkup,
    EditMessageText,
    SendChatAction,
    SendMessage,
)

from tests.conftest import (
    OWNER,
    SERVICE_TOKEN,
    STRANGER,
    FakeBackend,
    MockedSession,
    callback_update,
    fx,
    kb_flat,
    kb_rows,
    message_update,
)

N = " "
A1 = "6b0c0d3e-1111-4a2b-9c3d-000000000001"
A1_HEX = A1.replace("-", "")
UNDO1 = "7c1d0e4f-2222-4b3c-8d4e-000000000001"
UNDO1_HEX = UNDO1.replace("-", "")
PREVIEW = "8d2e1f50-3333-4c4d-9e5f-000000000001"
PREVIEW_HEX = PREVIEW.replace("-", "")


def plain(text: str) -> str:
    return text.replace(N, " ")


def answers(session: MockedSession) -> list[AnswerCallbackQuery]:
    return session.of(AnswerCallbackQuery)


# ------------------------------------------------------------------------ access control
async def test_stranger_gets_silence(feed, session, backend):
    await feed(message_update("еда 150к", user_id=STRANGER))
    await feed(message_update("/start", user_id=STRANGER))
    await feed(callback_update("u:" + A1_HEX, user_id=STRANGER))
    assert session.requests == []  # not even answerCallbackQuery
    assert backend.calls == []


async def test_owner_in_group_gets_silence(feed, session, backend):
    await feed(message_update("еда 150к", chat_type="group", chat_id=-100500))
    assert session.requests == [] and backend.calls == []


# ------------------------------------------------------------------------ text → assistant
async def test_text_creates_operation(feed, session, backend: FakeBackend):
    backend.on("POST", "/assistant/messages", fx("created"))
    upd = message_update("еда 150к", message_id=321)
    await feed(upd)
    req = backend.last("POST", "/assistant/messages")
    assert req.headers["Authorization"] == f"Bearer {SERVICE_TOKEN}"
    assert req.headers["X-Telegram-User-Id"] == str(OWNER)
    assert req.headers["Idempotency-Key"] == f"tg:{OWNER}:321"
    assert backend.body(req) == {"text": "еда 150к"}
    assert session.of(SendChatAction)
    sent = session.sent[-1]
    assert plain(sent.text) == "✅ 150 000 ₫ · Еда\nКарта · сегодня, 14:32"
    assert sent.parse_mode == "HTML"
    assert kb_rows(sent) == [[("↩️ Отменить", f"u:{A1_HEX}"), ("✏️ Изменить", "e:101")]]


async def test_too_long_text_not_sent_to_backend(feed, session, backend):
    await feed(message_update("а" * 2001))
    assert backend.calls == []
    assert "до 2000 символов" in session.sent[-1].text


async def test_non_text_and_edited_messages(feed, session, backend):
    await feed(message_update(None, sticker=True))
    assert "только текст" in session.sent[-1].text
    await feed(message_update("еда 200к", edited=True))
    assert "Правки сообщений не отслеживаю" in session.sent[-1].text
    assert backend.calls == []


async def test_unknown_command(feed, session, backend):
    await feed(message_update("/foo"))
    assert "Не знаю такой команды" in session.sent[-1].text
    assert backend.calls == []


# ------------------------------------------------------------------------ undo / redo
async def test_undo_then_redo(feed, session, backend):
    backend.on("POST", "/actions/{id}/undo", fx("undo_result"))
    upd = callback_update(f"u:{A1_HEX}")
    await feed(upd)
    req = backend.last("POST", f"/actions/{A1}/undo")
    assert req.headers["Idempotency-Key"] == f"tgcb:{upd.callback_query.id}"
    edit = session.edits[-1]
    assert plain(edit.text) == "↩️ Отменено: 150 000 ₫ · Еда"
    assert kb_rows(edit) == [[("↪️ Вернуть", f"u:{UNDO1_HEX}")]]
    assert answers(session)[-1].text == "Отменено"
    # «Вернуть» = undo of the undo-action
    redo_res = {"status": "undone", "undone_action_id": UNDO1,
                "undo_action_id": "9e3f2061-4444-4d5e-8f60-000000000001",
                "summary": "Возвращено: 150 000 ₫ · Еда", "changed": ["transactions"]}
    backend.on("POST", f"/actions/{UNDO1}/undo", redo_res)
    await feed(callback_update(f"u:{UNDO1_HEX}"))
    edit = session.edits[-1]
    assert edit.text.startswith("↪️ Возвращено")
    assert kb_rows(edit)[0][0][0] == "↩️ Отменить"
    assert answers(session)[-1].text == "Возвращено"


async def test_undo_conflict_is_reported(feed, session, backend):
    backend.error("POST", "/actions/{id}/undo", 409, "undo_conflict", "Запись изменена")
    await feed(callback_update(f"u:{A1_HEX}"))
    assert "Не могу отменить" in answers(session)[-1].text
    assert "Не могу отменить" in session.sent[-1].text


async def test_undo_many(feed, session, backend, stash):
    backend.on("POST", "/assistant/messages", fx("created_many"))
    await feed(message_update("еда 120к, такси 45к и кофе 30к"))
    rows = kb_rows(session.sent[-1])
    undo_cb = rows[0][0][1]
    assert undo_cb.startswith("U:")
    backend.on("POST", "/actions/{id}/undo", fx("undo_result"))
    upd = callback_update(undo_cb)
    await feed(upd)
    calls = backend.find("POST", "/actions/")
    assert len(calls) == 3
    keys = {c.headers["Idempotency-Key"] for c in calls}
    assert len(keys) == 3 and all(k.startswith(f"tgcb:{upd.callback_query.id}:") for k in keys)
    assert session.edits[-1].text == "↩️ Отменено: 3 операции"
    assert kb_rows(session.edits[-1])[0][0][0] == "↪️ Вернуть все"


async def test_edit_many_picker(feed, session, backend):
    backend.on("POST", "/assistant/messages", fx("created_many"))
    await feed(message_update("еда 120к, такси 45к и кофе 30к"))
    edit_cb = kb_rows(session.sent[-1])[0][1][1]
    assert edit_cb.startswith("M:")
    await feed(callback_update(edit_cb))
    mk = session.of(EditMessageReplyMarkup)[-1]
    flat = kb_flat(mk)
    assert [d for _, d in flat[:3]] == ["e:201", "e:202", "e:203"]
    assert flat[-1][0] == "‹ Назад"


# ------------------------------------------------------------------------ edit flow
async def test_edit_button_then_new_amount_edits_same_operation(feed, session, backend):
    await feed(callback_update("e:101"))
    assert "Напишите новую сумму" in session.sent[-1].text
    assert kb_flat(session.sent[-1]) == [("✖️ Отмена", "z")]
    backend.on("POST", "/assistant/messages", fx("updated"))
    await feed(message_update("250к", message_id=400))
    req = backend.last("POST", "/assistant/messages")
    assert backend.body(req) == {"text": "250к", "context": {"edit_transaction_id": 101}}
    assert plain(session.sent[-1].text).endswith("Изменено: 150 000 ₫ → 250 000 ₫")
    # the prompt is one-shot: the next message is an ordinary one
    backend.on("POST", "/assistant/messages", fx("created"))
    await feed(message_update("кофе 85"))
    assert backend.body(backend.last("POST", "/assistant/messages")) == {"text": "кофе 85"}


async def test_edit_prompt_cancel_and_expiry(feed, session, backend, monkeypatch):
    await feed(callback_update("e:101"))
    await feed(callback_update("z"))
    assert session.edits[-1].text == "✖️ Отменено."
    backend.on("POST", "/assistant/messages", fx("created"))
    await feed(message_update("кофе 85"))
    assert "context" not in backend.body(backend.last("POST", "/assistant/messages"))
    # expiry: 10 minutes
    import pocket_bot.handlers as h

    await feed(callback_update("e:101"))
    real = h.time.time
    monkeypatch.setattr(h.time, "time", lambda: real() + 601)
    await feed(message_update("250к"))
    assert "context" not in backend.body(backend.last("POST", "/assistant/messages"))


async def test_command_clears_edit_prompt(feed, session, backend):
    await feed(callback_update("e:101"))
    await feed(message_update("/help"))
    backend.on("POST", "/assistant/messages", fx("created"))
    await feed(message_update("250к"))
    assert "context" not in backend.body(backend.last("POST", "/assistant/messages"))


# ------------------------------------------------------------------------ choose account
async def test_needs_account_choose_wallet(feed, session, backend):
    backend.on("POST", "/assistant/messages", fx("needs_account"))
    await feed(message_update("потратил 15 USDT на подписку"))
    sent = session.sent[-1]
    assert plain(sent.text).endswith("Записал в расходы. С какого счёта?")
    assert ("Кошелёк", "a:125:3") in kb_flat(sent)
    backend.on("POST", "/transactions/125/account", fx("mutation_assign"))
    upd = callback_update("a:125:3", message_id=777)
    await feed(upd)
    req = backend.last("POST", "/transactions/125/account")
    assert backend.body(req) == {"account_id": 3}
    assert req.headers["Idempotency-Key"] == f"tgcb:{upd.callback_query.id}"
    edit = session.edits[-1]
    assert edit.message_id == 777
    assert plain(edit.text).startswith("✅ 15 USDT · Подписки\nКошелёк")
    assert kb_flat(edit)[0][0] == "↩️ Отменить"
    assert answers(session)[-1].text == "Счёт: Кошелёк"


async def test_choose_no_account_and_later(feed, session, backend):
    res = fx("mutation_assign")
    res["transaction"]["account"] = None
    backend.on("POST", "/transactions/125/account", res)
    await feed(callback_update("a:125:0"))
    assert backend.body(backend.last("POST", "/transactions/125/account")) == {"account_id": None}
    assert "без счёта" in session.edits[-1].text
    backend.on("GET", "/transactions/needs-account/count", {"count": 2})
    await feed(callback_update("l:125", text="💸 15 USDT · Подписки"))
    edit = session.edits[-1]
    assert edit.text.endswith("📥 Отложил. Разобрать: 2 — /inbox")
    assert edit.reply_markup is None


# ------------------------------------------------------------------------ categories
async def test_new_category_move_to_other(feed, session, backend):
    backend.on("POST", "/assistant/messages", fx("created_new_category"))
    await feed(message_update("перевёл 3 тона @alice за аккаунт gpt pro"))
    sent = session.sent[-1]
    assert ("🗂 В другую категорию", "c:124") in kb_flat(sent)
    original_kb = {"inline_keyboard": [[{"text": "↩️ Отменить", "callback_data": f"u:{A1_HEX}"}],
                                       [{"text": "🗂 В другую категорию",
                                         "callback_data": "c:124"}]]}
    backend.on("GET", "/categories/suggest", fx("suggest"))
    await feed(callback_update("c:124", message_id=900, reply_markup=original_kb))
    assert backend.last("GET", "/categories/suggest").url.params["transaction_id"] == "124"
    picker = session.of(EditMessageReplyMarkup)[-1]
    flat = kb_flat(picker)
    assert flat[0] == ("🔁 Подписки", "s:124:7")
    assert ("‹ Назад", "b:124") in flat and ("🔍 Другая…", "n:124") in flat
    # «‹ Назад» restores the saved keyboard without a backend call
    n_calls = len(backend.calls)
    await feed(callback_update("b:124", message_id=900))
    back = session.of(EditMessageReplyMarkup)[-1]
    assert kb_flat(back) == [("↩️ Отменить", f"u:{A1_HEX}"),
                             ("🗂 В другую категорию", "c:124")]
    assert len(backend.calls) == n_calls
    # pick a category: GET version → PATCH
    backend.on("GET", "/transactions/124", fx("tx_ton"))
    backend.on("PATCH", "/transactions/124", fx("mutation_category"))
    await feed(callback_update("s:124:7", message_id=900))
    patch = backend.last("PATCH", "/transactions/124")
    assert backend.body(patch) == {"category_id": 7, "version": 1}
    edit = session.edits[-1]
    assert plain(edit.text).split("\n")[0] == "✅ 3 TON · Подписки"
    assert "Запомню: @alice → Подписки" in edit.text


async def test_category_other_by_name(feed, session, backend):
    await feed(callback_update("n:124"))
    assert "название категории" in session.sent[-1].text
    backend.on("GET", "/categories/suggest", fx("suggest"))
    backend.on("GET", "/transactions/124", fx("tx_ton"))
    backend.on("PATCH", "/transactions/124", fx("mutation_category"))
    await feed(message_update("подписки"))
    assert backend.last("GET", "/categories/suggest").url.params["q"] == "подписки"
    assert backend.body(backend.last("PATCH", "/transactions/124"))["category_id"] == 7
    assert "Подписки" in session.sent[-1].text


async def test_category_unknown_name_goes_to_assistant(feed, session, backend):
    await feed(callback_update("n:124"))
    backend.on("GET", "/categories/suggest", {"similar": [], "all": []})
    backend.on("POST", "/assistant/messages", fx("updated"))
    await feed(message_update("Хобби"))
    body = backend.body(backend.last("POST", "/assistant/messages"))
    assert body == {"text": "категория Хобби", "context": {"edit_transaction_id": 124}}


# ------------------------------------------------------------------------ previews
async def test_preview_confirm(feed, session, backend):
    backend.on("POST", "/assistant/messages", fx("preview"))
    await feed(message_update("перенеси Highlands в кофе"))
    sent = session.sent[-1]
    assert kb_flat(sent) == [("✅ Применить", f"p:{PREVIEW_HEX}"),
                             ("✖️ Отмена", f"x:{PREVIEW_HEX}")]
    backend.on("POST", f"/assistant/previews/{PREVIEW}/confirm", fx("applied"))
    upd = callback_update(f"p:{PREVIEW_HEX}", message_id=901)
    await feed(upd)
    req = backend.last("POST", f"/assistant/previews/{PREVIEW}/confirm")
    assert req.headers["Idempotency-Key"] == f"tgcb:{upd.callback_query.id}"
    edit = session.edits[-1]
    assert edit.message_id == 901 and edit.text == "✅ Изменено 3 операции"


async def test_preview_cancel_and_expired(feed, session, backend):
    backend.on("POST", f"/assistant/previews/{PREVIEW}/cancel", {"ok": True})
    await feed(callback_update(f"x:{PREVIEW_HEX}", text="🔎 Изменю 3 операции"))
    assert session.edits[-1].text.endswith("✖️ Отменено — ничего не изменено.")
    backend.error("POST", f"/assistant/previews/{PREVIEW}/confirm", 410, "preview_expired",
                  "Превью устарело")
    await feed(callback_update(f"p:{PREVIEW_HEX}", text="🔎 Изменю 3 операции"))
    assert "Превью устарело" in session.edits[-1].text
    assert session.edits[-1].reply_markup is None


# ------------------------------------------------------------------------ unclear / errors
async def test_unclear_option_sends_text(feed, session, backend):
    backend.on("POST", "/assistant/messages", fx("unclear"))
    await feed(message_update("перевел вчера"))
    assert ("Это расход…", "t:0") in kb_flat(session.sent[-1])
    backend.on("POST", "/assistant/messages", fx("created"))
    upd = callback_update("t:0", text="🤔 Не понял, что записать")
    await feed(upd)
    req = backend.last("POST", "/assistant/messages")
    assert backend.body(req) == {"text": "расход вчера 100к"}
    assert req.headers["Idempotency-Key"] == f"tgt:{OWNER}:555:0"
    assert "→ <i>расход вчера 100к</i>" in session.edits[-1].text
    assert plain(session.sent[-1].text).startswith("✅ 150 000 ₫ · Еда")


async def test_ai_unavailable_card_and_retry_with_new_key(feed, session, backend):
    backend.on("POST", "/assistant/messages", fx("error_ai"))
    await feed(message_update("сложная фраза", message_id=500))
    sent = session.sent[-1]
    assert sent.text.startswith("⚠️ AI сейчас недоступен")
    assert ("🔁 Повторить", "y:500:n") in kb_flat(sent)
    backend.on("POST", "/assistant/messages", fx("created"))
    upd = callback_update("y:500:n", message_id=501)
    await feed(upd)
    req = backend.last("POST", "/assistant/messages")
    assert backend.body(req) == {"text": "сложная фраза"}
    assert req.headers["Idempotency-Key"] == f"tgcb:{upd.callback_query.id}"
    assert plain(session.edits[-1].text).startswith("✅ 150 000 ₫ · Еда")


async def test_server_error_then_retry_with_same_key(feed, session, backend):
    backend.error("POST", "/assistant/messages", 500, "internal", "boom")
    await feed(message_update("еда 150к", message_id=600))
    sent = session.sent[-1]
    assert sent.text == "⚠️ Не удалось сохранить, ничего не записано. Попробуйте ещё раз."
    assert kb_flat(sent) == [("🔁 Повторить", "y:600:s")]
    backend.on("POST", "/assistant/messages", fx("created"))
    await feed(callback_update("y:600:s", message_id=601))
    req = backend.last("POST", "/assistant/messages")
    assert req.headers["Idempotency-Key"] == f"tg:{OWNER}:600"  # same key → no duplicate
    assert plain(session.edits[-1].text).startswith("✅ 150 000 ₫")


async def test_transport_errors_are_honest(feed, session, backend):
    def boom(req):
        raise httpx.ConnectError("refused", request=req)

    backend.routes.insert(0, ("POST", __import__("re").compile("^/assistant/messages$"), boom))
    await feed(message_update("еда 150к"))
    assert session.sent[-1].text.startswith("⚠️ Сервер недоступен. Ничего не записано.")

    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)

    backend.routes.insert(0, ("POST", __import__("re").compile("^/assistant/messages$"), slow))
    await feed(message_update("еда 150к"))
    assert "Если запись всё-таки прошла, повтор не создаст дубль" in session.sent[-1].text


async def test_rate_limit_and_forbidden(feed, session, backend):
    backend.error("POST", "/assistant/messages", 429, "rate_limited", "Слишком часто",
                  headers={"Retry-After": "7"})
    await feed(message_update("еда 150к"))
    assert session.sent[-1].text == "⏳ Слишком часто. Подождите 7 с."
    backend.error("POST", "/assistant/messages", 403, "telegram_forbidden", "нет")
    await feed(message_update("еда 150к"))
    assert "не узнал этот Telegram-аккаунт" in session.sent[-1].text


async def test_validation_message_is_escaped(feed, session, backend):
    backend.error("POST", "/assistant/messages", 422, "validation_error", "Сумма <0>")
    await feed(message_update("еда -5"))
    assert session.sent[-1].text == "⚠️ Сумма &lt;0&gt;"


async def test_stale_buttons(feed, session, backend):
    await feed(callback_update("U:missing"))
    assert answers(session)[-1].text.startswith("Кнопка устарела")
    await feed(callback_update("Q:1"))
    assert answers(session)[-1].text.startswith("Кнопка устарела")
    await feed(callback_update("t:5"))
    assert answers(session)[-1].text.startswith("Кнопка устарела")
    assert backend.calls == []


async def test_html_parse_error_falls_back_to_plain(feed, session, backend):
    backend.on("POST", "/assistant/messages", fx("created"))
    session.fail(lambda m: TelegramBadRequest(m, "Bad Request: can't parse entities"),
                 on=SendMessage)
    await feed(message_update("еда 150к"))
    msgs = session.of(SendMessage)
    assert msgs[-1].parse_mode is None
    assert plain(msgs[-1].text).startswith("✅ 150 000 ₫")


async def test_edit_not_modified_is_ignored(feed, session, backend):
    backend.on("POST", "/actions/{id}/undo", fx("undo_result"))
    session.fail(lambda m: TelegramBadRequest(m, "Bad Request: message is not modified"),
                 on=EditMessageText)
    await feed(callback_update(f"u:{A1_HEX}"))
    assert len(session.of(EditMessageText)) == 1
    assert session.sent == []  # «not modified» is not an error → no fallback message


async def test_uneditable_message_falls_back_to_new_message(feed, session, backend):
    backend.on("POST", "/actions/{id}/undo", fx("undo_result"))
    session.fail(lambda m: TelegramBadRequest(m, "Bad Request: message can't be edited"),
                 on=EditMessageText)
    await feed(callback_update(f"u:{A1_HEX}"))
    assert session.sent and plain(session.sent[-1].text).startswith("↩️ Отменено")


async def test_unexpected_exception_reported_by_error_handler(settings, api, stash, renderer,
                                                              bot, session, backend):
    from pocket_bot.app import build_dispatcher

    dp = build_dispatcher(settings, api, stash=stash, renderer=renderer)  # catch_errors=True
    backend.on("POST", "/assistant/messages", {"cards": "garbage"})
    await dp.feed_update(bot, message_update("еда 150к"))
    assert "Внутренняя ошибка бота" in session.sent[-1].text

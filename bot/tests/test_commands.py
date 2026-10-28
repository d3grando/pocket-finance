"""Commands /start /help /today /month /report /inbox /last /undo /settings and the recurring
reminder buttons ([Записать] r:, [Пропустить] k:, [Другая сумма] m:)."""

from __future__ import annotations

from aiogram.methods import AnswerCallbackQuery, SendMessage

from tests.conftest import OWNER, callback_update, fx, kb_flat, message_update

N = " "
A1 = "6b0c0d3e-1111-4a2b-9c3d-000000000001"


def plain(text: str) -> str:
    return text.replace(N, " ")


async def test_start_and_help(feed, session, backend):
    await feed(message_update("/start"))
    assert "Pocket</b> на связи" in session.sent[-1].text
    assert "/today" in session.sent[-1].text
    await feed(message_update("/help"))
    text = session.sent[-1].text
    for cmd in ("/today", "/month", "/report", "/inbox", "/last", "/undo", "/settings"):
        assert cmd in text
    assert backend.calls == []


async def test_today(feed, session, backend):
    backend.on("GET", "/stats/summary", fx("summary"))
    backend.on("GET", "/stats/by-category", fx("by_category_today"))
    await feed(message_update("/today"))
    assert backend.last("GET", "/stats/summary").url.params["currency"] == "VND"
    p = backend.last("GET", "/stats/by-category").url.params
    assert (p["preset"], p["kind"], p["currency"]) == ("today", "expense", "VND")
    sent = session.sent[-1]
    lines = plain(sent.text).split("\n")
    assert lines[0] == "📊 Сегодня потрачено <b>−595 000 ₫</b> ≈ $22.76"
    assert lines[2:5] == ["🍴 Еда — 320 000 ₫", "🚕 Такси/транспорт — 120 000 ₫",
                          "☕ Кофе — 85 000 ₫"]
    assert lines[5] == "… ещё 2 категории — 70 000 ₫"
    assert "📥 Разобрать: 2 — /inbox" in lines
    assert kb_flat(sent) == [("📈 Открыть в Pocket", "https://pocket.test/analytics?period=today")]


async def test_today_zero_and_no_data(feed, session, backend):
    s = fx("summary")
    s["spent_today"]["value"] = "0"
    s["needs_account_count"] = 0
    backend.on("GET", "/stats/summary", s)
    backend.on("GET", "/stats/by-category", fx("by_category_today"))
    await feed(message_update("/today"))
    assert session.sent[-1].text == "📊 Сегодня расходов нет."
    s["spent_today"]["value"] = None
    backend.on("GET", "/stats/summary", s)
    await feed(message_update("/today"))
    assert "данных пока нет" in session.sent[-1].text


async def test_month(feed, session, backend):
    by_cat = fx("by_category_today")
    backend.on("GET", "/stats/summary", fx("summary"))
    backend.on("GET", "/stats/by-category", by_cat)
    await feed(message_update("/month"))
    assert backend.last("GET", "/stats/by-category").url.params["preset"] == "this_month"
    text = plain(session.sent[-1].text)
    assert text.startswith("🗓 <b>Октябрь 2026</b>")
    assert "Расходы  −12 450 000 ₫  (+12% к прошлому месяцу)" in text
    assert "Доходы   +18 300 000 ₫" in text
    assert "Поток    <b>+5 850 000 ₫</b> ≈ $223.80" in text
    assert "🍴 Еда — 320 000 ₫ · 54%" in text


async def test_stats_backend_down(feed, session, backend):
    backend.error("GET", "/stats/summary", 503, "internal", "down")
    await feed(message_update("/today"))
    assert session.sent[-1].text == "⚠️ Ошибка сервера. Попробуйте ещё раз."


async def test_by_category_failure_still_shows_summary(feed, session, backend):
    backend.on("GET", "/stats/summary", fx("summary"))
    backend.error("GET", "/stats/by-category", 501, "not_implemented", "нет")
    await feed(message_update("/today"))
    assert plain(session.sent[-1].text).startswith("📊 Сегодня потрачено <b>−595 000 ₫</b>")


async def test_report_current_and_previous(feed, session, backend):
    backend.on("GET", "/reports/monthly", fx("report_monthly"))
    await feed(message_update("/report"))
    assert backend.last("GET", "/reports/monthly").url.params["on"] == "2026-10-27"
    sent = session.sent[-1]
    assert sent.text.startswith("🗓 <b>Октябрь 2026</b>")
    assert ("📥 Разобрать", "i") in kb_flat(sent)
    assert ("📊 Подробно", "https://pocket.test/analytics?period=this_month") in kb_flat(sent)
    await feed(message_update("/report прошлый"))
    assert backend.last("GET", "/reports/monthly").url.params["on"] == "2026-09-30"
    assert ("📊 Подробно", "https://pocket.test/analytics?period=last_month") in kb_flat(
        session.sent[-1])
    backend.on("GET", "/reports/weekly", dict(fx("report_monthly"), kind="weekly"))
    await feed(message_update("/report неделя"))
    assert backend.last("GET", "/reports/weekly")


async def test_inbox(feed, session, backend):
    backend.on("GET", "/transactions", fx("page_needs_account"))
    backend.on("GET", "/accounts", fx("accounts"))
    await feed(message_update("/inbox"))
    p = backend.last("GET", "/transactions").url.params
    assert p["status"] == "needs_account"
    msgs = session.sent
    assert msgs[-2].text.startswith("📥 <b>Разобрать: 1</b>")
    card = msgs[-1]
    assert plain(card.text).startswith("💸 15 USDT · Подписки")
    flat = kb_flat(card)
    assert flat[0] == ("Кошелёк", "a:125:3") and ("Без счёта", "a:125:0") in flat
    assert ("↩️ Отменить", "u:" + A1.replace("-", "")) in flat


async def test_inbox_empty_and_via_button(feed, session, backend):
    page = fx("page_needs_account")
    page["items"], page["total"] = [], 0
    backend.on("GET", "/transactions", page)
    await feed(callback_update("i"))
    assert session.sent[-1].text.startswith("📥 Разбирать нечего")


async def test_last(feed, session, backend):
    backend.on("GET", "/transactions", fx("page_last"))
    await feed(message_update("/last"))
    text = plain(session.sent[-1].text)
    assert "27.10 · Еда — −150 000 ₫" in text
    assert "27.10 · Клиенты — +300 USDT" in text
    assert "… всего 42 операции" in text


async def test_undo_command(feed, session, backend):
    backend.on("GET", "/actions", fx("actions_recent"))
    backend.on("POST", "/actions/{id}/undo", fx("undo_result"))
    upd = message_update("/undo", message_id=777)
    await feed(upd)
    assert backend.last("GET", "/actions").url.params["recent"] == "true"
    req = backend.last("POST", "/actions/")
    assert req.url.path.endswith(f"/actions/{A1}/undo")  # the very last action
    assert req.headers["Idempotency-Key"] == f"tg:{OWNER}:777"
    assert plain(session.sent[-1].text) == "↩️ Отменено: 150 000 ₫ · Еда"


async def test_undo_command_nothing(feed, session, backend):
    backend.on("GET", "/actions", [])
    await feed(message_update("/undo"))
    assert session.sent[-1].text.startswith("Нечего отменять")


async def test_settings_toggle(feed, session, backend):
    backend.on("GET", "/settings", fx("settings"))
    backend.on("GET", "/recurring", fx("recurring"))
    await feed(message_update("/settings"))
    sent = session.sent[-1]
    assert "✅ Месячный — 1-го числа в 10:00" in sent.text
    assert "▫️ Ежедневный" in sent.text
    assert "• Аренда — 11" in sent.text
    assert ("▫️ Ежедневный", "g:d") in kb_flat(sent)
    patched = dict(fx("settings"), report_daily=True, version=4)
    backend.on("PATCH", "/settings", patched)
    await feed(callback_update("g:d", text=sent.text))
    assert backend.body(backend.last("PATCH", "/settings")) == {"version": 3,
                                                                "report_daily": True}
    edit = session.edits[-1]
    assert "✅ Ежедневный" in edit.text
    assert ("✅ Ежедневный", "g:d") in kb_flat(edit)


# ------------------------------------------------------------------------ recurring reminders
async def test_recurring_record(feed, session, backend):
    res = fx("mutation_assign")
    res["transaction"].update(amount="11000000", currency="VND", category={
        "id": 5, "name": "Жильё", "kind": "expense", "is_business": False, "is_new": False},
        account={"id": 1, "name": "Карта"}, account_amount="11000000", account_asset="VND")
    backend.on("POST", "/recurring/2/record", res)
    upd = callback_update("r:2:20261105", text="⏰ Сегодня обычно платишь")
    await feed(upd)
    req = backend.last("POST", "/recurring/2/record")
    # one key per reminder message (a double tap replays), the occurrence goes as ?due_on=
    msg = upd.callback_query.message
    assert req.headers["Idempotency-Key"] == f"tgrec:{msg.chat.id}:{msg.message_id}"
    assert req.url.params.get("due_on") == "2026-11-05"
    assert backend.body(req) == {}
    assert plain(session.edits[-1].text).startswith("✅ 11 000 000 ₫ · Жильё\nКарта")


async def test_recurring_record_already_done(feed, session, backend):
    backend.on("POST", "/recurring/2/record", {"error": {
        "code": "already_exists", "message": "Уже записано", "details": {}}}, status=409)
    n_edits = len(session.edits)
    await feed(callback_update("r:2:20261105", text="⏰ Сегодня обычно платишь"))
    assert len(session.edits) == n_edits  # no new card, only the toast
    assert any("Уже записано" in (a.text or "") for a in session.of(AnswerCallbackQuery))


async def test_recurring_skip(feed, session, backend):
    backend.on("POST", "/recurring/2/skip", fx("recurring")[0])
    await feed(callback_update("k:2", text="⏰ Сегодня обычно платишь"))
    edit = session.edits[-1]
    assert edit.text.endswith("⏭ Пропущено. Следующее напоминание — 5 ноября.")
    assert edit.reply_markup is None


async def test_recurring_other_amount(feed, session, backend):
    await feed(callback_update("m:2", message_id=888, text="⏰ Сегодня обычно платишь"))
    assert "Напишите сумму" in session.sent[-1].text
    backend.on("POST", "/recurring/2/record", fx("mutation_assign"))
    await feed(message_update("11.5м", message_id=889))
    req = backend.last("POST", "/recurring/2/record")
    assert backend.body(req) == {"amount_text": "11.5м"}
    assert req.headers["Idempotency-Key"] == f"tg:{OWNER}:889"
    assert session.sent[-1].text.startswith("✅")
    # the reminder message loses its buttons
    from aiogram.methods import EditMessageReplyMarkup

    mk = session.of(EditMessageReplyMarkup)[-1]
    assert mk.message_id == 888 and mk.reply_markup is None


async def test_commands_never_send_without_parse_mode(feed, session, backend):
    await feed(message_update("/start"))
    assert all(m.parse_mode == "HTML" for m in session.of(SendMessage))

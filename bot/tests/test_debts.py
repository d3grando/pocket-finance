"""Debts in the bot (docs/DEBTS.md §9, §11): /debts, the person card dv:, «Записать возврат»
dr:/dra:, «Напомнить позже» dz:, assistant debt cards, outbox debt_reminder, /settings toggle.

Updates go through ``Dispatcher.feed_update``; Bot API is the recording ``MockedSession``; the
backend is ``FakeBackend`` serving payloads built by the real backend schemas (contract.json)."""

from __future__ import annotations

from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, SendMessage

from tests.conftest import OWNER, STRANGER, callback_update, fx, kb_flat, kb_rows, message_update

N = "\u00a0"  # NBSP inside amounts (fmt.NBSP)
A1 = "6b0c0d3e-1111-4a2b-9c3d-000000000001"
A2 = "6b0c0d3e-1111-4a2b-9c3d-000000000002"
REMINDER = "⏰ Коля должен 300 000 ₫ уже 30 дней"
REMINDER_KB = {"inline_keyboard": [[{"text": "Записать возврат", "callback_data": "dr:5"},
                                    {"text": "Напомнить позже", "callback_data": "dz:5"}]]}


def plain(text: str) -> str:
    return text.replace(N, " ")


def toasts(session) -> list[str]:
    return [a.text or "" for a in session.of(AnswerCallbackQuery)]


def stripped(session) -> list[int]:
    """message ids whose keyboard was removed (EditMessageReplyMarkup with no markup)."""
    return [m.message_id for m in session.of(EditMessageReplyMarkup) if m.reply_markup is None]


# ------------------------------------------------------------------------------ /debts
async def test_debts_list_both_sides(feed, session, backend):
    backend.on("GET", "/debts", fx("debts_list"))
    await feed(message_update("/debts"))
    req = backend.last("GET", "/debts")
    assert req.url.params["include_settled"] == "false"
    assert req.headers["X-Telegram-User-Id"] == str(OWNER)
    sent = session.sent[-1]
    lines = plain(sent.text).split("\n")
    assert lines[0] == "💸 <b>Долги</b>"
    assert lines[2] == "Мне должны ≈ <b>4 230 000 ₫</b>"
    assert lines[3] == "• Коля — 300 000 ₫ · 30 дн."
    assert lines[4] == "• Вася — 50 USDT (≈ $50.00) · 3 дн."
    assert lines[5] == "• Лена &lt;b&gt; — 100 USDT (≈ $100.00)"  # escaped, opened today
    assert lines[7] == "Я должен <b>1 200 000 ₫</b>"  # only ₫ balances → exact, no ≈
    assert lines[8] == "• Петя — 1 000 000 ₫ · 7 дн."
    assert lines[9] == "• Лена &lt;b&gt; — 200 000 ₫ · 2 дн."
    rows = kb_rows(sent)
    # one button per person (Лена is in both lists → once), 2 per row, then the web link
    assert rows[:2] == [[("👤 Коля", "dv:5"), ("👤 Вася", "dv:6")],
                        [("👤 Лена <b>", "dv:8"), ("👤 Петя", "dv:7")]]
    assert rows[-1] == [("📂 Открыть в Pocket", "https://pocket.test/accounts?debt=all")]


async def test_debts_vnd_only_is_exact(feed, session, backend):
    backend.on("GET", "/debts", fx("debts_vnd_only"))
    await feed(message_update("/debts"))
    text = plain(session.sent[-1].text)
    assert "Мне должны <b>300 000 ₫</b>" in text and "Я должен" not in text
    assert "≈" not in text


async def test_debts_missing_rates_hint(feed, session, backend):
    data = fx("debts_list")
    data["totals"]["missing_rates"] = True
    data["totals"]["i_owe_vnd"] = None
    data["totals"]["i_owe_usd"] = None
    backend.on("GET", "/debts", data)
    await feed(message_update("/debts"))
    text = plain(session.sent[-1].text)
    assert "Мне должны ≈ <b>4 230 000 ₫</b>" in text
    assert "\nЯ должен\n" in text  # no total at all → just the heading
    assert text.split("\n")[-1] == "⚠️ Не для всех валют есть курс — итог ≈ неполный."


async def test_debts_many_debtors_buttons_reach_i_owe(feed, session, backend):
    """8+ debtors used to take all 8 person buttons — «Я должен · Петя» had none."""
    backend.on("GET", "/debts", _many_debts(9, 1))
    await feed(message_update("/debts"))
    sent = session.sent[-1]
    persons = [(t, d) for t, d in kb_flat(sent) if d.startswith("dv:")]
    assert len(persons) == 8
    assert persons[-1] == ("👤 Петя", "dv:7")
    assert [d for _, d in persons[:7]] == [f"dv:{100 + i}" for i in range(7)]
    text = plain(sent.text)
    assert "• Петя — 400 000 ₫ · 7 дн." in text and "… и ещё" not in text


async def test_debts_long_lists_cut_per_section(feed, session, backend):
    backend.on("GET", "/debts", _many_debts(18, 17))
    await feed(message_update("/debts"))
    sent = session.sent[-1]
    lines = plain(sent.text).split("\n")
    assert lines.count("… и ещё 3") == 1 and lines.count("… и ещё 2") == 1
    assert "• Петя — 400 000 ₫ · 7 дн." in lines
    persons = [d for _, d in kb_flat(sent) if d.startswith("dv:")]
    assert persons == [f"dv:{100 + i}" for i in range(4)] + ["dv:7"] + [
        f"dv:{200 + i}" for i in range(3)]


async def test_debts_empty(feed, session, backend):
    backend.on("GET", "/debts", fx("debts_empty"))
    await feed(message_update("/debts"))
    sent = session.sent[-1]
    assert sent.text.startswith("Долгов нет 👌")
    assert sent.reply_markup is None


async def test_debts_backend_not_ready_is_honest(feed, session, backend):
    backend.error("GET", "/debts", 501, "not_implemented", "Не реализовано")
    await feed(message_update("/debts"))
    assert session.sent[-1].text == "⚠️ Эта функция на сервере ещё не готова."
    backend.error("GET", "/debts", 503, "internal", "down")
    await feed(message_update("/debts"))
    assert session.sent[-1].text == "⚠️ Ошибка сервера. Попробуйте ещё раз."


# ------------------------------------------------------------------------------ dv: card
async def test_person_card(feed, session, backend):
    backend.on("GET", "/debts/5", fx("debt_detail"))
    upd = callback_update("dv:5", text="💸 Долги")
    await feed(upd)
    assert backend.last("GET", "/debts/5").url.params["limit"] == "10"
    card = session.sent[-1]
    lines = plain(card.text).split("\n")
    assert lines[:2] == ["👤 <b>Коля</b>", "Коля: долг тебе 300 000 ₫ · 30 дн."]
    assert lines[3] == "<b>История</b>"
    assert lines[4:6] == ["20.10 · Возврат 200 000 ₫ · долг тебе 300 000 ₫",
                          "27.09 · Дал в долг 500 000 ₫ · долг тебе 500 000 ₫"]
    assert kb_rows(card) == [[("💰 Записать возврат", "dr:5"), ("🤝 Списать остаток", "t:0")],
                             [("📂 Открыть в Pocket", "https://pocket.test/accounts?debt=5")]]
    assert session.edits == []  # the list stays, the card is a new message


async def test_person_card_forgive_goes_through_assistant(feed, session, backend):
    backend.on("GET", "/debts/5", fx("debt_detail"))
    await feed(callback_update("dv:5", text="💸 Долги"))
    card_id = 9000  # MockedSession numbers sent messages from 9000
    # a later clarify card overwrites the «last card» options — t:0 under the person card must
    # still send its own text (options are remembered per message)
    backend.on("POST", "/assistant/messages", fx("unclear"))
    await feed(message_update("перевел вчера"))
    forgive = fx("created")
    forgive["cards"][0]["title"] = "300 000 ₫ · Списан долг: Коля"
    backend.on("POST", "/assistant/messages", forgive)
    upd = callback_update("t:0", message_id=card_id, text="👤 Коля")
    await feed(upd)
    req = backend.last("POST", "/assistant/messages")
    assert backend.body(req) == {"text": "списать долг: Коля"}
    assert req.headers["Idempotency-Key"] == f"tgt:{OWNER}:{card_id}:0"
    assert plain(session.sent[-1].text).startswith("✅ 300 000 ₫ · Списан долг: Коля")
    # the clarify card keeps its own option too
    backend.on("POST", "/assistant/messages", fx("created"))
    await feed(callback_update("t:0", message_id=card_id + 1, text="🤔 Не понял"))
    assert backend.body(backend.last("POST", "/assistant/messages")) == {
        "text": "расход вчера 100к"}


async def test_person_card_settled_and_i_owe(feed, session, backend):
    backend.on("GET", "/debts/5", fx("debt_detail_settled"))
    await feed(callback_update("dv:5"))
    card = session.sent[-1]
    lines = plain(card.text).split("\n")
    assert lines[1] == "✅ Долгов нет — всё возвращено."
    assert lines[-1] == "… всего 3 — в Pocket"
    assert "27.10 · Списан 300 000 ₫ · долг закрыт" in lines
    assert all(not d.startswith(("dr:", "t:")) for _, d in kb_flat(card))
    backend.on("GET", "/debts/7", fx("debt_detail_petya"))
    await feed(callback_update("dv:7"))
    lines = plain(session.sent[-1].text).split("\n")
    assert lines[1] == "Ты должен 1 000 000 ₫ · 7 дн."
    assert lines[-1] == "20.10 · Занял 1 000 000 ₫ · ты должен 1 000 000 ₫"


async def test_person_card_not_found(feed, session, backend):
    backend.error("GET", "/debts/99", 404, "not_found", "Долг не найден")
    await feed(callback_update("dv:99"))
    assert session.sent[-1].text == "⚠️ Не нашёл — возможно, запись уже удалили."


# ------------------------------------------------------------------------------ dr: / dra:
async def _ask_repay(feed, session, backend, debt="debt_detail", did=5, reminder_id=700):
    backend.on("GET", f"/debts/{did}", fx(debt))
    await feed(callback_update(f"dr:{did}", message_id=reminder_id, text=REMINDER,
                               reply_markup=REMINDER_KB))
    assert backend.last("GET", f"/debts/{did}").url.params["limit"] == "1"
    return session.sent[-1]


async def test_repay_prompt_then_amount(feed, session, backend):
    prompt = await _ask_repay(feed, session, backend)
    assert plain(prompt.text).startswith("💰 Сколько вернул Коля? Напиши сумму — например, "
                                         "<code>200к</code>")
    assert plain(prompt.text).endswith("Сейчас: Коля: долг тебе 300 000 ₫.")
    assert kb_rows(prompt) == [[("✅ Весь долг", "dra:5"), ("✖️ Отмена", "z")]]
    backend.on("POST", "/assistant/messages", fx("created_debt_excess"))
    await feed(message_update("200к с кошелька", message_id=701))
    req = backend.last("POST", "/assistant/messages")
    assert backend.body(req) == {"text": "возврат долга: Коля 200к с кошелька"}
    assert req.headers["Idempotency-Key"] == f"tg:{OWNER}:701"
    assert plain(session.sent[-1].text).startswith("✅ 400 000 ₫ · Возврат долга: Коля")
    # the consumed prompt and the answered reminder lose their buttons
    gone = stripped(session)
    assert 700 in gone and len(gone) == 2


async def test_repay_prompt_words_for_all_and_verbs(feed, session, backend):
    backend.on("POST", "/assistant/messages", fx("created_debt_lend"))
    await _ask_repay(feed, session, backend)
    await feed(message_update("Весь долг"))
    assert backend.body(backend.last("POST", "/assistant/messages")) == {
        "text": "возврат долга: Коля"}
    await _ask_repay(feed, session, backend)
    await feed(message_update("вернул 50 usdt"))
    assert backend.body(backend.last("POST", "/assistant/messages")) == {
        "text": "возврат долга: Коля 50 usdt"}


async def test_repay_prompt_other_text_is_an_ordinary_message(feed, session, backend):
    await _ask_repay(feed, session, backend)
    backend.on("POST", "/assistant/messages", fx("created"))
    await feed(message_update("кофе 85к"))
    assert backend.body(backend.last("POST", "/assistant/messages")) == {"text": "кофе 85к"}
    assert 700 not in stripped(session)  # nothing was returned → the reminder keeps buttons
    # a command drops the prompt too: the next amount is an ordinary message
    await _ask_repay(feed, session, backend)
    backend.on("GET", "/debts", fx("debts_empty"))
    await feed(message_update("/debts"))
    await feed(message_update("200к"))
    assert backend.body(backend.last("POST", "/assistant/messages")) == {"text": "200к"}


async def test_repay_prompt_cancel(feed, session, backend):
    await _ask_repay(feed, session, backend)
    await feed(callback_update("z", message_id=9001, text="💰 Сколько вернул Коля?"))
    assert session.edits[-1].text == "✖️ Отменено."
    backend.on("POST", "/assistant/messages", fx("created"))
    await feed(message_update("200к"))
    assert backend.body(backend.last("POST", "/assistant/messages")) == {"text": "200к"}


async def test_repay_prompt_i_owe(feed, session, backend):
    prompt = await _ask_repay(feed, session, backend, debt="debt_detail_petya", did=7)
    text = plain(prompt.text)
    assert text.startswith("💰 Сколько ты вернул? Долг: Петя.")
    assert text.endswith("Сейчас: ты должен 1 000 000 ₫.")


async def test_repay_on_settled_debt(feed, session, backend):
    backend.on("GET", "/debts/5", fx("debt_detail_settled"))
    await feed(callback_update("dr:5", message_id=700, text=REMINDER, reply_markup=REMINDER_KB))
    assert "Долг уже закрыт" in toasts(session)
    edit = session.edits[-1]
    assert edit.text.endswith("✅ Долг уже закрыт — записывать нечего.")
    assert edit.reply_markup is None
    assert session.sent == []


async def test_repay_all_button(feed, session, backend):
    await _ask_repay(feed, session, backend)
    n_gets = len(backend.find("GET", "/debts/5"))
    backend.on("POST", "/assistant/messages", fx("created_debt_lend"))
    upd = callback_update("dra:5", message_id=9000, text="💰 Сколько вернул Коля?")
    await feed(upd)
    req = backend.last("POST", "/assistant/messages")
    assert backend.body(req) == {"text": "возврат долга: Коля"}
    assert req.headers["Idempotency-Key"] == f"tgdra:{OWNER}:9000"  # double tap → replay
    assert len(backend.find("GET", "/debts/5")) == n_gets  # the name came from the prompt
    edit = session.edits[-1]  # the prompt turns into the card
    assert edit.message_id == 9000
    assert plain(edit.text).startswith("✅ 500 000 ₫ · Дал в долг: Коля")
    assert 700 in stripped(session)
    # the prompt is consumed: a following amount is an ordinary message
    backend.on("POST", "/assistant/messages", fx("created"))
    await feed(message_update("200к"))
    assert backend.body(backend.last("POST", "/assistant/messages")) == {"text": "200к"}


async def test_repay_all_without_prompt_state_reads_the_name(feed, session, backend):
    backend.on("GET", "/debts/5", fx("debt_detail"))
    backend.on("POST", "/assistant/messages", fx("created_debt_lend"))
    await feed(callback_update("dra:5", message_id=800, text="💰 Сколько вернул Коля?"))
    assert backend.last("GET", "/debts/5")
    assert backend.body(backend.last("POST", "/assistant/messages")) == {
        "text": "возврат долга: Коля"}


async def test_repay_all_backend_down(feed, session, backend):
    backend.on("GET", "/debts/5", fx("debt_detail"))
    backend.error("POST", "/assistant/messages", 503, "internal", "down")
    await feed(callback_update("dra:5", message_id=800, text="💰 Сколько вернул Коля?"))
    assert session.sent[-1].text == ("⚠️ Не удалось сохранить, ничего не записано. "
                                     "Попробуйте ещё раз.")


# ------------------------------------------------------------------------------ dz:
async def test_snooze(feed, session, backend):
    backend.on("POST", "/debts/5/snooze", fx("debt_snoozed"))
    await feed(callback_update("dz:5", message_id=700, text=REMINDER, reply_markup=REMINDER_KB))
    req = backend.last("POST", "/debts/5/snooze")
    assert backend.body(req) == {"days": 7}
    assert "Напомню через 7 дней" in toasts(session)
    edit = session.edits[-1]
    assert plain(edit.text) == plain(REMINDER) + "\n\n⏰ Напомню через 7 дней."
    assert kb_rows(edit) == [[("Записать возврат", "dr:5")]]  # «Напомнить позже» is gone


async def test_snooze_unknown_debt(feed, session, backend):
    backend.error("POST", "/debts/5/snooze", 404, "not_found", "Долг не найден")
    await feed(callback_update("dz:5", message_id=700, text=REMINDER, reply_markup=REMINDER_KB))
    assert session.sent[-1].text == "⚠️ Не нашёл — возможно, запись уже удалили."


async def test_stranger_cannot_press_debt_buttons(feed, session, backend):
    for data in ("dz:5", "dr:5", "dra:5", "dv:5"):
        await feed(callback_update(data, user_id=STRANGER))
    await feed(message_update("/debts", user_id=STRANGER))
    assert backend.calls == [] and session.requests == []
    assert not session.of(SendMessage)


# ------------------------------------------------------------------------------ assistant cards
async def test_card_lend(feed, session, backend):
    backend.on("POST", "/assistant/messages", fx("created_debt_lend"))
    await feed(message_update("дал в долг Коле 500к"))
    sent = session.sent[-1]
    assert plain(sent.text) == "✅ 500 000 ₫ · Дал в долг: Коля\nКарта · Коля: долг тебе 500 000 ₫"
    assert kb_rows(sent) == [[("↩️ Отменить", "u:" + A1.replace("-", "")),
                              ("✏️ Изменить", "e:401")]]


async def test_card_lend_without_subtitle_uses_debt_brief(feed, session, backend):
    data = fx("created_debt_lend")
    data["cards"][0]["subtitle"] = None
    backend.on("POST", "/assistant/messages", data)
    await feed(message_update("дал в долг Коле 500к"))
    assert plain(session.sent[-1].text).split("\n")[1] == "Коля: долг тебе 500 000 ₫"


async def test_card_overpayment_offers_excess_button(feed, session, backend):
    backend.on("POST", "/assistant/messages", fx("created_debt_excess"))
    await feed(message_update("Коля вернул 400к"))
    sent = session.sent[-1]
    lines = plain(sent.text).split("\n")
    assert lines == ["✅ 400 000 ₫ · Возврат долга: Коля", "Карта · долг закрыт",
                     "Излишек 100 000 ₫ — теперь ты должен: Коля"]
    rows = kb_rows(sent)
    assert rows[0] == [("Излишек — мне", "t:0")]
    assert rows[1] == [("↩️ Отменить", "u:" + A2.replace("-", "")), ("✏️ Изменить", "e:402")]
    backend.on("POST", "/assistant/messages", fx("created"))
    await feed(callback_update("t:0", message_id=9000, text=sent.text))
    assert backend.body(backend.last("POST", "/assistant/messages")) == {
        "text": "списать мой долг: Коля 100к"}


async def test_card_needs_account_debt(feed, session, backend):
    backend.on("POST", "/assistant/messages", fx("needs_account_debt"))
    await feed(message_update("дал Васе в долг 50 usdt"))
    sent = session.sent[-1]
    assert plain(sent.text).split("\n") == ["💸 50 USDT · Дал в долг: Вася",
                                            "Вася: долг тебе 50 USDT · ≈ $50.00",
                                            "Долг записал. С какого счёта?"]
    flat = kb_flat(sent)
    assert flat[:2] == [("Кошелёк", "a:403:3"), ("Без счёта", "a:403:0")]
    assert ("⏳ Позже", "l:403") in flat and ("↩️ Отменить", "u:" + A1.replace("-", "")) in flat


async def test_choose_account_for_debt_inflow(feed, session, backend):
    backend.on("POST", "/transactions/406/account", fx("mutation_assign_debt_in"))
    await feed(callback_update("a:406:3", text="💸 50 USDT · Возврат долга: Вася"))
    assert backend.body(backend.last("POST", "/transactions/406/account")) == {"account_id": 3}
    edit = session.edits[-1]
    # the real side of an inflow is counter_account (Кошелёк), not «Долг: Вася»; no «Вася» twice
    assert plain(edit.text) == "✅ 50 USDT · Возврат долга: Вася\nКошелёк · ≈ $50.00 · сегодня"
    assert "Счёт: Кошелёк" in toasts(session)


async def test_answer_card_with_debts(feed, session, backend):
    """«кто мне должен» with several persons → the same sections as /debts."""
    backend.on("POST", "/assistant/messages", fx("answer_debts"))
    await feed(message_update("кто мне должен"))
    sent = session.sent[-1]
    lines = plain(sent.text).split("\n")
    assert lines == ["📊 Долги", "",
                     "Мне должны ≈ <b>4 230 000 ₫</b>",
                     "• Коля — 300 000 ₫ · 30 дн.",
                     "• Вася — 50 USDT (≈ $50.00) · 3 дн.",
                     "• Лена &lt;b&gt; — 100 USDT (≈ $100.00)", "",
                     "Я должен <b>1 200 000 ₫</b>",
                     "• Петя — 1 000 000 ₫ · 7 дн.",
                     "• Лена &lt;b&gt; — 200 000 ₫ · 2 дн."]
    rows = kb_rows(sent)
    assert rows[0] == [("👤 Коля", "dv:5"), ("👤 Вася", "dv:6")]
    assert rows[-1] == [("📈 Открыть долги", "https://pocket.test/accounts?debt=all")]


def _person(did: int, name: str, amount: str, currency: str = "VND") -> dict:
    """A DebtOut cloned from the contract fixture (Коля) with another id/name/balance."""
    import copy

    d = copy.deepcopy(fx("debts_list")["owed_to_me"][0])
    owed = not amount.startswith("-")
    d.update(id=did, name=name, account_id=100 + did,
             status="owed_to_me" if owed else "i_owe")
    d["balances"][0].update(amount=amount, currency=currency, approx_vnd=amount.lstrip("-"),
                            direction="owed_to_me" if owed else "i_owe",
                            open_since="2026-10-20")
    return d


def _many_debts(n_owed: int, n_owe: int) -> dict:
    data = fx("debts_list")
    data["owed_to_me"] = [_person(100 + i, f"Друг{i}", f"{(i + 1) * 10000}")
                          for i in range(n_owed)]
    data["i_owe"] = ([_person(7, "Петя", "-400000")]
                     + [_person(200 + i, f"Микс{i}", "-5000") for i in range(n_owe - 1)])
    data["totals"].update(owed_to_me_count=n_owed, i_owe_count=n_owe)
    return data


async def test_answer_card_many_debtors_keeps_i_owe(feed, session, backend):
    """18 people owe me, 2 I owe: «мои долги» used to cut the flat lines at 15 — the whole
    «Я должен» list (Петя, Микс0) vanished with no «… и ещё N»; lines also got the 🏷 emoji."""
    debts = _many_debts(18, 2)
    answer = fx("answer_debts")
    card = answer["cards"][0]
    card["debts"] = debts
    card["lines"] = ([{"label": d["name"], "value": "долг тебе 10 000 ₫", "icon": "HandCoins"}
                      for d in debts["owed_to_me"]]
                     + [{"label": d["name"], "value": "ты должен 5 000 ₫", "icon": "HandCoins"}
                        for d in debts["i_owe"]])
    backend.on("POST", "/assistant/messages", answer)
    await feed(message_update("мои долги"))
    sent = session.sent[-1]
    text = plain(sent.text)
    lines = text.split("\n")
    assert "🏷" not in text
    assert lines[2] == "Мне должны <b>4 230 000 ₫</b>"  # all in ₫ → exact
    assert sum(ln.startswith("• Друг") for ln in lines) == 15
    assert "… и ещё 3" in lines
    i = lines.index("Я должен <b>1 200 000 ₫</b>")
    assert lines[i + 1:i + 3] == ["• Петя — 400 000 ₫ · 7 дн.", "• Микс0 — 5 000 ₫ · 7 дн."]
    # the «Я должен» people get person buttons too
    assert ("👤 Петя", "dv:7") in kb_flat(sent) and ("👤 Микс0", "dv:200") in kb_flat(sent)


async def test_answer_card_one_person_keeps_lines(feed, session, backend):
    """«сколько должен Коля?»: one person (totals are global) → flat lines, no 🏷 emoji."""
    answer = fx("answer_debts")
    card = answer["cards"][0]
    kolya = fx("debts_list")["owed_to_me"][0]
    card.update(title="Долг: Коля", headline="Коля: долг тебе 300 000 ₫", headline_approx=None,
                text=None, lines=[{"label": "Коля", "value": "долг тебе 300 000 ₫",
                                   "icon": "HandCoins"}])
    card["debts"] = dict(card["debts"], owed_to_me=[kolya], i_owe=[])
    backend.on("POST", "/assistant/messages", answer)
    await feed(message_update("сколько должен Коля"))
    sent = session.sent[-1]
    assert plain(sent.text).split("\n") == ["📊 Долг: Коля <b>Коля: долг тебе 300 000 ₫</b>", "",
                                             "• Коля — долг тебе 300 000 ₫"]
    assert kb_rows(sent)[0] == [("👤 Коля", "dv:5")]


async def test_answer_card_without_debts_is_unchanged(feed, session, backend):
    backend.on("POST", "/assistant/messages", fx("answer"))
    await feed(message_update("сколько потратил сегодня"))
    assert all(not d.startswith("dv:") for _, d in kb_flat(session.sent[-1]))


# ------------------------------------------------------------------------------ lists
async def test_inbox_debt_inflow_asks_where_to(feed, session, backend):
    page = fx("page_needs_account")
    page["items"] = [fx("tx_debt_in_needs_account")]
    backend.on("GET", "/transactions", page)
    backend.on("GET", "/accounts", fx("accounts"))
    await feed(message_update("/inbox"))
    lines = plain(session.sent[-1].text).split("\n")
    assert lines[0] == "💸 50 USDT · Возврат долга: Вася"
    assert lines[-1] == "На какой счёт?"
    page["items"] = [fx("tx_debt_lend_usdt")]
    await feed(message_update("/inbox"))
    assert plain(session.sent[-1].text).split("\n")[-1] == "С какого счёта?"


async def test_last_shows_debt_operations(feed, session, backend):
    backend.on("GET", "/transactions", fx("page_last_debts"))
    await feed(message_update("/last"))
    text = plain(session.sent[-1].text)
    assert "27.10 · Списан долг: Коля — −300 000 ₫" in text
    assert "20.10 · Возврат долга: Коля — +200 000 ₫" in text
    assert "27.09 · Дал в долг: Коля — −500 000 ₫" in text


# ------------------------------------------------------------------------------ outbox
async def test_outbox_debt_reminder(bot, session, backend, api, renderer):
    from pocket_bot.outbox import process_once

    backend.on("POST", "/bot/outbox/claim", fx("outbox_debt_reminder"))
    backend.on("POST", "/bot/outbox/{id}/ack", {"ok": True})
    assert await process_once(bot, api, allowed_ids=[OWNER], renderer=renderer) == 2
    kolya, petya = session.of(SendMessage)
    assert kolya.chat_id == OWNER and kolya.parse_mode == "HTML"
    assert plain(kolya.text) == plain(REMINDER)
    assert kb_rows(kolya) == [[("Записать возврат", "dr:5"), ("Напомнить позже", "dz:5")]]
    assert kb_rows(petya) == [[("Записать возврат", "dr:7"), ("Напомнить позже", "dz:7")]]
    acks = [backend.body(r) for r in backend.find("POST", "/bot/outbox/")
            if r.url.path.endswith("/ack")]
    assert [a["ok"] for a in acks] == [True, True]


# ------------------------------------------------------------------------------ /help /settings
async def test_help_and_start_mention_debts(feed, session, backend):
    await feed(message_update("/help"))
    text = session.sent[-1].text
    assert "/debts — долги" in text and "<code>дал в долг Коле 500к</code>" in text
    assert "<code>кто мне должен</code>" in text
    await feed(message_update("/start"))
    assert "/debts" in session.sent[-1].text
    assert backend.calls == []


async def test_settings_debt_reminder_toggle(feed, session, backend):
    backend.on("GET", "/settings", fx("settings"))
    backend.on("GET", "/recurring", [])
    await feed(message_update("/settings"))
    sent = session.sent[-1]
    assert "▫️ Напоминать, если долгу 30 дн. и больше" in sent.text
    assert ("▫️ Напоминать о долгах", "g:b") in kb_flat(sent)
    backend.on("PATCH", "/settings", dict(fx("settings"), debt_reminders_enabled=True,
                                          version=4))
    await feed(callback_update("g:b", text=sent.text))
    assert backend.body(backend.last("PATCH", "/settings")) == {
        "version": 3, "debt_reminders_enabled": True}
    assert "Включено" in toasts(session)
    assert ("✅ Напоминать о долгах", "g:b") in kb_flat(session.edits[-1])


async def test_settings_without_debt_fields_has_no_toggle(feed, session, backend):
    st = fx("settings")
    st.pop("debt_reminders_enabled")
    st.pop("debt_reminder_days")
    backend.on("GET", "/settings", st)
    backend.on("GET", "/recurring", [])
    await feed(message_update("/settings"))
    assert "долг" not in session.sent[-1].text.lower()
    assert ("▫️ Напоминать о долгах", "g:b") not in kb_flat(session.sent[-1])


# ------------------------------------------------------------------------------ units
def _row_ids(rows) -> list[str]:
    return [b.callback_data for row in rows for b in row]


def test_debt_person_rows_fair_split():
    from pocket_bot.render import debt_person_rows

    def ps(*ids):
        return [{"id": i, "name": f"P{i}"} for i in ids]

    # only one side → it takes every slot
    assert _row_ids(debt_person_rows({"owed_to_me": ps(*range(1, 12))})) == [
        f"dv:{i}" for i in range(1, 9)]
    assert _row_ids(debt_person_rows({"i_owe": ps(*range(1, 12))})) == [
        f"dv:{i}" for i in range(1, 9)]
    # many debtors, few creditors → creditors keep their buttons
    assert _row_ids(debt_person_rows({"owed_to_me": ps(*range(1, 19)),
                                      "i_owe": ps(50, 51)})) == [
        *(f"dv:{i}" for i in range(1, 7)), "dv:50", "dv:51"]
    # few debtors, many creditors → the debtors' unused half goes to creditors
    assert _row_ids(debt_person_rows({"owed_to_me": ps(1, 2),
                                      "i_owe": ps(*range(50, 60))})) == [
        "dv:1", "dv:2", *(f"dv:{i}" for i in range(50, 56))]
    # both long → half and half, 2 per row
    rows = debt_person_rows({"owed_to_me": ps(*range(1, 20)), "i_owe": ps(*range(50, 70))})
    assert [len(r) for r in rows] == [2, 2, 2, 2]
    assert _row_ids(rows) == ["dv:1", "dv:2", "dv:3", "dv:4",
                              "dv:50", "dv:51", "dv:52", "dv:53"]


def test_debt_person_rows_person_in_both_lists():
    from pocket_bot.render import debt_person_rows

    def ps(*ids):
        return [{"id": i, "name": f"P{i}"} for i in ids]

    # shown once (as a debtor); the freed slot goes to the next debtor
    assert _row_ids(debt_person_rows({"owed_to_me": ps(*range(1, 11)),
                                      "i_owe": ps(2, 50)})) == [
        "dv:1", "dv:2", "dv:3", "dv:4", "dv:5", "dv:6", "dv:7", "dv:50"]
    assert _row_ids(debt_person_rows({"owed_to_me": ps(*range(1, 11)),
                                      "i_owe": ps(2, 50, 51, 52, 53)})) == [
        "dv:1", "dv:2", "dv:3", "dv:4", "dv:50", "dv:51", "dv:52", "dv:53"]
    # a debtor cut off the first half still gets a button from «Я должен»
    assert _row_ids(debt_person_rows({"owed_to_me": ps(*range(1, 11)),
                                      "i_owe": ps(9, 50)})) == [
        "dv:1", "dv:2", "dv:3", "dv:4", "dv:5", "dv:6", "dv:9", "dv:50"]
    # junk ids are skipped
    assert _row_ids(debt_person_rows({"owed_to_me": [{"id": None}, {"id": "x"}, *ps(1)]})) == [
        "dv:1"]


def test_repay_text():
    from pocket_bot.debts import repay_text

    assert repay_text("Коля", "200к") == "возврат долга: Коля 200к"
    assert repay_text("Коля", "  1.5м   налом ") == "возврат долга: Коля 1.5м налом"
    assert repay_text("Коля", "$50") == "возврат долга: Коля $50"
    assert repay_text("Коля", "Вернула 50 USDT") == "возврат долга: Коля 50 USDT"
    for word in ("весь", "Всё", "все", "весь долг", "полностью", "целиком."):
        assert repay_text("Коля", word) == "возврат долга: Коля"
    for other in ("кофе 85к", "отмена", "", "сколько он должен?", "1" * 70):
        assert repay_text("Коля", other) is None


def test_debt_state_words():
    from pocket_bot.fmt import debt_state

    assert plain(debt_state("300000", "VND", "Коля")) == "Коля: долг тебе 300 000 ₫"
    assert plain(debt_state("-1000000", "VND", "Петя")) == "ты должен 1 000 000 ₫"
    assert debt_state("0", "VND") == "долг закрыт" and debt_state(None, "VND") == "долг закрыт"
    assert plain(debt_state("50", "USDT")) == "долг тебе 50 USDT"

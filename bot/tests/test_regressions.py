"""Bot regression tests: «Отменить все», /undo after «Вернуть», /inbox account
choices for $, recurring «Записать» without an account, /settings schedule, duplicated lines."""

from __future__ import annotations

import copy

import pytest
from aiogram.methods import AnswerCallbackQuery

from pocket_bot.render import Renderer, action_is_redo, undo_summary_parts
from pocket_bot.views import settings_view
from tests.conftest import OWNER, callback_update, fx, kb_flat, kb_rows, message_update

N = "\u00a0"
A1 = "6b0c0d3e-1111-4a2b-9c3d-000000000001"
A2 = "6b0c0d3e-1111-4a2b-9c3d-000000000002"
A3 = "6b0c0d3e-1111-4a2b-9c3d-000000000003"
B1 = "7c1d0e4f-2222-4b3c-8d4e-000000000001"  # «Отменить» of A1
C1 = "9e3f2061-4444-4d5e-8f60-000000000001"  # «Вернуть» (undo of B1)
D1 = "a1b2c3d4-5555-4e6f-8a70-000000000001"  # undo of C1
SOK = "6b0c0d3e-1111-4a2b-9c3d-0000000000aa"  # an older, unrelated operation


def plain(text: str) -> str:
    return text.replace(N, " ")


def hx(u: str) -> str:
    return u.replace("-", "")


def T(rd) -> str:
    return rd.text.replace(N, " ")


def flat(rd) -> list[tuple[str, str]]:
    if rd.markup is None:
        return []
    return [(b.text, b.callback_data or b.url or "") for r in rd.markup.inline_keyboard for b in r]


def action(aid: str, kind: str, summary: str, *, undoable: bool = True) -> dict:
    return {"id": aid, "kind": kind, "summary": summary, "source": "telegram",
            "input_text": None, "entity_refs": [{"type": "transaction", "id": 151}],
            "is_undoable": undoable, "undone_at": None, "created_at": "2026-10-27T07:40:00Z"}


def many_with_one_undo_button() -> dict:
    """Real backend shape: card.action_ids has 3 ids, the undo button only the first."""
    data = fx("created_many")
    card = data["cards"][0]
    undo_btns = [b for b in card["buttons"] if b["action"]["kind"] == "undo"]
    card["buttons"] = [undo_btns[0]] + [b for b in card["buttons"]
                                        if b["action"]["kind"] != "undo"]
    assert card["action_ids"] == [A1, A2, A3]
    return data


# ------------------------------------------------------------------ «Отменить все» (high)
def test_created_many_undo_all_takes_card_action_ids(renderer: Renderer, stash):
    rd = renderer.render_response(many_with_one_undo_button())[0]
    undo = next(b for b in flat(rd) if b[0] == "↩️ Отменить все")
    assert undo[1].startswith("U:")
    assert stash.get(undo[1][2:]) == [A1, A2, A3]


def test_created_many_undo_all_without_buttons_and_dedup(renderer: Renderer, stash):
    data = fx("created_many")
    card = data["cards"][0]
    card["buttons"] = []
    rd = renderer.render_response(data)[0]
    undo = next(b for b in flat(rd) if b[0] == "↩️ Отменить все")
    assert stash.get(undo[1][2:]) == [A1, A2, A3]
    # ids only in buttons are still kept (fallback), no duplicates
    data = fx("created_many")
    data["cards"][0]["action_ids"] = [A1]
    rd = renderer.render_response(data)[0]
    undo = next(b for b in flat(rd) if b[0] == "↩️ Отменить все")
    assert stash.get(undo[1][2:]) == [A1, A2, A3]


async def test_undo_all_undoes_every_operation(feed, session, backend):
    backend.on("POST", "/assistant/messages", many_with_one_undo_button())
    await feed(message_update("обед 121к, метро 46к и чай 31к"))
    undo_cb = next(cbd for t, cbd in kb_flat(session.sent[-1]) if t == "↩️ Отменить все")
    backend.on("POST", "/actions/{id}/undo", fx("undo_result"))
    await feed(callback_update(undo_cb))
    undone = sorted(c.url.path.rsplit("/", 2)[-2] for c in backend.find("POST", "/actions/"))
    assert undone == [A1, A2, A3]
    assert session.edits[-1].text == "↩️ Отменено: 3 операции"


# ------------------------------------------------------------------ /undo (high)
def test_undo_summary_parity():
    assert undo_summary_parts("Отменено: 150 000 ₫ · Еда") == (False, "150 000 ₫ · Еда")
    assert undo_summary_parts("Возвращено: X") == (True, "X")
    assert undo_summary_parts("Возвращено: Возвращено: X") == (False, "X")
    assert undo_summary_parts("Возвращено: Возвращено: Возвращено: X") == (True, "X")
    assert undo_summary_parts("Еда 150 000 ₫") == (None, "Еда 150 000 ₫")
    assert action_is_redo(action(C1, "undo", "Возвращено: X"))
    assert not action_is_redo(action(B1, "undo", "Отменено: X"))
    assert not action_is_redo(action(A1, "create_transaction", "Возвращено: X"))


async def test_undo_command_after_redo_undoes_the_redo(feed, session, backend):
    """«еда 151к» → Отменить → Вернуть → /undo: cancels «еда 151к» again, «сок 20к» stays."""
    backend.on("GET", "/actions", [action(C1, "undo", "Возвращено: 151 000 ₫ · Еда"),
                                   action(SOK, "create_transaction", "20 000 ₫ · Еда")])
    # current backend stacks the prefix when a redo is undone
    backend.on("POST", f"/actions/{C1}/undo", {
        "status": "undone", "undone_action_id": C1, "undo_action_id": D1,
        "summary": "Возвращено: Возвращено: 151 000 ₫ · Еда", "changed": ["transactions"]})
    await feed(message_update("/undo", message_id=901))
    posts = backend.find("POST", "/actions/")
    assert [p.url.path for p in posts] == [f"/api/v1/actions/{C1}/undo"]
    assert posts[0].headers["Idempotency-Key"] == f"tg:{OWNER}:901"
    sent = session.sent[-1]
    assert plain(sent.text) == "↩️ Отменено: 151 000 ₫ · Еда"
    assert kb_rows(sent) == [[("↪️ Вернуть", f"u:{hx(D1)}")]]


async def test_undo_command_last_is_cancel_does_not_touch_older(feed, session, backend):
    backend.on("GET", "/actions", [action(B1, "undo", "Отменено: 151 000 ₫ · Еда"),
                                   action(SOK, "create_transaction", "20 000 ₫ · Еда")])
    await feed(message_update("/undo"))
    assert backend.find("POST", "/actions/") == []
    sent = session.sent[-1]
    assert plain(sent.text) == "↩️ Последнее действие уже отменено: 151 000 ₫ · Еда"
    assert kb_rows(sent) == [[("↪️ Вернуть", f"u:{hx(B1)}")]]
    # the offered «Вернуть» is a redo
    backend.on("POST", f"/actions/{B1}/undo", {
        "status": "undone", "undone_action_id": B1, "undo_action_id": C1,
        "summary": "Возвращено: 151 000 ₫ · Еда", "changed": ["transactions"]})
    await feed(callback_update(f"u:{hx(B1)}", text=sent.text))
    assert plain(session.edits[-1].text) == "↪️ Возвращено: 151 000 ₫ · Еда"
    assert session.of(AnswerCallbackQuery)[-1].text == "Возвращено"


async def test_undo_command_plain_last_and_not_undoable(feed, session, backend):
    backend.on("GET", "/actions", fx("actions_recent"))
    backend.on("POST", "/actions/{id}/undo", fx("undo_result"))
    await feed(message_update("/undo"))
    assert backend.last("POST", "/actions/").url.path.endswith(f"/actions/{A1}/undo")
    assert plain(session.sent[-1].text) == "↩️ Отменено: 150 000 ₫ · Еда"
    backend.calls.clear()
    backend.on("GET", "/actions", [action(A2, "settings", "Настройки", undoable=False),
                                   action(SOK, "create_transaction", "20 000 ₫ · Еда")])
    await feed(message_update("/undo"))
    assert backend.find("POST", "/actions/") == []
    assert session.sent[-1].text.startswith("⚠️ Последнее действие отменить нельзя")


async def test_undo_button_after_redo_says_cancelled(feed, session, backend):
    """↩️ under «↪️ Возвращено…»: backend answers «Возвращено: Возвращено: X» — show a cancel."""
    backend.on("POST", f"/actions/{C1}/undo", {
        "status": "undone", "undone_action_id": C1, "undo_action_id": D1,
        "summary": "Возвращено: Возвращено: 151 000 ₫ · Еда", "changed": ["transactions"]})
    await feed(callback_update(f"u:{hx(C1)}"))
    edit = session.edits[-1]
    assert plain(edit.text) == "↩️ Отменено: 151 000 ₫ · Еда"
    assert kb_rows(edit) == [[("↪️ Вернуть", f"u:{hx(D1)}")]]
    assert session.of(AnswerCallbackQuery)[-1].text == "Отменено"


@pytest.mark.parametrize(("summary", "redo", "expected"), [
    ("Уже отменено: Еда 150 000 ₫", False, "↩️ Уже отменено: Еда 150 000 ₫"),
    ("Уже отменено: Отменено: 150 000 ₫ · Еда", True, "↪️ Уже возвращено: 150 000 ₫ · Еда"),
    ("Уже отменено: Возвращено: 150 000 ₫ · Еда", False, "↩️ Уже отменено: 150 000 ₫ · Еда"),
])
def test_already_undone_texts(renderer: Renderer, summary, redo, expected):
    res = {"status": "already_undone", "undone_action_id": A1, "undo_action_id": B1,
           "summary": summary, "changed": []}
    rd = renderer.undo_result(res, redo=redo)
    assert T(rd) == expected


# ------------------------------------------------------------------ /inbox accounts for $ (medium)
def usd_accounts() -> list[dict]:
    accs = fx("accounts")  # Карта(VND,1) Кошелёк(multi,3) Копилка(multi,4) Карта RUB(RUB,7)
    cash = dict(copy.deepcopy(accs[0]), id=2, name="Наличные", sort_order=2, aliases=["налом"],
                default_for_currencies=[])
    old = dict(copy.deepcopy(accs[0]), id=9, name="Старый", sort_order=9, is_archived=True)
    return accs[:1] + [cash] + accs[1:] + [old]


def usd_tx() -> dict:
    return dict(fx("tx_usdt"), id=694, currency="USD", amount="12", amount_usd="12",
                created_action_id=A1)


def test_inbox_usd_offers_fiat_accounts_like_the_card(renderer: Renderer):
    rd = renderer.needs_account_from_tx(usd_tx(), usd_accounts())
    names = [t for t, cbd in flat(rd) if cbd.startswith("a:")]
    # backend account_options order: holders of USD, multi-asset wallets, fiat accounts
    assert names == ["Кошелёк", "Копилка", "Карта", "Наличные", "Карта RUB", "Без счёта"]
    assert ("Карта", "a:694:1") in flat(rd)
    assert "Старый" not in names


def test_inbox_crypto_still_only_wallets(renderer: Renderer):
    rd = renderer.needs_account_from_tx(fx("tx_usdt"), usd_accounts())
    names = [t for t, cbd in flat(rd) if cbd.startswith("a:")]
    assert names == ["Кошелёк", "Копилка", "Без счёта"]


async def test_inbox_command_usd(feed, session, backend):
    page = fx("page_needs_account")
    page["items"] = [usd_tx()]
    backend.on("GET", "/transactions", page)
    backend.on("GET", "/accounts", usd_accounts())
    await feed(message_update("/inbox"))
    assert ("Карта", "a:694:1") in kb_flat(session.sent[-1])


# ------------------------------------------------------------------ recurring without account
def hetzner_needs_account() -> dict:
    res = fx("mutation_assign")
    res["action_id"] = A2
    res["transaction"].update(
        id=9, amount="45", currency="USD", amount_usd="45", account=None, account_amount=None,
        account_asset=None, status="needs_account", recurring_id=12, created_action_id=A2)
    return res


async def test_recurring_record_without_account_asks_which(feed, session, backend):
    backend.on("POST", "/recurring/12/record", hetzner_needs_account())
    backend.on("GET", "/accounts", usd_accounts())
    await feed(callback_update("r:12", message_id=321, text="⏰ Сегодня обычно платишь"))
    edit = session.edits[-1]
    assert edit.message_id == 321
    lines = plain(edit.text).split("\n")
    assert lines[0] == "💸 $45.00 · Подписки"
    assert lines[-1] == "Записал в расходы. С какого счёта?"
    fl = kb_flat(edit)
    assert ("Карта", "a:9:1") in fl and ("Кошелёк", "a:9:3") in fl and ("Без счёта", "a:9:0") in fl
    assert ("⏳ Позже", "l:9") in fl and ("↩️ Отменить", f"u:{hx(A2)}") in fl
    assert not edit.text.startswith("✅")


async def test_recurring_other_amount_without_account_asks_which(feed, session, backend):
    await feed(callback_update("m:12", message_id=322, text="⏰ Сегодня обычно платишь"))
    backend.on("POST", "/recurring/12/record", hetzner_needs_account())
    backend.on("GET", "/accounts", usd_accounts())
    await feed(message_update("50$", message_id=323))
    sent = session.sent[-1]
    assert plain(sent.text).endswith("С какого счёта?")
    assert ("Карта", "a:9:1") in kb_flat(sent)


async def test_recurring_record_accounts_down_still_offers_choice(feed, session, backend):
    backend.on("POST", "/recurring/12/record", hetzner_needs_account())
    backend.error("GET", "/accounts", 503, "internal")
    await feed(callback_update("r:12", text="⏰ Сегодня обычно платишь"))
    fl = kb_flat(session.edits[-1])
    assert ("Без счёта", "a:9:0") in fl and ("⏳ Позже", "l:9") in fl


# ------------------------------------------------------------------ /settings schedule (low)
def test_settings_shows_real_schedule(renderer: Renderer):
    st = dict(fx("settings"), report_daily_time="22:00:00", report_weekly_day=6,
              report_weekly_time="21:00:00")
    text = settings_view(renderer, st, []).text
    assert "Месячный — 1-го числа в 10:00" in text
    assert "Ежедневный — в 22:00" in text
    assert "Недельный — по воскресеньям в 21:00" in text
    st.update(report_time="09:30:00", report_daily_time="08:05:00", report_weekly_day=0,
              report_weekly_time="9:15")
    text = settings_view(renderer, st, []).text
    assert "1-го числа в 09:30" in text and "Ежедневный — в 08:05" in text
    assert "Недельный — по понедельникам в 09:15" in text


def test_settings_defaults_match_backend_defaults(renderer: Renderer):
    st = fx("settings")  # no daily/weekly fields at all
    for k in ("report_daily_time", "report_weekly_day", "report_weekly_time"):
        st.pop(k, None)
    text = settings_view(renderer, st, None).text
    assert "Ежедневный — в 22:00" in text and "по воскресеньям в 21:00" in text
    assert "понедельникам" not in text


# ------------------------------------------------------------------ duplicated lines (low)
def test_updated_card_shows_change_once(renderer: Renderer):
    data = fx("updated")
    card = data["cards"][0]
    card.update(title="41 000 ₫ · Такси/транспорт", subtitle="Изменено: 31 000 ₫ → 41 000 ₫",
                changes=["31 000 ₫ → 41 000 ₫"])
    text = T(renderer.render_response(data)[0])
    assert text == "✅ 41 000 ₫ · Такси/транспорт\nИзменено: 31 000 ₫ → 41 000 ₫"
    # subtitle without the change → the change line is still added
    text = T(renderer.render_response(fx("updated"))[0])
    assert text.count("Изменено") == 1 and text.endswith("Изменено: 150 000 ₫ → 250 000 ₫")


def test_preview_delete_has_no_duplicate_lines(renderer: Renderer):
    data = fx("preview")
    card = data["cards"][0]
    card.update(operation="bulk_delete", title="Удалю 2 операции", subtitle="удаление",
                change_summary="удаление")
    text = T(renderer.render_response(data)[0])
    assert "удаление" not in text
    assert text.startswith("🗑 Удалю 2 операции\n\n<pre>")
    # equal subtitle/change_summary for a bulk update → shown once
    data = fx("preview")
    data["cards"][0]["subtitle"] = data["cards"][0]["change_summary"]
    text = T(renderer.render_response(data)[0])
    assert text.count("категория: Прочее → Кофе") == 1

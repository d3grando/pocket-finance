"""Card rendering against contract fixtures generated from the backend schemas (DESIGN.md §12)."""

from __future__ import annotations

from pocket_bot import callbacks as cb
from pocket_bot.render import Renderer
from tests.conftest import fx

N = " "
A1 = "6b0c0d3e-1111-4a2b-9c3d-000000000001"
A1_HEX = A1.replace("-", "")
PREVIEW_HEX = "8d2e1f50-3333-4c4d-9e5f-000000000001".replace("-", "")


def T(rd) -> str:
    """Rendered text with NBSP shown as plain spaces (NBSP itself is covered in fmt tests)."""
    return rd.text.replace(N, " ")


def rows(rd):
    if rd.markup is None:
        return []
    return [[(T(b), b.callback_data or b.url) for b in r] for r in rd.markup.inline_keyboard]


def one(renderer: Renderer, name: str, msg_id: int | None = 77):
    out = renderer.render_response(fx(name), msg_id=msg_id)
    assert len(out) == 1
    return out[0]


def test_created_compact_with_undo_edit(renderer):
    rd = one(renderer, "created")
    assert T(rd) == "✅ 150 000 ₫ · Еда\nКарта · сегодня, 14:32"
    assert rows(rd) == [[("↩️ Отменить", f"u:{A1_HEX}"), ("✏️ Изменить", "e:101")]]


def test_created_new_category_has_move_button(renderer):
    rd = one(renderer, "created_new_category")
    lines = T(rd).split("\n")
    assert lines[0] == "✅ 3 TON · Софт и AI 🆕"
    assert lines[1] == "Кошелёк · @alice · «аккаунт GPT Pro» · ≈ $16.20"
    assert lines[2] == "Новая категория. Запомню: @alice → Софт и AI"
    assert rows(rd) == [
        [("↩️ Отменить", f"u:{A1_HEX}"), ("✏️ Изменить", "e:124")],
        [("🗂 В другую категорию", "c:124")],
    ]


def test_created_new_category_button_added_when_backend_forgot(renderer):
    data = fx("created_new_category")
    data["cards"][0]["buttons"] = []
    rd = renderer.render_response(data)[0]
    assert ("🗂 В другую категорию", "c:124") in [b for r in rows(rd) for b in r]
    assert ("↩️ Отменить", f"u:{A1_HEX}") in [b for r in rows(rd) for b in r]


def test_created_many_undo_all_and_edit_picker(renderer, stash):
    rd = one(renderer, "created_many")
    lines = T(rd).split("\n")
    assert lines[0] == "✅ Записал 3 операции · −195 000 ₫"
    assert lines[1:] == ["• Еда — 120 000 ₫", "• Такси/транспорт — 45 000 ₫",
                         "• Кофе — 30 000 ₫"]
    (undo, edit), = rows(rd)
    assert undo[0] == "↩️ Отменить все" and undo[1].startswith("U:")
    assert len(stash.get(undo[1][2:])) == 3
    assert edit[0] == "✏️ Изменить" and edit[1].startswith("M:")
    items = stash.get(edit[1][2:])
    assert [i[0] for i in items] == [201, 202, 203]


def test_needs_account_choice(renderer):
    rd = one(renderer, "needs_account")
    assert T(rd) == "💸 15 USDT · Подписки\n≈ $15.00\nЗаписал в расходы. С какого счёта?"
    assert rows(rd) == [
        [("Кошелёк", "a:125:3"), ("Копилка", "a:125:4"), ("Без счёта", "a:125:0")],
        [("⏳ Позже", "l:125"), ("↩️ Отменить", f"u:{A1_HEX}")],
    ]


def test_preview_with_confirm_cancel(renderer):
    rd = one(renderer, "preview")
    assert T(rd).startswith("🔎 Изменю 3 операции\nкатегория: Прочее → Кофе\n\n<pre>")
    assert "21.10  Highlands" in T(rd) and "Phúc Long" in T(rd)
    assert "Итого 136 000 ₫" in T(rd)
    assert rows(rd) == [[("✅ Применить", f"p:{PREVIEW_HEX}"), ("✖️ Отмена", f"x:{PREVIEW_HEX}")]]


def test_preview_delete_variant(renderer):
    data = fx("preview")
    card = data["cards"][0]
    card["operation"] = "bulk_delete"
    card["title"] = "Удалю 3 операции"
    rd = renderer.render_response(data)[0]
    assert T(rd).startswith("🗑 Удалю 3 операции")
    assert rows(rd)[0][0] == ("🗑 Удалить", f"p:{PREVIEW_HEX}")


def test_answer_escapes_and_links(renderer):
    rd = one(renderer, "answer")
    assert T(rd).startswith("📊 Сегодня потрачено <b>−595 000 ₫</b> ≈ $22.76")
    assert "🍴 Еда — 320 000 ₫" in T(rd)
    assert "🚕 Такси/транспорт" in T(rd)
    assert "&lt;script&gt;" in T(rd) and "<script>" not in T(rd)
    assert "… и ещё 2" in T(rd)
    assert rows(rd) == [[("📈 Открыть в аналитике",
                          "https://pocket.test/analytics?period=today")]]


def test_unclear_options_become_send_text(renderer):
    rd = one(renderer, "unclear")
    assert T(rd) == "🤔 Не понял, что записать\n«перевел вчера» — не хватает суммы."
    assert rd.options == ["расход вчера 100к", "перевод вчера 100к"]
    assert rows(rd) == [[("Это расход…", "t:0")], [("Это перевод…", "t:1")],
                        [("✍️ Вручную", "https://pocket.test/add")]]


def test_error_ai_unavailable(renderer):
    rd = one(renderer, "error_ai", msg_id=77)
    assert T(rd).split("\n")[0] == "⚠️ AI сейчас недоступен"
    assert "Ничего не записано" in T(rd)
    assert "<code>кофе 85к</code>" in T(rd)
    assert rows(rd) == [[("🔁 Повторить", "y:77:n")], [("✍️ Вручную", "https://pocket.test/add")]]


def test_undone_card_marks_redo(renderer):
    rd = one(renderer, "undone")
    assert T(rd) == "↩️ Отменено: 150 000 ₫ · Еда"
    redo_hex = "7c1d0e4f-2222-4b3c-8d4e-000000000001".replace("-", "")
    assert rows(rd) == [[("↪️ Вернуть", f"u:{redo_hex}")]]
    assert renderer.is_redo(redo_hex)


def test_deleted_updated_applied_balance(renderer):
    d = one(renderer, "deleted")
    assert T(d).startswith("🗑 Удалил: 150 000 ₫ · Еда")
    assert rows(d)[0][0][0] == "↪️ Вернуть"
    u = one(renderer, "updated")
    assert T(u).endswith("Изменено: 150 000 ₫ → 250 000 ₫")
    a = one(renderer, "applied")
    assert T(a) == "✅ Изменено 3 операции"
    assert rows(a)[0][0][0] == "↩️ Отменить"
    b = one(renderer, "balance")
    assert T(b).startswith("🏦 Карта: 5")


def test_title_html_is_escaped(renderer):
    data = fx("created")
    data["cards"][0]["title"] = "<i>150</i> & co"
    data["cards"][0]["subtitle"] = "a<b"
    rd = renderer.render_response(data)[0]
    assert T(rd).startswith("✅ &lt;i&gt;150&lt;/i&gt; &amp; co\na&lt;b")


def test_unknown_card_kind_and_empty_response(renderer):
    rd = renderer.render_card({"kind": "future", "title": "Новое <x>", "buttons": []})
    assert T(rd) == "ℹ️ Новое &lt;x&gt;"
    out = renderer.render_response({"cards": []})
    assert out[0].text.startswith("🤔")


def test_tx_done_and_inbox_card(renderer):
    tx = fx("mutation_assign")["transaction"]
    rd = renderer.tx_done(tx, "6b0c0d3e-1111-4a2b-9c3d-000000000002")
    assert T(rd).split("\n")[0] == "✅ 15 USDT · Подписки"
    assert T(rd).split("\n")[1].startswith("Кошелёк · ≈ $15.00")
    inbox = renderer.needs_account_from_tx(fx("tx_usdt"), fx("accounts"))
    flat = [b for r in rows(inbox) for b in r]
    # multi-asset wallets first (Кошелёк has USDT), RUB card and Карта (VND) are not offered
    assert flat[0] == ("Кошелёк", "a:125:3")
    assert ("Копилка", "a:125:4") in flat and ("Без счёта", "a:125:0") in flat
    assert not any(t == "Карта RUB" or t == "Карта" for t, _ in flat)
    assert ("⏳ Позже", "l:125") in flat


def test_category_picker(renderer):
    kb = renderer.category_picker(124, fx("suggest"))
    r = [[(T(b), b.callback_data) for b in row] for row in kb.inline_keyboard]
    assert r[0] == [("🔁 Подписки", "s:124:7"), ("🖥 Серверы/инфра", "s:124:14")]
    assert len([b for row in r[:-1] for b in row]) == 6
    assert r[-1] == [("🔍 Другая…", cb.category_other(124)), ("‹ Назад", cb.category_back(124))]


def test_long_text_is_clipped(renderer):
    data = fx("answer")
    data["cards"][0]["text"] = "\n".join(["строка " * 20] * 100)
    rd = renderer.render_response(data)[0]
    assert len(T(rd)) <= 4096 and T(rd).endswith("…")

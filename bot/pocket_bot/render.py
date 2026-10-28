"""Card → Telegram message (HTML) + inline keyboard. DESIGN.md §12, API.md §9, §13.

Backend cards carry ready Russian ``title``/``subtitle``/``lines``; the bot adds the emoji marker
by ``kind`` and escapes every string (``fmt.esc``). Buttons are mapped by ``action.kind`` to the
callback table in ``callbacks.py``; ``open``/``manual`` become URL buttons to PUBLIC_URL.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pocket_bot import callbacks as cb
from pocket_bot.fmt import (
    DEBT_INFLOW_OPS,
    DEBT_OUTFLOW_OPS,
    FIAT_CODES,
    category_emoji,
    debt_state,
    esc,
    format_money,
    human_date,
    ops_word,
    short_date,
    to_decimal,
    tx_amount,
    tx_category_name,
    tx_debt,
    tx_label,
)
from pocket_bot.stash import Stash

TG_TEXT_LIMIT = 4096
EXAMPLES = "Пример: <code>такси 70к</code>, <code>получил 300 USDT от клиента</code>"
SIMPLE_EXAMPLES = "Простые записи работают: <code>кофе 85к</code>, <code>такси 150к вчера</code>"

Btn = InlineKeyboardButton


@dataclass
class Rendered:
    text: str
    markup: InlineKeyboardMarkup | None = None
    options: list[str] = field(default_factory=list)  # send_text texts, index = t:{n}


def clip(text: str, limit: int = TG_TEXT_LIMIT - 96) -> str:
    """Keep under Telegram's 4096-char limit, cutting on a line boundary."""
    if len(text) <= limit:
        return text
    out: list[str] = []
    size = 0
    for line in text.split("\n"):
        if size + len(line) + 1 > limit:
            break
        out.append(line)
        size += len(line) + 1
    # never leave an unclosed <pre>
    body = "\n".join(out)
    if body.count("<pre>") > body.count("</pre>"):
        body += "</pre>"
    return body + "\n…"


def markup(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup | None:
    rows = [r for r in rows if r]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def chunk(items: list[InlineKeyboardButton], size: int) -> list[list[InlineKeyboardButton]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


MAX_PERSON_BUTTONS = 8


def _debt_persons(items: Any, skip: set[int]) -> list[tuple[int, str]]:
    """DebtOut list → unique ``(id, name)`` in order, without ids in ``skip``."""
    out: list[tuple[int, str]] = []
    for d in items or []:
        try:
            did = int(d.get("id"))
        except (AttributeError, TypeError, ValueError):
            continue
        if did in skip or any(did == o for o, _ in out):
            continue
        out.append((did, str(d.get("name") or "—")))
    return out


def debt_person_rows(debts: dict[str, Any] | None,
                     limit: int = MAX_PERSON_BUTTONS) -> list[list[InlineKeyboardButton]]:
    """DebtsOut → «👤 Коля» buttons ``dv:{id}`` (owed_to_me first, then i_owe; a person listed in
    both is shown once), 2 per row, at most ``limit``.

    The buttons are shared fairly: each list gets up to half of ``limit`` and the other list
    takes what is left — so 18 debtors never push every «Я должен» person off the keyboard."""
    src = debts or {}
    owed = _debt_persons(src.get("owed_to_me"), set())
    take = min(len(owed), max(limit - len(_debt_persons(src.get("i_owe"), set())), limit // 2))
    first = owed[:take]
    owe = _debt_persons(src.get("i_owe"), {did for did, _ in first})[:limit - take]
    # slots left when a person in both lists already has a «Мне должны» button
    taken = {did for did, _ in owe}
    more = [p for p in owed[take:] if p[0] not in taken][:limit - take - len(owe)]
    return chunk([Btn(text=f"👤 {name}", callback_data=cb.debt_view(did))
                  for did, name in first + more + owe], 2)


def debt_brief_line(brief: Any) -> str | None:
    """Card ``debt`` (DebtBrief) → «Коля: долг тебе 300 000 ₫» / «ты должен …» / «долг закрыт»."""
    if not isinstance(brief, dict) or not brief.get("currency"):
        return None
    return debt_state(brief.get("balance_after"), brief.get("currency"), brief.get("name"))


_CANCEL_PREFIX = "Отменено: "
_REDO_PREFIX = "Возвращено: "
_ALREADY_PREFIX = "Уже отменено: "


def undo_summary_parts(summary: Any) -> tuple[bool | None, str]:
    """Backend undo summary → (is_redo, base text).

    «Отменено: X» → (False, X), «Возвращено: X» → (True, X), no prefix → (None, text).
    Undoing a «Вернуть» currently comes back as «Возвращено: Возвращено: X» (the backend stacks
    the prefix), so the parity of stacked «Возвращено: » decides: odd → redo, even → cancel."""
    s = str(summary or "").strip()
    n_redo = 0
    while s.startswith(_REDO_PREFIX):
        n_redo += 1
        s = s[len(_REDO_PREFIX):].lstrip()
    cancel = False
    while s.startswith(_CANCEL_PREFIX):
        cancel = True
        s = s[len(_CANCEL_PREFIX):].lstrip()
    if n_redo:
        return n_redo % 2 == 1, s
    return (False, s) if cancel else (None, s)


def action_is_redo(action: dict[str, Any]) -> bool:
    """ActionOut of kind «undo» that re-applied an operation («Вернуть»)."""
    return action.get("kind") == "undo" and undo_summary_parts(action.get("summary"))[0] is True


# main-row ordering (DESIGN §12: [Применить][Отмена], [Позже][Отменить], [Отменить][Изменить])
_MAIN_PRIORITY = {
    "confirm_preview": 0, "later": 1, "undo": 2, "redo": 2, "edit": 3,
    "cancel_preview": 4, "retry": 5,
}


class Renderer:
    def __init__(self, public_url: str, stash: Stash,
                 today: Callable[[], date] | None = None) -> None:
        self.public_url = public_url.rstrip("/")
        self.stash = stash
        self._today = today or date.today

    # ------------------------------------------------------------------ helpers
    def url(self, route: str) -> str | None:
        if not self.public_url.startswith(("http://", "https://")):
            return None
        if not route.startswith("/"):
            route = "/" + route
        return self.public_url + route

    def url_button(self, label: str, route: str) -> InlineKeyboardButton | None:
        u = self.url(route)
        return Btn(text=label, url=u) if u else None

    def today(self) -> date:
        return self._today()

    # ------------------------------------------------------------------ buttons
    def _map_button(self, b: dict[str, Any], card: dict[str, Any], msg_id: int | None,
                    options: list[str]) -> tuple[str, InlineKeyboardButton] | None:
        """→ (group, button). group: choice|main|extra. None = not representable in TG."""
        a = b.get("action") or {}
        kind = a.get("kind")
        label = str(b.get("label") or "").strip()
        tx_id = a.get("transaction_id") or (card.get("transaction") or {}).get("id")
        try:
            if kind == "undo" and a.get("action_id"):
                text = f"↪️ {label}" if label.lower().startswith("вернуть") else \
                    f"↩️ {label or 'Отменить'}"
                return "main", Btn(text=text, callback_data=cb.undo(a["action_id"]))
            if kind == "redo" and a.get("action_id"):
                return "main", Btn(text=f"↪️ {label or 'Вернуть'}",
                                   callback_data=cb.undo(a["action_id"]))
            if kind == "edit" and tx_id:
                return "main", Btn(text=f"✏️ {label or 'Изменить'}", callback_data=cb.edit(tx_id))
            if kind == "choose_account" and tx_id:
                return "choice", Btn(text=label or "Счёт",
                                     callback_data=cb.choose_account(tx_id, a.get("account_id")))
            if kind == "later" and tx_id:
                return "main", Btn(text=f"⏳ {label or 'Позже'}", callback_data=cb.later(tx_id))
            if kind == "change_category" and tx_id:
                return "extra", Btn(text="🗂 В другую категорию",
                                    callback_data=cb.change_category(tx_id))
            if kind == "set_category" and tx_id and a.get("category_id"):
                return "choice", Btn(text=label or "Категория",
                                     callback_data=cb.set_category(tx_id, a["category_id"]))
            if kind == "confirm_preview" and a.get("preview_token"):
                text = "🗑 Удалить" if card.get("operation") == "bulk_delete" else "✅ Применить"
                return "main", Btn(text=text, callback_data=cb.confirm_preview(a["preview_token"]))
            if kind == "cancel_preview" and a.get("preview_token"):
                return "main", Btn(text="✖️ Отмена",
                                   callback_data=cb.cancel_preview(a["preview_token"]))
            if kind == "send_text" and a.get("text"):
                options.append(str(a["text"]))
                return "choice", Btn(text=label or str(a["text"]),
                                     callback_data=cb.send_text(len(options) - 1))
            if kind == "retry" and msg_id is not None:
                return "main", Btn(text="🔁 Повторить", callback_data=cb.retry(msg_id, False))
            if kind == "open" and a.get("route"):
                btn = self.url_button(f"📈 {label.rstrip(' →') or 'Открыть в Pocket'}", a["route"])
                return ("extra", btn) if btn else None
            if kind == "manual":
                btn = self.url_button("✍️ Вручную", "/add")
                return ("extra", btn) if btn else None
        except (ValueError, TypeError):
            return None
        return None

    def keyboard(self, card: dict[str, Any], *, msg_id: int | None = None,
                 extra_buttons: list[dict[str, Any]] | None = None,
                 options: list[str] | None = None) -> InlineKeyboardMarkup | None:
        options = options if options is not None else []
        buttons = list(card.get("buttons") or []) + list(extra_buttons or [])
        undo_ids: list[str] = []
        choice: list[tuple[str, InlineKeyboardButton]] = []
        main: list[tuple[int, int, InlineKeyboardButton]] = []
        extra: list[InlineKeyboardButton] = []
        seen: set[str] = set()
        for i, b in enumerate(buttons):
            a = b.get("action") or {}
            if a.get("kind") == "undo" and card.get("kind") == "created_many":
                if a.get("action_id"):
                    undo_ids.append(str(a["action_id"]))
                continue
            mapped = self._map_button(b, card, msg_id, options)
            if not mapped:
                continue
            group, btn = mapped
            key = btn.callback_data or btn.url or btn.text
            if key in seen:
                continue
            seen.add(key)
            if group == "choice":
                choice.append((a.get("kind") or "", btn))
            elif group == "main":
                main.append((_MAIN_PRIORITY.get(a.get("kind") or "", 9), i, btn))
            else:
                extra.append(btn)
        if card.get("kind") == "created_many":
            # «Отменить все» must cover every operation of the message: card.action_ids is the
            # source of truth (the backend may put only the first id into the undo button);
            # ids found only in buttons are kept as a fallback.
            ids: list[str] = []
            for x in [*(card.get("action_ids") or []), *undo_ids]:
                if x and str(x) not in ids:
                    ids.append(str(x))
            if len(ids) == 1:
                main.append((2, -1, Btn(text="↩️ Отменить все", callback_data=cb.undo(ids[0]))))
            elif ids:
                key = self.stash.put(ids)
                main.append((2, -1, Btn(text="↩️ Отменить все", callback_data=cb.undo_many(key))))
        rows: list[list[InlineKeyboardButton]] = []
        accounts = [b for k, b in choice if k == "choose_account"]
        cats = [b for k, b in choice if k == "set_category"]
        texts = [b for k, b in choice if k == "send_text"]
        rows += chunk(accounts, 3) + chunk(cats, 2) + chunk(texts, 1)
        main.sort(key=lambda t: (t[0], t[1]))
        rows += chunk([b for _, _, b in main], 2)
        url_btns = [b for b in extra if b.url]
        rows += [[b] for b in extra if not b.url]
        rows += chunk(url_btns, 2)
        return markup(rows)

    # ------------------------------------------------------------------ cards
    def render_response(self, resp: dict[str, Any], *, msg_id: int | None = None) -> list[Rendered]:
        cards = list(resp.get("cards") or [])
        if not cards:
            return [Rendered("🤔 Не понял, что сделать.\n" + EXAMPLES)]
        return [self.render_card(c, msg_id=msg_id) for c in cards]

    def render_card(self, card: dict[str, Any], *, msg_id: int | None = None) -> Rendered:
        kind = card.get("kind")
        fn = getattr(self, f"_card_{kind}", None)
        if fn is None:
            return self._card_generic(card, msg_id)
        return fn(card, msg_id)

    def _lines_head(self, marker: str, card: dict[str, Any]) -> list[str]:
        lines = [f"{marker} {esc(card.get('title'))}".strip()]
        if card.get("subtitle"):
            lines.append(esc(card["subtitle"]))
        return lines

    def _card_generic(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        opts: list[str] = []
        lines = self._lines_head("ℹ️", card)
        return Rendered(clip("\n".join(lines)), self.keyboard(card, msg_id=msg_id, options=opts),
                        opts)

    def _card_created(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        tx = card.get("transaction") or {}
        is_new = bool(card.get("category_is_new") or (tx.get("category") or {}).get("is_new"))
        title = str(card.get("title") or tx_label(tx))
        head = f"✅ {esc(title)}"
        if is_new and "🆕" not in title:
            head += " 🆕"
        lines = [head]
        sub = str(card.get("subtitle") or "")
        approx = str(card.get("approx") or "")
        if approx and approx not in sub and approx not in title:
            sub = f"{sub} · {approx}" if sub else approx
        if sub:
            lines.append(esc(sub))
        elif card.get("debt"):  # old-style card without subtitle: say where the debt stands
            line = debt_brief_line(card.get("debt"))
            if line:
                lines.append(esc(line[:1].upper() + line[1:]))
        note = card.get("note")
        if is_new or note:
            parts = (["Новая категория."] if is_new else []) + ([esc(note)] if note else [])
            lines.append(" ".join(parts))
        extra: list[dict[str, Any]] = []
        kinds = {(b.get("action") or {}).get("kind") for b in card.get("buttons") or []}
        if "undo" not in kinds and card.get("action_id"):
            extra.append({"label": "Отменить",
                          "action": {"kind": "undo", "action_id": card["action_id"]}})
        if "edit" not in kinds and tx.get("id"):
            extra.append({"label": "Изменить",
                          "action": {"kind": "edit", "transaction_id": tx["id"]}})
        if is_new and "change_category" not in kinds and tx.get("id"):
            extra.append({"label": "В другую",
                          "action": {"kind": "change_category", "transaction_id": tx["id"]}})
        # send_text buttons («Излишек — мне» under a debt overpayment) → t:{n} options
        opts: list[str] = []
        return Rendered(clip("\n".join(lines)),
                        self.keyboard(card, msg_id=msg_id, extra_buttons=extra, options=opts),
                        opts)

    def _card_created_many(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        txs = list(card.get("transactions") or [])
        title = str(card.get("title") or "")
        if not title:
            total = card.get("total")
            title = ops_word(len(txs)) + (
                f" · {format_money(total, card.get('total_currency'))}" if total else "")
        if title[:1].isdigit():
            title = "Записал " + title
        lines = [f"✅ {esc(title)}"]
        if txs:
            for tx in txs:
                lines.append(f"• {esc(tx_category_name(tx))} — {esc(tx_amount(tx))}"
                             + (" 🆕" if (tx.get("category") or {}).get("is_new") else ""))
        elif card.get("subtitle"):
            lines.append(esc(card["subtitle"]))
        # «Изменить»: per-operation edit picker (one tx → direct e:{tx})
        btns = [b for b in card.get("buttons") or []
                if (b.get("action") or {}).get("kind") != "edit"]
        card2 = dict(card, buttons=btns)
        kb = self.keyboard(card2, msg_id=msg_id)
        rows = list(kb.inline_keyboard) if kb else []
        tx_ids = [int(t["id"]) for t in txs if t.get("id")]
        edit_btn: InlineKeyboardButton | None = None
        if len(tx_ids) == 1:
            edit_btn = Btn(text="✏️ Изменить", callback_data=cb.edit(tx_ids[0]))
        elif tx_ids:
            items = [(t["id"], f"{tx_category_name(t)} — {tx_amount(t)}") for t in txs
                     if t.get("id")]
            edit_btn = Btn(text="✏️ Изменить", callback_data=cb.edit_many(self.stash.put(items)))
        if edit_btn:
            for r in rows:
                if any((b.callback_data or "").startswith(("U:", "u:")) for b in r) and len(r) < 2:
                    r.append(edit_btn)
                    break
            else:
                rows.insert(0, [edit_btn])
        return Rendered(clip("\n".join(lines)), markup(rows))

    def _card_needs_account(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        tx = card.get("transaction") or {}
        lines = self._lines_head("💸", card)
        debt_op = (card.get("debt") or {}).get("op") or (tx_debt(tx) or {}).get("op")
        if debt_op:  # a debt is not an expense/income (DEBTS.md §0 п.1)
            question = card.get("question") or (
                "На какой счёт?" if debt_op in DEBT_INFLOW_OPS else "С какого счёта?")
            lines.append(f"Долг записал. {esc(question)}")
        else:
            where = "доходы" if tx.get("kind") in ("income",) else "расходы"
            question = card.get("question") or "С какого счёта?"
            lines.append(f"Записал в {where}. {esc(question)}")
        kinds = {(b.get("action") or {}).get("kind") for b in card.get("buttons") or []}
        extra: list[dict[str, Any]] = []
        if "choose_account" not in kinds:
            for o in card.get("options") or []:
                extra.append({"label": o.get("label"),
                              "action": {"kind": "choose_account", "transaction_id": tx.get("id"),
                                         "account_id": o.get("account_id")}})
        if "later" not in kinds and tx.get("id"):
            extra.append({"label": "Позже",
                          "action": {"kind": "later", "transaction_id": tx.get("id")}})
        if "undo" not in kinds and card.get("action_id"):
            extra.append({"label": "Отменить",
                          "action": {"kind": "undo", "action_id": card["action_id"]}})
        return Rendered(clip("\n".join(lines)),
                        self.keyboard(card, msg_id=msg_id, extra_buttons=extra))

    def _card_updated(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        lines = self._lines_head("✅", card)
        changes = [str(c) for c in card.get("changes") or [] if c]
        sub = str(card.get("subtitle") or "")
        # the backend may already put «Изменено: было → стало» into the subtitle
        already = sub.strip().lower().startswith("изменено") or (
            changes and all(c in sub for c in changes))
        if changes and not already:
            lines.append("Изменено: " + "; ".join(esc(c) for c in changes))
        return Rendered(clip("\n".join(lines)), self.keyboard(card, msg_id=msg_id))

    def _card_deleted(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        title = str(card.get("title") or "")
        lines = [f"🗑 {esc(title)}" if title.lower().startswith("удал")
                 else f"🗑 Удалил: {esc(title)}"]
        if card.get("subtitle"):
            lines.append(esc(card["subtitle"]))
        return Rendered(clip("\n".join(lines)), self.keyboard(card, msg_id=msg_id))

    def _card_preview(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        op = card.get("operation")
        marker = "🗑" if op == "bulk_delete" else "🔎"
        title = str(card.get("title") or "")
        total_count = int(card.get("total_count") or 0)
        if not title:
            verb = "Удалю" if op == "bulk_delete" else "Изменю"
            title = f"{verb} {ops_word(total_count)}"
        lines = [f"{marker} {esc(title)}"]
        seen = {title.strip().lower()}
        for extra in (card.get("change_summary"), card.get("subtitle")):
            s = str(extra or "").strip()
            # «удаление» under «🗑 Удалю N операций» says nothing new; equal lines shown once
            if not s or s.lower() in seen or (op == "bulk_delete" and s.lower() == "удаление"):
                continue
            seen.add(s.lower())
            lines.append(esc(s))
        items = list(card.get("items") or [])[:10]
        if items:
            rows: list[tuple[str, str, str]] = []
            for it in items:
                tx = it.get("transaction") or {}
                before, after = it.get("before"), it.get("after")
                amount = tx_amount(tx)
                if (before and after and before != after
                        and any(ch.isdigit() for ch in str(before) + str(after))):
                    amount = f"{before} → {after}"
                label = (tx.get("merchant") or {}).get("name") or tx.get("description") \
                    or tx_label(tx)
                label = str(label)
                if len(label) > 16:
                    label = label[:15] + "…"
                rows.append((short_date(tx.get("occurred_on")), label, str(amount)))
            lw = max(len(r[1]) for r in rows)
            aw = max(len(r[2]) for r in rows)
            table = "\n".join(f"{d}  {lab.ljust(lw)}  {amt.rjust(aw)}" for d, lab, amt in rows)
            lines.append("")
            lines.append(f"<pre>{esc(table)}</pre>")
        shown = len(items)
        if total_count > shown:
            lines.append(f"… и ещё {total_count - shown}")
        if card.get("total"):
            lines.append(esc(card["total"]))
        lines.append("<i>Превью живёт 10 минут.</i>")
        return Rendered(clip("\n".join(lines)), self.keyboard(card, msg_id=msg_id))

    def _card_applied(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        lines = self._lines_head("✅", card)
        kinds = {(b.get("action") or {}).get("kind") for b in card.get("buttons") or []}
        extra = []
        if "undo" not in kinds and card.get("action_id"):
            extra.append({"label": "Отменить",
                          "action": {"kind": "undo", "action_id": card["action_id"]}})
        return Rendered(clip("\n".join(lines)),
                        self.keyboard(card, msg_id=msg_id, extra_buttons=extra))

    def _debt_sections(self, card: dict[str, Any]) -> list[str]:
        """«мои долги» / «кто мне должен?» answer (``debts`` with ≥ 2 persons) → the same
        «Мне должны / Я должен» sections as ``/debts``: per-section limits with «… и ещё N»,
        so 18 debtors never cut the «Я должен» list off. One person («сколько должен Коля?»)
        → [] — that card's ``totals`` are global, its flat ``lines`` already say everything."""
        debts = card.get("debts")
        if not isinstance(debts, dict):
            return []
        from pocket_bot.debts import debt_sections, open_debts  # debts.py imports this module

        owed, owe = open_debts(debts)
        if len({str(d.get("id")) for d in owed + owe}) < 2:
            return []
        return debt_sections(self, debts)

    def _card_answer(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        sections = self._debt_sections(card)
        if sections:  # headline/lines/text only repeat what the sections show
            lines = self._lines_head("📊", card) + sections
            return Rendered(clip("\n".join(lines)), self._answer_keyboard(card, msg_id))
        head = f"📊 {esc(card.get('title'))}".rstrip()
        if card.get("headline"):
            head += f" <b>{esc(card['headline'])}</b>"
        if card.get("headline_approx"):
            head += f" {esc(card['headline_approx'])}"
        lines = [head]
        if card.get("subtitle"):
            lines.append(esc(card["subtitle"]))
        body: list[str] = []
        a_lines = list(card.get("lines") or [])
        txs = list(card.get("transactions") or [])
        if card.get("answer_type") == "list" or (txs and not a_lines):
            if a_lines:
                for n, ln in enumerate(a_lines[:15], 1):
                    body.append(f"{n}. {esc(ln.get('label'))} — {esc(ln.get('value'))}")
            else:
                for n, tx in enumerate(txs[:15], 1):
                    body.append(f"{n}. {short_date(tx.get('occurred_on'))} · "
                                f"{esc(tx_label(tx))} — {esc(tx_amount(tx, signed=True))}")
            shown = min(len(a_lines or txs), 15)
        else:
            is_debt = isinstance(card.get("debts"), dict)  # person lines: no category emoji
            for ln in a_lines[:15]:
                emoji = category_emoji(ln.get("label")) if not is_debt and (
                    ln.get("category_id") or ln.get("icon")) else "•"
                body.append(f"{emoji} {esc(ln.get('label'))} — {esc(ln.get('value'))}")
            shown = min(len(a_lines), 15)
        total_count = card.get("total_count")
        if isinstance(total_count, int) and total_count > shown and shown:
            body.append(f"… и ещё {total_count - shown}")
        if body:
            lines.append("")
            lines.extend(body)
        if card.get("text"):
            lines.append("")
            lines.append(esc(card["text"]))
        return Rendered(clip("\n".join(lines)), self._answer_keyboard(card, msg_id))

    def _answer_keyboard(self, card: dict[str, Any], msg_id: int | None
                         ) -> InlineKeyboardMarkup | None:
        kb = self.keyboard(card, msg_id=msg_id)
        persons = debt_person_rows(card.get("debts")) if isinstance(card.get("debts"),
                                                                      dict) else []
        if persons:  # «кто мне должен?»: one tap → the person card (dv:{id})
            kb = markup(persons + (list(kb.inline_keyboard) if kb else []))
        return kb

    def _card_balance(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        marker = "🟰" if card.get("result") == "matches" else "🏦"
        return Rendered(clip("\n".join(self._lines_head(marker, card))),
                        self.keyboard(card, msg_id=msg_id))

    def _card_category(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        return Rendered(clip("\n".join(self._lines_head("🏷", card))),
                        self.keyboard(card, msg_id=msg_id))

    def mark_redo(self, action_id: Any) -> None:
        """Remember that undoing ``action_id`` is a «Вернуть» (redo), for the result text."""
        try:
            self.stash.put(True, key=f"redo:{cb.hex_id(str(action_id))}")
        except ValueError:
            pass

    def is_redo(self, action_hex: str) -> bool:
        return self.stash.get(f"redo:{action_hex}") is not None

    def _card_undone(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        lines = self._lines_head("↩️", card)
        if card.get("undo_action_id"):
            self.mark_redo(card["undo_action_id"])
        for b in card.get("buttons") or []:
            a = b.get("action") or {}
            if a.get("kind") == "redo" and a.get("action_id"):
                self.mark_redo(a["action_id"])
        kinds = {(b.get("action") or {}).get("kind") for b in card.get("buttons") or []}
        extra = []
        if "redo" not in kinds and card.get("undo_action_id"):
            extra.append({"label": "Вернуть",
                          "action": {"kind": "redo", "action_id": card["undo_action_id"]}})
        return Rendered(clip("\n".join(lines)),
                        self.keyboard(card, msg_id=msg_id, extra_buttons=extra))

    def _card_unclear(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        title = str(card.get("title") or "Не понял, что записать")
        lines = [f"🤔 {esc(title)}"]
        question = card.get("question")
        if question and question != title:
            lines.append(esc(question))
        if card.get("subtitle") and card.get("subtitle") not in (question, title):
            lines.append(esc(card["subtitle"]))
        opts: list[str] = []
        kb = self.keyboard(card, msg_id=msg_id, options=opts)
        if not opts:
            lines.append(EXAMPLES)
        return Rendered(clip("\n".join(lines)), kb, opts)

    def _card_error(self, card: dict[str, Any], msg_id: int | None) -> Rendered:
        title = str(card.get("title") or "Не получилось")
        lines = [f"⚠️ {esc(title)}"]
        for extra in (card.get("subtitle"), card.get("text")):
            if extra and extra != title and esc(extra) not in lines:
                lines.append(esc(extra))
        joined = " ".join(lines)
        if card.get("code") == "ai_unavailable":
            if "ничего не записано" not in joined.lower():
                lines.append("Ничего не записано.")
            if "простые" not in joined.lower():
                lines.append(SIMPLE_EXAMPLES)
        kinds = {(b.get("action") or {}).get("kind") for b in card.get("buttons") or []}
        extra_btns = []
        if (card.get("code") in ("ai_unavailable", "internal") and "retry" not in kinds
                and msg_id is not None):
            extra_btns.append({"label": "Повторить", "action": {"kind": "retry"}})
        return Rendered(clip("\n".join(lines)),
                        self.keyboard(card, msg_id=msg_id, extra_buttons=extra_btns))

    # ------------------------------------------------------------------ non-card results
    @staticmethod
    def _undo_parse(res: dict[str, Any], redo: bool) -> tuple[bool, bool, str]:
        """UndoResult → (is_redo, already_undone, base summary). ``redo`` (the bot's own memory
        of «this button is Вернуть») is only a fallback when the summary does not say."""
        raw = str(res.get("summary") or "").strip()
        already = res.get("status") == "already_undone"
        if already and raw.startswith(_ALREADY_PREFIX):
            raw = raw[len(_ALREADY_PREFIX):].lstrip()
        kind, base = undo_summary_parts(raw)
        if already:
            # ``raw`` describes the action that had been undone earlier: undoing a cancel
            # («Отменено: …») is a «Вернуть»; undoing an operation or a «Вернуть» is a cancel
            is_redo = (kind is False) if kind is not None else redo
        else:
            is_redo = kind if kind is not None else redo
        return is_redo, already, base

    def undo_is_redo(self, res: dict[str, Any], *, redo: bool = False) -> bool:
        return self._undo_parse(res, redo)[0]

    def undo_result(self, res: dict[str, Any], *, redo: bool = False) -> Rendered:
        """UndoResult → «↩️ Отменено: 150 000 ₫ · Еда» + [↪️ Вернуть] (or the redo variant)."""
        is_redo, already, base = self._undo_parse(res, redo)
        if already:
            verb = "Уже возвращено" if is_redo else "Уже отменено"
        else:
            verb = "Возвращено" if is_redo else "Отменено"
        summary = f"{verb}: {base}" if base else verb
        new_id = res.get("undo_action_id")
        if new_id and not is_redo:
            self.mark_redo(new_id)
        if is_redo:
            text = f"↪️ {esc(summary)}"
            kb = markup([[Btn(text="↩️ Отменить", callback_data=cb.undo(new_id))]]) \
                if new_id else None
        else:
            text = f"↩️ {esc(summary)}"
            kb = markup([[Btn(text="↪️ Вернуть", callback_data=cb.undo(new_id))]]) \
                if new_id else None
        return Rendered(text, kb)

    @staticmethod
    def real_side(tx: dict[str, Any]) -> tuple[Any, Any, Any]:
        """(account name, amount, asset) of the real side. Debt inflows (repay_in/borrow) land on
        ``counter_account`` — ``account`` is the person's service account «Долг: Коля»."""
        op = (tx_debt(tx) or {}).get("op")
        if op in DEBT_INFLOW_OPS:
            return ((tx.get("counter_account") or {}).get("name"), tx.get("counter_amount"),
                    tx.get("counter_asset"))
        if op == "forgive":  # no real account: the remainder becomes an expense/income
            return None, None, None
        return ((tx.get("account") or {}).get("name"), tx.get("account_amount"),
                tx.get("account_asset"))

    def tx_subtitle(self, tx: dict[str, Any]) -> str:
        parts: list[str] = []
        debt = tx_debt(tx)
        acc, acc_amount, asset = self.real_side(tx)
        if debt and debt.get("op") == "forgive":
            parts.append(str((tx.get("category") or {}).get("name") or "без счёта"))
        elif acc:
            acc_part = str(acc)
            if (asset and acc_amount is not None
                    and str(asset).upper() != str(tx.get("currency") or "").upper()):
                op = (debt or {}).get("op")
                sign = -1 if tx.get("kind") == "expense" or op in DEBT_OUTFLOW_OPS else 1
                amt = to_decimal(acc_amount)
                if amt is not None:
                    acc_part += " " + format_money(amt * sign, asset,
                                                   approx=bool(tx.get("is_estimated")),
                                                   sign="auto")
            parts.append(acc_part)
        elif tx.get("status") == "needs_account":
            parts.append("счёт не выбран")
        else:
            parts.append("без счёта")
        cur = str(tx.get("currency") or "").upper()
        if cur and cur not in ("VND",) and tx.get("amount_usd") and cur != "USD":
            parts.append(format_money(tx["amount_usd"], "USD", approx=True))
        who = tx.get("counterparty")
        if who and not (debt and str(who).strip() == str(debt.get("name") or "").strip()):
            parts.append(str(who))
        when = human_date(tx.get("occurred_on"), self.today())
        if when:
            parts.append(when)
        return " · ".join(parts)

    def tx_done(self, tx: dict[str, Any], action_id: str | None, *,
                note: str | None = None, marker: str = "✅") -> Rendered:
        """Operation after a button (account chosen, category changed, recurring recorded)."""
        sign = "always" if tx.get("kind") in ("income", "refund") else "auto"
        amount = format_money(tx.get("amount"), tx.get("currency"), sign=sign)
        cat = tx_category_name(tx)
        head = f"{marker} {esc(amount)} · {esc(cat)}"
        if (tx.get("category") or {}).get("is_new"):
            head += " 🆕"
        lines = [head, esc(self.tx_subtitle(tx))]
        if note:
            lines.append(esc(note))
        row: list[InlineKeyboardButton] = []
        if action_id:
            row.append(Btn(text="↩️ Отменить", callback_data=cb.undo(action_id)))
        if tx.get("id"):
            row.append(Btn(text="✏️ Изменить", callback_data=cb.edit(tx["id"])))
        rows = [row]
        if (tx.get("category") or {}).get("is_new") and tx.get("id"):
            rows.append([Btn(text="🗂 В другую категорию",
                             callback_data=cb.change_category(tx["id"]))])
        return Rendered("\n".join(lines), markup(rows))

    @staticmethod
    def account_choices(accounts: list[dict[str, Any]], currency: Any,
                        limit: int = 8) -> list[dict[str, Any]]:
        """«С какого счёта?» options in the backend order (assistant ``account_options``): accounts
        holding the asset first, then multi-asset wallets, then — for fiat — the single-currency
        fiat accounts ($ with Карта, Наличные, Карта RUB). The same list as the original card."""
        c = str(currency or "").upper()
        live = sorted((a for a in accounts if a.get("id") and not a.get("is_archived")),
                      key=lambda a: (a.get("sort_order") or 0, a["id"]))

        def holds(a: dict[str, Any]) -> bool:
            return bool(c) and (str(a.get("currency") or "").upper() == c or any(
                str(x.get("asset") or "").upper() == c for x in a.get("assets") or []))

        with_asset = [a for a in live if holds(a)]
        multi = [a for a in live if a.get("is_multi_asset") and a not in with_asset]
        fiat = [a for a in live
                if c in FIAT_CODES and not a.get("is_multi_asset") and a not in with_asset
                and str(a.get("currency") or "").upper() in FIAT_CODES]
        return (with_asset + multi + fiat)[:limit] or live[:limit]

    def needs_account_from_tx(self, tx: dict[str, Any], accounts: list[dict[str, Any]], *,
                              action_id: str | None = None, recorded: bool = False) -> Rendered:
        """«С какого счёта?» card built from TransactionOut + GET /accounts (/inbox, and a
        recurring payment recorded without an account — then ``recorded``: «Записал в …»)."""
        debt_op = (tx_debt(tx) or {}).get("op")
        income = tx.get("kind") in ("income", "refund") or debt_op in DEBT_INFLOW_OPS
        amount = format_money(tx.get("amount"), tx.get("currency"),
                              sign="always" if income and not debt_op else "auto")
        when = human_date(tx.get("occurred_on"), self.today())
        lines = [f"💸 {esc(amount)} · {esc(tx_category_name(tx))}"]
        sub = " · ".join(p for p in (str(tx.get("description") or ""), when) if p)
        if sub:
            lines.append(esc(sub))
        q = "На какой счёт?" if income else "С какого счёта?"
        if debt_op:
            q = f"Долг записал. {q}" if recorded else q
        elif recorded:
            q = f"Записал в {'доходы' if income else 'расходы'}. {q}"
        lines.append(q)
        pool = self.account_choices(accounts, tx.get("currency"))
        acc_btns = [Btn(text=str(a.get("name")), callback_data=cb.choose_account(tx["id"], a["id"]))
                    for a in pool]
        acc_btns.append(Btn(text="Без счёта", callback_data=cb.choose_account(tx["id"], None)))
        rows = chunk(acc_btns, 3)
        tail = [Btn(text="⏳ Позже", callback_data=cb.later(tx["id"]))]
        undo_id = action_id or tx.get("created_action_id")
        if undo_id:
            tail.append(Btn(text="↩️ Отменить", callback_data=cb.undo(undo_id)))
        rows.append(tail)
        return Rendered("\n".join(lines), markup(rows))

    def category_picker(self, tx_id: int, suggest: dict[str, Any],
                        current_id: int | None = None) -> InlineKeyboardMarkup | None:
        """«В другую»: 6 similar categories (2 per row) + [🔍 Другая…] + [‹ Назад]."""
        picked: list[dict[str, Any]] = []
        seen: set[int] = set()
        for s in suggest.get("similar") or []:
            c = s.get("category") or {}
            if c.get("id") and c["id"] not in seen and c["id"] != current_id:
                picked.append(c)
                seen.add(c["id"])
        for c in suggest.get("all") or []:
            if len(picked) >= 6:
                break
            if c.get("id") and c["id"] not in seen and c["id"] != current_id \
                    and not c.get("is_archived"):
                picked.append(c)
                seen.add(c["id"])
        btns = [Btn(text=f"{category_emoji(c.get('name'))} {c.get('name')}",
                    callback_data=cb.set_category(tx_id, c["id"])) for c in picked[:6]]
        rows = chunk(btns, 2)
        rows.append([Btn(text="🔍 Другая…", callback_data=cb.category_other(tx_id)),
                     Btn(text="‹ Назад", callback_data=cb.category_back(tx_id))])
        return markup(rows)

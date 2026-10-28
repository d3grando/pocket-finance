"""Card builders. Texts per DESIGN.md §9.2/§14, money via core.money.format_money.

Titles never contain emoji except 🆕 (the bot prepends ✅/❓/⚠️ itself). All texts are Russian.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from app.core.money import format_money
from app.core.timeutil import tz
from app.schemas.assistant import (
    AccountOption,
    AnswerCard,
    AnswerLine,
    AppliedCard,
    Button,
    ButtonAction,
    CategoryCard,
    CreatedCard,
    CreatedManyCard,
    DeletedCard,
    ErrorCard,
    NeedsAccountCard,
    PreviewCard,
    PreviewItem,
    UnclearCard,
    UndoneCard,
    UpdatedCard,
)
from app.schemas.categories import CategoryOut
from app.schemas.intents import SearchFilter
from app.schemas.transactions import TransactionOut

NEW = "🆕"
_MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
_WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

AI_UNAVAILABLE_TITLE = "AI недоступен"
AI_UNAVAILABLE_TEXT = "Напиши проще: «еда 150к» — такие записи работают и без AI."


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def ops(n: int) -> str:
    """Nominative: «1 операция», «2 операции», «5 операций»."""
    return f"{n} {plural(n, 'операция', 'операции', 'операций')}"


def ops_acc(n: int) -> str:
    """Accusative after «Изменю/Удалю/Нашёл»: «1 операцию»."""
    return f"{n} {plural(n, 'операцию', 'операции', 'операций')}"


# ----------------------------------------------------------------------------- buttons


def btn(
    id_: str, label: str, kind: str, *, style: str = "ghost", hotkey: str | None = None, **kw
) -> Button:
    return Button(
        id=id_, label=label, style=style, hotkey=hotkey,  # type: ignore[arg-type]
        action=ButtonAction(kind=kind, **kw),  # type: ignore[arg-type]
    )


def undo_btn(action_id: uuid.UUID, label: str = "Отменить") -> Button:
    return btn("undo", label, "undo", hotkey="Ctrl+Z", action_id=action_id)


def edit_btn(tx_id: int | None) -> Button:
    return btn("edit", "Изменить", "edit", hotkey="Ctrl+E", transaction_id=tx_id)


def manual_btn(text: str | None = None) -> Button:
    return btn("manual", "Вручную", "manual", style="outline",
               prefill={"text": text} if text else None)


def retry_btn() -> Button:
    return btn("retry", "Повторить", "retry", style="ghost")


def change_category_btn(tx_id: int) -> Button:
    return btn("change-category", "В другую", "change_category", style="outline",
               transaction_id=tx_id)


# ----------------------------------------------------------------------------- texts


def day_label(tx: TransactionOut, tz_name: str, today: date) -> str:
    at = tx.occurred_at.astimezone(tz(tz_name)) if tx.occurred_at.tzinfo else tx.occurred_at
    d = tx.occurred_on
    if d == today:
        return f"сегодня, {at:%H:%M}"
    if (today - d).days == 1:
        return "вчера"
    if (today - d).days == 2:
        return "позавчера"
    s = f"{_WD[d.weekday()]}, {d.day} {_MONTHS[d.month - 1]}"
    return s if d.year == today.year else f"{s} {d.year}"


def signed_money(tx: TransactionOut) -> str:
    sign = "always" if tx.kind in ("income", "refund") else "auto"
    return format_money(tx.amount, tx.currency, sign=sign)


def tx_label(tx: TransactionOut) -> str:
    """Category / transfer label for the title."""
    if tx.kind in ("transfer", "exchange"):
        a = tx.account.name if tx.account else "?"
        b = tx.counter_account.name if tx.counter_account else "?"
        return f"{'Обмен' if tx.kind == 'exchange' else 'Перевод'} {a} → {b}"
    if tx.kind == "adjustment":
        return "Корректировка"
    name = tx.category.name if tx.category else "Без категории"
    if tx.kind == "refund":
        name = f"Возврат · {name}"
    if tx.category and tx.category.is_new:
        name = f"{name} {NEW}"
    return name


def tx_title(tx: TransactionOut) -> str:
    if tx.kind == "exchange" and tx.counter_amount is not None and tx.counter_asset:
        return (f"{format_money(tx.amount, tx.currency)} → "
                f"{format_money(tx.counter_amount, tx.counter_asset)} · {tx_label(tx)}")
    return f"{signed_money(tx)} · {tx_label(tx)}"


def tx_summary(tx: TransactionOut) -> str:
    """Action-log summary (undo shows «Отменено: <summary>»), no 🆕."""
    return tx_title(tx).replace(f" {NEW}", "")


def approx_of(tx: TransactionOut) -> str | None:
    cur = tx.currency.upper()
    if cur == "VND":
        return None
    if cur == "USD":
        return format_money(tx.amount_vnd, "VND", approx=True) if tx.amount_vnd else None
    return format_money(tx.amount_usd, "USD", approx=True) if tx.amount_usd is not None else None


def tx_subtitle(tx: TransactionOut, tz_name: str, today: date) -> str:
    parts: list[str] = []
    if tx.kind not in ("transfer", "exchange") and tx.account:
        acc = tx.account.name
        if (
            tx.account_amount is not None
            and tx.account_asset
            and tx.account_asset.upper() != tx.currency.upper()
        ):
            sign = "auto" if tx.kind == "expense" else "always"
            amt = -tx.account_amount if tx.kind == "expense" else tx.account_amount
            acc += " " + format_money(amt, tx.account_asset, sign=sign, approx=tx.is_estimated)
        parts.append(acc)
    elif tx.kind not in ("transfer", "exchange") and tx.status == "posted":
        parts.append("без счёта")
    if tx.merchant and tx.merchant.name != (tx.counterparty or ""):
        parts.append(tx.merchant.name)
    if tx.counterparty:
        parts.append(tx.counterparty)
    if tx.description:
        parts.append(f"«{tx.description}»")
    parts.append(day_label(tx, tz_name, today))
    return " · ".join(parts)


# ----------------------------------------------------------------------------- cards


def created_card(
    tx: TransactionOut,
    action_id: uuid.UUID,
    *,
    category_is_new: bool,
    tz_name: str = "Asia/Ho_Chi_Minh",
    today: date | None = None,
    note: str | None = None,
) -> CreatedCard:
    """title «150 000 ₫ · Еда», subtitle «Карта · сегодня, 14:32»; buttons undo/edit
    (+ change_category «В другую» when category_is_new)."""
    today = today or tx.occurred_on
    buttons: list[Button] = []
    if category_is_new:
        buttons.append(change_category_btn(tx.id))
    buttons += [undo_btn(action_id), edit_btn(tx.id)]
    return CreatedCard(
        title=tx_title(tx),
        subtitle=tx_subtitle(tx, tz_name, today),
        action_id=action_id,
        transaction=tx,
        category_is_new=category_is_new,
        approx=approx_of(tx),
        note=note,
        buttons=buttons,
    )


def created_many_card(
    txs: Sequence[TransactionOut], action_ids: Sequence[uuid.UUID]
) -> CreatedManyCard:
    currencies = {t.currency for t in txs}
    total: Decimal | None = None
    cur = None
    if len(currencies) == 1:
        cur = next(iter(currencies))
        total = sum(
            (t.amount if t.kind in ("income", "refund") else -t.amount for t in txs
             if t.kind in ("expense", "income", "refund")),
            Decimal(0),
        )
    title = ops(len(txs)) + (f" · {format_money(total, cur, sign='always')}" if cur else "")
    subtitle = " · ".join(
        f"{tx_label(t).replace(f' {NEW}', '')} {format_money(t.amount, t.currency)}" for t in txs
    )
    return CreatedManyCard(
        title=title,
        subtitle=subtitle,
        action_ids=list(action_ids),
        transactions=list(txs),
        total=total,
        total_currency=cur,
        buttons=[undo_btn(action_ids[0], "Отменить все"), edit_btn(txs[-1].id)],
    )


def needs_account_card(
    tx: TransactionOut, action_id: uuid.UUID, options: list[AccountOption]
) -> NeedsAccountCard:
    """DESIGN §14: «Записал в расходы. С какого счёта?» — the operation IS saved (in expenses,
    not yet in balances)."""
    question = {
        "income": "Записал в доходы. На какой счёт?",
        "refund": "Записал возврат. На какой счёт?",
    }.get(tx.kind, "Записал в расходы. С какого счёта?")
    buttons: list[Button] = []
    if tx.category and tx.category.is_new:
        buttons.append(change_category_btn(tx.id))
    for o in options:
        if o.account_id is None:
            buttons.append(btn("acc-none", o.label, "choose_account", hotkey=o.hotkey,
                               transaction_id=tx.id, account_id=None))
        else:
            buttons.append(btn(f"acc-{o.account_id}", o.label, "choose_account", style="outline",
                               hotkey=o.hotkey, transaction_id=tx.id, account_id=o.account_id))
    buttons += [btn("later", "Позже", "later"), undo_btn(action_id)]
    return NeedsAccountCard(
        title=tx_title(tx),
        subtitle=approx_of(tx),
        action_id=action_id,
        transaction=tx,
        question=question,
        options=options,
        buttons=buttons,
    )


def updated_card(tx: TransactionOut, action_id: uuid.UUID, changes: list[str]) -> UpdatedCard:
    return UpdatedCard(
        title=tx_title(tx),
        subtitle=("Изменено: " + "; ".join(changes)) if changes else "Изменено",
        action_id=action_id,
        transaction=tx,
        changes=changes,
        buttons=[undo_btn(action_id), edit_btn(tx.id)],
    )


def deleted_card(tx: TransactionOut, action_id: uuid.UUID) -> DeletedCard:
    return DeletedCard(
        title=f"Удалено: {tx_summary(tx)}",
        subtitle=None,
        action_id=action_id,
        transaction=tx,
        buttons=[undo_btn(action_id, "Вернуть")],
    )


def preview_card(
    *,
    token: uuid.UUID,
    operation: str,
    change_summary: str,
    items: list[PreviewItem],
    total_count: int,
    total: str | None,
    expires_at: datetime,
) -> PreviewCard:
    verb = "Удалю" if operation == "bulk_delete" else "Изменю"
    return PreviewCard(
        title=f"{verb} {ops_acc(total_count)}",
        # «удаление» repeats the title — no second line then
        subtitle=change_summary if change_summary and change_summary != "удаление" else None,
        preview_token=token,
        operation=operation,  # type: ignore[arg-type]
        change_summary=change_summary,
        items=items[:20],
        total_count=total_count,
        total=total,
        expires_at=expires_at,
        buttons=[
            btn("cancel", "Отмена", "cancel_preview", hotkey="Esc", preview_token=token),
            btn("apply", "Применить", "confirm_preview", style="primary", hotkey="Enter",
                preview_token=token),
        ],
    )


def applied_card(action_id: uuid.UUID, count: int, operation: str) -> AppliedCard:
    verb = (
        plural(count, "Удалена", "Удалены", "Удалено")
        if operation == "bulk_delete"
        else plural(count, "Изменена", "Изменены", "Изменено")
    )
    return AppliedCard(title=f"{verb} {ops(count)}", action_id=action_id, count=count,
                       buttons=[undo_btn(action_id)])


def category_card(cat: CategoryOut, action_id: uuid.UUID) -> CategoryCard:
    kind = "доходов" if cat.kind == "income" else "расходов"
    biz = ", бизнес" if cat.is_business else ""
    return CategoryCard(
        title=f"Категория «{cat.name}»" + (f" {NEW}" if cat.is_new else ""),
        subtitle=f"Создал категорию {kind}{biz}",
        action_id=action_id,
        category=cat,
        buttons=[undo_btn(action_id)],
    )


def undone_card(summary: str, undone_action_id: uuid.UUID, undo_action_id: uuid.UUID | None,
                *, already: bool = False) -> UndoneCard:
    title = summary if summary.startswith(("Отменено", "Возвращено", "Уже отменено")) else (
        f"Отменено: {summary}")
    return UndoneCard(
        title=title,
        subtitle="Уже было отменено" if already else None,
        undone_action_id=undone_action_id,
        undo_action_id=undo_action_id,
        buttons=[btn("redo", "Вернуть", "redo", action_id=undo_action_id)] if undo_action_id
        else [],
    )


def text_answer_card(text: str, title: str = "Ответ") -> AnswerCard:
    return AnswerCard(title=title, answer_type="text", text=text)


def info_card(title: str, text: str | None = None) -> AnswerCard:
    return AnswerCard(title=title, subtitle=text, answer_type="text", text=text)


def search_card(
    txs: list[TransactionOut],
    total_count: int,
    sf: SearchFilter,
    *,
    tz_name: str,
    today: date,
    expense_vnd: Decimal | None = None,
) -> AnswerCard:
    if total_count == 0:
        return AnswerCard(title="Ничего не нашёл", answer_type="list", total_count=0, filter=sf,
                          subtitle="Попробуйте другой период или условие")
    lines = [
        AnswerLine(
            label=f"{day_label(t, tz_name, today)} · {tx_label(t).replace(f' {NEW}', '')}",
            value=signed_money(t),
            amount=t.amount,
            currency=t.currency,
            transaction_id=t.id,
            category_id=t.category.id if t.category else None,
            icon=t.category.icon if t.category else None,
        )
        for t in txs
    ]
    more = total_count - len(txs)
    return AnswerCard(
        title=f"Нашёл {ops_acc(total_count)}",
        subtitle=f"показаны {len(txs)}, ещё {more}" if more > 0 else None,
        answer_type="list",
        headline=format_money(-expense_vnd, "VND") if expense_vnd else None,
        lines=lines,
        transactions=txs,
        total_count=total_count,
        filter=sf,
        buttons=[btn("open", "Открыть в операциях →", "open", route="/operations")],
    )


def error_card(
    code: str, title: str, text: str | None = None, *, input_text: str | None = None,
    retry: bool | None = None, keep_input: bool = True,
) -> ErrorCard:
    """``retry`` defaults to transient codes (ai_unavailable/internal). ``keep_input=False``
    when part of the message WAS recorded: re-sending the whole text would duplicate it."""
    buttons = [manual_btn(input_text)]
    if retry if retry is not None else code in ("ai_unavailable", "internal"):
        buttons.append(retry_btn())
    return ErrorCard(code=code, title=title, text=text, subtitle=text,  # type: ignore[arg-type]
                     buttons=buttons, keep_input=keep_input)


def ai_unavailable_card(input_text: str | None = None) -> ErrorCard:
    return error_card("ai_unavailable", AI_UNAVAILABLE_TITLE, AI_UNAVAILABLE_TEXT,
                      input_text=input_text)


_LABEL_CONNECTORS = ("на", "с", "со", "в", "во", "из", "к", "ко", "от")


def option_labels(options: list[str]) -> list[str]:
    """Button labels for clarify options. Options built as «<the whole phrase> на Карта» share a
    long prefix, and a 60-char label cut them all to the same text.
    Then show only the distinct tail, with its connector word: «на Карта», «на Наличные»."""
    plain = [o[:60] for o in options]
    if len(options) < 2:
        return plain
    prefix = os.path.commonprefix(options)
    cut = prefix.rfind(" ") + 1  # back to a word boundary
    if cut < 20:
        return plain
    head = prefix[:cut].rstrip().rsplit(" ", 1)[-1].lower()
    labels = []
    for o in options:
        tail = o[cut:].strip()
        if not tail:
            return plain
        labels.append((f"{head} {tail}" if head in _LABEL_CONNECTORS else tail)[:60])
    return labels


def unclear_card(
    reason: str, question: str, options: list[str], *, input_text: str | None = None
) -> UnclearCard:
    opts = [x.strip() for x in options if x and x.strip()][:4]
    buttons = [
        btn(f"opt-{i}", label, "send_text", style="outline", hotkey=str(i), text=o[:300])
        for i, (o, label) in enumerate(zip(opts, option_labels(opts), strict=True), 1)
    ]
    buttons.append(manual_btn(input_text))
    title = {
        "missing_amount": "Не хватает суммы",
        "ambiguous_currency": "Валюта неясна",
        "ambiguous_target": "Уточните",
        "other": "Уточните",
    }.get(reason, "Не понял, что записать")
    return UnclearCard(
        title=title,
        subtitle=question,
        reason=reason if reason in (  # type: ignore[arg-type]
            "ambiguous_currency", "ambiguous_target", "missing_amount", "unknown", "other"
        ) else "other",
        question=question,
        buttons=buttons,
    )

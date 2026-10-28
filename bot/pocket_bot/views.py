"""Texts built by the bot itself: /start, /help, /today, /month, /last, /settings and honest
short answers for backend/transport errors (DESIGN.md §12, §14)."""

from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from pocket_bot import callbacks as cb
from pocket_bot.api import ApiError
from pocket_bot.fmt import (
    category_emoji,
    esc,
    format_money,
    format_percent,
    neg,
    ops_word,
    short_date,
    to_decimal,
    tx_amount,
    tx_label,
)
from pocket_bot.render import Rendered, Renderer, markup

Btn = InlineKeyboardButton

COMMANDS: list[tuple[str, str]] = [
    ("today", "Сегодня"),
    ("month", "Этот месяц"),
    ("report", "Отчёт за месяц"),
    ("inbox", "Разобрать"),
    ("last", "Последние операции"),
    ("debts", "Долги: кто кому должен"),
    ("undo", "Отменить последнее"),
    ("settings", "Отчёты и напоминания"),
    ("help", "Помощь"),
]

START_TEXT = (
    "👛 <b>Pocket</b> на связи.\n"
    "Пишите, как другу: сумма и на что — запишу сам.\n\n"
    "<code>кофе 85к</code> · <code>такси 70к вчера</code> · "
    "<code>получил 300 USDT от клиента</code>\n"
    "<code>еда 120к, такси 45к и кофе 30к</code>\n\n"
    "Спрашивайте: <code>сколько потратил сегодня</code>, "
    "<code>на что ушло за месяц</code>.\n"
    "Исправить: <code>нет, 250к</code> или кнопка «Изменить».\n"
    "Долги: <code>дал в долг Коле 500к</code>, <code>Коля вернул 200к</code>.\n\n"
    "/today — сегодня · /month — месяц · /report — отчёт · /debts — долги · "
    "/help — все команды"
)

HELP_TEXT = (
    "<b>Как записывать</b>\n"
    "• <code>кофе 85</code> → 85 000 ₫ (голое число меньше 1000 — тысячи)\n"
    "• <code>к</code> — тысячи, <code>м</code> — миллионы: <code>аренда 11м</code>\n"
    "• валюта: <code>15$</code>, <code>300 USDT</code>, <code>3 тона с кошелька</code>, "
    "<code>500 руб</code>\n"
    "• дата: <code>вчера такси 70к</code>, <code>в пятницу продукты 450к</code>\n"
    "• несколько сразу: <code>еда 120к, такси 45к</code>\n\n"
    "<b>Исправить</b>: <code>нет, 250к</code>, <code>удали последний расход</code>, "
    "<code>отмени</code> — или кнопки под ответом.\n"
    "<b>Спросить</b>: <code>сколько потратил на еду за неделю</code>, "
    "<code>покажи траты дороже 100$</code>.\n\n"
    "<b>Долги</b> — не расход и не доход, деньги двигаются по счёту:\n"
    "• <code>дал в долг Коле 500к</code> · <code>Коля вернул 200к</code> "
    "(частями, в любой валюте)\n"
    "• <code>занял у Пети 1м</code> · <code>отдал Пете 500к</code> · "
    "<code>вернул Пете весь долг</code>\n"
    "• <code>Коля не вернёт</code> / <code>простить долг Коле</code> — остаток в расходы\n"
    "• <code>кто мне должен</code>, <code>сколько должен Коля</code> или /debts\n\n"
    "<b>Команды</b>\n"
    "/today — расходы за сегодня\n"
    "/month — итоги месяца\n"
    "/report — отчёт за месяц (<code>/report прошлый</code>, <code>/report неделя</code>, "
    "<code>/report день</code>)\n"
    "/inbox — операции без счёта («Разобрать»)\n"
    "/last — последние операции\n"
    "/debts — долги: кто мне должен и кому я должен\n"
    "/undo — отменить последнее действие\n"
    "/settings — отчёты и напоминания (и о долгах)\n"
    "/help — эта справка"
)

NON_TEXT = "Пока понимаю только текст. Напишите, например, <code>кофе 85к</code>."
EDITED_HINT = ("✏️ Правки сообщений не отслеживаю. Чтобы исправить операцию, нажмите "
               "«Изменить» под ответом или напишите <code>нет, 250к</code>.")
TOO_LONG = "⚠️ Слишком длинное сообщение — до 2000 символов. Ничего не записано."
STALE_BUTTON = "Кнопка устарела — повторите запрос."
EDIT_PROMPT = ("✏️ Напишите новую сумму или категорию — например, <code>250к</code> "
               "или <code>Кофе</code>.")
CATEGORY_PROMPT = "🔍 Напишите название категории — например, <code>Подписки</code>."
AMOUNT_PROMPT = "✏️ Напишите сумму — например, <code>11.5м</code>."


def api_error_text(err: ApiError, *, mutation: bool = True) -> str:
    """Short honest answer for an API failure. ``mutation``: the request could write data."""
    nothing = " Ничего не записано." if mutation else ""
    if err.code == "timeout":
        if mutation:
            return ("⚠️ Сервер не ответил вовремя. Если запись всё-таки прошла, повтор "
                    "не создаст дубль.")
        return "⚠️ Сервер не ответил вовремя. Попробуйте ещё раз."
    if err.status == 0:
        return "⚠️ Сервер недоступен." + nothing + " Попробуйте позже."
    if err.status == 401:
        return "⚠️ Бот не авторизован на сервере (BOT_SERVICE_TOKEN)." + nothing
    if err.status == 403:
        return "⚠️ Сервер не узнал этот Telegram-аккаунт." + nothing
    if err.status == 404:
        return "⚠️ Не нашёл — возможно, запись уже удалили."
    if err.code == "idempotency_in_progress":
        return "⏳ Ещё обрабатываю это сообщение…"
    if err.code == "idempotency_mismatch":
        return "⚠️ Это сообщение уже обработано с другим текстом. Напишите новое."
    if err.code == "version_conflict":
        return "⚠️ Операцию уже изменили в другом месте. Ничего не изменено — повторите."
    if err.code == "undo_conflict":
        return "⚠️ Не могу отменить: запись уже изменили после этого действия."
    if err.code in ("preview_expired", "preview_stale", "gone", "action_expired"):
        return "⌛ Превью устарело — повторите запрос."
    if err.status == 429:
        wait = f" Подождите {err.retry_after} с." if err.retry_after else " Подождите минуту."
        return "⏳ Слишком часто." + wait
    if err.status == 501:
        return "⚠️ Эта функция на сервере ещё не готова." + nothing
    if err.status == 503 and err.code == "ai_unavailable":
        return "⚠️ AI сейчас недоступен." + nothing
    if err.status in (400, 409, 410, 422) and err.message:
        return f"⚠️ {esc(err.message)}"
    if err.status >= 500:
        if mutation:
            return "⚠️ Не удалось сохранить, ничего не записано. Попробуйте ещё раз."
        return "⚠️ Ошибка сервера. Попробуйте ещё раз."
    return f"⚠️ {esc(err.message or 'Не получилось')}"


def retry_markup(msg_id: int, *, same_key: bool) -> InlineKeyboardMarkup | None:
    return markup([[Btn(text="🔁 Повторить", callback_data=cb.retry(msg_id, same_key))]])


# ------------------------------------------------------------------------- stats views
def _kpi_value(kpi: dict[str, Any] | None) -> Any:
    return (kpi or {}).get("value")


def _category_lines(items: list[dict[str, Any]], currency: str, *, top: int,
                    with_share: bool) -> list[str]:
    lines: list[str] = []
    for it in items[:top]:
        s = f"{category_emoji(it.get('name'))} {esc(it.get('name'))} — " \
            f"{format_money(it.get('amount'), currency)}"
        if with_share and it.get("share") is not None:
            s += f" · {format_percent(it.get('share'), sign='never')}"
        lines.append(s)
    rest = items[top:]
    if rest:
        total = sum((to_decimal(i.get("amount")) or 0 for i in rest), start=to_decimal("0"))
        n = len(rest)
        word = "категория" if n % 10 == 1 and n % 100 != 11 else (
            "категории" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "категорий")
        s = f"… ещё {n} {word} — {format_money(total, currency)}"
        if with_share:
            share = sum((to_decimal(i.get("share")) or 0 for i in rest), start=to_decimal("0"))
            s += f" · {format_percent(share, sign='never')}"
        lines.append(s)
    return lines


def today_view(r: Renderer, summary: dict[str, Any], by_cat: dict[str, Any] | None) -> Rendered:
    cur = summary.get("currency") or "VND"
    kpi = summary.get("spent_today") or {}
    value = _kpi_value(kpi)
    if value is None:
        head = "📊 Сегодня: данных пока нет — у операций ещё нет курса."
    elif to_decimal(value) == 0:
        head = "📊 Сегодня расходов нет."
    else:
        head = f"📊 Сегодня потрачено <b>{format_money(neg(value), cur)}</b>"
        if kpi.get("approx_usd") and cur != "USD":
            head += f" {format_money(kpi['approx_usd'], 'USD', approx=True)}"
    lines = [head]
    items = list((by_cat or {}).get("items") or [])
    if items and value is not None and to_decimal(value) != 0:
        lines.append("")
        lines += _category_lines(items, (by_cat or {}).get("currency") or cur, top=3,
                                 with_share=False)
    lines += _footer(summary)
    btn = r.url_button("📈 Открыть в Pocket", "/analytics?period=today")
    return Rendered("\n".join(lines), markup([[btn]] if btn else []))


def month_view(r: Renderer, summary: dict[str, Any], by_cat: dict[str, Any] | None) -> Rendered:
    cur = summary.get("currency") or "VND"
    label = (summary.get("month") or {}).get("label") or "Этот месяц"
    spent = summary.get("spent_month") or {}
    income = summary.get("income_month") or {}
    net = summary.get("net_month") or {}
    lines = [f"🗓 <b>{esc(label)}</b>", ""]
    s = f"Расходы  {format_money(neg(_kpi_value(spent)), cur)}"
    if spent.get("change_ratio") is not None:
        s += f"  ({format_percent(spent['change_ratio'])} к прошлому месяцу)"
    lines.append(s)
    lines.append(f"Доходы   {format_money(_kpi_value(income), cur, sign='always')}")
    n = f"Поток    <b>{format_money(_kpi_value(net), cur, sign='always')}</b>"
    if net.get("approx_usd") and cur != "USD":
        n += f" {format_money(net['approx_usd'], 'USD', approx=True)}"
    lines.append(n)
    items = list((by_cat or {}).get("items") or [])
    if items:
        lines += ["", "Куда ушло:"]
        lines += _category_lines(items, (by_cat or {}).get("currency") or cur, top=5,
                                 with_share=True)
    lines += _footer(summary)
    rows = []
    b1 = r.url_button("📊 Подробно", "/analytics?period=this_month")
    if b1:
        rows.append([b1])
    return Rendered("\n".join(lines), markup(rows))


def _footer(summary: dict[str, Any]) -> list[str]:
    out: list[str] = []
    n = int(summary.get("needs_account_count") or 0)
    if n:
        out += ["", f"📥 Разобрать: {n} — /inbox"]
    m = int(summary.get("missing_rates_count") or 0)
    if m:
        out.append(f"⚠️ У {ops_word(m)} ещё нет курса — итог неполный.")
    return out


def last_view(r: Renderer, page: dict[str, Any]) -> Rendered:
    items = list(page.get("items") or [])
    if not items:
        return Rendered("Пока ни одной операции. Напишите, как другу: сумма и на что.")
    lines = ["🧾 <b>Последние операции</b>", ""]
    for tx in items:
        mark = " 💸" if tx.get("status") == "needs_account" else ""
        lines.append(f"{short_date(tx.get('occurred_on'))} · {esc(tx_label(tx))} — "
                     f"{esc(tx_amount(tx, signed=True))}{mark}")
    total = int(page.get("total") or 0)
    if total > len(items):
        lines.append(f"… всего {ops_word(total)}")
    btn = r.url_button("📋 Все операции", "/operations")
    return Rendered("\n".join(lines), markup([[btn]] if btn else []))


# SettingsOut.report_weekly_day: 0=Пн … 6=Вс (API.md §2 Settings)
WEEKDAYS_EVERY = [
    "по понедельникам", "по вторникам", "по средам", "по четвергам", "по пятницам",
    "по субботам", "по воскресеньям",
]


def _hhmm(value: Any, default: str) -> str:
    """'22:00:00' → '22:00' (defaults are the backend SettingsOut defaults)."""
    parts = str(value or "").strip().split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return default
    return f"{h:02d}:{m:02d}" if 0 <= h < 24 and 0 <= m < 60 else default


def settings_view(r: Renderer, settings: dict[str, Any],
                  recurring: list[dict[str, Any]] | None) -> Rendered:
    def mark(v: Any) -> str:
        return "✅" if v else "▫️"

    monthly_t = _hhmm(settings.get("report_time"), "10:00")
    daily_t = _hhmm(settings.get("report_daily_time"), "22:00")
    weekly_t = _hhmm(settings.get("report_weekly_time"), "21:00")
    wd = settings.get("report_weekly_day")
    weekday = WEEKDAYS_EVERY[wd] if isinstance(wd, int) and 0 <= wd <= 6 else WEEKDAYS_EVERY[6]
    lines = [
        "⚙️ <b>Отчёты в Telegram</b>",
        f"{mark(settings.get('report_monthly'))} Месячный — 1-го числа в {esc(monthly_t)}",
        f"{mark(settings.get('report_daily'))} Ежедневный — в {esc(daily_t)}",
        f"{mark(settings.get('report_weekly'))} Недельный — {weekday} в {esc(weekly_t)}",
    ]
    active = [x for x in recurring or [] if x.get("is_active")]
    if recurring is not None:
        lines += ["", "⏰ <b>Регулярные платежи</b>"]
        if not active:
            lines.append("Нет напоминаний — добавляются в настройках Pocket.")
        for x in active[:10]:
            amt = format_money(x.get("amount"), x.get("currency")) if x.get("amount") else \
                "сумма не задана"
            lines.append(f"• {esc(x.get('title'))} — {esc(amt)} · следующее "
                         f"{short_date(x.get('next_due_on'))}")
    # debt reminders (DEBTS.md §9; off by default) — only when the backend knows the field
    has_debts = "debt_reminders_enabled" in settings
    if has_debts:
        days = settings.get("debt_reminder_days") or 30
        lines += ["", "💸 <b>Долги</b>",
                  (f"{mark(settings.get('debt_reminders_enabled'))} Напоминать, если долгу "
                   f"{esc(days)} дн. и больше")]
    rows = [[
        Btn(text=f"{mark(settings.get('report_monthly'))} Месячный",
            callback_data=cb.settings_toggle("m")),
        Btn(text=f"{mark(settings.get('report_daily'))} Ежедневный",
            callback_data=cb.settings_toggle("d")),
        Btn(text=f"{mark(settings.get('report_weekly'))} Недельный",
            callback_data=cb.settings_toggle("w")),
    ]]
    if has_debts:
        rows.append([Btn(text=f"{mark(settings.get('debt_reminders_enabled'))} "
                              "Напоминать о долгах", callback_data=cb.settings_toggle("b"))])
    btn = r.url_button("⚙️ Настройки Pocket", "/settings")
    if btn:
        rows.append([btn])
    return Rendered("\n".join(lines), markup(rows))


SETTINGS_FIELDS = {"m": "report_monthly", "d": "report_daily", "w": "report_weekly",
                   "b": "debt_reminders_enabled"}

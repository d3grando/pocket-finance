"""Money/date formatting for bot-built texts (DESIGN.md §3). Mirrors backend
``app/core/money.format_money`` and web ``src/lib/format.ts``: NBSP thousands, U+2212 minus,
dot decimal separator. Decimal only — never float."""

from __future__ import annotations

import html
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

NBSP = " "
MINUS = "−"
APPROX = "≈"

FIAT_SYMBOLS: dict[str, str] = {"USD": "$", "EUR": "€", "THB": "฿", "CNY": "¥"}
FIAT_CODES = frozenset({"VND", "RUB", *FIAT_SYMBOLS})  # backend app/core/money.FIAT
STABLECOINS = {"USDT", "USDC", "DAI", "FDUSD"}
STABLE_DECIMALS = 6
CRYPTO_DECIMALS = 8

MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def esc(value: Any) -> str:
    """HTML-escape any user/backend-provided string for parse_mode=HTML."""
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def to_decimal(value: Any) -> Decimal | None:
    """API money strings → Decimal. None/empty → None. Floats are rejected."""
    if value is None or value == "":
        return None
    if isinstance(value, (bool, float)):
        raise TypeError("money must be a string/int/Decimal, not float")
    if isinstance(value, Decimal):
        return value
    try:
        d = Decimal(str(value).strip().replace(NBSP, "").replace(" ", ""))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _group(int_part: str) -> str:
    out: list[str] = []
    while len(int_part) > 3:
        out.insert(0, int_part[-3:])
        int_part = int_part[:-3]
    out.insert(0, int_part)
    return NBSP.join(out)


def _number(amount: Decimal, min_dec: int, max_dec: int) -> str:
    a = abs(amount)
    step = Decimal(1).scaleb(-max_dec) if max_dec else Decimal(1)
    q = a.quantize(step, rounding=ROUND_HALF_EVEN)
    s = format(q, "f")
    if "." in s:
        ip, fp = s.split(".")
        fp = fp.rstrip("0")
        if len(fp) < min_dec:
            fp = fp.ljust(min_dec, "0")
    else:
        ip, fp = s, "0" * min_dec
    return _group(ip) + (f".{fp}" if fp else "")


def format_money(
    amount: Any, currency: str | None, *, sign: str = "auto", approx: bool = False
) -> str:
    """«1 250 000 ₫», «$12.40», «3 TON», «+300 USDT», «≈ $12.40». ``sign``: auto|always|never.
    Unknown/empty amount → «—»."""
    amt = to_decimal(amount)
    if amt is None:
        return "—"
    cur = (currency or "VND").upper()
    if cur == "VND":
        body = _number(amt, 0, 0) + NBSP + "₫"
    elif cur == "RUB":
        has_kop = abs(amt) != abs(amt).to_integral_value()
        body = _number(amt, 2 if has_kop else 0, 2) + NBSP + "₽"
    elif cur in FIAT_SYMBOLS:
        body = FIAT_SYMBOLS[cur] + _number(amt, 2, 2)
    elif cur in STABLECOINS:
        # «300 USDT», «20.50 USDT», «20.2552 USDT» — same as backend money.py
        is_int = abs(amt) == abs(amt).to_integral_value()
        body = _number(amt, 0 if is_int else 2, STABLE_DECIMALS) + NBSP + cur
    else:
        body = _number(amt, 0, CRYPTO_DECIMALS) + NBSP + cur
    prefix = ""
    if sign != "never":
        if amt < 0:
            prefix = MINUS
        elif sign == "always" and amt > 0:
            prefix = "+"
    out = prefix + body
    if approx:
        out = APPROX + NBSP + out
    return out


def neg(amount: Any) -> Decimal | None:
    d = to_decimal(amount)
    return None if d is None else -d


def format_percent(ratio: Any, *, sign: str = "always") -> str:
    """'0.183' → «+18%», '0.045' → «+4.5%» (1 decimal only when |x| < 10%)."""
    r = to_decimal(ratio)
    if r is None:
        return "—"
    pct = r * 100
    if abs(pct) < 10:
        q = pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN)
        if q == q.to_integral_value():
            q = q.quantize(Decimal(1))
    else:
        q = pct.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)
    body = format(abs(q), "f") + "%"
    if q < 0:
        return MINUS + body
    if q > 0 and sign == "always":
        return "+" + body
    return body


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian plural: 1 операция, 2 операции, 5 операций."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def ops_word(n: int) -> str:
    return f"{n} {plural(n, 'операция', 'операции', 'операций')}"


def parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def short_date(value: Any) -> str:
    """'2026-10-21' → «21.10»."""
    d = parse_date(value)
    return d.strftime("%d.%m") if d else ""


def human_date(value: Any, today: date | None = None) -> str:
    """«сегодня» / «вчера» / «21 октября» / «21 октября 2025»."""
    d = parse_date(value)
    if d is None:
        return ""
    if today is not None:
        delta = (today - d).days
        if delta == 0:
            return "сегодня"
        if delta == 1:
            return "вчера"
    s = f"{d.day} {MONTHS_GEN[d.month - 1]}"
    if today is not None and d.year != today.year:
        s += f" {d.year}"
    return s


# --------------------------------------------------------------------- category emoji
# DESIGN.md §12.7 — fixed table by category name; new/unknown categories → 🏷.
CATEGORY_EMOJI: dict[str, str] = {
    "еда": "🍴",
    "продукты": "🧺",
    "кофе": "☕",
    "такси": "🚕",
    "такси/транспорт": "🚕",
    "транспорт": "🚕",
    "жильё": "🏠",
    "жилье": "🏠",
    "связь": "📱",
    "подписки": "🔁",
    "здоровье": "💊",
    "покупки": "🛍",
    "развлечения": "🎟",
    "подарки": "🎁",
    "прочее": "▫️",
    "серверы": "🖥",
    "серверы/инфра": "🖥",
    "софт и ai": "🤖",
    "реклама": "📣",
    "подрядчики": "🤝",
    "комиссии": "💱",
    "комиссии/обмен": "💱",
    "закупка товара": "📦",
    "переводы людям": "👤",
    "клиенты": "💼",
    "продажи": "🏪",
    "продажи (бизнес)": "🏪",
    "зарплата": "💵",
}
NEW_CATEGORY_EMOJI = "🏷"


def category_emoji(name: str | None) -> str:
    if not name:
        return NEW_CATEGORY_EMOJI
    key = name.replace("🆕", "").strip().lower()
    if key in CATEGORY_EMOJI:
        return CATEGORY_EMOJI[key]
    head = key.split("/")[0].strip()
    return CATEGORY_EMOJI.get(head, NEW_CATEGORY_EMOJI)


# --------------------------------------------------------------------- transactions
KIND_LABELS = {
    "expense": "Расход",
    "income": "Доход",
    "refund": "Возврат",
    "transfer": "Перевод",
    "exchange": "Обмен",
    "adjustment": "Корректировка",
}


# --------------------------------------------------------------------- debts (DEBTS.md §8)
#: card titles of debt operations («500 000 ₫ · Дал в долг: Коля»); forgive of my own debt
#: (income on the debt account) is «Долг прощён»
DEBT_OP_TITLES = {
    "lend": "Дал в долг",
    "repay_in": "Возврат долга",
    "borrow": "Занял",
    "repay_out": "Вернул долг",
    "forgive": "Списан долг",
}
#: person card history (DEBTS.md §10): short labels
DEBT_HISTORY_LABELS = {
    "lend": "Дал в долг",
    "repay_in": "Возврат",
    "borrow": "Занял",
    "repay_out": "Вернул долг",
    "forgive": "Списан",
}
#: money comes INTO a real account (the real side is ``counter_account``, DEBTS.md §3.1)
DEBT_INFLOW_OPS = frozenset({"repay_in", "borrow"})
DEBT_OUTFLOW_OPS = frozenset({"lend", "repay_out"})


def tx_debt(tx: dict[str, Any]) -> dict[str, Any] | None:
    """``TransactionOut.debt`` ({id, name, op}) or None for ordinary operations."""
    d = tx.get("debt")
    return d if isinstance(d, dict) and d.get("op") else None


def debt_title(op: str | None, name: Any, *, kind: str | None = None) -> str:
    """«Дал в долг: Коля» (plain, unescaped). The name always goes after a colon (nominative)."""
    label = DEBT_OP_TITLES.get(str(op), "Долг")
    if op == "forgive" and kind == "income":
        label = "Долг прощён"
    who = str(name or "").strip()
    return f"{label}: {who}" if who else label


def tx_debt_label(tx: dict[str, Any]) -> str | None:
    d = tx_debt(tx)
    if d is None:
        return None
    return debt_title(d.get("op"), d.get("name"), kind=tx.get("kind"))


def debt_state(amount: Any, currency: str | None, name: Any = None) -> str:
    """Signed debt balance → words: «Коля: долг тебе 300 000 ₫» / «ты должен 1 000 000 ₫» /
    «долг закрыт». Without ``name``: «долг тебе 300 000 ₫». No gendered «X должен» about the
    person (a name ending in -а/-я can be Коля or Маша) — same wording as the backend cards."""
    amt = to_decimal(amount)
    if amt is None or amt == 0:
        return "долг закрыт"
    body = format_money(abs(amt), currency)
    if amt > 0:
        who = str(name or "").strip()
        return f"{who}: долг тебе {body}" if who else f"долг тебе {body}"
    return f"ты должен {body}"


def tx_label(tx: dict[str, Any]) -> str:
    """Plain (unescaped) one-liner: debt label → backend ``title`` → category → kind label."""
    debt = tx_debt_label(tx)
    if debt:
        return debt
    title = (tx.get("title") or "").strip()
    if title:
        return title
    cat = tx.get("category") or {}
    if cat.get("name"):
        return str(cat["name"])
    return KIND_LABELS.get(str(tx.get("kind")), "Операция")


def tx_category_name(tx: dict[str, Any]) -> str:
    debt = tx_debt_label(tx)
    if debt:
        return debt
    cat = tx.get("category") or {}
    if cat.get("name"):
        return str(cat["name"])
    return KIND_LABELS.get(str(tx.get("kind")), "Операция")


def tx_amount(tx: dict[str, Any], *, signed: bool = False) -> str:
    """Amount in the original currency. ``signed``: −expense, +income/refund (lists)."""
    kind = tx.get("kind")
    amount = tx.get("amount")
    currency = tx.get("currency")
    if kind == "adjustment" and tx.get("account_amount") is not None:
        return format_money(tx.get("account_amount"), tx.get("account_asset") or currency,
                            sign="always")
    if not signed:
        return format_money(amount, currency)
    op = (tx_debt(tx) or {}).get("op")
    if op in DEBT_OUTFLOW_OPS:  # money left a real account
        return format_money(neg(amount), currency)
    if op in DEBT_INFLOW_OPS:
        return format_money(amount, currency, sign="always")
    if kind == "expense":
        return format_money(neg(amount), currency)
    if kind in ("income", "refund"):
        return format_money(amount, currency, sign="always")
    return format_money(amount, currency)

"""Amount tokens. «к/k/тыс» ×1000, «тр» ×1000 RUB, «м/m/млн/лям/kk» ×1e6,
«1.5м», «1,5м», «450000», «450 000», «450.000», «85» (< 1000 with VND and small_thousands →
×1000), «180$», «$180», «15 долларов», «3 тона», «300 HMSTR» (coin candidate)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.core.money import AMOUNT_MAX
from app.services.parser.currencies import (
    WORD_TO_CODE,
    coin_candidate,
    detect_currency,
    is_crypto,
)
from app.services.parser.text import Token, clean_original, normalize, tokenize

K = Decimal(1000)
M = Decimal(1_000_000)
B = Decimal(1_000_000_000)

MULTIPLIERS: dict[str, Decimal] = {
    "к": K, "k": K, "тыс": K, "тыс.": K, "тысяч": K, "тысячи": K, "тысяча": K, "тысячу": K,
    "тыщ": K, "тыщи": K, "тыща": K, "тыщу": K, "thousand": K, "косарь": K, "косаря": K,
    "косарей": K, "кк": M, "kk": M, "м": M, "m": M, "млн": M, "млн.": M, "мл": M, "млн.р": M,
    "миллион": M, "миллиона": M, "миллионов": M, "миллионы": M, "мио": M, "mln": M,
    "million": M, "millions": M, "mil": M, "лям": M, "ляма": M, "лямов": M, "лямы": M,
    "лимон": M, "лимона": M, "лимонов": M, "млрд": B, "млрд.": B, "миллиард": B, "миллиарда": B,
    "миллиардов": B, "bn": B, "billion": B,
    # Vietnamese: triệu = million, nghìn/ngàn = thousand («5tr», «200 nghìn»)
    "tr": M, "triệu": M, "trieu": M, "nghìn": K, "nghin": K, "ngàn": K, "ngan": K,
}
# Single letters that need care when written with a space («100 к ужину»).
_SPACED_RISKY = {"к", "k", "м", "m", "мл"}
# Glued suffix prefixes for combos like «85kđ», «2kk$», «50тр».
_GLUED_PREFIXES = ("кк", "kk", "к", "k", "м", "m", "млн", "тыс", "tr")

# Number followed by these words is a quantity, not money.
QTY_WORDS = {
    "%", "шт", "штук", "штуки", "штука", "раз", "раза", "дня", "дней", "день", "недели",
    "неделю", "недель", "неделя", "час", "часа", "часов", "мин", "минут", "минуты", "сек",
    "кг", "г", "гр", "л", "км", "человек", "чел", "лет", "год", "года", "году", "мес",
    "месяц", "месяца", "месяцев", "ночи", "ночей", "ночь", "порции", "порций", "порция",
    "x", "х", "gb", "гб", "tb", "мб", "mb", "days", "hours", "pcs", "числа", "го", "е",
}


@dataclass(slots=True)
class AmountTok:
    start: int
    end: int
    number: str  # numeric text («1.5», «450 000»)
    mult: Decimal | None = None
    currency: str | None = None  # explicit currency written next to the number
    currency_kind: str = ""  # symbol | word | code | ticker | known_asset | suffix
    coin_candidate: bool = False
    sign: str | None = None  # "+" / "-" written right before
    sign_idx: int | None = None
    token_idx: list[int] = field(default_factory=list)
    raw: str = ""

    def value(self, currency: str | None, small_thousands: bool,
              default_currency: str = "VND") -> Decimal:
        cur = (currency or self.currency or default_currency).upper()
        num, _ = parse_number(self.number, cur, self.mult is not None)
        v = num * (self.mult or 1)
        if self.mult is None and cur == "VND" and small_thousands and v < 1000:
            v *= K
        return clean_decimal(v)

    def ambiguous(self, currency: str | None, default_currency: str = "VND") -> bool:
        cur = (currency or self.currency or default_currency).upper()
        return parse_number(self.number, cur, self.mult is not None)[1]


def clean_decimal(d: Decimal) -> Decimal:
    """Exact value without exponent / trailing zeros («1500000», «0.5»)."""
    try:
        if d == d.to_integral_value():
            return d.quantize(Decimal(1))
        return d.normalize()
    except InvalidOperation:  # beyond context precision — caller rejects it as too big
        return d


def parse_number(s: str, currency: str | None, has_mult: bool) -> tuple[Decimal, bool]:
    """Parse «450000», «450 000», «450.000», «1,5», «1.250.000», «1,234.56».

    Returns (value, ambiguous). ``1.500`` style (one separator + 3 digits) is a thousands
    separator for VND/RUB/unknown currency, a decimal point for crypto (flagged ambiguous).
    """
    s = s.replace(" ", "")
    try:
        if "." not in s and "," not in s:
            return Decimal(s), False
        parts = s.replace(",", ".").split(".")
        if "." in s and "," in s:
            if len(parts[-1]) == 3 and all(len(p) == 3 for p in parts[1:]):
                return Decimal("".join(parts)), False
            return Decimal("".join(parts[:-1]) + "." + parts[-1]), False
        if len(parts) > 2:
            if 1 <= len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:]):
                return Decimal("".join(parts)), False
            raise ValueError(f"не понял число {s!r}")
        ip, fp = parts
        if len(fp) == 3 and 1 <= len(ip) <= 3 and ip.strip("0") and not has_mult:
            cur = (currency or "VND").upper()
            if cur in ("VND", "RUB"):
                return Decimal(ip + fp), False
            if is_crypto(cur):
                return Decimal(ip + "." + fp), True
            return Decimal(ip + fp), True
        return Decimal(ip + "." + fp), False
    except InvalidOperation as e:
        raise ValueError(f"не понял число {s!r}") from e


def split_suffix(word: str) -> tuple[Decimal | None, str | None] | None:
    """«к» → (1000, None); «тр» → (1000, RUB); «kđ» → (1000, VND); «usdt» → (None, USDT)."""
    if word in MULTIPLIERS:
        return MULTIPLIERS[word], None
    if word in ("тр", "т.р", "тыр", "тыс.руб", "ткр"):
        return K, "RUB"
    code = WORD_TO_CODE.get(word)
    if code:
        return None, code
    for p in _GLUED_PREFIXES:
        if word.startswith(p) and len(word) > len(p):
            rest = word[len(p):].lstrip(".")
            if rest in WORD_TO_CODE:
                return MULTIPLIERS[p], WORD_TO_CODE[rest]
    return None


_SYM_CUR = {"$": "USD", "₫": "VND", "₽": "RUB", "€": "EUR", "฿": "THB", "¥": "CNY"}
_PRE_CODES = {"usd", "usdt", "usdc", "vnd", "rub", "eur", "ton", "btc", "eth", "trx", "thb"}


def _gap(a: Token, b: Token, norm: str) -> int:
    """Characters between tokens a and b; -1 if something other than spaces is in between."""
    between = norm[a.end : b.start]
    return len(between) if between.strip() == "" and "\n" not in between else -1


def scan_amounts(toks: list[Token], norm: str, *, known_assets: set[str] | None = None,
                 not_tickers: set[str] | None = None, role: str = "amount") -> list[AmountTok]:
    """Find amounts among free tokens and mark consumed tokens with ``role``.
    ``not_tickers``: alias words (accounts/categories/merchants) never taken as coin tickers."""
    known = known_assets or set()
    vocab = not_tickers or set()
    out: list[AmountTok] = []
    n = len(toks)
    for i, t in enumerate(toks):
        if t.kind != "num" or not t.free or "/" in t.text:
            continue
        j = i + 1
        nxt = toks[j] if j < n else None
        if nxt is not None and nxt.free and _gap(t, nxt, norm) in (0, 1):
            if nxt.text in QTY_WORDS or (nxt.kind == "sym" and nxt.text == "%"):
                t.role = "qty"
                continue
        if i > 0 and toks[i - 1].kind == "sym" and toks[i - 1].text in "#№":
            t.role = "qty"
            continue
        a = AmountTok(start=t.start, end=t.end, number=t.text, token_idx=[i])
        # prefix currency: «$180», «₫85k», «usdt 300»
        k = i - 1
        if k >= 0 and toks[k].free and _gap(toks[k], t, norm) in (0, 1):
            p = toks[k]
            if p.kind == "sym" and p.text in _SYM_CUR:
                a.currency, a.currency_kind = _SYM_CUR[p.text], "symbol"
            elif p.kind == "word" and p.text in _PRE_CODES and not (
                k > 0 and toks[k - 1].kind == "num"
            ):
                a.currency, a.currency_kind = detect_currency(p.text), "code"
            if a.currency:
                a.start = p.start
                a.token_idx.insert(0, k)
                k -= 1
        if k >= 0 and toks[k].free and toks[k].kind == "sym" and toks[k].text in "+-":
            if _gap(toks[k], toks[a.token_idx[0]], norm) in (0, 1):
                a.sign, a.sign_idx = toks[k].text, k
        # multiplier / glued suffix
        if nxt is not None and nxt.free and nxt.kind == "word":
            g = _gap(t, nxt, norm)
            sfx = split_suffix(nxt.text) if g in (0, 1) else None
            if sfx is not None:
                mult, cur = sfx
                risky = g == 1 and nxt.text in _SPACED_RISKY and _followed_by_word(toks, j, norm)
                if mult is not None and not risky:
                    a.mult = mult
                    if cur and not a.currency:
                        a.currency, a.currency_kind = cur, "suffix"
                    a.token_idx.append(j)
                    a.end = nxt.end
                    j += 1
                elif mult is None and cur and not a.currency:
                    a.currency, a.currency_kind = cur, "word"
                    a.token_idx.append(j)
                    a.end = nxt.end
                    j += 1
        # post currency: «180$», «15 долларов», «300 USDT», «2 вьетнамских донга»
        if not a.currency and j < n:
            c = toks[j]
            last = toks[a.token_idx[-1]]
            g = _gap(last, c, norm)
            if c.free and g in (0, 1):
                if c.kind == "sym" and c.text in _SYM_CUR:
                    a.currency, a.currency_kind = _SYM_CUR[c.text], "symbol"
                elif c.kind == "word":
                    code = detect_currency(c.text)
                    if code:
                        a.currency, a.currency_kind = code, "word"
                    elif c.text.upper() in known:
                        a.currency, a.currency_kind = c.text.upper(), "known_asset"
                    elif c.text not in vocab:
                        cand = coin_candidate(c.text)
                        if cand:
                            a.currency, a.currency_kind = cand, "ticker"
                            a.coin_candidate = True
                if a.currency:
                    a.token_idx.append(j)
                    a.end = c.end
                    j += 1
                    # «вьетнамских донгов»: swallow a second word of the same currency
                    if j < n and toks[j].kind == "word" and toks[j].free and _gap(
                        c, toks[j], norm
                    ) == 1 and detect_currency(toks[j].text) == a.currency:
                        a.token_idx.append(j)
                        a.end = toks[j].end
        for idx in a.token_idx:
            toks[idx].role = role
        if a.sign_idx is not None:
            toks[a.sign_idx].role = role + "_sign"
        out.append(a)
    return out


def _followed_by_word(toks: list[Token], j: int, norm: str) -> bool:
    """True if token j is followed by a non-currency word («100 к ужину»)."""
    if j + 1 >= len(toks):
        return False
    nx = toks[j + 1]
    if nx.kind != "word" or _gap(toks[j], nx, norm) != 1:
        return False
    return detect_currency(nx.text) is None


def fill_raw(amounts: list[AmountTok], orig: str) -> None:
    for a in amounts:
        a.raw = orig[a.start : a.end].strip()


def parse_amount(raw: str, currency: str | None, small_thousands: bool = True, *,
                 allow_zero: bool = False) -> Decimal:
    """Exact Decimal from a raw token as written (suffixes applied, currency words ignored).
    ``currency`` is the resolved currency (None → currency written in ``raw``, else VND default
    for the small-number rule). Raise ValueError if not a positive amount (``allow_zero`` lets
    «0» through for balance reconciliation). Used by the AI validator too."""
    if raw is None:
        raise ValueError("пустая сумма")
    text = str(raw).strip()
    if not text:
        raise ValueError("пустая сумма")
    if len(text) > 64:
        raise ValueError("слишком длинная сумма")
    orig = clean_original(text)
    norm = normalize(text)
    toks = tokenize(norm, orig)
    amounts = scan_amounts(toks, norm)
    if len(amounts) != 1:
        raise ValueError(f"не понял сумму {raw!r}")
    leftovers = [t for t in toks if t.free and t.kind in ("num", "word")]
    if any(t.kind == "num" for t in leftovers):
        raise ValueError(f"не понял сумму {raw!r}")
    a = amounts[0]
    cur = (currency or a.currency or "VND").upper()
    v = a.value(cur, small_thousands)
    if v < 0 or (v == 0 and not allow_zero):
        raise ValueError("сумма должна быть больше нуля")
    if v >= AMOUNT_MAX:
        raise ValueError("слишком большая сумма")
    return v


__all__ = ["AmountTok", "MULTIPLIERS", "parse_amount", "parse_number", "scan_amounts"]

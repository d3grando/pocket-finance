"""Decimal money helpers shared by all backend modules (docs/ARCH.md §4, DESIGN.md §3).

Never use float for money. All amounts are ``Decimal``; JSON carries them as strings.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

NBSP = " "
MINUS = "−"
APPROX = "≈"

# Fiat currencies we know about: code -> (symbol, decimals for storage of account amounts)
FIAT: dict[str, tuple[str, int]] = {
    "VND": ("₫", 0),
    "USD": ("$", 2),
    "RUB": ("₽", 2),
    "EUR": ("€", 2),
    "THB": ("฿", 2),
    "CNY": ("¥", 2),
}
# Stablecoins: shown with 2 decimals.
STABLECOINS = {"USDT", "USDC", "DAI", "FDUSD"}
CRYPTO_DECIMALS = 8
STABLE_DECIMALS = 6  # display: at least 2 decimals when not whole, up to 6 (web/src/lib/format.ts)

AMOUNT_MAX = Decimal("1e19")  # NUMERIC(28,8) => 20 integer digits


def is_fiat(code: str) -> bool:
    return code.upper() in FIAT


def is_crypto(code: str) -> bool:
    return not is_fiat(code)


def asset_decimals(code: str) -> int:
    """Precision used when computing account_amount in this asset."""
    code = code.upper()
    if code in FIAT:
        return FIAT[code][1]
    if code in STABLECOINS:
        return 2
    return CRYPTO_DECIMALS


def to_decimal(value: object) -> Decimal:
    """Strictly convert str/int/Decimal to Decimal. Floats are rejected."""
    if isinstance(value, bool):
        raise TypeError("bool is not money")
    if isinstance(value, float):
        raise TypeError("float is not allowed for money")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, (int, str)):
        try:
            d = Decimal(str(value).strip().replace(NBSP, "").replace(" ", ""))
        except InvalidOperation as e:
            raise ValueError(f"not a decimal: {value!r}") from e
    else:
        raise TypeError(f"unsupported money type {type(value)!r}")
    if not d.is_finite():
        raise ValueError("amount must be finite")
    return d


def quantize_asset(amount: Decimal, asset: str) -> Decimal:
    """Round to asset precision with banker's rounding."""
    q = Decimal(1).scaleb(-asset_decimals(asset))
    return amount.quantize(q, rounding=ROUND_HALF_EVEN)


def normalize(amount: Decimal) -> str:
    """Canonical JSON string: no exponent, no trailing zeros ("150000", "3.5", "0.0215")."""
    if amount == 0:
        return "0"
    s = format(amount.normalize(), "f")
    return s


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
    amount: Decimal | str | int,
    currency: str,
    *,
    sign: str = "auto",
    approx: bool = False,
) -> str:
    """Format money exactly like web/src/lib/format.ts (DESIGN.md §3.2).

    sign: "auto" (minus only), "always" (+/−), "never".
    Examples: 1250000 VND -> "1 250 000 ₫"; -180 USD -> "−$180.00"; 3 TON -> "3 TON".
    """
    cur = currency.upper()
    # round to the display precision first: «4990.004 ₽» is «4 990 ₽», «-0.004 $» is «$0.00»
    if cur == "VND":
        places = 0
    elif cur == "RUB" or cur in FIAT:
        places = 2
    elif cur in STABLECOINS:
        places = STABLE_DECIMALS
    else:
        places = CRYPTO_DECIMALS
    amt = to_decimal(amount).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    if amt == 0:
        amt = abs(amt)
    if cur == "VND":
        body = _number(amt, 0, 0) + NBSP + "₫"
    elif cur == "RUB":
        has_kop = abs(amt) != abs(amt).to_integral_value()
        body = _number(amt, 2 if has_kop else 0, 2) + NBSP + "₽"
    elif cur in FIAT and cur != "VND":
        sym = FIAT[cur][0]
        body = sym + _number(amt, 2, 2)
    elif cur in STABLECOINS:
        # «300 USDT», «20.50 USDT», «20.2552 USDT»: the card echoes what was written
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

"""Currency / asset words: ₫ донг vnd; $ бакс доллар usd; ₽ руб rub;
usdt юсдт тезер; тон/тона/тонов ton; btc биток; eth эфир; etc. USD ≠ USDT.

``detect_currency`` knows symbols, codes and Russian/English word forms. An unknown latin
ticker (2–6 letters) written next to a number is only a *candidate* coin
(``coin_candidate``); analytics checks it against the rates provider.
"""

from __future__ import annotations

import re

from app.services.parser.text import normalize

FIAT_CODES = {"VND", "USD", "RUB", "EUR", "THB", "CNY", "GBP", "KZT", "UAH"}

_SYMBOLS: dict[str, str] = {
    "₫": "VND",
    "đ": "VND",
    "$": "USD",
    "₽": "RUB",
    "€": "EUR",
    "฿": "THB",
    "¥": "CNY",
}

# code → word forms (normalised: lowercase, ё→е)
_WORDS: dict[str, tuple[str, ...]] = {
    "VND": ("vnd", "vnđ", "донг", "донга", "донгов", "донги", "донгах", "донгами", "dong",
            "dongs", "вьетнамских"),
    "USD": ("usd", "бакс", "бакса", "баксов", "баксы", "баксах", "баксами", "доллар", "доллара",
            "долларов", "доллары", "долларах", "долларами", "долл", "дол", "dollar", "dollars",
            "buck", "bucks", "уе", "у.е", "зелени", "грин", "гринов"),
    "USDT": ("usdt", "юсдт", "юсдэт", "тезер", "тезера", "тезеров", "тезеры", "тезерах", "tether",
             "usdt-trc", "trc20", "усдт"),
    "USDC": ("usdc", "юсдс", "юсдц"),
    "RUB": ("rub", "rur", "руб", "рубль", "рубля", "рублей", "рубли", "рублях", "рублями", "р",
            "руб.", "деревянных"),
    "EUR": ("eur", "евро", "euro", "euros"),
    "THB": ("thb", "бат", "бата", "батов", "баты", "baht"),
    "CNY": ("cny", "rmb", "юань", "юаня", "юаней", "юани", "yuan"),
    "GBP": ("gbp", "фунт", "фунта", "фунтов"),
    "KZT": ("kzt", "тенге", "тг"),
    "UAH": ("uah", "грн", "гривна", "гривны", "гривен"),
    "TON": ("ton", "тон", "тона", "тонов", "тоны", "тонах", "тонкоин", "тонкоина", "тонкоинов",
            "toncoin", "тонн", "тонны"),
    "BTC": ("btc", "биток", "битка", "битков", "битке", "битки", "биткоин", "биткоина",
            "биткоинов", "биткоинах", "биткойн", "биткойна", "биткойнов", "bitcoin", "xbt"),
    "ETH": ("eth", "эфир", "эфира", "эфиров", "эфире", "эфиры", "эфириум", "эфириума", "ether",
            "ethereum", "етх", "эth"),
    "SOL": ("sol", "солана", "соланы", "солан", "солану", "solana", "сол", "сола", "солов"),
    "TRX": ("trx", "трон", "трона", "тронов", "троны", "tron", "трх"),
    "NOT": ("not", "ноткоин", "ноткоина", "ноткоинов", "notcoin", "нот", "нотов", "ноты"),
    "BNB": ("bnb", "бнб", "бинанс-коин"),
    "XRP": ("xrp", "рипл", "рипла", "риплов", "ripple", "хрп"),
    "DOGE": ("doge", "доги", "дога", "догов", "додж", "доджа", "доджей", "догикоин", "догикоина",
             "догикоинов", "dogecoin", "дож", "дожа"),
    "LTC": ("ltc", "лайткоин", "лайткоина", "лайткоинов", "litecoin", "лайт"),
    "ADA": ("ada", "кардано", "cardano"),
    "DOT": ("dot", "полкадот", "полкадота", "polkadot"),
    "AVAX": ("avax", "авакс", "аваксов", "avalanche"),
    "LINK": ("link", "чейнлинк", "chainlink"),
    "POL": ("pol", "matic", "матик", "матика", "матиков", "polygon"),
    "SHIB": ("shib", "шиба", "шибы", "шиб", "шибов"),
    "PEPE": ("pepe", "пепе"),
    "XMR": ("xmr", "монеро", "monero"),
    "TRUMP": ("trump", "трамп", "трампа", "трампов"),
    "HMSTR": ("hmstr", "хамстер", "хамстера", "хамстеров"),
    "DOGS": ("dogs", "догс", "догсов"),
    "ATOM": ("atom", "атом", "атома", "атомов", "cosmos"),
    "ARB": ("arb", "арбитрум", "arbitrum"),
    "APT": ("apt", "аптос", "aptos"),
    "SUI": ("sui", "суи"),
    "NEAR": ("near", "нир"),
    "DAI": ("dai",),
    "FDUSD": ("fdusd",),
    "WBTC": ("wbtc",),
    "MAJOR": ("major",),
    "CATI": ("cati",),
    "OP": ("op",),
}

WORD_TO_CODE: dict[str, str] = {}
for _code, _forms in _WORDS.items():
    for _f in _forms:
        WORD_TO_CODE.setdefault(normalize(_f), _code)
for _sym, _code in _SYMBOLS.items():
    WORD_TO_CODE[_sym] = _code
WORD_TO_CODE[normalize("đ")] = "VND"

KNOWN_CRYPTO = {c for c in _WORDS if c not in FIAT_CODES}
STABLECOINS = {"USDT", "USDC", "DAI", "FDUSD"}

# Latin words that are never tickers even right after a number.
_TICKER_STOP = {
    "k", "kk", "m", "mm", "km", "kg", "g", "gr", "gb", "mb", "tb", "kb", "ml", "l", "pcs", "pc",
    "x", "h", "hr", "hrs", "min", "mins", "sec", "ms", "am", "pm", "day", "days", "week",
    "weeks", "month", "year", "years", "times", "pro", "plus", "max", "mini", "ultra", "lite",
    "team", "plan", "gpu", "gpus", "cpu", "ram", "ssd", "hdd", "vps", "usb", "tv", "ok", "for",
    "the", "and", "on", "in", "at", "to", "of", "from", "with", "by", "per", "each", "last",
    "ago", "today", "coffee", "taxi", "food", "lunch", "or", "is", "it", "my", "me", "we",
    "you", "he", "she", "a", "an", "go", "do", "so", "no", "yes", "up", "out", "off", "via",
    "mln", "mil", "bln", "thousand", "million", "rub", "usd", "vnd", "eur", "pay", "paid",
    "got", "sent", "fee", "tips", "tip", "bonus", "cash", "card", "bank", "qr", "ai", "api",
    "gpt", "vpn", "sim", "esim", "beer", "pizza", "grab", "be", "xanh", "shop",
    "spent", "bought", "received", "income", "salary", "refund", "rent", "hotel", "gym",
}

_TICKER_RE = re.compile(r"^[a-z]{2,6}$")


def detect_currency(token: str) -> str | None:
    """Currency code for a word/symbol, or None.

    ``detect_currency("тона") == "TON"``, ``detect_currency("$") == "USD"``,
    ``detect_currency("юсдт") == "USDT"``. Unknown tickers return None (see coin_candidate).
    """
    if not token:
        return None
    t = normalize(token.strip()).strip(".") or token.strip()
    if t in WORD_TO_CODE:
        return WORD_TO_CODE[t]
    if t.upper() in _WORDS:
        return t.upper()
    return None


def coin_candidate(token: str) -> str | None:
    """Unknown latin ticker 2–6 letters → upper-case candidate code, else None."""
    t = normalize(token)
    if not _TICKER_RE.match(t) or t in _TICKER_STOP or t in WORD_TO_CODE:
        return None
    return t.upper()


def is_crypto(code: str) -> bool:
    return code.upper() not in FIAT_CODES


def is_fiat(code: str) -> bool:
    return code.upper() in FIAT_CODES

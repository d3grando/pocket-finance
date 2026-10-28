"""Built-in vocabulary: default account aliases, category keywords,
operation markers. User-specific aliases/merchants/categories come from ``ParseContext`` and
take priority; built-ins are used only when they map onto an existing context entry (or the
context is empty)."""

from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- accounts
# canonical name (accounts.seed), currency (None = multi-asset), kind, aliases.
# A neutral starter set; brands of a particular installation go to PARSER_LEXICON_EXTRA.
BUILTIN_ACCOUNTS: list[tuple[str, str | None, str, tuple[str, ...]]] = [
    ("Карта", "VND", "bank", ("карта", "банк", "банком", "банковской", "qr", "по qr", "кьюар",
                              "кюар", "qr-код", "card")),
    ("Наличные", "VND", "cash", ("нал", "налом", "наличные", "наличными", "наличных", "наличка",
                                "наличкой", "налички", "наличку", "кэш", "кеш", "кэшем", "кешем",
                                "cash", "наликом", "наликами")),
    ("Кошелёк", None, "crypto_wallet", ("кошелёк", "кошелек", "кошелька", "кошельку",
                                        "кошельком", "кошельке", "крипто", "крипта", "wallet")),
    ("Биржа", None, "exchange", ("биржа", "бирже", "биржи", "биржу", "биржей", "exchange")),
    ("Карта RUB", "RUB", "card", ("рублевая карта", "рублевой картой", "рублевой карты",
                                  "рублевую карту", "рублевой карте", "карта руб", "руб карта",
                                  "карта rub", "rub карта", "карта рф", "картой рф", "рф карта",
                                  "российская карта", "российской картой", "рублевая",
                                  "рублевой")),
]

#: env: path to a JSON file with more built-in accounts, e.g. the brands of the installation
#: owner: ``{"accounts": [["Wise", "USD", "bank", ["wise", "вайз"]], …]}`` — the same tuple as
#: ``BUILTIN_ACCOUNTS`` (canonical name, currency or null = multi-asset, kind, aliases). An entry
#: with a canonical name that already exists only adds its aliases. A bad file is ignored with
#: a WARNING (the parser keeps working on the built-ins).
LEXICON_EXTRA_ENV = "PARSER_LEXICON_EXTRA"
_EXTRA_KINDS = {"bank", "cash", "card", "crypto_wallet", "exchange", "other"}
_EXTRA_CURRENCY = re.compile(r"^[A-Z0-9]{2,16}$")
_EXTRA_MAX_ACCOUNTS = 100
_EXTRA_MAX_ALIASES = 50


def load_lexicon_extra(path: str) -> list[tuple[str, str | None, str, tuple[str, ...]]]:
    """Read and validate a ``PARSER_LEXICON_EXTRA`` file; ValueError on any problem."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"cannot read: {type(e).__name__}") from e
    rows = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) > _EXTRA_MAX_ACCOUNTS:
        raise ValueError(f'"accounts" must be a list of at most {_EXTRA_MAX_ACCOUNTS} entries')
    out: list[tuple[str, str | None, str, tuple[str, ...]]] = []
    for i, row in enumerate(rows):
        if not (isinstance(row, list) and len(row) == 4):
            raise ValueError(f"accounts[{i}]: expected [name, currency|null, kind, [aliases]]")
        canon, cur, kind, aliases = row
        if not isinstance(canon, str) or not 0 < len(canon.strip()) <= 64:
            raise ValueError(f"accounts[{i}]: bad name")
        if cur is not None and not (isinstance(cur, str) and _EXTRA_CURRENCY.match(cur)):
            raise ValueError(f"accounts[{i}]: bad currency")
        if kind not in _EXTRA_KINDS:
            raise ValueError(f"accounts[{i}]: bad kind")
        if not (isinstance(aliases, list) and len(aliases) <= _EXTRA_MAX_ALIASES and all(
                isinstance(a, str) and 0 < len(a.strip()) <= 64 for a in aliases)):
            raise ValueError(f"accounts[{i}]: aliases must be ≤ {_EXTRA_MAX_ALIASES} strings")
        out.append((canon.strip(), cur, kind, tuple(a.strip().lower() for a in aliases)))
    return out


def merge_accounts(
    base: list[tuple[str, str | None, str, tuple[str, ...]]],
    extra: list[tuple[str, str | None, str, tuple[str, ...]]],
) -> list[tuple[str, str | None, str, tuple[str, ...]]]:
    """``base`` + ``extra``: a known canonical name (case-insensitive) gets the new aliases."""
    out = list(base)
    pos = {canon.lower(): i for i, (canon, *_rest) in enumerate(out)}
    for canon, cur, kind, aliases in extra:
        i = pos.get(canon.lower())
        if i is None:
            pos[canon.lower()] = len(out)
            out.append((canon, cur, kind, aliases))
        else:
            c0, cur0, kind0, al0 = out[i]
            out[i] = (c0, cur0, kind0, al0 + tuple(a for a in aliases if a not in al0))
    return out


def _apply_extra() -> None:
    global BUILTIN_ACCOUNTS
    path = os.environ.get(LEXICON_EXTRA_ENV, "").strip()
    if not path:
        return
    try:
        extra = load_lexicon_extra(path)
    except ValueError as e:
        log.warning("%s ignored: %s", LEXICON_EXTRA_ENV, e)
        return
    BUILTIN_ACCOUNTS = merge_accounts(BUILTIN_ACCOUNTS, extra)
    log.info("%s: %s extra accounts", LEXICON_EXTRA_ENV, len(extra))


_apply_extra()

# Generic card words: resolved to the card/bank account that is default for the operation's
# currency (₫ → Карта, ₽ → Карта RUB).
CARD_WORDS = ("карта", "картой", "карте", "карту", "карты", "карточка", "карточкой",
              "карточке", "карточку", "card", "по карте", "с карты")

# ---------------------------------------------------------------- categories
# (default category name, kind, is_business, priority, keywords)
# priority: 3 brand/specific, 2 normal, 1 generic. Context names/aliases get priority 2.5.
BUILTIN_CATEGORIES: list[tuple[str, str, bool, int, tuple[str, ...]]] = [
    ("Еда", "expense", False, 2, (
        "еда", "еду", "еды", "едой", "еде", "поесть", "покушать", "обед", "ужин", "завтрак",
        "ланч", "бранч", "перекус", "ресторан", "кафе", "столовая", "бургер", "пицца", "пиццу",
        "суши", "роллы", "шаурма", "шаверма", "фо", "бун", "суп", "доставка еды", "грабфуд",
        "grabfood", "shopeefood", "food", "lunch", "dinner", "breakfast", "restaurant", "meal",
        "банми", "бань ми", "pho", "мак", "макдак", "kfc", "кфс", "стритфуд")),
    ("Продукты", "expense", False, 2, (
        "продукты", "продуктов", "продуктами", "супермаркет", "groceries", "grocery", "winmart",
        "винмарт", "lotte", "лотте", "рынок", "рынке", "овощи", "фрукты", "вода", "воду",
        "молоко", "хлеб", "яйца", "мясо", "circle k", "circlek", "family mart", "familymart",
        "7-eleven", "ministop", "продуктовый", "emart", "аеон", "aeon")),
    ("Кофе", "expense", False, 2, (
        "кофе", "кофейня", "кофейне", "капучино", "латте", "американо", "эспрессо", "раф",
        "coffee", "highlands", "хайлендс", "starbucks", "старбакс", "кофеек", "кофеёк",
        "phuc long", "katinat", "катинат", "cong", "конг")),
    ("Такси/транспорт", "expense", False, 2, (
        "такси", "taxi", "grab", "граб", "грэб", "грабе", "xanh sm", "xanhsm", "gojek", "гоуджек",
        "uber", "убер", "яндекс такси", "байк", "байка", "мото", "мотобайк", "бензин", "бенз",
        "заправка", "топливо", "fuel", "парковка", "parking", "автобус", "метро", "транспорт",
        "поезд", "электричка", "проезд", "трансфер", "аренда байка")),
    ("Жильё", "expense", False, 2, (
        "аренда", "аренду", "квартира", "квартиру", "жилье", "отель", "гостиница", "хостел",
        "airbnb", "booking", "букинг", "rent", "hotel", "коммуналка", "коммуналку",
        "электричество", "свет", "за свет", "за воду", "уборка", "управляющая")),
    ("Связь", "expense", False, 2, (
        "связь", "интернет", "мобильная связь", "симка", "симку", "sim", "esim", "есим",
        "viettel", "вьеттел", "mobifone", "vinaphone", "пополнение телефона", "телефон пополнил",
        "internet", "роуминг", "vpn", "впн")),
    ("Подписки", "expense", False, 2, (
        "подписка", "подписку", "подписки", "subscription", "subscriptions", "netflix",
        "нетфликс", "spotify",
        "спотифай", "youtube premium", "ютуб премиум", "apple music", "icloud", "айклауд",
        "telegram premium", "тг премиум", "премиум", "premium", "яндекс плюс")),
    ("Здоровье", "expense", False, 2, (
        "аптека", "аптеке", "аптеку", "лекарства", "лекарство", "таблетки", "врач", "врачу",
        "больница", "клиника", "стоматолог", "зубной", "анализы", "здоровье", "pharmacy",
        "doctor", "dentist", "витамины", "медицина", "страховка", "спортзал", "gym", "зал")),
    ("Покупки", "expense", False, 1, (
        "покупки", "покупка", "покупку", "шопинг", "шоппинг", "shopping", "одежда", "одежду",
        "обувь", "кроссовки",
        "футболка", "футболку", "куртка", "куртку", "джинсы", "shopee", "шопи", "lazada",
        "лазада", "ozon", "озон", "wildberries", "вб", "aliexpress", "али", "алиэкспресс",
        "клавиатура", "клавиатуру", "наушники", "мышка", "мышку", "ноутбук", "телефон",
        "айфон", "iphone", "чехол", "зарядка", "зарядку", "техника", "электроника",
        "косметика", "косметику", "бытовая химия", "хозтовары")),
    ("Развлечения", "expense", False, 2, (
        "развлечения", "кино", "кинотеатр", "бар", "пиво", "пива", "коктейль", "коктейли",
        "клуб", "концерт", "игры", "игра", "steam", "стим", "кальян", "караоке", "боулинг",
        "movie", "cinema", "beer", "bar", "massage", "массаж", "спа", "экскурсия", "музей")),
    ("Подарки", "expense", False, 2, (
        "подарок", "подарки", "подарка", "подарком", "gift", "цветы", "букет", "сувенир")),
    ("Прочее", "expense", False, 1, ("прочее", "разное", "другое", "misc", "other")),
    ("Серверы/инфра", "expense", True, 3, (
        "сервер", "серверы", "сервера", "server", "servers", "vps", "впс", "vds", "хостинг",
        "hosting", "домен",
        "домены", "hetzner", "хетзнер", "vast", "vast.ai", "васт", "runpod", "ранпод",
        "digitalocean", "aws", "амазон aws", "gcp", "инфра", "инфраструктура", "gpu", "гпу",
        "датацентр", "cdn", "netcup", "contabo", "selectel", "селектел", "timeweb")),
    ("Софт и AI", "expense", True, 3, (
        "софт", "gpt", "chatgpt", "чатгпт", "чат гпт", "openai", "claude", "клод", "anthropic",
        "cursor", "курсор", "midjourney", "миджорни", "copilot", "jetbrains", "github",
        "гитхаб", "нейросеть", "нейросети", "api", "апи", "токены", "openrouter", "gemini",
        "perplexity", "лицензия", "лицензию", "софтина", "ai",
        # «за аккаунт gpt pro» — a bought account is software by default
        "аккаунт", "аккаунта", "аккаунты", "аккаунтов", "акк", "акка")),
    ("Реклама", "expense", True, 3, (
        "реклама", "рекламу", "рекламы", "ads", "таргет", "таргетинг", "продвижение",
        "посев", "посевы", "промо", "реклама в тг", "закуп рекламы")),
    ("Подрядчики", "expense", True, 3, (
        "подрядчик", "подрядчику", "подрядчики", "фрилансер", "фрилансеру", "фриланс",
        "дизайнеру", "дизайнер", "разработчику", "программисту", "контрактор", "аутсорс",
        "дизайн", "логотип", "верстка", "разработка", "копирайтер", "монтажер")),
    ("Комиссии/обмен", "expense", True, 3, (
        "комиссия", "комиссию", "комиссии", "fee", "fees", "спред", "обменник")),
    ("Закупка товара", "expense", True, 3, (
        "закупка", "закупку", "закуп", "для магазина", "на продажу", "для перепродажи",
        "на перепродажу", "под перепродажу", "товар", "товара")),
    ("Переводы людям", "expense", False, 1, ("переводы людям",)),
    ("Клиенты", "income", True, 2, (
        "клиент", "клиента", "клиентов", "клиенты", "клиенту", "заказчик", "заказчика",
        "client", "clients", "от клиента", "оплата от клиента", "проект", "заказ")),
    ("Продажи", "income", True, 2, (
        "продажа", "продажи", "продажу", "продаж", "выручка", "выручку", "sales", "sale",
        "с продаж", "магазин", "магазина", "shop")),
    ("Зарплата", "income", False, 3, (
        "зарплата", "зарплату", "зп", "аванс", "salary", "оклад", "получка")),
    ("Прочее", "income", False, 1, ("прочее", "разное", "другое", "misc", "other")),
]
# Keywords that by themselves make a phrase an income («зп 20м», «от клиента 300 usdt»).
INCOME_STRONG = {"зарплата", "зарплату", "зп", "аванс", "salary", "оклад", "получка", "клиент",
                 "клиента", "клиентов", "от клиента", "заказчика", "выручка", "выручку"}
# Words meaning «for resale» → for_resale=True, category «Закупка товара».
RESALE_PHRASES = ("для магазина", "на продажу", "для перепродажи", "на перепродажу",
                  "под перепродажу", "для шопа", "в магазин на продажу")
# «на покупку лицензии», «на оплату сервера», «на подписку netflix» — the purpose of a payment,
# like «за X». Prefixes of the noun right after «на».
PURPOSE_ON_PREFIXES = ("покупк", "оплат", "подписк", "продлен", "аренд", "лиценз", "заказ")
# Generic nouns that carry no meaning of their own: the purpose is the words after them.
PURPOSE_ON_GENERIC = ("покупк", "оплат")
TRANSFER_CATEGORY = "Переводы людям"
OTHER_CATEGORY = "Прочее"
SALES_CATEGORY = "Продажи"

# Brand keywords (a subset of the category keywords above) → canonical merchant name.
# «васт» → vast.ai → Серверы/инфра, the rule is learned and updated on correction —
# so a brand word yields ``merchant_ref`` (the dispatcher creates/learns the merchant rule).
BRANDS: dict[str, tuple[str, ...]] = {
    "vast.ai": ("vast", "vast.ai", "васт"),
    "Hetzner": ("hetzner", "хетзнер"),
    "RunPod": ("runpod", "ранпод"),
    "DigitalOcean": ("digitalocean",),
    "AWS": ("aws", "амазон aws"),
    "Google Cloud": ("gcp",),
    "netcup": ("netcup",),
    "Contabo": ("contabo",),
    "Selectel": ("selectel", "селектел"),
    "Timeweb": ("timeweb",),
    "ChatGPT": ("chatgpt", "чатгпт", "чат гпт"),
    "OpenAI": ("openai",),
    "Claude": ("claude", "клод"),
    "Anthropic": ("anthropic",),
    "Cursor": ("cursor", "курсор"),
    "Midjourney": ("midjourney", "миджорни"),
    "GitHub Copilot": ("copilot",),
    "JetBrains": ("jetbrains",),
    "GitHub": ("github", "гитхаб"),
    "OpenRouter": ("openrouter",),
    "Gemini": ("gemini",),
    "Perplexity": ("perplexity",),
    "Highlands Coffee": ("highlands", "хайлендс"),
    "Starbucks": ("starbucks", "старбакс"),
    "Phuc Long": ("phuc long",),
    "Katinat": ("katinat", "катинат"),
    "Cong Caphe": ("cong", "конг"),
    "Grab": ("grab", "граб", "грэб", "грабе"),
    "GrabFood": ("grabfood", "грабфуд"),
    "ShopeeFood": ("shopeefood",),
    "Xanh SM": ("xanh sm", "xanhsm"),
    "Gojek": ("gojek", "гоуджек"),
    "Uber": ("uber", "убер"),
    "Яндекс Такси": ("яндекс такси",),
    "KFC": ("kfc", "кфс"),
    "McDonald's": ("макдак",),
    "WinMart": ("winmart", "винмарт"),
    "Lotte Mart": ("lotte", "лотте"),
    "Circle K": ("circle k", "circlek"),
    "FamilyMart": ("family mart", "familymart"),
    "7-Eleven": ("7-eleven",),
    "Ministop": ("ministop",),
    "Emart": ("emart",),
    "AEON": ("aeon", "аеон"),
    "Airbnb": ("airbnb",),
    "Booking.com": ("booking", "букинг"),
    "Viettel": ("viettel", "вьеттел"),
    "MobiFone": ("mobifone",),
    "VinaPhone": ("vinaphone",),
    "Netflix": ("netflix", "нетфликс"),
    "Spotify": ("spotify", "спотифай"),
    "YouTube Premium": ("youtube premium", "ютуб премиум"),
    "Apple Music": ("apple music",),
    "iCloud": ("icloud", "айклауд"),
    "Telegram Premium": ("telegram premium", "тг премиум"),
    "Яндекс Плюс": ("яндекс плюс",),
    "Shopee": ("shopee", "шопи"),
    "Lazada": ("lazada", "лазада"),
    "Ozon": ("ozon", "озон"),
    "Wildberries": ("wildberries", "вб"),
    "AliExpress": ("aliexpress", "алиэкспресс"),
    "Steam": ("steam", "стим"),
}
BRAND_OF: dict[str, str] = {kw: canon for canon, kws in BRANDS.items() for kw in kws}

# ---------------------------------------------------------------- markers
# Exact word forms are checked first, then prefixes (order: income, refund, exchange, transfer).
INCOME_WORDS = {
    "получил", "получила", "получили", "получено", "получаю", "пришло", "пришли", "пришел",
    "пришла", "поступило", "поступили", "поступление", "зачислили", "зачисление", "зачислено",
    "заработал", "заработала", "заработали", "заработок", "кешбэк", "кешбек", "кэшбэк",
    "кэшбек", "cashback", "received", "earned", "got", "income", "доход", "подарили",
    "скинули", "прислали", "выплатили", "выплата", "закинули", "перечислили", "оплатили мне",
    "+",
}
INCOME_PREFIXES = ("зачисл", "поступ", "пришл", "кешб", "кэшб", "заработ")
REFUND_WORDS = {"вернули", "вернул", "вернула", "возврат", "возвратили", "refund", "refunded",
                "returned", "рефанд"}
REFUND_PREFIXES = ("возврат", "вернули")
EXCHANGE_WORDS = {"обмен", "swap", "swapped", "exchanged", "exchange", "конвертация"}
EXCHANGE_PREFIXES = ("поменял", "обменял", "сконверт", "конвертнул", "конвертировал",
                     "свапнул", "разменял", "обменя")
TRANSFER_WORDS = {"перевод", "transfer", "transferred", "sent", "moved", "send"}
TRANSFER_PREFIXES = ("перевел", "перекинул", "закинул", "скинул", "отправил", "вывел",
                     "пополнил", "переслал", "задонатил", "отослал", "перевёл", "кинул")
WITHDRAW_WORDS = {"снял", "сняла", "снятие", "обналичил", "обналичила", "обнал", "withdrew",
                  "withdraw", "withdrawal", "банкомат", "банкомате", "atm"}
BUY_SELL_WORDS = {"купил": "buy", "купила": "buy", "bought": "buy", "продал": "sell",
                  "продала": "sell", "продали": "sell", "sold": "sell"}
EXPENSE_WORDS = {
    "потратил", "потратила", "потратили", "потрачено", "трата", "траты", "расход", "заплатил",
    "заплатила", "заплатили", "оплатил", "оплатила", "оплатили", "оплата", "оплачено",
    "купил", "купила", "купили", "взял", "взяла", "взяли", "отдал", "отдала",
    "отдали", "ушло", "ушли", "минус", "spent", "paid", "bought", "pay", "buy", "spend",
    "запиши", "записать", "добавь", "внеси", "запомни", "закинь", "заплатить", "купить",
    "списали", "списалось", "отложил",
}
FILLERS = {
    "я", "мы", "мне", "меня", "мой", "моя", "мои", "на", "за", "в", "во", "с", "со", "из", "от",
    "по", "для", "и", "а", "у", "к", "до", "через", "это", "этот", "эту", "тоже", "еще", "ещё",
    "там", "тут", "вот", "уже", "просто", "ну", "типа", "где-то", "около", "примерно", "всего",
    "было", "был", "была", "были", "the", "for", "on", "at", "in", "from", "to", "of", "my",
    "i", "a", "an", "and", "with", "via", "by", "сегодня", "сумма", "сумму", "руб", "раз",
    "штука", "сам", "себе", "свой", "свою", "своей", "своего", "наших", "наш", "наши",
    "как", "так", "плюс", "потом", "затем", "ок", "окей", "ага", "спасибо", "пожалуйста",
    "плиз", "pls", "please", "thanks", "итого", "всё", "все",
}
BALANCE_WORDS = {"остаток", "осталось", "остаток:", "баланс", "лежит", "лежат", "есть",
                 "сейчас", "теперь", "итого", "всего", "на счету", "имеется", "balance", "left",
                 "сверка", "реально", "фактически", "по факту"}
NEGATION_WORDS = {"не", "нет", "ни", "never", "not"}
FUTURE_WORDS = {"буду", "будет", "планирую", "собираюсь", "надо", "нужно", "хочу", "должен",
                "должна", "заплачу", "куплю", "потрачу", "will", "plan"}
DEBT_WORDS = {"долг", "долга", "должен", "должна", "занял", "заняла", "одолжил", "одолжила",
              "взаймы", "в долг", "дал в долг", "debt", "loan", "кредит", "должок", "должка",
              "должку", "должком"}

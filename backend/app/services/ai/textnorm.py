"""Text normalisation, similarity and synonym helpers for reference resolution.

Pure functions (no DB). Used by the validator to resolve account/category/merchant references
written by the user or the LLM and to de-duplicate categories the model wants to create.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from difflib import SequenceMatcher

_PUNCT_RE = re.compile(r"[^\w\s/+&-]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
# Common Russian inflection endings, longest first (crude stemming is enough for dedup).
_RU_ENDINGS = (
    "иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "иях", "ах", "ях", "ов", "ев",
    "ей", "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ые", "ие", "ам", "ям", "ом", "ем", "ую", "юю",
    "ы", "и", "а", "я", "е", "у", "ю", "о", "ь",
)
_PREPOSITIONS = {"с", "со", "на", "в", "во", "из", "от", "за", "через", "by", "from", "to", "on"}


def norm(text: str | None) -> str:
    """Lowercase, ё→е, strip emoji/punctuation (keeps / + & -), collapse spaces."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    t = "".join(ch for ch in t if unicodedata.category(ch)[0] != "S" or ch in "/+&$")
    t = _PUNCT_RE.sub(" ", t)
    return _SPACE_RE.sub(" ", t).strip()


def strip_preposition(text: str) -> str:
    """«с кошелька» → «кошелька», «на биржу» → «биржу»."""
    parts = text.split(" ", 1)
    if len(parts) == 2 and parts[0] in _PREPOSITIONS:
        return parts[1]
    return text


def stem(word: str) -> str:
    """Very crude Russian/English stem: drop one known ending if the rest stays ≥ 3 chars."""
    w = word
    if re.fullmatch(r"[a-z0-9]+", w):
        for suf in ("ies", "es", "s"):
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                return w[: -len(suf)]
        return w
    for suf in _RU_ENDINGS:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def stem_phrase(text: str) -> str:
    return " ".join(stem(w) for w in norm(text).split())


def trigrams(text: str) -> set[str]:
    """pg_trgm-like trigram set of a normalised string (words padded with spaces)."""
    out: set[str] = set()
    for w in norm(text).split():
        p = f"  {w} "
        out.update(p[i : i + 3] for i in range(len(p) - 2))
    return out


def similarity(a: str, b: str) -> float:
    """Max of trigram Jaccard (pg_trgm semantics) and difflib ratio on stems; 0..1."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    sa, sb = stem_phrase(na), stem_phrase(nb)
    if sa == sb and len(sa) >= 3:
        return 0.95
    ta, tb = trigrams(na), trigrams(nb)
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    ratio = SequenceMatcher(None, sa, sb).ratio()
    return max(jac, ratio if min(len(sa), len(sb)) >= 4 else 0.0)


def best_match(
    ref: str, candidates: Iterable[tuple[object, Iterable[str]]]
) -> tuple[object, float]:
    """Best (item, score) of ``ref`` against each item's names/aliases."""
    best: tuple[object, float] = (None, 0.0)
    for item, names in candidates:
        for n in names:
            s = similarity(ref, n)
            if s > best[1]:
                best = (item, s)
    return best


# Synonyms → canonical default category name (ARCH §3.3). Keys are stems.
CATEGORY_SYNONYMS: dict[str, tuple[str, str]] = {}


def _syn(category: str, kind: str, *words: str) -> None:
    for w in words:
        CATEGORY_SYNONYMS[stem_phrase(w)] = (category, kind)


_syn("Еда", "expense", "еда", "ресторан", "рестораны", "кафе", "обед", "ужин", "завтрак",
     "доставка еды", "фастфуд", "пицца", "суши", "food", "restaurant", "lunch", "dinner",
     "питание", "столовая", "бургер")
_syn("Продукты", "expense", "продукты", "супермаркет", "groceries", "grocery", "магазин продуктов",
     "овощи", "фрукты")
_syn("Кофе", "expense", "кофе", "кофейня", "coffee", "латте", "капучино", "highlands", "starbucks")
_syn("Такси/транспорт", "expense", "такси", "транспорт", "taxi", "grab", "грэб", "грab",
     "бензин", "метро", "автобус", "байк", "аренда байка", "парковка", "транспорт и такси",
     "поездки", "uber", "убер", "be")
_syn("Жильё", "expense", "жилье", "аренда", "квартира", "rent", "коммуналка", "коммунальные",
     "электричество", "вода", "квартплата")
_syn("Связь", "expense", "связь", "интернет", "мобильная связь", "симка", "телефон", "mobile")
_syn("Подписки", "expense", "подписка", "подписки", "subscription", "netflix", "spotify",
     "youtube premium", "apple music", "icloud")
_syn("Здоровье", "expense", "здоровье", "аптека", "лекарства", "врач", "стоматолог", "медицина",
     "больница", "анализы", "health", "pharmacy")
_syn("Покупки", "expense", "покупки", "шопинг", "одежда", "обувь", "техника", "электроника",
     "shopping", "clothes")
_syn("Развлечения", "expense", "развлечения", "кино", "бар", "клуб", "игры", "концерт",
     "entertainment", "отдых")
_syn("Подарки", "expense", "подарок", "подарки", "gift", "gifts")
_syn("Переводы людям", "expense", "переводы людям", "перевод", "переводы", "перевод другу")
_syn("Прочее", "expense", "прочее", "другое", "разное", "misc", "other")
_syn("Серверы/инфра", "expense", "серверы", "сервер", "хостинг", "vps", "инфра",
     "инфраструктура", "домен", "домены", "облако", "cloud", "gpu", "vast", "hetzner")
_syn("Софт и AI", "expense", "софт", "ai", "нейросети", "нейросеть", "api", "chatgpt", "claude",
     "openai", "cursor", "software", "программы", "лицензии", "сервисы ai")
_syn("Реклама", "expense", "реклама", "ads", "продвижение", "таргет", "маркетинг")
_syn("Подрядчики", "expense", "подрядчики", "подрядчик", "фриланс", "фрилансер", "дизайнер")
_syn("Комиссии/обмен", "expense", "комиссия", "комиссии", "fee", "fees", "обмен", "комиссия обмена")
_syn("Закупка товара", "expense", "закупка", "закупка товара", "для магазина", "на продажу",
     "товар", "перепродажа")
_syn("Клиенты", "income", "клиенты", "клиент", "client", "заказчик", "заказ")
_syn("Продажи", "income", "продажи", "продажа", "sales", "магазин")
_syn("Зарплата", "income", "зарплата", "зп", "salary", "оклад", "аванс")
_syn("Прочее", "income", "прочее", "другое", "разное")


def synonym_category(name: str, kind: str) -> str | None:
    """Canonical default category for a word/phrase, e.g. «Рестораны» → «Еда»."""
    key = stem_phrase(name)
    hit = CATEGORY_SYNONYMS.get(key)
    if hit and hit[1] == kind:
        return hit[0]
    for w in key.split():
        hit = CATEGORY_SYNONYMS.get(w)
        if hit and hit[1] == kind:
            return hit[0]
    return None


_BAD_CATEGORY_RE = re.compile(
    r"(https?://|www\.|@|[<>{}\[\];`$\\]|\b(drop|select|insert|delete|update|truncate|exec)\b)",
    re.I,
)


def clean_category_name(name: str) -> str | None:
    """Tidy a model-proposed category name; None if it does not look like a category."""
    n = _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", name)).strip(" .,:;!?\"'«»🆕")
    if not (2 <= len(n) <= 40) or _BAD_CATEGORY_RE.search(n):
        return None
    if not re.search(r"[A-Za-zА-Яа-яЁё]", n):
        return None
    if len(n.split()) > 4:
        return None
    return n[0].upper() + n[1:]

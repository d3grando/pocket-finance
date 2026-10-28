"""Debt vocabulary and pure helpers for the debt parser (docs/DEBTS.md §5).

Verb classes, service words, the «кто мне должен?» question detector and matching of a written
person form against ``ParseContext.debts`` (names + aliases + open currencies, never amounts).
"""

from __future__ import annotations

import re

from app.core.person_names import clean, name_key, nominative_guess, same_person, words
from app.schemas.intents import CtxDebt, ParseContext

# ---------------------------------------------------------------- verb classes
VERBS: dict[str, str] = {}


def _add(cls: str, *forms: str) -> None:
    for f in forms:
        VERBS[f] = cls


_add("give", "дал", "дала", "давал", "давала", "gave")
_add("lend", "одолжил", "одолжила", "lent", "lend", "loaned")
_add("borrow", "занял", "заняла", "borrowed", "borrow")
_add("take", "взял", "взяла", "took")
_add("return", "вернул", "вернула", "возвратил", "возвратила", "returned")
_add("return_pl", "вернули", "возвратили", "отдали")
_add("payoff", "отдал", "отдала", "заплатил", "заплатила")
_add("payoff_strong", "погасил", "погасила", "выплатил", "выплатила", "закрыл", "закрыла",
     "repaid")
_add("settle", "рассчитался", "рассчиталась", "расплатился", "расплатилась")
_add("send", "перевел", "перевела", "скинул", "скинула", "кинул", "кинула", "закинул",
     "закинула", "отправил", "отправила", "прислал", "прислала", "sent")
_add("forgive", "простил", "простила", "простить", "прощаю", "прости", "forgave", "forgive")
_add("writeoff", "списать", "спиши", "списал", "списала", "списываю", "спишу", "забудь",
     "забыть", "забей")
_add("paid", "paid")

#: «Коля не вернёт» / «не отдаст» → forgive (the rest of the debt becomes an expense)
NOT_RETURN = {"вернет", "вернут", "отдаст", "отдадут", "вернешь", "отдашь"}
#: debt-ish words we cannot interpret alone → the whole message goes to the LLM
HINT_WORDS = {"вернет", "вернут", "отдаст", "отдадут", "верну", "отдам", "должен", "должна",
              "должны", "должник", "должница", "owe", "owes", "кредит", "кредита", "займ",
              "заем", "займы", "займа", "одолжи", "займи", "одолжить", "занять"}
DEBT_WORDS = {"долг", "долга", "долгу", "долги", "долгов", "долгом", "долге", "взаймы",
              "должок", "должка", "должку", "должком", "debt", "loan"}
ALL_WORDS = {"весь", "все", "целиком", "полностью", "остаток", "full", "entire", "whole"}
ME = {"мне", "me"}
ME_OBJ = {"меня", "мной", "мною"}
I_WORDS = {"я", "i", "мы", "we"}
MY = {"мой", "мою", "моего", "моем", "my", "свой", "свою", "своего"}
PREPS = {"у": "gen", "от": "gen", "с": "ins", "со": "ins", "перед": "ins", "from": "nom",
         "to": "nom", "with": "nom"}
PURPOSE_PREPS = {"за", "на", "для", "for", "под"}
NEUTRAL = {"в", "во", "уже", "наконец", "наконец-то", "сегодня", "обратно", "назад", "back",
           "часть", "частично", "половину", "еще", "тоже", "он", "она", "они", "ты", "вы",
           "его", "ее", "ему", "ей", "им", "их", "то", "это", "просто", "вот", "ну", "а", "и",
           "сумму", "сумма", "деньги", "денег", "бабки", "бабло", "the", "a", "an", "of", "and",
           "целый", "типа", "там", "тут", "всего", "итого", "кстати", "сам", "сама", "his",
           "her", "their", "money", "cash"}
#: subjects that are not persons («Магазин вернул 200к» is a refund, not a debt)
NOT_PERSONS = {"магазин", "продавец", "банк", "сервис", "поддержка", "курьер", "компания",
               "фирма", "клиент", "заказчик", "работодатель", "арендодатель", "хозяин",
               "хозяйка", "отель", "биржа", "платформа", "маркетплейс", "налоговая",
               "государство", "страховая", "кэшбэк", "кешбэк", "кешбек", "кто-то", "кто",
               "никто", "все", "друг", "другу", "друга", "подруга", "подруге", "подруги",
               "знакомый", "знакомому", "знакомого", "коллега", "коллеге", "коллеги",
               "сосед", "соседу", "соседа", "человек", "человеку", "чувак", "чуваку",
               "парень", "парню", "парня", "девушка", "девушке", "shop", "store", "bank",
               "someone", "friend", "другом", "подругу", "подругой", "знакомая", "знакомой",
               "знакомым", "знакомую", "коллегу", "коллегой", "соседом", "соседка", "соседке",
               "соседки", "соседку", "соседкой", "чувака", "чуваком", "девушку", "девушкой",
               "colleague", "neighbor", "neighbour"}
#: name keys of NOT_PERSONS («другу», «у друга», «другом» → «друг») — common nouns, never a person
NOT_PERSON_KEYS = {name_key(w) for w in NOT_PERSONS}

TRIGGER = re.compile(
    r"долг|взаймы|займ|заем|одолж|занял|вернул|верн[её]т|возвратил|отдал|отдаст|погасил|"
    r"выплатил|закрыл|рассчитал|расплатил|прост(?:ил|ить|и)|прощаю|списа|спиши|спишу|забудь|"
    r"забей|должн|должен|должо?к|(?:перев[её]л|скинул|кинул|отправил|прислал)\w*\s+мне|"
    r"\blent\b|\blend|borrow|\bowe|repaid|paid|returned|forg[ai]ve|\bdebt|\bloan|заплатил"
)

# ---------------------------------------------------------------- questions
_Q_WORDS = {
    "кто", "кому", "сколько", "мне", "я", "ты", "мой", "моя", "мои", "моих", "должен", "должна",
    "должны", "должно", "долг", "долги", "долгов", "долга", "покажи", "показать", "список",
    "все", "у", "перед", "какой", "какие", "каков", "ли", "еще", "денег", "а", "ну", "сейчас",
    "теперь", "итого", "всего", "who", "whom", "owes", "owe", "do", "does", "i", "me", "how",
    "much", "my", "debts", "debt", "show", "list", "what", "are", "is", "by", "открой",
    "открыть", "долгам", "долгах", "по", "меня",
}
_Q_FIRST = {"кто", "кому", "сколько", "какие", "какой", "покажи", "показать", "мои", "все",
            "список", "долги", "who", "how", "my", "show", "list", "debts", "открой", "открыть"}
_DEBT_Q = {"должен", "должна", "должны", "долги", "долгов", "долга", "долг", "долгам",
           "долгах", "debts", "owe", "owes", "debt"}
_Q_RE = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)?", re.U)


def query_parts(norm: str, orig: str) -> tuple[str | None, str | None] | None:
    """«кто мне должен?», «кому я должен», «мои долги», «/debts», «сколько должен Коля?» →
    (person as written or None, direction or None). None when it is not a debts question."""
    words = _Q_RE.findall(norm)
    if not words or not (set(words) & _DEBT_Q) or any(w in VERBS for w in words):
        return None
    question = "?" in norm or words[0] in _Q_FIRST or norm.strip().startswith("/debts")
    if not question:
        return None
    rest: list[tuple[int, str, str | None]] = []  # (word index, word, preposition before it)
    prev = None
    for n, w in enumerate(words):
        if w in _Q_WORDS:
            prev = w if w in ("у", "перед") else None
            continue
        rest.append((n, w, prev))
        prev = None
    if len(rest) > 2 or (rest and rest[-1][0] - rest[0][0] != len(rest) - 1):
        return None
    if any(w in PURPOSE_PREPS or w in NEUTRAL or w.endswith(("ть", "ться")) for _, w, _ in rest):
        return None  # «сколько я должен заплатить за аренду» is not about a person
    ws = set(words)
    direction = None
    if "кому" in ws or ("i" in ws and "owe" in ws) or (
        ws & {"я"} and ws & {"должен", "должна", "должны"}
    ):
        direction = "i_owe"
    elif ws & {"кто", "мне", "owes", "who"} or (rest and ws & {"должен", "должна"}):
        direction = "owed_to_me"
    if not rest:
        return None, direction
    form = _orig_words(orig, [w for _, w, _ in rest])
    prep = rest[0][2]
    return ((prep + " " + form) if prep else form), direction


def _orig_words(orig: str, low_words: list[str]) -> str:
    """Original-case spelling of the given lowercase words (in order)."""
    found = _Q_RE.findall(orig)
    out: list[str] = []
    for w in low_words:
        hit = next((f for f in found if clean(f) == clean(w)), w)
        out.append(hit)
    return " ".join(out)


# ---------------------------------------------------------------- persons
def match_person(ctx: ParseContext, form: str, handle: str | None = None
                 ) -> CtxDebt | list[CtxDebt] | None:
    """Known person for a written form: handle → exact alias/name → same name key.
    Several hits → the list (the validator asks «Какой?»); none → None."""
    debts = ctx.debts
    if not debts:
        return None
    if not handle and is_generic(form):
        return None  # «другу» is not «Другу» from an earlier mistake: the LLM asks «Какому?»
    if handle:
        h = handle.lower().lstrip("@")
        hits = [d for d in debts if (d.handle or "").lower().lstrip("@") == h]
        if hits:
            return hits[0] if len(hits) == 1 else hits
    low = clean(form)
    if not low:
        return None
    exact = [d for d in debts if clean(d.name) == low or low in {clean(a) for a in d.aliases}]
    if len(exact) == 1:
        return exact[0]
    key = name_key(form)
    if not key:
        return None
    hits = [d for d in debts if same_person(d.name_key or name_key(d.name), key)]
    if len(hits) == 1:
        return hits[0]
    return hits or None


def has_open(debt: CtxDebt | list[CtxDebt] | None, currency: str | None,
             direction: str) -> bool:
    """The context shows a non-zero balance of that sign in that currency (no amounts)."""
    if not isinstance(debt, CtxDebt) or not currency:
        return False
    return any(o.currency.upper() == currency.upper() and o.direction == direction
               for o in debt.open)


def open_currencies(debt: CtxDebt | list[CtxDebt] | None,
                    direction: str | None = None) -> list[str]:
    if not isinstance(debt, CtxDebt):
        return []
    return [o.currency.upper() for o in debt.open if direction is None or o.direction == direction]


def case_by_ending(word: str) -> str:
    """Grammatical case of a name written after «долг» («долг Коли» / «долг Пете»)."""
    w = clean(word).split(" ")[-1] if word else ""
    if not w or not re.search(r"[а-я]", w):
        return "nom"
    last, prev = w[-1], (w[-2] if len(w) > 1 else "")
    if last in "еую":
        return "dat"
    if last in "иы":
        return "gen"
    if last == "а":
        return "nom" if prev in "жшчщ" else "gen"
    if last == "я":
        return "gen" if prev == "е" else "nom"
    return "nom"


def guess_name(form: str, case: str) -> tuple[str, bool]:
    """Display name for a NEW person (nominative best effort); Latin words stay as written.
    «ё» of the written form is kept («Серёже» → «Серёжа», «Лёша» → «Лёша»)."""
    name, certain = nominative_guess(form, case)  # type: ignore[arg-type]
    return keep_yo(form, name), certain


_YO_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)?", re.U)


def keep_yo(written: str, name: str) -> str:
    """Put back the «ё» that matching keys drop: a name built from «Алёше» / «Серёже» is
    «Алёша» / «Серёжа», not «Алеша». Word by word, only where the letters otherwise agree."""
    if "ё" not in written.lower() or not name:
        return name
    src = [w for w in _YO_WORD.findall(written)
           if w.lower() not in ("у", "к", "от", "для", "с", "со", "ко", "перед")]
    out = name.split(" ")
    if len(src) != len(out):
        return name
    fixed = []
    for w, n in zip(src, out, strict=True):
        chars = list(n)
        for i, ch in enumerate(w[: len(chars)]):
            if ch in "ёЁ" and chars[i].lower() == "е":
                chars[i] = "Ё" if chars[i].isupper() else "ё"
        fixed.append("".join(chars))
    return " ".join(fixed)


def is_generic(form: str) -> bool:
    """«другу», «у соседа», «коллеге» — a common noun, not a person's name (which friend?)."""
    ws = [w for w in words(form) if w not in PREPS]
    return bool(ws) and all(name_key(w) in NOT_PERSON_KEYS for w in ws)


def is_name_like(raw: str) -> bool:
    """A proper name written with a capital letter («Коля», «Dan»), not a known non-person."""
    w = raw.strip()
    return bool(w) and w[0].isupper() and clean(w) not in NOT_PERSONS


__all__ = [
    "ALL_WORDS", "DEBT_WORDS", "HINT_WORDS", "I_WORDS", "ME", "ME_OBJ", "MY", "NEUTRAL",
    "NOT_PERSONS", "NOT_PERSON_KEYS", "NOT_RETURN", "PREPS", "PURPOSE_PREPS", "TRIGGER", "VERBS",
    "case_by_ending", "guess_name", "has_open", "is_generic", "is_name_like", "keep_yo",
    "match_person", "open_currencies", "query_parts",
]

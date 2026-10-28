"""Person names for debts (docs/DEBTS.md §5.3): case/declension-insensitive matching and a
best-effort nominative guess. Pure functions, no DB — shared by the parser, the AI validator and
the debts service (one implementation, so «Коле», «у Коли», «Коля вернул» are one person).

Russian names change endings by case: Коля / Коле / Коли / Колю / Колей. ``name_key`` strips one
case ending per word; ``same_person`` compares keys allowing a one-letter tail difference
(«андр» ~ «андре» for Андрей / Андрея). Latin words are only lowercased.
"""

from __future__ import annotations

import re
from typing import Literal

Case = Literal["nom", "gen", "dat", "acc", "ins"]

_VOWEL_END = "аяоеиыуюйь"
_TWO_END = ("ом", "ем", "ой", "ей", "ою", "ею", "ью")
_HUSH = "гкхжшчщ"  # after these «и» is written instead of «ы»
_PREP = {"у", "к", "от", "для", "с", "со", "ко", "to", "from"}
_WORD_RE = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)?", re.U)
_CYR_RE = re.compile(r"[а-яё]", re.I)


def clean(s: str) -> str:
    """Lowercase, ё→е, no «@», collapse spaces."""
    return " ".join((s or "").replace("ё", "е").replace("Ё", "Е").lower().lstrip("@").split())


def _stem(word: str) -> str:
    if not _CYR_RE.search(word):
        return word
    for end in _TWO_END:
        if word.endswith(end) and len(word) - len(end) >= 3:
            return word[: -len(end)]
    if word[-1] in _VOWEL_END and len(word) - 1 >= 3:
        return word[:-1]
    return word


def words(s: str) -> list[str]:
    """Name words without leading prepositions («у Пети» → ["пети"])."""
    ws = _WORD_RE.findall(clean(s))
    while ws and ws[0] in _PREP:
        ws = ws[1:]
    return ws


def name_key(s: str) -> str:
    """Match key: one stem per word, space-joined. «Коля» / «Коле» / «у Коли» → «кол»;
    «Иван Петров» / «Ивану Петрову» → «иван петров»; «@vasya» → «vasya»."""
    return " ".join(_stem(w) for w in words(s))[:64]


def _close(a: str, b: str) -> bool:
    if a == b:
        return True
    if (  # short names keep their ending: «оля» ~ «оле» ~ «олю»
        len(a) == len(b) >= 3 and a[:-1] == b[:-1] and a[-1] in _VOWEL_END and b[-1] in _VOWEL_END
    ):
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 3 and long_.startswith(short) and len(long_) - len(short) <= 1


def same_person(key_a: str, key_b: str) -> bool:
    """Keys of the same person: same number of words, each equal or differing by one tail
    letter (stem ≥ 3). «кол» ~ «кол», «андр» ~ «андре», «ян» !~ «яна» (too short → exact only)."""
    wa, wb = key_a.split(), key_b.split()
    if not wa or len(wa) != len(wb):
        return False
    return all(_close(x, y) for x, y in zip(wa, wb, strict=True))


def capitalize(s: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in s.split())


def _nom_word(w: str, case: Case) -> tuple[str, bool]:
    if not _CYR_RE.search(w) or case == "nom":
        return w, True
    last, prev = w[-1], (w[-2] if len(w) > 1 else "")
    if case == "gen":
        if last == "и":
            return w[:-1] + ("а" if prev in _HUSH else "я"), True  # Пети→Петя, Маши→Маша
        if last == "ы":
            return w[:-1] + "а", True  # Лены→Лена
        if last == "а":
            return w[:-1], True  # Ивана→Иван
        if last == "я":
            return w[:-1] + ("й" if prev in "аеиоуыэюя" else "ь"), True  # Андрея, Игоря
    if case == "dat":
        if last == "у":
            return w[:-1], True  # Ивану→Иван
        if last == "ю":
            return w[:-1] + ("й" if prev in "аеиоуыэюя" else "ь"), True  # Андрею, Игорю
        if last == "е" and prev in "жшчщ":
            return w[:-1] + "а", True  # Маше→Маша, Саше→Саша
        if last == "е":
            return w[:-1] + "я", False  # Коле→Коля (but Лене→Лена!) — not certain
    if case == "acc":
        if last == "у":
            return w[:-1] + "а", True  # Машу→Маша
        if last == "ю":
            return w[:-1] + "я", True  # Колю→Коля
    if case == "ins":
        for end, rep in (("ом", ""), ("ем", "й"), ("ой", "а"), ("ей", "я")):
            if w.endswith(end) and len(w) > len(end) + 1:
                return w[: -len(end)] + rep, end != "ей"
    return w, False


def nominative_guess(form: str, case: Case = "nom") -> tuple[str, bool]:
    """Best-effort display name from a written form and the grammatical case implied by the
    construction («дал в долг Коле» → dat, «занял у Пети» → gen, «Коля вернул» → nom).
    Returns (Capitalized name, certain). ``certain=False`` (e.g. dative «-е»: Коле→Коля vs
    Лене→Лена) — the parser asks the LLM for ``person_name`` when it is available; the name
    stays editable (PATCH /debts/{id}). Matching never depends on it (``name_key``)."""
    ws = words(form)
    if not ws:
        return "", False
    out, certain = [], True
    for w in ws:
        n, c = _nom_word(w, case)
        out.append(n)
        certain = certain and c
    return capitalize(" ".join(out))[:56], certain


__all__ = ["Case", "capitalize", "clean", "name_key", "nominative_guess", "same_person", "words"]

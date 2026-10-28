"""Text normalisation, tokenisation and a tiny Russian stemmer (pure, no I/O).

``normalize`` maps every character 1:1 (lowercase, ё→е, unified spaces/dashes), so spans found
in the normalised text are valid in the original text too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_COMMON = {
    " ": " ",
    " ": " ",
    " ": " ",
    " ": " ",
    "\t": " ",
    "\r": " ",
    "−": "-",
    "–": "-",
    "—": "-",
    "’": "'",
    "`": "'",
    "＄": "$",
    "₀": "0",
}
_ORIG_MAP = str.maketrans(_COMMON)
_NORM_MAP = str.maketrans({**_COMMON, "ё": "е", "Ё": "е"})


def clean_original(text: str) -> str:
    """Original text with unified spaces/dashes (same length as input)."""
    return text.translate(_ORIG_MAP)


def normalize(text: str) -> str:
    """Lowercase + ё→е + unified spaces/dashes. Same length as the input."""
    out = text.translate(_NORM_MAP)
    low = out.lower()
    if len(low) != len(out):  # rare unicode expansions (e.g. "İ")
        low = "".join((c.lower()[:1] or c) for c in out)
    return low


# Vietnamese-only letters (đ is excluded: it is also the ₫ sign written after numbers).
_VI_CHARS = set(
    "ăâêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ"
)


def has_vietnamese(norm: str) -> bool:
    if any(c in _VI_CHARS for c in norm):
        return True
    # «đ» inside a word (not a ₫ sign after a number / k / m) → Vietnamese text
    for m in re.finditer("đ", norm):
        i = m.start()
        prev = norm[i - 1] if i else " "
        nxt = norm[i + 1] if i + 1 < len(norm) else " "
        if nxt.isalpha():
            return True
        if prev.isalpha() and prev not in "kкmм":
            return True
    return False


_TOKEN_RE = re.compile(
    r"(?P<handle>@[a-z0-9_]{2,64})"
    r"|(?P<time>(?<![\d.,:])(?:[01]?\d|2[0-3]):[0-5]\d(?![\d:]))"
    r"|(?P<num>\d+(?:[.,/]\d+)*)"
    # «85 тыс. донгов»: an abbreviation keeps its dot (not a sentence end / segment split)
    r"|(?P<word>(?:тыс|млн|млрд|руб|долл)\.(?=\s|$)|[^\W\d_]+(?:[.\-'][^\W\d_]+)*)"
    r"|(?P<sym>[$€₽₫฿¥%+\-<>=*#№])"
    r"|(?P<punct>[,;!?\n:()\"«»]|\.)"
)


@dataclass(slots=True)
class Token:
    kind: str  # num | word | sym | handle | time | punct
    text: str  # normalised
    raw: str  # original slice
    start: int
    end: int
    stem: str = ""
    role: str = ""  # what consumed the token ("" = free)

    @property
    def free(self) -> bool:
        return not self.role


def tokenize(norm: str, orig: str) -> list[Token]:
    toks: list[Token] = []
    for m in _TOKEN_RE.finditer(norm):
        kind = m.lastgroup or "punct"
        s, e = m.span()
        t = Token(kind, m.group(), orig[s:e], s, e)
        if kind == "word":
            t.stem = stem(t.text)
        toks.append(t)
    return _merge_space_thousands(toks, norm)


def _merge_space_thousands(toks: list[Token], norm: str) -> list[Token]:
    """«450 000», «1 250 000» → one number token (groups of exactly 3 digits)."""
    out: list[Token] = []
    for t in toks:
        if (
            t.kind == "num"
            and len(t.text) == 3
            and t.text.isdigit()
            and out
            and out[-1].kind == "num"
            and out[-1].end + 1 == t.start
            and norm[out[-1].end] == " "
            and _space_group_ok(out[-1].text)
        ):
            prev = out[-1]
            out[-1] = Token("num", prev.text + " " + t.text, prev.raw + " " + t.raw,
                            prev.start, t.end)
            continue
        out.append(t)
    return out


def _space_group_ok(s: str) -> bool:
    parts = s.split(" ")
    if not (1 <= len(parts[0]) <= 3 and parts[0].isdigit() and parts[0][0] != "0"):
        return False
    return all(len(p) == 3 and p.isdigit() for p in parts[1:])


# ---------------------------------------------------------------- stemming

_ENDINGS = sorted(
    {
        "иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "ией",
        "ую", "юю", "ая", "яя", "ое", "ее", "ые", "ие", "ой", "ей", "ий", "ый", "ых", "их",
        "ым", "им", "ом", "ем", "ам", "ям", "ах", "ях", "ов", "ев", "ью", "ия", "ии", "ию",
        "а", "я", "у", "ю", "е", "о", "ы", "и", "ь", "й",
    },
    key=len,
    reverse=True,
)
_CYR = re.compile(r"[а-я]")


def stem(word: str) -> str:
    """Very small suffix stripper: one ending, stem ≥ 3 letters. Latin: plural «s»."""
    w = word.lower().replace("ё", "е")
    if len(w) < 4:
        return w
    if _CYR.search(w):
        for e in _ENDINGS:
            if w.endswith(e) and len(w) - len(e) >= 3:
                return w[: -len(e)]
        return w
    if len(w) >= 5 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def phrase_stems(phrase: str) -> tuple[str, ...]:
    """Stems of a dictionary phrase («для магазина» → ("для", "магазин"))."""
    norm = normalize(phrase)
    return tuple(stem(w) for w in re.findall(r"[^\W\d_]+(?:[.\-'][^\W\d_]+)*|\d+", norm))


def phrase_forms(phrase: str) -> tuple[str, ...]:
    """Normalised full word forms of a dictionary phrase."""
    norm = normalize(phrase)
    return tuple(re.findall(r"[^\W\d_]+(?:[.\-'][^\W\d_]+)*|\d+", norm))


def span_text(orig: str, toks: list[Token]) -> str:
    """Original text covering the given tokens (in order), single-spaced."""
    if not toks:
        return ""
    return " ".join(orig[t.start : t.end] for t in sorted(toks, key=lambda x: x.start))

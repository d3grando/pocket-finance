"""Resolver / alias scale: building the index is linear in the number of
aliases (it was O(n²): 8k aliases = 5 s, 100k hung the API for minutes), the LRU cache is safe
across worker threads, and PARSER_LEXICON_EXTRA adds built-in accounts from a JSON file."""

from __future__ import annotations

import json
import threading
import time

import pytest

from app.schemas.intents import CtxMerchant
from app.services.parser import lexicon as lx
from app.services.parser import parse, refs

from .ctxs import ctx


def _merchants(n: int, per: int, word: str = "магазин") -> list[CtxMerchant]:
    # every alias starts with the same word: the worst case for the per-key duplicate check
    return [
        CtxMerchant(id=1000 + m, name=f"Shop{m}",
                    aliases=[f"{word} {m} {a}" for a in range(per)])
        for m in range(n)
    ]


def test_50_merchants_x_50_aliases_parse_under_1s() -> None:
    c = ctx(merchants=_merchants(50, 50), my_handles=["scale-50x50"])
    t0 = time.perf_counter()
    res = parse("кофе 85к", c)  # cold: the resolver is built for this context
    assert time.perf_counter() - t0 < 1.0
    assert res.intents and not res.needs_llm


def test_index_build_is_linear_in_aliases() -> None:
    """20k aliases with one shared first word: the old list scan needed ~2·10⁸ comparisons."""
    c = ctx(merchants=_merchants(1, 20_000), my_handles=["scale-20k"])
    t0 = time.perf_counter()
    r = refs.get_resolver(c)
    assert time.perf_counter() - t0 < 3.0
    assert len(r.merch_index["магазин"]) == 20_000


def test_first_registration_still_wins() -> None:
    """The O(1) duplicate check keeps the old rule: same phrase + kind under one key → the
    first (higher-priority) registration stays, a different kind is a separate entry."""
    idx = refs._Index()
    a, b = refs.CatRef("Еда", "expense"), refs.CatRef("Кафе", "expense")
    inc = refs.CatRef("Продажи", "income")
    stems = ("кафе",)
    refs._add(idx, stems, a, "кафе", 3)
    refs._add(idx, stems, b, "кафе", 2)
    refs._add(idx, stems, inc, "кафе", 2)
    assert [(e[2].name, e[4]) for e in idx["кафе"]] == [("Еда", 3), ("Продажи", 2)]
    plain: dict = {}  # a plain dict (no ``seen``) behaves the same
    for ref, prio in ((a, 3), (b, 2), (inc, 2)):
        refs._add(plain, stems, ref, "кафе", prio)
    assert [(e[2].name, e[4]) for e in plain["кафе"]] == [("Еда", 3), ("Продажи", 2)]


def test_norm_aliases_is_linear_capped_and_dedups_after_the_cut() -> None:
    from app.services.ledger.common import MAX_ALIASES, norm_aliases

    t0 = time.perf_counter()
    out = norm_aliases([f"alias {i}" for i in range(50_000)], limit=50_000)
    assert time.perf_counter() - t0 < 1.0 and len(out) == 50_000
    long_a, long_b = "x" * 64 + "a", "x" * 64 + "b"  # equal after the 64-char cut
    assert norm_aliases([long_a, long_b, " Кафе ", "кафе"]) == ["x" * 64, "кафе"]
    assert len(norm_aliases([f"a{i}" for i in range(80)])) == MAX_ALIASES
    # a learned alias is dropped at the cap, the old ones stay
    full = [f"a{i}" for i in range(MAX_ALIASES)]
    assert norm_aliases(full, "new") == full


def test_resolver_cache_is_thread_safe() -> None:
    contexts = [ctx(my_handles=[f"thread-{i}"]) for i in range(40)]
    errors: list[BaseException] = []

    def work(k: int) -> None:
        try:
            for i in range(60):
                refs.get_resolver(contexts[(k * 7 + i) % len(contexts)])
        except BaseException as e:  # pragma: no cover - the failure being tested
            errors.append(e)

    threads = [threading.Thread(target=work, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(refs._CACHE) <= 16


# ------------------------------------------------------------------------------- lexicon extra


def test_lexicon_extra_load_and_merge(tmp_path) -> None:
    p = tmp_path / "extra.json"
    p.write_text(json.dumps({"accounts": [
        ["Wise", "USD", "bank", ["wise", "вайз"]],
        ["Наличные", "VND", "cash", ["налик"]],
    ]}), encoding="utf-8")
    extra = lx.load_lexicon_extra(str(p))
    assert extra[0] == ("Wise", "USD", "bank", ("wise", "вайз"))
    merged = lx.merge_accounts(lx.BUILTIN_ACCOUNTS, extra)
    assert merged[-1][0] == "Wise"
    cash = next(a for a in merged if a[0] == "Наличные")
    assert "налик" in cash[3] and "нал" in cash[3]
    assert len(merged) == len(lx.BUILTIN_ACCOUNTS) + 1


@pytest.mark.parametrize("content", [
    "not json",
    json.dumps({"accounts": "x"}),
    json.dumps({"accounts": [["Wise", "usd", "bank", []]]}),
    json.dumps({"accounts": [["Wise", None, "spaceship", []]]}),
    json.dumps({"accounts": [["Wise", None, "bank", ["x" * 65]]]}),
    json.dumps({"accounts": [["Wise", None, "bank"]]}),
])
def test_lexicon_extra_bad_file_is_ignored_with_a_warning(tmp_path, monkeypatch, caplog,
                                                          content) -> None:
    p = tmp_path / "extra.json"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        lx.load_lexicon_extra(str(p))
    before = list(lx.BUILTIN_ACCOUNTS)
    monkeypatch.setenv(lx.LEXICON_EXTRA_ENV, str(p))
    with caplog.at_level("WARNING", logger=lx.__name__):
        lx._apply_extra()
    assert lx.BUILTIN_ACCOUNTS == before
    assert any(lx.LEXICON_EXTRA_ENV in r.getMessage() for r in caplog.records)


def test_lexicon_extra_from_env(tmp_path, monkeypatch) -> None:
    p = tmp_path / "extra.json"
    p.write_text(json.dumps({"accounts": [["Wise", "USD", "bank", ["вайз"]]]}),
                 encoding="utf-8")
    monkeypatch.setattr(lx, "BUILTIN_ACCOUNTS", list(lx.BUILTIN_ACCOUNTS))
    monkeypatch.setenv(lx.LEXICON_EXTRA_ENV, str(p))
    lx._apply_extra()
    assert lx.BUILTIN_ACCOUNTS[-1] == ("Wise", "USD", "bank", ("вайз",))
    monkeypatch.setenv(lx.LEXICON_EXTRA_ENV, str(tmp_path / "missing.json"))
    lx._apply_extra()  # missing file: a warning, nothing else

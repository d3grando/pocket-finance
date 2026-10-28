"""Speed: < 2 ms per phrase on average (ARCH: parser < 5 ms, task: < 2 ms)."""

from __future__ import annotations

import time

from app.services.parser import parse

from . import cases_cmd, cases_ops
from .ctxs import CTX

TEXTS = [c[0] for c in cases_ops.ALL + cases_cmd.ALL if len(c[0]) < 200]


def test_average_under_2ms() -> None:
    for t in TEXTS:  # warm up (regex compile, resolver cache)
        parse(t, CTX)
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        for t in TEXTS:
            parse(t, CTX)
        best = min(best, (time.perf_counter() - t0) / len(TEXTS))
    assert best < 0.002, f"{best * 1000:.3f} ms per phrase"


def test_worst_phrase_under_5ms() -> None:
    for t in TEXTS:
        parse(t, CTX)
    worst = 0.0
    for t in TEXTS:
        t0 = time.perf_counter()
        for _ in range(5):
            parse(t, CTX)
        worst = max(worst, (time.perf_counter() - t0) / 5)
    assert worst < 0.005, f"worst {worst * 1000:.3f} ms"


def test_cold_context_build_is_cheap() -> None:
    """A changed dictionary (new alias) rebuilds the resolver: still fast."""
    from .ctxs import ctx

    c = ctx(my_handles=["perf-test-handle"])
    t0 = time.perf_counter()
    parse("кофе 85к", c)
    assert time.perf_counter() - t0 < 0.05

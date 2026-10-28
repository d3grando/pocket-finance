"""Table-driven tests of ``parser.parse`` (all spec examples included)."""

from __future__ import annotations

import pytest

from app.services.parser import parse

from . import cases_cmd, cases_more, cases_ops
from .ctxs import CTXS
from .helpers import check

CASES = cases_ops.ALL + cases_cmd.ALL + cases_more.MORE


def _id(case: tuple) -> str:
    text = case[0].replace("\n", "⏎")
    return (text[:40] + ("…" if len(text) > 40 else "")) + (f" [{case[2]}]" if len(case) > 2
                                                              else "")


@pytest.mark.parametrize("case", CASES, ids=[_id(c) for c in CASES])
def test_parse(case: tuple) -> None:
    text, spec = case[0], case[1]
    ctx = CTXS[case[2] if len(case) > 2 else "seed"]
    res = parse(text, ctx)
    errs = check(res, spec)
    assert not errs, f"{text!r}:\n  " + "\n  ".join(errs) + "\n  got: " + res.model_dump_json(
        exclude_defaults=True)


def test_case_count() -> None:
    assert len(CASES) >= 250

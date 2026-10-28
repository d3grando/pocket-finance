"""Bench harness: dataset validity, scoring, and a full runner pass against the fake server."""

from __future__ import annotations

import json

from app.schemas.intents import IntentList
from bench import run as bench_run
from bench.context import bench_context
from bench.score import canon, score_case, summarize


def _canon(text: str, *items: dict) -> list[dict]:
    ctx = bench_context()
    il = IntentList.model_validate({"intents": list(items)})
    return [canon(it, ctx, text) for it in il.intents]


def test_dataset_has_150_plus_phrases_and_is_self_consistent(capsys):
    cases = bench_run.load_dataset()
    assert len(cases) >= 150
    types = {e["type"] if isinstance(e["type"], str) else "any" for c in cases for e in c["expect"]}
    assert {"create_transaction", "transfer", "adjust_balance", "update_transaction",
            "delete_transaction", "undo", "search", "stats", "create_category", "answer",
            "clarify"} <= types
    assert bench_run.self_check(cases) == 0


def test_canon_uses_production_semantics():
    got = _canon("вчера мы сходили в ресторан, отдали 600к, потом ещё 150к на такси",
                 {"type": "create_transaction", "amount": "6000000", "amount_raw": "600к",
                  "category_ref": "ресторан", "occurred_on": "2026-10-26"},
                 {"type": "transfer", "amount": "3", "amount_raw": "3 тона", "currency": "TON",
                  "from_account_ref": "Кошелёк", "to_handle": "bob"})
    assert got[0] == {"type": "create_transaction", "kind": "expense", "amount": "600000",
                      "currency": "VND", "category": "Еда", "account": None,
                      "counterparty": None, "date": -1}
    # amount_raw absent from this text → rejected like in production
    assert "error" in got[1]
    [t] = _canon("перевёл 3 тона @bob с кошелька",
                 {"type": "transfer", "amount": "3", "amount_raw": "3 тона", "currency": "TON",
                  "from_account_ref": "Кошелёк", "to_handle": "bob"})
    # foreign @handle → expense with a recipient, «Переводы людям»
    assert t["type"] == "create_transaction" and t["counterparty"] == "@bob"
    assert t["category"] == "Переводы людям" and t["account"] == "Кошелёк"


def test_score_case_fields_and_any():
    exp = [{"type": "create_transaction", "amount": "85000", "category": {"$any": ["Кофе", "Еда"]},
            "date": 0}]
    ok = score_case(exp, [{"type": "create_transaction", "amount": "85000", "category": "Еда",
                           "date": 0}])
    assert ok["exact"]
    bad = score_case(exp, [{"type": "create_transaction", "amount": "85", "category": "Кофе",
                            "date": 0}])
    assert not bad["exact"] and bad["fields"] == {"0.type": True, "0.amount": False,
                                                  "0.category": True, "0.date": True}
    fail = score_case(exp, None)
    assert not fail["exact"] and not any(fail["fields"].values())
    s = summarize([{"status": "ok", "latency_ms": 100, "score": ok, "prompt_tokens": 10,
                    "completion_tokens": 5, "tags": ["simple"]},
                   {"status": "invalid", "latency_ms": 300, "score": fail, "prompt_tokens": 10,
                    "completion_tokens": 5, "tags": ["simple"]}])
    assert s["exact"] == 0.5 and s["valid_json"] == 0.5 and s["fields"]["amount"] == 0.5
    assert s["latency_ms"]["p50"] in (100, 300) and s["latency_ms"]["p95"] == 300


async def test_runner_end_to_end_on_fake_server(llm, monkeypatch, tmp_path):
    monkeypatch.setenv("AI_BASE_URL", llm.url)
    monkeypatch.setenv("AI_API_KEY", "k")
    llm.intents({"type": "create_transaction", "amount": "85000", "amount_raw": "85к",
                 "category_ref": "Кофе"})
    llm.reply("garbage")
    out = tmp_path / "summary.json"
    rc = await bench_run.amain(["--models", "fake-a", "--limit", "2", "--concurrency", "1",
                                "--no-repair", "--retries", "0", "--out", str(out),
                                "--results-dir", str(tmp_path)])
    assert rc == 0
    summary = json.loads(out.read_text())["summaries"][0]
    assert summary["n"] == 2 and summary["model"] == "fake-a"
    assert summary["status"] == {"ok": 1, "invalid": 1}
    assert summary["exact"] == 0.5
    assert len(llm.requests) == 2
    assert any(p.name.endswith("-fake-a.json") for p in tmp_path.iterdir())


def test_debt_phrases_in_dataset_and_canon():
    cases = [c for c in bench_run.load_dataset() if "debt" in c.get("tags", [])]
    assert len(cases) >= 20
    got = _canon("Коля вернул 200к", {"type": "debt", "op": "repay_in", "person": "Коля",
                                       "person_name": "Коля", "amount": "200000",
                                       "amount_raw": "200к", "fallback": "income"})
    assert got == [{"type": "debt", "op": "repay_in", "person": "Коля", "amount": "200000",
                    "currency": "VND", "account": None, "direction": None, "date": 0}]
    # п.5 in the bench context: Петя does not owe me → the ordinary income
    [inc] = _canon("Петя вернул 200к", {"type": "debt", "op": "repay_in", "person": "Петя",
                                        "amount": "200000", "amount_raw": "200к",
                                        "fallback": "income"})
    assert inc["type"] == "create_transaction" and inc["kind"] == "income"
    assert inc["category"] == "Прочее" and inc["counterparty"] == "петя"
    [bad] = _canon("дал в долг 500к", {"type": "debt", "op": "lend", "person": "Коле",
                                       "amount": "500000", "amount_raw": "500к"})
    assert "error" in bad  # an invented person is not recorded
    [q] = _canon("сколько я должен Пете?", {"type": "debts_query", "person": "Пете",
                                             "direction": "i_owe"})
    assert q == {"type": "debts_query", "person": "Петя", "direction": "i_owe"}

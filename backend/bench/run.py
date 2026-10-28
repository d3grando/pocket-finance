"""Model benchmark runner: field accuracy + latency p50/p95 over bench/dataset.jsonl.

Calls the REAL OpenAI-compatible API (AI_BASE_URL / AI_API_KEY) through the production
LLMClient (same prompt, schema, parsing, repair) — LLM only, no parser, so models are compared on
the hard path. It spends real tokens; at most 4 concurrent calls:

    cd backend
    uv run python -m bench.run --env ../.env --models your-model,another-model --concurrency 4

    # smoke (N phrases per model, no repair retries):
    uv run python -m bench.run --env ../.env --models your-model --limit 1 --no-repair --retries 0

    # offline check of the dataset/scorer (expected intents scored against themselves, no API):
    uv run python -m bench.run --self-check

Writes bench/results/<timestamp>-<model>.json (per-phrase details) and prints a Markdown table;
``--out`` sets the summary JSON path (all models).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.ai.client import LLMClient
from bench.context import bench_context
from bench.score import canon, score_case, summarize

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset.jsonl"
RESULTS = HERE / "results"


def load_dataset(path: Path = DATASET) -> list[dict[str, Any]]:
    cases = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        c = json.loads(line)
        if not c.get("text") or not isinstance(c.get("expect"), list) or not c["expect"]:
            raise ValueError(f"dataset line {i}: needs text + non-empty expect")
        cases.append(c)
    return cases


def load_env(path: str | None) -> None:
    """KEY=VALUE lines → os.environ (does not override already set variables)."""
    if not path:
        return
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def run_model(model: str, cases: list[dict[str, Any]], args: argparse.Namespace) -> dict:
    ctx = bench_context()
    client = LLMClient(
        base_url=os.environ.get("AI_BASE_URL"), api_key=os.environ.get("AI_API_KEY", ""),
        model=model, timeout=args.timeout, max_concurrency=args.concurrency,
        retries=args.retries, repair=not args.no_repair, enabled=True,
    )
    sem = asyncio.Semaphore(args.concurrency)
    rows: list[dict[str, Any]] = []

    async def one(case: dict[str, Any]) -> None:
        throttled = 0
        async with sem:
            while True:
                res = await client.extract_intents(case["text"], ctx)
                err = res.error or ""
                if (res.status == "unavailable" and ("HTTP 429" in err or "HTTP 5" in err)
                        and throttled < args.throttle_retries):
                    throttled += 1
                    wait = args.throttle_pause * throttled
                    print(f"  [{model}] throttled ({err[:40]}), pause {wait:g}s", flush=True)
                    await asyncio.sleep(wait)
                    continue
                break
        got = [canon(it, ctx, case["text"]) for it in res.intents.intents] if res.ok else None
        sc = score_case(case["expect"], got)
        attempts = (res.response_log or {}).get("attempts") or []
        rows.append({
            "id": case.get("id"), "text": case["text"], "tags": case.get("tags", []),
            "expect": case["expect"], "got": got, "status": res.status, "error": res.error,
            "latency_ms": res.latency_ms, "prompt_tokens": res.prompt_tokens,
            "completion_tokens": res.completion_tokens, "score": sc,
            "raw": (attempts[-1].get("content") if attempts else None),
            "modes": [a.get("mode") for a in attempts], "throttled": throttled,
        })
        mark = "ok " if sc["exact"] else ("ERR" if got is None else "-- ")
        print(f"  [{model}] {mark} {res.latency_ms or 0:>6} ms  {case['text'][:70]}", flush=True)

    t0 = time.monotonic()
    await asyncio.gather(*(one(c) for c in cases))
    await client.aclose()
    rows.sort(key=lambda r: (r["id"] is None, r["id"]))
    summary = summarize(rows)
    summary["model"] = model
    summary["wall_s"] = round(time.monotonic() - t0, 1)
    return {"summary": summary, "rows": rows}


def markdown(summaries: list[dict[str, Any]]) -> str:
    keys = ["type", "kind", "amount", "currency", "category", "account", "date", "target",
            "metric", "period"]
    head = ["model", "exact", "valid JSON", "p50 ms", "p95 ms"] + keys
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for s in summaries:
        f = s["fields"]
        row = [s["model"], f"{s['exact']:.1%}" if s["exact"] is not None else "—",
               f"{s['valid_json']:.1%}" if s["valid_json"] is not None else "—",
               str(s["latency_ms"]["p50"]), str(s["latency_ms"]["p95"])]
        row += [f"{f[k]:.0%}" if k in f else "—" for k in keys]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def self_check(cases: list[dict[str, Any]]) -> int:
    """Every expected intent must be a scorable dict with a type; ids unique."""
    ids = [c.get("id") for c in cases]
    assert len(ids) == len(set(ids)), "duplicate ids"
    for c in cases:
        sc = score_case(c["expect"], [
            {k: (v["$any"][0] if isinstance(v, dict) and "$any" in v else v)
             for k, v in e.items()} for e in c["expect"]])
        assert sc["exact"], (c["id"], sc)
        for e in c["expect"]:
            assert "type" in e, c["id"]
    print(f"dataset ok: {len(cases)} phrases, "
          f"{sum(len(c['expect']) for c in cases)} expected intents")
    return 0


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


async def amain(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--models", default="",
                    help="comma-separated model names (default: AI_MODEL from env / --env)")
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--limit", type=int, default=0, help="first N phrases (0 = all)")
    ap.add_argument("--tags", default="", help="only phrases with any of these tags")
    ap.add_argument("--ids", default="", help="only these phrase ids, e.g. 92,57")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--no-repair", action="store_true")
    ap.add_argument("--throttle-retries", type=int, default=4,
                    help="extra attempts after HTTP 429/5xx (pause grows linearly)")
    ap.add_argument("--throttle-pause", type=float, default=10.0, help="base pause, seconds")
    ap.add_argument("--env", default=None, help="file with AI_BASE_URL / AI_API_KEY")
    ap.add_argument("--out", default=None, help="summary JSON path")
    ap.add_argument("--results-dir", default=str(RESULTS))
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(argv)
    if args.concurrency > 4:
        ap.error("concurrency > 4 is not allowed")

    cases = load_dataset(Path(args.dataset))
    if args.self_check:
        return self_check(cases)
    if args.tags:
        want = set(args.tags.split(","))
        cases = [c for c in cases if want & set(c.get("tags", []))]
    if args.ids:
        ids = {int(x) for x in args.ids.split(",") if x.strip()}
        cases = [c for c in cases if c.get("id") in ids]
    if args.limit:
        cases = cases[: args.limit]
    load_env(args.env)
    if not os.environ.get("AI_API_KEY"):
        print("AI_API_KEY is not set (use --env)", file=sys.stderr)
        return 2
    models = [m.strip() for m in (args.models or os.environ.get("AI_MODEL", "")).split(",")
              if m.strip()]
    if not models:
        print("no model: pass --models or set AI_MODEL", file=sys.stderr)
        return 2

    results = Path(args.results_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    summaries = []
    for model in models:
        print(f"== {model}: {len(cases)} phrases", flush=True)
        res = await run_model(model, cases, args)
        _write_json(results / f"{stamp}-{model}.json", res)
        summaries.append(res["summary"])
    table = markdown(summaries)
    print("\n" + table)
    out = Path(args.out) if args.out else results / f"{stamp}-summary.json"
    _write_json(out, {"summaries": summaries, "table": table})
    print(f"\nsaved: {out}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()

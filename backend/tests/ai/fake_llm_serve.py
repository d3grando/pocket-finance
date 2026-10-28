"""Standalone fake OpenAI-compatible LLM for manual runs and the compose smoke test (:8199).

Same handler as the assistant test server (tests/ai/fake_llm.FakeLLM). Differences for manual use:
* an empty scenario queue answers a harmless ``clarify`` intent instead of HTTP 500;
* ``POST /_fake/push`` queues scenarios: ``{"intents": [...]}`` or ``{"content": "..."}`` or
  ``{"status": 500}`` / ``{"delay": 5}``; ``POST /_fake/reset`` clears the queue;
  ``GET /_fake/requests`` returns the last 50 request bodies.

Run: ``python -m tests.ai.fake_llm_serve --port 8199`` from backend/.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tests.ai.fake_llm import FakeLLM, Scenario

DEFAULT_ANSWER = {
    "intents": [
        {
            "type": "clarify",
            "reason": "other",
            "question": "Фейковый AI для тестов: сценарий не задан. Попробуйте простую фразу.",
            "options": [],
        }
    ]
}


def build_app() -> Starlette:
    fake = FakeLLM()

    def default(_body: dict) -> Scenario:
        return Scenario(content=json.dumps(DEFAULT_ANSWER, ensure_ascii=False))

    async def chat(request: Request) -> JSONResponse:
        if not fake.queue:
            fake.push(default)
        resp = await fake._handle(request)
        del fake.requests[:-50]
        return resp

    async def push(request: Request) -> JSONResponse:
        data: Any = await request.json()
        items = data if isinstance(data, list) else [data]
        for it in items:
            if "intents" in it:
                fake.intents(*it["intents"], delay=float(it.get("delay", 0)))
            elif "content" in it:
                fake.push(Scenario(content=str(it["content"]), delay=float(it.get("delay", 0))))
            else:
                fake.push(Scenario(status=int(it.get("status", 500)),
                                   delay=float(it.get("delay", 0))))
        return JSONResponse({"ok": True, "queued": len(fake.queue)})

    async def reset(_request: Request) -> JSONResponse:
        fake.queue.clear()
        fake.requests.clear()
        return JSONResponse({"ok": True})

    async def requests(_request: Request) -> JSONResponse:
        return JSONResponse([r["body"] for r in fake.requests])

    async def models(_request: Request) -> JSONResponse:
        return JSONResponse({"object": "list", "data": [{"id": "fake", "object": "model"}]})

    return Starlette(routes=[
        Route("/v1/chat/completions", chat, methods=["POST"]),
        Route("/v1/models", models, methods=["GET"]),
        Route("/_fake/push", push, methods=["POST"]),
        Route("/_fake/reset", reset, methods=["POST"]),
        Route("/_fake/requests", requests, methods=["GET"]),
    ])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8199)
    a = ap.parse_args()
    uvicorn.run(build_app(), host=a.host, port=a.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()

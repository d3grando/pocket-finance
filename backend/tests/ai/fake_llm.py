"""Fake OpenAI-compatible server for assistant tests (real HTTP on 127.0.0.1, own thread/loop).

Scenarios are queued per test: each incoming ``POST /v1/chat/completions`` pops the next one.
An empty queue answers HTTP 500 «no scenario» (so an unexpected LLM call is visible).
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


@dataclass
class Scenario:
    status: int = 200
    content: str | None = None  # assistant message content
    body: Any = None  # raw JSON body (overrides content)
    delay: float = 0.0
    reject_formats: tuple[str, ...] = ()  # 400 if request response_format.type is one of these
    usage: dict[str, int] = field(default_factory=lambda: {"prompt_tokens": 120,
                                                           "completion_tokens": 30})


def chat_body(content: str, usage: dict[str, int] | None = None, model: str = "fake") -> dict:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": usage or {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }


class FakeLLM:
    def __init__(self) -> None:
        self.queue: deque[Scenario | Callable[[dict], Scenario]] = deque()
        self.requests: list[dict[str, Any]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}/v1"
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ scenario helpers
    def reset(self) -> None:
        deadline = time.time() + 5  # let delayed answers of a previous test finish
        while self.in_flight and time.time() < deadline:
            time.sleep(0.02)
        self.queue.clear()
        self.requests.clear()
        self.max_in_flight = 0

    def push(self, *items: Scenario | Callable[[dict], Scenario]) -> None:
        self.queue.extend(items)

    def reply(self, obj: Any, **kw: Any) -> None:
        """Queue a JSON answer (dict/list → json.dumps; str → as is)."""
        content = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
        self.push(Scenario(content=content, **kw))

    def intents(self, *intents: dict, **kw: Any) -> None:
        self.reply({"intents": list(intents)}, **kw)

    # ------------------------------------------------------------------ server
    async def _handle(self, request: Request) -> JSONResponse:
        body = await request.json()
        rec = {"body": body, "headers": dict(request.headers)}
        with self._lock:
            self.requests.append(rec)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            item = self.queue.popleft() if self.queue else None
        try:
            if item is None:
                return JSONResponse({"error": {"message": "no scenario"}}, status_code=500)
            sc = item(body) if callable(item) else item
            if sc.delay:
                await asyncio.sleep(sc.delay)
            rf = (body.get("response_format") or {}).get("type")
            if rf and rf in sc.reject_formats:
                # the scenario stays for the next (degraded) request
                with self._lock:
                    self.queue.appendleft(item)
                return JSONResponse(
                    {"error": {"message": f"response_format {rf} is not supported by this model",
                               "type": "invalid_request_error"}},
                    status_code=400,
                )
            if sc.status != 200:
                return JSONResponse(sc.body or {"error": {"message": "fake error"}},
                                    status_code=sc.status)
            if sc.body is not None:
                return JSONResponse(sc.body)
            return JSONResponse(chat_body(sc.content or "", sc.usage, body.get("model", "fake")))
        finally:
            with self._lock:
                self.in_flight -= 1

    def start(self) -> None:
        app = Starlette(routes=[Route("/v1/chat/completions", self._handle, methods=["POST"])])
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning",
                                lifespan="off", access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("fake LLM server did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

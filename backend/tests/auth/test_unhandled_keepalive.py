"""Regression: an unhandled exception used to be answered with a JSON
500 and then re-raised past ServerErrorMiddleware → uvicorn logged «Exception in ASGI application»
and closed the transport, so the next request on the same keep-alive connection got ECONNRESET.

Runs the real app under a real uvicorn server (lifespan off, no DB needed) and checks that the
request after a 500 goes over the *same* TCP connection and succeeds.
"""

from __future__ import annotations

import asyncio
import logging
import socket

import httpx
import uvicorn
from fastapi import Request


def _build_app():  # type: ignore[no-untyped-def]
    from app.main import create_app

    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        app = create_app()  # reconfigures root logging — restore pytest's handlers below
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)

    async def boom() -> dict[str, str]:
        raise RuntimeError("boom (test)")

    async def whoami(request: Request) -> dict[str, int]:
        return {"port": request.client.port if request.client else -1}

    app.add_api_route("/api/v1/__test/boom", boom, methods=["POST"])
    app.add_api_route("/api/v1/__test/whoami", whoami, methods=["POST"])
    return app


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


async def test_unhandled_500_keeps_the_keepalive_connection():
    app = _build_app()
    app_log, server_log = _Collect(), _Collect()
    logging.getLogger("pocket.errors").addHandler(app_log)
    logging.getLogger("uvicorn.error").addHandler(server_log)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_config=None, access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(500):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as c:
            r0 = await c.post("/api/v1/__test/whoami", json={})
            assert r0.status_code == 200
            r1 = await c.post(
                "/api/v1/__test/boom",
                json={"amount": "0.000000001"},
                headers={"X-Request-Id": "keepalive-500-test"},
            )
            assert r1.status_code == 500
            assert r1.json()["error"]["code"] == "internal"
            assert r1.json()["error"]["request_id"] == "keepalive-500-test"
            assert r1.headers["X-Request-Id"] == "keepalive-500-test"
            assert "close" not in r1.headers.get("connection", "").lower()
            # the next request on the same client: no ReadError, same TCP connection
            r2 = await c.post("/api/v1/__test/whoami", json={})
            assert r2.status_code == 200
            assert r2.json()["port"] == r0.json()["port"]
        # the error is still logged (by the app, with the request id), not by uvicorn
        assert any(
            r.exc_info and getattr(r, "request_id", None) == "keepalive-500-test"
            for r in app_log.records
        )
        assert not any(
            "Exception in ASGI application" in r.getMessage() for r in server_log.records
        )
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        logging.getLogger("pocket.errors").removeHandler(app_log)
        logging.getLogger("uvicorn.error").removeHandler(server_log)

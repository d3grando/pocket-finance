"""FastAPI application factory."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import get_settings
from app.core.errors import install_error_handlers
from app.db import dispose_engine, get_engine


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", None)
        if rid:
            d["request_id"] = rid
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False)


def setup_logging(level: str) -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_engine()
    from app.services.events.bus import start_listener, stop_listener

    await start_listener()
    try:
        yield
    finally:
        await stop_listener()
        await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    s.check_prod()
    setup_logging(s.log_level)
    app = FastAPI(
        title="Pocket Finance API",
        version=s.app_version,
        lifespan=lifespan,
        docs_url="/api/docs" if not s.is_prod else None,
        openapi_url="/api/openapi.json" if not s.is_prod else None,
        redoc_url=None,
    )
    install_error_handlers(app)

    # Order: last added = outermost. CORS must be outermost.
    from app.middleware import install_middlewares

    install_middlewares(app)
    if s.is_prod:
        # Prod: the web is same-origin (no CORS needed); only Electron (app://pocket) calls
        # cross-origin and it authenticates with Bearer device tokens only — no credentials.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[s.desktop_origin],
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-Id"],
            expose_headers=["X-Request-Id", "Retry-After"],
            max_age=600,
        )
    else:
        # Dev/test: web dev servers on other ports use cookie sessions (credentials).
        app.add_middleware(
            CORSMiddleware,
            allow_origins=s.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "X-CSRF-Token",
                "X-Request-Id",
                "X-Telegram-User-Id",
            ],
            expose_headers=["X-Request-Id", "Retry-After"],
            max_age=600,
        )
    app.include_router(api_router)
    return app


app = create_app()

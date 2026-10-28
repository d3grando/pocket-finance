"""HTTP middlewares. ``install_middlewares`` is called by app.main.create_app.

Order (outermost first; CORS is added by main.py after this and wraps everything):
  RequestContextMiddleware  — X-Request-Id, client IP (trusted proxies), security headers, 500
  RateLimitMiddleware       — 429 rate_limited + Retry-After (ARCH §6)
  SessionCookieMiddleware   — sliding renewal / clearing of session cookies
"""

from __future__ import annotations

from fastapi import FastAPI

from app.middleware.ratelimit import RateLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware, install_log_context
from app.middleware.session_cookies import SessionCookieMiddleware


def install_middlewares(app: FastAPI) -> None:
    install_log_context()
    # add_middleware inserts at position 0 → the last added is the outermost.
    app.add_middleware(SessionCookieMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)

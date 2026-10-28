"""Request context + security headers. Pure ASGI (SSE-friendly).

* ``X-Request-Id``: accepted from the client if it looks sane, else generated; echoed on every
  response; ``scope.state.request_id`` and the logging context (``record.request_id``).
* Client IP: ``scope.state.client_ip`` — X-Forwarded-For is trusted only when the direct peer is
  in TRUSTED_PROXIES (walk the chain right→left, skip trusted hops).
* Security headers on every response: nosniff, X-Frame-Options DENY, Referrer-Policy same-origin,
  CSP (strict for /api, SPA policy for anything else), Permissions-Policy, COOP, HSTS (prod),
  ``Cache-Control: no-store`` for API responses unless the route set its own.
* Unhandled exception before the response started → logged here and answered with a JSON 500
  carrying the request id; the exception is **not** re-raised. Re-raising (Starlette's default)
  makes uvicorn log «Exception in ASGI application» and close the transport after the 500 was
  sent, so the client's next request on that keep-alive connection fails with ECONNRESET.
  An exception after the response started is re-raised: the stream is broken anyway.
"""

from __future__ import annotations

import contextvars
import ipaddress
import logging
import re
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.core.errors import error_body
from app.core.security_config import IPNetwork, get_security_settings

REQUEST_ID_HEADER = "X-Request-Id"
_RID_RE = re.compile(r"[A-Za-z0-9._:\-]{8,64}")
_request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pf_request_id", default=None
)
_DOCS_PATHS = ("/api/docs", "/api/openapi.json", "/api/redoc")
log = logging.getLogger("pocket.errors")

PERMISSIONS_POLICY = (
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
    "publickey-credentials-get=(self), publickey-credentials-create=(self)"
)


def current_request_id() -> str | None:
    return _request_id_ctx.get()


_factory_installed = False


def install_log_context() -> None:
    """Add ``request_id`` to every LogRecord created while a request is running."""
    global _factory_installed
    if _factory_installed:
        return
    old = logging.getLogRecordFactory()

    def factory(*args, **kwargs):  # type: ignore[no-untyped-def]
        record = old(*args, **kwargs)
        if not hasattr(record, "request_id"):
            rid = _request_id_ctx.get()
            if rid:
                record.request_id = rid
        return record

    logging.setLogRecordFactory(factory)
    _factory_installed = True


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    value = value.strip().strip('"')
    if value.startswith("[") and "]" in value:  # [v6]:port
        value = value[1 : value.index("]")]
    elif value.count(":") == 1 and "." in value:  # v4:port
        value = value.split(":", 1)[0]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _trusted(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, nets: list[IPNetwork]) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip.version == n.version and ip in n for n in nets)


def resolve_client_ip(peer: str | None, xff: str | None, nets: list[IPNetwork]) -> str | None:
    """Real client IP: peer, or the right-most untrusted X-Forwarded-For hop behind trusted
    proxies."""
    peer_ip = _parse_ip(peer) if peer else None
    if peer_ip is None:
        return peer
    if not xff or not _trusted(peer_ip, nets):
        return str(peer_ip)
    candidate = peer_ip
    for part in reversed([p for p in xff.split(",") if p.strip()]):
        ip = _parse_ip(part)
        if ip is None:
            break  # garbage → stop at the last trusted hop
        candidate = ip
        if not _trusted(ip, nets):
            break
    return str(candidate)


def apply_security_headers(headers: MutableHeaders, path: str) -> None:
    sec = get_security_settings()
    is_api = path.startswith("/api/")
    is_docs = path.startswith(_DOCS_PATHS)

    def put(name: str, value: str) -> None:
        if name not in headers:
            headers[name] = value

    put("X-Content-Type-Options", "nosniff")
    put("X-Frame-Options", "DENY")
    put("Referrer-Policy", "same-origin")
    put("Cross-Origin-Opener-Policy", "same-origin")
    put("Permissions-Policy", PERMISSIONS_POLICY)
    if not is_docs:
        put("Content-Security-Policy", sec.csp_api if is_api else sec.csp_web)
    if is_api:
        put("Cache-Control", "no-store")
    if get_settings().is_prod and sec.hsts_seconds > 0:
        put("Strict-Transport-Security", f"max-age={sec.hsts_seconds}; includeSubDomains")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        rid = headers.get("x-request-id") or ""
        if not _RID_RE.fullmatch(rid):
            rid = uuid.uuid4().hex
        state = scope.setdefault("state", {})
        state["request_id"] = rid
        client = scope.get("client")
        state["client_ip"] = resolve_client_ip(
            client[0] if client else None,
            headers.get("x-forwarded-for"),
            get_security_settings().trusted_networks,
        )
        path: str = scope.get("path", "")
        started = False

        async def _send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                h = MutableHeaders(scope=message)
                h[REQUEST_ID_HEADER] = rid
                apply_security_headers(h, path)
            await send(message)

        token = _request_id_ctx.set(rid)
        try:
            await self.app(scope, receive, _send)
        except Exception:
            if started:
                raise
            log.exception("unhandled error")
            resp = JSONResponse(
                error_body("internal", "Внутренняя ошибка сервера", None, rid), status_code=500
            )
            await resp(scope, receive, _send)
        finally:
            _request_id_ctx.reset(token)

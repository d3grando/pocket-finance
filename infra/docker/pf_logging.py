"""JSON logging for the backend container outside the app.

ARCH §6: logs are JSON lines on stdout. The app (app/main.py) configures the root logger
itself, but uvicorn's own loggers (startup, errors, access log) and Alembic (migrations at
start) are configured before/outside the app — this module covers them:

* ``JsonFormatter`` — same shape as the app's formatter: ts, level, logger, msg
  (+ request_id, exc).
* ``AccessJsonFormatter`` — uvicorn access records with structured fields
  (client, method, path, status).
* ``HealthcheckFilter`` — drops access lines of the container healthcheck
  (``GET /api/v1/health`` from loopback every 10 s); external health probes are still logged.
* ``uvicorn-log.json`` (next to this file) wires them up: ``uvicorn --log-config …``.
* ``configure(level)`` — root logger → JSON on stdout (used by pf_migrate).

Stdlib only: imported by uvicorn before the app is loaded.
"""

from __future__ import annotations

import json
import logging
import sys

HEALTH_PATH = "/api/v1/health"
_LOOPBACK_PREFIXES = ("127.", "::1", "[::1]")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", None)
        if rid:
            d["request_id"] = rid
        d.update(self.extra_fields(record))
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False, default=str)

    def extra_fields(self, record: logging.LogRecord) -> dict[str, object]:
        return {}


def access_args(record: logging.LogRecord) -> tuple[str, str, str, str, int] | None:
    """uvicorn access record args: (client_addr, method, path, http_version, status)."""
    args = record.args
    if isinstance(args, tuple) and len(args) == 5:
        client, method, path, version, status = args
        try:
            return str(client), str(method), str(path), str(version), int(status)
        except (TypeError, ValueError):
            return None
    return None


class AccessJsonFormatter(JsonFormatter):
    def format(self, record: logging.LogRecord) -> str:
        # no query strings in logs (msg and path): /search?q=…, /transactions?q=… carry user text
        if access_args(record) is not None:
            record = logging.makeLogRecord(record.__dict__)
            args = list(record.args)  # type: ignore[arg-type]
            args[2] = str(args[2]).split("?", 1)[0]
            record.args = tuple(args)
        return super().format(record)

    def extra_fields(self, record: logging.LogRecord) -> dict[str, object]:
        a = access_args(record)
        if a is None:
            return {}
        client, method, path, _version, status = a
        return {"client": client, "method": method, "path": path, "status": status}


class HealthcheckFilter(logging.Filter):
    """Hide the container's own healthcheck requests from the access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        a = access_args(record)
        if a is None:
            return True
        client, method, path, _version, _status = a
        local = client.startswith(_LOOPBACK_PREFIXES)
        return not (local and method == "GET" and path == HEALTH_PATH)


def configure(level: str | int = "INFO") -> None:
    """Root logger → one JSON line per record on stdout."""
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level if isinstance(level, int) else str(level).upper())

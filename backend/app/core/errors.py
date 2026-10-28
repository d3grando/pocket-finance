"""Application errors and the uniform JSON error model (docs/ARCH.md §10)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError as SADataError
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("pocket.errors")


class AppError(Exception):
    status: int = 400
    code: str = "bad_request"
    message: str = "Некорректный запрос"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status: int | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status = status or self.status
        self.details = details or {}
        self.headers = headers or {}
        super().__init__(self.message)


class BadRequest(AppError):
    status, code, message = 400, "bad_request", "Некорректный запрос"


class Unauthorized(AppError):
    status, code, message = 401, "unauthorized", "Нужно войти"


class Forbidden(AppError):
    status, code, message = 403, "forbidden", "Нет доступа"


class CsrfFailed(AppError):
    status, code, message = 403, "csrf_failed", "Проверка CSRF не пройдена, обновите страницу"


class NotFound(AppError):
    status, code, message = 404, "not_found", "Не найдено"


class Conflict(AppError):
    status, code, message = 409, "conflict", "Конфликт изменений"


class VersionConflict(Conflict):
    code, message = "version_conflict", "Запись уже изменили, обновите данные"


class Gone(AppError):
    status, code, message = 410, "gone", "Срок действия истёк"


class ValidationFailed(AppError):
    status, code, message = 422, "validation_error", "Проверьте введённые данные"


class LimitReached(ValidationFailed):
    """Too many objects of one kind for one user (accounts, categories, merchants, people):
    the parser and the AI context index all of them."""

    code, message = "limit_reached", "Достигнут лимит"


class IdempotencyKeyRequired(AppError):
    status, code, message = 428, "idempotency_key_required", "Нужен заголовок Idempotency-Key"


class RateLimited(AppError):
    status, code, message = 429, "rate_limited", "Слишком много запросов, подождите"


class ServiceUnavailable(AppError):
    status, code, message = 503, "unavailable", "Сервис временно недоступен"


class AIUnavailable(ServiceUnavailable):
    code, message = "ai_unavailable", "AI сейчас недоступен"


def error_body(
    code: str, message: str, details: dict[str, Any] | None, request_id: str | None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id,
        }
    }


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _bad_value(exc: DBAPIError) -> bool:
    """A value only the database / driver rejected: int64 overflow of an id or offset
    («value out of int64 range»), NUMERIC overflow, a bad date / text (SQLSTATE class 22)."""
    orig = getattr(exc, "orig", None)
    cause = getattr(orig, "__cause__", None) or orig
    try:
        from asyncpg.exceptions import DataError as PgDataError
        from asyncpg.exceptions._base import DataError as DriverDataError
    except ImportError:  # pragma: no cover - asyncpg is the only driver
        return False
    if isinstance(cause, (PgDataError, DriverDataError)):
        return True
    code = getattr(cause, "sqlstate", None) or getattr(orig, "pgcode", None)
    return isinstance(code, str) and code.startswith("22")


def _value_error_response(request: Request) -> JSONResponse:
    return JSONResponse(
        error_body(
            "validation_error", "Проверьте введённые данные",
            {"fields": [{"loc": ["body"], "msg": "Недопустимое значение"}]}, _rid(request),
        ),
        status_code=422,
    )


def _internal_response(request: Request) -> JSONResponse:
    return JSONResponse(
        error_body("internal", "Внутренняя ошибка сервера", None, _rid(request)),
        status_code=500,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            error_body(exc.code, exc.message, exc.details, _rid(request)),
            status_code=exc.status,
            headers=exc.headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"loc": [str(x) for x in e.get("loc", ())], "msg": str(e.get("msg", ""))}
            for e in exc.errors()
        ]
        return JSONResponse(
            error_body(
                "validation_error", "Проверьте введённые данные", {"fields": fields}, _rid(request)
            ),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "bad_request"}.get(
            exc.status_code, "bad_request"
        )
        return JSONResponse(
            error_body(code, str(exc.detail), None, _rid(request)),
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(SADataError)
    async def _data_error(request: Request, exc: SADataError) -> JSONResponse:
        # bad value that only the DB caught (\x00 in text, NUMERIC overflow…) → 422, not 500
        log.warning("db data error: %s", type(getattr(exc, "orig", exc)).__name__)
        return _value_error_response(request)

    @app.exception_handler(DBAPIError)
    async def _dbapi_error(request: Request, exc: DBAPIError) -> JSONResponse:
        # asyncpg errors arrive as a generic DBAPIError: only bad values become a 422
        if _bad_value(exc):
            cause = getattr(exc.orig, "__cause__", None) or exc.orig
            log.warning("db data error: %s", type(cause).__name__)
            return _value_error_response(request)
        log.exception("unhandled database error", exc_info=exc)
        return _internal_response(request)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return _internal_response(request)

"""Idempotency-Key handling. Contract: docs/ARCH.md §5.2.

Usage in a route::

    @router.post("/transactions", status_code=201)
    async def create(body: TransactionCreate, principal: PrincipalDep, session: SessionDep,
                     idem: IdemDep):
        if idem.replay is not None:
            return idem.replay                    # stored response of the first attempt
        result = await ledger.create_transaction(session, principal, body)
        await session.commit()
        return await idem.save(201, result)       # stores JSON, returns JSONResponse

On an exception the key is released (row deleted) so a retry is possible — the dependency does
this automatically via ``yield``/except when ``save`` was not called.

Storage: ``idempotency_keys`` (PK user_id+key). The ``in_progress`` row is inserted with
``INSERT … ON CONFLICT DO NOTHING`` and committed immediately (its own short transaction), so a
concurrent duplicate sees it and gets 409 ``idempotency_in_progress``. Same key + same request
hash (method, path+query, body) after completion → stored response is replayed (header
``Idempotent-Replayed: true``); different hash → 409 ``idempotency_mismatch``. Rows live 48 h
(cleanup is the worker's job; an expired row is simply replaced).

Abandoned keys: if the API process dies or is restarted mid-request, the ``in_progress`` row is
never released. Such a row is a *lease*: it is alive for ``in_progress_lease()`` (default
``max(60 s, 2·AI_TIMEOUT + 20 s)`` — longer than any request can run) after ``created_at``.
Until then a duplicate gets 409 ``idempotency_in_progress`` with ``Retry-After``/
``details.retry_after``; after that the row is considered abandoned and the retry takes the key
over (deletes the stale row and runs the request). Ledger routes are additionally protected by
the unique ``action_log`` key (``ledger.idempotency.run_idempotent``), so a takeover can never
book an operation twice.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydantic_core import to_jsonable_python
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.api.deps_base import PrincipalDep, SessionDep
from app.config import get_settings
from app.core.errors import BadRequest, Conflict, IdempotencyKeyRequired
from app.core.principal import Principal
from app.core.security_config import get_security_settings
from app.models import IdempotencyKey

log = logging.getLogger("pocket.idempotency")

IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replayed"
IDEMPOTENCY_TTL = timedelta(hours=48)
_KEY_RE = re.compile(r"[A-Za-z0-9._:\-]{1,128}")
IN_PROGRESS_MESSAGE = "Этот запрос ещё выполняется, подождите"


def in_progress_lease() -> timedelta:
    """How long an ``in_progress`` key stays alive before it counts as abandoned.

    ``IDEMPOTENCY_LEASE_SECONDS`` if set; else ``max(60, 2·AI_TIMEOUT + 20)`` seconds, i.e.
    comfortably longer than the slowest request (the LLM call is hard-capped by AI_TIMEOUT)."""
    explicit = get_security_settings().idempotency_lease_seconds
    if explicit and explicit > 0:
        return timedelta(seconds=explicit)
    ai_timeout = float(get_settings().ai_timeout or 0)
    return timedelta(seconds=max(60.0, 2 * ai_timeout + 20))


class Idempotency:
    """Per-request idempotency handle."""

    def __init__(
        self,
        principal: Principal,
        key: str | None,
        *,
        session: AsyncSession | None = None,
        owned: bool = False,
    ) -> None:
        self.principal = principal
        self.key = key
        #: Completed response of an earlier identical request → return it as is.
        self.replay: JSONResponse | None = None
        self.saved = False
        self._session = session
        #: True when this request inserted the in_progress row (and must finish or release it).
        self._owned = owned

    async def save(self, status_code: int, body: Any) -> JSONResponse:
        """Persist the response (state=done) and commit; return the JSONResponse.

        ``body`` may be a Pydantic model (serialized with mode='json') or a JSON-able object.
        Without a key: just builds the JSONResponse.
        """
        data = _jsonable(body)
        if self._owned and self._session is not None and self.key is not None:
            await self._session.execute(
                update(IdempotencyKey)
                .where(
                    IdempotencyKey.user_id == self.principal.user_id,
                    IdempotencyKey.key == self.key,
                )
                .values(state="done", response_status=status_code, response_body=data)
            )
            await self._session.commit()
            self._owned = False
        self.saved = True
        return JSONResponse(data, status_code=status_code)

    async def release(self) -> None:
        """Delete the in_progress row (request failed) so the client can retry.

        Tries the request session first; if that connection is broken (e.g. the request was
        cancelled mid-query on shutdown) it retries on a fresh session. If both fail, the row
        stays and is taken over once its lease expires (see ``in_progress_lease``)."""
        if not self._owned or self._session is None or self.key is None:
            return
        self._owned = False
        try:
            await self._session.rollback()
            await _delete_in_progress(self._session, self.principal.user_id, self.key)
            return
        except Exception:
            log.warning(
                "idempotency: releasing key on the request session failed, retrying on a new one",
                exc_info=True,
            )
        try:
            from app.db import get_sessionmaker

            async with get_sessionmaker()() as fresh:
                await _delete_in_progress(fresh, self.principal.user_id, self.key)
        except Exception:
            log.warning(
                "idempotency: key left in_progress (user=%s); it is freed after the lease",
                self.principal.user_id,
                exc_info=True,
            )


async def _delete_in_progress(session: AsyncSession, user_id: int, key: str) -> None:
    await session.execute(
        delete(IdempotencyKey).where(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.key == key,
            IdempotencyKey.state == "in_progress",
        )
    )
    await session.commit()


async def _delete_stale(
    session: AsyncSession, user_id: int, key: str, condition: ColumnElement[bool]
) -> int:
    """Delete the key row only if ``condition`` still holds (never a row another request has
    just re-created); commit. Returns the number of deleted rows."""
    res = await session.execute(
        delete(IdempotencyKey).where(
            IdempotencyKey.user_id == user_id, IdempotencyKey.key == key, condition
        )
    )
    await session.commit()
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


def _jsonable(body: Any) -> Any:
    if isinstance(body, BaseModel):
        return body.model_dump(mode="json")
    if isinstance(body, list):
        return [_jsonable(x) for x in body]
    if isinstance(body, dict):
        return {k: _jsonable(v) for k, v in body.items()}
    return to_jsonable_python(body)


def _request_hash(request: Request, body: bytes) -> bytes:
    h = hashlib.sha256()
    h.update(request.method.encode())
    h.update(b"\0")
    h.update(request.url.path.encode())
    h.update(b"?")
    h.update(request.url.query.encode())
    h.update(b"\0")
    h.update(body)
    return h.digest()


async def _begin(
    request: Request, principal: Principal, session: AsyncSession, *, required: bool
) -> Idempotency:
    raw = request.headers.get(IDEMPOTENCY_HEADER)
    key = raw.strip() if raw else ""
    if not key:
        if required:
            raise IdempotencyKeyRequired()
        return Idempotency(principal, None)
    if not _KEY_RE.fullmatch(key):
        raise BadRequest(
            "Некорректный Idempotency-Key (до 128 символов: буквы, цифры, . _ : -)",
            details={"header": IDEMPOTENCY_HEADER},
        )
    body = await request.body()
    req_hash = _request_hash(request, body)
    path = request.url.path[:256]

    lease = in_progress_lease()
    for _ in range(3):
        now = datetime.now(UTC)
        inserted = (
            await session.execute(
                pg_insert(IdempotencyKey)
                .values(
                    user_id=principal.user_id,
                    key=key,
                    method=request.method[:8],
                    path=path,
                    request_hash=req_hash,
                    state="in_progress",
                    expires_at=now + IDEMPOTENCY_TTL,
                )
                .on_conflict_do_nothing(index_elements=["user_id", "key"])
                .returning(IdempotencyKey.key)
            )
        ).scalar_one_or_none()
        if inserted is not None:
            await session.commit()
            return Idempotency(principal, key, session=session, owned=True)

        row = (
            await session.execute(
                select(IdempotencyKey)
                .where(IdempotencyKey.user_id == principal.user_id, IdempotencyKey.key == key)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None:
            continue  # deleted between our INSERT and SELECT → try again
        if row.expires_at <= now:
            await _delete_stale(
                session, principal.user_id, key, IdempotencyKey.expires_at <= now
            )
            continue
        if row.request_hash != req_hash:
            await session.rollback()
            raise Conflict(
                "Этот Idempotency-Key уже использован для другого запроса",
                code="idempotency_mismatch",
            )
        if row.state != "done" or row.response_status is None:
            lease_end = row.created_at + lease
            if row.state == "in_progress" and lease_end <= now:
                # The attempt that owned the key died (process killed/restarted mid-request):
                # take the key over. The predicate makes the delete a no-op if a concurrent
                # retry has already replaced the row.
                n = await _delete_stale(
                    session,
                    principal.user_id,
                    key,
                    (IdempotencyKey.state == "in_progress")
                    & (IdempotencyKey.created_at <= now - lease),
                )
                if n:
                    log.warning(
                        "idempotency: abandoned in_progress key taken over (user=%s, age=%ss)",
                        principal.user_id,
                        int((now - row.created_at).total_seconds()),
                    )
                continue
            await session.rollback()
            retry_after = max(1, math.ceil((lease_end - now).total_seconds()))
            raise Conflict(
                IN_PROGRESS_MESSAGE,
                code="idempotency_in_progress",
                details={"retry_after": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        idem = Idempotency(principal, key)
        idem.replay = JSONResponse(
            row.response_body, status_code=row.response_status, headers={REPLAY_HEADER: "true"}
        )
        await session.rollback()
        return idem
    raise Conflict(IN_PROGRESS_MESSAGE, code="idempotency_in_progress")


async def idempotency_required(
    request: Request, principal: PrincipalDep, session: SessionDep
) -> AsyncIterator[Idempotency]:
    """428 ``idempotency_key_required`` without header; 409 ``idempotency_in_progress`` /
    ``idempotency_mismatch`` per ARCH §5.2; sets ``replay`` for a completed duplicate."""
    idem = await _begin(request, principal, session, required=True)
    try:
        yield idem
    except BaseException:
        await idem.release()
        raise
    if not idem.saved:
        await idem.release()


async def idempotency_optional(
    request: Request, principal: PrincipalDep, session: SessionDep
) -> AsyncIterator[Idempotency]:
    """Same as required, but a missing header means no idempotency (key=None)."""
    idem = await _begin(request, principal, session, required=False)
    try:
        yield idem
    except BaseException:
        await idem.release()
        raise
    if not idem.saved:
        await idem.release()

"""Backend HTTP client. Every call carries Bearer BOT_SERVICE_TOKEN and
X-Telegram-User-Id; mutations carry Idempotency-Key (tg:{chat}:{msg} / tgcb:{callback_id} /
tgt:{chat}:{msg}:{n} for option buttons).
Contract: docs/API.md (§13 for the bot). Money stays as strings (Decimal on the backend)."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

log = logging.getLogger(__name__)


class ApiError(Exception):
    """Non-2xx answer or transport failure. ``status`` 0 = no HTTP answer (network/timeout)."""

    def __init__(self, status: int, code: str, message: str, *,
                 details: dict[str, Any] | None = None, retry_after: int | None = None,
                 request_id: str | None = None) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message
        self.details = details or {}
        self.retry_after = retry_after
        self.request_id = request_id

    @property
    def is_server(self) -> bool:
        return self.status >= 500 or self.status == 0


def _error_from_response(resp: httpx.Response) -> ApiError:
    code, message, details, rid = "http_error", f"HTTP {resp.status_code}", {}, None
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            code = str(err.get("code") or code)
            message = str(err.get("message") or message)
            details = err.get("details") or {}
            rid = err.get("request_id")
        elif isinstance(body, dict) and "detail" in body:  # bare FastAPI error
            message = str(body["detail"])
    except ValueError:
        pass
    retry_after = None
    ra = resp.headers.get("Retry-After")
    if ra and ra.strip().isdigit():
        retry_after = int(ra.strip())
    return ApiError(resp.status_code, code, message, details=details,
                    retry_after=retry_after, request_id=rid or resp.headers.get("X-Request-Id"))


class PocketApi:
    def __init__(self, base_url: str, service_token: str, *, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {service_token}",
                "Accept": "application/json",
                "User-Agent": "pocket-bot/0.1",
            },
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ transport
    async def _request(self, method: str, path: str, *, tg_user_id: int | None = None,
                       idem_key: str | None = None, json: Any = None,
                       params: Any = None) -> Any:
        headers: dict[str, str] = {}
        if tg_user_id is not None:
            headers["X-Telegram-User-Id"] = str(tg_user_id)
        if idem_key is not None:
            headers["Idempotency-Key"] = idem_key[:128]
        try:
            resp = await self._client.request(method, path, headers=headers, json=json,
                                              params=params)
        except httpx.TimeoutException as e:
            raise ApiError(0, "timeout", "Сервер не ответил вовремя") from e
        except httpx.HTTPError as e:
            raise ApiError(0, "network", "Сервер недоступен") from e
        if resp.status_code >= 400:
            err = _error_from_response(resp)
            log.warning("api %s %s -> %s %s (request_id=%s)", method, path, err.status,
                        err.code, err.request_id)
            raise err
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as e:
            raise ApiError(resp.status_code, "bad_response", "Сервер ответил не JSON") from e

    # ------------------------------------------------------------------ assistant
    async def assistant(self, tg_user_id: int, text: str, *, idem_key: str,
                        edit_transaction_id: int | None = None) -> dict[str, Any]:
        """POST /assistant/messages → AssistantResponse JSON."""
        body: dict[str, Any] = {"text": text}
        if edit_transaction_id is not None:
            body["context"] = {"edit_transaction_id": edit_transaction_id}
        return await self._request("POST", "/assistant/messages", tg_user_id=tg_user_id,
                                   idem_key=idem_key, json=body)

    async def confirm_preview(self, tg_user_id: int, token: str, *,
                              idem_key: str) -> dict[str, Any]:
        return await self._request("POST", f"/assistant/previews/{token}/confirm",
                                   tg_user_id=tg_user_id, idem_key=idem_key)

    async def cancel_preview(self, tg_user_id: int, token: str) -> Any:
        return await self._request("POST", f"/assistant/previews/{token}/cancel",
                                   tg_user_id=tg_user_id)

    # ------------------------------------------------------------------ actions / undo
    async def undo(self, tg_user_id: int, action_id: str, *, idem_key: str) -> dict[str, Any]:
        """POST /actions/{id}/undo → UndoResult (also «Вернуть» = undo of the undo-action)."""
        return await self._request("POST", f"/actions/{action_id}/undo", tg_user_id=tg_user_id,
                                   idem_key=idem_key)

    async def recent_actions(self, tg_user_id: int) -> list[dict[str, Any]]:
        return await self._request("GET", "/actions", tg_user_id=tg_user_id,
                                   params={"recent": "true", "limit": 10}) or []

    # ------------------------------------------------------------------ transactions
    async def get_transaction(self, tg_user_id: int, tx_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/transactions/{tx_id}", tg_user_id=tg_user_id)

    async def list_transactions(self, tg_user_id: int, **params: Any) -> dict[str, Any]:
        q = {k: v for k, v in params.items() if v is not None}
        return await self._request("GET", "/transactions", tg_user_id=tg_user_id, params=q)

    async def needs_account_count(self, tg_user_id: int) -> int:
        data = await self._request("GET", "/transactions/needs-account/count",
                                   tg_user_id=tg_user_id)
        return int((data or {}).get("count") or 0)

    async def patch_transaction(self, tg_user_id: int, tx_id: int, patch: dict[str, Any], *,
                                idem_key: str | None = None) -> dict[str, Any]:
        return await self._request("PATCH", f"/transactions/{tx_id}", tg_user_id=tg_user_id,
                                   idem_key=idem_key, json=patch)

    async def assign_account(self, tg_user_id: int, tx_id: int, account_id: int | None,
                             *, idem_key: str, version: int | None = None) -> dict[str, Any]:
        """POST /transactions/{id}/account → MutationResult. ``None`` = «Без счёта».
        ``version`` (optional): 409 version_conflict when the operation changed; without it the
        backend answers 409 once the account is already chosen."""
        body: dict[str, Any] = {"account_id": account_id}
        if version is not None:
            body["version"] = version
        return await self._request("POST", f"/transactions/{tx_id}/account",
                                   tg_user_id=tg_user_id, idem_key=idem_key, json=body)

    async def suggest_categories(self, tg_user_id: int, tx_id: int,
                                 q: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"transaction_id": tx_id}
        if q:
            params["q"] = q
        return await self._request("GET", "/categories/suggest", tg_user_id=tg_user_id,
                                   params=params)

    async def list_accounts(self, tg_user_id: int) -> list[dict[str, Any]]:
        return await self._request("GET", "/accounts", tg_user_id=tg_user_id) or []

    # ------------------------------------------------------------------ stats / reports
    async def summary(self, tg_user_id: int, currency: str = "VND") -> dict[str, Any]:
        return await self._request("GET", "/stats/summary", tg_user_id=tg_user_id,
                                   params={"currency": currency})

    async def by_category(self, tg_user_id: int, preset: str, *, kind: str = "expense",
                          currency: str = "VND") -> dict[str, Any]:
        return await self._request("GET", "/stats/by-category", tg_user_id=tg_user_id,
                                   params={"preset": preset, "kind": kind,
                                           "currency": currency})

    async def report(self, tg_user_id: int, kind: str, on: date | None = None) -> dict[str, Any]:
        params = {"on": on.isoformat()} if on else None
        return await self._request("GET", f"/reports/{kind}", tg_user_id=tg_user_id,
                                   params=params)

    # ------------------------------------------------------------------ settings
    async def get_settings(self, tg_user_id: int) -> dict[str, Any]:
        return await self._request("GET", "/settings", tg_user_id=tg_user_id)

    async def patch_settings(self, tg_user_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PATCH", "/settings", tg_user_id=tg_user_id, json=patch)

    # ------------------------------------------------------------------ recurring
    async def list_recurring(self, tg_user_id: int) -> list[dict[str, Any]]:
        return await self._request("GET", "/recurring", tg_user_id=tg_user_id) or []

    async def recurring_record(self, tg_user_id: int, rid: int, *, idem_key: str,
                               amount_text: str | None = None,
                               due_on: str | None = None) -> dict[str, Any]:
        """``due_on`` (YYYY-MM-DD, from the reminder button): a second record of the same
        occurrence → 409 already_exists instead of a duplicate."""
        body: dict[str, Any] = {}
        if amount_text:
            body["amount_text"] = amount_text
        return await self._request("POST", f"/recurring/{rid}/record", tg_user_id=tg_user_id,
                                   idem_key=idem_key, json=body,
                                   params={"due_on": due_on} if due_on else None)

    async def recurring_skip(self, tg_user_id: int, rid: int, *, due_on: str | None = None,
                             idem_key: str | None = None) -> dict[str, Any]:
        return await self._request("POST", f"/recurring/{rid}/skip", tg_user_id=tg_user_id,
                                   idem_key=idem_key,
                                   params={"due_on": due_on} if due_on else None)

    # ------------------------------------------------------------------ debts (DEBTS.md §7)
    async def list_debts(self, tg_user_id: int, *, include_settled: bool = False) -> dict[str, Any]:
        """GET /debts → DebtsOut {owed_to_me[], i_owe[], settled[], totals}."""
        return await self._request("GET", "/debts", tg_user_id=tg_user_id,
                                   params={"include_settled": "true" if include_settled
                                           else "false"}) or {}

    async def get_debt(self, tg_user_id: int, debt_id: int, *, limit: int = 10) -> dict[str, Any]:
        """GET /debts/{id} → DebtDetailOut {debt, history[] (newest first), total}."""
        return await self._request("GET", f"/debts/{int(debt_id)}", tg_user_id=tg_user_id,
                                   params={"limit": limit}) or {}

    async def snooze_debt(self, tg_user_id: int, debt_id: int, days: int = 7) -> dict[str, Any]:
        """POST /debts/{id}/snooze {days} → DebtOut («Напомнить позже»)."""
        return await self._request("POST", f"/debts/{int(debt_id)}/snooze",
                                   tg_user_id=tg_user_id, json={"days": int(days)}) or {}

    # ------------------------------------------------------------------ outbox (bot-svc)
    async def outbox_claim(self, limit: int = 20) -> list[dict[str, Any]]:
        data = await self._request("POST", "/bot/outbox/claim", params={"limit": limit})
        return list((data or {}).get("items") or [])

    async def outbox_ack(self, item_id: int, *, ok: bool, error: str | None, retry: bool) -> None:
        body = {"ok": ok, "error": (error[:1000] if error else None), "retry": retry}
        await self._request("POST", f"/bot/outbox/{item_id}/ack", json=body)

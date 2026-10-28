"""Contract checks against the real backend:
- every PocketApi call hits a route that exists in the backend router with that HTTP method
  (fixtures/openapi_routes.json — regenerate with tests/contract/gen_openapi.py);
- callback_data emitted by the bot only uses prefixes from API.md §13 (+ documented bot-local)."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import httpx

from pocket_bot import callbacks as cb
from pocket_bot.api import PocketApi
from tests.conftest import API_BASE, OWNER, SERVICE_TOKEN

ROUTES: dict[str, list[str]] = json.loads(
    (Path(__file__).parent / "fixtures" / "openapi_routes.json").read_text())


def _route_regex(template: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", template) + "$")


COMPILED = [(_route_regex(p), set(m)) for p, m in ROUTES.items()]


def _exists(method: str, path: str) -> bool:
    return any(rx.match(path) and method in methods for rx, methods in COMPILED)


async def test_every_client_call_hits_a_backend_route():
    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        if req.url.path.endswith("/claim"):
            return httpx.Response(200, json={"items": []})
        if req.url.path.endswith("/count"):
            return httpx.Response(200, json={"count": 0})
        return httpx.Response(200, json={})

    api = PocketApi(API_BASE, SERVICE_TOKEN, transport=httpx.MockTransport(handler))
    tok = "8d2e1f50-3333-4c4d-9e5f-000000000001"
    await api.assistant(OWNER, "x", idem_key="k")
    await api.confirm_preview(OWNER, tok, idem_key="k")
    await api.cancel_preview(OWNER, tok)
    await api.undo(OWNER, tok, idem_key="k")
    await api.recent_actions(OWNER)
    await api.get_transaction(OWNER, 1)
    await api.list_transactions(OWNER, status="needs_account")
    await api.needs_account_count(OWNER)
    await api.patch_transaction(OWNER, 1, {"version": 1})
    await api.assign_account(OWNER, 1, None, idem_key="k")
    await api.suggest_categories(OWNER, 1, "x")
    await api.list_accounts(OWNER)
    await api.summary(OWNER)
    await api.by_category(OWNER, "today")
    await api.report(OWNER, "monthly", date(2026, 10, 1))
    await api.get_settings(OWNER)
    await api.patch_settings(OWNER, {"version": 1})
    await api.list_recurring(OWNER)
    await api.recurring_record(OWNER, 2, idem_key="k")
    await api.recurring_skip(OWNER, 2)
    await api.list_debts(OWNER)
    await api.get_debt(OWNER, 5)
    await api.snooze_debt(OWNER, 5)
    await api.outbox_claim()
    await api.outbox_ack(1, ok=True, error=None, retry=False)
    await api.aclose()
    assert len(seen) == 25
    missing = [f"{m} {p}" for m, p in seen if not _exists(m, p)]
    assert not missing, f"bot calls routes the backend does not have: {missing}"


def test_callback_prefixes_are_known():
    backend_defined = {"u", "e", "a", "l", "c", "s", "p", "x", "t", "r", "k", "o",
                       "dr", "dra", "dz", "dv"}  # API.md §13 + DEBTS.md §9, §11
    bot_local = {"m", "n", "b", "y", "U", "M", "g", "i", "z"}
    from pocket_bot.handlers import _CALLBACKS

    assert set(_CALLBACKS) == backend_defined | bot_local
    assert cb.INBOX == "i" and cb.CANCEL_PROMPT == "z"


def test_debt_callbacks_fit_and_parse():
    big = 9_223_372_036_854_775_807  # BIGINT max — still ≤ 64 bytes
    for make, op in ((cb.debt_view, "dv"), (cb.debt_repay, "dr"), (cb.debt_repay_all, "dra"),
                     (cb.debt_snooze, "dz")):
        data = make(big)
        assert len(data.encode()) <= cb.MAX_LEN
        parsed = cb.parse(data)
        assert parsed is not None and parsed.op == op and parsed.int_arg(0) == big
    assert cb.debt_repay(5) == "dr:5" and cb.debt_repay_all(5) == "dra:5"
    assert cb.debt_snooze(5) == "dz:5" and cb.debt_view(5) == "dv:5"

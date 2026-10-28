"""OpenAI-compatible LLM client. Config: AI_BASE_URL, AI_API_KEY, AI_MODEL,
AI_TIMEOUT, AI_MAX_CONCURRENCY. Uses httpx, response_format json_schema of IntentList.

Never raises on provider problems: returns ``LLMResult(ok=False, status=timeout|unavailable|
error|invalid)``. Output is parsed strictly (IntentList Pydantic validation); one repair retry is
made with the validation error. ``response_format`` degrades json_schema → json_object → none
when the provider rejects it (remembered per base_url+model for the process lifetime).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import get_settings
from app.schemas.intents import IntentList, ParseContext
from app.services.ai import prompts

log = logging.getLogger("pocket.ai.llm")

MODES = ("json_schema", "json_object", "none")
_FORMAT_ERROR_RE = re.compile(r"response_format|json_schema|json_object|schema", re.I)


@dataclass
class LLMResult:
    ok: bool
    intents: IntentList | None = None
    status: str = "ok"  # ok | invalid | timeout | unavailable | error
    model: str | None = None
    error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    request_log: dict[str, Any] = field(default_factory=dict)  # sanitized, no secrets
    response_log: dict[str, Any] = field(default_factory=dict)
    #: the first answer failed validation and was repaired: its error and how many intents it
    #: had (a repaired answer with fewer intents silently dropped part of the message)
    repair_error: str | None = None
    first_attempt_intents: int | None = None


class _ProviderError(Exception):
    def __init__(self, status: str, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.http_status = http_status


def _strip_titles(node: Any) -> Any:
    """Schema for the provider: drop pydantic titles and the OpenAPI-only discriminator."""
    if isinstance(node, dict):
        return {
            k: _strip_titles(v) for k, v in node.items() if k not in ("title", "discriminator")
        }
    if isinstance(node, list):
        return [_strip_titles(x) for x in node]
    return node


_SCHEMA: dict[str, Any] | None = None


def intent_json_schema() -> dict[str, Any]:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = _strip_titles(IntentList.model_json_schema())
    return copy.deepcopy(_SCHEMA)


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def extract_json(content: str) -> Any:
    """JSON value from model text: plain, ```json fenced```, or embedded in prose.
    Floats become Decimal (money is never float)."""
    text = (content or "").strip()
    if not text:
        raise ValueError("пустой ответ модели")
    candidates = [text]
    candidates += [m.group(1).strip() for m in _FENCE_RE.finditer(text)]
    first, last = text.find("{"), text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first : last + 1])
    first, last = text.find("["), text.rfind("]")
    if 0 <= first < last:
        candidates.append(text[first : last + 1])
    err: Exception | None = None
    for c in candidates:
        try:
            return json.loads(c, parse_float=Decimal)
        except (json.JSONDecodeError, ValueError) as e:
            err = e
    raise ValueError(f"ответ модели не JSON: {err}")


def _drop_nulls(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _drop_nulls(v) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [_drop_nulls(x) for x in node]
    return node


def coerce_intents(data: Any) -> dict[str, Any]:
    """Lenient wrapping of common shapes before strict validation:
    list → {"intents": list}; single intent → wrapped; nulls dropped (defaults apply)."""
    if isinstance(data, list):
        data = {"intents": data}
    elif isinstance(data, dict) and "intents" not in data and "type" in data:
        data = {"intents": [data]}
    if not isinstance(data, dict):
        raise ValueError("ожидался JSON-объект {\"intents\": [...]}")
    return _drop_nulls(data)


def parse_intents(content: str) -> IntentList:
    """Strict: JSON → IntentList (Pydantic). Raises ValueError/ValidationError."""
    return IntentList.model_validate(coerce_intents(extract_json(content)))


def _content_of(body: dict[str, Any]) -> str:
    try:
        msg = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise _ProviderError("error", f"неожиданный ответ провайдера: {e!r}") from e
    content = msg.get("content")
    if isinstance(content, list):  # content parts
        content = "".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    if not content and isinstance(msg.get("parsed"), (dict, list)):
        return json.dumps(msg["parsed"], ensure_ascii=False)
    return content or ""


class LLMClient:
    """One process-wide instance; at most ``max_concurrency`` calls in flight."""

    _mode_cache: dict[str, str] = {}

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        max_concurrency: int | None = None,
        retries: int = 1,
        repair: bool = True,
        max_tokens: int = 1200,
        transport: httpx.AsyncBaseTransport | None = None,
        enabled: bool | None = None,
    ) -> None:
        s = get_settings()
        #: AI_ENABLED=false: the rule-based parser only — no network call, no prompt is built
        self.enabled = s.ai_enabled if enabled is None else enabled
        self.base_url = (base_url or s.ai_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else s.ai_api_key
        self.model = model or s.ai_model
        self.timeout = float(timeout if timeout is not None else s.ai_timeout)
        self.max_concurrency = max(1, int(max_concurrency or s.ai_max_concurrency))
        self.retries = max(0, retries)
        self.repair = repair
        self.max_tokens = max_tokens
        self._transport = transport
        self._sem: asyncio.Semaphore | None = None
        self._http: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ plumbing
    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.base_url and self.api_key and self.model)

    def _mode_key(self) -> str:
        return f"{self.base_url}|{self.model}"

    def _ensure(self) -> tuple[asyncio.Semaphore, httpx.AsyncClient]:
        loop = asyncio.get_running_loop()
        if self._loop is not loop or self._http is None or self._sem is None:
            self._loop = loop
            self._sem = asyncio.Semaphore(self.max_concurrency)
            self._http = httpx.AsyncClient(
                transport=self._transport,
                headers={"User-Agent": "pocket-finance/0.1"},
                limits=httpx.Limits(max_connections=self.max_concurrency * 2),
            )
        return self._sem, self._http

    async def aclose(self) -> None:
        if self._http is not None:
            try:
                await self._http.aclose()
            except RuntimeError:  # pragma: no cover - loop already closed
                pass
        self._http = None

    def _payload(self, messages: list[dict[str, str]], mode: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "intent_list", "strict": False,
                                "schema": intent_json_schema()},
            }
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        return body

    async def _post(self, body: dict[str, Any], budget: float) -> dict[str, Any]:
        _, http = self._ensure()
        try:
            r = await http.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=httpx.Timeout(budget, connect=min(5.0, budget)),
            )
        except httpx.TimeoutException as e:
            raise _ProviderError("timeout", f"таймаут провайдера: {type(e).__name__}") from e
        except httpx.HTTPError as e:
            raise _ProviderError("unavailable", f"сеть: {type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            snippet = r.text[:300]
            if r.status_code == 400 and _FORMAT_ERROR_RE.search(snippet):
                raise _ProviderError("format", snippet, http_status=400)
            down = r.status_code in (401, 403, 404, 429) or r.status_code >= 500
            raise _ProviderError("unavailable" if down else "error",
                                 f"HTTP {r.status_code}: {snippet}", http_status=r.status_code)
        try:
            data = r.json()
        except ValueError as e:
            raise _ProviderError("error", "провайдер вернул не JSON") from e
        if not isinstance(data, dict):
            raise _ProviderError("error", "провайдер вернул неожиданный JSON")
        if data.get("error") and not data.get("choices"):
            raise _ProviderError("unavailable", f"ошибка провайдера: {str(data['error'])[:300]}")
        return data

    # ------------------------------------------------------------------ main entry
    async def extract_intents(self, text: str, ctx: ParseContext) -> LLMResult:
        """One call (+1 retry with the validation error). Never raises on provider errors —
        returns ok=False with status timeout/unavailable/error."""
        res = LLMResult(ok=False, model=self.model)
        if not self.enabled:
            res.status = "unavailable"
            res.error = "AI выключен (AI_ENABLED=false): работает только разбор по правилам"
            return res
        messages = prompts.build_messages(text, ctx)
        res.request_log = {
            "model": self.model,
            "prompt_version": prompts.PROMPT_VERSION,
            "system_sha": prompts.system_prompt_digest(),
            "user_content": messages[1]["content"],
            "temperature": 0,
        }
        if not self.configured:
            res.status = "unavailable"
            res.error = "AI не настроен (AI_BASE_URL/AI_API_KEY/AI_MODEL)"
            return res
        t0 = time.monotonic()
        sem, _ = self._ensure()
        try:
            async with asyncio.timeout(self.timeout):
                async with sem:
                    await self._run(messages, res, t0)
        except TimeoutError:
            res.ok, res.intents = False, None
            res.status, res.error = "timeout", f"нет ответа за {self.timeout:g} с"
        except Exception as e:  # pragma: no cover - defensive: never raise to the pipeline
            log.exception("llm client crashed")
            res.ok, res.status, res.error = False, "error", repr(e)[:300]
        res.latency_ms = int((time.monotonic() - t0) * 1000)
        res.response_log["latency_ms"] = res.latency_ms
        return res

    async def _run(self, messages: list[dict[str, str]], res: LLMResult, t0: float) -> None:
        mode = self._mode_cache.get(self._mode_key(), "json_schema")
        attempts: list[dict[str, Any]] = []
        res.response_log = {"attempts": attempts}
        transport_retries = self.retries
        repaired = not self.repair
        convo = list(messages)
        while True:
            budget = self.timeout - (time.monotonic() - t0)
            if budget <= 0.2:
                raise TimeoutError
            att: dict[str, Any] = {"mode": mode}
            attempts.append(att)
            try:
                data = await self._post(self._payload(convo, mode), budget)
            except _ProviderError as e:
                att["error"] = str(e)[:300]
                att["status"] = e.status
                if e.status == "format" and mode != "none":
                    mode = MODES[MODES.index(mode) + 1]
                    self._mode_cache[self._mode_key()] = mode
                    continue
                if e.status in ("unavailable", "timeout", "error") and transport_retries > 0 and (
                    e.http_status is None or e.http_status == 429 or e.http_status >= 500
                ):
                    transport_retries -= 1
                    await asyncio.sleep(min(0.5, max(0.0, budget - 1)))
                    continue
                res.status = "timeout" if e.status == "timeout" else (
                    "error" if e.status in ("error", "format") else "unavailable")
                res.error = str(e)[:500]
                return
            usage = data.get("usage") or {}
            res.prompt_tokens = (res.prompt_tokens or 0) + int(usage.get("prompt_tokens") or 0)
            res.completion_tokens = (res.completion_tokens or 0) + int(
                usage.get("completion_tokens") or 0
            )
            try:
                content = _content_of(data)
            except _ProviderError as e:
                res.status, res.error = "error", str(e)
                return
            att["content"] = content[:4000]
            att["finish_reason"] = (data.get("choices") or [{}])[0].get("finish_reason")
            try:
                res.intents = parse_intents(content)
                res.ok, res.status, res.error = True, "ok", None
                return
            except (ValueError, ValidationError) as e:
                err = _short_validation_error(e)
                att["invalid"] = err
                if repaired:
                    res.ok, res.intents = False, None
                    res.status, res.error = "invalid", err
                    return
                repaired = True
                res.repair_error = err
                res.first_attempt_intents = _count_intents(content)
                convo = [*messages, {"role": "assistant", "content": content[:4000]},
                         prompts.repair_message(err)]


def _count_intents(content: str) -> int | None:
    try:
        data = coerce_intents(extract_json(content))
    except Exception:
        return None
    items = data.get("intents")
    return len(items) if isinstance(items, list) else None


def _short_validation_error(e: Exception) -> str:
    if isinstance(e, ValidationError):
        parts = []
        for er in e.errors()[:6]:
            loc = ".".join(str(x) for x in er.get("loc", ()))
            parts.append(f"{loc}: {er.get('msg')}")
        return "; ".join(parts)[:800]
    return str(e)[:800]


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Process-wide client (semaphore AI_MAX_CONCURRENCY). Tests override this."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def set_llm_client(client: LLMClient | None) -> None:
    """Replace the process-wide client (tests, bench, settings reload)."""
    global _client
    _client = client

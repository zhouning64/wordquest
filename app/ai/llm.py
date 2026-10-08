from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import httpx

from app.security import redact

# Delays before retry 1 and retry 2 of a transport error / HTTP 5xx (spec §7.2).
RETRY_DELAYS_S: tuple[float, ...] = (1.0, 2.0)
_SNIPPET_CHARS = 300


class LLMError(Exception):
    """Base class for every LLM failure. The message is always passed through redact()."""

    def __init__(self, message: str = "") -> None:
        super().__init__(redact(str(message)))


class TransientError(LLMError):
    """Transport errors / HTTP 5xx that persisted after the in-call retries."""


class InvalidOutput(LLMError):
    """Unparseable JSON, finish_reason == "length", or output that does not match the schema."""


class RateLimited(LLMError):
    """HTTP 429. retry_after is seconds (None if the server gave none); daily=True for a per-day quota."""

    def __init__(self, message: str, retry_after: float | None = None, daily: bool = False) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.daily = daily


class DailyCapReached(LLMError):
    """Raised by an on_request callback when AI_DAILY_CALL_LIMIT has been reached."""


@dataclass
class LLMResult:
    data: dict
    usage: dict
    finish_reason: str


class LLMClient(Protocol):
    async def chat_json(self, *, name: str, schema: dict, system: str, user: str) -> LLMResult:
        ...


async def _sleep(seconds: float) -> None:
    """Module-level wrapper so tests can monkeypatch the retry delay away."""
    await asyncio.sleep(seconds)


def _snippet(text: str) -> str:
    # Redact the whole text before collapsing and cutting it: a key that straddles the cut would leave a prefix
    # behind, because redact() only matches the complete secret.
    text = " ".join(redact(str(text)).split())
    if len(text) > _SNIPPET_CHARS:
        return text[:_SNIPPET_CHARS] + "…"
    return text


def parse_retry_after(raw: str | None) -> float | None:
    """Seconds from a Retry-After header. Missing, unparseable or non-finite (inf, -inf, nan) gives None (no
    hint); a negative value gives 0.0. The job worker computes now + retry_after, so nothing else may pass."""
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def _rate_limited(resp: httpx.Response) -> RateLimited:
    retry_after = parse_retry_after(resp.headers.get("retry-after"))
    body = resp.text
    daily = resp.headers.get("x-ratelimit-remaining-requests-day") == "0" or "day" in body.lower()
    return RateLimited(f"HTTP 429 rate limited: {_snippet(body)}", retry_after=retry_after, daily=daily)


def _parse_completion(resp: httpx.Response) -> LLMResult:
    try:
        payload: Any = resp.json()
        choice = payload["choices"][0]
        finish_reason = str(choice.get("finish_reason") or "")
        content = choice["message"]["content"]
        usage = payload.get("usage") or {}
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise InvalidOutput(f"malformed completion response: {_snippet(resp.text)}") from None
    if finish_reason == "length":
        raise InvalidOutput("model output was cut off (finish_reason=length)")
    if not isinstance(content, str) or not content.strip():
        raise InvalidOutput("model returned no content")
    try:
        data = json.loads(content)
    except ValueError:
        raise InvalidOutput(f"model output is not valid JSON: {_snippet(content)}") from None
    if not isinstance(data, dict):
        raise InvalidOutput("model output is not a JSON object")
    return LLMResult(data=data, usage=dict(usage) if isinstance(usage, dict) else {}, finish_reason=finish_reason)


class CerebrasClient:
    """OpenAI-compatible chat client for Cerebras with strict json_schema output."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        max_completion_tokens: int,
        timeout_s: float,
        on_request: Callable[[], None] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._max_completion_tokens = max_completion_tokens
        self._on_request = on_request
        self._client = httpx.AsyncClient(timeout=timeout_s, transport=transport)

    async def chat_json(self, *, name: str, schema: dict, system: str, user: str) -> LLMResult:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_completion_tokens": self._max_completion_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        attempts = len(RETRY_DELAYS_S) + 1
        last_error = ""
        for attempt in range(attempts):
            if attempt > 0:
                await _sleep(RETRY_DELAYS_S[attempt - 1])
            if self._on_request is not None:
                # Counts every outbound request (a synchronous Repository write) and may raise DailyCapReached.
                # Run it in a worker thread so the blocking SQLite call never stalls the event loop.
                await asyncio.to_thread(self._on_request)
            try:
                resp = await self._client.post(self._url, json=body, headers=headers)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}: {_snippet(resp.text)}"
                continue
            if resp.status_code == 429:
                raise _rate_limited(resp)
            if resp.status_code >= 400:
                raise LLMError(f"HTTP {resp.status_code}: {_snippet(resp.text)}")
            return _parse_completion(resp)
        raise TransientError(f"LLM request failed after {attempts} attempts: {last_error}")

    async def aclose(self) -> None:
        await self._client.aclose()

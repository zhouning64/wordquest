from __future__ import annotations

import json
import threading

import httpx
import pytest

from app.ai import llm
from app.ai.llm import (
    CerebrasClient,
    DailyCapReached,
    InvalidOutput,
    LLMError,
    LLMResult,
    RateLimited,
    TransientError,
)
from app.security import register_secrets

API_KEY = "csk-test-SENTINEL-9f8e7d6c5b4a"
BASE_URL = "https://api.cerebras.test/v1"
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Replace the retry sleep with a recorder so tests run instantly."""
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(llm, "_sleep", fake_sleep)
    return delays


def ok_response(content, *, finish_reason: str = "stop", usage: dict | None = None) -> httpx.Response:
    if not isinstance(content, str):
        content = json.dumps(content)
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
            ],
            "usage": usage if usage is not None else {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        },
    )


class Recorder:
    """httpx.MockTransport handler that replays scripted responses and records requests."""

    def __init__(self, *steps) -> None:
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def make_client(handler, on_request=None) -> CerebrasClient:
    return CerebrasClient(
        api_key=API_KEY,
        model="gpt-oss-120b",
        base_url=BASE_URL,
        max_completion_tokens=16000,
        timeout_s=5.0,
        on_request=on_request,
        transport=httpx.MockTransport(handler),
    )


async def call(handler, on_request=None) -> LLMResult:
    client = make_client(handler, on_request)
    try:
        return await client.chat_json(name="learn_card", schema=SCHEMA, system="SYSTEM TEXT", user="USER TEXT")
    finally:
        await client.aclose()


class Counter:
    def __init__(self) -> None:
        self.n = 0
        self.threads: list[int] = []

    def __call__(self) -> None:
        self.n += 1
        self.threads.append(threading.get_ident())


async def test_success_returns_parsed_result(sleeps):
    handler = Recorder(ok_response({"answer": "frugal"}, usage={"prompt_tokens": 11, "completion_tokens": 22}))
    result = await call(handler)
    assert isinstance(result, LLMResult)
    assert result.data == {"answer": "frugal"}
    assert result.usage == {"prompt_tokens": 11, "completion_tokens": 22}
    assert result.finish_reason == "stop"
    assert sleeps == []


async def test_request_shape_and_auth_header():
    handler = Recorder(ok_response({"answer": "x"}))
    await call(handler)
    assert len(handler.requests) == 1
    req = handler.requests[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api.cerebras.test/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {API_KEY}"
    body = json.loads(req.content)
    assert body["model"] == "gpt-oss-120b"
    assert body["messages"] == [
        {"role": "system", "content": "SYSTEM TEXT"},
        {"role": "user", "content": "USER TEXT"},
    ]
    assert body["max_completion_tokens"] == 16000
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "learn_card", "strict": True, "schema": SCHEMA},
    }
    assert "reasoning_effort" not in body  # the model default unless a call asks for more


async def test_reasoning_effort_is_sent_only_when_given():
    handler = Recorder(ok_response({"answer": "x"}), ok_response({"answer": "y"}), ok_response({"answer": "z"}))
    client = make_client(handler)
    try:
        await client.chat_json(name="answer_check", schema=SCHEMA, system="s", user="u", reasoning_effort="high")
        await client.chat_json(name="answer_check", schema=SCHEMA, system="s", user="u", reasoning_effort=None)
        await client.chat_json(name="answer_check", schema=SCHEMA, system="s", user="u")
    finally:
        await client.aclose()
    bodies = [json.loads(req.content) for req in handler.requests]
    assert bodies[0]["reasoning_effort"] == "high"
    assert "reasoning_effort" not in bodies[1] and "reasoning_effort" not in bodies[2]
    assert {k: v for k, v in bodies[0].items() if k != "reasoning_effort"} == bodies[1] == bodies[2]


async def test_base_url_trailing_slash_is_tolerated():
    handler = Recorder(ok_response({"answer": "x"}))
    client = CerebrasClient(
        api_key=API_KEY, model="m", base_url=BASE_URL + "/", max_completion_tokens=10, timeout_s=5.0,
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.chat_json(name="n", schema=SCHEMA, system="s", user="u")
    finally:
        await client.aclose()
    assert str(handler.requests[0].url) == "https://api.cerebras.test/v1/chat/completions"
    assert client.model == "m"


async def test_on_request_called_once_per_successful_call_off_the_event_loop():
    counter = Counter()
    await call(Recorder(ok_response({"answer": "x"})), on_request=counter)
    assert counter.n == 1
    # The production hook is a blocking SQLite write, so it must not run on the event-loop thread.
    assert counter.threads[0] != threading.get_ident()


async def test_500_then_200_retries_once(sleeps):
    counter = Counter()
    handler = Recorder(httpx.Response(500, text="upstream exploded"), ok_response({"answer": "ok"}))
    result = await call(handler, on_request=counter)
    assert result.data == {"answer": "ok"}
    assert len(handler.requests) == 2
    assert counter.n == 2
    assert sleeps == [1.0]


async def test_connect_error_twice_then_success(sleeps):
    counter = Counter()
    handler = Recorder(
        httpx.ConnectError("connection refused"),
        httpx.ConnectError("connection refused"),
        ok_response({"answer": "ok"}),
    )
    result = await call(handler, on_request=counter)
    assert result.data == {"answer": "ok"}
    assert counter.n == 3
    assert sleeps == [1.0, 2.0]


async def test_three_transport_errors_raise_transient(sleeps):
    counter = Counter()
    handler = Recorder(
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectError("connection refused"),
    )
    with pytest.raises(TransientError) as ei:
        await call(handler, on_request=counter)
    assert counter.n == 3
    assert len(handler.requests) == 3
    assert sleeps == [1.0, 2.0]
    assert "ConnectError" in str(ei.value)


async def test_three_5xx_raise_transient(sleeps):
    handler = Recorder(httpx.Response(502), httpx.Response(503), httpx.Response(500, text="still down"))
    with pytest.raises(TransientError) as ei:
        await call(handler)
    assert "HTTP 500" in str(ei.value)
    assert sleeps == [1.0, 2.0]


async def test_429_with_retry_after_is_rate_limited(sleeps):
    counter = Counter()
    handler = Recorder(
        httpx.Response(429, headers={"retry-after": "7"}, json={"message": "Too many requests, slow down"})
    )
    with pytest.raises(RateLimited) as ei:
        await call(handler, on_request=counter)
    assert ei.value.retry_after == 7.0
    assert ei.value.daily is False
    assert counter.n == 1
    assert sleeps == []


async def test_429_without_retry_after():
    handler = Recorder(httpx.Response(429, json={"message": "Too many requests"}))
    with pytest.raises(RateLimited) as ei:
        await call(handler)
    assert ei.value.retry_after is None
    assert ei.value.daily is False


async def test_429_daily_quota_header():
    handler = Recorder(
        httpx.Response(
            429,
            headers={"retry-after": "3600", "x-ratelimit-remaining-requests-day": "0"},
            json={"message": "Too many requests"},
        )
    )
    with pytest.raises(RateLimited) as ei:
        await call(handler)
    assert ei.value.daily is True
    assert ei.value.retry_after == 3600.0


async def test_429_daily_quota_body_text():
    handler = Recorder(httpx.Response(429, json={"message": "Requests per DAY limit exceeded"}))
    with pytest.raises(RateLimited) as ei:
        await call(handler)
    assert ei.value.daily is True


async def test_400_is_llm_error_without_retry(sleeps):
    counter = Counter()
    handler = Recorder(httpx.Response(400, json={"message": "bad request: unknown field"}))
    with pytest.raises(LLMError) as ei:
        await call(handler, on_request=counter)
    assert not isinstance(ei.value, (RateLimited, TransientError, InvalidOutput))
    assert "HTTP 400" in str(ei.value)
    assert counter.n == 1
    assert sleeps == []


async def test_finish_reason_length_is_invalid_output():
    handler = Recorder(ok_response('{"answer": "fru', finish_reason="length"))
    with pytest.raises(InvalidOutput) as ei:
        await call(handler)
    assert "length" in str(ei.value)


async def test_non_json_content_is_invalid_output():
    handler = Recorder(ok_response("Sure! Here is your card: frugal means careful."))
    with pytest.raises(InvalidOutput):
        await call(handler)


async def test_json_array_content_is_invalid_output():
    handler = Recorder(ok_response("[1, 2, 3]"))
    with pytest.raises(InvalidOutput):
        await call(handler)


async def test_malformed_envelope_is_invalid_output():
    handler = Recorder(httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(InvalidOutput):
        await call(handler)


async def test_error_body_echoing_key_is_redacted():
    register_secrets([API_KEY])
    handler = Recorder(httpx.Response(401, json={"error": f"Invalid API key {API_KEY} for {BASE_URL}"}))
    with pytest.raises(LLMError) as ei:
        await call(handler)
    message = str(ei.value)
    assert "[REDACTED]" in message
    assert API_KEY not in message


async def test_transport_error_echoing_key_is_redacted():
    register_secrets([API_KEY])
    leak = httpx.ConnectError(f"cannot connect to https://x.test/?key={API_KEY}")
    handler = Recorder(leak, leak, leak)
    with pytest.raises(TransientError) as ei:
        await call(handler)
    assert API_KEY not in str(ei.value)
    assert "[REDACTED]" in str(ei.value)


async def test_on_request_daily_cap_propagates_without_http():
    handler = Recorder(ok_response({"answer": "x"}))

    def capped() -> None:
        raise DailyCapReached("daily AI call limit reached")

    with pytest.raises(DailyCapReached):
        await call(handler, on_request=capped)
    assert handler.requests == []


@pytest.mark.parametrize("status", [400, 429])
async def test_error_body_is_redacted_before_it_is_cut_to_300_chars(status):
    """The key starts at character 290 of the body. Cutting first would keep its first 10 characters, which
    redact() can no longer match. The whole body is redacted before the cut."""
    register_secrets([API_KEY])
    handler = Recorder(httpx.Response(status, text="x" * 290 + API_KEY))
    with pytest.raises(LLMError) as ei:
        await call(handler)
    message = str(ei.value)
    assert API_KEY[:8] not in message
    assert "[REDACTED]" in message


@pytest.mark.parametrize("raw, expected", [("inf", None), ("-inf", None), ("nan", None), ("-5", 0.0)])
async def test_429_retry_after_is_a_finite_non_negative_number(raw, expected):
    handler = Recorder(httpx.Response(429, headers={"retry-after": raw}, json={"message": "slow down"}))
    with pytest.raises(RateLimited) as ei:
        await call(handler)
    assert ei.value.retry_after == expected

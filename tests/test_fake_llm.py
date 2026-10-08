from __future__ import annotations

import pytest

from app.ai.llm import InvalidOutput, LLMResult, RateLimited
from tests.fakes import FakeLLM


async def ask(fake: FakeLLM, name: str, user: str = "U") -> LLMResult:
    return await fake.chat_json(name=name, schema={"type": "object"}, system="S", user=user)


async def test_dict_items_are_returned_in_order():
    fake = FakeLLM({"learn_card": [{"n": 1}, {"n": 2}]})
    first = await ask(fake, "learn_card")
    second = await ask(fake, "learn_card")
    assert isinstance(first, LLMResult)
    assert first.data == {"n": 1}
    assert second.data == {"n": 2}
    assert first.usage == {"completion_tokens": 10}
    assert first.finish_reason == "stop"


async def test_exception_items_are_raised():
    fake = FakeLLM({"question_batch": [RateLimited("slow down", retry_after=7.0), InvalidOutput]})
    with pytest.raises(RateLimited) as ei:
        await ask(fake, "question_batch")
    assert ei.value.retry_after == 7.0
    with pytest.raises(InvalidOutput):
        await ask(fake, "question_batch")


async def test_callable_items_receive_system_and_user():
    seen = []

    def respond(system: str, user: str) -> dict:
        seen.append((system, user))
        return {"echo": user}

    fake = FakeLLM({"answer_check": [respond]})
    result = await ask(fake, "answer_check", user="hello")
    assert result.data == {"echo": "hello"}
    assert seen == [("S", "hello")]


async def test_exhausted_or_unknown_name_raises_assertion():
    fake = FakeLLM({"learn_card": [{"n": 1}]})
    await ask(fake, "learn_card")
    with pytest.raises(AssertionError):
        await ask(fake, "learn_card")
    with pytest.raises(AssertionError):
        await ask(fake, "question_batch")


async def test_calls_are_recorded_and_add_appends():
    fake = FakeLLM({})
    fake.add("learn_card", {"a": 1})
    await ask(fake, "learn_card", user="word: frugal")
    assert fake.calls == [{"name": "learn_card", "system": "S", "user": "word: frugal"}]
    assert fake.calls_for("learn_card") == fake.calls
    assert fake.calls_for("answer_check") == []


async def test_returned_data_is_a_copy():
    item = {"list": [1]}
    fake = FakeLLM({"x": [item]})
    result = await ask(fake, "x")
    result.data["list"].append(2)
    assert item == {"list": [1]}

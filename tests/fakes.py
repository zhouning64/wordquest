
"""In-memory test doubles shared by the test suite.

Task 5 creates this file with MemoryBlobStore; Task 8 adds FakeLLM and Task 12 adds FakeImageProvider.
"""
from __future__ import annotations
import copy
from typing import Callable, Union

from app.ai.llm import LLMResult

from app.storage.base import BlobStore


class MemoryBlobStore(BlobStore):
    """BlobStore that keeps bytes in a dict; url_for(key) == "/media/" + key."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.data[key] = bytes(data)
        self.content_types[key] = content_type

    def url_for(self, key: str) -> str:
        return "/media/" + key

    def exists(self, key: str) -> bool:
        return key in self.data

    def delete(self, key: str) -> None:
        self.data.pop(key, None)
        self.content_types.pop(key, None)

    def get(self, key: str) -> bytes:
        """Test helper: the stored bytes (KeyError if missing)."""
        return self.data[key]

ScriptItem = Union[dict, BaseException, Callable[[str, str], dict]]


class FakeLLM:
    """Scripted stand-in for CerebrasClient (plan Appendix A §9).

    script maps a schema name (e.g. "learn_card") to the responses for successive calls with that name:
    a dict is returned as LLMResult.data, an exception (instance or class) is raised, and a callable is
    called with (system, user) and must return the dict. Every call is recorded in .calls.
    """

    model = "fake-llm"

    def __init__(self, script: dict[str, list[ScriptItem]] | None = None) -> None:
        self.script: dict[str, list[ScriptItem]] = {name: list(items) for name, items in (script or {}).items()}
        self.calls: list[dict] = []

    def add(self, name: str, *items: ScriptItem) -> None:
        """Append more scripted responses for a schema name."""
        self.script.setdefault(name, []).extend(items)

    def calls_for(self, name: str) -> list[dict]:
        return [c for c in self.calls if c["name"] == name]

    async def chat_json(self, *, name: str, schema: dict, system: str, user: str) -> LLMResult:
        self.calls.append({"name": name, "system": system, "user": user})
        queue = self.script.get(name)
        if not queue:
            raise AssertionError(f"FakeLLM: no scripted response left for schema {name!r}")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item("scripted failure")
        if callable(item):
            item = item(system, user)
        if not isinstance(item, dict):
            raise AssertionError(f"FakeLLM: scripted response for {name!r} must be a dict, got {type(item).__name__}")
        return LLMResult(data=copy.deepcopy(item), usage={"completion_tokens": 10}, finish_reason="stop")

# ---- Task 12: fake image provider -------------------------------------------
import io  # noqa: E402

from PIL import Image  # noqa: E402

from app.ai.images.base import ImageProvider  # noqa: E402


class FakeImageProvider(ImageProvider):
    """Returns a 32x32 PNG. The first `fail_times` calls raise RuntimeError.
    Every prompt received (including failed calls) is recorded in `prompts`."""

    name = "fake"

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.prompts: list[str] = []

    async def generate(self, prompt: str) -> bytes:
        self.prompts.append(prompt)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("fake image provider failure")
        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (124, 92, 255)).save(buf, format="PNG")
        return buf.getvalue()

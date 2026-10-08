
"""In-memory test doubles shared by the test suite.

Task 5 creates this file with MemoryBlobStore; Task 8 adds FakeLLM and Task 12 adds FakeImageProvider.
"""
from __future__ import annotations

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

from __future__ import annotations

from abc import ABC, abstractmethod


class ImageProvider(ABC):
    """Turns a text prompt into raw image bytes (any format Pillow can open)."""

    name: str = "base"

    @abstractmethod
    async def generate(self, prompt: str) -> bytes:
        """Return image bytes for the prompt. Raise on failure."""

    async def aclose(self) -> None:
        """Release network resources. Default: nothing to release."""
        return None

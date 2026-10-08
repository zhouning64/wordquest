from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
from typing import Callable

import httpx

from app.ai.images.base import ImageProvider
from app.ai.llm import LLMError, RateLimited, TransientError
from app.security import redact

DEFAULT_SIZE = "1024x1024"  # valid for both z.ai models (glm-image and cogview-4-250304)
MAX_IMAGE_MB = 20
MAX_IMAGE_BYTES = MAX_IMAGE_MB * 1024 * 1024  # larger downloaded or decoded pictures are refused


class OpenAICompatibleImageProvider(ImageProvider):
    """POST {base_url}/images/generations in the OpenAI Images shape (WordQuest's chosen provider is z.ai).

    The JSON body is {"model", "prompt", "size"} plus "quality" only when one is configured. No "n" (z.ai does
    not document it; OpenAI and Together default to one picture) and no "response_format" (OpenAI's gpt-image
    models reject it). data[0].b64_json is decoded when present; otherwise data[0].url is downloaded at once
    with the same HTTP client (z.ai answers with a link that expires after 30 days). Other response fields
    (z.ai's "created" and "content_filter") are ignored. Pictures larger than MAX_IMAGE_BYTES are refused.

    Errors use the llm.py exception family so the worker treats them like LLM errors:
    429 -> RateLimited, 5xx / transport -> TransientError, other failures -> LLMError.
    No retries inside one attempt (the job worker retries). All messages pass through redact().
    """

    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        size: str = DEFAULT_SIZE,
        quality: str = "",
        timeout_s: float = 120.0,
        on_request: Callable[[], None] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.size = size.strip()
        self.quality = quality.strip()  # "" = not sent, so the model's own default applies
        self._api_key = api_key
        self._on_request = on_request
        self._client = httpx.AsyncClient(timeout=timeout_s, transport=transport)

    async def generate(self, prompt: str) -> bytes:
        if self._on_request is not None:
            # Counts the request (a synchronous Repository write) and may raise DailyCapReached;
            # run it off the event loop.
            await asyncio.to_thread(self._on_request)
        body = {"model": self.model, "prompt": prompt, "size": self.size}
        if self.quality:
            body["quality"] = self.quality
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            resp = await self._client.post(
                f"{self.base_url}/images/generations", json=body, headers=headers
            )
        except httpx.HTTPError as e:
            raise TransientError(redact(f"image request failed: {type(e).__name__}: {e}")) from None
        _raise_for_status(resp, "image provider")
        try:
            item = resp.json()["data"][0]
            b64 = item.get("b64_json")
            url = item.get("url")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            raise LLMError(redact(f"image provider returned an unexpected response: {e}")) from None
        if b64:
            try:
                data = base64.b64decode(b64, validate=True)
            except (ValueError, TypeError, binascii.Error) as e:
                raise LLMError(redact(f"image provider returned invalid base64: {e}")) from None
            if len(data) > MAX_IMAGE_BYTES:
                raise LLMError(f"image provider returned a picture larger than {MAX_IMAGE_MB} MB")
            return data
        if url:
            return await self._download(url)
        raise LLMError("image provider returned neither b64_json nor url")

    async def _download(self, url: object) -> bytes:
        """GET a returned image URL right away (z.ai links expire after 30 days).

        Not counted by on_request (it is part of the same picture) and sent without the Authorization header
        (the URL is pre-signed and may point at another host). Redirects are followed (CDN and pre-signed links
        often answer 302 first). The body is streamed: a Content-Length over the cap is refused before reading,
        and reading stops as soon as the body passes MAX_IMAGE_BYTES."""
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise LLMError("image provider returned an unusable image url")
        too_large = f"image download is larger than {MAX_IMAGE_MB} MB"
        try:
            async with self._client.stream("GET", url, follow_redirects=True) as resp:
                if not resp.is_success:
                    head = await _read_at_most(resp, 300)
                    _raise_for_status(resp, "image download", head.decode("utf-8", errors="replace"))
                    # still not 2xx, e.g. a 3xx without a usable Location
                    raise LLMError(redact(f"image download HTTP {resp.status_code}"))
                declared = resp.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > MAX_IMAGE_BYTES:
                    raise LLMError(too_large)
                data = await _read_at_most(resp, MAX_IMAGE_BYTES + 1)
        except httpx.HTTPError as e:
            raise TransientError(redact(f"image download failed: {type(e).__name__}: {e}")) from None
        if len(data) > MAX_IMAGE_BYTES:
            raise LLMError(too_large)
        if not data:
            raise LLMError("image download returned no data")
        return data

    async def aclose(self) -> None:
        await self._client.aclose()


async def _read_at_most(resp: httpx.Response, limit: int) -> bytes:
    """Read a streamed body, stopping once `limit` bytes have arrived (the rest is never downloaded)."""
    buf = bytearray()
    async with contextlib.aclosing(resp.aiter_bytes()) as chunks:
        async for chunk in chunks:
            buf += chunk
            if len(buf) >= limit:
                break
    return bytes(buf[:limit])


def _raise_for_status(resp: httpx.Response, what: str, text: str | None = None) -> None:
    """Raise the llm.py exception for an HTTP error status; `text` (default: the body) goes into the message."""
    if resp.status_code < 400:
        return
    detail = (resp.text if text is None else text)[:300]
    if resp.status_code == 429:
        retry_after: float | None
        try:
            retry_after = float(resp.headers.get("retry-after", ""))
        except ValueError:
            retry_after = None
        raise RateLimited(redact(f"{what} rate limited: {detail}"), retry_after=retry_after)
    if resp.status_code >= 500:
        raise TransientError(redact(f"{what} HTTP {resp.status_code}: {detail}"))
    raise LLMError(redact(f"{what} HTTP {resp.status_code}: {detail}"))

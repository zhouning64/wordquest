from __future__ import annotations

import base64
import io
import json
import threading

import httpx
import pytest
from PIL import Image

import app.ai.images as images_pkg
from app.ai.images import make_image_provider
from app.ai.images.base import ImageProvider
from app.ai.images.openai_compatible import MAX_IMAGE_BYTES, OpenAICompatibleImageProvider
from app.ai.images.process import STYLE_PREAMBLE, build_image_prompt, image_key, to_webp
from app.ai.llm import DailyCapReached, LLMError, RateLimited, TransientError
from app.config import Settings
from app.security import register_secrets
from tests.fakes import FakeImageProvider


def png_bytes(size: tuple[int, int], mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    color = (200, 100, 50, 128) if mode == "RGBA" else (200, 100, 50)
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


def open_img(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


# ---------------------------------------------------------------- process.py


def test_to_webp_shrinks_wide_image_preserving_aspect():
    out = to_webp(png_bytes((1600, 800)))
    im = open_img(out)
    assert im.format == "WEBP"
    assert im.size == (768, 384)


def test_to_webp_shrinks_tall_image():
    im = open_img(to_webp(png_bytes((600, 1200))))
    assert im.format == "WEBP"
    assert im.size == (384, 768)


def test_to_webp_does_not_upscale_small_images():
    im = open_img(to_webp(png_bytes((100, 50))))
    assert im.format == "WEBP"
    assert im.size == (100, 50)


def test_to_webp_custom_max_edge_and_alpha():
    im = open_img(to_webp(png_bytes((1000, 1000), mode="RGBA"), max_edge=256))
    assert im.format == "WEBP"
    assert im.size == (256, 256)
    assert im.mode == "RGBA"


def test_to_webp_accepts_palette_and_jpeg_input():
    buf = io.BytesIO()
    Image.new("P", (900, 300)).save(buf, format="GIF")
    assert open_img(to_webp(buf.getvalue())).size == (768, 256)
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (1, 2, 3)).save(buf, format="JPEG")
    assert open_img(to_webp(buf.getvalue())).format == "WEBP"


def test_to_webp_rejects_non_images():
    with pytest.raises(OSError):
        to_webp(b"not an image")


def test_image_key_slug():
    assert image_key("6-8", "in lieu of", 2) == "images/6-8/in-lieu-of-v2.webp"
    assert image_key("3-5", "frugal", 1) == "images/3-5/frugal-v1.webp"
    assert image_key("9-12", "well-being", 10) == "images/9-12/well-being-v10.webp"


def test_build_image_prompt():
    prompt = build_image_prompt("  a fox   reading a book\nunder a tree ")
    assert prompt.startswith(STYLE_PREAMBLE)
    assert "a fox reading a book under a tree" in prompt
    for phrase in ("flat cartoon", "no text or letters", "kid-safe"):
        assert phrase in STYLE_PREAMBLE


def test_image_prompt_asks_for_children_of_varied_appearance():
    # Every checkpoint picture showed the same brown-haired boy: each prompt asks for variety, without stereotypes.
    prompt = build_image_prompt("a kid saving coins in a jar")
    sentence = next(s for s in prompt.split(". ") if "children" in s.lower())
    for phrase in ("varied", "skin tone", "hair", "girls and boys", "stereotype"):
        assert phrase in sentence, phrase


# ---------------------------------------------------------------- FakeImageProvider


async def test_fake_image_provider_fails_then_returns_png():
    fake = FakeImageProvider(fail_times=2)
    assert isinstance(fake, ImageProvider)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await fake.generate("p")
    data = await fake.generate("p3")
    im = open_img(data)
    assert im.format == "PNG" and im.size == (32, 32)
    assert fake.prompts == ["p", "p", "p3"]


async def test_base_provider_aclose_is_a_no_op():
    class MinimalProvider(ImageProvider):
        async def generate(self, prompt: str) -> bytes:
            return b"picture"

    p = MinimalProvider()
    assert p.name == "base"
    assert await p.aclose() is None
    assert await p.aclose() is None  # safe to call more than once
    assert await p.generate("x") == b"picture"  # nothing was released
    assert await FakeImageProvider().aclose() is None  # the fake inherits the no-op


# ---------------------------------------------------------------- OpenAICompatibleImageProvider


def provider(handler, *, key: str = "sk-img-test-1234", on_request=None) -> OpenAICompatibleImageProvider:
    return OpenAICompatibleImageProvider(
        api_key=key,
        model="img-model",
        base_url="https://img.example/v1/",
        on_request=on_request,
        transport=httpx.MockTransport(handler),
    )


async def test_provider_request_shape_and_decoding():
    png = png_bytes((64, 64))
    seen: list[httpx.Request] = []
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png).decode()}]})

    p = provider(handler, on_request=lambda: calls.append(threading.get_ident()))
    try:
        data = await p.generate("a fox reading")
    finally:
        await p.aclose()
    assert data == png
    assert len(calls) == 1
    assert calls[0] != threading.get_ident()  # the hook (a blocking SQLite write) runs off the event loop
    assert len(seen) == 1
    req = seen[0]
    assert req.method == "POST"
    assert str(req.url) == "https://img.example/v1/images/generations"
    assert req.headers["authorization"] == "Bearer sk-img-test-1234"
    body = json.loads(req.content)
    assert body == {"model": "img-model", "prompt": "a fox reading", "size": "1024x1024"}  # exactly these keys
    assert "n" not in body  # not in z.ai's API; OpenAI and Together default to one picture
    assert "response_format" not in body  # gpt-image models reject it
    assert p.name == "openai_compatible"


async def test_provider_handles_a_zai_style_url_response():
    """z.ai (the chosen provider) answers {"created", "data": [{"url"}], "content_filter"}. The link expires
    after 30 days, so the picture is downloaded at once, without the API key."""
    png = png_bytes((40, 40))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={
                "created": 1791331200,
                "data": [{"url": "https://files.example/zai/20261007/abc123.png"}],
                "content_filter": [{"role": "assistant", "level": 3}],
            })
        return httpx.Response(200, content=png, headers={"content-type": "image/png"})

    p = OpenAICompatibleImageProvider(
        api_key="zai-key-0123456789",
        model="glm-image",
        base_url="https://api.z.ai/api/paas/v4",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await p.generate("a fox reading") == png
    finally:
        await p.aclose()
    assert [(r.method, str(r.url)) for r in seen] == [
        ("POST", "https://api.z.ai/api/paas/v4/images/generations"),
        ("GET", "https://files.example/zai/20261007/abc123.png"),
    ]
    assert seen[0].headers["authorization"] == "Bearer zai-key-0123456789"
    assert json.loads(seen[0].content) == {"model": "glm-image", "prompt": "a fox reading", "size": "1024x1024"}
    assert "authorization" not in seen[1].headers


@pytest.mark.parametrize("size, quality, extra", [
    ("1024x1024", "", {}),                       # empty quality is not sent: the model's default applies
    ("1280x1280", "hd", {"quality": "hd"}),
    (" 1024x1024 ", " standard ", {"quality": "standard"}),
    ("768x1344", "   ", {}),                     # whitespace-only quality counts as empty
])
async def test_provider_sends_size_and_only_a_non_empty_quality(size, quality, extra):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png_bytes((8, 8))).decode()}]})

    p = OpenAICompatibleImageProvider(
        api_key="k-123456",
        model="cogview-4-250304",
        base_url="https://img.example/v1",
        size=size,
        quality=quality,
        transport=httpx.MockTransport(handler),
    )
    try:
        await p.generate("x")
    finally:
        await p.aclose()
    assert seen == [{"model": "cogview-4-250304", "prompt": "x", "size": size.strip(), **extra}]
    assert (p.size, p.quality) == (size.strip(), quality.strip())


async def test_provider_downloads_url_responses_with_the_same_client():
    png = png_bytes((48, 48))
    seen: list[httpx.Request] = []
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/out/fox.png?sig=abc"}]})
        return httpx.Response(200, content=png, headers={"content-type": "image/png"})

    p = provider(handler, on_request=lambda: calls.append("counted"))
    try:
        data = await p.generate("a fox reading")
    finally:
        await p.aclose()
    assert data == png
    assert calls == ["counted"]  # the download is not a second AI request
    assert [(r.method, str(r.url)) for r in seen] == [
        ("POST", "https://img.example/v1/images/generations"),
        ("GET", "https://cdn.example/out/fox.png?sig=abc"),
    ]
    assert "authorization" not in seen[1].headers  # the API key never goes to the download host
    assert "response_format" not in json.loads(seen[0].content)


async def test_provider_download_follows_redirects():
    png = png_bytes((24, 24))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/out/fox.png"}]})
        if request.url.host == "cdn.example":
            return httpx.Response(302, headers={"location": "https://storage.example/signed/fox.png?sig=1"})
        return httpx.Response(200, content=png, headers={"content-type": "image/png"})

    p = provider(handler)
    try:
        assert await p.generate("x") == png
    finally:
        await p.aclose()
    assert [(r.method, str(r.url)) for r in seen] == [
        ("POST", "https://img.example/v1/images/generations"),
        ("GET", "https://cdn.example/out/fox.png"),
        ("GET", "https://storage.example/signed/fox.png?sig=1"),
    ]
    assert all("authorization" not in r.headers for r in seen[1:])


async def test_provider_download_without_a_usable_redirect_is_llm_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/out/fox.png"}]})
        return httpx.Response(304, text="<html>not modified</html>")  # 3xx without a Location: nothing to follow

    p = provider(handler)
    try:
        with pytest.raises(LLMError, match="image download HTTP 304"):
            await p.generate("x")
    finally:
        await p.aclose()


async def test_provider_prefers_b64_json_when_both_are_present():
    png = png_bytes((16, 16))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png).decode(),
                                                   "url": "https://cdn.example/unused.png"}]})

    p = provider(handler)
    try:
        assert await p.generate("x") == png
    finally:
        await p.aclose()
    assert seen == ["POST"]


@pytest.mark.parametrize("status, error", [(404, LLMError), (503, TransientError), (429, RateLimited)])
async def test_provider_url_download_errors(status, error):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/out/fox.png"}]})
        return httpx.Response(status, text="download problem")

    p = provider(handler)
    try:
        with pytest.raises(error):
            await p.generate("x")
    finally:
        await p.aclose()


@pytest.mark.parametrize("size, accepted", [(MAX_IMAGE_BYTES, True), (MAX_IMAGE_BYTES + 1, False)])
async def test_provider_download_cap_is_20_mb(size, accepted):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/out/big.png"}]})
        return httpx.Response(200, content=b"\0" * size)  # httpx sets Content-Length from the content

    p = provider(handler)
    try:
        if accepted:
            assert len(await p.generate("x")) == MAX_IMAGE_BYTES
        else:
            with pytest.raises(LLMError, match="larger than 20 MB"):
                await p.generate("x")
    finally:
        await p.aclose()


async def test_provider_refuses_a_declared_oversize_download_without_reading_it():
    reads: list[int] = []

    async def small_body():
        reads.append(1)
        yield b"tiny"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/out/huge.png"}]})
        return httpx.Response(200, headers={"content-length": str(MAX_IMAGE_BYTES + 1)}, content=small_body())

    p = provider(handler)
    try:
        with pytest.raises(LLMError, match="larger than 20 MB"):
            await p.generate("x")
    finally:
        await p.aclose()
    assert reads == []  # refused on the Content-Length header alone


async def test_provider_stops_reading_a_download_once_it_passes_20_mb():
    assert MAX_IMAGE_BYTES == 20 * 1024 * 1024
    sent: list[int] = []

    async def endless_body():  # 40 MB on offer, streamed without a Content-Length
        for i in range(40):
            sent.append(i)
            yield b"\0" * (1024 * 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/out/endless.png"}]})
        return httpx.Response(200, content=endless_body())

    p = provider(handler)
    try:
        with pytest.raises(LLMError, match="larger than 20 MB"):
            await p.generate("x")
    finally:
        await p.aclose()
    assert len(sent) == 21  # stopped right after passing the cap instead of reading all 40 MB


async def test_provider_refuses_base64_pictures_over_20_mb():
    big = base64.b64encode(b"\0" * (MAX_IMAGE_BYTES + 1)).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"b64_json": big}]})

    p = provider(handler)
    try:
        with pytest.raises(LLMError, match="larger than 20 MB"):
            await p.generate("x")
    finally:
        await p.aclose()


async def test_provider_http_error_is_redacted():
    key = "sk-img-sentinel-9f8e7d6c"
    register_secrets([key])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"internal error for key {key} at {request.url}")

    p = provider(handler, key=key)
    try:
        with pytest.raises(TransientError) as exc:
            await p.generate("x")
    finally:
        await p.aclose()
    msg = str(exc.value)
    assert key not in msg
    assert "[REDACTED]" in msg
    assert "500" in msg


async def test_provider_4xx_is_llm_error_and_redacted():
    key = "sk-img-sentinel-4xx-1a2b"
    register_secrets([key])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": f"invalid key {key}"})

    p = provider(handler, key=key)
    try:
        with pytest.raises(LLMError) as exc:
            await p.generate("x")
    finally:
        await p.aclose()
    assert not isinstance(exc.value, (TransientError, RateLimited))
    assert key not in str(exc.value)


async def test_provider_429_is_rate_limited_with_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "12"}, text="slow down")

    p = provider(handler)
    try:
        with pytest.raises(RateLimited) as exc:
            await p.generate("x")
    finally:
        await p.aclose()
    assert exc.value.retry_after == 12.0


async def test_provider_transport_error_is_transient():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    p = provider(handler)
    try:
        with pytest.raises(TransientError):
            await p.generate("x")
    finally:
        await p.aclose()


@pytest.mark.parametrize("payload", [
    {"data": []},
    {"data": [{}]},  # neither b64_json nor url
    {"data": [{"b64_json": None, "url": None}]},
    {"data": [{"b64_json": "%%% not base64 %%%"}]},
    {"data": [{"url": "file:///etc/passwd"}]},
    {"error": "no data key"},
])
async def test_provider_bad_payload_is_llm_error(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            raise AssertionError("no download for a malformed answer")
        return httpx.Response(200, json=payload)

    p = provider(handler)
    try:
        with pytest.raises(LLMError):
            await p.generate("x")
    finally:
        await p.aclose()


async def test_on_request_can_block_the_call():
    hits: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": ""}]})

    def cap() -> None:
        raise DailyCapReached("daily cap")

    p = provider(handler, on_request=cap)
    try:
        with pytest.raises(DailyCapReached):
            await p.generate("x")
    finally:
        await p.aclose()
    assert hits == []


STRADDLE_KEY = "sk-img-straddle-7c3d9e1f2a"


@pytest.mark.parametrize("status, error", [(400, LLMError), (500, TransientError), (429, RateLimited)])
async def test_error_body_is_redacted_before_it_is_cut_to_300_chars(status, error):
    """The key starts at character 290 of the body. Cutting first would keep its first 10 characters, which
    redact() (an exact-substring replace) can no longer match. The whole body is redacted before the cut."""
    register_secrets([STRADDLE_KEY])
    body = "x" * 290 + STRADDLE_KEY

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    p = provider(handler, key=STRADDLE_KEY)
    try:
        with pytest.raises(error) as exc:
            await p.generate("x")
    finally:
        await p.aclose()
    assert STRADDLE_KEY[:8] not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)


async def test_download_error_body_is_redacted_before_it_is_cut_to_300_chars():
    register_secrets([STRADDLE_KEY])
    body = "x" * 290 + STRADDLE_KEY

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/out/fox.png"}]})
        return httpx.Response(404, text=body)

    p = provider(handler, key=STRADDLE_KEY)
    try:
        with pytest.raises(LLMError) as exc:
            await p.generate("x")
    finally:
        await p.aclose()
    assert STRADDLE_KEY[:8] not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)


@pytest.mark.parametrize("raw, expected", [("inf", None), ("-inf", None), ("nan", None), ("-5", 0.0)])
async def test_provider_429_retry_after_is_a_finite_non_negative_number(raw, expected):
    """inf, -inf and nan are no hint (None); a negative value counts as 0. The worker computes now + retry_after."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": raw}, text="slow down")

    p = provider(handler)
    try:
        with pytest.raises(RateLimited) as exc:
            await p.generate("x")
    finally:
        await p.aclose()
    assert exc.value.retry_after == expected


# ---------------------------------------------------------------- make_image_provider


@pytest.mark.parametrize("value", ["none", " None\t", "", "   "])  # whitespace-only means none too
def test_make_image_provider_none(value):
    assert make_image_provider(Settings(_env_file=None, image_provider=value)) is None


async def test_make_image_provider_openai_compatible():
    def hook() -> None:
        return None

    s = Settings(
        _env_file=None,
        image_provider="openai_compatible",
        image_api_key="sk-x-0000",
        image_model="glm-image",
        image_base_url="https://api.z.ai/api/paas/v4",
        image_size="1280x1280",
        image_quality="hd",
    )
    p = make_image_provider(s, on_request=hook)
    assert isinstance(p, OpenAICompatibleImageProvider)
    assert p.name == "openai_compatible"
    assert p.model == "glm-image"
    assert p.base_url == "https://api.z.ai/api/paas/v4"
    assert (p.size, p.quality) == ("1280x1280", "hd")
    assert p._on_request is hook
    await p.aclose()


async def test_make_image_provider_strips_settings_and_defaults_size_and_quality():
    s = Settings(
        _env_file=None,
        image_provider="OpenAI_Compatible",
        image_api_key="sk-x-0000",
        image_model=" glm-image ",
        image_base_url=" https://api.z.ai/api/paas/v4/ ",
    )
    p = make_image_provider(s)
    assert isinstance(p, OpenAICompatibleImageProvider)
    assert (p.base_url, p.model) == ("https://api.z.ai/api/paas/v4", "glm-image")
    assert (p.size, p.quality) == ("1024x1024", "")
    await p.aclose()


BASE_URL_REQUIRED = ("IMAGE_BASE_URL is required when IMAGE_PROVIDER=openai_compatible "
                     "(for z.ai use https://api.z.ai/api/paas/v4)")
API_KEY_REQUIRED = ("IMAGE_API_KEY is required when IMAGE_PROVIDER=openai_compatible "
                    "(create a z.ai key at https://z.ai/manage-apikey/apikey-list and put it in .env)")
MODEL_REQUIRED = ("IMAGE_MODEL is required when IMAGE_PROVIDER=openai_compatible "
                  "(for z.ai use glm-image or cogview-4-250304)")


@pytest.mark.parametrize("field, message", [
    ("image_base_url", BASE_URL_REQUIRED),
    ("image_api_key", API_KEY_REQUIRED),
    ("image_model", MODEL_REQUIRED),
])
def test_make_image_provider_requires_base_url_key_and_model(field, message):
    values = {"image_base_url": "https://api.z.ai/api/paas/v4", "image_api_key": "zai-key-0000", "image_model": "glm-image"}
    values[field] = "   "  # blank counts as missing
    with pytest.raises(ValueError) as exc:
        make_image_provider(Settings(_env_file=None, image_provider="openai_compatible", **values))
    assert str(exc.value) == message


def test_make_image_provider_names_every_missing_setting_and_has_no_openai_default():
    with pytest.raises(ValueError) as exc:
        make_image_provider(Settings(_env_file=None, image_provider="openai_compatible"))
    assert str(exc.value).split("; ") == [BASE_URL_REQUIRED, API_KEY_REQUIRED, MODEL_REQUIRED]
    assert not hasattr(images_pkg, "DEFAULT_OPENAI_BASE_URL")  # a forgotten base URL never means api.openai.com


def test_make_image_provider_unknown_raises():
    with pytest.raises(ValueError):
        make_image_provider(Settings(_env_file=None, image_provider="bogus"))

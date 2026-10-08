from __future__ import annotations

import io

from PIL import Image, ImageOps

STYLE_PREAMBLE = (
    "Friendly flat cartoon illustration, consistent soft palette, simple background, "
    "no text or letters, kid-safe."
)


def build_image_prompt(scene: str) -> str:
    scene = " ".join(scene.split())
    return f"{STYLE_PREAMBLE} Scene: {scene}"


def to_webp(data: bytes, max_edge: int = 768) -> bytes:
    """Decode any Pillow-readable image, shrink so the long edge is <= max_edge
    (never upscale), and re-encode as WebP."""
    with Image.open(io.BytesIO(data)) as src:
        src.load()
        im = ImageOps.exif_transpose(src)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
    elif im.mode != "RGB":
        im = im.convert("RGB")
    w, h = im.size
    long_edge = max(w, h)
    if long_edge > max_edge:
        scale = max_edge / long_edge
        im = im.resize(
            (max(1, round(w * scale)), max(1, round(h * scale))),
            Image.Resampling.LANCZOS,
        )
    out = io.BytesIO()
    im.save(out, format="WEBP", quality=82, method=4)
    return out.getvalue()


def _slug(word: str) -> str:
    return "-".join(word.split())


def image_key(band: str, word: str, version: int) -> str:
    return f"images/{band}/{_slug(word)}-v{version}.webp"

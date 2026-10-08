from __future__ import annotations

# One-off: draws the Add-to-Home-Screen icons (indigo→violet gradient tile with a white "W").
# Run once from the repo root and commit the PNGs:
#     .venv/bin/python scripts/make_icons.py

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "web" / "icons"
SIZES = (192, 512)
TOP = (99, 102, 241)      # #6366f1 (legacy hero start)
BOTTOM = (139, 92, 246)   # #8b5cf6 (legacy hero middle)
WHITE = (255, 255, 255)
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
)


def _font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, px)
        except OSError:
            continue
    return ImageFont.load_default(size=px)


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def make_icon(size: int) -> Image.Image:
    """Full-bleed square (safe for "maskable"): vertical gradient plus a centered white W."""
    img = Image.new("RGB", (size, size), TOP)
    draw = ImageDraw.Draw(img)
    for y in range(size):
        draw.line([(0, y), (size - 1, y)], fill=_lerp(TOP, BOTTOM, y / (size - 1)))
    font = _font(int(size * 0.56))
    left, top, right, bottom = draw.textbbox((0, 0), "W", font=font)
    x = (size - (right - left)) / 2 - left
    y = (size - (bottom - top)) / 2 - top
    draw.text((x, y), "W", font=font, fill=WHITE)
    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        path = OUT_DIR / f"icon-{size}.png"
        make_icon(size).save(path, format="PNG", optimize=True)
        print(f"wrote {path.relative_to(ROOT)} ({size}x{size})")


if __name__ == "__main__":
    main()

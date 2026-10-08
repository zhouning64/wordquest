from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

LEGACY_CLASSES = (
    "wrap hero card btn btn-primary btn-secondary btn-ghost btn-opt correct wrong btn-learn chips chip topbar "
    "timer pbar qtype qprompt blank feedback typebox learn-img learn-word learn-pos learn-def parts ex synchips "
    "syn stars stat-grid stat toast eyebreak tile-fallback"
).split()
NEW_CLASSES = (
    "profile-grid profile-tile avatar pname gate gate-msg notice parent-link speak optkey qhead intro-card "
    "learn-head kid-def sense hook hl use-right use-wrong ant banner-answer banner-missed check-view check-head "
    "giveup results-card results-list badge table-wrap data row-actions field form-grid toolbar tabs tab "
    "queue-banner emoji-tile btn-sub btn-inline wide error-card"
).split()


def _load_make_icons():
    spec = importlib.util.spec_from_file_location("make_icons", ROOT / "scripts" / "make_icons.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _near(a: tuple[int, ...], b: tuple[int, ...], tol: int = 12) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_make_icon_draws_gradient_tile_with_white_w():
    mod = _load_make_icons()
    img = mod.make_icon(192)
    assert img.size == (192, 192)
    assert img.mode == "RGB"
    assert _near(img.getpixel((0, 0)), (99, 102, 241))
    assert _near(img.getpixel((0, 191)), (139, 92, 246))
    middle = [img.getpixel((x, y)) for x in range(40, 152) for y in range(60, 132)]
    assert sum(1 for p in middle if p == (255, 255, 255)) > 300, "a white W is drawn in the middle"


def test_committed_icons_exist_with_exact_sizes():
    for size in (192, 512):
        with Image.open(WEB / "icons" / f"icon-{size}.png") as img:
            assert img.format == "PNG"
            assert img.size == (size, size)


def test_manifest_is_valid_for_add_to_home_screen():
    manifest = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["name"] == "WordQuest"
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    assert manifest["theme_color"] == "#6366f1"
    sizes = {icon["sizes"]: icon["src"] for icon in manifest["icons"]}
    assert sizes == {"192x192": "/static/icons/icon-192.png", "512x512": "/static/icons/icon-512.png"}


def test_index_html_is_the_single_page_shell():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert '<main id="app"' in html
    assert '<script type="module" src="/static/js/app.js"></script>' in html
    assert 'href="/static/css/app.css"' in html
    assert 'href="/static/manifest.webmanifest"' in html
    assert 'id="toast"' in html


def test_css_keeps_every_legacy_class_and_defines_the_new_ones():
    css = (WEB / "css" / "app.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
    missing = [c for c in LEGACY_CLASSES + NEW_CLASSES if c not in defined]
    assert missing == []
    assert "@media (min-width: 768px)" in css


def _css_rule(css: str, selector: str) -> str:
    """The declarations of the first rule whose whole selector list is `selector`."""
    m = re.search(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.MULTILINE)
    assert m, f"no CSS rule for {selector!r}"
    return m.group(1)


def _px(declarations: str, prop: str) -> int:
    m = re.search(rf"(?:^|;)\s*{prop}\s*:\s*(\d+)px", declarations)
    assert m, f"{prop} is not set in {declarations.strip()!r}"
    return int(m.group(1))


def test_tap_targets_are_at_least_44px():  # spec §10
    css = (WEB / "css" / "app.css").read_text(encoding="utf-8")
    assert _px(_css_rule(css, ".btn, .btn-opt, .speak, .profile-tile, .typebox button, .typebox input"), "min-height") >= 44
    assert _px(_css_rule(css, ".speak"), "min-width") >= 44
    example_speak = _css_rule(css, ".ex .speak")  # the 🔊 next to every example sentence on the Learn page
    assert _px(example_speak, "min-height") >= 44 and _px(example_speak, "min-width") >= 44
    assert _px(_css_rule(css, "a.parent-link"), "min-height") >= 44
    assert _px(_css_rule(css, ".row-actions .btn"), "min-height") >= 44
    assert _px(_css_rule(css, ".tab"), "min-height") >= 44


def test_wide_pages_grow_to_1100px_on_desktop():
    # Unconditional .wrap.wide rules are the mobile-first base: one placed after the breakpoints would override them.
    css = (WEB / "css" / "app.css").read_text(encoding="utf-8")
    desktop = css.index("@media (min-width: 1024px)")
    assert "max-width: 1100px" in css[desktop:css.index("}", css.index(".wrap.wide", desktop))]
    later = [m.start() for m in re.finditer(r"^\.wrap\.wide\s*\{", css, re.MULTILINE) if m.start() > desktop]
    assert later == [], "a top-level .wrap.wide rule after the 1024px breakpoint overrides its 1100px width"

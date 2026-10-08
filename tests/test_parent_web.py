from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
PARENT_DIR = ROOT / "web" / "js" / "parent"
SCREENS = ["login", "profiles", "lists", "preview", "stats", "backup"]
SUPPORT = ["helpers", "shell"]

# A template-literal interpolation that is nothing but a model/text field, e.g. `${q.prompt}`, is unescaped HTML.
RAW_FIELD = re.compile(
    r"\$\{\s*[A-Za-z_][A-Za-z0-9_]*\.(?:prompt|explanation|short_def|kid_def|error|image_error|image_config_error"
    r"|entry|reason|word|choices|examples|model)\s*\}"
)


@pytest.fixture()
def client(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    app = create_app(settings, start_worker=False, seed=False)
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("name", SCREENS + SUPPORT)
def test_parent_module_is_served_as_javascript(client, name):
    r = client.get(f"/static/js/parent/{name}.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]


@pytest.mark.parametrize("name", SCREENS)
def test_each_parent_screen_exports_render(name):
    src = (PARENT_DIR / f"{name}.js").read_text(encoding="utf-8")
    assert re.search(r"^export async function render\(root, ctx\)", src, re.MULTILINE)


def test_app_router_loads_every_parent_screen():
    src = (ROOT / "web" / "js" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"PARENT_SCREENS\s*=\s*\[([^\]]*)\]", src)
    assert m, "web/js/app.js must define PARENT_SCREENS"
    assert re.findall(r'"([a-z]+)"', m.group(1)) == SCREENS
    assert re.search(r"import\(`\./parent/\$\{\w+\}\.js`\)", src)


def test_preview_reuses_the_learner_learn_renderer_read_only():
    src = (PARENT_DIR / "preview.js").read_text(encoding="utf-8")
    assert 'import { render as renderLearn } from "../screens/learn.js";' in src
    assert "readOnly: true" in src
    assert "canCheck: false" in src


@pytest.mark.parametrize("name", SCREENS + SUPPORT)
def test_no_raw_model_text_interpolation(name):
    src = (PARENT_DIR / f"{name}.js").read_text(encoding="utf-8")
    assert RAW_FIELD.findall(src) == []


def test_parent_styles_are_appended_to_app_css():
    css = (ROOT / "web" / "css" / "app.css").read_text(encoding="utf-8")
    for selector in (".pwrap", ".ptabs", ".pbadge.is-ready", ".pbanner.warn", ".pq li.correct", ".pbtn-danger",
                     ".preview-learn > button", ".pimgerr"):
        assert selector in css

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import clock
from app.ai.content import ContentGenerator
from app.ai.images.base import ImageProvider
from app.ai.llm import DailyCapReached
from app.auth import Auth
from app.config import Settings
from app.jobs import Worker
from app.main import LEGACY_PATH, OpenAccessWarning, create_app
from app.models import WordList
from app.security import redact
from app.storage.local_blobs import LocalBlobStore
from app.storage.sqlite_repo import SqliteRepository
from tests.fakes import FakeLLM

SITE_CODE = "app-test-site-code"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        cerebras_api_key="",
        image_provider="none",
        site_access_code=SITE_CODE,
        parent_passcode="app-test-parent-code",
        secret_key="app-test-secret-key",
        data_dir=tmp_path / "data",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def injected(tmp_path: Path) -> dict:
    return {"repo": SqliteRepository(tmp_path / "wq.db"), "blobs": LocalBlobStore(tmp_path / "data")}


def login_site(c: TestClient) -> None:
    assert c.post("/api/auth/site", json={"code": SITE_CODE}).status_code == 200


class StubImageProvider(ImageProvider):
    name = "stub"

    async def generate(self, prompt: str) -> bytes:
        return b""


class Closings:
    """Records close()/aclose() calls of the fakes below, in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def repo(self, path: Path) -> SqliteRepository:
        calls = self.calls

        class ClosingRepo(SqliteRepository):
            def close(self) -> None:
                calls.append("repo")
                super().close()

        return ClosingRepo(path)

    def llm(self) -> object:
        calls = self.calls

        class ClosingLLM:
            model = "closing-model"

            async def chat_json(self, *, name: str, schema: dict, system: str, user: str):
                raise AssertionError("no model call in this test")

            async def aclose(self) -> None:
                calls.append("llm")

        return ClosingLLM()

    def images(self) -> ImageProvider:
        calls = self.calls

        class ClosingImages(ImageProvider):
            name = "closing"

            async def generate(self, prompt: str) -> bytes:
                return b""

            async def aclose(self) -> None:
                calls.append("images")

        return ClosingImages()


def test_create_app_has_no_filesystem_side_effects(tmp_path):
    settings = make_settings(tmp_path, secret_key="")
    create_app(settings, start_worker=False, seed=False)
    assert not settings.data_dir.exists()


def test_root_serves_the_page_shell(tmp_path):
    with TestClient(create_app(make_settings(tmp_path), **injected(tmp_path), start_worker=False, seed=False)) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "WordQuest" in r.text
        assert 'id="app"' in r.text
        assert c.get("/static/index.html").status_code == 200
        assert c.get("/static/nope.js").status_code == 404


def test_lifespan_populates_app_state(tmp_path):
    settings = make_settings(tmp_path)
    deps = injected(tmp_path)
    app = create_app(settings, **deps, start_worker=False, seed=False)
    with TestClient(app):
        assert app.state.settings is settings
        assert app.state.repo is deps["repo"]
        assert app.state.blobs is deps["blobs"]
        assert isinstance(app.state.auth, Auth)
        assert app.state.generator is None  # no key and no injected llm
        assert app.state.image_provider is None  # IMAGE_PROVIDER=none
        assert app.state.image_config_error == ""  # "none" is a valid choice, not a broken setting
        assert isinstance(app.state.worker, Worker)
        assert (settings.data_dir / "images").is_dir()
        assert (settings.data_dir / "logs").is_dir()


def test_lifespan_builds_default_storage_and_secret_key(tmp_path):
    settings = make_settings(tmp_path, secret_key="")
    app = create_app(settings, start_worker=False, seed=False)
    with TestClient(app):
        assert isinstance(app.state.repo, SqliteRepository)
        assert isinstance(app.state.blobs, LocalBlobStore)
        assert (settings.data_dir / "secret_key").is_file()


def test_injected_llm_and_image_provider_are_used(tmp_path):
    provider = StubImageProvider()
    app = create_app(
        make_settings(tmp_path),
        **injected(tmp_path),
        llm=FakeLLM({}),
        image_provider=provider,
        start_worker=False,
        seed=False,
    )
    with TestClient(app):
        assert isinstance(app.state.generator, ContentGenerator)
        assert app.state.image_provider is provider


@pytest.mark.parametrize(
    "overrides, error",
    [
        ({"image_provider": "openai_compatible", "image_api_key": "img-key-5566-secret", "image_model": "glm-image"},
         "IMAGE_BASE_URL is required when IMAGE_PROVIDER=openai_compatible (for z.ai use https://api.z.ai/api/paas/v4)"),
        # A secret pasted into the wrong setting is redacted in the stored message and in the log.
        ({"image_provider": SITE_CODE}, "Unknown IMAGE_PROVIDER '[REDACTED]' (use 'none' or 'openai_compatible')"),
    ],
)
def test_bad_image_settings_start_the_app_with_pictures_off(tmp_path, caplog, overrides, error):
    app = create_app(make_settings(tmp_path, **overrides), **injected(tmp_path), start_worker=False, seed=False)
    with caplog.at_level(logging.ERROR, logger="wordquest"):
        with TestClient(app) as c:
            assert c.get("/").status_code == 200  # the app still starts and serves
            assert app.state.image_provider is None  # pictures off: image jobs finish with emoji scenes
            assert app.state.worker.image_provider is None
            assert app.state.image_config_error == error
    logged = [r.getMessage() for r in caplog.records if r.name == "wordquest" and r.levelno == logging.ERROR]
    assert len(logged) == 1 and logged[0].endswith(error)
    assert SITE_CODE not in logged[0]


def test_ai_clients_share_the_workers_daily_call_limit_hook(tmp_path, monkeypatch):
    captured: dict = {}

    class StubCerebras:
        def __init__(self, **kwargs) -> None:
            captured["llm_kwargs"] = kwargs
            self.model = kwargs["model"]

        async def chat_json(self, *, name: str, schema: dict, system: str, user: str):
            raise AssertionError("no model call in this test")

        async def aclose(self) -> None:
            captured["closed"] = True

    def stub_image_provider(settings, on_request=None):
        captured["image_on_request"] = on_request
        return None

    monkeypatch.setattr(main_module, "CerebrasClient", StubCerebras)
    monkeypatch.setattr(main_module, "make_image_provider", stub_image_provider)
    deps = injected(tmp_path)
    app = create_app(
        make_settings(tmp_path, cerebras_api_key="csk-app-test-key", ai_daily_call_limit=2),
        **deps, start_worker=False, seed=False,
    )
    with TestClient(app):
        llm_hook = captured["llm_kwargs"]["on_request"]
        image_hook = captured["image_on_request"]
        llm_hook()
        image_hook()
        with pytest.raises(DailyCapReached):
            llm_hook()  # the 3rd outbound AI request today exceeds AI_DAILY_CALL_LIMIT=2
        assert deps["repo"].get_ai_calls(clock.utc_date()) == 3
        assert isinstance(app.state.generator, ContentGenerator)
        assert app.state.worker.generator is app.state.generator
    assert captured["closed"] is True  # the app closes the client it created


def test_secrets_are_registered_for_redaction(tmp_path):
    with TestClient(create_app(make_settings(tmp_path), **injected(tmp_path), start_worker=False, seed=False)):
        redacted = redact(f"the code was {SITE_CODE}!")
        assert SITE_CODE not in redacted
        assert "[REDACTED]" in redacted


def test_generated_secret_key_is_registered_for_redaction(tmp_path):
    settings = make_settings(tmp_path, secret_key="")  # the key is generated into DATA_DIR/secret_key
    with TestClient(create_app(settings, **injected(tmp_path), start_worker=False, seed=False)):
        generated = (settings.data_dir / "secret_key").read_text(encoding="utf-8").strip()
        assert len(generated) >= 32
        assert generated not in settings.secret_values()  # so the lifespan must register it itself
        redacted = redact(f"cookie key {generated} leaked")
        assert generated not in redacted
        assert redacted == "cookie key [REDACTED] leaked"


def test_shutdown_closes_what_the_app_opened(tmp_path, monkeypatch):
    closings = Closings()
    monkeypatch.setattr(main_module, "make_storage",
                        lambda settings: (closings.repo(tmp_path / "own.db"), LocalBlobStore(settings.data_dir)))
    monkeypatch.setattr(main_module, "CerebrasClient", lambda **kwargs: closings.llm())
    monkeypatch.setattr(main_module, "make_image_provider", lambda settings, on_request=None: closings.images())
    app = create_app(make_settings(tmp_path, cerebras_api_key="csk-close-test-key"), start_worker=True, seed=False)
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        assert closings.calls == []
    assert sorted(closings.calls) == ["images", "llm", "repo"]
    assert closings.calls[-1] == "repo"  # the database closes last, after the worker and the AI clients


def test_shutdown_leaves_injected_dependencies_open(tmp_path):
    closings = Closings()
    repo = closings.repo(tmp_path / "wq.db")
    app = create_app(make_settings(tmp_path), repo=repo, blobs=LocalBlobStore(tmp_path / "data"),
                     llm=closings.llm(), image_provider=closings.images(), start_worker=False, seed=False)
    with TestClient(app):
        pass
    assert closings.calls == []  # the caller owns injected objects
    assert repo.list_lists() == []  # and the injected repository still works


@pytest.mark.parametrize("start_worker, expected", [(True, ["started", "stopped"]), (False, [])])
def test_worker_task_follows_start_worker(tmp_path, monkeypatch, start_worker, expected):
    events: list[str] = []

    async def fake_run(self, stop: asyncio.Event) -> None:
        events.append("started")
        await stop.wait()
        events.append("stopped")

    monkeypatch.setattr(Worker, "run", fake_run)
    app = create_app(make_settings(tmp_path), **injected(tmp_path), start_worker=start_worker, seed=False)
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
    assert events == expected


def test_seed_runs_only_on_first_start(tmp_path, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(main_module, "seed_legacy", lambda repo, blobs, path: calls.append((repo, blobs, path)) or 24)
    deps = injected(tmp_path)

    with TestClient(create_app(make_settings(tmp_path), **deps, start_worker=False, seed=False)):
        pass
    assert calls == []

    with TestClient(create_app(make_settings(tmp_path), **deps, start_worker=False, seed=True)):
        pass
    assert calls == [(deps["repo"], deps["blobs"], LEGACY_PATH)]

    deps["repo"].save_list(WordList(id="l1", name="Mine", words=["candid"]))
    with TestClient(create_app(make_settings(tmp_path), **deps, start_worker=False, seed=True)):
        pass
    assert len(calls) == 1


def test_media_serves_blobs_from_the_local_store(tmp_path):
    deps = injected(tmp_path)
    deps["blobs"].put("images/6-8/candid-v1.webp", b"RIFF0000WEBPVP8 ", "image/webp")
    deps["blobs"].put("images/legacy/zenith.PNG", b"\x89PNG-bytes", "image/png")
    with TestClient(create_app(make_settings(tmp_path), **deps, start_worker=False, seed=False)) as c:
        login_site(c)
        r = c.get(deps["blobs"].url_for("images/6-8/candid-v1.webp"))
        assert r.status_code == 200
        assert r.content == b"RIFF0000WEBPVP8 "
        assert r.headers["content-type"] == "image/webp"
        r = c.get("/media/images/legacy/zenith.PNG")
        assert (r.status_code, r.headers["content-type"]) == (200, "image/png")
        assert c.get("/media/images/6-8/missing-v1.webp").status_code == 404


def test_media_requires_the_site_cookie_when_a_code_is_set(tmp_path):
    deps = injected(tmp_path)
    deps["blobs"].put("images/6-8/candid-v1.webp", b"RIFF0000WEBPVP8 ", "image/webp")
    with TestClient(create_app(make_settings(tmp_path), **deps, start_worker=False, seed=False)) as c:
        r = c.get("/media/images/6-8/candid-v1.webp")
        assert r.status_code == 401
        assert r.json() == {"detail": "access_code_required"}
        login_site(c)  # a same-origin <img> request carries this cookie automatically
        assert c.get("/media/images/6-8/candid-v1.webp").status_code == 200
    open_app = create_app(make_settings(tmp_path, site_access_code=""), **injected(tmp_path),
                          start_worker=False, seed=False)
    with TestClient(open_app) as c:  # no access code: no cookie needed
        assert c.get("/media/images/6-8/candid-v1.webp").status_code == 200


@pytest.mark.parametrize(
    "key",
    ["secret_key", "wordquest.db", "logs/ai-usage.jsonl", "images/6-8/notes.txt", "backups/candid-v1.webp"],
)
def test_media_serves_nothing_but_images(tmp_path, key):
    target = tmp_path / "data" / key  # LocalBlobStore root == DATA_DIR
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("private: not a picture")
    with TestClient(create_app(make_settings(tmp_path), **injected(tmp_path), start_worker=False, seed=False)) as c:
        login_site(c)
        r = c.get(f"/media/{key}")
        assert r.status_code == 404
        assert "private" not in r.text


@pytest.mark.parametrize(
    "path",
    [
        "/media/%2e%2e/secret.txt",
        "/media/images/%2e%2e/%2e%2e/secret.txt",
        "/media/images/..%2f..%2fsecret.txt",
        "/media/images/%2e%2e/secret_key",
        "/media/images/6-8/%2e%2e/%2e%2e/secret_key",
        "/media/images/..%2fsecret_key",
        # An image suffix passes the images/ + suffix filter, so only the traversal guards stop these.
        "/media/images/%2e%2e/%2e%2e/outside.png",
        "/media/images/..%2f..%2foutside.png",
        "/media/images/6-8/%2e%2e/%2e%2e/%2e%2e/outside.png",
        # Dot and empty segments are refused even when the path would stay inside images/.
        "/media/images/6-8/%2e%2e/inside.webp",
        "/media/images/%2e/inside.webp",
        "/media/images//inside.webp",
    ],
)
def test_media_rejects_path_traversal(tmp_path, path):
    (tmp_path / "secret.txt").write_text("top secret")
    (tmp_path / "outside.png").write_bytes(b"top secret picture")  # outside DATA_DIR
    (tmp_path / "data" / "images").mkdir(parents=True)
    (tmp_path / "data" / "secret_key").write_text("top secret key")
    (tmp_path / "data" / "images" / "inside.webp").write_bytes(b"top secret inside")
    with TestClient(create_app(make_settings(tmp_path), **injected(tmp_path), start_worker=False, seed=False)) as c:
        login_site(c)
        r = c.get(path)
        assert r.status_code == 404
        assert "top secret" not in r.text
        assert c.get("/media/images/inside.webp").status_code == 200  # the plain key works


def test_media_refuses_symlinks_that_leave_the_image_folder_or_loop(tmp_path):
    (tmp_path / "secret.png").write_bytes(b"top secret picture")
    folder = tmp_path / "data" / "images" / "6-8"
    folder.mkdir(parents=True)
    try:
        (folder / "escape-v1.webp").symlink_to(tmp_path / "secret.png")
        (folder / "loop-v1.webp").symlink_to(folder / "loop-v1.webp")
    except OSError:  # e.g. Windows without the symlink privilege
        pytest.skip("cannot create symlinks here")
    with TestClient(create_app(make_settings(tmp_path), **injected(tmp_path), start_worker=False, seed=False)) as c:
        login_site(c)
        for key in ("images/6-8/escape-v1.webp", "images/6-8/loop-v1.webp"):
            r = c.get(f"/media/{key}")
            assert r.status_code == 404, key
            assert "top secret" not in r.text


@pytest.mark.parametrize(
    "path",
    [
        "/media/images/6-8/" + "b" * 300 + ".webp",  # one segment longer than 255 bytes (ENAMETOOLONG)
        "/media/images/" + "/".join(["d" * 250] * 20) + ".webp",  # the whole path longer than PATH_MAX
    ],
)
def test_media_answers_404_for_names_the_filesystem_refuses(tmp_path, path):
    deps = injected(tmp_path)
    deps["blobs"].put("images/6-8/candid-v1.webp", b"RIFF0000WEBPVP8 ", "image/webp")  # images/6-8/ exists
    with TestClient(create_app(make_settings(tmp_path), **deps, start_worker=False, seed=False)) as c:
        login_site(c)
        r = c.get(path)
        assert r.status_code == 404
        assert r.json() == {"detail": "not_found"}


def test_warns_once_when_open_to_non_loopback_clients(tmp_path, caplog):
    app = create_app(make_settings(tmp_path, site_access_code=""), **injected(tmp_path), start_worker=False, seed=False)
    with caplog.at_level(logging.WARNING, logger="wordquest"):
        with TestClient(app) as c:  # the TestClient peer address is "testclient", not loopback
            c.get("/")
            c.get("/")
    warnings = [r for r in caplog.records if "SITE_ACCESS_CODE" in r.getMessage()]
    assert len(warnings) == 1


def test_no_open_access_warning_when_code_is_set(tmp_path, caplog):
    app = create_app(make_settings(tmp_path), **injected(tmp_path), start_worker=False, seed=False)
    with caplog.at_level(logging.WARNING, logger="wordquest"):
        with TestClient(app) as c:
            c.get("/")
    assert not [r for r in caplog.records if "SITE_ACCESS_CODE" in r.getMessage()]


async def test_open_access_warning_ignores_loopback_clients(tmp_path):
    seen: list[tuple] = []

    async def inner(scope, receive, send) -> None:
        seen.append(scope["client"])

    middleware = OpenAccessWarning(inner, make_settings(tmp_path, site_access_code=""))
    await middleware({"type": "http", "client": ("127.0.0.1", 5000)}, None, None)
    await middleware({"type": "http", "client": ("::1", 5000)}, None, None)
    assert middleware.warned is False
    await middleware({"type": "http", "client": ("192.168.1.20", 5000)}, None, None)
    assert middleware.warned is True
    assert len(seen) == 3

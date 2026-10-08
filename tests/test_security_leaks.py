"""Spec §12 leak test and §9 access-gate coverage.

Sentinel secrets are pushed through the real Cerebras client and image client (via httpx.MockTransport
responses that echo the key in the body and the URL), through the job worker's failure paths, and then
every surface a person could read is scanned: GET responses, stored error fields, job errors, the
export, and every file under DATA_DIR.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta

import httpx
from fastapi.testclient import TestClient

import app.ai.llm as llm_mod
from app import clock
from app.ai.images.openai_compatible import OpenAICompatibleImageProvider
from app.ai.llm import CerebrasClient
from app.config import Settings
from app.jobs import Worker
from app.main import create_app
from app.models import LearnCard, Sense, WordContent

TODAY = "2026-10-07"
JOB_KINDS = ("learn", "questions", "image", "topup")
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
PATH_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[A-Za-z_]+)?\}")
# Every scanned GET answers 200 unless listed here. /media/{key:path} answers 404 because the lantern picture
# failed in this scenario, so no file is stored under the key the scan asks for.
EXPECTED_GET_STATUS = {"/media/{key:path}": 404}


def _sentinels() -> dict[str, str]:
    return {
        "cerebras_api_key": f"csk-{uuid.uuid4().hex}",
        "image_api_key": f"imgk-{uuid.uuid4().hex}",
        "site_access_code": f"site-{uuid.uuid4().hex}",
        "parent_passcode": f"parent-{uuid.uuid4().hex}",
        "secret_key": f"cookie-{uuid.uuid4().hex}",
    }


def _assert_clean(label: str, text: str, sentinels: dict[str, str]) -> None:
    leaked = [name for name, value in sentinels.items() if value in text]
    assert not leaked, f"{', '.join(leaked)} leaked in {label}"


def _fill_path(path: str, values: dict[str, str]) -> str:
    """Substitute every path parameter. A parameter with no value is an error, so a new route can never be skipped."""
    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        assert name in values, f"no value for path parameter {{{name}}} in {path}; add it to the scenario"
        return values[name]

    return PATH_PARAM.sub(fill, path)


def _placeholders(path: str) -> dict[str, str]:
    """Stand-in values for requests that the site gate rejects before any route handler runs."""
    return {name: "placeholder" for name in PATH_PARAM.findall(path)}


def _route_table(app) -> set[tuple[str, str]]:
    """Every (METHOD, path) the app serves.

    Top-level routes come from app.routes. Routes of included routers come from app.routes on FastAPI
    versions that flatten them and from the OpenAPI schema on newer versions that keep included routers
    nested inside app.routes, so both sources are merged.
    """
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path and methods:
            found.update((m, path) for m in methods if m != "HEAD")
    for path, operations in app.openapi()["paths"].items():
        found.update((m.upper(), path) for m in operations if m.upper() in HTTP_METHODS)
    return found


class SteppingClock:
    """now_fn for the Worker: every call is 10 minutes later, so 5 s / 30 s backoffs are always due."""

    def __init__(self) -> None:
        self.now = clock.utc_now()

    def __call__(self) -> datetime:
        self.now += timedelta(minutes=10)
        return self.now


def _lantern_card() -> LearnCard:
    return LearnCard(
        pos="noun",
        short_def="a light you can carry",
        kid_def="A lantern is a lamp with a handle that you can carry around.",
        senses=[Sense(pos="noun", definition="a portable lamp", example="She held the lantern up high.")],
        examples=[
            "The lantern glowed on the porch.",
            "We packed a lantern for camping.",
            "His lantern swung in the wind.",
            "Lanterns lit the path to the lake.",
        ],
        image_scene="a glowing lantern on a wooden porch at night",
    )


def _bad_card_json(key: str) -> str:
    # Schema-shaped but fails validation (one example): exercises the rejection log with the key inside.
    return json.dumps({
        "pos": "adjective", "forms": [], "short_def": f"brave means {key}", "kid_def": "Not afraid.",
        "senses": [{"pos": "adjective", "definition": "not afraid", "example": "The brave dog barked."}],
        "examples": ["The brave dog barked."], "word_parts": "", "memory_hook": "",
        "synonyms": [], "antonyms": [], "right_use": {"sentence": ""}, "wrong_use": {"sentence": "", "why": ""},
        "image_scene": "", "emoji_scene": "🐕",
    })


def test_no_secret_reaches_any_response_store_export_or_log(tmp_path, monkeypatch):
    secrets = _sentinels()
    llm_key, img_key = secrets["cerebras_api_key"], secrets["image_api_key"]
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        cerebras_base_url=f"https://llm.invalid/{llm_key}/v1",       # the key is in every request URL
        image_provider="openai_compatible",
        image_model="test-image-model",
        image_base_url=f"https://img.invalid/{img_key}/v1",
        **secrets,
    )
    seen = {"llm": 0, "img": 0, "llm_key_sent": 0, "img_key_sent": 0}

    def llm_handler(request: httpx.Request) -> httpx.Response:
        seen["llm"] += 1
        if request.headers.get("authorization") == f"Bearer {llm_key}" and llm_key in str(request.url):
            seen["llm_key_sent"] += 1
        echo = f"key={llm_key} auth={request.headers.get('authorization')} url={request.url}"
        if seen["llm"] == 1:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": _bad_card_json(llm_key)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "note": echo},
            })
        status = 500 if seen["llm"] % 2 == 0 else 400
        return httpx.Response(status, json={"error": {"message": f"upstream failure: {echo}"}})

    def img_handler(request: httpx.Request) -> httpx.Response:
        seen["img"] += 1
        if img_key in (request.headers.get("authorization") or "") and img_key in str(request.url):
            seen["img_key_sent"] += 1
        echo = f"key={img_key} auth={request.headers.get('authorization')} url={request.url}"
        return httpx.Response(400, text=f"invalid request: {echo}")

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm_mod, "_sleep", no_sleep)
    llm = CerebrasClient(
        api_key=llm_key, model=settings.cerebras_model, base_url=settings.cerebras_base_url,
        max_completion_tokens=1000, timeout_s=5.0, transport=httpx.MockTransport(llm_handler),
    )
    provider = OpenAICompatibleImageProvider(
        api_key=img_key, model=settings.image_model, base_url=settings.image_base_url,
        transport=httpx.MockTransport(img_handler),
    )
    app = create_app(settings, llm=llm, image_provider=provider, start_worker=False, seed=False)
    responses: list[tuple[str, httpx.Response]] = []

    def call(method: str, url: str, **kwargs) -> httpx.Response:
        r = client.request(method, url, **kwargs)
        responses.append((f"{method} {url}", r))
        return r

    with TestClient(app) as client:
        repo = app.state.repo
        assert not call("POST", "/api/auth/site", json={"code": "wrong-code"}).is_success
        assert call("POST", "/api/auth/site", json={"code": secrets["site_access_code"]}).is_success
        assert not call("POST", "/api/auth/parent", json={"passcode": "wrong-pass"}).is_success
        assert call("POST", "/api/auth/parent", json={"passcode": secrets["parent_passcode"]}).is_success

        # A ready word (so picture and regeneration jobs exist) plus a brand-new word.
        repo.save_content(WordContent(word="lantern", band="6-8", status="ready", card=_lantern_card()))
        pid = call("POST", "/api/parent/profiles", json={"name": "Ava", "band": "6-8"}).json()["id"]
        lid = call("POST", "/api/parent/lists", json={
            "name": "Leak test", "words": "brave, lantern", "assign_profile_ids": [pid],
        }).json()["list"]["id"]
        assert call("POST", "/api/parent/content/6-8/lantern/regenerate", json={"part": "image"}).is_success
        assert call("POST", "/api/parent/content/6-8/lantern/regenerate", json={"part": "all"}).is_success
        session = call("POST", f"/api/profiles/{pid}/sessions", json={"mode": "normal", "local_date": TODAY})
        sid = (session.json().get("session_id") if session.is_success else None) or "no-session"

        worker = Worker(
            repo=repo, blobs=app.state.blobs, generator=app.state.generator,
            image_provider=app.state.image_provider, settings=settings, now_fn=SteppingClock(),
        )

        async def drain() -> int:
            runs = 0
            while runs < 60 and await worker.run_one():
                runs += 1
            await llm.aclose()
            return runs

        assert asyncio.run(drain()) < 60

        # The failure paths really ran, with the real secret on the wire.
        assert seen["llm"] >= 6 and seen["llm_key_sent"] == seen["llm"]
        assert seen["img"] >= 3 and seen["img_key_sent"] == seen["img"]
        brave_job = repo.get_job("learn:6-8:brave")
        assert brave_job.status == "failed" and brave_job.attempts == 3 and brave_job.last_error
        assert repo.get_content("6-8", "brave").status == "failed"
        assert repo.get_content("6-8", "brave").error
        assert repo.get_job("learn:6-8:lantern").status == "failed"
        assert repo.get_content("6-8", "lantern").status == "ready"
        assert repo.get_job("image:6-8:lantern").status == "failed"
        assert repo.get_content("6-8", "lantern").image_status == "failed"
        # The parent sees why the picture failed (image_error); that text is scanned with every response below.
        rows = call("GET", "/api/parent/content", params={"list_id": lid, "band": "6-8"}).json()["items"]
        assert "HTTP 400" in next(row for row in rows if row["word"] == "lantern")["image_error"]

        # Every GET route, with real ids substituted into its path.
        values = {
            "id": pid, "profile_id": pid, "pid": pid, "list_id": lid, "lid": lid, "sid": sid,
            "session_id": sid, "band": "6-8", "word": "lantern", "key": "images/6-8/lantern-v1.webp",
        }
        query = {"local_date": TODAY, "list_id": lid, "band": "6-8"}
        get_paths = sorted(path for method, path in _route_table(app) if method == "GET")
        assert {"/api/parent/export", "/api/parent/content", "/api/parent/queue", "/api/profiles"} <= set(get_paths)
        for path in get_paths:
            r = call("GET", _fill_path(path, values), params=query)
            expected = EXPECTED_GET_STATUS.get(path, 200)
            assert r.status_code == expected, f"GET {path} -> {r.status_code}, expected {expected}"

        for label, r in responses:
            _assert_clean(f"response to {label}", r.text, secrets)
            _assert_clean(f"headers of {label}", json.dumps(dict(r.headers)), secrets)

        contents = repo.list_contents()
        assert contents
        for c in contents:
            _assert_clean(f"content {c.key}", c.model_dump_json(), secrets)
            for kind in JOB_KINDS:
                job = repo.get_job(f"{kind}:{c.band}:{c.word}")
                if job is not None:
                    _assert_clean(f"job {job.key}", job.model_dump_json(), secrets)
        _assert_clean("export_all()", json.dumps(repo.export_all(), ensure_ascii=False), secrets)

        # The secret really reached each stored error before redaction: redact() leaves this marker where it removed one.
        assert "[REDACTED]" in repo.get_content("6-8", "brave").error
        failed = {
            job.key: job
            for c in repo.list_contents()
            for job in (repo.get_job(f"{kind}:{c.band}:{c.word}") for kind in JOB_KINDS)
            if job is not None and job.status == "failed"
        }
        assert set(failed) == {"learn:6-8:brave", "learn:6-8:lantern", "image:6-8:lantern"}, sorted(failed)
        for key, job in failed.items():
            assert "[REDACTED]" in job.last_error, key

    logs_dir = settings.data_dir / "logs"
    assert logs_dir.is_dir()
    for name in ("ai-rejections.jsonl", "ai-usage.jsonl"):
        assert (logs_dir / name).is_file(), f"{name} was not written"
        assert "[REDACTED]" in (logs_dir / name).read_text(encoding="utf-8"), f"{name} holds no redacted secret"
    files = [p for p in settings.data_dir.rglob("*") if p.is_file()]
    assert files
    for path in files:
        _assert_clean(str(path.relative_to(settings.data_dir)), path.read_bytes().decode("utf-8", errors="ignore"), secrets)


def test_every_api_route_requires_site_cookie(tmp_path):
    settings = Settings(
        _env_file=None, data_dir=tmp_path / "data", site_access_code="gate-code-5521",
        parent_passcode="gate-parent-7781", secret_key="gate-cookie-secret", cerebras_api_key="",
    )
    app = create_app(settings, start_worker=False, seed=False)
    checked: list[tuple[str, str]] = []
    with TestClient(app) as client:
        r = client.post("/api/auth/site", json={"code": "not-the-code"})
        assert r.json().get("detail") != "access_code_required"      # the one route open without the cookie
        # /api/* and /media/* are gated; / and the docs pages are public by design and are not swept here.
        for method, path in sorted(_route_table(app)):
            if not path.startswith(("/api/", "/media/")) or (method, path) == ("POST", "/api/auth/site"):
                continue
            r = client.request(method, _fill_path(path, _placeholders(path)), json={})
            assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
            assert r.json() == {"detail": "access_code_required"}, f"{method} {path}"
            checked.append((method, PATH_PARAM.sub("{}", path)))
    must_cover = {
        ("POST", "/api/auth/parent"), ("POST", "/api/auth/parent/logout"),
        ("GET", "/api/profiles"), ("POST", "/api/profiles/{}/sessions"), ("POST", "/api/sessions/{}/events"),
        ("POST", "/api/sessions/{}/finish"), ("GET", "/api/parent/profiles"), ("POST", "/api/parent/lists"),
        ("POST", "/api/parent/import"), ("GET", "/api/parent/export"),
        ("POST", "/api/parent/content/{}/{}/regenerate"), ("PUT", "/api/parent/profiles/{}/lists"),
        ("GET", "/media/{}"),
    }
    missing = must_cover - set(checked)
    assert not missing, f"routes not found: {sorted(missing)}"

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app import clock
from app.auth import (
    MAX_FAILURES,
    PARENT_COOKIE,
    PARENT_MAX_AGE,
    SITE_COOKIE,
    SITE_MAX_AGE,
    require_parent,
    require_site,
)
from app.config import Settings
from app.main import create_app
from app.storage.base import Repository
from app.storage.local_blobs import LocalBlobStore
from app.storage.sqlite_repo import SqliteRepository

SITE_CODE = "open-sesame"
PARENT_CODE = "grown-ups-only"
SECRET = "auth-test-secret-key"


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        cerebras_api_key="",
        image_provider="none",
        site_access_code=SITE_CODE,
        parent_passcode=PARENT_CODE,
        secret_key=SECRET,
        data_dir=tmp_path / "data",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def build_app(tmp_path: Path, repo: Repository | None = None, **overrides) -> FastAPI:
    app = create_app(
        make_settings(tmp_path, **overrides),
        repo=repo if repo is not None else SqliteRepository(tmp_path / "wq.db"),
        blobs=LocalBlobStore(tmp_path / "data"),
        start_worker=False,
        seed=False,
    )

    # Probe routes stand in for the learner/parent routers, which arrive in later tasks.
    @app.get("/api/_probe/site", dependencies=[Depends(require_site)])
    def probe_site() -> dict:
        return {"ok": True}

    @app.get("/api/_probe/parent", dependencies=[Depends(require_site), Depends(require_parent)])
    def probe_parent() -> dict:
        return {"ok": True}

    return app


@pytest.fixture
def client(tmp_path):
    with TestClient(build_app(tmp_path)) as c:
        yield c


def login_site(c: TestClient) -> None:
    r = c.post("/api/auth/site", json={"code": SITE_CODE})
    assert r.status_code == 200, r.text


def login_parent(c: TestClient) -> None:
    r = c.post("/api/auth/parent", json={"passcode": PARENT_CODE})
    assert r.status_code == 200, r.text


def wrong_site(c: TestClient) -> int:
    return c.post("/api/auth/site", json={"code": "wrong"}).status_code


def wrong_parent(c: TestClient) -> int:
    return c.post("/api/auth/parent", json={"passcode": "wrong"}).status_code


def test_wrong_site_code_is_401(client):
    r = client.post("/api/auth/site", json={"code": "nope"})
    assert r.status_code == 401
    assert r.json() == {"detail": "wrong_code"}
    assert client.cookies.get(SITE_COOKIE) is None


def test_right_site_code_sets_signed_httponly_cookie(client):
    r = client.post("/api/auth/site", json={"code": SITE_CODE})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    header = r.headers["set-cookie"]
    assert header.startswith(f"{SITE_COOKIE}=")
    assert "httponly" in header.lower()
    assert "samesite=lax" in header.lower()
    assert f"Max-Age={SITE_MAX_AGE}" in header
    assert "secure" not in header.lower()
    value = client.cookies.get(SITE_COOKIE)
    assert value and SITE_CODE not in value
    assert TimestampSigner(SECRET).unsign(value) == b"site"


def test_gate_blocks_api_until_site_login(client):
    r = client.get("/api/_probe/site")
    assert r.status_code == 401
    assert r.json() == {"detail": "access_code_required"}
    login_site(client)
    assert client.get("/api/_probe/site").json() == {"ok": True}


def test_page_shell_is_reachable_without_cookie(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/index.html").status_code == 200


def test_gate_off_allows_api_without_cookie(tmp_path):
    with TestClient(build_app(tmp_path, site_access_code="")) as c:
        assert c.get("/api/_probe/site").json() == {"ok": True}
        r = c.post("/api/auth/site", json={"code": "anything"})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert "set-cookie" not in r.headers


def test_sixth_wrong_attempt_is_429_even_with_the_right_code(client):
    for _ in range(MAX_FAILURES):
        assert wrong_site(client) == 401
    r = client.post("/api/auth/site", json={"code": "wrong"})
    assert r.status_code == 429
    assert r.json() == {"detail": "too_many_attempts"}
    assert client.post("/api/auth/site", json={"code": SITE_CODE}).status_code == 429


def test_success_resets_the_failure_count(client):
    for _ in range(MAX_FAILURES - 1):
        assert wrong_site(client) == 401
    login_site(client)
    for _ in range(MAX_FAILURES):
        assert wrong_site(client) == 401
    assert wrong_site(client) == 429


def test_a_new_15_minute_window_allows_attempts_again(client, monkeypatch):
    start = datetime(2026, 10, 7, 14, 0, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, "utc_now", lambda: start)
    for _ in range(MAX_FAILURES):
        assert wrong_site(client) == 401
    assert wrong_site(client) == 429
    monkeypatch.setattr(clock, "utc_now", lambda: start + timedelta(minutes=15))
    assert wrong_site(client) == 401
    login_site(client)


def test_failure_counts_live_in_the_repository(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    shared = SqliteRepository(tmp_path / "shared.db")
    with TestClient(build_app(tmp_path / "a", repo=shared)) as first:
        for _ in range(MAX_FAILURES):
            assert wrong_site(first) == 401
    with TestClient(build_app(tmp_path / "b", repo=shared)) as second:
        assert wrong_site(second) == 429


def test_parent_lockout_does_not_lock_the_site_scope(client):
    login_site(client)
    for _ in range(MAX_FAILURES):
        assert wrong_parent(client) == 401
    assert wrong_parent(client) == 429
    login_site(client)


@pytest.mark.parametrize(
    "value",
    [
        "site",
        TimestampSigner("some-other-key").sign("site").decode(),
        TimestampSigner(SECRET).sign("parent").decode(),
    ],
    ids=["unsigned", "wrong-key", "wrong-scope"],
)
def test_forged_site_cookie_is_rejected(client, value):
    client.cookies.set(SITE_COOKIE, value)
    r = client.get("/api/_probe/site")
    assert r.status_code == 401
    assert r.json() == {"detail": "access_code_required"}


def test_cookie_signed_with_the_secret_key_is_accepted(client):
    client.cookies.set(SITE_COOKIE, TimestampSigner(SECRET).sign("site").decode())
    assert client.get("/api/_probe/site").json() == {"ok": True}


def test_edited_site_cookie_is_rejected(client):
    login_site(client)
    good = client.cookies.get(SITE_COOKIE)
    assert good.startswith("site.")
    client.cookies.clear()
    client.cookies.set(SITE_COOKIE, "parent" + good[len("site"):])
    assert client.get("/api/_probe/site").status_code == 401


def test_parent_disabled_without_passcode(tmp_path):
    with TestClient(build_app(tmp_path, parent_passcode="")) as c:
        login_site(c)
        r = c.post("/api/auth/parent", json={"passcode": "anything"})
        assert r.status_code == 403
        assert r.json() == {"detail": "parent_disabled"}
        r = c.get("/api/_probe/parent")
        assert r.status_code == 403
        assert r.json() == {"detail": "parent_disabled"}


def test_parent_login_requires_site_cookie_when_code_set(client):
    r = client.post("/api/auth/parent", json={"passcode": PARENT_CODE})
    assert r.status_code == 401
    assert r.json() == {"detail": "access_code_required"}
    assert client.cookies.get(PARENT_COOKIE) is None


def test_parent_login_flow(client):
    login_site(client)
    r = client.get("/api/_probe/parent")
    assert r.status_code == 401
    assert r.json() == {"detail": "parent_login_required"}
    r = client.post("/api/auth/parent", json={"passcode": "guess"})
    assert r.status_code == 401
    assert r.json() == {"detail": "wrong_code"}
    r = client.post("/api/auth/parent", json={"passcode": PARENT_CODE})
    assert r.status_code == 200
    assert f"Max-Age={PARENT_MAX_AGE}" in r.headers["set-cookie"]
    assert client.get("/api/_probe/parent").json() == {"ok": True}


def test_parent_routes_still_need_the_site_cookie(client):
    login_site(client)
    login_parent(client)
    client.cookies.delete(SITE_COOKIE)
    r = client.get("/api/_probe/parent")
    assert r.status_code == 401
    assert r.json() == {"detail": "access_code_required"}


def test_parent_cookie_expires_after_12_hours(client, monkeypatch):
    login_site(client)
    login_parent(client)
    signer = client.app.state.auth.signer
    now = int(time.time())
    monkeypatch.setattr(signer, "get_timestamp", lambda: now + PARENT_MAX_AGE - 60)
    assert client.get("/api/_probe/parent").status_code == 200
    monkeypatch.setattr(signer, "get_timestamp", lambda: now + PARENT_MAX_AGE + 60)
    r = client.get("/api/_probe/parent")
    assert r.status_code == 401
    assert r.json() == {"detail": "parent_login_required"}
    assert client.get("/api/_probe/site").status_code == 200  # the site cookie lasts 180 days


def test_logout_clears_the_parent_cookie_only(client):
    login_site(client)
    login_parent(client)
    assert client.get("/api/_probe/parent").status_code == 200
    r = client.post("/api/auth/parent/logout")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert r.headers["set-cookie"].startswith(f"{PARENT_COOKIE}=")
    assert client.get("/api/_probe/parent").status_code == 401
    assert client.get("/api/_probe/site").status_code == 200


def test_cookie_is_secure_over_https(tmp_path):
    with TestClient(build_app(tmp_path), base_url="https://testserver") as c:
        r = c.post("/api/auth/site", json={"code": SITE_CODE})
        assert r.status_code == 200
        assert "secure" in r.headers["set-cookie"].lower()
        assert c.get("/api/_probe/site").status_code == 200

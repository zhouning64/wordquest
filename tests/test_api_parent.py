from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
from itertools import product
from string import ascii_lowercase
import pytest
from fastapi.testclient import TestClient

import app.api.parent as parent_api
from app import clock
from app.ai.images.base import ImageProvider
from app.backup import import_backup as real_import_backup
from app.config import Settings
from app.jobs import Worker
from app.learning.srs import apply_events_to_state
from app.main import create_app
from app.models import AnswerEvent, LearnCard, Question, Sense, Session, WordContent, WordList
from app.storage.local_blobs import LocalBlobStore
from app.storage.sqlite_repo import SqliteRepository

SITE = "site-code-4821"
PARENT = "parent-pass-9377"
TODAY = "2026-10-07"


class RecordingWorker:
    """Stands in for app.jobs.Worker on app.state.worker: records pause/drain/resume calls."""

    def __init__(self, drained: bool = True) -> None:
        self.drained = drained
        self.paused = False
        self.calls: list[str] = []

    async def pause_and_drain(self, timeout: float = 30.0) -> bool:
        self.calls.append("pause_and_drain")
        self.paused = True
        return self.drained

    def resume(self) -> None:
        self.calls.append("resume")
        self.paused = False


def _settings(tmp_path, **overrides) -> Settings:
    values = dict(
        data_dir=tmp_path / "data",
        site_access_code=SITE,
        parent_passcode=PARENT,
        secret_key="unit-test-cookie-secret",
        cerebras_api_key="",
        cerebras_model="gpt-oss-120b",
        image_provider="none",
        ai_daily_call_limit=2000,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _login(client: TestClient, *, parent: bool = True) -> None:
    assert client.post("/api/auth/site", json={"code": SITE}).is_success
    if parent:
        assert client.post("/api/auth/parent", json={"passcode": PARENT}).is_success


@pytest.fixture
def repo(tmp_path) -> SqliteRepository:
    return SqliteRepository(tmp_path / "wq.db")


@pytest.fixture
def blobs(tmp_path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path / "blobs")


@pytest.fixture
def app(tmp_path, repo, blobs):
    return create_app(_settings(tmp_path), repo=repo, blobs=blobs, start_worker=False, seed=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        _login(c)
        yield c


def _card(word: str) -> LearnCard:
    return LearnCard(
        pos="adjective",
        short_def=f"{word}: a short meaning",
        kid_def=f"A longer kid-friendly explanation of {word}.",
        senses=[Sense(pos="adjective", definition="a meaning", example=f"The {word} kid smiled.")],
        examples=[f"One {word} example.", f"Two {word} example.", f"Three {word} example.", f"Four {word} example."],
        synonyms=["bold"],
        image_scene="a kid on a playground",
    )


def _question(word: str, n: int, qtype: str = "meaning", tier: int = 1, version: int = 1) -> Question:
    return Question(
        id=f"{word}-q{n}", word=word, band="6-8", content_version=version, type=qtype, tier=tier,
        prompt=f"What does {word} mean? ({n})", choices=["a", "b", "c", "d"], answer_index=n % 4,
        explanation="because", verified=True, created_at=f"2026-10-07T10:00:{n:02d}Z",
    )


def _ready(repo, word: str, *, band: str = "6-8", pool: int = 0, **fields) -> WordContent:
    content = WordContent(word=word, band=band, status="ready", card=_card(word), **fields)
    repo.save_content(content)
    if pool:
        repo.add_questions(band, word, content.content_version, [_question(word, i) for i in range(pool)])
    return content


def _new_profile(client, name: str = "Ava", band: str = "6-8", **extra) -> dict:
    r = client.post("/api/parent/profiles", json={"name": name, "band": band, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _new_list(client, name: str, words, assign: list[str] | None = None) -> dict:
    r = client.post("/api/parent/lists", json={"name": name, "words": words, "assign_profile_ids": assign or []})
    assert r.status_code == 200, r.text
    return r.json()


def _event(n: int, word: str, kind: str, *, sid: str, pid: str, qtype: str | None = "meaning",
           correct: bool | None = None) -> AnswerEvent:
    return AnswerEvent(
        client_event_id=f"evt-{n:04d}", session_id=sid, profile_id=pid, word=word,
        question_id=f"q{n}" if qtype else None, question_type=qtype, kind=kind, correct=correct,
        ms=800, local_date=TODAY, at=f"{TODAY}T10:00:{n:02d}Z",
    )


# ---------- auth ----------

def test_parent_routes_need_site_then_parent_cookie(client):
    client.cookies.clear()
    r = client.get("/api/parent/profiles")
    assert r.status_code == 401
    assert r.json()["detail"] == "access_code_required"
    _login(client, parent=False)
    r = client.get("/api/parent/profiles")
    assert r.status_code == 401
    assert r.json()["detail"] == "parent_login_required"
    _login(client)
    assert client.get("/api/parent/profiles").status_code == 200


def test_parent_area_disabled_without_passcode(tmp_path, repo, blobs):
    app = create_app(_settings(tmp_path, parent_passcode=""), repo=repo, blobs=blobs, start_worker=False, seed=False)
    with TestClient(app) as c:
        _login(c, parent=False)
        r = c.get("/api/parent/status")
        assert r.status_code == 403
        assert r.json()["detail"] == "parent_disabled"


# ---------- profiles ----------

def test_create_and_list_profiles(client):
    created = _new_profile(client, "  Ava  ", "6-8", avatar="🦊", settings={"session_minutes": 20})
    assert re.fullmatch(r"[0-9a-f]{12}", created["id"])
    assert created["name"] == "Ava"
    assert created["avatar"] == "🦊"
    assert created["created_at"].endswith("Z")
    assert created["list_ids"] == []
    assert created["settings"]["session_minutes"] == 20
    assert created["settings"]["new_words_per_session"] == 5
    listed = client.get("/api/parent/profiles").json()
    assert [p["id"] for p in listed] == [created["id"]]


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "band": "6-8"},
        {"name": "   ", "band": "6-8"},
        {"name": "x" * 31, "band": "6-8"},
        {"name": "Ava", "band": "k-2"},
        {"name": "Ava", "band": "6-8", "settings": {"session_minutes": 45}},
    ],
)
def test_create_profile_rejects_invalid_input(client, body):
    assert client.post("/api/parent/profiles", json=body).status_code == 422
    assert client.get("/api/parent/profiles").json() == []


def test_create_profile_with_lists_triggers_generation(client, repo):
    wl = _new_list(client, "Week 1", "brave, calm")["list"]
    assert repo.get_job("learn:3-5:brave") is None          # unassigned list generates nothing
    created = _new_profile(client, "Ben", "3-5", list_ids=[wl["id"]])
    assert created["list_ids"] == [wl["id"]]
    for word in ("brave", "calm"):
        job = repo.get_job(f"learn:3-5:{word}")
        assert job is not None and job.status == "pending"
        assert job.target_version == 1 and job.chain == ["questions", "image"]
        assert repo.get_content("3-5", word).status == "pending"
    r = client.post("/api/parent/profiles", json={"name": "Cy", "band": "3-5", "list_ids": ["nope"]})
    assert r.status_code == 400
    assert "unknown list ids" in r.json()["detail"]


def test_patch_profile_fields_settings_and_band_change(client, repo):
    p = _new_profile(client, "Ava", "6-8")
    wl = _new_list(client, "Week 1", ["brave"], assign=[p["id"]])["list"]
    assert repo.get_job("learn:6-8:brave") is not None
    assert repo.get_job("learn:9-12:brave") is None

    r = client.patch(f"/api/parent/profiles/{p['id']}", json={"name": "Ava B", "avatar": "🐼"})
    assert r.status_code == 200
    assert (r.json()["name"], r.json()["avatar"], r.json()["band"]) == ("Ava B", "🐼", "6-8")
    assert repo.get_job("learn:9-12:brave") is None          # no band change → no new generation

    r = client.patch(f"/api/parent/profiles/{p['id']}", json={"settings": {"session_minutes": 20, "break_reminder": False}})
    assert r.status_code == 200
    s = r.json()["settings"]
    assert (s["session_minutes"], s["break_reminder"], s["new_words_per_session"]) == (20, False, 5)

    r = client.patch(f"/api/parent/profiles/{p['id']}", json={"band": "9-12"})
    assert r.status_code == 200 and r.json()["band"] == "9-12"
    assert r.json()["list_ids"] == [wl["id"]]
    job = repo.get_job("learn:9-12:brave")
    assert job is not None and job.status == "pending" and job.target_version == 1

    assert client.patch(f"/api/parent/profiles/{p['id']}", json={"settings": {"session_minutes": 99}}).status_code == 422
    assert repo.get_profile(p["id"]).settings.session_minutes == 20
    assert client.patch("/api/parent/profiles/missing", json={"name": "X"}).status_code == 404


def test_delete_profile_cascades(client, repo):
    p = _new_profile(client)
    pid = p["id"]
    repo.save_session(Session(id="s1", profile_id=pid, mode="normal", local_date=TODAY,
                              started_at=f"{TODAY}T10:00:00Z", planned_minutes=15, words=["brave"]))
    repo.apply_events("s1", [_event(1, "brave", "intro_seen", sid="s1", pid=pid, qtype=None),
                             _event(2, "brave", "answer", sid="s1", pid=pid, correct=True)], apply_events_to_state)
    assert repo.list_progress(pid) and repo.list_events(pid, "2000-01-01")

    assert client.delete(f"/api/parent/profiles/{pid}").json() == {"ok": True}
    assert client.get("/api/parent/profiles").json() == []
    assert repo.list_progress(pid) == []
    assert repo.get_session("s1") is None
    assert repo.list_events(pid, "2000-01-01") == []
    assert client.delete(f"/api/parent/profiles/{pid}").status_code == 404


def test_put_profile_lists_replaces_dedupes_and_triggers(client, repo):
    p = _new_profile(client, "Ava", "9-12")
    l1 = _new_list(client, "One", "brave")["list"]
    l2 = _new_list(client, "Two", "calm")["list"]
    r = client.put(f"/api/parent/profiles/{p['id']}/lists", json={"list_ids": [l2["id"], l1["id"], l2["id"]]})
    assert r.status_code == 200
    assert r.json()["list_ids"] == [l2["id"], l1["id"]]
    assert repo.get_job("learn:9-12:brave").status == "pending"
    assert repo.get_job("learn:9-12:calm").status == "pending"

    r = client.put(f"/api/parent/profiles/{p['id']}/lists", json={"list_ids": [l1["id"], "ghost"]})
    assert r.status_code == 400
    assert "ghost" in r.json()["detail"]
    assert repo.get_profile(p["id"]).list_ids == [l2["id"], l1["id"]]
    assert client.put("/api/parent/profiles/missing/lists", json={"list_ids": []}).status_code == 404


# ---------- lists ----------

LONG = "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopq"   # 43 letters


def test_create_list_normalizes_reports_rejections_and_generates_for_both_bands(client, repo):
    young = _new_profile(client, "Ben", "3-5")
    teen = _new_profile(client, "Cy", "9-12")
    body = _new_list(client, "Week 1", f"Brave, brave\nCALM\n{LONG}\none two three four",
                     assign=[young["id"], teen["id"]])
    wl = body["list"]
    assert wl["name"] == "Week 1"
    assert wl["words"] == ["brave", "calm"]
    assert wl["word_count"] == 2
    assert wl["ready_count"] == 0
    assert wl["bands"] == ["3-5", "9-12"]
    assert sorted(wl["profile_ids"]) == sorted([young["id"], teen["id"]])
    assert {(r["entry"].strip(), r["reason"]) for r in body["rejected"]} == {
        (LONG, "too long"),
        ("one two three four", "more than 3 words"),
    }
    assert repo.get_profile(young["id"]).list_ids == [wl["id"]]
    assert repo.get_profile(teen["id"]).list_ids == [wl["id"]]
    for band in ("3-5", "9-12"):
        for word in ("brave", "calm"):
            job = repo.get_job(f"learn:{band}:{word}")
            assert job is not None and job.status == "pending"
            assert job.target_version == 1 and job.chain == ["questions", "image"]
            assert repo.get_content(band, word).status == "pending"
    assert repo.get_job("learn:6-8:brave") is None


def test_create_list_with_unknown_profile_creates_nothing(client, repo):
    r = client.post("/api/parent/lists", json={"name": "W", "words": "brave", "assign_profile_ids": ["ghost"]})
    assert r.status_code == 400
    assert repo.list_lists() == []
    assert client.post("/api/parent/lists", json={"name": "", "words": "brave"}).status_code == 422


def test_get_lists_reports_readiness(client, repo):
    p = _new_profile(client, "Ava", "6-8")
    _ready(repo, "brave", pool=6)
    wl = _new_list(client, "Week 1", "brave, calm", assign=[p["id"]])["list"]
    rows = client.get("/api/parent/lists").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == wl["id"]
    assert (row["word_count"], row["ready_count"], row["bands"], row["profile_ids"]) == (2, 1, ["6-8"], [p["id"]])


def test_patch_list_add_remove_rename_and_reassign(client, repo):
    p1 = _new_profile(client, "Ava", "6-8")
    p2 = _new_profile(client, "Ben", "3-5")
    wl = _new_list(client, "Week 1", "brave, calm", assign=[p1["id"]])["list"]

    r = client.patch(f"/api/parent/lists/{wl['id']}", json={
        "name": "Week 2", "add_words": "eager, Brave, one two three four", "remove_words": ["CALM"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["list"]["name"] == "Week 2"
    assert body["list"]["words"] == ["brave", "eager"]
    assert [(r["entry"].strip(), r["reason"]) for r in body["rejected"]] == [("one two three four", "more than 3 words")]
    assert repo.get_job("learn:6-8:eager").status == "pending"

    r = client.patch(f"/api/parent/lists/{wl['id']}", json={"assign_profile_ids": [p2["id"]]})
    assert r.status_code == 200
    assert r.json()["list"]["profile_ids"] == [p2["id"]]
    assert repo.get_profile(p1["id"]).list_ids == []
    assert repo.get_profile(p2["id"]).list_ids == [wl["id"]]
    assert repo.get_job("learn:3-5:brave").status == "pending"
    assert repo.get_job("learn:3-5:eager").status == "pending"

    assert client.patch(f"/api/parent/lists/{wl['id']}", json={"assign_profile_ids": ["ghost"]}).status_code == 400
    assert repo.get_profile(p2["id"]).list_ids == [wl["id"]]
    assert client.patch("/api/parent/lists/missing", json={"name": "X"}).status_code == 404


def test_patch_list_rejects_words_past_600(client, repo):
    words = ["".join(t) for t in product(ascii_lowercase, repeat=3)][:599]
    repo.save_list(WordList(id="big", name="Big", words=words, created_at=clock.utc_now_iso()))
    r = client.patch("/api/parent/lists/big", json={"add_words": ["kite", "lamp", "aaa"]})
    assert r.status_code == 200
    assert r.json()["list"]["word_count"] == 600
    assert r.json()["list"]["words"][-1] == "kite"
    assert r.json()["rejected"] == [{"entry": "lamp", "reason": "list is full (600 max)"}]


def test_delete_list_removes_it_from_profiles(client, repo):
    p = _new_profile(client)
    wl = _new_list(client, "Week 1", "brave", assign=[p["id"]])["list"]
    assert client.delete(f"/api/parent/lists/{wl['id']}").json() == {"ok": True}
    assert client.get("/api/parent/lists").json() == []
    assert repo.get_profile(p["id"]).list_ids == []
    assert client.delete(f"/api/parent/lists/{wl['id']}").status_code == 404


# ---------- content ----------

def test_content_listing_rows(client, repo):
    p = _new_profile(client, "Ava", "6-8")
    wl = _new_list(client, "Week 1", "brave, calm", assign=[p["id"]])["list"]
    # Overwrite the pending records the trigger created with known states.
    _ready(repo, "brave", pool=3, image_key="images/6-8/brave-v1.webp", image_status="ready", draft_version=2)
    repo.save_content(WordContent(word="calm", band="6-8", status="failed", error="learn card rejected: L1"))

    r = client.get("/api/parent/content", params={"list_id": wl["id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["bands"] == ["6-8"]
    assert body["note"] is None
    rows = {row["word"]: row for row in body["items"]}
    assert rows["brave"] == {
        "word": "brave", "band": "6-8", "status": "ready", "image_status": "ready", "pool_size": 3,
        "error": "", "image_error": "", "regenerating": True, "source": "ai", "content_version": 1,
    }
    assert rows["calm"]["status"] == "failed"
    assert rows["calm"]["error"] == "learn card rejected: L1"
    assert rows["calm"]["regenerating"] is False

    other = client.get("/api/parent/content", params={"list_id": wl["id"], "band": "9-12"}).json()
    assert other["bands"] == ["9-12"]
    assert [(row["word"], row["status"], row["pool_size"]) for row in other["items"]] == [("brave", "missing", 0), ("calm", "missing", 0)]
    assert client.get("/api/parent/content", params={"list_id": wl["id"], "band": "1-2"}).status_code == 400
    assert client.get("/api/parent/content", params={"list_id": "missing"}).status_code == 404


def test_content_listing_for_unassigned_list_is_empty_with_note(client):
    wl = _new_list(client, "Loose", "brave")["list"]
    body = client.get("/api/parent/content", params={"list_id": wl["id"]}).json()
    assert body["bands"] == [] and body["items"] == []
    assert "not assigned" in body["note"]


def test_content_detail_includes_card_and_full_pool(client, repo):
    _ready(repo, "brave", pool=2, image_key="images/6-8/brave-v1.webp", image_status="ready")
    r = client.get("/api/parent/content/6-8/brave")
    assert r.status_code == 200
    body = r.json()
    assert body["content"]["card"]["short_def"] == "brave: a short meaning"
    assert body["image_url"] == "/media/images/6-8/brave-v1.webp"
    assert body["image_error"] == ""
    assert [q["id"] for q in body["pool"]] == ["brave-q0", "brave-q1"]
    assert body["pool"][1]["answer_index"] == 1
    assert body["pool"][0]["verified"] is True
    assert client.get("/api/parent/content/6-8/missing").status_code == 404
    assert client.get("/api/parent/content/2-3/brave").status_code == 400


def _picture_job(repo, word: str, error: str, *, gave_up: bool, band: str = "6-8") -> None:
    """A picture job whose attempt failed with `error`: gave_up=True leaves it failed for good; otherwise it is retried
    and succeeds (its last_error stays on the finished job). Call it while no other job is queued, so claims pick it."""
    repo.enqueue_job("image", band, word, 1, [])
    job = repo.claim_next_job(clock.utc_now_iso(), 300)
    assert job is not None and job.key == f"image:{band}:{word}"
    assert repo.fail_job(job.key, job.lease_token, error, None if gave_up else clock.utc_now_iso())
    if not gave_up:
        job = repo.claim_next_job(clock.utc_now_iso(), 300)
        assert job is not None and job.key == f"image:{band}:{word}"
        assert repo.finish_job(job.key, job.lease_token)


class _BrokenImages(ImageProvider):
    name = "broken"

    async def generate(self, prompt: str) -> bytes:
        raise RuntimeError("image service down")


class _SteppingClock:
    """Worker now_fn: every call is 10 minutes later, so the 5 s / 30 s picture retries are always due."""

    def __init__(self) -> None:
        self.now = clock.utc_now()

    def __call__(self):
        self.now += timedelta(minutes=10)
        return self.now


def test_failed_picture_is_flagged_only_when_no_earlier_picture_exists(client, repo, blobs, tmp_path):
    reason = "RuntimeError: image service down"
    _ready(repo, "brave", pool=6, image_status="pending")  # first picture still to draw
    _ready(repo, "calm", pool=6, image_key="images/6-8/calm-v1.webp", image_status="ready")
    p = _new_profile(client, "Ava", "6-8")
    wl = _new_list(client, "Week 1", "brave, calm", assign=[p["id"]])["list"]
    repo.enqueue_job("image", "6-8", "brave", 1, [])
    assert client.post("/api/parent/content/6-8/calm/regenerate", json={"part": "image"}).is_success  # redraw
    worker = Worker(repo=repo, blobs=blobs, generator=None, image_provider=_BrokenImages(),
                    settings=_settings(tmp_path), now_fn=_SteppingClock())

    async def drain() -> None:
        while await worker.run_one():
            pass

    asyncio.run(drain())  # both picture jobs give up after 3 attempts

    rows = {row["word"]: row for row in client.get("/api/parent/content", params={"list_id": wl["id"]}).json()["items"]}
    assert (rows["brave"]["image_status"], rows["brave"]["image_error"]) == ("failed", reason)  # 🖼 failed + Retry
    assert (rows["calm"]["image_status"], rows["calm"]["image_error"]) == ("ready", "")  # earlier picture kept
    assert repo.get_job("image:6-8:calm").last_error == reason  # ...and the failure stays on the job
    assert client.get("/api/parent/content/6-8/calm").json()["image_url"] == "/media/images/6-8/calm-v1.webp"
    session = client.post(f"/api/profiles/{p['id']}/sessions", json={"mode": "normal", "local_date": TODAY}).json()
    assert session["words"]["calm"]["image_url"] == "/media/images/6-8/calm-v1.webp"  # learners still see it
    assert session["words"]["brave"]["image_url"] is None  # emoji scene


def test_content_rows_and_detail_explain_a_failed_picture(client, repo):
    reason = "LLMError: image provider HTTP 400: size 999x999 is not allowed for glm-image"
    _picture_job(repo, "brave", reason, gave_up=True)
    _picture_job(repo, "calm", "TransientError: image request failed: ReadTimeout", gave_up=False)
    p = _new_profile(client, "Ava", "6-8")
    wl = _new_list(client, "Week 1", "brave, calm, eager", assign=[p["id"]])["list"]
    _ready(repo, "brave", pool=3, image_status="failed")
    _ready(repo, "calm", pool=3, image_key="images/6-8/calm-v1.webp", image_status="ready")  # worked on the retry
    _ready(repo, "eager", pool=3, image_status="failed")  # failed, but its job is gone (e.g. after an import)

    rows = {row["word"]: row for row in client.get("/api/parent/content", params={"list_id": wl["id"]}).json()["items"]}
    assert (rows["brave"]["image_status"], rows["brave"]["image_error"]) == ("failed", reason)
    assert rows["calm"]["image_error"] == ""  # the earlier attempt's reason is not shown for a ready picture
    assert rows["eager"]["image_error"] == ""
    assert client.get("/api/parent/content/6-8/brave").json()["image_error"] == reason  # Preview shows it too
    assert client.get("/api/parent/content/6-8/calm").json()["image_error"] == ""

    r = client.post("/api/parent/content/6-8/brave/regenerate", json={"part": "image"})  # Retry picture
    assert (r.json()["image_status"], r.json()["image_error"]) == ("pending", "")


def test_regenerate_keeps_current_version_serving(client, repo):
    _ready(repo, "brave", pool=6)
    r = client.post("/api/parent/content/6-8/brave/regenerate", json={"part": "questions"})
    assert r.status_code == 200
    assert r.json()["regenerating"] is True
    assert r.json()["status"] == "ready"
    content = repo.get_content("6-8", "brave")
    assert (content.status, content.content_version, content.draft_version) == ("ready", 1, 2)
    assert len(repo.get_pool("6-8", "brave")) == 6
    job = repo.get_job("questions:6-8:brave")
    assert (job.status, job.target_version, job.chain) == ("pending", 2, [])

    assert client.post("/api/parent/content/6-8/brave/regenerate", json={"part": "everything"}).status_code == 400
    repo.save_content(WordContent(word="calm", band="6-8"))      # pending, no card yet
    assert client.post("/api/parent/content/6-8/calm/regenerate", json={"part": "questions"}).status_code == 400
    assert client.post("/api/parent/content/6-8/ghost/regenerate", json={"part": "all"}).status_code == 404


# ---------- queue, stats, status ----------

def test_queue_counts_calls_and_deferral(client, repo):
    p = _new_profile(client)
    _new_list(client, "Week 1", "brave, calm", assign=[p["id"]])
    today = clock.utc_date()
    repo.incr_ai_calls(today)
    repo.incr_ai_calls(today)
    body = client.get("/api/parent/queue").json()
    assert body == {
        "counts": {"pending": 2, "running": 0, "done": 0, "failed": 0},
        "ai_calls_today": 2,
        "ai_daily_limit": 2000,
        "deferred_to_tomorrow": False,
    }
    claimed = {job.word: job for job in (repo.claim_next_job(clock.utc_now_iso(), 300) for _ in range(2))}
    assert repo.defer_job("learn:6-8:brave", claimed["brave"].lease_token, clock.utc_now_iso())
    assert client.get("/api/parent/queue").json()["deferred_to_tomorrow"] is False
    assert repo.defer_job("learn:6-8:calm", claimed["calm"].lease_token, clock.add_days(today, 1) + "T00:00:00Z")
    assert client.get("/api/parent/queue").json()["deferred_to_tomorrow"] is True


def test_profile_stats_endpoint(client, repo):
    p = _new_profile(client)
    pid = p["id"]
    _new_list(client, "Week 1", "brave, calm", assign=[pid])
    repo.save_session(Session(id="s1", profile_id=pid, mode="normal", local_date=TODAY,
                              started_at=f"{TODAY}T10:00:00Z", planned_minutes=15, words=["brave"]))
    repo.apply_events("s1", [
        _event(1, "brave", "intro_seen", sid="s1", pid=pid, qtype=None),
        _event(2, "brave", "answer", sid="s1", pid=pid, qtype="meaning", correct=True),
        _event(3, "brave", "answer", sid="s1", pid=pid, qtype="fill_blank", correct=False),
    ], apply_events_to_state)

    r = client.get(f"/api/parent/profiles/{pid}/stats", params={"local_date": TODAY})
    assert r.status_code == 200
    stats = r.json()
    assert stats["counts"] == {"mastered": 0, "learning": 1, "new": 1, "due_today": 0, "total": 2}
    assert stats["accuracy"]["overall"] == 0.5
    assert stats["accuracy"]["by_type"] == {
        "meaning": {"answered": 1, "correct": 1, "accuracy": 1.0},
        "fill_blank": {"answered": 1, "correct": 0, "accuracy": 0.0},
    }
    assert stats["unsure_rate"] == 0.0
    assert stats["days_practiced_30"] == 1
    assert [w["word"] for w in stats["weakest"]] == ["brave"]
    assert [(s["id"], s["answered"], s["correct"]) for s in stats["recent_sessions"]] == [("s1", 2, 1)]
    assert client.get("/api/parent/profiles/missing/stats").status_code == 404
    assert client.get(f"/api/parent/profiles/{pid}/stats", params={"local_date": "yesterday"}).status_code == 400


def test_status_reports_config_without_secret_values(tmp_path, repo, blobs):
    settings = _settings(tmp_path, cerebras_api_key="csk-live-abcdef123456", image_api_key="img-key-998877",
                         image_provider="openai_compatible", image_model="glm-image",
                         image_base_url="https://api.z.ai/api/paas/v4")
    app = create_app(settings, repo=repo, blobs=blobs, start_worker=False, seed=False)
    with TestClient(app) as c:
        _login(c)
        r = c.get("/api/parent/status")
    assert r.status_code == 200
    assert r.json() == {
        "ai_enabled": True, "model": "gpt-oss-120b", "image_provider": "openai_compatible",
        "image_model": "glm-image", "image_config_error": "", "parent_enabled": True,
    }
    for secret in settings.secret_values():
        assert secret not in r.text


def test_status_when_ai_not_configured(client):
    assert client.get("/api/parent/status").json() == {
        "ai_enabled": False, "model": "gpt-oss-120b", "image_provider": "none", "image_model": "",
        "image_config_error": "", "parent_enabled": True,
    }


def test_broken_image_settings_still_serve_and_status_explains_pictures_off(tmp_path, repo, blobs):
    settings = _settings(tmp_path, image_provider="openai_compatible", image_api_key="img-key-998877",
                         image_model="glm-image")  # IMAGE_BASE_URL forgotten
    app = create_app(settings, repo=repo, blobs=blobs, start_worker=False, seed=False)
    with TestClient(app) as c:
        _login(c)
        assert c.get("/api/parent/profiles").status_code == 200  # the app runs normally
        r = c.get("/api/parent/status")
    assert r.json() == {
        "ai_enabled": False, "model": "gpt-oss-120b", "image_provider": "none", "image_model": "",
        "image_config_error": (
            "IMAGE_BASE_URL is required when IMAGE_PROVIDER=openai_compatible (for z.ai use https://api.z.ai/api/paas/v4)"
        ),
        "parent_enabled": True,
    }
    assert "img-key-998877" not in r.text


# ---------- export / import ----------

def test_export_import_round_trip(app, client, repo, blobs, monkeypatch):
    p = _new_profile(client)
    wl = _new_list(client, "Week 1", "brave, calm, eager, glad, frugal, kind", assign=[p["id"]])["list"]
    # Overwrite the pending records the trigger created with known states.
    _ready(repo, "brave", pool=6, image_key="images/6-8/brave-v1.webp", image_status="ready",
           draft_version=2)                                                                   # regeneration in flight
    blobs.put("images/6-8/brave-v1.webp", b"RIFF-fake-webp", "image/webp")
    _ready(repo, "calm", pool=6, image_key="images/6-8/calm-v1.webp", image_status="ready")   # blob missing
    repo.save_content(WordContent(word="eager", band="6-8"))                                 # pending, no card
    repo.save_content(WordContent(word="glad", band="6-8", card=_card("glad")))                # pending, card, no pool
    repo.save_content(WordContent(word="frugal", band="6-8", status="failed", card=_card("frugal"), error="x"))
    repo.save_content(WordContent(word="kind", band="6-8", card=_card("kind")))                # pending, card, full pool
    repo.add_questions("6-8", "kind", 1, [_question("kind", i) for i in range(5)] + [_question("kind", 5, "usage", 2)])

    r = client.get("/api/parent/export")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment;")
    assert ".json" in r.headers["content-disposition"]
    backup = r.json()
    assert backup["format"] == "wordquest-backup" and backup["version"] == 1
    assert len(backup["contents"]) == 6
    raw = json.dumps(backup)

    assert client.delete(f"/api/parent/lists/{wl['id']}").status_code == 200
    assert client.delete(f"/api/parent/profiles/{p['id']}").status_code == 200

    files = {"file": ("backup.json", raw, "application/json")}
    r = client.post("/api/parent/import", files=files)
    assert r.status_code == 400 and r.json()["detail"] == "confirm_required"
    r = client.post("/api/parent/import", files=files, data={"confirm": "false"})
    assert r.status_code == 400
    assert client.get("/api/parent/profiles").json() == []

    worker = RecordingWorker()
    app.state.worker = worker

    def spy(repo_, blobs_, data_):
        worker.calls.append(f"import (paused={worker.paused})")
        return real_import_backup(repo_, blobs_, data_)

    monkeypatch.setattr(parent_api, "import_backup", spy)
    r = client.post("/api/parent/import", files=files, data={"confirm": "true"})
    assert r.status_code == 200, r.text
    counts = r.json()["counts"]
    assert (counts["profiles"], counts["lists"], counts["contents"], counts["questions"]) == (1, 1, 6, 18)
    assert (counts["images_missing"], counts["jobs_requeued"]) == (1, 4)
    assert worker.calls == ["pause_and_drain", "import (paused=True)", "resume"]
    assert worker.paused is False

    assert [x["id"] for x in client.get("/api/parent/profiles").json()] == [p["id"]]
    assert client.get("/api/parent/lists").json()[0]["words"] == ["brave", "calm", "eager", "glad", "frugal", "kind"]
    brave = repo.get_content("6-8", "brave")
    assert (brave.image_status, brave.image_key) == ("ready", "images/6-8/brave-v1.webp")
    assert (brave.status, brave.draft_version) == ("ready", None)   # the orphaned regeneration was dropped
    calm = repo.get_content("6-8", "calm")
    assert (calm.image_status, calm.image_key) == ("none", None)
    assert len(repo.get_pool("6-8", "brave")) == 6

    eager_job = repo.get_job("learn:6-8:eager")
    assert (eager_job.status, eager_job.target_version, eager_job.chain) == ("pending", 1, ["questions", "image"])
    glad_job = repo.get_job("questions:6-8:glad")
    assert (glad_job.status, glad_job.chain) == ("pending", ["image"])
    frugal_job = repo.get_job("questions:6-8:frugal")
    assert (frugal_job.status, frugal_job.chain) == ("pending", ["image"])
    assert repo.get_content("6-8", "frugal").status == "pending"
    kind = repo.get_content("6-8", "kind")                 # card + minimum pool: it only needed the last step
    assert kind.status == "ready"
    assert repo.get_job("image:6-8:kind").status == "pending"
    assert repo.get_job("questions:6-8:kind") is None
    assert repo.get_job("learn:6-8:brave") is None


def test_import_rejects_bad_files(app, client):
    _new_profile(client)
    worker = RecordingWorker()
    app.state.worker = worker
    bad_json = {"file": ("b.json", b"{not json", "application/json")}
    r = client.post("/api/parent/import", files=bad_json, data={"confirm": "true"})
    assert r.status_code == 400 and r.json()["detail"] == "invalid_json"
    assert worker.calls == []  # rejected before the worker is touched
    wrong = {"file": ("b.json", json.dumps({"format": "something-else", "version": 1}), "application/json")}
    r = client.post("/api/parent/import", files=wrong, data={"confirm": "true"})
    assert r.status_code == 400 and r.json()["detail"].startswith("invalid_backup")
    assert worker.calls == ["pause_and_drain", "resume"] and worker.paused is False  # resumed after the error
    assert len(client.get("/api/parent/profiles").json()) == 1


def test_import_answers_busy_when_running_jobs_do_not_finish(app, client, monkeypatch):
    _new_profile(client)
    backup = client.get("/api/parent/export").json()
    worker = RecordingWorker(drained=False)  # pause_and_drain timed out
    app.state.worker = worker
    imported: list[dict] = []
    monkeypatch.setattr(parent_api, "import_backup", lambda repo_, blobs_, data_: imported.append(data_))
    files = {"file": ("b.json", json.dumps(backup), "application/json")}
    r = client.post("/api/parent/import", files=files, data={"confirm": "true"})
    assert r.status_code == 503 and r.json() == {"detail": "busy"}
    assert imported == []
    assert worker.calls == ["pause_and_drain", "resume"] and worker.paused is False


def test_import_waits_for_the_real_worker_and_resumes_it(app, client):
    _new_profile(client)
    backup = client.get("/api/parent/export").json()
    worker = app.state.worker  # the real Worker (not running: start_worker=False), idle → drains at once
    files = {"file": ("b.json", json.dumps(backup), "application/json")}
    r = client.post("/api/parent/import", files=files, data={"confirm": "true"})
    assert r.status_code == 200, r.text
    assert worker.paused is False and worker.in_flight == 0


@pytest.mark.parametrize(
    "crafted_key",
    [
        "../x",  # LocalBlobStore refuses this key with ValueError
        "images/6-8/" + "b" * 5000 + ".webp",  # the filesystem refuses this name with OSError (ENAMETOOLONG)
    ],
    ids=["traversal", "name-too-long"],
)
def test_import_treats_an_unsafe_image_key_as_missing(client, repo, crafted_key):
    _ready(repo, "brave", pool=6, image_key="images/6-8/brave-v1.webp", image_status="ready",
           draft_version=2)                                         # a regeneration was in flight at export time
    repo.save_content(WordContent(word="calm", band="6-8"))         # pending, no card: must be re-enqueued
    backup = client.get("/api/parent/export").json()
    assert backup["contents"][0]["word"] == "brave"
    backup["contents"][0]["image_key"] = crafted_key
    files = {"file": ("b.json", json.dumps(backup), "application/json")}
    r = client.post("/api/parent/import", files=files, data={"confirm": "true"})
    assert r.status_code == 200, r.text
    counts = r.json()["counts"]
    assert (counts["images_missing"], counts["jobs_requeued"]) == (1, 1)
    brave = repo.get_content("6-8", "brave")
    assert (brave.status, brave.image_status, brave.image_key) == ("ready", "none", None)
    assert brave.draft_version is None                              # every later import step still ran
    assert repo.get_job("learn:6-8:calm").status == "pending"


def test_import_requeues_a_picture_that_was_still_being_drawn(client, repo):
    _ready(repo, "brave", pool=6, image_status="pending")       # ready, but its picture was still being drawn at export
    repo.enqueue_job("image", "6-8", "brave", 1, [])
    _ready(repo, "calm", pool=6, image_status="ready")          # picture finished: nothing to re-queue
    backup = client.get("/api/parent/export").json()
    files = {"file": ("b.json", json.dumps(backup), "application/json")}
    r = client.post("/api/parent/import", files=files, data={"confirm": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["jobs_requeued"] == 1
    brave = repo.get_content("6-8", "brave")
    assert (brave.status, brave.image_status) == ("ready", "pending")   # the picture is still in progress
    job = repo.get_job("image:6-8:brave")
    assert (job.status, job.target_version, job.chain) == ("pending", 1, [])
    assert repo.get_job("image:6-8:calm") is None
    assert repo.job_counts()["pending"] == 1

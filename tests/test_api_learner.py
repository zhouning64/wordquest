from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.ai.images.process import image_key
from app.api.learner import eligible_words
from app.config import Settings
from app.main import create_app
from app.models import (
    TIER,
    AnswerEvent,
    LearnCard,
    Profile,
    ProfileSettings,
    Question,
    Sense,
    Session,
    WordContent,
    WordList,
    new_id,
)
from app.storage.base import Repository
from app.storage.local_blobs import LocalBlobStore
from app.storage.sqlite_repo import SqliteRepository

TODAY = "2026-10-07"
BAND = "6-8"
SITE_CODE = "learner-site-code"
POOL_TYPES = ["meaning", "pick_word", "fill_blank", "fill_blank", "usage", "scenario", "synonym", "antonym", "spell_it", "word_parts"]
DEFAULT_BREAK = ProfileSettings().break_message


def card_for(word: str) -> LearnCard:
    return LearnCard(
        pos="adjective",
        short_def=f"a short meaning of {word}",
        kid_def=f"a longer, kid-friendly meaning of {word}",
        senses=[Sense(pos="adjective", definition=f"the meaning of {word}", example=f"The {word} kid smiled.")],
        examples=[f"The {word} dog barked.", f"A {word} answer helps.", f"My {word} friend waved.", f"Be {word} today."],
        synonyms=["plain"],
        antonyms=["fancy"],
    )


def pool_for(word: str, version: int = 1) -> list[Question]:
    questions = []
    for i, qtype in enumerate(POOL_TYPES):
        common = dict(
            id=f"{word}-q{i}",
            word=word,
            band=BAND,
            content_version=version,
            type=qtype,
            tier=TIER[qtype],
            explanation=f"Because {word} fits.",
            verified=True,
            created_at=f"2026-10-01T00:00:{i:02d}Z",
        )
        if qtype == "spell_it":
            questions.append(Question(**common, prompt="Type the word: The ___ cat purred. (means: test cue)", accepted_answers=[word]))
        else:
            questions.append(
                Question(
                    **common,
                    prompt=f"{qtype} question {i} about {word}?",
                    choices=[f"{word} answer", "wrong one", "wrong two", "wrong three"],
                    answer_index=0,
                )
            )
    return questions


def add_word(repo: Repository, word: str, *, status: str = "ready", image_key: str | None = None, image_status: str = "none") -> None:
    ready = status == "ready"
    repo.save_content(
        WordContent(
            word=word,
            band=BAND,
            status=status,
            card=card_for(word) if ready else None,
            image_key=image_key,
            image_status=image_status,
            model="test-model",
            generated_at="2026-10-01T00:00:00Z",
        )
    )
    if ready:
        repo.add_questions(BAND, word, 1, pool_for(word))


def add_profile(repo: Repository, profile_id: str, name: str, lists: list[list[str]], *,
                avatar: str = "🦊", created_at: str = "2026-10-01T00:00:00Z", **settings) -> Profile:
    list_ids = []
    for i, words in enumerate(lists):
        word_list = WordList(id=f"{profile_id}-list{i}", name=f"List {i}", words=words,
                             created_at=created_at, updated_at=created_at)
        repo.save_list(word_list)
        list_ids.append(word_list.id)
    profile = Profile(id=profile_id, name=name, avatar=avatar, band=BAND, list_ids=list_ids,
                      settings=ProfileSettings(**settings), created_at=created_at)
    repo.save_profile(profile)
    return profile


def set_progress(repo: Repository, profile_id: str, states: dict[str, dict]) -> None:
    """Writes WordProgress through apply_events (the Repository has no direct progress setter)."""
    sid = "seed" + new_id()
    repo.save_session(Session(id=sid, profile_id=profile_id, mode="normal", local_date="2026-10-01",
                              started_at="2026-10-01T00:00:00Z", planned_minutes=15))
    events = [
        AnswerEvent(client_event_id=f"{sid}-{i:04d}", word=word, kind="learn_open", local_date="2026-10-01",
                    at="2026-10-01T00:00:00Z", session_id=sid, profile_id=profile_id)
        for i, word in enumerate(states)
    ]

    def write(session, progress, new_events) -> None:
        for word, fields in states.items():
            for name, value in fields.items():
                setattr(progress[word], name, value)

    repo.apply_events(sid, events, write)


def ev(cid: str, word: str, kind: str, *, at: str, correct: bool | None = None, question: dict | None = None,
       local_date: str = TODAY) -> dict:
    event = {"client_event_id": cid, "word": word, "kind": kind, "local_date": local_date, "at": at, "ms": 1500}
    if correct is not None:
        event["correct"] = correct
    if question is not None:
        event["question_id"] = question["id"]
        event["question_type"] = question["type"]
    return event


def question_for(payload: dict, word: str) -> dict:
    return next(item["question"] for item in payload["queue"] if item["kind"] == "question" and item["word"] == word)


def make_app(tmp_path, *, site_code: str = SITE_CODE) -> SimpleNamespace:
    settings = Settings(
        _env_file=None,
        cerebras_api_key="",
        image_provider="none",
        site_access_code=site_code,
        parent_passcode="",
        secret_key="learner-test-secret",
        data_dir=tmp_path / "data",
    )
    repo = SqliteRepository(tmp_path / "wq.db")
    blobs = LocalBlobStore(tmp_path / "data")
    app = create_app(settings, repo=repo, blobs=blobs, start_worker=False, seed=False)
    return SimpleNamespace(app=app, repo=repo, blobs=blobs)


@pytest.fixture
def env(tmp_path):
    built = make_app(tmp_path)
    with TestClient(built.app) as client:
        assert client.post("/api/auth/site", json={"code": SITE_CODE}).status_code == 200
        built.client = client
        yield built


@pytest.fixture
def three_new_words(env):
    for word in ("candid", "frugal", "mellow"):
        add_word(env.repo, word)
    add_profile(env.repo, "p1", "Ava", [["candid", "frugal", "mellow"]])
    return env


def start(env, profile_id: str = "p1", mode: str = "normal") -> dict:
    r = env.client.post(f"/api/profiles/{profile_id}/sessions", json={"mode": mode, "local_date": TODAY})
    assert r.status_code == 200, r.text
    return r.json()


# --- access ----------------------------------------------------------------------------------


def test_learner_routes_require_the_site_cookie(env):
    add_profile(env.repo, "p1", "Ava", [])
    env.client.cookies.clear()
    for method, path, body in [
        ("get", "/api/profiles", None),
        ("get", f"/api/profiles/p1/home?local_date={TODAY}", None),
        ("post", "/api/profiles/p1/sessions", {"mode": "normal", "local_date": TODAY}),
        ("post", "/api/sessions/abc/events", {"events": []}),
        ("post", "/api/sessions/abc/finish", {"active_minutes": 1}),
    ]:
        r = env.client.request(method.upper(), path, json=body)
        assert r.status_code == 401, path
        assert r.json() == {"detail": "access_code_required"}


def test_learner_routes_are_open_when_no_site_code_is_set(tmp_path):
    built = make_app(tmp_path, site_code="")
    add_profile(built.repo, "p1", "Ava", [])
    with TestClient(built.app) as client:
        assert client.get("/api/profiles").json() == [{"id": "p1", "name": "Ava", "avatar": "🦊"}]


# --- profiles and home -------------------------------------------------------------------------


def test_eligible_words_follow_list_order_without_duplicates(env):
    profile = add_profile(env.repo, "p1", "Ava", [["candid", "frugal"], ["frugal", "mellow", "candid"]])
    profile.list_ids.insert(1, "deleted-list")
    assert eligible_words(env.repo, profile) == ["candid", "frugal", "mellow"]


def test_profiles_lists_id_name_avatar_in_creation_order(env):
    add_profile(env.repo, "p2", "Zed", [], avatar="🐢", created_at="2026-10-02T00:00:00Z")
    add_profile(env.repo, "p1", "Ava", [], avatar="🦊", created_at="2026-10-01T00:00:00Z")
    r = env.client.get("/api/profiles")
    assert r.status_code == 200
    assert r.json() == [
        {"id": "p1", "name": "Ava", "avatar": "🦊"},
        {"id": "p2", "name": "Zed", "avatar": "🐢"},
    ]


def test_home_counts_over_current_list_words(env):
    for word in ("candid", "frugal", "bold", "mellow", "quirky"):
        add_word(env.repo, word)
    add_word(env.repo, "nimble", status="pending")
    add_word(env.repo, "zany", status="failed")
    add_profile(env.repo, "p1", "Ava", [["candid", "frugal", "bold"], ["frugal", "mellow", "nimble", "zany"]], session_minutes=20)
    set_progress(env.repo, "p1", {
        "candid": {"stage": 5, "due_date": "2026-11-01", "interval_days": 30, "last_graded_on": "2026-10-02"},
        "frugal": {"stage": 2, "due_date": TODAY, "interval_days": 3, "last_graded_on": "2026-10-04"},
        "bold": {"stage": 4, "due_date": "2026-10-06", "interval_days": 14, "last_graded_on": "2026-09-22"},
        "quirky": {"stage": 3, "due_date": TODAY, "interval_days": 7, "last_graded_on": "2026-09-30"},  # not in a list
    })
    r = env.client.get(f"/api/profiles/p1/home?local_date={TODAY}")
    assert r.status_code == 200
    assert r.json() == {
        "profile": {"id": "p1", "name": "Ava", "avatar": "🦊", "band": BAND, "session_minutes": 20,
                    "new_words_per_session": 5},
        "mastered": 1,
        "learning": 2,
        "new": 3,
        "due_today": 2,
        "ready_new": 1,
        "practice_eligible": 1,
        "preparing": {"ready": 4, "total": 6},
    }


def test_home_errors(env):
    assert env.client.get(f"/api/profiles/nobody/home?local_date={TODAY}").json() == {"detail": "profile_not_found"}
    add_profile(env.repo, "p1", "Ava", [])
    assert env.client.get("/api/profiles/p1/home?local_date=10/07/2026").status_code == 422
    assert env.client.get("/api/profiles/p1/home").status_code == 422


# --- starting sessions --------------------------------------------------------------------------


def test_start_session_payload_and_saved_session(env):
    add_word(env.repo, "candid", image_key="images/6-8/candid-v1.webp", image_status="ready")
    add_word(env.repo, "frugal", image_key="images/6-8/frugal-v1.webp", image_status="failed")
    add_word(env.repo, "mellow")
    add_profile(env.repo, "p1", "Ava", [["candid", "frugal", "mellow"]], session_minutes=20)

    data = start(env)

    assert isinstance(data["session_id"], str) and data["session_id"]
    assert data["mode"] == "normal"
    assert data["local_date"] == TODAY
    assert data["empty_reason"] is None
    assert data["preparing"] == {"ready": 3, "total": 3}
    assert data["settings"]["session_minutes"] == 20
    assert {item["word"] for item in data["queue"]} == {"candid", "frugal", "mellow"}
    assert {item["word"] for item in data["queue"] if item["kind"] == "intro"} == {"candid", "frugal", "mellow"}
    assert set(data["words"]) == {"candid", "frugal", "mellow"}
    assert all("image_key" not in entry for entry in data["words"].values())
    assert data["words"]["candid"]["image_url"] == "/media/images/6-8/candid-v1.webp"
    assert data["words"]["frugal"]["image_url"] is None  # image failed: emoji fallback
    assert data["words"]["mellow"]["image_url"] is None

    saved = env.repo.get_session(data["session_id"])
    assert saved is not None
    assert saved.profile_id == "p1"
    assert saved.mode == "normal"
    assert saved.local_date == TODAY
    assert saved.planned_minutes == 20
    assert saved.finished_at is None
    assert set(saved.words) == {"candid", "frugal", "mellow"}


def test_start_session_errors(env):
    r = env.client.post("/api/profiles/nobody/sessions", json={"mode": "normal", "local_date": TODAY})
    assert r.status_code == 404
    assert r.json() == {"detail": "profile_not_found"}
    add_profile(env.repo, "p1", "Ava", [])
    assert env.client.post("/api/profiles/p1/sessions", json={"mode": "turbo", "local_date": TODAY}).status_code == 422
    assert env.client.post("/api/profiles/p1/sessions", json={"mode": "normal", "local_date": "today"}).status_code == 422


def test_nothing_ready_returns_reason_and_saves_no_session(env):
    add_word(env.repo, "nimble", status="pending")
    add_profile(env.repo, "p1", "Ava", [["nimble"]])
    data = start(env)
    assert data["queue"] == []
    assert data["empty_reason"] == "preparing"
    assert data["preparing"] == {"ready": 0, "total": 1}
    assert data["session_id"] is None
    assert env.repo.list_sessions("p1", 10) == []


def test_topup_enqueued_when_75_percent_of_pool_seen(env):
    add_word(env.repo, "candid")
    add_word(env.repo, "frugal")
    add_profile(env.repo, "p1", "Ava", [["candid", "frugal"]])
    review = {"stage": 2, "due_date": TODAY, "interval_days": 3, "introduced_on": "2026-10-01", "last_graded_on": "2026-10-04"}
    set_progress(env.repo, "p1", {
        "candid": {**review, "seen_question_ids": [f"candid-q{i}" for i in range(8)]},  # 8 of 10 seen
        "frugal": {**review, "seen_question_ids": ["frugal-q0"]},                       # 1 of 10 seen
    })

    data = start(env)

    assert {item["word"] for item in data["queue"]} == {"candid", "frugal"}
    job = env.repo.get_job(f"topup:{BAND}:candid")
    assert job is not None
    assert job.kind == "topup"
    assert job.status == "pending"
    assert env.repo.get_job(f"topup:{BAND}:frugal") is None


def test_practice_mode_uses_shaky_words_only(env):
    for word in ("candid", "frugal", "bold", "mellow"):
        add_word(env.repo, word)
    add_profile(env.repo, "p1", "Ava", [["candid", "frugal", "bold", "mellow"]])
    set_progress(env.repo, "p1", {
        "candid": {"stage": 2, "due_date": "2026-10-20", "interval_days": 3, "last_graded_on": "2026-10-05"},
        "frugal": {"stage": 0, "due_date": TODAY, "interval_days": 1, "introduced_on": TODAY},
        "bold": {"stage": 5, "due_date": "2026-11-01", "interval_days": 30, "last_graded_on": "2026-10-02"},
    })

    data = start(env, mode="practice")

    assert data["mode"] == "practice"
    assert [item["kind"] for item in data["queue"]] == ["question"] * len(data["queue"])
    assert {item["word"] for item in data["queue"]} == {"candid"}
    assert env.repo.get_session(data["session_id"]).mode == "practice"


@pytest.mark.parametrize("word", ["in lieu of", "self-esteem", "o'clock"])
def test_phrase_and_punctuated_words_get_a_fetchable_image_url(env, word):
    key = image_key(BAND, word, 1)  # the real Task 12 key: spaces become "-", apostrophes and hyphens stay
    env.blobs.put(key, b"RIFF0000WEBPVP8 ", "image/webp")
    add_word(env.repo, word, image_key=key, image_status="ready")
    add_profile(env.repo, "p1", "Ava", [[word]])

    data = start(env)

    assert {item["word"] for item in data["queue"]} == {word}
    url = data["words"][word]["image_url"]
    assert url == env.blobs.url_for(key)
    r = env.client.get(url)
    assert r.status_code == 200
    assert r.content == b"RIFF0000WEBPVP8 "


# --- events ------------------------------------------------------------------------------------


def test_events_are_applied_to_progress_and_session(three_new_words):
    env = three_new_words
    data = start(env)
    sid = data["session_id"]
    q_candid = question_for(data, "candid")
    q_frugal = question_for(data, "frugal")
    events = [
        ev("intro-candid-1", "candid", "intro_seen", at="2026-10-07T15:00:00Z"),
        ev("answer-candid-1", "candid", "answer", correct=True, question=q_candid, at="2026-10-07T15:00:05Z"),
        ev("intro-frugal-1", "frugal", "intro_seen", at="2026-10-07T15:00:10Z"),
        ev("answer-frugal-1", "frugal", "answer", correct=False, question=q_frugal, at="2026-10-07T15:00:15Z"),
        ev("learn-frugal-1", "frugal", "learn_open", at="2026-10-07T15:00:20Z"),
    ]

    r = env.client.post(f"/api/sessions/{sid}/events", json={"events": events})

    assert r.status_code == 200
    assert sorted(r.json()["accepted"]) == sorted(e["client_event_id"] for e in events)
    progress = env.repo.get_progress("p1", ["candid", "frugal"])
    candid, frugal = progress["candid"], progress["frugal"]
    assert (candid.stage, candid.interval_days, candid.due_date, candid.last_graded_on) == (1, 1, "2026-10-08", TODAY)
    assert candid.introduced_on == TODAY
    assert (candid.seen, candid.correct) == (1, 1)
    assert candid.seen_question_ids == [q_candid["id"]]
    assert (frugal.stage, frugal.due_date, frugal.wrong) == (0, "2026-10-08", 1)
    session = env.repo.get_session(sid)
    assert (session.answered, session.correct, session.unsure) == (2, 1, 0)
    assert session.new_words == ["candid", "frugal"]
    assert session.stars_up == ["candid"]
    assert session.missed == ["frugal"]
    assert session.learn_opened == 1


def test_resent_events_are_accepted_but_applied_once(three_new_words):
    env = three_new_words
    data = start(env)
    sid = data["session_id"]
    q = question_for(data, "candid")
    first = [
        ev("intro-candid-1", "candid", "intro_seen", at="2026-10-07T15:00:00Z"),
        ev("answer-candid-1", "candid", "answer", correct=True, question=q, at="2026-10-07T15:00:05Z"),
    ]
    assert env.client.post(f"/api/sessions/{sid}/events", json={"events": first}).status_code == 200

    again = [first[1], ev("answer-candid-2", "candid", "answer", correct=True, question=q, at="2026-10-07T15:03:00Z")]
    r = env.client.post(f"/api/sessions/{sid}/events", json={"events": again})

    assert sorted(r.json()["accepted"]) == ["answer-candid-1", "answer-candid-2"]
    session = env.repo.get_session(sid)
    assert (session.answered, session.correct) == (2, 2)
    candid = env.repo.get_progress("p1", ["candid"])["candid"]
    assert candid.seen == 2
    assert candid.stage == 1  # the second answer today does not change the stage
    assert session.stars_up == ["candid"]


def test_two_devices_answering_the_same_word_on_the_same_day(env):
    """A sibling on the iPad and a parent on the Mac share one profile: the word's stage changes once, no answer is lost."""
    add_word(env.repo, "candid")
    add_profile(env.repo, "p1", "Ava", [["candid"]])
    set_progress(env.repo, "p1", {"candid": {"stage": 2, "due_date": TODAY, "interval_days": 3,
                                             "introduced_on": "2026-10-01", "last_graded_on": "2026-10-04"}})
    ipad, mac = start(env), start(env)
    assert ipad["session_id"] != mac["session_id"]
    q_ipad, q_mac = question_for(ipad, "candid"), question_for(mac, "candid")

    def upload(payload: dict, events: list[dict]) -> list[str]:
        r = env.client.post(f"/api/sessions/{payload['session_id']}/events", json={"events": events})
        assert r.status_code == 200, r.text
        return r.json()["accepted"]

    # Interleaved uploads: the iPad's correct answer arrives first, then the Mac's miss (answered a moment
    # earlier but uploaded later), then another iPad answer, then the Mac resends its batch.
    mac_miss = ev("mac-00001", "candid", "answer", correct=False, question=q_mac, at="2026-10-07T15:00:03Z")
    assert upload(ipad, [ev("ipad-0001", "candid", "answer", correct=True, question=q_ipad, at="2026-10-07T15:00:05Z")]) == ["ipad-0001"]
    assert upload(mac, [mac_miss]) == ["mac-00001"]
    assert upload(ipad, [ev("ipad-0002", "candid", "answer", correct=True, question=q_ipad, at="2026-10-07T15:02:00Z")]) == ["ipad-0002"]
    assert upload(mac, [mac_miss]) == ["mac-00001"]

    candid = env.repo.get_progress("p1", ["candid"])["candid"]
    assert (candid.stage, candid.interval_days, candid.due_date, candid.last_graded_on) == (3, 7, "2026-10-14", TODAY)
    assert (candid.seen, candid.correct, candid.wrong) == (3, 2, 1)
    stored = {e.client_event_id for e in env.repo.list_events("p1", TODAY)}
    assert stored == {"ipad-0001", "mac-00001", "ipad-0002"}
    ipad_session = env.repo.get_session(ipad["session_id"])
    mac_session = env.repo.get_session(mac["session_id"])
    assert (ipad_session.answered, ipad_session.stars_up) == (2, ["candid"])
    assert (mac_session.answered, mac_session.stars_up, mac_session.missed) == (1, [], ["candid"])


def test_event_errors(three_new_words):
    env = three_new_words
    r = env.client.post("/api/sessions/nope/events", json={"events": []})
    assert r.status_code == 404
    assert r.json() == {"detail": "session_not_found"}
    sid = start(env)["session_id"]
    assert env.client.post(f"/api/sessions/{sid}/events", json={"events": []}).json() == {"accepted": []}
    short_id = ev("short", "candid", "intro_seen", at="2026-10-07T15:00:00Z")
    assert env.client.post(f"/api/sessions/{sid}/events", json={"events": [short_id]}).status_code == 422
    bad_kind = ev("bad-kind-0001", "candid", "dance", at="2026-10-07T15:00:00Z")
    assert env.client.post(f"/api/sessions/{sid}/events", json={"events": [bad_kind]}).status_code == 422


# --- validation at the API boundary -------------------------------------------------------------


def upload(env, sid: str, events: list[dict]):
    return env.client.post(f"/api/sessions/{sid}/events", json={"events": events})


def nothing_applied(env, sid: str) -> None:
    assert env.repo.get_session(sid).answered == 0
    assert env.repo.list_events("p1", TODAY) == []
    assert "candid" not in env.repo.get_progress("p1", ["candid"])


@pytest.mark.parametrize("bad_day", ["today", "2026-1-07", "2026-13-01", "2026-02-30"])
def test_malformed_event_date_rejects_the_whole_batch(three_new_words, bad_day):
    env = three_new_words
    data = start(env)
    sid = data["session_id"]
    events = [
        ev("intro-candid-1", "candid", "intro_seen", at="2026-10-07T15:00:00Z"),
        ev("answer-candid-1", "candid", "answer", correct=True, question=question_for(data, "candid"),
           at="2026-10-07T15:00:05Z", local_date=bad_day),
    ]
    assert upload(env, sid, events).status_code == 422
    nothing_applied(env, sid)


@pytest.mark.parametrize("bad_at", ["today", "2026-10-07T15:00:00", "2026-10-07 15:00:05Z",
                                    "2026-13-07T15:00:00Z", "2026-10-07T25:00:00Z"])
def test_malformed_event_timestamp_rejects_the_whole_batch(three_new_words, bad_at):
    env = three_new_words
    sid = start(env)["session_id"]
    assert upload(env, sid, [ev("intro-candid-1", "candid", "intro_seen", at=bad_at)]).status_code == 422
    nothing_applied(env, sid)


@pytest.mark.parametrize("good_at", ["2026-10-07T15:00:05Z", "2026-10-07T15:00:05.5Z", "2026-10-07T15:00:05.123Z"])
def test_utc_timestamps_with_or_without_fractions_are_accepted(three_new_words, good_at):
    env = three_new_words
    sid = start(env)["session_id"]
    r = upload(env, sid, [ev("intro-candid-1", "candid", "intro_seen", at=good_at)])
    assert r.status_code == 200
    assert r.json() == {"accepted": ["intro-candid-1"]}


@pytest.mark.parametrize("day", ["2026-10-05", "2026-10-09"])  # two days before and after the session
def test_event_date_two_days_from_the_session_rejects_the_batch(three_new_words, day):
    env = three_new_words
    data = start(env)
    sid = data["session_id"]
    events = [
        ev("intro-candid-1", "candid", "intro_seen", at="2026-10-07T15:00:00Z"),
        ev("answer-candid-1", "candid", "answer", correct=True, question=question_for(data, "candid"),
           at="2026-10-07T15:00:05Z", local_date=day),
    ]
    r = upload(env, sid, events)
    assert r.status_code == 422
    assert r.json()["detail"] == "event_date_out_of_range"
    nothing_applied(env, sid)


@pytest.mark.parametrize("day", ["2026-10-06", "2026-10-08"])  # one day either side: midnight, clock skew
def test_event_date_one_day_from_the_session_is_accepted(three_new_words, day):
    env = three_new_words
    data = start(env)
    sid = data["session_id"]
    event = ev("answer-candid-1", "candid", "answer", correct=True, question=question_for(data, "candid"),
               at="2026-10-07T15:00:05Z", local_date=day)
    r = upload(env, sid, [event])
    assert r.status_code == 200
    assert r.json() == {"accepted": ["answer-candid-1"]}
    assert env.repo.get_session(sid).answered == 1


@pytest.mark.parametrize("field, value", [
    ("word", ""),
    ("word", "x" * 101),
    ("ms", -1),
    ("ms", 86_400_001),
    ("question_id", "q" * 65),
    ("question_type", "t" * 65),
])
def test_event_field_bounds_reject_the_whole_batch(three_new_words, field, value):
    env = three_new_words
    sid = start(env)["session_id"]
    good = ev("intro-candid-1", "candid", "intro_seen", at="2026-10-07T15:00:00Z")
    bad = ev("intro-frugal-1", "frugal", "intro_seen", at="2026-10-07T15:00:10Z")
    bad[field] = value
    assert upload(env, sid, [good, bad]).status_code == 422
    nothing_applied(env, sid)


@pytest.mark.parametrize("bad_day", ["2026-13-01", "2026-02-30"])
def test_impossible_calendar_dates_are_422_for_home_and_session_start(env, bad_day):
    add_profile(env.repo, "p1", "Ava", [])
    assert env.client.get(f"/api/profiles/p1/home?local_date={bad_day}").status_code == 422
    assert env.client.post("/api/profiles/p1/sessions", json={"mode": "normal", "local_date": bad_day}).status_code == 422
    assert env.repo.list_sessions("p1", 10) == []


def test_keyerror_inside_the_apply_step_is_a_500_not_a_missing_session(three_new_words, monkeypatch):
    env = three_new_words
    sid = start(env)["session_id"]

    def broken_apply(session, progress, events):
        raise KeyError("a word with no progress row")

    monkeypatch.setattr("app.api.learner.apply_events_to_state", broken_apply)
    client = TestClient(env.app, raise_server_exceptions=False)  # the session exists: this is not a 404
    assert client.post("/api/auth/site", json={"code": SITE_CODE}).status_code == 200
    r = client.post(f"/api/sessions/{sid}/events",
                    json={"events": [ev("intro-candid-1", "candid", "intro_seen", at="2026-10-07T15:00:00Z")]})
    assert r.status_code == 500
    assert env.repo.get_session(sid).answered == 0


# --- finish------------------------------------------------------------------------------------


def test_finish_returns_results_and_closes_the_session(three_new_words):
    env = three_new_words
    data = start(env)
    sid = data["session_id"]
    events = [
        ev("intro-candid-1", "candid", "intro_seen", at="2026-10-07T15:00:00Z"),
        ev("answer-candid-1", "candid", "answer", correct=True, question=question_for(data, "candid"), at="2026-10-07T15:00:05Z"),
        ev("intro-frugal-1", "frugal", "intro_seen", at="2026-10-07T15:00:10Z"),
        ev("unsure-frugal-1", "frugal", "unsure", question=question_for(data, "frugal"), at="2026-10-07T15:00:15Z"),
    ]
    assert env.client.post(f"/api/sessions/{sid}/events", json={"events": events}).status_code == 200

    r = env.client.post(f"/api/sessions/{sid}/finish", json={"active_minutes": 12})

    assert r.status_code == 200
    assert r.json() == {
        "accuracy": 50,
        "answered": 2,
        "new_words": ["candid", "frugal"],
        "stars_up": ["candid"],
        "keep_practicing": [{"word": "frugal", "stage": 0}],
        "break_reminder": True,
        "break_message": DEFAULT_BREAK,
    }
    session = env.repo.get_session(sid)
    assert session.finished_at is not None
    assert session.active_minutes == 12
    assert session.unsure == 1


def test_keep_practicing_lists_up_to_5_missed_words_lowest_stage_first(env):
    add_profile(env.repo, "p1", "Ava", [], break_reminder=False, break_message="Stretch your legs!")
    set_progress(env.repo, "p1", {
        "able": {"stage": 3}, "brisk": {"stage": 1}, "calm": {"stage": 2}, "eager": {"stage": 4}, "frail": {"stage": 1},
    })
    env.repo.save_session(Session(
        id="s-keep", profile_id="p1", mode="normal", local_date=TODAY, started_at="2026-10-07T15:00:00Z",
        planned_minutes=15, answered=8, correct=1, missed=["able", "brisk", "calm", "dense", "eager", "frail"],
    ))

    results = env.client.post("/api/sessions/s-keep/finish", json={"active_minutes": 9}).json()

    assert results["keep_practicing"] == [
        {"word": "dense", "stage": 0},
        {"word": "brisk", "stage": 1},
        {"word": "frail", "stage": 1},
        {"word": "calm", "stage": 2},
        {"word": "able", "stage": 3},
    ]
    assert results["accuracy"] == 13  # 12.5% rounds half up
    assert results["break_reminder"] is False
    assert results["break_message"] == "Stretch your legs!"


def test_finish_errors(env):
    r = env.client.post("/api/sessions/nope/finish", json={"active_minutes": 3})
    assert r.status_code == 404
    assert r.json() == {"detail": "session_not_found"}
    add_profile(env.repo, "p1", "Ava", [])
    env.repo.save_session(Session(id="s1", profile_id="p1", mode="normal", local_date=TODAY,
                                  started_at="2026-10-07T15:00:00Z", planned_minutes=15))
    assert env.client.post("/api/sessions/s1/finish", json={"active_minutes": -1}).status_code == 422
    assert env.client.post("/api/sessions/s1/finish", json={"active_minutes": 0}).json()["accuracy"] == 0

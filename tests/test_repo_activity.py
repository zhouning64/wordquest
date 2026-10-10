"""Task 5: progress, sessions, apply_events, list_events, usage, auth failures, export/import."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from app.models import (
    TIER,
    AnswerEvent,
    LearnCard,
    Profile,
    Question,
    Sense,
    Session,
    WordContent,
    WordList,
    WordProgress,
)
from app.storage.sqlite_repo import SqliteRepository

DAY = "2026-10-07"


@pytest.fixture
def repo(tmp_path: Path):
    r = SqliteRepository(tmp_path / "wq.db")
    yield r
    r.close()


def make_session(sid: str = "s1", profile_id: str = "p1", started_at: str = "2026-10-07T10:00:00Z",
                 mode: str = "normal") -> Session:
    return Session(id=sid, profile_id=profile_id, mode=mode, local_date=started_at[:10],
                   started_at=started_at, planned_minutes=15, words=["brave", "calm"])


def make_event(n: int, word: str = "brave", *, sid: str = "s1", profile_id: str = "p1", kind: str = "answer",
               correct: bool | None = True, local_date: str = DAY, at: str | None = None) -> AnswerEvent:
    return AnswerEvent(
        client_event_id=f"evt-{sid}-{n:04d}", word=word, question_id=f"q{n}", question_type="meaning",
        kind=kind, correct=correct, ms=1200, local_date=local_date,
        at=at or f"{local_date}T10:00:{n:02d}Z", session_id=sid, profile_id=profile_id,
    )


def counting_apply(session: Session, progress: dict[str, WordProgress], events: list[AnswerEvent]) -> None:
    """A tiny stand-in for srs.apply_events_to_state: counts answers."""
    for e in events:
        p = progress[e.word]
        p.seen += 1
        if e.correct:
            p.correct += 1
        session.answered += 1


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, WordProgress]]] = []

    def __call__(self, session, progress, events) -> None:
        self.calls.append(([e.client_event_id for e in events], {w: p.model_copy() for w, p in progress.items()}))
        counting_apply(session, progress, events)


def make_card(word: str) -> LearnCard:
    return LearnCard(
        pos="adjective", short_def=f"short {word}", kid_def=f"kid {word}",
        senses=[Sense(pos="adjective", definition=f"def {word}", example=f"A {word} cat.")],
        examples=[f"So {word}.", f"Very {word}.", f"Be {word}.", f"Not {word}."],
    )


def populate(repo: SqliteRepository) -> None:
    repo.save_list(WordList(id="l1", name="Week 1", words=["brave", "calm"], created_at="2026-10-01T09:00:00Z"))
    repo.save_profile(Profile(id="p1", name="Ada", band="6-8", list_ids=["l1"], created_at="2026-10-01T09:00:00Z"))
    repo.save_profile(Profile(id="p2", name="Bo", band="3-5", created_at="2026-10-02T09:00:00Z"))
    repo.save_content(WordContent(word="brave", band="6-8", status="ready", card=make_card("brave"),
                                  image_key="images/6-8/brave-v1.webp", image_status="ready"))
    repo.save_content(WordContent(word="calm", band="6-8", status="failed", error="timeout"))
    repo.add_questions("6-8", "brave", 1, [
        Question(id="q1", word="brave", band="6-8", content_version=1, type="meaning", tier=TIER["meaning"],
                 prompt="What does brave mean?", choices=["bold", "sad", "slow", "tiny"], answer_index=0,
                 verified=True, created_at="2026-10-01T09:00:00Z"),
        Question(id="q2", word="brave", band="6-8", content_version=1, type="spell_it", tier=TIER["spell_it"],
                 prompt="She was ___ in the storm. (means: not afraid)", accepted_answers=["brave"],
                 verified=True, created_at="2026-10-01T09:00:01Z"),
    ])
    repo.save_session(make_session("s1", "p1"))
    repo.apply_events("s1", [make_event(1), make_event(2, "calm", correct=False)], counting_apply)
    repo.enqueue_job("learn", "6-8", "calm", 1, ["questions", "image"])
    repo.incr_ai_calls("2026-10-07")


# ---- progress and sessions ------------------------------------------------------------------

def test_get_progress_returns_only_stored_words(repo):
    repo.save_session(make_session())
    repo.apply_events("s1", [make_event(1, "brave"), make_event(2, "calm")], counting_apply)
    got = repo.get_progress("p1", ["calm", "ghost", "brave"])
    assert list(got) == ["calm", "brave"]
    assert got["brave"].seen == 1 and got["brave"].profile_id == "p1"
    assert repo.get_progress("p2", ["brave"]) == {}
    assert [p.word for p in repo.list_progress("p1")] == ["brave", "calm"]
    assert repo.list_progress("p2") == []


def test_session_round_trip_update_and_missing(repo):
    s = make_session()
    repo.save_session(s)
    assert repo.get_session("s1") == s
    repo.save_session(s.model_copy(update={"finished_at": "2026-10-07T10:15:00Z", "active_minutes": 14}))
    got = repo.get_session("s1")
    assert got.finished_at == "2026-10-07T10:15:00Z" and got.active_minutes == 14
    assert repo.get_session("nope") is None


def test_list_sessions_newest_first_with_limit(repo):
    repo.save_session(make_session("s-old", started_at="2026-10-05T10:00:00Z"))
    repo.save_session(make_session("s-new", started_at="2026-10-07T10:00:00Z"))
    repo.save_session(make_session("s-mid", started_at="2026-10-06T10:00:00Z"))
    repo.save_session(make_session("s-other", profile_id="p2", started_at="2026-10-08T10:00:00Z"))
    assert [s.id for s in repo.list_sessions("p1", 10)] == ["s-new", "s-mid", "s-old"]
    assert [s.id for s in repo.list_sessions("p1", 2)] == ["s-new", "s-mid"]
    assert repo.list_sessions("p1", 0) == []


# ---- apply_events ---------------------------------------------------------------------------

def test_apply_events_applies_new_events_in_one_go(repo):
    repo.save_session(make_session())
    rec = Recorder()
    accepted = repo.apply_events("s1", [make_event(1, "brave"), make_event(2, "calm", correct=False)], rec)
    assert accepted == ["evt-s1-0001", "evt-s1-0002"]
    assert len(rec.calls) == 1
    event_ids, progress_seen = rec.calls[0]
    assert event_ids == ["evt-s1-0001", "evt-s1-0002"]
    assert progress_seen == {"brave": WordProgress(profile_id="p1", word="brave"),
                             "calm": WordProgress(profile_id="p1", word="calm")}
    assert repo.get_session("s1").answered == 2
    prog = repo.get_progress("p1", ["brave", "calm"])
    assert (prog["brave"].seen, prog["brave"].correct) == (1, 1)
    assert (prog["calm"].seen, prog["calm"].correct) == (1, 0)
    assert [e.client_event_id for e in repo.list_events("p1", DAY)] == ["evt-s1-0001", "evt-s1-0002"]


def test_apply_events_is_idempotent_and_passes_only_new_events(repo):
    repo.save_session(make_session())
    repo.apply_events("s1", [make_event(1, "brave")], counting_apply)
    rec = Recorder()
    accepted = repo.apply_events("s1", [make_event(1, "brave"), make_event(3, "calm")], rec)
    assert accepted == ["evt-s1-0001", "evt-s1-0003"]
    assert rec.calls[0][0] == ["evt-s1-0003"]
    assert list(rec.calls[0][1]) == ["calm"]  # progress only for words of NEW events
    assert repo.get_progress("p1", ["brave"])["brave"].seen == 1
    assert repo.get_session("s1").answered == 2
    assert len(repo.list_events("p1", DAY)) == 2


def test_apply_events_with_only_known_ids_writes_nothing(repo):
    repo.save_session(make_session())
    repo.apply_events("s1", [make_event(1)], counting_apply)
    rec = Recorder()
    assert repo.apply_events("s1", [make_event(1), make_event(1)], rec) == ["evt-s1-0001"]
    assert rec.calls == []
    assert repo.get_session("s1").answered == 1


def test_apply_events_dedupes_ids_within_one_batch(repo):
    repo.save_session(make_session())
    rec = Recorder()
    assert repo.apply_events("s1", [make_event(1), make_event(1)], rec) == ["evt-s1-0001"]
    assert rec.calls[0][0] == ["evt-s1-0001"]
    assert repo.get_session("s1").answered == 1


def test_apply_events_loads_existing_progress(repo):
    repo.save_session(make_session())
    repo.apply_events("s1", [make_event(1, "brave")], counting_apply)
    rec = Recorder()
    repo.apply_events("s1", [make_event(2, "brave")], rec)
    assert rec.calls[0][1]["brave"].seen == 1
    assert repo.get_progress("p1", ["brave"])["brave"].seen == 2


def test_apply_events_saves_progress_added_by_apply_fn(repo):
    repo.save_session(make_session())

    def add_extra(session, progress, events):
        progress["extra"] = WordProgress(profile_id="p1", word="extra", stage=2)

    repo.apply_events("s1", [make_event(1, "brave")], add_extra)
    assert repo.get_progress("p1", ["extra"])["extra"].stage == 2


def test_apply_events_unknown_session_raises_key_error(repo):
    with pytest.raises(KeyError):
        repo.apply_events("nope", [make_event(1, sid="nope")], counting_apply)
    assert repo.list_events("p1", "2000-01-01") == []


def test_apply_events_rejects_events_of_another_session(repo):
    repo.save_session(make_session("s1"))
    repo.save_session(make_session("s2"))
    with pytest.raises(ValueError):
        repo.apply_events("s1", [make_event(1, sid="s1"), make_event(2, sid="s2")], counting_apply)
    with pytest.raises(ValueError):
        repo.apply_events("s1", [make_event(3, sid="s1", profile_id="p2")], counting_apply)
    assert repo.list_events("p1", "2000-01-01") == []
    assert repo.get_session("s1").answered == 0


def test_apply_events_is_atomic_when_apply_fn_fails(repo):
    repo.save_session(make_session())

    def explode(session, progress, events):
        counting_apply(session, progress, events)  # mutate first, then fail
        raise RuntimeError("bug in apply_fn")

    with pytest.raises(RuntimeError, match="bug in apply_fn"):
        repo.apply_events("s1", [make_event(1, "brave"), make_event(2, "calm")], explode)
    assert repo.list_events("p1", "2000-01-01") == []
    assert repo.get_progress("p1", ["brave", "calm"]) == {}
    assert repo.get_session("s1").answered == 0
    # the same events can be applied afterwards, exactly once
    assert repo.apply_events("s1", [make_event(1, "brave"), make_event(2, "calm")], counting_apply) == [
        "evt-s1-0001", "evt-s1-0002"]
    assert repo.get_session("s1").answered == 2


def test_concurrent_apply_events_on_one_profile_lose_no_updates(repo):
    """Two devices on the same profile: both sessions write progress for the same word at once."""
    repo.save_session(make_session("s1"))
    repo.save_session(make_session("s2"))
    errors: list[BaseException] = []

    def device(sid: str) -> None:
        try:
            for n in range(25):
                repo.apply_events(sid, [make_event(n, "brave", sid=sid)], counting_apply)
        except BaseException as exc:  # surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=device, args=(sid,)) for sid in ("s1", "s2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == []
    assert repo.get_progress("p1", ["brave"])["brave"].seen == 50
    assert repo.get_session("s1").answered == 25
    assert repo.get_session("s2").answered == 25


def test_list_events_filters_by_profile_and_since_date_in_order(repo):
    repo.save_session(make_session("s1", "p1"))
    repo.save_session(make_session("s9", "p2"))
    repo.apply_events("s1", [
        make_event(1, local_date="2026-10-07", at="2026-10-07T10:00:05Z"),
        make_event(2, local_date="2026-10-05", at="2026-10-05T10:00:00Z"),
        make_event(3, local_date="2026-10-07", at="2026-10-07T10:00:01Z"),
        make_event(4, local_date="2026-10-06", at="2026-10-06T10:00:00Z"),
    ], counting_apply)
    repo.apply_events("s9", [make_event(5, sid="s9", profile_id="p2")], counting_apply)
    got = repo.list_events("p1", "2026-10-06")
    assert [e.client_event_id for e in got] == ["evt-s1-0004", "evt-s1-0003", "evt-s1-0001"]
    assert got[0] == make_event(4, local_date="2026-10-06", at="2026-10-06T10:00:00Z")
    assert len(repo.list_events("p1", "2026-10-01")) == 4
    assert repo.list_events("p1", "2026-10-08") == []


def test_delete_profile_cascades_through_public_api(repo):
    repo.save_profile(Profile(id="p1", name="Ada", band="6-8"))
    repo.save_profile(Profile(id="p2", name="Bo", band="6-8"))
    repo.save_session(make_session("s1", "p1"))
    repo.save_session(make_session("s2", "p2"))
    repo.apply_events("s1", [make_event(1, sid="s1")], counting_apply)
    repo.apply_events("s2", [make_event(1, sid="s2", profile_id="p2")], counting_apply)
    repo.delete_profile("p1")
    assert repo.get_session("s1") is None
    assert repo.list_progress("p1") == []
    assert repo.list_events("p1", "2000-01-01") == []
    assert repo.get_session("s2") is not None
    assert len(repo.list_progress("p2")) == 1
    assert len(repo.list_events("p2", "2000-01-01")) == 1


# ---- usage and auth failures ----------------------------------------------------------------

def test_ai_call_counter_per_utc_date(repo):
    assert repo.get_ai_calls("2026-10-07") == 0
    assert [repo.incr_ai_calls("2026-10-07") for _ in range(3)] == [1, 2, 3]
    assert repo.incr_ai_calls("2026-10-08") == 1
    assert repo.get_ai_calls("2026-10-07") == 3
    assert repo.get_ai_calls("2026-10-08") == 1


def test_auth_failures_count_per_window_and_clear_per_scope_and_ip(repo):
    w1, w2 = "2026-10-07T10:00:00Z", "2026-10-07T10:15:00Z"
    assert [repo.incr_auth_failure("site", "10.0.0.5", w1) for _ in range(3)] == [1, 2, 3]
    assert repo.incr_auth_failure("site", "10.0.0.5", w2) == 1
    assert repo.incr_auth_failure("parent", "10.0.0.5", w1) == 1
    assert repo.incr_auth_failure("site", "10.0.0.55", w1) == 1
    repo.clear_auth_failures("site", "10.0.0.5")
    assert repo.incr_auth_failure("site", "10.0.0.5", w1) == 1   # both windows were cleared
    assert repo.incr_auth_failure("site", "10.0.0.5", w2) == 1
    assert repo.incr_auth_failure("parent", "10.0.0.5", w1) == 2  # other scope untouched
    assert repo.incr_auth_failure("site", "10.0.0.55", w1) == 2   # similar-prefix ip untouched


# ---- export / import ------------------------------------------------------------------------

def test_export_has_exact_shape_and_excludes_jobs(repo):
    populate(repo)
    data = repo.export_all()
    assert list(data) == ["format", "version", "profiles", "lists", "contents", "questions",
                          "progress", "sessions", "events"]
    assert data["format"] == "wordquest-backup" and data["version"] == 1
    assert [p["id"] for p in data["profiles"]] == ["p1", "p2"]
    assert [c["word"] for c in data["contents"]] == ["brave", "calm"]
    assert [q["id"] for q in data["questions"]] == ["q1", "q2"]
    assert [(p["word"], p["seen"]) for p in data["progress"]] == [("brave", 1), ("calm", 1)]
    assert data["sessions"][0]["answered"] == 2
    assert [e["client_event_id"] for e in data["events"]] == ["evt-s1-0001", "evt-s1-0002"]
    assert json.loads(json.dumps(data)) == data  # plain JSON types only


def test_export_import_round_trip_replaces_data_and_clears_jobs(repo, tmp_path):
    populate(repo)
    exported = json.loads(json.dumps(repo.export_all()))

    other = SqliteRepository(tmp_path / "other.db")
    other.save_profile(Profile(id="stale", name="Old", band="9-12"))
    other.enqueue_job("topup", "9-12", "abate", 1, [])
    other.incr_ai_calls("2026-10-07")
    other.import_all(exported)

    assert other.export_all() == exported
    assert other.get_profile("stale") is None
    assert other.job_counts() == {"pending": 0, "running": 0, "done": 0, "failed": 0}
    assert other.get_ai_calls("2026-10-07") == 1  # usage counters are not part of a backup
    assert [q.id for q in other.get_pool("6-8", "brave")] == ["q1", "q2"]
    assert other.get_content("6-8", "brave").image_key == "images/6-8/brave-v1.webp"
    # imported event ids keep apply_events idempotent
    rec = Recorder()
    other.apply_events("s1", [make_event(1)], rec)
    assert rec.calls == []
    other.close()


def test_progress_daily_count_fields_load_from_old_backups_and_round_trip(repo, tmp_path):
    populate(repo)
    exported = json.loads(json.dumps(repo.export_all()))
    row = next(x for x in exported["progress"] if x["word"] == "brave")
    assert (row["graded_today"], row["last_graded_at"]) == (0, None)
    # A backup written before these fields existed still imports, with the defaults.
    old = json.loads(json.dumps(exported))
    for x in old["progress"]:
        del x["graded_today"], x["last_graded_at"]
    other = SqliteRepository(tmp_path / "other.db")
    other.import_all(old)
    assert other.export_all() == exported
    # Set values survive export → import.
    row.update(last_graded_on="2026-10-07", graded_today=2, last_graded_at="2026-10-07T17:00:00.250Z")
    other.import_all(exported)
    brave = other.get_progress("p1", ["brave"])["brave"]
    assert (brave.graded_today, brave.last_graded_at) == (2, "2026-10-07T17:00:00.250Z")
    assert other.export_all() == exported
    other.close()


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.update(format="something-else"), "format"),
    (lambda d: d.update(version=2), "version"),
    (lambda d: d.pop("events"), "events"),
    (lambda d: d["profiles"].append({"id": "bad", "name": "X", "band": "1-2"}), "band"),
])
def test_import_rejects_bad_backups_without_changing_anything(repo, tmp_path, mutate, message):
    populate(repo)
    data = json.loads(json.dumps(repo.export_all()))
    mutate(data)
    target = SqliteRepository(tmp_path / "target.db")
    target.save_profile(Profile(id="keep", name="Keep", band="6-8"))
    target.enqueue_job("learn", "6-8", "keep", 1, [])
    with pytest.raises(ValueError, match=message):
        target.import_all(data)
    assert [p.id for p in target.list_profiles()] == ["keep"]
    assert target.job_counts()["pending"] == 1
    target.close()


def test_import_with_duplicate_event_ids_is_a_value_error(repo, tmp_path):
    populate(repo)
    data = json.loads(json.dumps(repo.export_all()))
    data["events"].append(dict(data["events"][0]))
    target = SqliteRepository(tmp_path / "target.db")
    target.save_profile(Profile(id="keep", name="Keep", band="6-8"))
    with pytest.raises(ValueError, match="conflicting"):
        target.import_all(data)
    assert [p.id for p in target.list_profiles()] == ["keep"]
    target.close()


@pytest.mark.parametrize("collection", [
    "profiles", "lists", "contents", "questions", "progress", "sessions", "events",
])
def test_import_rejects_a_duplicate_primary_key_in_every_collection(repo, tmp_path, collection):
    populate(repo)
    data = json.loads(json.dumps(repo.export_all()))
    data[collection].append(dict(data[collection][0]))  # a second record with the same primary key
    target = SqliteRepository(tmp_path / "target.db")
    target.save_profile(Profile(id="keep", name="Keep", band="6-8"))
    target.enqueue_job("learn", "6-8", "keep", 1, [])
    before = target.export_all()
    with pytest.raises(ValueError, match=f"conflicting records: duplicate {collection} "):
        target.import_all(data)
    assert target.export_all() == before  # nothing deleted, nothing written
    assert target.job_counts()["pending"] == 1
    target.close()

from __future__ import annotations

from app.learning.stats import profile_stats, window_start
from app.models import AnswerEvent, Profile, Session, WordProgress

TODAY = "2026-10-07"


def _profile() -> Profile:
    return Profile(id="p1", name="Ava", avatar="🦊", band="6-8", list_ids=["l1"], created_at="2026-09-01T00:00:00Z")


def _progress(word: str, stage: int, *, wrong: int = 0, unsure: int = 0, due: str | None = None, seen: int = 0) -> WordProgress:
    return WordProgress(profile_id="p1", word=word, stage=stage, wrong=wrong, unsure=unsure, due_date=due, seen=seen)


def _event(n: int, kind: str, *, qtype: str | None = "meaning", correct: bool | None = None, local_date: str = TODAY) -> AnswerEvent:
    return AnswerEvent(
        client_event_id=f"evt-{n:04d}",
        session_id="s1",
        profile_id="p1",
        word="calm",
        question_id=f"q{n}" if qtype else None,
        question_type=qtype,
        kind=kind,
        correct=correct,
        ms=900,
        local_date=local_date,
        at=f"{local_date}T10:00:{n:02d}Z",
    )


def _session(sid: str, local_date: str, started_at: str, *, answered: int, correct: int, unsure: int = 0,
             minutes: int = 10, finished: bool = True) -> Session:
    return Session(
        id=sid, profile_id="p1", mode="normal", local_date=local_date, started_at=started_at,
        finished_at=started_at if finished else None, planned_minutes=15, active_minutes=minutes,
        answered=answered, correct=correct, unsure=unsure,
    )


ELIGIBLE = ["brave", "calm", "eager", "frugal", "glad"]

PROGRESS = [
    _progress("brave", 5, due="2026-11-01", seen=9),
    _progress("calm", 2, wrong=3, unsure=1, due="2026-10-07", seen=8),
    _progress("eager", 2, wrong=1, due="2026-10-09", seen=4),
    _progress("frugal", 0, wrong=1, unsure=2, due="2026-10-06", seen=3),
    _progress("zest", 1, wrong=9, due="2026-10-01", seen=9),  # not on a current list: ignored
]

EVENTS = [
    _event(1, "answer", qtype="meaning", correct=True),
    _event(2, "answer", qtype="meaning", correct=False),
    _event(3, "unsure", qtype="fill_blank", local_date="2026-10-06"),
    _event(4, "answer", qtype="spell_it", correct=True, local_date="2026-10-05"),
    _event(5, "check_answer", qtype="meaning", correct=True),            # stats-only kind: excluded
    _event(6, "answer", qtype="meaning", correct=True, local_date="2026-09-01"),  # older than 30 days
    _event(7, "intro_seen", qtype=None),
]

SESSIONS = [
    _session("s3", "2026-10-05", "2026-10-05T16:00:00Z", answered=4, correct=4, minutes=8),
    _session("s1", "2026-10-07", "2026-10-07T15:00:00Z", answered=5, correct=4, unsure=1, minutes=12),
    _session("s5", "2026-08-01", "2026-08-01T10:00:00Z", answered=6, correct=3),
    _session("s2", "2026-10-07", "2026-10-07T09:00:00Z", answered=3, correct=1, minutes=6),
    _session("s4", "2026-10-04", "2026-10-04T10:00:00Z", answered=0, correct=0, minutes=1, finished=False),
]


def _stats() -> dict:
    return profile_stats(_profile(), PROGRESS, SESSIONS, EVENTS, TODAY, ELIGIBLE)


def test_window_start_is_29_days_back():
    assert window_start("2026-10-07") == "2026-09-08"


def test_counts_cover_only_eligible_words():
    stats = _stats()
    assert stats["counts"] == {"mastered": 1, "learning": 3, "new": 1, "due_today": 2, "total": 5}
    assert stats["profile"] == {"id": "p1", "name": "Ava", "avatar": "🦊", "band": "6-8"}
    assert stats["today"] == TODAY
    assert stats["window_start"] == "2026-09-08"


def test_accuracy_overall_and_by_type_use_graded_events_in_window():
    stats = _stats()
    assert stats["accuracy"]["answered"] == 4
    assert stats["accuracy"]["correct"] == 2
    assert stats["accuracy"]["overall"] == 0.5
    by_type = stats["accuracy"]["by_type"]
    assert list(by_type) == ["meaning", "fill_blank", "spell_it"]
    assert by_type["meaning"] == {"answered": 2, "correct": 1, "accuracy": 0.5}
    assert by_type["fill_blank"] == {"answered": 1, "correct": 0, "accuracy": 0.0}
    assert by_type["spell_it"] == {"answered": 1, "correct": 1, "accuracy": 1.0}
    assert stats["unsure_rate"] == 0.25


def test_days_practiced_counts_distinct_dates_with_answers():
    # 10-07 (two sessions) and 10-05; 10-04 had no answers; 08-01 is outside the window.
    assert _stats()["days_practiced_30"] == 2


def test_weakest_orders_by_stage_then_misses():
    weakest = _stats()["weakest"]
    assert [w["word"] for w in weakest] == ["frugal", "calm", "eager", "brave"]
    assert weakest[0] == {"word": "frugal", "stage": 0, "wrong": 1, "unsure": 2, "misses": 3, "seen": 3, "due_date": "2026-10-06"}
    assert weakest[1]["misses"] == 4


def test_recent_sessions_newest_first_with_accuracy():
    rows = _stats()["recent_sessions"]
    assert [r["id"] for r in rows] == ["s1", "s2", "s3", "s4", "s5"]
    assert rows[0] == {
        "id": "s1", "local_date": "2026-10-07", "mode": "normal", "minutes": 12,
        "answered": 5, "correct": 4, "unsure": 1, "accuracy": 0.8, "finished": True,
    }
    assert rows[3]["accuracy"] is None
    assert rows[3]["finished"] is False


def test_empty_profile_has_no_rates():
    stats = profile_stats(_profile(), [], [], [], TODAY, ["brave", "brave", "calm"])
    assert stats["counts"] == {"mastered": 0, "learning": 0, "new": 2, "due_today": 0, "total": 2}
    assert stats["accuracy"] == {"overall": None, "answered": 0, "correct": 0, "by_type": {}}
    assert stats["unsure_rate"] is None
    assert stats["days_practiced_30"] == 0
    assert stats["weakest"] == []
    assert stats["recent_sessions"] == []


def test_weakest_and_recent_are_capped():
    words = [f"word{chr(97 + i)}" for i in range(14)]
    progress = [_progress(w, i % 5, wrong=i) for i, w in enumerate(words)]
    sessions = [
        _session(f"s{i:02d}", "2026-10-01", f"2026-10-01T{i:02d}:00:00Z", answered=1, correct=1) for i in range(15)
    ]
    stats = profile_stats(_profile(), progress, sessions, [], TODAY, words)
    assert len(stats["weakest"]) == 12
    assert len(stats["recent_sessions"]) == 10
    assert stats["recent_sessions"][0]["id"] == "s14"
    assert stats["days_practiced_30"] == 1

from __future__ import annotations

import itertools

from app.learning.trends import profile_trends
from app.models import AnswerEvent, Session, WordProgress

ELIGIBLE = ["brave", "calm", "eager"]
_ids = itertools.count(1)


def _event(word: str, kind: str, at: str, *, correct: bool | None = None, session_id: str = "s1") -> AnswerEvent:
    n = next(_ids)
    return AnswerEvent(
        client_event_id=f"evt-{n:05d}",
        session_id=session_id,
        profile_id="p1",
        word=word,
        question_id=None if kind == "intro_seen" else f"q{n}",
        question_type=None if kind == "intro_seen" else "meaning",
        kind=kind,
        correct=correct,
        ms=900,
        local_date=at[:10],
        at=at,
    )



def _session(sid: str = "s1", mode: str = "normal") -> Session:
    return Session(id=sid, profile_id="p1", mode=mode, local_date="2026-10-01", started_at="2026-10-01T09:00:00Z",
                   planned_minutes=15)


def _trends(events: list[AnswerEvent], today: str, *, progress: list[WordProgress] | None = None,
            eligible: list[str] = ELIGIBLE, sessions: list[Session] | None = None) -> dict:
    return profile_trends(events, sessions or [_session()], progress or [], eligible, today)


def _correct_days(word: str, dates: list[str]) -> list[AnswerEvent]:
    """An intro on the first date, then one correct answer on each date (a day apart, so each one counts)."""
    events = [_event(word, "intro_seen", f"{dates[0]}T09:00:00Z")]
    events += [_event(word, "answer", f"{d}T10:00:00Z", correct=True) for d in dates]
    return events


# ---------- star levels per day ----------

def test_star_levels_at_the_end_of_each_day_carry_over_idle_days():
    events = [
        _event("calm", "intro_seen", "2026-10-01T10:00:00Z"),
        _event("calm", "answer", "2026-10-01T10:05:00Z", correct=True),     # 0 -> 1
        _event("brave", "intro_seen", "2026-10-01T10:06:00Z"),               # stays 0
        _event("calm", "answer", "2026-10-03T10:00:00Z", correct=True),     # 1 -> 2
        _event("brave", "answer", "2026-10-03T10:01:00Z", correct=False),   # 0 stays 0
    ]
    days = _trends(events, "2026-10-03")["days"]
    assert [d["date"] for d in days] == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert [d["stars"] for d in days] == [
        [1, 1, 0, 0, 0, 0],
        [1, 1, 0, 0, 0, 0],   # idle day: same as the day before
        [1, 0, 1, 0, 0, 0],
    ]


def test_star_levels_use_the_counting_rule_so_a_quick_repeat_earns_no_extra_star():
    events = [
        _event("calm", "intro_seen", "2026-10-01T10:00:00Z"),
        _event("calm", "answer", "2026-10-01T10:05:00Z", correct=True),     # counted: 0 -> 1
        _event("calm", "answer", "2026-10-01T10:30:00Z", correct=True),     # 25 min later: not counted
    ]
    day = _trends(events, "2026-10-01")["days"][0]
    assert day["stars"] == [0, 1, 0, 0, 0, 0]
    assert day["gained"] == 1


def test_words_no_longer_on_the_lists_are_left_out():
    events = _correct_days("calm", ["2026-10-01"]) + _correct_days("zest", ["2026-10-01"])
    day = _trends(events, "2026-10-01")["days"][0]
    assert day["stars"] == [0, 1, 0, 0, 0, 0]
    assert day["new"] == 1
    assert day["gained"] == 1


# ---------- daily activity ----------

def test_daily_activity_counts_new_words_and_stars_gained():
    events = [
        _event("calm", "intro_seen", "2026-10-01T10:00:00Z"),
        _event("brave", "intro_seen", "2026-10-01T10:01:00Z"),
        _event("calm", "answer", "2026-10-01T10:05:00Z", correct=True),
        _event("brave", "answer", "2026-10-01T10:06:00Z", correct=True),
        _event("calm", "intro_seen", "2026-10-02T10:00:00Z"),               # repeated intro: not a new word
        _event("calm", "answer", "2026-10-02T10:05:00Z", correct=True),
    ]
    days = _trends(events, "2026-10-02")["days"]
    assert [(d["new"], d["gained"], d["lost"]) for d in days] == [(2, 2, 0), (0, 1, 0)]


def test_stars_lost_counts_every_star_a_miss_takes_away():
    events = _correct_days("calm", ["2026-10-01", "2026-10-02", "2026-10-03"])   # 3 stars
    events.append(_event("calm", "answer", "2026-10-04T10:00:00Z", correct=False))  # 3 -> 1
    events.append(_event("brave", "intro_seen", "2026-10-04T10:01:00Z"))
    events.append(_event("brave", "unsure", "2026-10-04T10:02:00Z"))                # 0 stays 0: nothing lost
    day = _trends(events, "2026-10-04")["days"][-1]
    assert day["stars"] == [1, 1, 0, 0, 0, 0]
    assert (day["gained"], day["lost"]) == (0, 2)


def test_practice_answers_count_like_normal_ones():
    events = [_event("calm", "intro_seen", "2026-10-01T10:00:00Z", session_id="n1"),
              _event("calm", "answer", "2026-10-01T10:05:00Z", correct=True, session_id="n1"),
              _event("calm", "answer", "2026-10-01T13:00:00Z", correct=True, session_id="p1")]
    days = _trends(events, "2026-10-01", sessions=[_session("n1"), _session("p1", "practice")])["days"]
    assert days[0]["stars"] == [0, 0, 1, 0, 0, 0]


# ---------- the window ----------

def test_days_start_at_the_first_activity():
    events = _correct_days("calm", ["2026-10-05"])
    days = _trends(events, "2026-10-07")["days"]
    assert [d["date"] for d in days] == ["2026-10-05", "2026-10-06", "2026-10-07"]


def test_days_cover_at_most_30_days_but_replay_everything_before():
    events = _correct_days("calm", ["2026-08-01", "2026-08-02"])   # 2 stars, long before the window
    events += _correct_days("brave", ["2026-10-07"])
    days = _trends(events, "2026-10-07")["days"]
    assert len(days) == 30
    assert days[0]["date"] == "2026-09-08"
    assert days[0]["stars"] == [0, 0, 1, 0, 0, 0]
    assert days[-1]["stars"] == [0, 1, 1, 0, 0, 0]


def test_no_activity_means_no_days():
    assert _trends([], "2026-10-07")["days"] == []


def test_events_dated_after_today_are_ignored():
    events = _correct_days("calm", ["2026-10-01", "2026-10-02"])
    days = _trends(events, "2026-10-01")["days"]
    assert [d["date"] for d in days] == ["2026-10-01"]
    assert days[0]["stars"] == [0, 1, 0, 0, 0, 0]


# ---------- reviews coming up ----------

def _due(word: str, due: str | None) -> WordProgress:
    return WordProgress(profile_id="p1", word=word, stage=2, due_date=due)


def test_upcoming_reviews_put_overdue_words_on_today_and_cover_14_days():
    progress = [
        _due("brave", "2026-10-05"),   # overdue: today
        _due("calm", "2026-10-07"),    # today
        _due("eager", "2026-10-08"),   # tomorrow
        _due("frugal", "2026-10-20"),  # last day shown
        _due("glad", "2026-10-21"),    # beyond the 14 days
        _due("hardy", None),           # no schedule
        _due("zest", "2026-10-08"),    # not on the lists
    ]
    eligible = ["brave", "calm", "eager", "frugal", "glad", "hardy"]
    upcoming = _trends([], "2026-10-07", progress=progress, eligible=eligible)["upcoming"]
    assert len(upcoming) == 14
    assert upcoming[0] == {"date": "2026-10-07", "due": 2}
    assert upcoming[1] == {"date": "2026-10-08", "due": 1}
    assert upcoming[13] == {"date": "2026-10-20", "due": 1}
    assert sum(u["due"] for u in upcoming) == 4


def test_the_replay_leaves_the_callers_sessions_untouched():
    sessions = [_session("s1")]
    _trends(_correct_days("calm", ["2026-10-01"]), "2026-10-01", sessions=sessions)
    assert (sessions[0].answered, sessions[0].correct, sessions[0].new_words) == (0, 0, [])

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.learning.srs import (
    INTERVALS,
    MAX_COUNTED_PER_DAY,
    MIN_HOURS_BETWEEN_COUNTED,
    SEEN_IDS_CAP,
    apply_check_result,
    apply_events_to_state,
    apply_graded,
    apply_intro,
    is_stage_changing,
)
from app.models import AnswerEvent, Session, WordProgress

VECTORS = json.loads((Path(__file__).parent / "srs_vectors.json").read_text(encoding="utf-8"))
STATE_FIELDS = ("stage", "due_date", "interval_days", "last_graded_on", "graded_today", "last_graded_at")
TODAY = "2026-10-07"
NOON = "2026-10-07T12:00:00Z"


def state(p: WordProgress) -> dict:
    return {f: getattr(p, f) for f in STATE_FIELDS}


# ---------- shared vectors (also consumed by tests/js/srs.test.mjs) ----------

def test_vector_file_shape():
    assert len(VECTORS) >= 16
    names = [v["name"] for v in VECTORS]
    assert len(names) == len(set(names))
    for v in VECTORS:
        assert set(v) == {"name", "before", "event", "after", "stage_up"}
        assert set(v["before"]) == set(STATE_FIELDS)
        assert set(v["after"]) == set(STATE_FIELDS)
        assert set(v["event"]) == {"correct", "unsure", "local_date", "at", "mode"}
        assert v["event"]["mode"] in ("normal", "practice")
        assert isinstance(v["stage_up"], bool)


@pytest.mark.parametrize("vec", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vector(vec):
    p = WordProgress(profile_id="p1", word="brave", **vec["before"])
    ev = vec["event"]
    up = apply_graded(
        p, correct=ev["correct"], unsure=ev["unsure"], local_date=ev["local_date"], at=ev["at"], mode=ev["mode"],
        question_id="q1",
    )
    assert state(p) == vec["after"]
    assert up is vec["stage_up"]
    assert p.seen == 1  # every graded answer is counted, even when the schedule does not move
    assert p.seen_question_ids == ["q1"]


# ---------- pure rules ----------

def test_constants():
    assert INTERVALS == {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}
    assert SEEN_IDS_CAP == 60
    assert MAX_COUNTED_PER_DAY == 3
    assert MIN_HOURS_BETWEEN_COUNTED == 2


@pytest.mark.parametrize(
    "last, count, last_at, local, at, mode, expected",
    [
        (None, 0, None, TODAY, NOON, "normal", True),
        (None, 0, None, TODAY, NOON, "practice", True),
        ("2026-10-06", 3, "2026-10-06T20:00:00Z", TODAY, NOON, "normal", True),      # new day resets the count
        ("2026-10-01", 1, "2026-10-01T12:00:00Z", TODAY, NOON, "practice", True),
        (TODAY, 1, "2026-10-07T10:00:00Z", TODAY, NOON, "practice", True),           # exactly 2 h later
        (TODAY, 2, "2026-10-07T10:00:00Z", TODAY, NOON, "normal", True),             # third of the day
        (TODAY, 1, "2026-10-07T10:00:01Z", TODAY, NOON, "normal", False),            # 1 s short of 2 h
        (TODAY, 3, "2026-10-07T06:00:00Z", TODAY, NOON, "practice", False),          # fourth of the day
        ("2026-10-06", 1, "2026-10-07T11:00:00Z", TODAY, NOON, "normal", False),     # new day, but only 1 h later
        ("2026-10-08", 1, "2026-10-08T12:00:00Z", TODAY, NOON, "normal", False),     # local_date out of order
        (TODAY, 1, "2026-10-07T15:00:00Z", TODAY, NOON, "practice", False),          # at out of order
        (TODAY, 0, None, TODAY, NOON, "normal", True),                               # pre-upgrade row: 1 so far, gap ok
        (TODAY, 3, None, TODAY, NOON, "normal", False),                              # (not written by code) cap holds
        (None, 0, None, TODAY, NOON, "cram", False),
    ],
)
def test_is_stage_changing(last, count, last_at, local, at, mode, expected):
    p = WordProgress(profile_id="p1", word="brave", stage=2, last_graded_on=last, graded_today=count,
                     last_graded_at=last_at)
    assert is_stage_changing(p, local, mode, at) is expected


def test_counted_answers_update_the_daily_count_and_time():
    p = WordProgress(profile_id="p1", word="brave", stage=1, due_date=TODAY, interval_days=1, last_graded_on="2026-10-06",
                     graded_today=3, last_graded_at="2026-10-06T20:00:00Z")
    times = ["2026-10-07T08:00:00Z", "2026-10-07T09:00:00Z", "2026-10-07T10:00:00Z", "2026-10-07T13:30:00Z",
             "2026-10-07T20:00:00Z"]
    ups = [apply_graded(p, correct=True, unsure=False, local_date=TODAY, at=t, mode="practice", question_id=None)
           for t in times]
    # 08:00 counted (new day), 09:00 too soon, 10:00 counted, 13:30 counted (third), 20:00 is the fourth: not counted
    assert ups == [True, False, True, True, False]
    assert (p.stage, p.graded_today, p.last_graded_at, p.last_graded_on) == (4, 3, "2026-10-07T13:30:00Z", TODAY)
    assert (p.interval_days, p.due_date) == (14, "2026-10-21")
    assert (p.seen, p.correct) == (5, 5)


def test_pre_upgrade_row_graded_today_gets_two_more_counted_answers():
    p = WordProgress(profile_id="p1", word="brave", stage=1, due_date="2026-10-08", interval_days=1, last_graded_on=TODAY)
    assert (p.graded_today, p.last_graded_at) == (0, None)  # what an old row loads as
    ups = [apply_graded(p, correct=True, unsure=False, local_date=TODAY, at=t, mode="practice", question_id=None)
           for t in ("2026-10-07T12:00:00Z", "2026-10-07T14:00:00Z", "2026-10-07T18:00:00Z")]
    assert ups == [True, True, False]
    assert (p.stage, p.graded_today, p.last_graded_at) == (3, 3, "2026-10-07T14:00:00Z")


def test_counts_correct_wrong_unsure_are_exclusive():
    p = WordProgress(profile_id="p1", word="brave")
    apply_graded(p, correct=True, unsure=False, local_date=TODAY, at=NOON, mode="normal", question_id="a")
    apply_graded(p, correct=False, unsure=False, local_date=TODAY, at=NOON, mode="normal", question_id="b")
    apply_graded(p, correct=False, unsure=True, local_date=TODAY, at=NOON, mode="normal", question_id="c")
    apply_graded(p, correct=True, unsure=False, local_date="2026-10-01", at="2026-10-01T12:00:00Z", mode="normal",
                 question_id=None)
    assert (p.seen, p.correct, p.wrong, p.unsure) == (4, 2, 1, 1)
    assert p.seen_question_ids == ["a", "b", "c"]  # None is not recorded


def test_seen_question_ids_keep_last_60():
    p = WordProgress(profile_id="p1", word="brave", stage=3, last_graded_on=TODAY, graded_today=1, last_graded_at=NOON)
    for i in range(65):
        apply_graded(p, correct=True, unsure=False, local_date=TODAY, at=NOON, mode="normal", question_id=f"q{i}")
    assert len(p.seen_question_ids) == SEEN_IDS_CAP
    assert p.seen_question_ids[0] == "q5"
    assert p.seen_question_ids[-1] == "q64"
    assert p.stage == 3  # all at the same moment as the last counted answer: none is counted


def test_apply_intro_creates_schedule_once():
    p = WordProgress(profile_id="p1", word="brave")
    apply_intro(p, TODAY)
    assert (p.stage, p.due_date, p.introduced_on) == (0, TODAY, TODAY)
    p.stage, p.due_date = 2, "2026-10-10"
    apply_intro(p, "2026-10-09")
    assert (p.stage, p.due_date, p.introduced_on) == (2, "2026-10-10", TODAY)


@pytest.mark.parametrize(
    "check_set, passed, expected_due",
    [(2, False, "2026-10-08"), (1, False, "2026-10-20"), (2, True, "2026-10-20"), (1, True, "2026-10-20")],
)
def test_apply_check_result(check_set, passed, expected_due):
    p = WordProgress(profile_id="p1", word="brave", stage=3, due_date="2026-10-20", interval_days=7, last_graded_on=TODAY)
    apply_check_result(p, check_set=check_set, passed=passed, local_date=TODAY)
    assert p.due_date == expected_due
    assert (p.stage, p.interval_days, p.last_graded_on) == (3, 7, TODAY)


# ---------- apply_events_to_state ----------

def make_session(mode: str = "normal") -> Session:
    return Session(id="s1", profile_id="p1", mode=mode, local_date=TODAY, started_at="2026-10-07T15:00:00Z", planned_minutes=15)


def ev(kind: str, word: str, at_s: int, *, correct: bool | None = None, qid: str | None = None,
       check_set: int | None = None, correct_count: int | None = None, passed: bool | None = None,
       local_date: str = TODAY, at: str | None = None) -> AnswerEvent:
    return AnswerEvent(
        client_event_id=uuid.uuid4().hex[:16],
        session_id="s1",
        profile_id="p1",
        word=word,
        question_id=qid,
        question_type=None,
        kind=kind,
        correct=correct,
        check_set=check_set,
        correct_count=correct_count,
        passed=passed,
        ms=1500,
        local_date=local_date,
        at=at or f"2026-10-07T15:{at_s // 60:02d}:{at_s % 60:02d}Z",
    )


def prog(word: str, stage: int, due: str, interval: int, last: str | None) -> WordProgress:
    return WordProgress(profile_id="p1", word=word, stage=stage, due_date=due, interval_days=interval,
                        introduced_on="2026-09-01", last_graded_on=last)


def test_events_are_applied_in_at_order():
    s = make_session()
    progress = {"brave": WordProgress(profile_id="p1", word="brave")}
    # Sent out of order: the answer (t=20) arrives before the intro (t=10).
    events = [ev("answer", "brave", 20, correct=True, qid="q1"), ev("intro_seen", "brave", 10)]
    apply_events_to_state(s, progress, events)
    p = progress["brave"]
    assert (p.stage, p.due_date, p.interval_days, p.introduced_on, p.last_graded_on) == (1, "2026-10-08", 1, TODAY, TODAY)
    assert s.stars_up == ["brave"]
    assert s.new_words == ["brave"]


def test_intro_creates_missing_progress_and_new_words_unique():
    s = make_session()
    progress: dict[str, WordProgress] = {}
    apply_events_to_state(s, progress, [ev("intro_seen", "brave", 1), ev("intro_seen", "brave", 2)])
    p = progress["brave"]
    assert (p.profile_id, p.word, p.stage, p.due_date, p.introduced_on) == ("p1", "brave", 0, TODAY, TODAY)
    assert s.new_words == ["brave"]
    assert p.updated_at.endswith("Z")


def test_session_counters_missed_and_stars_up():
    s = make_session()
    progress = {
        "agile": prog("agile", 1, TODAY, 1, "2026-10-06"),
        "bold": prog("bold", 2, TODAY, 3, "2026-10-04"),
        "calm": prog("calm", 3, TODAY, 7, "2026-09-30"),
    }
    events = [
        ev("answer", "agile", 1, correct=True, qid="a1"),
        ev("answer", "bold", 2, correct=False, qid="b1"),
        ev("unsure", "calm", 3, qid="c1"),
        ev("answer", "bold", 4, correct=False, qid="b2"),
        ev("answer", "calm", 5, correct=True, qid="c2"),
    ]
    apply_events_to_state(s, progress, events)
    assert (s.answered, s.correct, s.unsure) == (5, 2, 1)
    assert s.missed == ["bold", "calm"]
    assert s.stars_up == ["agile"]  # calm's later correct answer came 2 s after its counted miss
    assert progress["agile"].stage == 2
    assert progress["bold"].stage == 1
    assert progress["calm"].stage == 1
    assert progress["bold"].seen_question_ids == ["b1", "b2"]
    assert (progress["calm"].seen, progress["calm"].unsure, progress["calm"].correct) == (2, 1, 1)


def test_stars_up_is_unique_across_learner_days():
    s = make_session()
    progress = {"agile": prog("agile", 1, TODAY, 1, "2026-10-06")}
    events = [
        ev("answer", "agile", 0, correct=True, qid="a1", local_date=TODAY, at="2026-10-07T21:30:00Z"),
        # the session ran past midnight; 2.5 hours later it is a new learner day
        ev("answer", "agile", 0, correct=True, qid="a2", local_date="2026-10-08", at="2026-10-08T00:00:00Z"),
    ]
    apply_events_to_state(s, progress, events)
    assert progress["agile"].stage == 3
    assert progress["agile"].last_graded_on == "2026-10-08"
    assert (progress["agile"].graded_today, progress["agile"].last_graded_at) == (1, "2026-10-08T00:00:00Z")
    assert s.stars_up == ["agile"]


def test_past_midnight_answer_less_than_2_hours_later_is_not_counted():
    s = make_session()
    progress = {"agile": prog("agile", 1, TODAY, 1, "2026-10-06")}
    events = [
        ev("answer", "agile", 0, correct=True, qid="a1", local_date=TODAY, at="2026-10-07T23:30:00Z"),
        ev("answer", "agile", 0, correct=True, qid="a2", local_date="2026-10-08", at="2026-10-08T00:30:00Z"),
    ]
    apply_events_to_state(s, progress, events)
    p = progress["agile"]
    assert (p.stage, p.last_graded_on, p.graded_today, p.last_graded_at) == (2, TODAY, 1, "2026-10-07T23:30:00Z")


def test_unsure_with_correct_flag_is_still_a_miss():
    s = make_session()
    progress = {"bold": prog("bold", 2, TODAY, 3, "2026-10-04")}
    apply_events_to_state(s, progress, [ev("unsure", "bold", 1, correct=True, qid="b1")])
    assert (s.answered, s.correct, s.unsure, s.missed) == (1, 0, 1, ["bold"])
    assert progress["bold"].stage == 1


def test_check_answer_changes_nothing():
    s = make_session()
    p = prog("bold", 2, "2026-10-10", 3, TODAY)
    progress = {"bold": p}
    apply_events_to_state(s, progress, [ev("check_answer", "bold", 1, correct=False, qid="b9")])
    assert state(p) == {"stage": 2, "due_date": "2026-10-10", "interval_days": 3, "last_graded_on": TODAY,
                        "graded_today": 0, "last_graded_at": None}
    assert (p.seen, p.wrong, p.seen_question_ids, p.updated_at) == (0, 0, [], "")
    assert (s.answered, s.correct, s.missed, s.checks_failed, s.checks_passed) == (0, 0, [], 0, 0)


def test_second_check_fail_sets_due_tomorrow():
    s = make_session()
    p = prog("bold", 1, "2026-10-20", 1, TODAY)
    progress = {"bold": p}
    events = [
        ev("check_result", "bold", 1, check_set=1, correct_count=1, passed=False),
        ev("check_result", "bold", 2, check_set=2, correct_count=0, passed=False),
    ]
    apply_events_to_state(s, progress, events)
    assert p.due_date == "2026-10-08"
    assert p.stage == 1
    assert (s.checks_failed, s.checks_passed) == (2, 0)
    assert p.updated_at.endswith("Z")


def test_first_check_fail_and_pass_do_not_move_due():
    s = make_session()
    p = prog("bold", 2, "2026-10-10", 3, TODAY)
    progress = {"bold": p}
    events = [
        ev("check_result", "bold", 1, check_set=1, correct_count=1, passed=False),
        ev("check_result", "bold", 2, check_set=2, correct_count=2, passed=True),
    ]
    apply_events_to_state(s, progress, events)
    assert p.due_date == "2026-10-10"
    assert (s.checks_failed, s.checks_passed) == (1, 1)


def test_learn_open_counts():
    s = make_session()
    progress = {"bold": prog("bold", 2, "2026-10-10", 3, TODAY)}
    apply_events_to_state(s, progress, [ev("learn_open", "bold", 1), ev("learn_open", "bold", 2)])
    assert s.learn_opened == 2
    assert progress["bold"].updated_at == ""


def test_practice_session_answers_count_with_the_daily_limits():
    s = make_session("practice")
    progress = {
        "bold": prog("bold", 2, "2026-10-12", 3, "2026-10-05"),
        "calm": prog("calm", 3, "2026-10-12", 7, "2026-10-05"),
        # counted 30 minutes ago in a normal session
        "dull": prog("dull", 2, "2026-10-10", 3, TODAY).model_copy(
            update={"graded_today": 1, "last_graded_at": "2026-10-07T14:30:00Z"}),
    }
    events = [
        ev("answer", "bold", 1, correct=False, qid="b1"),
        ev("answer", "calm", 2, correct=True, qid="c1"),
        ev("answer", "dull", 3, correct=False, qid="d1"),
        ev("answer", "calm", 4, correct=True, qid="c2"),  # a re-ask 2 s later: not counted
    ]
    apply_events_to_state(s, progress, events)
    assert state(progress["bold"]) == {"stage": 1, "due_date": "2026-10-08", "interval_days": 1, "last_graded_on": TODAY,
                                       "graded_today": 1, "last_graded_at": "2026-10-07T15:00:01Z"}
    assert state(progress["calm"]) == {"stage": 4, "due_date": "2026-10-21", "interval_days": 14, "last_graded_on": TODAY,
                                       "graded_today": 1, "last_graded_at": "2026-10-07T15:00:02Z"}
    # not counted (too soon): the practice miss only pulls the due date to tomorrow
    assert state(progress["dull"]) == {"stage": 2, "due_date": "2026-10-08", "interval_days": 3, "last_graded_on": TODAY,
                                       "graded_today": 1, "last_graded_at": "2026-10-07T14:30:00Z"}
    assert (s.answered, s.correct, s.missed, s.stars_up) == (4, 2, ["bold", "dull"], ["calm"])


def test_check_answer_and_check_result_never_count():
    s = make_session("practice")
    p = prog("bold", 2, "2026-10-10", 3, "2026-10-05")
    progress = {"bold": p}
    events = [
        ev("check_answer", "bold", 1, correct=True, qid="b1"),
        ev("check_result", "bold", 2, check_set=1, correct_count=3, passed=True),
    ]
    apply_events_to_state(s, progress, events)
    assert state(p) == {"stage": 2, "due_date": "2026-10-10", "interval_days": 3, "last_graded_on": "2026-10-05",
                        "graded_today": 0, "last_graded_at": None}


def test_seen_question_ids_cap_through_events():
    s = make_session()
    progress = {"bold": prog("bold", 2, TODAY, 3, "2026-10-01")}
    events = [ev("answer", "bold", i, correct=True, qid=f"q{i}") for i in range(65)]
    apply_events_to_state(s, progress, events)
    ids = progress["bold"].seen_question_ids
    assert len(ids) == 60 and ids[0] == "q5" and ids[-1] == "q64"
    assert progress["bold"].stage == 3  # the others came less than 2 hours after the counted one
    assert s.answered == 65 and s.stars_up == ["bold"]

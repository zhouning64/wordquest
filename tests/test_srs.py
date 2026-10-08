from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.learning.srs import (
    INTERVALS,
    SEEN_IDS_CAP,
    apply_check_result,
    apply_events_to_state,
    apply_graded,
    apply_intro,
    is_stage_changing,
)
from app.models import AnswerEvent, Session, WordProgress

VECTORS = json.loads((Path(__file__).parent / "srs_vectors.json").read_text(encoding="utf-8"))
STATE_FIELDS = ("stage", "due_date", "interval_days", "last_graded_on")
TODAY = "2026-10-07"


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
        assert set(v["event"]) == {"correct", "unsure", "local_date", "mode"}
        assert v["event"]["mode"] in ("normal", "practice")
        assert isinstance(v["stage_up"], bool)


@pytest.mark.parametrize("vec", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vector(vec):
    p = WordProgress(profile_id="p1", word="brave", **vec["before"])
    ev = vec["event"]
    up = apply_graded(
        p, correct=ev["correct"], unsure=ev["unsure"], local_date=ev["local_date"], mode=ev["mode"], question_id="q1"
    )
    assert state(p) == vec["after"]
    assert up is vec["stage_up"]
    assert p.seen == 1  # every graded answer is counted, even when the schedule does not move
    assert p.seen_question_ids == ["q1"]


# ---------- pure rules ----------

def test_constants():
    assert INTERVALS == {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}
    assert SEEN_IDS_CAP == 60


@pytest.mark.parametrize(
    "last, local, mode, expected",
    [
        (None, TODAY, "normal", True),
        ("2026-10-06", TODAY, "normal", True),
        (TODAY, TODAY, "normal", False),
        ("2026-10-08", TODAY, "normal", False),
        (None, TODAY, "practice", False),
        ("2026-10-01", TODAY, "practice", False),
    ],
)
def test_is_stage_changing(last, local, mode, expected):
    p = WordProgress(profile_id="p1", word="brave", stage=2, last_graded_on=last)
    assert is_stage_changing(p, local, mode) is expected


def test_counts_correct_wrong_unsure_are_exclusive():
    p = WordProgress(profile_id="p1", word="brave")
    apply_graded(p, correct=True, unsure=False, local_date=TODAY, mode="normal", question_id="a")
    apply_graded(p, correct=False, unsure=False, local_date=TODAY, mode="normal", question_id="b")
    apply_graded(p, correct=False, unsure=True, local_date=TODAY, mode="normal", question_id="c")
    apply_graded(p, correct=True, unsure=False, local_date="2026-10-01", mode="normal", question_id=None)
    assert (p.seen, p.correct, p.wrong, p.unsure) == (4, 2, 1, 1)
    assert p.seen_question_ids == ["a", "b", "c"]  # None is not recorded


def test_seen_question_ids_keep_last_60():
    p = WordProgress(profile_id="p1", word="brave", stage=3, last_graded_on=TODAY)
    for i in range(65):
        apply_graded(p, correct=True, unsure=False, local_date=TODAY, mode="normal", question_id=f"q{i}")
    assert len(p.seen_question_ids) == SEEN_IDS_CAP
    assert p.seen_question_ids[0] == "q5"
    assert p.seen_question_ids[-1] == "q64"
    assert p.stage == 3  # all same-day answers after the first graded one


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
       local_date: str = TODAY) -> AnswerEvent:
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
        at=f"2026-10-07T15:{at_s // 60:02d}:{at_s % 60:02d}Z",
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
    assert s.stars_up == ["agile"]  # calm's later correct answer is a same-day repeat
    assert progress["agile"].stage == 2
    assert progress["bold"].stage == 1
    assert progress["calm"].stage == 1
    assert progress["bold"].seen_question_ids == ["b1", "b2"]
    assert (progress["calm"].seen, progress["calm"].unsure, progress["calm"].correct) == (2, 1, 1)


def test_stars_up_is_unique_across_learner_days():
    s = make_session()
    progress = {"agile": prog("agile", 1, TODAY, 1, "2026-10-06")}
    events = [
        ev("answer", "agile", 1, correct=True, qid="a1", local_date=TODAY),
        ev("answer", "agile", 2, correct=True, qid="a2", local_date="2026-10-08"),  # session ran past midnight
    ]
    apply_events_to_state(s, progress, events)
    assert progress["agile"].stage == 3
    assert progress["agile"].last_graded_on == "2026-10-08"
    assert s.stars_up == ["agile"]


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
    assert state(p) == {"stage": 2, "due_date": "2026-10-10", "interval_days": 3, "last_graded_on": TODAY}
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


def test_practice_session_never_changes_stage():
    s = make_session("practice")
    progress = {
        "bold": prog("bold", 2, "2026-10-12", 3, "2026-10-05"),
        "calm": prog("calm", 3, "2026-10-12", 7, "2026-10-05"),
    }
    events = [ev("answer", "bold", 1, correct=False, qid="b1"), ev("answer", "calm", 2, correct=True, qid="c1")]
    apply_events_to_state(s, progress, events)
    assert state(progress["bold"]) == {"stage": 2, "due_date": "2026-10-08", "interval_days": 3, "last_graded_on": "2026-10-05"}
    assert state(progress["calm"]) == {"stage": 3, "due_date": "2026-10-12", "interval_days": 7, "last_graded_on": "2026-10-05"}
    assert (s.answered, s.correct, s.missed, s.stars_up) == (2, 1, ["bold"], [])


def test_seen_question_ids_cap_through_events():
    s = make_session()
    progress = {"bold": prog("bold", 2, TODAY, 3, "2026-10-01")}
    events = [ev("answer", "bold", i, correct=True, qid=f"q{i}") for i in range(65)]
    apply_events_to_state(s, progress, events)
    ids = progress["bold"].seen_question_ids
    assert len(ids) == 60 and ids[0] == "q5" and ids[-1] == "q64"
    assert progress["bold"].stage == 3  # only the first answer of the day moved it
    assert s.answered == 65 and s.stars_up == ["bold"]

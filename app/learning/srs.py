"""Spaced-repetition rules (spec §8.5). Pure functions over WordProgress / Session."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import clock
from app.models import AnswerEvent, Session, WordProgress

INTERVALS: dict[int, int] = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}
SEEN_IDS_CAP = 60
MAX_STAGE = 5
MASTERED_MIN_INTERVAL = 30
MASTERED_MAX_INTERVAL = 60
MAX_COUNTED_PER_DAY = 3         # stage-changing ("counted") answers per word per learner local date
MIN_HOURS_BETWEEN_COUNTED = 2   # between a word's counted answers, measured with the events' `at` (UTC)
COUNTING_MODES = ("normal", "practice")
_MIN_GAP_MS = MIN_HOURS_BETWEEN_COUNTED * 3_600_000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _epoch_ms(at: str | None) -> int | None:
    """Whole milliseconds since the epoch (the precision of web/js/srs.js), or None if missing or unparseable."""
    if not at:
        return None
    try:
        return (clock.parse_iso(at) - _EPOCH) // timedelta(milliseconds=1)
    except ValueError:
        return None


def counted_on(p: WordProgress, local_date: str) -> int:
    """The word's counted answers on local_date. A row graded that day before last_graded_at existed (pre-upgrade)
    has graded_today 0 and no last_graded_at: it counts as one answer, with the 2-hour gap already satisfied."""
    if p.last_graded_on != local_date:
        return 0
    if p.last_graded_at is None:
        return max(1, p.graded_today)
    return p.graded_today


def is_out_of_order(p: WordProgress, local_date: str, at: str) -> bool:
    """Dated before the word's last counted answer, by learner date or by `at`: such an answer updates counts only."""
    if p.last_graded_on is not None and local_date < p.last_graded_on:
        return True
    last, now = _epoch_ms(p.last_graded_at), _epoch_ms(at)
    return last is not None and now is not None and now < last


def is_stage_changing(p: WordProgress, local_date: str, mode: str, at: str) -> bool:
    """A graded answer is counted when: the session is normal or practice; it is not out of order; the word has fewer
    than MAX_COUNTED_PER_DAY counted answers on local_date; and MIN_HOURS_BETWEEN_COUNTED have passed since its last
    counted answer (even across midnight). An answer whose `at` cannot be read is not counted."""
    if mode not in COUNTING_MODES or is_out_of_order(p, local_date, at):
        return False
    if counted_on(p, local_date) >= MAX_COUNTED_PER_DAY:
        return False
    now = _epoch_ms(at)
    if now is None:
        return False
    last = _epoch_ms(p.last_graded_at)
    return last is None or now - last >= _MIN_GAP_MS


def apply_intro(p: WordProgress, local_date: str) -> None:
    """intro_seen: create the schedule once; a repeated intro never resets an existing word."""
    if p.introduced_on is None:
        p.stage = 0
        p.due_date = local_date
        p.introduced_on = local_date


def apply_graded(
    p: WordProgress,
    *,
    correct: bool,
    unsure: bool,
    local_date: str,
    at: str,
    mode: str,
    question_id: str | None,
) -> bool:
    """Apply one graded answer ("answer" or "unsure"). Returns True iff the stage increased."""
    miss = unsure or not correct
    p.seen += 1
    if unsure:
        p.unsure += 1
    elif correct:
        p.correct += 1
    else:
        p.wrong += 1
    if question_id:
        p.seen_question_ids = (list(p.seen_question_ids) + [question_id])[-SEEN_IDS_CAP:]

    if is_out_of_order(p, local_date, at):
        return False  # dated before the last counted answer: counts only

    if is_stage_changing(p, local_date, mode, at):
        before = p.stage
        if not miss:
            p.stage = min(MAX_STAGE, before + 1)
            if before < MAX_STAGE:
                p.interval_days = INTERVALS[p.stage]
            else:
                p.interval_days = min(MASTERED_MAX_INTERVAL, max(MASTERED_MIN_INTERVAL, p.interval_days * 2))
        else:
            p.stage = 0 if before == 0 else max(1, before - 2)
            p.interval_days = 1
        p.due_date = clock.add_days(local_date, p.interval_days)
        p.graded_today = counted_on(p, local_date) + 1  # before last_graded_on moves: a new date starts at 1
        p.last_graded_on = local_date
        p.last_graded_at = at
        return p.stage > before

    if mode == "practice" and miss:
        tomorrow = clock.add_days(local_date, 1)
        p.due_date = tomorrow if p.due_date is None else min(p.due_date, tomorrow)
    return False


def apply_check_result(p: WordProgress, *, check_set: int, passed: bool, local_date: str) -> None:
    """Second lock-in fail: come back tomorrow (stage unchanged)."""
    if check_set == 2 and not passed:
        p.due_date = clock.add_days(local_date, 1)


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def apply_events_to_state(session: Session, progress: dict[str, WordProgress], events: list[AnswerEvent]) -> None:
    """The Repository.apply_events ApplyFn: mutates session counters and progress in place."""
    now = clock.utc_now_iso()
    touched: set[str] = set()
    for e in sorted(events, key=lambda ev: ev.at):
        if e.kind == "learn_open":
            session.learn_opened += 1
            continue
        if e.kind == "check_answer":
            continue  # stats only; never changes progress
        p = progress.get(e.word)
        if p is None:
            p = WordProgress(profile_id=session.profile_id, word=e.word)
            progress[e.word] = p
        if e.kind == "intro_seen":
            apply_intro(p, e.local_date)
            _append_unique(session.new_words, e.word)
            touched.add(e.word)
        elif e.kind in ("answer", "unsure"):
            is_unsure = e.kind == "unsure"
            is_correct = bool(e.correct) and e.kind == "answer"
            stage_up = apply_graded(
                p,
                correct=is_correct,
                unsure=is_unsure,
                local_date=e.local_date,
                at=e.at,
                mode=session.mode,
                question_id=e.question_id,
            )
            session.answered += 1
            if is_correct:
                session.correct += 1
            if is_unsure:
                session.unsure += 1
            if not is_correct:
                _append_unique(session.missed, e.word)
            if stage_up:
                _append_unique(session.stars_up, e.word)
            touched.add(e.word)
        elif e.kind == "check_result":
            passed = bool(e.passed)
            apply_check_result(p, check_set=e.check_set or 0, passed=passed, local_date=e.local_date)
            if passed:
                session.checks_passed += 1
            else:
                session.checks_failed += 1
            touched.add(e.word)
    for word in touched:
        progress[word].updated_at = now

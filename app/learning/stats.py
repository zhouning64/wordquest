"""Parent progress dashboard numbers (spec §11). Pure functions: no storage access."""
from __future__ import annotations

from collections import Counter

from app import clock
from app.models import QTYPES, AnswerEvent, Profile, Session, WordProgress

WINDOW_DAYS = 30
WEAKEST_LIMIT = 12
RECENT_SESSIONS_LIMIT = 10
GRADED_KINDS = ("answer", "unsure")


def window_start(today: str) -> str:
    """First local date of the 30-day window that ends on `today` (inclusive)."""
    return clock.add_days(today, -(WINDOW_DAYS - 1))


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def profile_stats(
    profile: Profile,
    progress: list[WordProgress],
    sessions: list[Session],
    events: list[AnswerEvent],
    today: str,
    eligible_words: list[str],
) -> dict:
    since = window_start(today)
    by_word = {p.word: p for p in progress}
    eligible = list(dict.fromkeys(eligible_words))

    # Word counts over the profile's current lists (spec §8.5 derived status).
    mastered = learning = new = due_today = 0
    for word in eligible:
        p = by_word.get(word)
        if p is None:
            new += 1
            continue
        if p.stage >= 5:
            mastered += 1
        else:
            learning += 1
        if p.due_date is not None and p.due_date <= today:
            due_today += 1

    # Accuracy from graded events (answer / unsure) in the last 30 days.
    answered = correct = unsure = 0
    type_answered: Counter[str] = Counter()
    type_correct: Counter[str] = Counter()
    for e in events:
        if e.kind not in GRADED_KINDS or e.local_date < since:
            continue
        qtype = e.question_type or "unknown"
        answered += 1
        type_answered[qtype] += 1
        if e.kind == "unsure":
            unsure += 1
        elif e.correct:
            correct += 1
            type_correct[qtype] += 1
    type_order = [t for t in QTYPES if t in type_answered]
    type_order += sorted(t for t in type_answered if t not in QTYPES)
    by_type = {
        t: {"answered": type_answered[t], "correct": type_correct[t], "accuracy": _ratio(type_correct[t], type_answered[t])}
        for t in type_order
    }

    # A day counts as practiced when a session that day recorded at least one graded answer.
    days = {s.local_date for s in sessions if s.answered > 0 and s.local_date >= since}

    # Weakest words: eligible words with progress, lowest stage first, then most misses.
    tracked = [by_word[w] for w in eligible if w in by_word]
    tracked.sort(key=lambda p: (p.stage, -(p.wrong + p.unsure), p.word))
    weakest = [
        {
            "word": p.word,
            "stage": p.stage,
            "wrong": p.wrong,
            "unsure": p.unsure,
            "misses": p.wrong + p.unsure,
            "seen": p.seen,
            "due_date": p.due_date,
        }
        for p in tracked[:WEAKEST_LIMIT]
    ]

    recent = sorted(sessions, key=lambda s: s.started_at, reverse=True)[:RECENT_SESSIONS_LIMIT]
    recent_rows = [
        {
            "id": s.id,
            "local_date": s.local_date,
            "mode": s.mode,
            "minutes": s.active_minutes,
            "answered": s.answered,
            "correct": s.correct,
            "unsure": s.unsure,
            "accuracy": _ratio(s.correct, s.answered),
            "finished": s.finished_at is not None,
        }
        for s in recent
    ]

    return {
        "profile": {"id": profile.id, "name": profile.name, "avatar": profile.avatar, "band": profile.band},
        "today": today,
        "window_start": since,
        "counts": {"mastered": mastered, "learning": learning, "new": new, "due_today": due_today, "total": len(eligible)},
        "accuracy": {"overall": _ratio(correct, answered), "answered": answered, "correct": correct, "by_type": by_type},
        "unsure_rate": _ratio(unsure, answered),
        "days_practiced_30": len(days),
        "weakest": weakest,
        "recent_sessions": recent_rows,
    }

"""Parent progress charts (spec §11): star levels per day, daily activity, reviews coming up. Pure functions: no
storage access.

History is rebuilt by replaying the whole answer log with the live grading rules (srs.apply_events_to_state), so it
needs no stored snapshots; past days are drawn with today's rules."""
from __future__ import annotations

from itertools import groupby

from app import clock
from app.learning.srs import MAX_STAGE, apply_events_to_state
from app.models import AnswerEvent, Session, WordProgress

TREND_DAYS = 30      # days shown, ending today
UPCOMING_DAYS = 14   # today (with any overdue words) and the 13 days after


def _star_counts(progress: dict[str, WordProgress], eligible: set[str]) -> list[int]:
    counts = [0] * (MAX_STAGE + 1)
    for word, p in progress.items():
        if word in eligible:
            counts[min(MAX_STAGE, p.stage)] += 1
    return counts


def _replay(events: list[AnswerEvent], sessions: list[Session], eligible: set[str], today: str) -> dict[str, dict]:
    """{local_date: {"stars", "new", "gained", "lost"}} for every date up to today that has events. "stars" is the
    word count at each star level once that date's events are applied."""
    modes = {s.id: s.mode for s in sessions}
    replay_sessions: dict[str, Session] = {}  # throwaway copies: apply_events_to_state bumps session counters
    progress: dict[str, WordProgress] = {}
    days: dict[str, dict] = {}
    ordered = sorted(events, key=lambda e: (e.local_date, e.at))
    for date, day_events in groupby(ordered, key=lambda e: e.local_date):
        if date > today:
            break
        new = gained = lost = 0
        for e in day_events:
            session = replay_sessions.get(e.session_id)
            if session is None:
                session = Session(id=e.session_id, profile_id=e.profile_id, mode=modes.get(e.session_id, "normal"),
                                  local_date=e.local_date, started_at=e.at, planned_minutes=0)
                replay_sessions[e.session_id] = session
            before = progress[e.word].stage if e.word in progress else None
            apply_events_to_state(session, progress, [e])
            if e.word not in eligible or e.word not in progress:
                continue
            after = progress[e.word].stage
            if before is None:
                new += 1
            elif after > before:
                gained += after - before
            else:
                lost += before - after
        days[date] = {"stars": _star_counts(progress, eligible), "new": new, "gained": gained, "lost": lost}
    return days


def _upcoming(progress: list[WordProgress], eligible: set[str], today: str) -> list[dict]:
    dates = [clock.add_days(today, i) for i in range(UPCOMING_DAYS)]
    due = dict.fromkeys(dates, 0)
    for p in progress:
        if p.word not in eligible or p.due_date is None:
            continue
        date = today if p.due_date <= today else p.due_date
        if date in due:
            due[date] += 1
    return [{"date": d, "due": due[d]} for d in dates]


def profile_trends(
    events: list[AnswerEvent],
    sessions: list[Session],
    progress: list[WordProgress],
    eligible_words: list[str],
    today: str,
) -> dict:
    """`events` must be the profile's whole log: star levels depend on every answer since the first one."""
    eligible = set(eligible_words)
    replayed = _replay(events, sessions, eligible, today)
    days: list[dict] = []
    if replayed:
        dates = sorted(replayed)
        date = max(dates[0], clock.add_days(today, -(TREND_DAYS - 1)))
        stars = [0] * (MAX_STAGE + 1)
        i = 0
        while date <= today:
            while i < len(dates) and dates[i] <= date:  # carry the latest star levels over idle days
                stars = replayed[dates[i]]["stars"]
                i += 1
            day = replayed.get(date, {"new": 0, "gained": 0, "lost": 0})
            days.append({"date": date, "stars": list(stars), "new": day["new"], "gained": day["gained"],
                         "lost": day["lost"]})
            date = clock.add_days(date, 1)
    return {"days": days, "upcoming": _upcoming(progress, eligible, today)}

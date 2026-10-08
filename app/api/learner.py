"""Learner API: profile picker, home counts, sessions, answer events, results (spec §8, §9)."""
from __future__ import annotations

import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app import clock
from app.auth import require_site
from app.learning.session import SessionInputs, build_session, topup_words
from app.learning.srs import apply_events_to_state
from app.models import AnswerEvent, EventIn, Profile, ProfileSettings, Session, WordContent, new_id
from app.storage.base import BlobStore, Repository
from app.triggers import maybe_enqueue_topup

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
KEEP_PRACTICING_MAX = 5
MAX_EVENTS_PER_UPLOAD = 500

router = APIRouter(prefix="/api", dependencies=[Depends(require_site)])


class SessionStart(BaseModel):
    mode: Literal["normal", "practice"] = "normal"
    local_date: str = Field(pattern=DATE_PATTERN)


class EventsUpload(BaseModel):
    events: list[EventIn] = Field(default_factory=list, max_length=MAX_EVENTS_PER_UPLOAD)


class FinishBody(BaseModel):
    active_minutes: int = Field(default=0, ge=0, le=600)


def _repo(request: Request) -> Repository:
    return request.app.state.repo


def _blobs(request: Request) -> BlobStore:
    return request.app.state.blobs


def _profile_or_404(repo: Repository, profile_id: str) -> Profile:
    profile = repo.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile_not_found")
    return profile


def _session_or_404(repo: Repository, session_id: str) -> Session:
    session = repo.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return session


def eligible_words(repo: Repository, profile: Profile) -> list[str]:
    """Words of the profile's current lists: list order, then word order, first occurrence wins."""
    seen: dict[str, None] = {}
    for list_id in profile.list_ids:
        word_list = repo.get_list(list_id)
        if word_list is None:
            continue
        for word in word_list.words:
            seen.setdefault(word, None)
    return list(seen)


def _content_ready(content: WordContent | None) -> bool:
    return content is not None and content.status == "ready" and content.card is not None


def _session_seed(session_id: str) -> int:
    return int(hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16], 16)


def _accuracy(correct: int, answered: int) -> int:
    if answered <= 0:
        return 0
    return (200 * correct + answered) // (2 * answered)  # round half up to a whole percent


@router.get("/profiles")
def list_profiles(request: Request) -> list:
    return [{"id": p.id, "name": p.name, "avatar": p.avatar} for p in _repo(request).list_profiles()]


@router.get("/profiles/{profile_id}/home")
def profile_home(profile_id: str, request: Request, local_date: str = Query(pattern=DATE_PATTERN)) -> dict:
    repo = _repo(request)
    profile = _profile_or_404(repo, profile_id)
    words = eligible_words(repo, profile)
    contents = repo.get_contents(profile.band, words)
    progress = repo.get_progress(profile.id, words)

    mastered = learning = new = due_today = ready_new = practice_eligible = ready = 0
    for word in words:
        is_ready = _content_ready(contents.get(word))
        if is_ready:
            ready += 1
        p = progress.get(word)
        if p is None:
            new += 1
            if is_ready:
                ready_new += 1
            continue
        if p.stage >= 5:
            mastered += 1
        else:
            learning += 1
        if p.due_date is not None and p.due_date <= local_date:
            due_today += 1
        if 1 <= p.stage <= 3 and is_ready:
            practice_eligible += 1

    return {
        "profile": {
            "id": profile.id,
            "name": profile.name,
            "avatar": profile.avatar,
            "band": profile.band,
            "session_minutes": profile.settings.session_minutes,
            "new_words_per_session": profile.settings.new_words_per_session,
        },
        "mastered": mastered,
        "learning": learning,
        "new": new,
        "due_today": due_today,
        "ready_new": ready_new,
        "practice_eligible": practice_eligible,
        "preparing": {"ready": ready, "total": len(words)},
    }


@router.post("/profiles/{profile_id}/sessions")
def start_session(profile_id: str, body: SessionStart, request: Request) -> dict:
    repo = _repo(request)
    blobs = _blobs(request)
    profile = _profile_or_404(repo, profile_id)
    words = eligible_words(repo, profile)
    session_id = new_id()
    inputs = SessionInputs(
        profile=profile,
        mode=body.mode,
        local_date=body.local_date,
        eligible_words=words,
        contents=repo.get_contents(profile.band, words),
        pools=repo.get_pools(profile.band, words),
        progress=repo.get_progress(profile.id, words),
        seed=_session_seed(session_id),
    )
    payload = build_session(inputs)

    for word, entry in payload["words"].items():
        image_key = entry.pop("image_key", None)
        content = inputs.contents.get(word)
        image_ready = content is not None and content.image_status == "ready"
        entry["image_url"] = blobs.url_for(image_key) if image_key and image_ready else None

    if not payload["queue"]:
        # Nothing to study (still preparing, or nothing due): no session record is created.
        return {**payload, "session_id": None}

    queue_words = list(dict.fromkeys(item["word"] for item in payload["queue"]))
    repo.save_session(
        Session(
            id=session_id,
            profile_id=profile.id,
            mode=body.mode,
            local_date=body.local_date,
            started_at=clock.utc_now_iso(),
            planned_minutes=profile.settings.session_minutes,
            words=queue_words,
        )
    )
    today_utc = clock.utc_date()
    for word in topup_words(inputs):
        maybe_enqueue_topup(repo, profile.band, word, today_utc)
    return {**payload, "session_id": session_id}


@router.post("/sessions/{session_id}/events")
def post_events(session_id: str, body: EventsUpload, request: Request) -> dict:
    repo = _repo(request)
    session = _session_or_404(repo, session_id)
    if not body.events:
        return {"accepted": []}
    events = [
        AnswerEvent(**event.model_dump(), session_id=session.id, profile_id=session.profile_id)
        for event in body.events
    ]
    try:
        accepted = repo.apply_events(session.id, events, apply_events_to_state)
    except KeyError:
        raise HTTPException(status_code=404, detail="session_not_found") from None
    return {"accepted": accepted}


@router.post("/sessions/{session_id}/finish")
def finish_session(session_id: str, body: FinishBody, request: Request) -> dict:
    repo = _repo(request)
    session = _session_or_404(repo, session_id)
    if session.finished_at is None:
        session.finished_at = clock.utc_now_iso()
    session.active_minutes = body.active_minutes
    repo.save_session(session)

    profile = repo.get_profile(session.profile_id)
    settings = profile.settings if profile is not None else ProfileSettings()
    progress = repo.get_progress(session.profile_id, session.missed)
    order = {word: i for i, word in enumerate(session.missed)}
    stage_of = {word: (progress[word].stage if word in progress else 0) for word in session.missed}
    weakest = sorted(session.missed, key=lambda w: (stage_of[w], order[w]))[:KEEP_PRACTICING_MAX]

    return {
        "accuracy": _accuracy(session.correct, session.answered),
        "answered": session.answered,
        "new_words": session.new_words,
        "stars_up": session.stars_up,
        "keep_practicing": [{"word": w, "stage": stage_of[w]} for w in weakest],
        "break_reminder": settings.break_reminder,
        "break_message": settings.break_message,
    }

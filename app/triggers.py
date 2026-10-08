from __future__ import annotations

# Generation triggers (spec §7.1): decide which generation jobs to enqueue.
#
# Every function is synchronous and talks only to the Repository; async callers wrap them in asyncio.to_thread.

from typing import Iterable

from app.ai.content import pool_meets_minimum
from app.models import BANDS, POOL_CAP, Profile, WordContent
from app.storage.base import Repository

FULL_CHAIN = ["questions", "image"]
REGENERATE_PARTS = ("all", "learn", "questions", "image")
_LIVE = ("pending", "running")


def bands_for_list(repo: Repository, list_id: str) -> set[str]:
    """Bands of every profile that has this list assigned (a list nobody uses has no band)."""
    return {p.band for p in repo.list_profiles() if list_id in p.list_ids}


def ensure_generation(repo: Repository, band: str, word: str) -> None:
    """Make sure content for word × band exists or is being built (first time or resume)."""
    content = repo.get_content(band, word)
    if content is None:
        repo.save_content(WordContent(word=word, band=band, status="pending", content_version=1))
        repo.enqueue_job("learn", band, word, 1, list(FULL_CHAIN))
        return
    if content.status == "ready":
        return
    if content.status == "pending" and _has_live_job(repo, band, word):
        return
    # failed, or pending without any live job (e.g. after a backup import cleared the jobs table)
    _resume(repo, content)


def _has_live_job(repo: Repository, band: str, word: str) -> bool:
    for kind in ("learn", "questions"):
        job = repo.get_job(f"{kind}:{band}:{word}")
        if job is not None and job.status in _LIVE:
            return True
    return False


def _resume(repo: Repository, content: WordContent) -> None:
    """Restart from the stage that failed; a valid card and verified questions are kept."""
    band, word, version = content.band, content.word, content.content_version
    if content.card is None:
        kind, chain = "learn", list(FULL_CHAIN)
    else:
        pool = [q for q in repo.get_pool(band, word, version) if q.verified]
        if pool_meets_minimum(pool):
            content.status = "ready"
            content.error = ""
            repo.save_content(content)
            if content.image_status != "ready":
                repo.enqueue_job("image", band, word, version, [])
            return
        kind, chain = "questions", ["image"]
    content.status = "pending"
    content.error = ""
    repo.save_content(content)
    repo.enqueue_job(kind, band, word, version, chain)


def on_lists_changed(repo: Repository, list_ids: Iterable[str]) -> None:
    """A list was saved/edited/assigned: every word × every band of the profiles using it."""
    for list_id in dict.fromkeys(list_ids):
        wordlist = repo.get_list(list_id)
        if wordlist is None:
            continue
        used = bands_for_list(repo, list_id)
        for band in (b for b in BANDS if b in used):
            for word in wordlist.words:
                ensure_generation(repo, band, word)


def on_profile_changed(repo: Repository, profile: Profile) -> None:
    """A profile was created, re-banded, or got new lists: its lists × its band."""
    for list_id in dict.fromkeys(profile.list_ids):
        wordlist = repo.get_list(list_id)
        if wordlist is None:
            continue
        for word in wordlist.words:
            ensure_generation(repo, profile.band, word)


def regenerate(repo: Repository, band: str, word: str, part: str) -> None:
    """Parent "Regenerate": build the replacement alongside the current version (spec §7.1)."""
    if part not in REGENERATE_PARTS:
        raise ValueError(f"unknown regenerate part: {part!r}")
    content = repo.get_content(band, word)
    if content is None or content.card is None:
        raise ValueError(f"no Learn card to regenerate for {band}:{word}")
    current = content.content_version
    if part == "image":
        repo.set_image(band, word, content.image_key, "pending", current)
        repo.enqueue_job("image", band, word, current, [])
        return
    # A regeneration that is already in flight is superseded by moving to a newer version. A version that still
    # holds stored questions (a superseded draft's leftovers) is never reused: its pool may belong to another card.
    target = max(current, content.draft_version or 0, repo.max_question_version(band, word)) + 1
    if part == "questions":
        if content.error:
            content.error = ""
            repo.save_content(content)
        repo.save_draft(band, word, content.card, target)
        repo.enqueue_job("questions", band, word, target, [])
        return
    content.draft = None
    content.draft_version = target
    content.error = ""
    repo.save_content(content)
    chain = list(FULL_CHAIN) if part == "all" else ["questions"]
    repo.enqueue_job("learn", band, word, target, chain)


def maybe_enqueue_topup(repo: Repository, band: str, word: str, today_utc: str) -> bool:
    """Learner-driven pool top-up, at most once per word × band per UTC day; True if enqueued."""
    content = repo.get_content(band, word)
    if content is None or content.status != "ready" or content.card is None:
        return False
    if content.draft_version is not None:
        return False  # a regeneration will replace this pool soon
    if len(repo.get_pool(band, word)) >= POOL_CAP:
        return False
    job = repo.get_job(f"topup:{band}:{word}")
    if job is not None:
        if job.status in _LIVE:
            return False
        if job.updated_at[:10] == today_utc:
            return False  # one already ran (done or failed) today
    repo.enqueue_job("topup", band, word, content.content_version, [])
    return True

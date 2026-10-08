"""Storage interfaces (spec §6.3).

Every caller outside app/storage/ talks to persistence only through these two ABCs, so Phase 2 can
swap SQLite + local files for Firestore + Cloud Storage without touching business code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from app.models import (
    AnswerEvent,
    Job,
    LearnCard,
    Profile,
    Question,
    Session,
    WordContent,
    WordList,
    WordProgress,
)

ApplyFn = Callable[[Session, dict[str, WordProgress], list[AnswerEvent]], None]


class Repository(ABC):
    """All records of the app. Every method is synchronous; async callers use asyncio.to_thread."""

    # ---- profiles -------------------------------------------------------------------------
    @abstractmethod
    def list_profiles(self) -> list[Profile]:
        """All profiles ordered by created_at (ties: insertion order)."""

    @abstractmethod
    def get_profile(self, profile_id: str) -> Profile | None:
        """The profile, or None."""

    @abstractmethod
    def save_profile(self, p: Profile) -> None:
        """Insert or replace the profile."""

    @abstractmethod
    def delete_profile(self, profile_id: str) -> None:
        """Delete the profile and its progress, sessions and events (one transaction)."""

    # ---- lists ----------------------------------------------------------------------------
    @abstractmethod
    def list_lists(self) -> list[WordList]:
        """All word lists ordered by created_at (ties: insertion order)."""

    @abstractmethod
    def get_list(self, list_id: str) -> WordList | None:
        """The list, or None."""

    @abstractmethod
    def save_list(self, wl: WordList) -> None:
        """Insert or replace the list."""

    @abstractmethod
    def delete_list(self, list_id: str) -> None:
        """Delete the list and remove its id from every profile.list_ids (one transaction)."""

    # ---- content --------------------------------------------------------------------------
    @abstractmethod
    def get_content(self, band: str, word: str) -> WordContent | None:
        """The WordContent with key "{band}:{word}", or None."""

    @abstractmethod
    def get_contents(self, band: str, words: list[str]) -> dict[str, WordContent]:
        """{word: content} for the words that have content in this band (missing words are absent)."""

    @abstractmethod
    def save_content(self, c: WordContent) -> None:
        """Insert or replace the whole record (error text is redacted)."""

    @abstractmethod
    def list_content_by_status(self, status: str) -> list[WordContent]:
        """Contents with this status, ordered by band then word."""

    @abstractmethod
    def list_contents(self) -> list[WordContent]:
        """All contents, ordered by band then word."""

    @abstractmethod
    def save_draft(self, band: str, word: str, card: LearnCard, version: int) -> None:
        """Set draft := card and draft_version := version. KeyError if the content does not exist."""

    @abstractmethod
    def swap_draft(self, band: str, word: str, version: int) -> bool:
        """Atomic. Only if draft is not None and draft_version == version: card := draft,
        content_version := version, draft := None, draft_version := None, status := "ready", error := "",
        and delete this word's questions with content_version < version. Returns True iff swapped."""

    @abstractmethod
    def set_image(self, band: str, word: str, key: str | None, status: str, version: int) -> bool:
        """Set image_key and image_status only when content_version == version. Returns True iff updated."""

    # ---- questions ------------------------------------------------------------------------
    @abstractmethod
    def get_pool(self, band: str, word: str, version: int | None = None) -> list[Question]:
        """Questions of one version (None → the active content_version; [] if no content), by created_at."""

    @abstractmethod
    def get_pools(self, band: str, words: list[str]) -> dict[str, list[Question]]:
        """{word: active-version pool} for every requested word ([] when it has no content)."""

    @abstractmethod
    def add_questions(self, band: str, word: str, version: int, qs: list[Question]) -> None:
        """Store questions (ValueError if a question's band/word/content_version differ from the arguments)."""

    @abstractmethod
    def delete_questions(self, band: str, word: str, version: int) -> int:
        """Delete every question of this word × band at exactly this content_version. Returns how many were deleted."""

    @abstractmethod
    def max_question_version(self, band: str, word: str) -> int:
        """Highest content_version among the stored questions of this word × band; 0 when there are none."""

    # ---- progress -------------------------------------------------------------------------
    @abstractmethod
    def get_progress(self, profile_id: str, words: list[str]) -> dict[str, WordProgress]:
        """{word: progress} for the words that have stored progress (missing words are absent)."""

    @abstractmethod
    def list_progress(self, profile_id: str) -> list[WordProgress]:
        """All progress of the profile, ordered by word."""

    # ---- sessions -------------------------------------------------------------------------
    @abstractmethod
    def save_session(self, s: Session) -> None:
        """Insert or replace the session."""

    @abstractmethod
    def get_session(self, session_id: str) -> Session | None:
        """The session, or None."""

    @abstractmethod
    def list_sessions(self, profile_id: str, limit: int) -> list[Session]:
        """The profile's sessions, newest started_at first, at most `limit`."""

    # ---- events ---------------------------------------------------------------------------
    @abstractmethod
    def apply_events(self, session_id: str, events: list[AnswerEvent], apply_fn: ApplyFn) -> list[str]:
        """ONE transaction: load the session (KeyError if missing); keep events whose client_event_id is
        not stored yet; load WordProgress for their words (missing → WordProgress(profile_id, word));
        call apply_fn(session, progress_by_word, new_events); insert the new events, save every progress
        in the dict and the session; commit. Returns the ids of ALL given events (new + already stored).
        On any exception nothing is written."""

    @abstractmethod
    def list_events(self, profile_id: str, since_date: str) -> list[AnswerEvent]:
        """The profile's events with local_date >= since_date, ordered by local_date then at."""

    # ---- jobs -----------------------------------------------------------------------------
    @abstractmethod
    def enqueue_job(self, kind: str, band: str, word: str, target_version: int, chain: list[str]) -> None:
        """No-op if job "{kind}:{band}:{word}" is pending/running with target_version >= the given one;
        otherwise upsert it as pending with attempts=0, last_error="", not_before="", lease_until="",
        lease_token="" (a running job replaced this way is no longer owned by its worker)."""

    @abstractmethod
    def claim_next_job(self, now: str, lease_s: int) -> Job | None:
        """Atomically claim (pending and not_before <= now) or (running and lease_until < now);
        non-topup kinds first, then created_at. Sets status running, lease_until = now + lease_s and a
        fresh random lease_token (uuid4 hex), and returns the claimed Job carrying that token."""

    @abstractmethod
    def get_job(self, key: str) -> Job | None:
        """The job with key "{kind}:{band}:{word}", or None."""

    # finish_job / fail_job / defer_job apply ONLY when the stored job is "running" with exactly this
    # lease_token (the worker still owns it). Otherwise — missing key, re-enqueued by a Regenerate, or
    # lease expired and re-claimed by another worker — they change nothing and return False.
    @abstractmethod
    def finish_job(self, key: str, lease_token: str) -> bool:
        """Status done; lease_until and lease_token cleared. True iff applied."""

    @abstractmethod
    def fail_job(self, key: str, lease_token: str, error: str, retry_at: str | None) -> bool:
        """attempts += 1; last_error = error (redacted); retry_at → pending + not_before; None → failed.
        Lease cleared. True iff applied."""

    @abstractmethod
    def defer_job(self, key: str, lease_token: str, not_before: str) -> bool:
        """Status pending with not_before set; attempts unchanged; lease cleared. True iff applied."""

    @abstractmethod
    def job_counts(self) -> dict[str, int]:
        """{"pending": n, "running": n, "done": n, "failed": n} — all four keys always present."""

    # ---- usage ----------------------------------------------------------------------------
    @abstractmethod
    def incr_ai_calls(self, utc_date: str) -> int:
        """Increment and return the outbound AI request count for this UTC date."""

    @abstractmethod
    def get_ai_calls(self, utc_date: str) -> int:
        """The outbound AI request count for this UTC date (0 if none)."""

    # ---- auth -----------------------------------------------------------------------------
    @abstractmethod
    def incr_auth_failure(self, scope: str, ip: str, window_start: str) -> int:
        """Increment and return the failure count for key "{scope}:{ip}:{window_start}"."""

    @abstractmethod
    def clear_auth_failures(self, scope: str, ip: str) -> None:
        """Delete every failure window of this scope and ip."""

    # ---- backup ---------------------------------------------------------------------------
    @abstractmethod
    def export_all(self) -> dict:
        """{"format": "wordquest-backup", "version": 1, "profiles", "lists", "contents", "questions",
        "progress", "sessions", "events"} — lists of model_dump(mode="json") dicts; no jobs/auth/usage."""

    @abstractmethod
    def import_all(self, data: dict) -> None:
        """Validate format/version/records (ValueError), then atomically replace the 7 collections
        and delete all jobs."""


class BlobStore(ABC):
    """Binary objects (images) addressed by keys like "images/6-8/brave-v1.webp"."""

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store (or overwrite) the object."""

    @abstractmethod
    def url_for(self, key: str) -> str:
        """The URL the browser uses to fetch the object."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """True if the object is stored."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the object (no error if it is missing)."""

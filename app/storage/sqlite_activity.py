"""Progress, sessions, answer events, jobs, usage counters, auth failures and backup for SqliteRepository.

ActivityMixin is mixed into SqliteRepository together with CatalogMixin and SqliteDB (which provides
_conn(), _tx(), _fetchone() and _fetchall()).
"""
from __future__ import annotations

import sqlite3
import uuid
from typing import Callable

from app import clock
from app.models import AnswerEvent, Job, Profile, Question, Session, WordContent, WordList, WordProgress
from app.security import redact
from app.storage.base import ApplyFn
from app.storage.sqlite_db import chunked, placeholders

JOB_STATUSES: tuple[str, ...] = ("pending", "running", "done", "failed")
BACKUP_FORMAT = "wordquest-backup"
BACKUP_VERSION = 1
BACKUP_COLLECTIONS: tuple[str, ...] = (
    "profiles", "lists", "contents", "questions", "progress", "sessions", "events",
)


class ActivityMixin:
    # ---- progress ---------------------------------------------------------------------------
    def _put_progress(self, conn: sqlite3.Connection, p: WordProgress) -> None:
        conn.execute(
            "INSERT INTO progress (key, profile_id, word, data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET profile_id = excluded.profile_id, word = excluded.word, "
            "data = excluded.data",
            (p.key, p.profile_id, p.word, p.model_dump_json()),
        )

    def get_progress(self, profile_id: str, words: list[str]) -> dict[str, WordProgress]:
        wanted = list(dict.fromkeys(words))
        found: dict[str, WordProgress] = {}
        keys = [f"{profile_id}:{w}" for w in wanted]
        for chunk in chunked(keys):
            rows = self._fetchall(
                f"SELECT data FROM progress WHERE key IN ({placeholders(len(chunk))})", chunk
            )
            for (data,) in rows:
                p = WordProgress.model_validate_json(data)
                found[p.word] = p
        return {w: found[w] for w in wanted if w in found}

    def list_progress(self, profile_id: str) -> list[WordProgress]:
        rows = self._fetchall(
            "SELECT data FROM progress WHERE profile_id = ? ORDER BY word", (profile_id,)
        )
        return [WordProgress.model_validate_json(data) for (data,) in rows]

    # ---- sessions ---------------------------------------------------------------------------
    def _put_session(self, conn: sqlite3.Connection, s: Session) -> None:
        conn.execute(
            "INSERT INTO sessions (id, profile_id, started_at, data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET profile_id = excluded.profile_id, "
            "started_at = excluded.started_at, data = excluded.data",
            (s.id, s.profile_id, s.started_at, s.model_dump_json()),
        )

    def save_session(self, s: Session) -> None:
        with self._tx() as conn:
            self._put_session(conn, s)

    def get_session(self, session_id: str) -> Session | None:
        row = self._fetchone("SELECT data FROM sessions WHERE id = ?", (session_id,))
        return Session.model_validate_json(row[0]) if row else None

    def list_sessions(self, profile_id: str, limit: int) -> list[Session]:
        rows = self._fetchall(
            "SELECT data FROM sessions WHERE profile_id = ? ORDER BY started_at DESC, rowid DESC LIMIT ?",
            (profile_id, max(0, int(limit))),
        )
        return [Session.model_validate_json(data) for (data,) in rows]

    # ---- events -----------------------------------------------------------------------------
    def _put_event(self, conn: sqlite3.Connection, e: AnswerEvent) -> None:
        conn.execute(
            "INSERT INTO events (client_event_id, profile_id, session_id, local_date, at, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (e.client_event_id, e.profile_id, e.session_id, e.local_date, e.at, e.model_dump_json()),
        )

    def apply_events(self, session_id: str, events: list[AnswerEvent], apply_fn: ApplyFn) -> list[str]:
        given_ids = list(dict.fromkeys(e.client_event_id for e in events))
        with self._tx() as conn:
            session = self.get_session(session_id)
            if session is None:
                raise KeyError(session_id)
            for e in events:
                if e.session_id != session_id or e.profile_id != session.profile_id:
                    raise ValueError(
                        f"event {e.client_event_id} does not belong to session {session_id}"
                    )
            taken: set[str] = set()
            for chunk in chunked(given_ids):
                rows = self._fetchall(
                    "SELECT client_event_id FROM events "
                    f"WHERE client_event_id IN ({placeholders(len(chunk))})",
                    chunk,
                )
                taken.update(r[0] for r in rows)
            new_events: list[AnswerEvent] = []
            for e in events:
                if e.client_event_id not in taken:
                    taken.add(e.client_event_id)
                    new_events.append(e)
            if not new_events:
                return given_ids  # everything was already applied: nothing to write
            words = list(dict.fromkeys(e.word for e in new_events))
            stored = self.get_progress(session.profile_id, words)
            progress = {
                w: stored[w] if w in stored else WordProgress(profile_id=session.profile_id, word=w)
                for w in words
            }
            apply_fn(session, progress, new_events)
            for e in new_events:
                self._put_event(conn, e)
            for p in progress.values():
                self._put_progress(conn, p)
            self._put_session(conn, session)
        return given_ids

    def list_events(self, profile_id: str, since_date: str) -> list[AnswerEvent]:
        rows = self._fetchall(
            "SELECT data FROM events WHERE profile_id = ? AND local_date >= ? "
            "ORDER BY local_date, at, rowid",
            (profile_id, since_date),
        )
        return [AnswerEvent.model_validate_json(data) for (data,) in rows]

    # ---- jobs -------------------------------------------------------------------------------
    def _put_job(self, conn: sqlite3.Connection, job: Job) -> None:
        conn.execute(
            "INSERT INTO jobs (key, kind, status, not_before, lease_until, created_at, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET kind = excluded.kind, status = excluded.status, "
            "not_before = excluded.not_before, lease_until = excluded.lease_until, "
            "created_at = excluded.created_at, data = excluded.data",
            (job.key, job.kind, job.status, job.not_before, job.lease_until, job.created_at,
             job.model_dump_json()),
        )

    def _update_owned_job(self, key: str, lease_token: str, change: Callable[[Job], dict]) -> bool:
        """Apply change(job) as a field update only while the caller still owns the job: the stored record
        is "running" with the same non-empty lease_token. Otherwise write nothing and return False."""
        with self._tx() as conn:
            job = self.get_job(key)
            if job is None or job.status != "running" or not lease_token or job.lease_token != lease_token:
                return False
            updates = change(job)
            updates.update(lease_until="", lease_token="", updated_at=clock.utc_now_iso())
            self._put_job(conn, job.model_copy(update=updates))
            return True

    def enqueue_job(self, kind: str, band: str, word: str, target_version: int, chain: list[str]) -> None:
        key = f"{kind}:{band}:{word}"
        with self._tx() as conn:
            current = self.get_job(key)
            if (current is not None and current.status in ("pending", "running")
                    and current.target_version >= target_version):
                return
            now = clock.utc_now_iso()
            job = Job(
                kind=kind, band=band, word=word, target_version=target_version, chain=list(chain),
                status="pending", attempts=0, last_error="", not_before="", lease_until="", lease_token="",
                created_at=now, updated_at=now,
            )
            self._put_job(conn, job)

    def claim_next_job(self, now: str, lease_s: int) -> Job | None:
        with self._tx() as conn:
            row = self._fetchone(
                "SELECT data FROM jobs "
                "WHERE (status = 'pending' AND not_before <= ?) OR (status = 'running' AND lease_until < ?) "
                # Finish words that are already under way before starting new ones: pictures for ready words,
                # then questions for words with a card, then new cards; learner top-ups last.
                "ORDER BY CASE kind WHEN 'image' THEN 0 WHEN 'questions' THEN 1 WHEN 'learn' THEN 2 ELSE 3 END, "
                "created_at, rowid LIMIT 1",
                (now, now),
            )
            if row is None:
                return None
            job = Job.model_validate_json(row[0]).model_copy(
                update={
                    "status": "running",
                    "lease_until": clock.add_seconds_iso(clock.parse_iso(now), lease_s),
                    "lease_token": uuid.uuid4().hex,  # a new owner on every claim
                    "updated_at": now,
                }
            )
            self._put_job(conn, job)
            return job

    def get_job(self, key: str) -> Job | None:
        row = self._fetchone("SELECT data FROM jobs WHERE key = ?", (key,))
        return Job.model_validate_json(row[0]) if row else None

    def finish_job(self, key: str, lease_token: str) -> bool:
        return self._update_owned_job(key, lease_token, lambda job: {"status": "done"})

    def fail_job(self, key: str, lease_token: str, error: str, retry_at: str | None) -> bool:
        def change(job: Job) -> dict:
            updates = {"attempts": job.attempts + 1, "last_error": redact(error)}
            if retry_at is None:
                updates["status"] = "failed"
            else:
                updates.update(status="pending", not_before=retry_at)
            return updates

        return self._update_owned_job(key, lease_token, change)

    def defer_job(self, key: str, lease_token: str, not_before: str) -> bool:
        return self._update_owned_job(key, lease_token, lambda job: {"status": "pending", "not_before": not_before})

    def wake_jobs(self, kinds: list[str]) -> int:
        kinds = sorted(set(kinds))
        if not kinds:
            return 0
        with self._tx() as conn:
            rows = self._fetchall(
                "SELECT data FROM jobs WHERE status = 'pending' AND not_before != '' "
                f"AND kind IN ({placeholders(len(kinds))})",
                kinds,
            )
            now = clock.utc_now_iso()
            for (data,) in rows:
                job = Job.model_validate_json(data)
                self._put_job(conn, job.model_copy(update={"not_before": "", "updated_at": now}))
            return len(rows)

    def job_counts(self) -> dict[str, int]:
        counts = {status: 0 for status in JOB_STATUSES}
        for status, n in self._fetchall("SELECT status, COUNT(*) FROM jobs GROUP BY status"):
            counts[status] = n
        return counts

    # ---- usage ------------------------------------------------------------------------------
    def incr_ai_calls(self, utc_date: str) -> int:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO ai_usage (utc_date, count) VALUES (?, 1) "
                "ON CONFLICT(utc_date) DO UPDATE SET count = count + 1",
                (utc_date,),
            )
            return int(self._fetchone("SELECT count FROM ai_usage WHERE utc_date = ?", (utc_date,))[0])

    def get_ai_calls(self, utc_date: str) -> int:
        row = self._fetchone("SELECT count FROM ai_usage WHERE utc_date = ?", (utc_date,))
        return int(row[0]) if row else 0

    # ---- auth failures ----------------------------------------------------------------------
    def incr_auth_failure(self, scope: str, ip: str, window_start: str) -> int:
        key = f"{scope}:{ip}:{window_start}"
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO auth_failures (key, count) VALUES (?, 1) "
                "ON CONFLICT(key) DO UPDATE SET count = count + 1",
                (key,),
            )
            return int(self._fetchone("SELECT count FROM auth_failures WHERE key = ?", (key,))[0])

    def clear_auth_failures(self, scope: str, ip: str) -> None:
        prefix = f"{scope}:{ip}:"
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM auth_failures WHERE substr(key, 1, ?) = ?", (len(prefix), prefix)
            )

    # ---- backup -----------------------------------------------------------------------------
    def export_all(self) -> dict:
        with self._tx(write=False):
            questions = self._fetchall(
                "SELECT data FROM questions ORDER BY band, word, version, created_at, rowid"
            )
            progress = self._fetchall("SELECT data FROM progress ORDER BY profile_id, word")
            sessions = self._fetchall("SELECT data FROM sessions ORDER BY started_at, rowid")
            events = self._fetchall("SELECT data FROM events ORDER BY profile_id, local_date, at, rowid")
            return {
                "format": BACKUP_FORMAT,
                "version": BACKUP_VERSION,
                "profiles": [p.model_dump(mode="json") for p in self.list_profiles()],
                "lists": [wl.model_dump(mode="json") for wl in self.list_lists()],
                "contents": [c.model_dump(mode="json") for c in self.list_contents()],
                "questions": [Question.model_validate_json(d).model_dump(mode="json") for (d,) in questions],
                "progress": [WordProgress.model_validate_json(d).model_dump(mode="json") for (d,) in progress],
                "sessions": [Session.model_validate_json(d).model_dump(mode="json") for (d,) in sessions],
                "events": [AnswerEvent.model_validate_json(d).model_dump(mode="json") for (d,) in events],
            }

    def import_all(self, data: dict) -> None:
        if not isinstance(data, dict) or data.get("format") != BACKUP_FORMAT:
            raise ValueError("not a WordQuest backup (format must be 'wordquest-backup')")
        if data.get("version") != BACKUP_VERSION:
            raise ValueError(f"unsupported backup version: {data.get('version')!r}")
        for name in BACKUP_COLLECTIONS:
            if not isinstance(data.get(name), list):
                raise ValueError(f"backup is missing the '{name}' list")
        # Validate everything before touching the database (pydantic ValidationError is a ValueError).
        profiles = [Profile.model_validate(x) for x in data["profiles"]]
        lists = [WordList.model_validate(x) for x in data["lists"]]
        contents = [WordContent.model_validate(x) for x in data["contents"]]
        questions = [Question.model_validate(x) for x in data["questions"]]
        progress = [WordProgress.model_validate(x) for x in data["progress"]]
        sessions = [Session.model_validate(x) for x in data["sessions"]]
        events = [AnswerEvent.model_validate(x) for x in data["events"]]
        # A repeated primary key is a conflict, not a merge: the upserts below would silently keep one copy.
        for name, keys in (
            ("profiles", [p.id for p in profiles]),
            ("lists", [wl.id for wl in lists]),
            ("contents", [c.key for c in contents]),
            ("questions", [q.id for q in questions]),
            ("progress", [wp.key for wp in progress]),
            ("sessions", [s.id for s in sessions]),
            ("events", [e.client_event_id for e in events]),
        ):
            seen: set[str] = set()
            for key in keys:
                if key in seen:
                    raise ValueError(f"backup has conflicting records: duplicate {name} {key!r}")
                seen.add(key)
        try:
            with self._tx() as conn:
                for table in BACKUP_COLLECTIONS + ("jobs",):
                    conn.execute(f"DELETE FROM {table}")
                for p in profiles:
                    self.save_profile(p)
                for wl in lists:
                    self.save_list(wl)
                for c in contents:
                    self.save_content(c)
                for q in questions:
                    self._put_question(conn, q)
                for wp in progress:
                    self._put_progress(conn, wp)
                for s in sessions:
                    self._put_session(conn, s)
                for e in events:
                    self._put_event(conn, e)
        except sqlite3.IntegrityError as exc:  # not expected: duplicate keys are rejected above
            raise ValueError(f"backup has conflicting records: {exc}") from exc

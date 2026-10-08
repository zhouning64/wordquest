"""Profiles, word lists, word content and question pools for SqliteRepository.

CatalogMixin is mixed into SqliteRepository together with SqliteDB, which provides
_conn(), _tx(), _fetchone() and _fetchall().
"""
from __future__ import annotations

import sqlite3

from app.models import LearnCard, Profile, Question, WordContent, WordList
from app.security import redact
from app.storage.sqlite_db import chunked, placeholders


def content_key(band: str, word: str) -> str:
    """Same format as WordContent.key."""
    return f"{band}:{word}"


class CatalogMixin:
    # ---- profiles ---------------------------------------------------------------------------
    def list_profiles(self) -> list[Profile]:
        rows = self._fetchall("SELECT data FROM profiles ORDER BY created_at, rowid")
        return [Profile.model_validate_json(data) for (data,) in rows]

    def get_profile(self, profile_id: str) -> Profile | None:
        row = self._fetchone("SELECT data FROM profiles WHERE id = ?", (profile_id,))
        return Profile.model_validate_json(row[0]) if row else None

    def save_profile(self, p: Profile) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO profiles (id, created_at, data) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET created_at = excluded.created_at, data = excluded.data",
                (p.id, p.created_at, p.model_dump_json()),
            )

    def delete_profile(self, profile_id: str) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM progress WHERE profile_id = ?", (profile_id,))
            conn.execute("DELETE FROM sessions WHERE profile_id = ?", (profile_id,))
            conn.execute("DELETE FROM events WHERE profile_id = ?", (profile_id,))
            conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))

    # ---- lists ------------------------------------------------------------------------------
    def list_lists(self) -> list[WordList]:
        rows = self._fetchall("SELECT data FROM lists ORDER BY created_at, rowid")
        return [WordList.model_validate_json(data) for (data,) in rows]

    def get_list(self, list_id: str) -> WordList | None:
        row = self._fetchone("SELECT data FROM lists WHERE id = ?", (list_id,))
        return WordList.model_validate_json(row[0]) if row else None

    def save_list(self, wl: WordList) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO lists (id, created_at, data) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET created_at = excluded.created_at, data = excluded.data",
                (wl.id, wl.created_at, wl.model_dump_json()),
            )

    def delete_list(self, list_id: str) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM lists WHERE id = ?", (list_id,))
            for p in self.list_profiles():
                if list_id in p.list_ids:
                    kept = [x for x in p.list_ids if x != list_id]
                    self.save_profile(p.model_copy(update={"list_ids": kept}))

    # ---- content ----------------------------------------------------------------------------
    def _put_content(self, conn: sqlite3.Connection, c: WordContent) -> None:
        if c.error:
            c = c.model_copy(update={"error": redact(c.error)})
        conn.execute(
            "INSERT INTO contents (key, band, word, status, data) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET band = excluded.band, word = excluded.word, "
            "status = excluded.status, data = excluded.data",
            (c.key, c.band, c.word, c.status, c.model_dump_json()),
        )

    def get_content(self, band: str, word: str) -> WordContent | None:
        row = self._fetchone("SELECT data FROM contents WHERE key = ?", (content_key(band, word),))
        return WordContent.model_validate_json(row[0]) if row else None

    def get_contents(self, band: str, words: list[str]) -> dict[str, WordContent]:
        wanted = list(dict.fromkeys(words))
        found: dict[str, WordContent] = {}
        keys = [content_key(band, w) for w in wanted]
        for chunk in chunked(keys):
            rows = self._fetchall(
                f"SELECT data FROM contents WHERE key IN ({placeholders(len(chunk))})", chunk
            )
            for (data,) in rows:
                c = WordContent.model_validate_json(data)
                found[c.word] = c
        return {w: found[w] for w in wanted if w in found}

    def save_content(self, c: WordContent) -> None:
        with self._tx() as conn:
            self._put_content(conn, c)

    def list_content_by_status(self, status: str) -> list[WordContent]:
        rows = self._fetchall(
            "SELECT data FROM contents WHERE status = ? ORDER BY band, word", (status,)
        )
        return [WordContent.model_validate_json(data) for (data,) in rows]

    def list_contents(self) -> list[WordContent]:
        rows = self._fetchall("SELECT data FROM contents ORDER BY band, word")
        return [WordContent.model_validate_json(data) for (data,) in rows]

    def save_draft(self, band: str, word: str, card: LearnCard, version: int) -> None:
        with self._tx() as conn:
            c = self.get_content(band, word)
            if c is None:
                raise KeyError(content_key(band, word))
            self._put_content(conn, c.model_copy(update={"draft": card, "draft_version": version}))

    def swap_draft(self, band: str, word: str, version: int) -> bool:
        with self._tx() as conn:
            c = self.get_content(band, word)
            if c is None or c.draft is None or c.draft_version != version:
                return False
            swapped = c.model_copy(
                update={
                    "card": c.draft,
                    "content_version": version,
                    "draft": None,
                    "draft_version": None,
                    "status": "ready",
                    "error": "",
                }
            )
            self._put_content(conn, swapped)
            conn.execute(
                "DELETE FROM questions WHERE band = ? AND word = ? AND version < ?",
                (band, word, version),
            )
            return True

    def set_image(self, band: str, word: str, key: str | None, status: str, version: int) -> bool:
        with self._tx() as conn:
            c = self.get_content(band, word)
            if c is None or c.content_version != version:
                return False
            data = c.model_dump()
            data.update(image_key=key, image_status=status)
            self._put_content(conn, WordContent.model_validate(data))  # rejects an unknown status
            return True

    # ---- questions --------------------------------------------------------------------------
    def get_pool(self, band: str, word: str, version: int | None = None) -> list[Question]:
        with self._tx(write=False):
            if version is None:
                c = self.get_content(band, word)
                if c is None:
                    return []
                version = c.content_version
            rows = self._fetchall(
                "SELECT data FROM questions WHERE band = ? AND word = ? AND version = ? "
                "ORDER BY created_at, rowid",
                (band, word, version),
            )
        return [Question.model_validate_json(data) for (data,) in rows]

    def get_pools(self, band: str, words: list[str]) -> dict[str, list[Question]]:
        wanted = list(dict.fromkeys(words))
        pools: dict[str, list[Question]] = {w: [] for w in wanted}
        with self._tx(write=False):
            active = {w: c.content_version for w, c in self.get_contents(band, wanted).items()}
            for chunk in chunked(list(active)):
                rows = self._fetchall(
                    "SELECT word, version, data FROM questions "
                    f"WHERE band = ? AND word IN ({placeholders(len(chunk))}) "
                    "ORDER BY created_at, rowid",
                    (band, *chunk),
                )
                for w, version, data in rows:
                    if active.get(w) == version:
                        pools[w].append(Question.model_validate_json(data))
        return pools

    def _put_question(self, conn: sqlite3.Connection, q: Question) -> None:
        conn.execute(
            "INSERT INTO questions (id, band, word, version, created_at, data) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET band = excluded.band, word = excluded.word, "
            "version = excluded.version, created_at = excluded.created_at, data = excluded.data",
            (q.id, q.band, q.word, q.content_version, q.created_at, q.model_dump_json()),
        )

    def add_questions(self, band: str, word: str, version: int, qs: list[Question]) -> None:
        for q in qs:
            if (q.band, q.word, q.content_version) != (band, word, version):
                raise ValueError(
                    f"question {q.id} is for {q.band}:{q.word} v{q.content_version}, "
                    f"not {band}:{word} v{version}"
                )
        with self._tx() as conn:
            for q in qs:
                self._put_question(conn, q)

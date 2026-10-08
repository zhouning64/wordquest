"""SQLite connection handling, transactions and schema for the Phase 1 repository.

Document-style tables: each row has a primary key, a few indexed columns, and `data` holding the
record's model_dump_json(). One connection per thread; explicit transactions via _tx().
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence, TypeVar

SCHEMA_VERSION = 1
CHUNK_SIZE = 500  # stays under SQLite's bound-parameter limit for IN (...) queries

SCHEMA: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS profiles ("
    " id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS lists ("
    " id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS contents ("
    " key TEXT PRIMARY KEY, band TEXT NOT NULL, word TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_contents_status ON contents (status)",
    "CREATE TABLE IF NOT EXISTS questions ("
    " id TEXT PRIMARY KEY, band TEXT NOT NULL, word TEXT NOT NULL, version INTEGER NOT NULL,"
    " created_at TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_questions_band_word_version ON questions (band, word, version)",
    "CREATE TABLE IF NOT EXISTS progress ("
    " key TEXT PRIMARY KEY, profile_id TEXT NOT NULL, word TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_progress_profile ON progress (profile_id)",
    "CREATE TABLE IF NOT EXISTS sessions ("
    " id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, started_at TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_profile ON sessions (profile_id)",
    "CREATE TABLE IF NOT EXISTS events ("
    " client_event_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, session_id TEXT NOT NULL,"
    " local_date TEXT NOT NULL, at TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_events_profile_date ON events (profile_id, local_date)",
    "CREATE TABLE IF NOT EXISTS jobs ("
    " key TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, not_before TEXT NOT NULL,"
    " lease_until TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status)",
    "CREATE TABLE IF NOT EXISTS ai_usage (utc_date TEXT PRIMARY KEY, count INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS auth_failures (key TEXT PRIMARY KEY, count INTEGER NOT NULL)",
)

T = TypeVar("T")


def chunked(items: Sequence[T], size: int = CHUNK_SIZE) -> Iterator[Sequence[T]]:
    """Yield consecutive slices of at most `size` items."""
    for start in range(0, len(items), size):
        yield items[start:start + size]


def placeholders(n: int) -> str:
    """A placeholder list like "?,?,?" for n bound parameters."""
    return ",".join("?" * n)


class SqliteDB:
    """Owns the database file: per-thread connections, PRAGMAs, schema, transactions."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._init_schema()

    # ---- connections ------------------------------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        """This thread's connection (opened on first use)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # check_same_thread=False only so close() can close every thread's connection;
            # each connection is still used by exactly one thread.
            conn = sqlite3.connect(
                str(self.db_path), timeout=5.0, isolation_level=None, check_same_thread=False
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
            self._local.depth = 0
            with self._conns_lock:
                self._conns.append(conn)
        return conn

    def close(self) -> None:
        """Close every connection opened by this object (used by tests and app shutdown)."""
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            conn.close()
        self._local = threading.local()

    # ---- transactions -----------------------------------------------------------------------
    @contextmanager
    def _tx(self, write: bool = True) -> Iterator[sqlite3.Connection]:
        """One explicit transaction on this thread's connection.

        write=True → BEGIN IMMEDIATE (takes the write lock up front, so read-modify-write is atomic);
        write=False → BEGIN (a consistent read snapshot). Re-entrant: a _tx() opened inside another
        _tx() on the same thread joins the outer transaction instead of issuing a second BEGIN.
        Any exception rolls the whole outer transaction back and propagates.
        """
        conn = self._conn()
        if self._local.depth > 0:
            self._local.depth += 1
            try:
                yield conn
            finally:
                self._local.depth -= 1
            return
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        self._local.depth = 1
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            self._local.depth = 0

    # ---- query helpers ----------------------------------------------------------------------
    def _fetchone(self, sql: str, params: Sequence[Any] = ()) -> tuple | None:
        return self._conn().execute(sql, tuple(params)).fetchone()

    def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        return self._conn().execute(sql, tuple(params)).fetchall()

    # ---- schema -----------------------------------------------------------------------------
    def _init_schema(self) -> None:
        (version,) = self._fetchone("PRAGMA user_version")
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema version {version} is newer than this app supports ({SCHEMA_VERSION})"
            )
        with self._tx() as conn:
            for statement in SCHEMA:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

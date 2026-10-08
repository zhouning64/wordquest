"""SQLite implementation of the storage interface (spec §6.3)."""
from __future__ import annotations

from app.storage.base import Repository
from app.storage.sqlite_activity import ActivityMixin
from app.storage.sqlite_catalog import CatalogMixin
from app.storage.sqlite_db import SqliteDB


class SqliteRepository(CatalogMixin, ActivityMixin, SqliteDB, Repository):
    """Document-style SQLite store: one connection per thread, WAL, explicit transactions.

    Constructor: SqliteRepository(db_path: Path) (from SqliteDB). Call close() on shutdown.
    """

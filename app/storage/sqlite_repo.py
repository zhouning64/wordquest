"""SQLite implementation of the storage interface (spec §6.3).

Task 4 provides the catalog half (profiles, lists, content, questions); Task 5 adds ActivityMixin
and makes this class a concrete Repository.
"""
from __future__ import annotations

from app.storage.sqlite_catalog import CatalogMixin
from app.storage.sqlite_db import SqliteDB


class SqliteRepository(CatalogMixin, SqliteDB):
    """Document-style SQLite store: one connection per thread, WAL, explicit transactions."""

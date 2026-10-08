"""Storage layer: the Repository/BlobStore interfaces (base.py) and their Phase 1 implementations
(SQLite in sqlite_*.py, files in local_blobs.py). Within app/, only modules in this package may import sqlite3.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.storage.base import ApplyFn, BlobStore, Repository
from app.storage.local_blobs import LocalBlobStore
from app.storage.sqlite_repo import SqliteRepository

if TYPE_CHECKING:
    from app.config import Settings

__all__ = ["ApplyFn", "BlobStore", "LocalBlobStore", "Repository", "SqliteRepository", "make_storage"]


def make_storage(settings: Settings) -> tuple[Repository, BlobStore]:
    """Build the configured backends. Phase 1 supports only STORAGE=local."""
    if settings.storage == "local":
        data_dir = settings.data_dir
        return SqliteRepository(data_dir / "wordquest.db"), LocalBlobStore(data_dir)
    raise ValueError(f"STORAGE={settings.storage} is Phase 2")

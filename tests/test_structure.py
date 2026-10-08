"""Architecture guards (spec §1 item 7, §14.2 "Structure")."""
from __future__ import annotations

import re
from pathlib import Path

from app.storage.base import Repository
from app.storage.sqlite_repo import SqliteRepository

ROOT = Path(__file__).resolve().parents[1]
SQLITE_IMPORT = re.compile(r"^\s*(?:import\s+sqlite3\b|from\s+sqlite3\b)", re.MULTILINE)


def sqlite_importers() -> list[str]:
    found = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        if SQLITE_IMPORT.search(path.read_text(encoding="utf-8")):
            found.append(path.relative_to(ROOT).as_posix())
    return found


def test_only_app_storage_imports_sqlite3():
    importers = sqlite_importers()
    assert "app/storage/sqlite_db.py" in importers  # the scan really sees imports
    assert [p for p in importers if not p.startswith("app/storage/")] == []


def test_sqlite_repository_is_a_concrete_repository(tmp_path):
    assert issubclass(SqliteRepository, Repository)
    assert SqliteRepository.__abstractmethods__ == frozenset()
    repo = SqliteRepository(tmp_path / "wq.db")
    assert isinstance(repo, Repository)
    repo.close()

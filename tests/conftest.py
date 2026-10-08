from __future__ import annotations

from pathlib import Path

import pytest

from app.security import clear_secrets
from app.config import Settings


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic: no Settings value leaks in from the developer's shell environment."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture(autouse=True)
def _fresh_secret_registry() -> None:
    """Every test starts with an empty redaction registry."""
    clear_secrets()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data")

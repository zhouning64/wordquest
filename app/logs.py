"""Append-only JSON-lines logs under DATA_DIR/logs (every string in every line is redacted)."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.security import redact


def _redact_value(value: Any) -> Any:
    """Redact every string in a record *before* serializing, so JSON escaping (quotes, backslashes)
    cannot hide a secret and a short numeric secret cannot corrupt a JSON number."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {_redact_key(key): _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(str(value))  # not JSON-native (datetime, Path, exception, ...): stringify, then redact


def _redact_key(key: Any) -> Any:
    if isinstance(key, str):
        return redact(key)
    if key is None or isinstance(key, (bool, int, float)):
        return key  # json.dumps turns these into string keys itself
    return redact(str(key))


class JsonlLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        line = json.dumps(_redact_value(record), ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

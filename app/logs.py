"""Append-only JSON-lines logs under DATA_DIR/logs (every line is redacted)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from app.security import redact


class JsonlLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        line = redact(json.dumps(record, ensure_ascii=False, default=str))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

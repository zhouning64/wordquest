"""Secret redaction and constant-time comparison (spec §12)."""
from __future__ import annotations

import hmac
import threading
from typing import Iterable
from urllib.parse import quote

REDACTED = "[REDACTED]"
MIN_SECRET_LEN = 4

_lock = threading.Lock()
_secrets: set[str] = set()


def register_secrets(values: Iterable[str]) -> None:
    """Add secret values to the module-level registry. Values shorter than 4 chars are ignored."""
    with _lock:
        for value in values:
            if value and len(value) >= MIN_SECRET_LEN:
                _secrets.add(value)
                encoded = quote(value, safe="")
                if encoded != value:
                    _secrets.add(encoded)  # the same secret as it appears inside a URL


def clear_secrets() -> None:
    """Empty the registry (used by the test suite between tests)."""
    with _lock:
        _secrets.clear()


def redact(text: str) -> str:
    """Replace every registered secret in text with "[REDACTED]" (longest secrets first)."""
    if not text:
        return text
    with _lock:
        ordered = sorted(_secrets, key=len, reverse=True)
    for secret in ordered:
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


def safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))

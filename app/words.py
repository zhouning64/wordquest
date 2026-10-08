"""Word-list entry normalization (spec §6.2)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.safety import find_blocked

MAX_LIST_WORDS = 600
MAX_ENTRY_CHARS = 40
MAX_TOKENS = 3

REASON_TOO_LONG = "too long"
REASON_TOO_MANY_WORDS = "more than 3 words"
REASON_BLOCKED = "not allowed"
REASON_LIST_FULL = f"list is full ({MAX_LIST_WORDS} max)"

_SPLIT_RE = re.compile(r"[\r\n,]+")
_CURLY = str.maketrans({"‘": "'", "’": "'", "ʼ": "'", "“": '"', "”": '"'})


@dataclass
class Rejected:
    entry: str  # what the parent typed (trimmed)
    reason: str


def _normalize(entry: str) -> str:
    text = entry.translate(_CURLY).strip().lower()
    # keep letters, whitespace, hyphens and apostrophes only
    text = "".join(ch for ch in text if ch.isalpha() or ch.isspace() or ch in "-'")
    # collapse whitespace; drop hyphens/apostrophes left dangling at token edges (e.g. 'frugal' or "- frugal")
    tokens = [tok.strip("-'") for tok in text.split()]
    return " ".join(tok for tok in tokens if tok)


def normalize_entries(raw: str | list[str]) -> tuple[list[str], list[Rejected]]:
    """Normalize pasted entries. Returns (accepted words in first-seen order, rejected entries with reasons).

    A str is split on newlines and commas; a list is taken entry by entry. Empty results are dropped
    silently and duplicates keep their first occurrence; nothing else is dropped without a reason.
    """
    entries = _SPLIT_RE.split(raw) if isinstance(raw, str) else list(raw)
    words: list[str] = []
    rejected: list[Rejected] = []
    seen: set[str] = set()
    for entry in entries:
        word = _normalize(entry)
        if not word or word in seen:
            continue
        seen.add(word)
        shown = entry.strip()
        if len(word) > MAX_ENTRY_CHARS:
            rejected.append(Rejected(shown, REASON_TOO_LONG))
        elif len(word.split(" ")) > MAX_TOKENS:
            rejected.append(Rejected(shown, REASON_TOO_MANY_WORDS))
        elif find_blocked(word) is not None:
            rejected.append(Rejected(shown, REASON_BLOCKED))
        elif len(words) >= MAX_LIST_WORDS:
            rejected.append(Rejected(shown, REASON_LIST_FULL))
        else:
            words.append(word)
    return words, rejected

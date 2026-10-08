"""Kid-safety blocklist matching (spec §7.5): whole-token, case-folded, including §7.7 inflections."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.ai.inflect import contains_word, tokenize, word_forms

DEFAULT_BLOCKLIST_PATH = Path(__file__).with_name("blocklist.txt")


def _normalize_term(term: str) -> str:
    return " ".join(tokenize(term))


def _parse(text: str) -> frozenset[str]:
    terms: set[str] = set()
    for line in text.splitlines():
        term = _normalize_term(line.split("#", 1)[0])
        if term:
            terms.add(term)
    return frozenset(terms)


@lru_cache(maxsize=1)
def _default_blocklist() -> frozenset[str]:
    return _parse(DEFAULT_BLOCKLIST_PATH.read_text(encoding="utf-8"))


def load_blocklist(path: Path | None = None) -> frozenset[str]:
    """Terms from path (one per line, '#' starts a comment). path=None → app/ai/blocklist.txt, cached."""
    if path is None:
        return _default_blocklist()
    return _parse(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=16)
def _index(blocklist: frozenset[str]) -> tuple[dict[str, str], tuple[str, ...]]:
    """(single-token form → entry, multi-token entries). Exact entries win over another entry's inflection."""
    entries = sorted({t for t in (_normalize_term(e) for e in blocklist) if t})
    singles: dict[str, str] = {}
    phrases: list[str] = []
    for entry in entries:
        if " " in entry:
            phrases.append(entry)
        else:
            singles[entry] = entry
    for entry in entries:
        if " " not in entry:
            for form in word_forms(entry):
                singles.setdefault(form, entry)
    return singles, tuple(phrases)


def find_blocked(text: str, blocklist: frozenset[str] | None = None) -> str | None:
    """The first blocklist entry found in text (as a whole token or an inflection of one), else None.

    Hyphenated tokens are also checked part by part ("bull-crap" hits "crap").
    """
    terms = load_blocklist() if blocklist is None else blocklist
    if not text or not terms:
        return None
    singles, phrases = _index(frozenset(terms))
    for tok in tokenize(text):
        candidates = [tok] + (tok.split("-") if "-" in tok else [])
        for cand in candidates:
            hit = singles.get(cand)
            if hit is not None:
                return hit
    for phrase in phrases:
        if contains_word(text, phrase, max_gap=0):  # blocklist phrases must be contiguous
            return phrase
    return None

"""Typed-answer grading for spell_it questions (spec §8.3)."""
from __future__ import annotations

_QUOTE_FIX = str.maketrans(
    {
        "‘": "'",  # ‘
        "’": "'",  # ’
        "ʼ": "'",  # ʼ
        "“": '"',  # “
        "”": '"',  # ”
    }
)

NEAR_MIN_LEN = 5


def normalize_answer(s: str) -> str:
    """Strip, lowercase, curly → straight quotes, collapse internal whitespace."""
    return " ".join(s.translate(_QUOTE_FIX).strip().lower().split())


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def grade_typed(given: str, accepted: list[str]) -> str:
    """Returns "correct" on an exact normalized match, "near" if within edit distance 1 of an
    accepted answer at least 5 characters long (graded as wrong, shown as "So close"), else "wrong"."""
    answer = normalize_answer(given)
    targets = [t for t in (normalize_answer(a) for a in accepted) if t]
    if not answer or not targets:
        return "wrong"
    if answer in targets:
        return "correct"
    if any(len(t) >= NEAR_MIN_LEN and levenshtein(answer, t) == 1 for t in targets):
        return "near"
    return "wrong"

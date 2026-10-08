"""Word-form matching for content checks (spec §7.7). Deliberately permissive; never used to grade typed answers."""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

# A token is a run of letters/digits, optionally joined by internal apostrophes or hyphens
# ("don't", "rock-and-roll"). Everything else (spaces, quotes, dashes, . , ! ? ; : brackets, …) separates tokens.
_TOKEN_RE = re.compile(r"[^\W_]+(?:['\-][^\W_]+)*")
# Typographic apostrophes and hyphens become their ASCII forms before tokenizing.
_CHAR_FIX = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "‐": "-", "‑": "-"})
_NO_DOUBLE = frozenset("aeiouwxy")
_PLAIN_SUFFIXES = ("s", "es", "ed", "d", "ing", "er", "est", "ly", "r", "st")


def tokenize(text: str) -> list[str]:
    """Case-folded tokens; surrounding punctuation and a trailing possessive 's are removed."""
    tokens: list[str] = []
    for tok in _TOKEN_RE.findall(text.translate(_CHAR_FIX).casefold()):
        if tok.endswith("'s") and len(tok) > 2:
            tok = tok[:-2]
        if tok:
            tokens.append(tok)
    return tokens


@lru_cache(maxsize=8192)
def _token_forms(w: str) -> frozenset[str]:
    """Rule-based forms of one lowercase token (spec §7.7), including the token itself."""
    forms = {w}
    forms.update(w + suffix for suffix in _PLAIN_SUFFIXES)
    if len(w) > 1 and w.endswith("y"):  # carry → carried/carries, happy → happier/happiest/happily
        forms.update(w[:-1] + suffix for suffix in ("ied", "ies", "ier", "iest", "ily"))
    if len(w) > 1 and w.endswith("e"):  # deplete → depleting, subtle → subtler (+r) / subtlest (+st)
        forms.update(w[:-1] + suffix for suffix in ("ing", "ed", "er", "est"))
    if len(w) > 1 and w[-1].isalpha() and w[-1] not in _NO_DOUBLE:  # stop → stopped/stopping, big → bigger
        forms.update(w + w[-1] + suffix for suffix in ("ed", "ing", "er", "est"))
    if len(w) > 2 and w.endswith("le"):  # subtle → subtly
        forms.add(w[:-2] + "ly")
    if w.endswith("ic"):  # basic → basically
        forms.add(w + "ally")
    if w.endswith("c"):  # panic → panicked/panicking
        forms.update((w + "ked", w + "king"))
    if w.endswith("ie"):  # lie → lying
        forms.add(w[:-2] + "ying")
    return frozenset(forms)


@lru_cache(maxsize=4096)
def _phrase_forms(tokens: tuple[str, ...]) -> frozenset[str]:
    if not tokens:
        return frozenset()
    out = {" ".join(tokens)}
    for i, tok in enumerate(tokens):
        for form in _token_forms(tok):
            out.add(" ".join(tokens[:i] + (form,) + tokens[i + 1 :]))
    return frozenset(out)


def word_forms(word: str) -> set[str]:
    """The (lowercase) word plus its rule-based forms. For a phrase, the inflection falls on any one token."""
    return set(_phrase_forms(tuple(tokenize(word))))


def _normalize_phrase(text: str) -> str:
    return " ".join(tokenize(text))


def valid_extra_forms(word: str, forms: Iterable[str]) -> list[str]:
    """Model-supplied forms (normalized, deduped, in order) that share the word's first 3 letters.

    Words shorter than 3 letters use the whole word as the prefix ("go" keeps "gone", rejects "went").
    """
    base = _normalize_phrase(word)
    if not base:
        return []
    prefix = base[:3]
    kept: list[str] = []
    for form in forms:
        norm = _normalize_phrase(form)
        if norm and norm.startswith(prefix) and norm not in kept:
            kept.append(norm)
    return kept


def _match_at(tokens: list[str], start: int, pattern: tuple[str, ...]) -> bool:
    """pattern matches tokens[start:start+len(pattern)] with at most one inflected position."""
    if start + len(pattern) > len(tokens):
        return False
    inflected = 0
    for offset, base in enumerate(pattern):
        tok = tokens[start + offset]
        if tok == base:
            continue
        if tok in _token_forms(base):
            inflected += 1
            if inflected > 1:
                return False
        else:
            return False
    return True


def _patterns(word: str, extra_forms: Iterable[str]) -> tuple[tuple[str, ...], list[tuple[str, ...]]]:
    base = tuple(tokenize(word))
    extras = [tuple(form.split(" ")) for form in valid_extra_forms(word, extra_forms)]
    return base, extras


def is_form_of(candidate: str, word: str, extra_forms: Iterable[str] = ()) -> bool:
    """True if the whole candidate (tokenized) is the word, a rule-based form, or a valid extra form."""
    tokens = tokenize(candidate)
    base, extras = _patterns(word, extra_forms)
    if not tokens or not base:
        return False
    if len(tokens) == len(base) and _match_at(tokens, 0, base):
        return True
    return tuple(tokens) in extras


def contains_word(text: str, word: str, extra_forms: Iterable[str] = ()) -> bool:
    """True if text contains the word or a form as whole tokens (phrases: contiguous and in order)."""
    tokens = tokenize(text)
    base, extras = _patterns(word, extra_forms)
    if not tokens or not base:
        return False
    for i in range(len(tokens)):
        if _match_at(tokens, i, base):
            return True
        for extra in extras:
            if tuple(tokens[i : i + len(extra)]) == extra:
                return True
    return False

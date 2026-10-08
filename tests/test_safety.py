from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.safety import find_blocked, load_blocklist


def test_default_blocklist_is_loaded_normalized_and_cached() -> None:
    terms = load_blocklist()
    assert isinstance(terms, frozenset)
    assert len(terms) >= 40
    assert all(t == t.strip().lower() and t and not t.startswith("#") for t in terms)
    assert {"crap", "jackass", "piss", "cocaine"} <= terms
    assert load_blocklist() is terms  # cached


def test_load_blocklist_from_custom_file_skips_comments_and_blanks(tmp_path: Path) -> None:
    path = tmp_path / "terms.txt"
    path.write_text("# a comment line\nWidget\n\n   gizmo   # trailing comment\nBad  Word\n", encoding="utf-8")
    assert load_blocklist(path) == frozenset({"widget", "gizmo", "bad word"})


@pytest.mark.parametrize(
    "text",
    [
        "The class will assess the bass player from Scunthorpe.",
        "The therapist studied an assassin bug.",
        "Grapes grow by the passage near the cockpit.",
        "The heroines of the story were brave.",
        "She let out a nervous titter.",
        "",
    ],
)
def test_innocent_text_containing_substrings_is_not_flagged(text: str) -> None:
    assert find_blocked(text) is None


@pytest.mark.parametrize(
    ("text", "entry"),
    [
        ("What a load of CRAP!", "crap"),  # case-folded
        ("He crapped out early.", "crap"),  # inflected entry (doubled consonant + ed)
        ("The jackasses laughed.", "jackass"),  # +es
        ("It was pissing down all day.", "piss"),  # +ing
        ("That was total bull-crap.", "crap"),  # part of a hyphenated token
        ("Crap's not a nice word.", "crap"),  # possessive stripped
    ],
)
def test_blocked_terms_and_their_inflections_are_flagged(text: str, entry: str) -> None:
    assert find_blocked(text) == entry


@pytest.mark.parametrize(
    ("text", "entry"),
    [
        ("What a crappy day.", "crappy"),  # -y adjectives are their own entries ...
        ("She was being bitchy.", "bitchy"),
        ("A slutty outfit.", "slutty"),
        ("The crappiest seat.", "crappy"),  # ... so their own inflections are covered too
        ("That fuckin' dog!", "fuckin"),  # dropped-g form (the trailing apostrophe is not part of the token)
        ("Fuckin dog!", "fuckin"),
        ("Fuck'em all.", "fuck"),  # apostrophe-joined tokens are checked part by part, like hyphenated ones
        ("This shit'll stop.", "shit"),
    ],
)
def test_derived_and_apostrophe_joined_forms_are_flagged(text: str, entry: str) -> None:
    assert find_blocked(text) == entry


@pytest.mark.parametrize(
    "text", ["Don't be late at five o'clock.", "We can't stop the rock'n'roll band.", "The lass'll pass the class."])
def test_apostrophe_split_leaves_innocent_contractions_alone(text: str) -> None:
    assert find_blocked(text) is None


def test_custom_blocklist_returns_the_entry_not_the_form() -> None:
    terms = frozenset({"widget"})
    assert find_blocked("Widgets everywhere!", terms) == "widget"
    assert find_blocked("A shiny widget's gears", terms) == "widget"
    assert find_blocked("A widgetry shop", terms) is None


def test_custom_blocklist_phrase_must_be_contiguous() -> None:
    terms = frozenset({"bad word"})
    assert find_blocked("Those bad words hurt.", terms) == "bad word"
    assert find_blocked("Bad, the word was.", terms) is None


def test_empty_blocklist_flags_nothing() -> None:
    assert find_blocked("crap", frozenset()) is None

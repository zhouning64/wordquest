from __future__ import annotations

import pytest

from app.learning.grading import grade_typed, levenshtein, normalize_answer


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Frugal ", "frugal"),
        ("IN   lieu\tof", "in lieu of"),
        ("o’clock", "o'clock"),
        ("‘quoted’ “words”", "'quoted' \"words\""),
        ("", ""),
    ],
)
def test_normalize_answer(raw: str, expected: str) -> None:
    assert normalize_answer(raw) == expected


@pytest.mark.parametrize(
    ("a", "b", "distance"),
    [
        ("", "", 0),
        ("abc", "abc", 0),
        ("", "abc", 3),
        ("abc", "", 3),
        ("kitten", "sitting", 3),
        ("flaw", "lawn", 2),
        ("frugal", "frugel", 1),  # substitution
        ("frugal", "frugl", 1),  # deletion
        ("frugal", "frugall", 1),  # insertion
        ("frugal", "fruagl", 2),  # transposition counts as 2
    ],
)
def test_levenshtein(a: str, b: str, distance: int) -> None:
    assert levenshtein(a, b) == distance
    assert levenshtein(b, a) == distance


def test_exact_match_is_correct() -> None:
    assert grade_typed("frugal", ["frugal"]) == "correct"


def test_case_and_surrounding_spaces_are_ignored() -> None:
    assert grade_typed("  FRUGAL  ", ["frugal"]) == "correct"


def test_curly_apostrophe_matches_straight() -> None:
    assert grade_typed("o’clock", ["o'clock"]) == "correct"
    assert grade_typed("o'clock", ["o’clock"]) == "correct"


def test_extra_internal_spaces_are_collapsed() -> None:
    assert grade_typed("in   lieu  of", ["in lieu of"]) == "correct"


def test_any_accepted_answer_counts() -> None:
    assert grade_typed("strove", ["strive", "strove", "striven"]) == "correct"


def test_near_miss_distance_one_on_long_answer() -> None:
    assert grade_typed("frugel", ["frugal"]) == "near"
    assert grade_typed("quel", ["quell"]) == "near"  # accepted answer has exactly 5 letters
    assert grade_typed("lucd", ["lucid"]) == "near"
    assert grade_typed("frugel", ["thrifty", "frugal"]) == "near"


def test_distance_two_is_wrong() -> None:
    assert grade_typed("fruagl", ["frugal"]) == "wrong"


def test_short_word_distance_one_is_wrong() -> None:
    assert grade_typed("vye", ["vie"]) == "wrong"
    assert grade_typed("for", ["four"]) == "wrong"  # accepted answer has only 4 letters


def test_empty_answer_or_no_accepted_answers_is_wrong() -> None:
    assert grade_typed("   ", ["frugal"]) == "wrong"
    assert grade_typed("frugal", []) == "wrong"

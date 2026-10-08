from __future__ import annotations

import pytest

from app.ai.inflect import contains_word, is_form_of, tokenize, valid_extra_forms, word_forms


# ---------- tokenize ----------

def test_tokenize_case_folds_and_strips_surrounding_punctuation() -> None:
    assert tokenize("The FRUGAL kid, (honestly) saved “every” coin!") == [
        "the", "frugal", "kid", "honestly", "saved", "every", "coin",
    ]


def test_tokenize_strips_possessive_straight_and_curly() -> None:
    assert tokenize("Galvani's frog") == ["galvani", "frog"]
    assert tokenize("Galvani’s frog") == ["galvani", "frog"]
    assert tokenize("the students' books") == ["the", "students", "books"]


def test_tokenize_keeps_internal_hyphens_and_apostrophes() -> None:
    assert tokenize("A well-known rock-and-roll song") == ["a", "well-known", "rock-and-roll", "song"]
    assert tokenize("Don’t stop") == ["don't", "stop"]  # curly apostrophe normalized to '


def test_tokenize_splits_on_dashes_and_glued_punctuation_and_drops_empties() -> None:
    assert tokenize("frugal—careful…end.Next") == ["frugal", "careful", "end", "next"]
    assert tokenize("  ... -- ___ !! ") == []
    assert tokenize("") == []


# ---------- word_forms (spec §7.7 rules) ----------

@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("deplete", {"deplete", "depleted", "depleting", "depletes"}),  # +d, drop-e+ing, +s
        ("frugal", {"frugally"}),  # +ly
        ("stop", {"stopped", "stopping", "stops"}),  # doubled consonant
        ("big", {"bigger", "biggest"}),  # doubled consonant + er/est
        ("panic", {"panicked", "panicking", "panics"}),  # c → ck
        ("mimic", {"mimicked", "mimicking"}),
        ("lie", {"lying", "lies", "lied"}),  # ie → ying
        ("vie", {"vying"}),
        ("subtle", {"subtler", "subtlest", "subtly"}),  # +r, +st, -le → -ly
        ("humble", {"humbly"}),
        ("basic", {"basically"}),  # -ic → -ically
        ("sporadic", {"sporadically"}),
        ("happy", {"happier", "happiest", "happily"}),  # y → ier/iest/ily
        ("carry", {"carried", "carries", "carrying"}),  # y → ied/ies, +ing
        ("hope", {"hoped", "hoping", "hopes"}),
        ("fast", {"faster", "fastest", "fasts"}),
        ("box", {"boxes"}),  # +es
        ("kindle", {"kindled", "kindling", "kindles"}),
    ],
)
def test_word_forms_include_rule_based_forms(word: str, expected: set[str]) -> None:
    forms = word_forms(word)
    assert word in forms
    assert expected <= forms


def test_word_forms_never_invent_irregular_forms() -> None:
    assert "went" not in word_forms("go")
    assert "strove" not in word_forms("strive")
    assert "stoped" in word_forms("stop")  # permissive by design; harmless extra form


def test_word_forms_lowercases_input() -> None:
    assert "depleted" in word_forms("Deplete")


def test_word_forms_of_a_phrase_inflect_one_token() -> None:
    forms = word_forms("give up")
    assert {"give up", "gives up", "giving up", "gived up"} <= forms
    assert "gives ups" not in forms


# ---------- valid_extra_forms ----------

def test_valid_extra_forms_keeps_forms_sharing_first_three_letters() -> None:
    assert valid_extra_forms("strive", ["strove", "Striven", " striving ", "drove", "", "strove"]) == [
        "strove", "striven", "striving",
    ]


def test_valid_extra_forms_short_word_uses_whole_word_as_prefix() -> None:
    assert valid_extra_forms("go", ["went", "gone", "Goes"]) == ["gone", "goes"]


def test_valid_extra_forms_phrase_keeps_an_irregular_first_token_with_the_same_rest() -> None:
    forms = ["took for granted", "takes for granted", "kept for granted", "took for free", "Taken for Granted"]
    kept = ["took for granted", "takes for granted", "taken for granted"]
    assert valid_extra_forms("take for granted", forms) == kept
    assert valid_extra_forms("give up", ["gave up", "gave in", "kept up"]) == ["gave up"]
    assert valid_extra_forms("go", ["went"]) == []  # single words keep the 3-letter rule


def test_valid_extra_forms_empty_word() -> None:
    assert valid_extra_forms("", ["anything"]) == []


# ---------- is_form_of ----------

@pytest.mark.parametrize(
    ("candidate", "word", "extra", "expected"),
    [
        ("Depleted", "deplete", (), True),
        ("  depleting. ", "deplete", (), True),
        ("depletion", "deplete", (), False),
        ("frugality", "frugal", (), False),
        ("strove", "strive", (), False),
        ("strove", "strive", ("strove",), True),
        ("went", "go", ("went",), False),  # rejected by the first-3-letters guard
        ("gone", "go", ("gone",), True),
        ("in lieu of", "in lieu of", (), True),
        ("In   Lieu  Of", "in lieu of", (), True),
        ("in lieus of", "in lieu of", (), True),  # inflection on one token
        ("lieu", "in lieu of", (), False),
        ("in place of", "in lieu of", (), False),
        ("gives ups", "give up", (), False),  # two inflected tokens
        ("", "frugal", (), False),
        ("frugal", "", (), False),
    ],
)
def test_is_form_of(candidate: str, word: str, extra: tuple[str, ...], expected: bool) -> None:
    assert is_form_of(candidate, word, extra) is expected


# ---------- contains_word ----------

@pytest.mark.parametrize(
    ("text", "word", "extra", "expected"),
    [
        ("A marathon will deplete his energy.", "deplete", (), True),
        ("Overfishing depleted the lake.", "deplete", (), True),
        ("Galvani's frogs twitched.", "galvani", (), True),
        ("She lived frugally all year.", "frugal", (), True),
        ("The crowd panicked.", "panic", (), True),
        ("The flavor was subtler than I expected.", "subtle", (), True),
        ("Basically, we won.", "basic", (), True),
        ("He was happier after lunch.", "happy", (), True),
        ("The dog was lying in the sun.", "lie", (), True),
        ("She stopped at the corner.", "stop", (), True),
        ("He was unhappy.", "happy", (), False),  # substring only
        ("Her frugality paid off.", "frugal", (), False),
        ("The scrutinizer smiled.", "scrutiny", (), False),
        ("They hiked the hinterland.", "hint", (), False),
        ("She strove to win.", "strive", (), False),
        ("She strove to win.", "strive", ("strove",), True),
        ("He went home.", "go", ("went",), False),
        ("She used a coupon in lieu of cash.", "in lieu of", (), True),
        ("In lieu of flowers, send cards.", "in lieu of", (), True),
        ("Of lieu in the end.", "in lieu of", (), False),  # wrong order
        ("in lieu the of", "in lieu of", (), False),  # the tokens after the first stay contiguous
        ("She gives up too fast.", "give up", (), True),
        ("They kept giving up.", "give up", (), True),
        ("", "frugal", (), False),
    ],
)
def test_contains_word(text: str, word: str, extra: tuple[str, ...], expected: bool) -> None:
    assert contains_word(text, word, extra) is expected


@pytest.mark.parametrize(
    ("text", "word", "extra", "expected"),
    [
        ("Aisha took her morning coffee for granted.", "take for granted", ("took for granted",), True),
        ("Zoe takes her teacher's help for granted.", "take for granted", (), True),
        ("He gave it up at last.", "give up", ("gave up",), True),
        ("We take for granted the clean water.", "take for granted", (), True),  # contiguous still matches
        ("They take it for granted.", "take for granted", (), True),
        # 5 tokens between the first token and the rest
        ("She took her big red morning coffee for granted.", "take for granted", ("took for granted",), False),
        ("For granted, she took nothing.", "take for granted", ("took for granted",), False),  # out of order
        ("She takes it for all granted.", "take for granted", (), False),  # the rest must stay contiguous
        ("Give the dog a bone and then look up.", "give up", (), False),  # 6 between
        ("He was unhappy at the happy party.", "happy", (), True),  # single words unaffected
        ("He was unhappy.", "happy", (), False),
    ],
)
def test_contains_word_separable_phrase(text: str, word: str, extra: tuple[str, ...], expected: bool) -> None:
    assert contains_word(text, word, extra) is expected


def test_contains_word_contiguous_only_when_asked() -> None:
    assert contains_word("They take it for granted.", "take for granted", max_gap=0) is False
    assert contains_word("They take for granted.", "take for granted", max_gap=0) is True


def test_is_form_of_stays_contiguous() -> None:
    assert is_form_of("gave it up", "give up", ["gave up"]) is False
    assert is_form_of("gave up", "give up", ["gave up"]) is True


def test_contains_word_with_multi_token_extra_form() -> None:
    assert contains_word("We gave in at last.", "give in", ["given in"]) is False
    assert contains_word("They have given in.", "give in", ["given in"]) is True

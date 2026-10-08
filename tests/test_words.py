from __future__ import annotations

from app.words import MAX_LIST_WORDS, Rejected, normalize_entries


def _letters(i: int) -> str:
    """0 → "a", 25 → "z", 26 → "aa", ... (letters only, so normalization keeps them distinct)."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(97 + r) + s
    return s


def test_string_input_splits_on_newlines_and_commas() -> None:
    words, rejected = normalize_entries("frugal\nbenevolent, candor\r\nlucid,,zenith")
    assert words == ["frugal", "benevolent", "candor", "lucid", "zenith"]
    assert rejected == []


def test_trim_and_lowercase() -> None:
    assert normalize_entries("   Frugal   \n MERITORIOUS ") == (["frugal", "meritorious"], [])


def test_punctuation_and_digits_are_stripped() -> None:
    assert normalize_entries("Frugal!\n(lucid)\nzenith.\nkindle?2") == (["frugal", "lucid", "zenith", "kindle"], [])


def test_curly_quotes_are_replaced_and_stray_quotes_removed() -> None:
    words, _ = normalize_entries("o’clock\n“candor”\n'jubilant'")
    assert words == ["o'clock", "candor", "jubilant"]


def test_internal_whitespace_collapses_and_hyphens_apostrophes_are_kept() -> None:
    words, _ = normalize_entries("in   lieu\tof\nWell-Known\nrock-'n'-roll")
    assert words == ["in lieu of", "well-known", "rock-'n'-roll"]


def test_empties_are_dropped_silently() -> None:
    assert normalize_entries("\n , ,\n!!!\n123\nfrugal\n") == (["frugal"], [])


def test_duplicates_keep_first_occurrence() -> None:
    words, rejected = normalize_entries("lucid\nFrugal\nfrugal!\nLUCID\nzenith")
    assert words == ["lucid", "frugal", "zenith"]
    assert rejected == []


def test_list_input_is_taken_entry_by_entry() -> None:
    words, rejected = normalize_entries(["Frugal", "frugal", "lucid, zenith"])
    assert words == ["frugal", "lucid zenith"]  # commas inside a list item are not separators
    assert rejected == []


def test_too_long_is_rejected_with_original_text() -> None:
    forty = "a" * 40
    long_entry = "  " + "B" * 41 + "  "
    words, rejected = normalize_entries([forty, long_entry])
    assert words == [forty]
    assert rejected == [Rejected(entry="B" * 41, reason="too long")]


def test_more_than_three_words_is_rejected() -> None:
    words, rejected = normalize_entries("in lieu of\nonce in a while")
    assert words == ["in lieu of"]
    assert rejected == [Rejected(entry="once in a while", reason="more than 3 words")]


def test_blocklisted_term_is_rejected_but_innocent_lookalikes_are_kept() -> None:
    words, rejected = normalize_entries("class\nCrap\nassess\nScunthorpe")
    assert words == ["class", "assess", "scunthorpe"]
    assert rejected == [Rejected(entry="Crap", reason="not allowed")]


def test_rejected_duplicates_are_reported_once() -> None:
    _, rejected = normalize_entries("crap\nCRAP!")
    assert rejected == [Rejected(entry="crap", reason="not allowed")]


def test_list_is_capped_at_600_words() -> None:
    raw = [f"word{_letters(i)}" for i in range(MAX_LIST_WORDS + 5)]
    words, rejected = normalize_entries(raw)
    assert MAX_LIST_WORDS == 600
    assert len(words) == 600
    assert words[0] == "worda" and words[-1] == raw[599]
    assert [r.entry for r in rejected] == raw[600:]
    assert {r.reason for r in rejected} == {"list is full (600 max)"}

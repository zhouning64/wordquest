from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.images.process import image_key
from app.ai.inflect import contains_word
from app.ai.validate import validate_card
from app.learning.grading import normalize_answer
from app.models import DEFAULT_EMOJI_SCENE
from app.seed_legacy import STARTER_LIST_NAME, main, parse_images, parse_word_bank, seed_legacy
from app.storage.sqlite_repo import SqliteRepository
from tests.fakes import MemoryBlobStore

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "legacy" / "index.html"
IMAGE_WORDS = {"benevolent", "frugal", "jubilant", "kindle", "scrutinize", "tenacious", "whimsical", "zenith"}
LEGACY_TYPES = {"meaning", "pick_word", "fill_blank", "spell_it", "synonym"}


class RecordingBlobs(MemoryBlobStore):
    def __init__(self) -> None:
        super().__init__()
        self.puts: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.puts[key] = (data, content_type)
        super().put(key, data, content_type)


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    repo = SqliteRepository(tmp_path_factory.mktemp("seed") / "t.db")
    blobs = RecordingBlobs()
    count = seed_legacy(repo, blobs, LEGACY)
    return repo, blobs, count


def contents_by_word(repo):
    return {c.word: c for c in repo.list_contents()}


def test_parsers_read_word_bank_and_images():
    html = LEGACY.read_text(encoding="utf-8")

    entries = parse_word_bank(html)
    assert len(entries) == 24
    assert (entries[0]["w"], entries[-1]["w"]) == ("aberration", "zenith")
    assert entries[0]["syn"] == ["anomaly", "deviation"]
    assert entries[0]["img"] is None and len(entries[0]["ex"]) == 2
    assert {e["w"] for e in entries if e["img"]} == IMAGE_WORDS

    images = parse_images(html)
    assert set(images) == IMAGE_WORDS
    assert all(uri.startswith("data:image/jpeg;base64,") for uri in images.values())


def test_seed_creates_starter_list_of_24_ready_legacy_words(seeded):
    repo, _, count = seeded
    assert count == 24
    lists = repo.list_lists()
    assert [wl.name for wl in lists] == [STARTER_LIST_NAME]
    words = lists[0].words
    assert len(words) == 24 and (words[0], words[-1]) == ("aberration", "zenith")
    contents = contents_by_word(repo)
    assert sorted(contents) == sorted(words)
    for content in contents.values():
        assert (content.band, content.status, content.source, content.content_version) == ("6-8", "ready", "legacy", 1)
        assert content.card is not None and content.draft is None


def test_card_fields_map_from_the_legacy_entry(seeded):
    repo, _, _ = seeded
    card = repo.get_content("6-8", "frugal").card
    assert card.pos == "adjective"
    assert card.short_def == "careful with money; not wasteful"
    assert card.kid_def == "careful with money and resources; not wasteful"
    assert card.examples == ["His frugal habits helped him save for a new bike.",
                             "Grandma's frugal cooking wastes nothing."]
    assert [s.model_dump() for s in card.senses] == [{
        "pos": "adjective", "definition": "careful with money; not wasteful",
        "example": "His frugal habits helped him save for a new bike.",
    }]
    assert card.synonyms == ["thrifty", "economical"] and card.antonyms == []
    assert card.word_parts.startswith("frux (Latin")
    assert (card.memory_hook, card.right_use.sentence, card.wrong_use.sentence) == ("", "", "")
    assert card.emoji_scene == DEFAULT_EMOJI_SCENE
    assert "frugally" in card.forms


def test_exactly_the_eight_embedded_images_are_stored_as_webp(seeded):
    repo, blobs, _ = seeded
    contents = contents_by_word(repo)
    assert {w for w, c in contents.items() if c.image_status == "ready"} == IMAGE_WORDS
    for word in IMAGE_WORDS:
        key = image_key("6-8", word, 1)
        assert contents[word].image_key == key
        assert blobs.exists(key)
        data, content_type = blobs.puts[key]
        assert content_type == "image/webp"
        assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    for word in set(contents) - IMAGE_WORDS:
        assert (contents[word].image_status, contents[word].image_key) == ("none", None)


def test_every_pool_has_verified_legacy_questions_including_a_synonym(seeded):
    repo, _, _ = seeded
    for word in contents_by_word(repo):
        pool = repo.get_pool("6-8", word)
        types = [q.type for q in pool]
        assert len(pool) >= 4, word
        assert "synonym" in types, word
        assert set(types) <= LEGACY_TYPES
        for q in pool:
            assert (q.verified, q.source, q.content_version, q.band) == (True, "legacy", 1, "6-8")
            if q.type == "spell_it":
                assert (q.choices, q.answer_index) == ([], -1) and q.accepted_answers
            else:
                assert len(q.choices) == 4 and len({c.lower() for c in q.choices}) == 4
                assert 0 <= q.answer_index <= 3 and q.accepted_answers == []


def test_answer_keys_point_at_the_right_content(seeded):
    repo, _, _ = seeded
    for word, content in contents_by_word(repo).items():
        card = content.card
        for q in repo.get_pool("6-8", word):
            if q.type == "spell_it":
                continue
            correct = q.choices[q.answer_index]
            distractors = [c for i, c in enumerate(q.choices) if i != q.answer_index]
            if q.type == "meaning":
                assert correct == card.short_def
            elif q.type in ("pick_word", "fill_blank"):
                assert correct == word and word not in distractors
            elif q.type == "synonym":
                assert correct == card.synonyms[0]
                assert not set(distractors) & set(card.synonyms)


def test_cards_pass_legacy_validation(seeded):
    repo, _, _ = seeded
    for word, content in contents_by_word(repo).items():
        check = validate_card(word, "6-8", content.card, legacy=True)
        assert check.card is not None, (word, check.errors)


def test_blanks_never_reveal_the_word(seeded):
    repo, _, _ = seeded
    for word, content in contents_by_word(repo).items():
        card = content.card
        for q in repo.get_pool("6-8", word):
            if q.type not in ("fill_blank", "spell_it"):
                continue
            assert q.prompt.count("___") == 1, q.prompt
            assert not contains_word(q.prompt, word, card.forms), q.prompt
            if q.type == "spell_it":
                cue = f" (means: {card.short_def})"
                assert q.prompt.endswith(cue), q.prompt
                (answer,) = q.accepted_answers
                assert answer == normalize_answer(answer)
                assert q.prompt[: -len(cue)].replace("___", answer).lower() == card.examples[0].lower()
            else:
                filled = q.prompt.replace("___", q.choices[q.answer_index])
                assert filled.lower() == card.examples[1].lower()


def test_spell_it_accepts_the_surface_form_used_in_the_sentence(seeded):
    repo, _, _ = seeded
    spell = [q for q in repo.get_pool("6-8", "resilient") if q.type == "spell_it"]
    assert len(spell) == 1
    assert spell[0].accepted_answers == ["resilient"]  # sentence starts with "Resilient"
    assert spell[0].prompt == "___ kids recover from setbacks fast. (means: able to bounce back from difficulty)"


def test_seed_is_idempotent(seeded):
    repo, _, _ = seeded
    before = {c.word: len(repo.get_pool("6-8", c.word)) for c in repo.list_contents()}

    assert seed_legacy(repo, MemoryBlobStore(), LEGACY) == 0

    assert len(repo.list_lists()) == 1
    assert {c.word: len(repo.get_pool("6-8", c.word)) for c in repo.list_contents()} == before


def test_main_seeds_the_configured_data_dir(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("STORAGE", "local")

    main()
    assert "Seeded 24 legacy starter words" in capsys.readouterr().out
    assert (data_dir / "wordquest.db").exists()
    assert (data_dir / "images" / "6-8" / "frugal-v1.webp").exists()

    main()
    assert "Seeded 0 legacy starter words" in capsys.readouterr().out

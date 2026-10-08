from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from app.ai.inflect import contains_word, is_form_of, tokenize
from app.ai.safety import find_blocked
from app.models import (
    BAND_MAX_WORDS,
    BANDS,
    CHOICE_TYPES,
    DEFAULT_EMOJI_SCENE,
    POOL_CAP,
    QTYPES,
    TIER,
    AnswerEvent,
    EventIn,
    Job,
    LearnCard,
    Profile,
    ProfileSettings,
    Question,
    Session,
    WordContent,
    WordList,
    WordProgress,
    new_id,
)
from tests.factories import make_card, make_content, make_list, make_pool, make_profile, make_question


# ---------- constants ----------

def test_constants() -> None:
    assert BANDS == ("3-5", "6-8", "9-12")
    assert QTYPES == (
        "meaning", "pick_word", "fill_blank", "usage", "scenario", "synonym", "antonym", "spell_it", "word_parts",
    )
    assert set(TIER) == set(QTYPES)
    assert [t for t in QTYPES if TIER[t] == 1] == ["meaning", "pick_word", "fill_blank"]
    assert [t for t in QTYPES if TIER[t] == 2] == ["usage", "scenario", "synonym", "antonym"]
    assert [t for t in QTYPES if TIER[t] == 3] == ["spell_it", "word_parts"]
    assert CHOICE_TYPES == frozenset(QTYPES) - {"spell_it"}
    assert BAND_MAX_WORDS == {"3-5": 15, "6-8": 20, "9-12": 28}
    assert POOL_CAP == 40
    assert DEFAULT_EMOJI_SCENE == "📖✨"


def test_new_id_is_12_hex_chars_and_unique() -> None:
    ids = {new_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(re.fullmatch(r"[0-9a-f]{12}", i) for i in ids)


# ---------- validation ----------

def test_profile_defaults() -> None:
    p = Profile(id="p1", name="Mia", band="6-8")
    assert p.avatar == "🙂"
    assert p.list_ids == []
    assert p.settings == ProfileSettings()
    assert p.settings.session_minutes == 15
    assert p.settings.new_words_per_session == 5
    assert p.settings.break_reminder is True
    assert p.settings.break_message == "Take a 10-minute break — look at something far away."


def test_profile_mutable_defaults_are_not_shared() -> None:
    a = Profile(id="a", name="A", band="3-5")
    b = Profile(id="b", name="B", band="3-5")
    a.list_ids.append("l1")
    a.settings.session_minutes = 20
    assert b.list_ids == []
    assert b.settings.session_minutes == 15


@pytest.mark.parametrize("name", ["", "x" * 31])
def test_profile_name_length_is_enforced(name: str) -> None:
    with pytest.raises(ValidationError):
        Profile(id="p1", name=name, band="6-8")


def test_profile_name_boundaries_accepted() -> None:
    assert Profile(id="p1", name="x", band="6-8").name == "x"
    assert Profile(id="p1", name="x" * 30, band="6-8").name == "x" * 30


@pytest.mark.parametrize("band", ["5-6", "K-2", "", "6–8"])
def test_band_must_be_one_of_the_literals(band: str) -> None:
    with pytest.raises(ValidationError):
        Profile(id="p1", name="Mia", band=band)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"session_minutes": 4},
        {"session_minutes": 31},
        {"new_words_per_session": -1},
        {"new_words_per_session": 11},
    ],
)
def test_profile_settings_ranges(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        ProfileSettings(**kwargs)


def test_profile_settings_boundaries_accepted() -> None:
    assert ProfileSettings(session_minutes=5, new_words_per_session=0).session_minutes == 5
    assert ProfileSettings(session_minutes=30, new_words_per_session=10).new_words_per_session == 10


@pytest.mark.parametrize("name", ["", "y" * 61])
def test_word_list_name_length_is_enforced(name: str) -> None:
    with pytest.raises(ValidationError):
        WordList(id="l1", name=name)


def test_question_type_must_be_known() -> None:
    with pytest.raises(ValidationError):
        Question(id="q1", word="frugal", band="6-8", content_version=1, type="essay", tier=1, prompt="?")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"client_event_id": "short"},  # < 8 chars
        {"client_event_id": "e" * 65},  # > 64 chars
        {"kind": "clicked"},  # unknown kind
    ],
)
def test_event_in_validation(kwargs: dict) -> None:
    data = {"client_event_id": "evt-12345678", "word": "frugal", "kind": "answer", "local_date": "2026-10-07",
            "at": "2026-10-07T14:03:00Z"}
    data.update(kwargs)
    with pytest.raises(ValidationError):
        EventIn(**data)


def test_answer_event_extends_event_in() -> None:
    e = EventIn(client_event_id="evt-12345678", word="frugal", kind="answer", correct=True,
                local_date="2026-10-07", at="2026-10-07T14:03:00Z")
    ae = AnswerEvent(**e.model_dump(), session_id="s1", profile_id="p1")
    assert ae.correct is True and ae.ms == 0 and ae.question_id is None
    assert ae.session_id == "s1" and ae.profile_id == "p1"


def test_session_defaults() -> None:
    s = Session(id="s1", profile_id="p1", mode="normal", local_date="2026-10-07",
                started_at="2026-10-07T14:00:00Z", planned_minutes=15)
    assert s.finished_at is None
    assert (s.answered, s.correct, s.unsure, s.learn_opened, s.checks_passed, s.checks_failed) == (0, 0, 0, 0, 0, 0)
    assert s.words == [] and s.new_words == [] and s.stars_up == [] and s.missed == []
    with pytest.raises(ValidationError):
        Session(id="s1", profile_id="p1", mode="cram", local_date="2026-10-07", started_at="x", planned_minutes=15)


# ---------- keys and public() ----------

def test_key_properties() -> None:
    assert WordContent(word="in lieu of", band="9-12").key == "9-12:in lieu of"
    assert WordProgress(profile_id="p1", word="frugal").key == "p1:frugal"
    assert Job(kind="learn", band="6-8", word="frugal", target_version=1).key == "learn:6-8:frugal"


def test_job_defaults() -> None:
    job = Job(kind="questions", band="3-5", word="kindle", target_version=2)
    assert job.chain == [] and job.status == "pending" and job.attempts == 0
    assert job.last_error == "" and job.not_before == "" and job.lease_until == "" and job.lease_token == ""


def test_word_content_defaults() -> None:
    c = WordContent(word="frugal", band="6-8")
    assert c.status == "pending" and c.source == "ai" and c.content_version == 1
    assert c.card is None and c.draft is None and c.draft_version is None
    assert c.image_key is None and c.image_status == "none"


def test_question_public_has_exactly_the_browser_keys() -> None:
    q = make_question("frugal", type="spell_it")
    pub = q.public()
    assert set(pub) == {"id", "type", "tier", "prompt", "choices", "answer_index", "accepted_answers", "explanation"}
    assert pub["id"] == q.id and pub["type"] == "spell_it" and pub["tier"] == 3
    assert pub["choices"] == [] and pub["answer_index"] == -1 and pub["accepted_answers"] == ["frugal"]
    pub["accepted_answers"].append("mutated")
    assert q.accepted_answers == ["frugal"]  # public() returns copies


def test_word_content_json_round_trip() -> None:
    c = make_content("frugal", draft=make_card("frugal"), draft_version=2, image_key="images/6-8/frugal-v1.webp",
                     image_status="ready")
    assert WordContent.model_validate_json(c.model_dump_json()) == c


def test_learn_card_defaults() -> None:
    card = LearnCard(pos="noun", short_def="d", kid_def="k", senses=[], examples=[])
    assert card.emoji_scene == DEFAULT_EMOJI_SCENE
    assert card.right_use.sentence == "" and card.wrong_use.sentence == "" and card.wrong_use.why == ""
    assert card.forms == [] and card.synonyms == [] and card.antonyms == []


# ---------- factories produce valid data for later tasks ----------

def _sentence_ok(s: str, band: str = "3-5") -> bool:
    return len(s.split()) <= BAND_MAX_WORDS[band] + 5 and find_blocked(s) is None


def test_make_card_satisfies_learn_card_rules() -> None:
    card = make_card("frugal")
    assert 0 < len(card.short_def) <= 90 and 0 < len(card.kid_def) <= 220
    assert len(card.senses) == 2 and all(contains_word(s.example, "frugal") for s in card.senses)
    assert len(card.examples) == 5 and all(contains_word(e, "frugal") for e in card.examples)
    assert card.synonyms and card.antonyms
    assert not any(is_form_of(x, "frugal") for x in card.synonyms + card.antonyms)
    assert contains_word(card.right_use.sentence, "frugal") and contains_word(card.wrong_use.sentence, "frugal")
    assert card.wrong_use.why
    sentences = card.examples + [s.example for s in card.senses] + [card.right_use.sentence, card.wrong_use.sentence]
    assert len(set(sentences)) == len(sentences)
    assert all(_sentence_ok(s) for s in sentences)
    assert card.forms == ["frugals"]
    assert make_card("frugal", synonyms=[]).synonyms == []  # overrides


def test_make_question_shapes() -> None:
    card = make_card("frugal")
    learn_sentences = set(card.examples)
    for qtype in QTYPES:
        q = make_question("frugal", type=qtype)
        assert q.tier == TIER[qtype] and q.verified is True and q.content_version == 1 and q.band == "6-8"
        assert len(q.explanation) <= 160 and _sentence_ok(q.prompt)
        if qtype in CHOICE_TYPES:
            assert len(q.choices) == 4 and 0 <= q.answer_index <= 3 and q.accepted_answers == []
            assert len({c.strip().casefold() for c in q.choices}) == 4
        else:
            assert q.choices == [] and q.answer_index == -1 and q.accepted_answers == ["frugal"]
        if qtype in ("fill_blank", "spell_it"):
            assert q.prompt.count("___") == 1 and not contains_word(q.prompt, "frugal")
            assert q.prompt.replace("___", "frugal") not in learn_sentences
        if qtype in ("pick_word", "fill_blank"):
            assert q.choices[q.answer_index] == "frugal"
        if qtype == "usage":
            assert all(contains_word(c, "frugal") for c in q.choices)
        if qtype == "synonym":
            assert q.choices[q.answer_index] in card.synonyms
        if qtype == "antonym":
            assert q.choices[q.answer_index] in card.antonyms
    assert make_question("frugal", type="spell_it").prompt.endswith(")")
    assert make_question("frugal", verified=False, version=3).verified is False


def test_make_pool_covers_all_tiers_with_unique_prompts_and_ordered_created_at() -> None:
    pool = make_pool("frugal")
    assert [q.type for q in pool] == list(QTYPES)
    assert {q.tier for q in pool} == {1, 2, 3}
    assert all(q.verified for q in pool)
    assert [q.created_at for q in pool] == sorted(q.created_at for q in pool)
    assert len({q.created_at for q in pool}) == len(pool)

    bigger = make_pool("frugal", band="9-12", version=2, n_per_type=2)
    assert len(bigger) == 18 and all(q.band == "9-12" and q.content_version == 2 for q in bigger)
    assert len({q.prompt for q in bigger}) == 18
    assert all(q.prompt.endswith(")") for q in bigger if q.type == "spell_it")

    tier1 = make_pool("frugal", n_per_type={"meaning": 2, "fill_blank": 1})
    assert [q.type for q in tier1] == ["meaning", "meaning", "fill_blank"]


def test_make_profile_list_and_content() -> None:
    p1, p2 = make_profile(), make_profile(name="Leo", band="3-5")
    assert p1.id != p2.id and p2.name == "Leo" and p2.band == "3-5"
    assert p1.created_at < p2.created_at
    wl = make_list(["frugal", "lucid"], name="Week 2")
    assert wl.words == ["frugal", "lucid"] and wl.name == "Week 2"
    ready = make_content("frugal")
    assert ready.status == "ready" and ready.card is not None and ready.key == "6-8:frugal"
    pending = make_content("lucid", band="3-5", status="pending")
    assert pending.card is None and pending.key == "3-5:lucid"
    assert tokenize(ready.card.examples[0])[1] == "frugal"

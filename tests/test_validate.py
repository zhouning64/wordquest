from __future__ import annotations

import pytest

from app.ai import validate
from app.ai.safety import find_blocked
from app.ai.schemas import RawQuestion
from app.ai.validate import CardCheck, Drop, sentence_word_count, validate_card, validate_questions
from app.models import DEFAULT_EMOJI_SCENE, LearnCard, RightUse, Sense, WrongUse


WORD = "frugal"
TEST_BLOCKLIST = frozenset({"zorkle"})


@pytest.fixture(autouse=True)
def fixed_blocklist(monkeypatch):
    """Use a known one-entry blocklist so these tests don't depend on the contents of blocklist.txt."""
    monkeypatch.setattr(validate, "find_blocked", lambda text, blocklist=None: find_blocked(text, TEST_BLOCKLIST))


def make_card(**overrides) -> LearnCard:
    data = dict(
        pos="adjective",
        forms=["frugally", "frugality"],
        short_def="careful with money; not wasteful",
        kid_def="A frugal person spends money carefully and does not waste food, things, or cash.",
        senses=[
            Sense(pos="adjective", definition="careful not to waste money or things",
                  example="Mia is frugal, so she saves her allowance."),
        ],
        examples=[
            "His frugal habits helped him buy a new bike.",
            "The frugal team reused old jerseys this season.",
            "Being frugal with paint, Leo finished the whole poster.",
            "Our frugal science club built a robot from spare parts.",
            "Grandpa cooks frugally and never wastes leftovers.",
        ],
        word_parts="",
        memory_hook="Frugal sounds like free-gull: a seagull that never pays for snacks.",
        synonyms=["thrifty", "economical"],
        antonyms=["wasteful", "extravagant"],
        right_use=RightUse(sentence="Being frugal, Sam fixed his old skateboard instead of buying one."),
        wrong_use=WrongUse(sentence="The frugal storm knocked down three fences.",
                           why="Frugal describes careful spending, not how strong a storm is."),
        image_scene="A kid dropping coins into a piggy bank next to a shelf of untouched toys.",
        emoji_scene="🐷🪙💰✅",
    )
    data.update(overrides)
    return LearnCard(**data)


def sentence_of(n: int) -> str:
    """An n-word sentence that uses the word."""
    assert n >= 3
    return " ".join(["Our", "frugal"] + ["friends"] * (n - 3) + ["shop."])


def errors_for(card: LearnCard, band: str = "6-8", **kw) -> list[str]:
    return validate_card(WORD, band, card, **kw).errors


# --- sentence_word_count -----------------------------------------------------------------------

def test_sentence_word_count():
    assert sentence_word_count("The frugal team reused old jerseys this season.") == 8
    assert sentence_word_count("Mom stays ___ by using coupons.") == 6
    assert sentence_word_count("Wait — it's Mia's turn!") == 4
    assert sentence_word_count(sentence_of(17)) == 17


# --- a good card ---------------------------------------------------------------------------------

def test_good_card_passes_unchanged():
    card = make_card()
    check = validate_card(WORD, "6-8", card)
    assert isinstance(check, CardCheck)
    assert check.errors == []
    assert check.card == card


def test_input_card_is_not_mutated():
    card = make_card(synonyms=["thrifty", "frugal"], emoji_scene="abc")
    before = card.model_dump()
    check = validate_card(WORD, "6-8", card)
    assert check.card is not None and check.card is not card
    assert card.model_dump() == before


# --- L1 ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "overrides",
    [
        {"short_def": "x" * 91},
        {"short_def": ""},
        {"kid_def": "y" * 221},
        {"kid_def": "   "},
    ],
)
def test_l1_definition_lengths_reject(overrides):
    check = validate_card(WORD, "6-8", make_card(**overrides))
    assert check.card is None
    assert any(e.startswith("L1") for e in check.errors)


def test_l1_boundaries_pass():
    assert errors_for(make_card(short_def="x" * 90, kid_def="y" * 220)) == []


# --- L2 ----------------------------------------------------------------------------------------------

def test_l2_needs_one_to_three_senses():
    sense = Sense(pos="adjective", definition="careful with money", example="Zoe is frugal at the book fair.")
    assert any(e.startswith("L2") for e in errors_for(make_card(senses=[])))
    assert any(e.startswith("L2") for e in errors_for(make_card(senses=[sense] * 4)))
    assert errors_for(make_card(senses=[sense] * 3)) == []


def test_l2_sense_example_must_use_word():
    bad = Sense(pos="adjective", definition="careful with money", example="Zoe saves her coins.")
    errors = errors_for(make_card(senses=[bad]))
    assert "L2: senses[0].example does not use the word" in errors


def test_l2_drops_examples_without_word_when_four_remain():
    examples = [
        "His frugal habits helped him buy a new bike.",
        "Kenji saved his coins for a month.",
        "The frugal team reused old jerseys this season.",
        "Being frugal with paint, Leo finished the whole poster.",
        "Grandpa cooks frugally and never wastes leftovers.",
    ]
    check = validate_card(WORD, "6-8", make_card(examples=examples))
    assert check.errors == []
    assert check.card.examples == [examples[0], examples[2], examples[3], examples[4]]


def test_l2_rejects_when_fewer_than_four_examples_remain():
    examples = [
        "His frugal habits helped him buy a new bike.",
        "Kenji saved his coins for a month.",
        "The frugal team reused old jerseys this season.",
        "Being frugal with paint, Leo finished the whole poster.",
    ]
    check = validate_card(WORD, "6-8", make_card(examples=examples))
    assert check.card is None
    assert "L2: only 3 examples use the word (need 4)" in check.errors


def test_l2_keeps_at_most_six_examples():
    examples = [f"Example {i}: a frugal choice." for i in range(8)]
    check = validate_card(WORD, "6-8", make_card(examples=examples))
    assert check.card.examples == examples[:6]


def test_l2_irregular_forms_count_but_hallucinated_forms_do_not():
    card = make_card(
        pos="verb",
        forms=["strives", "strove", "striven", "striving", "persevered"],
        short_def="to try very hard",
        kid_def="To strive means to work hard and keep trying to reach a goal.",
        senses=[Sense(pos="verb", definition="to try very hard", example="Kenji strives to keep his room clean.")],
        examples=[
            "Maya strove to finish the puzzle first.",
            "The team has striven for a win all season.",
            "Ben will strive to read ten books.",
            "Zoe persevered through the hard math test.",
            "Leo is striving to beat his best time.",
        ],
        synonyms=["try hard", "strive"],
        antonyms=["give up"],
        right_use=RightUse(sentence="Aisha will strive to learn the song by Friday."),
        wrong_use=WrongUse(sentence="The striving cookies were soft and warm.", why="Strive is about trying hard, which cookies cannot do."),
    )
    check = validate_card("strive", "6-8", card)
    assert check.errors == []
    assert "Zoe persevered through the hard math test." not in check.card.examples
    assert len(check.card.examples) == 4
    assert check.card.synonyms == ["try hard"]


def test_l2_l4_accept_a_separable_phrase_with_irregular_forms():
    card = make_card(
        pos="verb phrase",
        forms=["takes for granted", "took for granted", "taken for granted", "taking for granted"],
        senses=[Sense(pos="verb phrase", definition="to not notice how much something helps you",
                      example="Zoe takes her teacher's help for granted.")],
        examples=[
            "Aisha took her morning coffee for granted.",
            "Ben took the sunny days for granted until the rain came.",
            "Do you take clean water for granted?",
            "Leo never takes his friends for granted.",
        ],
        synonyms=["undervalue"],
        antonyms=["appreciate"],
        right_use=RightUse(sentence="Kenji took his old bike for granted until it broke."),
        wrong_use=WrongUse(sentence="Maya took the bus for granted to school.",
                           why="It means not valuing something, not riding it somewhere."),
    )
    check = validate_card("take for granted", "6-8", card)
    assert check.errors == []
    assert len(check.card.examples) == 4


# --- L3 ----------------------------------------------------------------------------------------------

def test_l3_removes_the_word_and_truncates_to_five():
    card = make_card(
        synonyms=["thrifty", "Frugal", "careful", "frugally", "economical", "sparing", "prudent", "saving"],
        antonyms=["wasteful", "frugal", "extravagant"],
    )
    check = validate_card(WORD, "6-8", card)
    assert check.errors == []
    assert check.card.synonyms == ["thrifty", "careful", "economical", "sparing", "prudent"]
    assert check.card.antonyms == ["wasteful", "extravagant"]


# --- L4 ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"right_use": RightUse(sentence="Sam fixed his old skateboard.")}, "L4: right_use.sentence does not use the word"),
        ({"wrong_use": WrongUse(sentence="The storm knocked down fences.", why="Storms do not spend money.")},
         "L4: wrong_use.sentence does not use the word"),
        ({"wrong_use": WrongUse(sentence="The frugal storm knocked down fences.", why=" ")}, "L4: wrong_use.why is empty"),
    ],
)
def test_l4_right_and_wrong_use(overrides, expected):
    check = validate_card(WORD, "6-8", make_card(**overrides))
    assert check.card is None
    assert expected in check.errors


# --- L5 ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("band, ok_words", [("3-5", 20), ("6-8", 25), ("9-12", 33)])
def test_l5_band_sentence_limits_with_tolerance(band, ok_words):
    assert errors_for(make_card(right_use=RightUse(sentence=sentence_of(ok_words))), band) == []
    errors = errors_for(make_card(right_use=RightUse(sentence=sentence_of(ok_words + 1))), band)
    assert errors == [f"L5: right_use.sentence has a {ok_words + 1}-word sentence (max {ok_words})"]


def test_l5_applies_to_examples_and_counts_per_sentence():
    long_example = sentence_of(21)
    two_short = "A frugal kid saves coins. Later she buys a kite with them."
    card = make_card(examples=make_card().examples[:4] + [long_example, two_short])
    errors = errors_for(card, "3-5")
    assert errors == ["L5: examples[4] has a 21-word sentence (max 20)"]


# --- L6 ----------------------------------------------------------------------------------------------

def test_l6_blocked_term_rejects_and_names_term():
    check = validate_card(WORD, "6-8", make_card(memory_hook="Frugal kids never buy a zorkle."))
    assert check.card is None
    assert check.errors == ["L6: blocked term 'zorkle' in memory_hook"]


def test_l6_matches_inflections_whole_token_only():
    assert errors_for(make_card(image_scene="Two zorkles sit on a shelf.")) == ["L6: blocked term 'zorkle' in image_scene"]
    assert errors_for(make_card(image_scene="A zorkleberry bush in a sunny garden.")) == []


# --- L7 ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("scene", ["", "   ", "piggy 🐷", "🐷2", "🐷" * 41])
def test_l7_bad_emoji_scene_is_replaced_never_rejected(scene):
    check = validate_card(WORD, "6-8", make_card(emoji_scene=scene))
    assert check.errors == []
    assert check.card.emoji_scene == DEFAULT_EMOJI_SCENE


@pytest.mark.parametrize("scene", ["🐷" * 40, "🐷 💰 ✅"])
def test_l7_good_emoji_scene_is_kept(scene):
    assert validate_card(WORD, "6-8", make_card(emoji_scene=scene)).card.emoji_scene == scene


# --- several rules / legacy --------------------------------------------------------------------------

def test_collects_every_error():
    card = make_card(short_def="x" * 95, right_use=RightUse(sentence="Sam fixed his skateboard."))
    errors = errors_for(card)
    assert [e[:2] for e in errors] == ["L1", "L4"]


def legacy_card(**overrides) -> LearnCard:
    data = dict(
        pos="adjective",
        forms=[],
        short_def="careful with money; not wasteful",
        kid_def="careful with money and resources; not wasteful",
        senses=[Sense(pos="adjective", definition="careful with money; not wasteful",
                      example="His frugal habits helped him save for a new bike.")],
        examples=["His frugal habits helped him save for a new bike.", "Grandma's frugal cooking wastes nothing."],
        word_parts="frux (Latin, fruit/value) = getting full value",
        synonyms=["thrifty", "economical"],
    )
    data.update(overrides)
    return LearnCard(**data)


def test_legacy_exempts_example_minimum_and_right_wrong_use():
    check = validate_card(WORD, "6-8", legacy_card(), legacy=True)
    assert check.errors == []
    assert len(check.card.examples) == 2
    assert check.card.emoji_scene == DEFAULT_EMOJI_SCENE


def test_same_card_without_legacy_flag_is_rejected():
    errors = errors_for(legacy_card())
    assert "L2: only 1 examples use the word (need 4)" in errors  # examples[0] repeats the sense example (L9)
    assert "L4: right_use.sentence does not use the word" in errors
    assert "L4: wrong_use.why is empty" in errors


def test_legacy_still_needs_one_example_using_the_word():
    errors = errors_for(legacy_card(examples=["Kenji saved coins."]), legacy=True)
    assert errors == ["L2: only 0 examples use the word (need 1)"]


# --- L8 forms / L9 repeated examples / typographic hyphens (cleaned, never a reason to reject) --------

def test_l8_drops_a_multi_word_form_whose_last_word_is_not_the_word():
    card = make_card(forms=["frugally", "more frugal", "most fragile", "very cheap", "frugality", "cheaper"])
    check = validate_card(WORD, "6-8", card)
    assert check.errors == []
    # one-word forms are never dropped here (irregular forms such as "mice" fail the 3-letter rule too)
    assert check.card.forms == ["frugally", "more frugal", "frugality", "cheaper"]


def test_l8_keeps_phrase_forms_and_legacy_forms():
    phrase_forms = ["takes for granted", "took for granted", "taking it for granted", "keeps in mind"]
    card = make_card(
        pos="verb phrase", forms=phrase_forms,
        senses=[Sense(pos="verb phrase", definition="to not notice how much something helps you",
                      example="Zoe takes her teacher's help for granted.")],
        examples=["Aisha took her morning coffee for granted.", "Ben took sunny days for granted.",
                  "Do you take clean water for granted?", "Leo never takes his friends for granted."],
        right_use=RightUse(sentence="Kenji took his old bike for granted until it broke."),
        wrong_use=WrongUse(sentence="Maya took the bus for granted to school.", why="It means not valuing something."),
    )
    assert validate_card("take for granted", "6-8", card).card.forms == phrase_forms
    legacy = validate_card(WORD, "6-8", legacy_card(forms=["most fragile"]), legacy=True)
    assert legacy.card.forms == ["most fragile"]


def test_l9_example_that_repeats_another_card_sentence_is_dropped():
    card = make_card()
    repeats = ["mia is FRUGAL so she saves her allowance", card.right_use.sentence, card.wrong_use.sentence + " "]
    check = validate_card(WORD, "6-8", make_card(examples=repeats + card.examples))
    assert check.errors == []
    assert check.card.examples == card.examples


def test_l9_the_example_minimum_applies_after_dropping_repeats():
    examples = make_card().examples[:3] + ["Mia is frugal, so she saves her allowance."]
    check = validate_card(WORD, "6-8", make_card(examples=examples))
    assert check.card is None
    assert "L2: only 3 examples use the word (need 4)" in check.errors


# --- L10 word_parts: phrases and invisible parts (cleaned, never a reason to reject) -------------------

@pytest.mark.parametrize(
    "parts",
    [
        "frux (fruit, value) + -al (like) = getting full value",  # "frux" is not in "frugal"
        "frug- (fruit) + -ous (full of) = full of value",  # "ous" is not in "frugal"
        "Latin frux (fruit) + -al (like)",  # the named text must appear as written
    ],
)
def test_l10_word_parts_with_a_part_not_visible_in_the_word_are_emptied(parts):
    check = validate_card(WORD, "6-8", make_card(word_parts=parts))
    assert check.errors == []
    assert check.card.word_parts == ""


@pytest.mark.parametrize(
    "parts",
    [
        "frug- (fruit, value) + -al (like) = careful with value",
        "FRUG (fruit) + al (like)",  # case-insensitive
        '"frug" (fruit) + "-al" (like)',  # quotes around a part are ignored
        "frux/frug- (fruit) + -al (like)",  # one spelling of a part that names alternatives is enough
        "frug- (fruit) + -al (like) = careful (not wasteful)",  # the combined meaning after "=" names no part
        "frug- (fruit (Latin frux) = value) + -al (like)",  # brackets and "=" inside a meaning name no part
        "careful with money",  # names no part at all
    ],
)
def test_l10_word_parts_whose_named_parts_are_all_visible_are_kept(parts):
    check = validate_card(WORD, "6-8", make_card(word_parts=parts))
    assert check.errors == []
    assert check.card.word_parts == parts


def test_l10_hyphens_are_ignored_on_both_sides():
    card = make_card(
        pos="noun", forms=[], short_def="how much you value yourself",
        kid_def="Your self-esteem is how much you like and respect yourself.",
        senses=[Sense(pos="noun", definition="respect for yourself", example="Kind words lift Ben's self-esteem.")],
        examples=["Aisha's self-esteem grew after the recital.", "Good friends help your self-esteem.",
                  "Leo's self-esteem rose when he solved the puzzle.", "Practice built Maya's self-esteem."],
        right_use=RightUse(sentence="Helping others boosted Zoe's self-esteem."),
        wrong_use=WrongUse(sentence="The self-esteem of the bridge held the trucks.", why="It means respect, not strength."),
        word_parts="self- (your own) + esteem (respect) = respect for your own worth",
    )
    check = validate_card("self-esteem", "6-8", card)
    assert check.errors == []
    assert check.card.word_parts == card.word_parts


def test_l10_a_phrase_target_never_has_word_parts():
    card = make_card(
        pos="verb phrase", forms=["takes for granted", "took for granted"],
        senses=[Sense(pos="verb phrase", definition="to not notice how much something helps you",
                      example="Zoe takes her teacher's help for granted.")],
        examples=["Aisha took her morning walk for granted.", "Ben took sunny days for granted.",
                  "Do you take clean water for granted?", "Leo never takes his friends for granted."],
        right_use=RightUse(sentence="Kenji took his old bike for granted until it broke."),
        wrong_use=WrongUse(sentence="Maya took the bus for granted to school.", why="It means not valuing something."),
        word_parts="take (grab) + granted (given) = treat as given",
    )
    check = validate_card("take for granted", "6-8", card)
    assert check.errors == []
    assert check.card.word_parts == ""


def test_l10_skips_legacy_cards():
    card = legacy_card(word_parts="frux (Latin, fruit/value) = getting full value")
    assert validate_card(WORD, "6-8", card, legacy=True).card.word_parts == card.word_parts


def test_typographic_hyphens_are_normalized_in_card_text():
    examples = make_card().examples[:3] + ["Our fru\u00adgal club built a robot from spare\u2010parts."]
    card = make_card(kid_def="A frugal person has self\u2011control with money.", examples=examples,
                     synonyms=["thrifty", "penny\u2011wise"])
    check = validate_card(WORD, "6-8", card)
    assert check.errors == []  # the soft hyphen no longer splits "frugal", so the fourth example counts
    assert check.card.kid_def == "A frugal person has self-control with money."
    assert check.card.examples[3] == "Our frugal club built a robot from spare-parts."
    assert check.card.synonyms == ["thrifty", "penny-wise"]


def test_typographic_hyphens_are_left_alone_in_legacy_cards():
    card = legacy_card(kid_def="careful with money and self\u2011control")
    assert validate_card(WORD, "6-8", card, legacy=True).card.kid_def == "careful with money and self\u2011control"

# =================================================================================================
# Questions (Q1–Q8)
# =================================================================================================

PARTS = "frux (Latin, fruit or value) + -al (like) = getting full value from things"
EXPLAIN = "Frugal people spend money carefully and avoid waste."


def rq(qtype: str, prompt: str, choices: list[str] | None = None, answer_index: int = 0,
       accepted: list[str] | None = None, explanation: str = EXPLAIN) -> RawQuestion:
    return RawQuestion(type=qtype, prompt=prompt, choices=choices if choices is not None else [],
                       answer_index=answer_index, accepted_answers=accepted if accepted is not None else [],
                       explanation=explanation)


GOOD = {
    "meaning": rq("meaning", 'What does "frugal" mean?',
                  ["careful not to waste money", "very angry about losing", "fast at running races", "happy to share secrets"], 0),
    "pick_word": rq("pick_word", "Which word means careful not to waste money or things?",
                    ["furious", "frugal", "fragile", "famous"], 1),
    "fill_blank": rq("fill_blank", "Nina was ___ and saved half of her birthday money.",
                     ["generous", "noisy", "frugal", "sleepy"], 2),
    "usage": rq("usage", 'Which sentence uses "frugal" correctly?',
                ["The frugal puppy barked at the mail carrier.", "My frugal homework was due on Monday.",
                 "The frugal sun melted the snowman.", "The frugal shopper compared prices before buying cereal."], 3),
    "scenario": rq("scenario", "Which kid is being frugal?",
                   ["Ava packs lunch from home to save money for a trip.", "Ben buys three snacks he will not eat.",
                    "Cara leaves her jacket at the park.", "Dev sings loudly in the hallway."], 0),
    "synonym": rq("synonym", 'Which word is closest in meaning to "frugal"?', ["lazy", "thrifty", "brave", "curious"], 1),
    "antonym": rq("antonym", 'Which word means the opposite of "frugal"?', ["quiet", "early", "wasteful", "careful"], 2),
    "spell_it": rq("spell_it", "Mom stays ___ by using coupons at the store. (means: careful with money)",
                   [], -1, ["frugal"]),
    "word_parts": rq("word_parts", 'In "frugal", the Latin root "frux" means...',
                     ["fear", "friend", "fast", "fruit or value"], 3),
}


def vq(questions, band: str = "6-8", card: LearnCard | None = None, **kw):
    card = card if card is not None else make_card(word_parts=PARTS)
    return validate_questions(WORD, band, card, list(questions), **kw)


def only(q: RawQuestion, **kw) -> str | None:
    """Validate one question; return its drop reason or None if kept."""
    kept, drops = vq([q], **kw)
    assert len(kept) + len(drops) == 1
    return drops[0].reason if drops else None


def variant(qtype: str, **changes) -> RawQuestion:
    return GOOD[qtype].model_copy(update=changes)


def test_good_batch_is_all_kept_in_order():
    kept, drops = vq(GOOD.values())
    assert drops == []
    assert [q.type for q in kept] == list(GOOD)
    assert all(isinstance(q, RawQuestion) for q in kept)


def test_original_order_kept_and_drop_indices_reported():
    batch = [
        GOOD["meaning"],
        variant("synonym", choices=["lazy", "thrifty", "brave"]),
        GOOD["spell_it"],
        variant("pick_word", answer_index=0),
        GOOD["usage"],
    ]
    kept, drops = vq(batch)
    assert [q.type for q in kept] == ["meaning", "spell_it", "usage"]
    assert [d.index for d in drops] == [1, 3]
    assert isinstance(drops[0], Drop)
    assert drops[0].reason.startswith("Q1")
    assert drops[1].reason.startswith("Q4")


# --- Q1 ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "q",
    [
        variant("meaning", choices=["careful not to waste money", "very angry", "fast"]),
        variant("synonym", choices=["thrifty", "Thrifty ", "brave", "curious"]),
        variant("synonym", choices=["thrifty", "", "brave", "curious"]),
        variant("meaning", answer_index=-1),
        variant("meaning", accepted_answers=["careful not to waste money"]),
        variant("spell_it", choices=["frugal", "thrifty", "noisy", "lazy"]),
        variant("spell_it", answer_index=0),
        variant("meaning", prompt="   "),
    ],
)
def test_q1_shape_rules(q):
    assert only(q).startswith("Q1")


# --- Q2 ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "q",
    [
        variant("fill_blank", prompt="Nina was frugal and saved half of her birthday money."),
        variant("fill_blank", prompt="Nina was ___ and saved ___ of her birthday money."),
        variant("fill_blank", prompt="Frugal Nina was ___ with her birthday money."),
        variant("spell_it", prompt="Mom stays ___ by using coupons at the store."),
        variant("spell_it", prompt="Mom stays ___ (means: careful with money) by using coupons."),
        variant("spell_it", prompt="Mom stays ___ by using coupons. (means: frugal and careful)"),
        variant("spell_it", prompt="Mom stays ___ by using coupons. (means: )"),
    ],
)
def test_q2_blank_and_cue_rules(q):
    assert only(q).startswith("Q2")


def test_q2_long_underscore_run_counts_as_one_blank_and_is_normalized():
    kept, drops = vq([variant("fill_blank", prompt="Nina was _____ and saved half of her birthday money.")])
    assert drops == []
    assert kept[0].prompt == "Nina was ___ and saved half of her birthday money."


def test_q2_cue_may_end_with_period():
    assert only(variant("spell_it", prompt="Mom stays ___ by using coupons. (Means: careful with money).")) is None


# --- Q3 ----------------------------------------------------------------------------------------------

def test_q3_spell_it_needs_accepted_answers_that_match_the_word():
    assert only(variant("spell_it", accepted_answers=[])).startswith("Q3")
    assert only(variant("spell_it", accepted_answers=["thrifty"])).startswith("Q3")
    assert only(variant("spell_it", accepted_answers=["frugal", "cheap"])).startswith("Q3")


def test_q3_accepted_answers_are_normalized_and_forms_allowed():
    kept, _ = vq([variant("spell_it", accepted_answers=[" Frugal ", "frugal"])])
    assert kept[0].accepted_answers == ["frugal"]
    adverb = rq("spell_it", "Grandpa shops ___ at the market. (means: in a way that saves money)", [], -1, ["frugally"])
    assert only(adverb) is None


# --- Q4 ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "q",
    [
        variant("pick_word", answer_index=0),
        variant("fill_blank", choices=["generous", "noisy", "thrifty", "sleepy"]),
        variant("fill_blank", choices=["frugal", "frugally", "noisy", "sleepy"], answer_index=0),
        variant("meaning", choices=["careful with money", "Careful with money!", "very loud", "quick to anger"]),
        variant("usage", choices=["The puppy barked at the mail carrier.", "My frugal homework was due on Monday.",
                                  "The frugal sun melted the snowman.", "The frugal shopper compared prices first."]),
        variant("synonym", choices=["lazy", "careful", "brave", "curious"]),
        variant("antonym", choices=["quiet", "early", "generous", "careful"]),
    ],
)
def test_q4_type_specific_keys(q):
    assert only(q).startswith("Q4")


def test_q4_synonym_match_ignores_case_and_forms_count_for_fill_blank():
    assert only(variant("synonym", choices=["lazy", "Thrifty", "brave", "curious"])) is None
    adverb = rq("fill_blank", "Leo spends his allowance ___ at the fair.", ["frugally", "angrily", "loudly", "slowly"], 0)
    assert only(adverb) is None


def test_q4_word_parts_needs_card_word_parts():
    assert only(GOOD["word_parts"], card=make_card(word_parts="")).startswith("Q4")


# --- Q5 ----------------------------------------------------------------------------------------------

def test_q5_blank_filled_with_word_matches_example():
    q = variant("fill_blank", prompt="His ___ habits helped him buy a new bike.")
    assert only(q).startswith("Q5")


def test_q5_blank_filled_with_a_form_matches_example():
    q = rq("fill_blank", "Grandpa cooks ___ and never wastes leftovers.", ["frugally", "angrily", "loudly", "slowly"], 0)
    assert only(q).startswith("Q5")


def test_q5_spell_it_cue_is_ignored_when_comparing():
    q = variant("spell_it", prompt="Mia is ___, so she saves her allowance. (means: careful with money)")
    assert only(q).startswith("Q5")


def test_q5_sentence_choice_matching_learn_sentence_ignores_case_and_punctuation():
    q = variant("usage", choices=["The frugal puppy barked at the mail carrier.", "My frugal homework was due on Monday.",
                                  "The frugal sun melted the snowman.", "the frugal team reused old jerseys this season"])
    assert only(q).startswith("Q5")


def test_q5_right_and_wrong_use_count_as_learn_sentences():
    q = variant("fill_blank", prompt="Being ___, Sam fixed his old skateboard instead of buying one.")
    assert only(q).startswith("Q5")


def test_q5_legacy_is_exempt():
    q = variant("fill_blank", prompt="His ___ habits helped him buy a new bike.")
    assert only(q, legacy=True) is None


# --- Q6 ----------------------------------------------------------------------------------------------

def blank_sentence(n: int) -> str:
    """An n-word fill_blank prompt."""
    return " ".join(["Nina", "was", "___", "and"] + ["saved"] * (n - 5) + ["money."])


@pytest.mark.parametrize("band, ok_words", [("3-5", 20), ("6-8", 25), ("9-12", 33)])
def test_q6_prompt_length_per_band(band, ok_words):
    assert only(variant("fill_blank", prompt=blank_sentence(ok_words)), band=band) is None
    reason = only(variant("fill_blank", prompt=blank_sentence(ok_words + 1)), band=band)
    assert reason == f"Q6: prompt has a {ok_words + 1}-word sentence (max {ok_words})"


def test_q6_choice_length():
    long_choice = " ".join(["The", "frugal"] + ["shopper"] * 23 + ["smiled."])  # 26 words
    q = variant("usage", choices=GOOD["usage"].choices[:3] + [long_choice])
    assert only(q) == "Q6: choice 4 has a 26-word sentence (max 25)"


def test_q6_explanation_length():
    assert only(variant("meaning", explanation="e" * 160)) is None
    assert only(variant("meaning", explanation="e" * 161)) == "Q6: explanation is 161 characters (max 160)"


# --- Q7 ----------------------------------------------------------------------------------------------

def test_q7_blocklisted_term_in_choice_or_explanation():
    assert only(variant("synonym", choices=["lazy", "thrifty", "zorkle", "curious"])) == "Q7: blocked term 'zorkle'"
    assert only(variant("meaning", explanation="Two zorkles would never be frugal.")) == "Q7: blocked term 'zorkle'"


# --- Q8 ----------------------------------------------------------------------------------------------

def test_q8_duplicate_of_existing_prompt_after_normalizing():
    reason = only(GOOD["meaning"], existing_prompts=["what does FRUGAL mean"])
    assert reason.startswith("Q8")
    assert only(GOOD["meaning"], existing_prompts=["What does frugal mean in this sentence?"]) is None


def test_q8_duplicate_within_batch_keeps_first():
    second = variant("meaning", choices=["careful with money", "very loud", "quick to anger", "full of energy"])
    kept, drops = vq([GOOD["meaning"], second])
    assert kept == [GOOD["meaning"]]
    assert [(d.index, d.reason[:2]) for d in drops] == [(1, "Q8")]


def test_q8_applies_to_legacy_too():
    assert only(GOOD["meaning"], existing_prompts=['What does "frugal" mean?'], legacy=True).startswith("Q8")


def test_q8_ignores_the_letter_hint_on_both_sides():
    old_unhinted = GOOD["spell_it"].prompt  # stored before the app added hints
    assert only(GOOD["spell_it"], existing_prompts=[old_unhinted]).startswith("Q8")
    hinted = 'Mom stays ___ by using coupons at the store. (means: careful with money; starts with "f")'
    assert only(GOOD["spell_it"], existing_prompts=[hinted]).startswith("Q8")
    other_cue = "Mom stays ___ by using coupons at the store. (means: spends little)"
    assert only(GOOD["spell_it"], existing_prompts=[other_cue]) is None


# --- spell_it first-letter hint --------------------------------------------------------------------

def kept_prompt(q: RawQuestion, word: str = WORD, **kw) -> str:
    kept, drops = validate_questions(word, "6-8", make_card(word_parts=PARTS), [q], **kw)
    assert drops == [], drops
    return kept[0].prompt


def test_hint_adds_the_first_letter_to_the_cue():
    prompt = kept_prompt(GOOD["spell_it"])
    assert prompt == 'Mom stays ___ by using coupons at the store. (means: careful with money; starts with "f")'
    assert validate._CUE_AT_END.search(prompt)


def test_hint_uses_the_accepted_form_and_counts_the_words_of_a_phrase():
    adverb = rq("spell_it", "Grandpa shops ___ at the market. (means: in a way that saves money)", [], -1, ["Frugally"])
    assert kept_prompt(adverb).endswith('(means: in a way that saves money; starts with "f")')
    phrase = rq("spell_it", "We ate rice ___ pasta at the picnic. (means: as a swap for)", [], -1, ["in lieu of"])
    prompt = kept_prompt(phrase, word="in lieu of")
    assert prompt == 'We ate rice ___ pasta at the picnic. (means: as a swap for; starts with "i", 3 words)'
    assert validate._CUE_AT_END.search(prompt)


@pytest.mark.parametrize(
    "cue",
    [
        '(means: careful with money; starts with "f")',
        "(means: careful with money; starts with F)",
        "(means: careful with money, starts with the letter f).",
        '(Means: careful with money; starts with "g", 2 words)',
        "(means: careful with money.)",
        "(means: careful with money .)",
        "(means: careful with money; it starts with F)",
        "(means: careful with money, the word starts with f)",
        "(means: careful with money; begins with f)",
        "(means: careful with money; first letter: f)",
        "(means: careful with money. First letter is F.)",
    ],
)
def test_hint_replaces_a_hint_the_model_wrote(cue):
    prompt = kept_prompt(variant("spell_it", prompt=f"Mom stays ___ by using coupons at the store. {cue}"))
    assert prompt == 'Mom stays ___ by using coupons at the store. (means: careful with money; starts with "f")'


def test_hint_never_rescues_a_cue_without_a_meaning():
    reason = only(variant("spell_it", prompt="Mom stays ___ by using coupons. (means: ; starts with f)"))
    assert reason is not None and reason.startswith("Q2")


def test_hint_words_do_not_count_as_the_word():
    q = rq("spell_it", "The race will ___ when the whistle blows. (means: begin)", [], -1, ["start"])
    assert kept_prompt(q, word="start").endswith('(means: begin; starts with "s")')


def test_hint_leaves_legacy_questions_unchanged():
    kept, drops = vq([GOOD["spell_it"]], legacy=True)
    assert drops == [] and kept[0].prompt == GOOD["spell_it"].prompt


@pytest.mark.parametrize("ending", ["(eases the trouble)", "(eases the trouble).", "( eases the trouble )"])
def test_bare_cue_gets_the_means_label_and_hint(ending):
    q = rq("spell_it", f"Maya wanted to ___ the noise before bedtime. {ending}", [], -1, ["mitigate"])
    assert kept_prompt(q, word="mitigate") == (
        'Maya wanted to ___ the noise before bedtime. (means: eases the trouble; starts with "m")'
    )


def test_bare_cue_repair_needs_a_trailing_parenthetical_without_a_blank():
    assert only(variant("spell_it", prompt="Mom stays ___ by using coupons at the store.")).startswith("Q2")
    reason = only(variant("spell_it", prompt="Mom stays careful at the store (always ___)."))
    assert reason is not None and reason.startswith("Q2")


def test_bare_cue_is_not_repaired_for_legacy():
    reason = only(variant("spell_it", prompt="Mom stays ___ by using coupons. (careful with money)"), legacy=True)
    assert reason is not None and reason.startswith("Q2")


PHRASAL = ["take for granted", "look up to", "run out of", "give up on"]


def test_q2_separable_phrase_in_the_blank_passes():
    q = rq("fill_blank", "Many people ___ clean water until a pipe breaks.", PHRASAL, 0)
    assert reason_for("take for granted", q) is None


def test_q2_split_phrase_outside_the_blank_is_still_caught():
    q = rq("fill_blank", "Leo takes his bike for granted, and Mia will ___ her skates.", PHRASAL, 0)
    assert reason_for("take for granted", q) == "Q2: prompt contains the word outside the blank"


# --- Q9 ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "explanation",
    [
        "Answer C is the one about saving money.",
        "(A) shows careful spending.",
        "A) shows careful spending.",
        "B is correct because Ava saves her money.",
        "C is right: Ava saves her money.",
        "D is the answer since Ava saves.",
        "Answer: B, because Ava saves her money.",
        "Answer:C fits the clue.",
        "(a) is right because Ava saves her money.",
        "The third one shows careful spending.",
        "The last one is about saving money.",
        "Sentence 1 is right and sentence 2 is not.",  # two positions: no safe repair
        "The first choice is wrong, so frugal must mean careful.",  # points at a wrong choice
    ],
)
def test_q9_explanation_must_not_name_a_choice_position(explanation):
    assert only(variant("meaning", explanation=explanation)) == "Q9: explanation refers to a choice position"


@pytest.mark.parametrize(
    "explanation",
    [
        "A kid who saves money is frugal.",
        "In a sentence, frugal describes careful spending.",
        "This sentence shows careful spending, so frugal fits.",
        "Ava was first to pick the cheaper option, which is frugal.",
        "The best answer is careful with money.",
        "I'm sure frugal fits, because Ava saves her money.",
        "Ava was the first one to pack lunch and save money.",
    ],
)
def test_q9_allows_ordinary_wording(explanation):
    assert only(variant("meaning", explanation=explanation)) is None


# --- Q10 ---------------------------------------------------------------------------------------------

MEANINGS = ["lasting a very short time", "very large and heavy", "full of bright colors", "loud and hard to ignore"]
EPHEMERAL_USES = [
    "The ephemeral rainbow faded in a minute.",
    "The ephemeral mountain stood for ages.",
    "Her ephemeral dog barked at the mail carrier.",
    "The ephemeral rock was too heavy to lift.",
]


def reason_for(word: str, q: RawQuestion, **kw) -> str | None:
    kept, drops = validate_questions(word, "9-12", make_card(), [q], **kw)
    return drops[0].reason if drops else None


@pytest.mark.parametrize(
    "word, q",
    [
        ("ephemeral", rq("meaning", 'What does "ephem-eral" mean?', MEANINGS, 0)),
        ("ephemeral", rq("meaning", 'What does "ephemerAl" mean?', MEANINGS, 0)),
        ("self-esteem", rq("meaning", 'What does "selfesteem" mean?', MEANINGS, 0)),
        ("in lieu of", rq("meaning", 'What does "inlieu of" mean?', MEANINGS, 0)),
        ("ephemeral", rq("usage", 'Which sentence uses "ephemeral" correctly?',
                         ["The ephemerAl rainbow faded in a minute."] + EPHEMERAL_USES[1:], 0)),
    ],
)
def test_q10_misspelled_word_is_rejected(word, q):
    assert reason_for(word, q) == "Q10: the word is misspelled"


@pytest.mark.parametrize(
    "word, prompt",
    [
        ("ephemeral", 'Ephemeral things fade fast. What does "ephemeral" mean?'),
        ("ephemeral", 'What does "EPHEMERAL" mean?'),
        ("ephemeral", 'What does "ephemerally" mean?'),
        ("self-esteem", 'What does "self-esteem" mean?'),
        ("self-esteem", "Self-esteem can be high or low. What does it mean?"),
        ("in lieu of", 'In lieu of what? What does "in lieu of" mean?'),
    ],
)
def test_q10_correct_spellings_pass(word, prompt):
    assert reason_for(word, rq("meaning", prompt, MEANINGS, 0)) is None


def test_q10_correctly_spelled_choices_pass():
    assert reason_for("ephemeral", rq("usage", 'Which sentence uses "ephemeral" correctly?', EPHEMERAL_USES, 0)) is None


@pytest.mark.parametrize(
    "explanation, repaired",
    [
        ("Only the first sentence shows the adjective meaning eager to learn.",
         "Only the correct sentence shows the adjective meaning eager to learn."),
        ('Sentence 1 uses "curious" to mean eager to learn.', 'The correct sentence uses "curious" to mean eager to learn.'),
        ("The first choice matches the definition of gigantic.", "The correct choice matches the definition of gigantic."),
        ("Only sentence 3 uses mitigate to mean lessen fatigue.",
         "Only the correct sentence uses mitigate to mean lessen fatigue."),
        ("the second option shows careful spending.", "The correct option shows careful spending."),
        ("It saves money, so option B fits.", "It saves money, so the correct option fits."),
        ("Saving is careful. Choice 2 shows it.", "Saving is careful. The correct choice shows it."),
        ("The last answer names careful spending.", "The correct answer names careful spending."),
    ],
)
def test_q9_positional_explanation_is_repaired(explanation, repaired):
    kept, drops = vq([variant("meaning", explanation=explanation)])
    assert drops == []
    assert kept[0].explanation == repaired and len(repaired) <= validate.EXPLANATION_MAX


def test_q9_repair_never_pushes_the_explanation_over_the_limit():
    explanation = "Sentence 1 shows saving. " + "Frugal people spend with care. " * 4 + "Truly."
    assert 150 < len(explanation) <= validate.EXPLANATION_MAX
    assert only(variant("meaning", explanation=explanation)) == "Q9: explanation refers to a choice position"


def test_q9_and_q10_skip_legacy_questions():
    assert only(variant("meaning", explanation="The first choice is right."), legacy=True) is None
    kept, _ = vq([variant("meaning", explanation="The first choice is right.")], legacy=True)
    assert kept[0].explanation == "The first choice is right."  # legacy is never repaired
    assert reason_for("ephemeral", rq("meaning", 'What does "ephem-eral" mean?', MEANINGS, 0), legacy=True) is None


# --- typographic hyphens in questions --------------------------------------------------------------

def test_typographic_hyphens_are_normalized_in_question_text():
    q = rq("spell_it", "Mom stays ___ with her pocket\u2011money. (means: careful with cash\u00ad)", [], -1,
           ["fru\u00adgal"], explanation="Saving pocket\u2010money is frugal.")
    kept, drops = vq([q, variant("synonym", choices=["lazy", "thrifty", "well\u2011known", "curious"])])
    assert drops == []
    assert kept[0].prompt == 'Mom stays ___ with her pocket-money. (means: careful with cash; starts with "f")'
    assert kept[0].accepted_answers == ["frugal"] and kept[0].explanation == "Saving pocket-money is frugal."
    assert kept[1].choices == ["lazy", "thrifty", "well-known", "curious"]


def test_typographic_hyphens_are_left_alone_in_legacy_questions():
    q = variant("synonym", choices=["lazy", "thrifty", "well\u2011known", "curious"])
    kept, _ = vq([q], legacy=True)
    assert kept[0].choices[2] == "well\u2011known"


# --- Q11 wrong choices that are the word or a card synonym ----------------------------------------

@pytest.mark.parametrize(
    "q, reason",
    [
        (variant("synonym", choices=["lazy", "thrifty", "frugally", "curious"]),
         "Q11: a wrong choice is the word or a form of it"),
        (variant("antonym", choices=["quiet", "Frugal", "wasteful", "careful"]),
         "Q11: a wrong choice is the word or a form of it"),
        (variant("synonym", choices=["lazy", "thrifty", " Economical ", "curious"]),  # a second right answer
         "Q11: a wrong choice is one of the card's synonyms"),
        (variant("antonym", choices=["quiet", "THRIFTY", "wasteful", "careful"]),
         "Q11: a wrong choice is one of the card's synonyms"),
        (variant("pick_word", choices=["economical", "frugal", "fragile", "famous"]),
         "Q11: a wrong choice is one of the card's synonyms"),
    ],
)
def test_q11_wrong_choice_is_the_word_or_a_card_synonym(q, reason):
    assert only(q) == reason


@pytest.mark.parametrize(
    "q",
    [
        variant("synonym", choices=["wasteful", "thrifty", "brave", "curious"]),  # an antonym is a fine distractor
        variant("synonym", choices=["thriftless", "thrifty", "brave", "curious"]),  # only whole synonyms count
        variant("fill_blank", choices=["thrifty", "noisy", "frugal", "sleepy"]),  # fill_blank is not covered
    ],
)
def test_q11_allows_other_distractors(q):
    assert only(q) is None


def test_q11_skips_legacy_questions():
    assert only(variant("antonym", choices=["quiet", "thrifty", "wasteful", "careful"]), legacy=True) is None


# --- Q12 giveaway: a choice contains the word -----------------------------------------------------

@pytest.mark.parametrize(
    "q",
    [
        variant("meaning", choices=["careful not to waste money", "very angry about losing", "fast at running races",
                                    "being frugal with time"]),
        variant("scenario", choices=["Ava packs lunch from home to save money for a trip.",
                                     "Ben is frugally buying three snacks.", "Cara leaves her jacket at the park.",
                                     "Dev sings loudly in the hallway."]),
        variant("synonym", choices=["lazy", "thrifty", "not frugal", "curious"]),
        variant("antonym", choices=["quiet", "early", "wasteful", "never frugal"]),
        variant("word_parts", choices=["fear", "a frugal friend", "fast", "fruit or value"]),
    ],
)
def test_q12_a_choice_that_contains_the_word_gives_it_away(q):
    assert only(q) == "Q12: a choice contains the word"


def test_q12_does_not_apply_to_usage_pick_word_fill_blank_or_legacy():
    for qtype in ("usage", "pick_word", "fill_blank"):
        assert only(GOOD[qtype]) is None, qtype
    giveaway = variant("synonym", choices=["lazy", "thrifty", "not frugal", "curious"])
    assert only(giveaway, legacy=True) is None


# --- Q13 spell_it cue word that starts with the answer's letter -----------------------------------

@pytest.mark.parametrize(
    "word, prompt, accepted",
    [
        (WORD, "Mom stays ___ by using coupons at the store. (means: fine with less)", ["frugal"]),
        (WORD, "Grandpa shops ___ at the market. (means: Fine with spending less)", ["frugally"]),
        ("gigantic", "The ___ whale swam past our boat. (means: great size)", ["gigantic"]),
        ("in lieu of", "We ate rice ___ pasta at the picnic. (means: instead of)", ["in lieu of"]),
    ],
)
def test_q13_cue_word_with_the_answers_first_letter_is_rejected(word, prompt, accepted):
    q = rq("spell_it", prompt, [], -1, accepted)
    kept, drops = validate_questions(word, "6-8", make_card(word_parts=PARTS), [q])
    assert [d.reason[:4] for d in drops] == ["Q13:"]


@pytest.mark.parametrize(
    "cue",
    [
        "(means: careful with money)",
        "(means: from a small budget)",  # "from" is a stopword
        "(means: few buys, no waste)",  # "few" is shorter than 4 letters
        "(means: careful with money; first letter: f)",  # a hint the model wrote is not part of the meaning
        '(means: careful with money; starts with "f")',
    ],
)
def test_q13_stopwords_short_words_and_the_hint_do_not_count(cue):
    assert only(variant("spell_it", prompt=f"Mom stays ___ by using coupons at the store. {cue}")) is None


def test_q13_skips_legacy_questions():
    assert only(variant("spell_it", prompt="Mom stays ___ by using coupons. (means: fine with less)"),
                legacy=True) is None


# --- Q5 near-copies of Learn-card sentences ---------------------------------------------------------

@pytest.mark.parametrize(
    "q",
    [
        # the same first name as a card sentence plus 3+ other shared content words (here 45% shared)
        variant("fill_blank", prompt="Leo was ___ with paint, so the whole art club poster got finished "
                                     "before lunch at school today."),
        # a different name, but 60% or more of the content words are shared
        variant("fill_blank", prompt="Maya was ___ with paint and finished the whole poster."),
        variant("scenario", choices=["Sam fixed his old skateboard instead of buying a new one.",
                                     "Ben buys three snacks he will not eat.", "Cara leaves her jacket at the park.",
                                     "Dev sings loudly in the hallway."]),
        variant("spell_it", prompt="Our science club built a ___ robot from spare parts. (means: careful with money)"),
    ],
)
def test_q5_near_copy_of_a_learn_card_sentence_is_rejected(q):
    assert only(q) == "Q5: near-copy of a Learn-card sentence"


@pytest.mark.parametrize(
    "q",
    [
        # a different first name and 4 shared words, but only 36% of the content words
        variant("fill_blank", prompt="Maya was ___ with paint, so the whole art club poster got finished "
                                     "before lunch at school today."),
        # the same name with only one other shared content word
        variant("fill_blank", prompt="Leo was ___ with paint at the bake sale on Friday morning."),
        # 2 of 4 content words shared; the word itself is not counted (it would make 3 of 5 = 60%)
        variant("usage", choices=GOOD["usage"].choices[:3] + ["The frugal team reused paper cups."]),
        # shared stopwords and short words do not count
        variant("fill_blank", prompt="She was so ___ with all of her things, and he is too."),
        # definitions are not compared with the card (a correct definition overlaps the kid_def by design)
        variant("meaning", choices=["spends money carefully and does not waste food or cash", "very angry about losing",
                                    "fast at running races", "happy to share secrets"]),
        variant("pick_word", prompt="Which word means spends money carefully and does not waste food or cash?"),
        # round 4: the kid_def is compared by the shared-name rule only, never the 60% rule (a fill_blank clue states
        # the meaning, so overlap with the kid_def is expected); these are the reviewer's probes
        variant("fill_blank", prompt="The ___ club spends money carefully and wastes nothing."),
        variant("fill_blank", prompt="Ben is ___ with money and does not waste things he can reuse."),
        variant("usage", choices=GOOD["usage"].choices[:3]
                + ["A frugal person spends money carefully and never wastes food or cash."]),
    ],
)
def test_q5_allows_questions_that_only_share_a_few_words(q):
    assert only(q) is None


def test_q5_kid_def_still_counts_for_the_shared_name_rule():
    card = make_card(word_parts=PARTS, kid_def="Like Priya, a frugal person spends money carefully and saves cash.")
    # the same name plus spends, money, carefully (4 of 8 content words, under 60%): still a near-copy
    q = variant("fill_blank", prompt="Priya was ___ and spends money carefully at the busy summer fair today.")
    assert only(q, card=card) == "Q5: near-copy of a Learn-card sentence"
    # no shared name: 3 of 5 content words (60%) shared with the kid_def is fine
    assert only(variant("fill_blank", prompt="Omar was ___ and spends money carefully at the fair."), card=card) is None


def test_q5_near_copy_skips_legacy_questions():
    q = variant("fill_blank", prompt="Maya was ___ with paint and finished the whole poster.")
    assert only(q, legacy=True) is None


# --- Q9 more positional shapes and negated references ----------------------------------------------

@pytest.mark.parametrize(
    "explanation",
    [
        "The first one is right because Ava saves her money.",
        "Definition 1 is wrong and definition 3 is right.",  # two references: no safe repair
        "Sentence 0 is right and sentence 2 is not.",
        # a negated reference is never repaired into a false statement
        "The first sentence does not use frugal correctly.",
        "Sentence 2 isn't about saving, so it is wrong.",
        "Option B never shows careful spending.",
        "Choice 3 cannot be right; frugal is about money.",
        "Not the first choice: frugal means careful with money.",
        # round 4: the scan now stops at ",", so a second, bare ordinal ("the second") blocks the repair instead
        "The first choice fits, but the second does not.",
        "Sentence 1 fits, but not the last.",
    ],
)
def test_q9_more_positional_shapes_and_negated_references_are_rejected(explanation):
    assert only(variant("meaning", explanation=explanation)) == "Q9: explanation refers to a choice position"


@pytest.mark.parametrize(
    "explanation, repaired",
    [
        ("Sentence 0 uses frugal to mean careful with money.", "The correct sentence uses frugal to mean careful with money."),
        ("The first definition matches careful spending.", "The correct definition matches careful spending."),
        ("Only definition 2 is about money.", "Only the correct definition is about money."),
        ("The first sentence shows saving. The others do not.", "The correct sentence shows saving. The others do not."),
        # round 4: a negation after "because", ":" or "," is about the scene, not the reference
        ("The first sentence is right because Ava does not waste food.",
         "The correct sentence is right because Ava does not waste food."),
        ("Sentence 2 shows frugal: Ben doesn't buy what he won't use.",
         "The correct sentence shows frugal: Ben doesn't buy what he won't use."),
        ("The first choice fits, since Ben never wastes money.", "The correct choice fits, since Ben never wastes money."),
    ],
)
def test_q9_more_positional_shapes_are_repaired(explanation, repaired):
    kept, drops = vq([variant("meaning", explanation=explanation)])
    assert drops == []
    assert kept[0].explanation == repaired

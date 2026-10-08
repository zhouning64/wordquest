from __future__ import annotations

import json

import pytest

from app.ai.prompts import BAND_GUIDE, check_prompt, learn_card_prompt, question_batch_prompt
from app.models import BANDS, LearnCard, RightUse, Sense, WrongUse

CARD = LearnCard(
    pos="adjective",
    forms=["frugally", "frugality"],
    short_def="careful with money; not wasteful",
    kid_def="A frugal person spends money carefully and does not waste food, things, or cash.",
    senses=[
        Sense(pos="adjective", definition="careful not to waste money or things", example="Mia is frugal, so she saves her allowance."),
        Sense(pos="adjective", definition="simple and plain, costing little", example="We ate a frugal lunch of rice and beans."),
    ],
    examples=[
        "His frugal habits helped him buy a new bike.",
        "The frugal team reused old jerseys this season.",
        "Being frugal with paint, Leo finished the whole poster.",
        "Grandpa cooks frugally and never wastes leftovers.",
    ],
    word_parts="frux (fruit, value) + -al = getting full value",
    memory_hook="Frugal sounds like free-gull: a seagull that never pays for snacks.",
    synonyms=["thrifty", "economical"],
    antonyms=["wasteful", "extravagant"],
    right_use=RightUse(sentence="Being frugal, Sam fixed his old skateboard instead of buying one."),
    wrong_use=WrongUse(sentence="The frugal storm knocked down three fences.", why="Frugal describes careful spending, not strength."),
    image_scene="A kid dropping coins into a piggy bank next to a shelf of untouched toys.",
    emoji_scene="🐷🪙💰✅",
)

MIX = {"meaning": 1, "pick_word": 1, "fill_blank": 2, "usage": 1, "scenario": 2, "synonym": 1, "antonym": 1, "spell_it": 2, "word_parts": 1}


def test_band_guide_covers_every_band_with_limits_and_settings():
    assert set(BAND_GUIDE) == set(BANDS)
    assert "15 words" in BAND_GUIDE["3-5"] and "pets" in BAND_GUIDE["3-5"] and "playground" in BAND_GUIDE["3-5"]
    assert "20 words" in BAND_GUIDE["6-8"] and "science class" in BAND_GUIDE["6-8"]
    assert "video games" in BAND_GUIDE["6-8"]
    assert "28 words" in BAND_GUIDE["9-12"] and "literature" in BAND_GUIDE["9-12"] and "SAT" in BAND_GUIDE["9-12"]
    assert "harder than the target word" in BAND_GUIDE["3-5"]
    assert "synonyms, antonyms, choices and explanations" in BAND_GUIDE["3-5"]
    assert "technical term" in BAND_GUIDE["6-8"]
    assert "no battles, monsters or weapons" in BAND_GUIDE["6-8"] and "safe lab habits only" in BAND_GUIDE["6-8"]
    assert 'never "kid"' in BAND_GUIDE["9-12"] and "invented person" in BAND_GUIDE["9-12"]
    assert 'never "kid"' not in BAND_GUIDE["3-5"]
    for phrase in ("history, literature, science, jobs, school debate club",
                   "no elections, voting, protests or political causes",
                   "Never open a sentence with a time or place phrase", "Every factual claim must be true",
                   "no death tolls or disasters"):
        assert phrase in BAND_GUIDE["9-12"], phrase
    assert "current events" not in BAND_GUIDE["9-12"]
    # round 4: "starts with a person or thing" made every sentence open the same way
    assert "person or thing" not in BAND_GUIDE["9-12"] and "setting phrase" not in BAND_GUIDE["9-12"]
    assert "time or place phrase" not in BAND_GUIDE["3-5"] + BAND_GUIDE["6-8"]


# Concrete content the model copied from earlier prompts (scenes, sample words, sample sentences).
REMOVED = (
    "brave cake", "brave outfit", '"pride"', '"instead"', '"practical"', "In the novel,", "During the Renaissance,",
    "Priya covering", "flood", "curious dog", "self-esteem", "curious = strange", "candid photo", "candied",
    "militate", "tenacious", "ephemeral", "steady", "frugal", "Maya was ___", "benevolent", "giant + -ic",
    "massive / tiny", "braved", "careful with money", "candidly",
    "chemical spill", "spill", "flames",  # fix round 1: the model copied "Priya ___ the chemical spill"
    "advocated", "kitchen", "come to terms",  # round 4: the review's examples stay out of the prompts
)


@pytest.mark.parametrize("band", ["3-5", "6-8", "9-12"])
def test_prompts_no_longer_carry_copyable_content_examples(band):
    card = CARD.model_copy(update={"examples": ["zest one", "zest two", "zest three", "zest four"],
                                   "senses": [], "synonyms": ["zeal"], "antonyms": [], "word_parts": "",
                                   "forms": [], "right_use": RightUse(), "wrong_use": WrongUse()})
    texts = [*learn_card_prompt("zest", band), *question_batch_prompt("zest", band, card, MIX, [])]
    texts += check_prompt([{"qid": "q1", "type": "meaning", "prompt": "What does zest mean?", "choices": []}], band)
    for text in texts:
        for phrase in REMOVED:
            assert phrase not in text, phrase


@pytest.mark.parametrize("band", ["3-5", "6-8", "9-12"])
def test_learn_card_prompt_contents(band):
    system, user = learn_card_prompt("frugal", band)
    assert system and user
    assert '"frugal"' in user
    assert BAND_GUIDE[band] in user
    for phrase in ("irregular", "strove", "1 to 3", "4 to 6", "memory_hook", "word_parts",
                   "right_use", "wrong_use", "3 to 6 emoji", "image_scene", "any text, letters, numbers, signs"):
        assert phrase.lower() in user.lower(), phrase
    assert 'stressed on "MAT"' in user and "prag-MAT-ic" not in user and "jy-GAN-tik" not in user
    for phrase in ("second meaning", "not a label", "real world", "sounds like itself",
                   "hidden inside", "every dictionary meaning", "single picture", "whole range", "same part of speech"):
        assert phrase.lower() in user.lower(), phrase
    # senses and forms (round 3): derived words never get a sense of their own; forms follow the senses
    for phrase in ("meanings of \"frugal\" itself", "(-ly, -ness, -ity, -ion, -ment, -ance, -ence, -ery)",
                   "never get a sense or part of speech of their own", "simple inflection of it (-s, -ed, -ing)",
                   "rare or technical sense", "the main sense without its key idea",
                   "the inflections and derived words of the senses you give"):
        assert phrase in user, phrase
    assert "needs its own sense" not in user
    # round 4 (Qwen): dictionary senses and real forms only, no word parts for phrases or invisible parts,
    # and a phrase's wrong_use is wrong under every meaning of the whole phrase
    for phrase in ("only senses found in a standard learner's dictionary", "never an invented noun use of an adjective",
                   "Every form is a real, correctly spelled dictionary word",
                   'Use "" for a phrase', 'every part you name must be visible in the spelling of "frugal"',
                   "for a phrase, every meaning of the whole phrase"):
        assert phrase in user, phrase
    # wrong_use, word_parts, memory_hook, comparatives (round 3)
    for phrase in ("a look-alike or a near-meaning word",
                   "the subject is an object, animal or weather", "the action fails", 'this sentence needs "<other word>"',
                   '"<part> (<meaning>) + <part> (<meaning>) = <combined meaning>"', "Name the real source word",
                   "never guess a root's meaning from a modern English word", "with certainty, use \"\"",
                   "use a root only if it is the one in word_parts", 'Use "more/most frugal" only when two or more things'):
        assert phrase in user, phrase
    assert "Never reuse a sentence" in user
    assert "school-appropriate" in system
    assert "goggles" in system and "bouncing back from mistakes" in system
    assert "no unsafe handling of materials or heat" in system
    assert "Never copy the examples in these instructions" in system
    assert "JSON" in system


@pytest.mark.parametrize("band", ["3-5", "6-8", "9-12"])
def test_learn_card_wrong_use_misuses_the_word_itself(band):
    # Fix round 1: "write a sentence where that other word is right" made the model write a correct sentence
    # with the OTHER word, so 11 of 17 live cards failed L4. The sentence must contain the word itself.
    _, user = learn_card_prompt("frugal", band)
    line = next(l for l in user.splitlines() if l.startswith("- wrong_use.sentence:"))
    for phrase in ('a sentence that contains "frugal" itself (or one of its forms)',
                   'put "frugal" in the spot where that other word belongs',
                   'the sentence would be correct if "frugal" were replaced by the other word',
                   "The other word does not appear in the sentence",
                   "X used where Y belongs", "names the other word", 'this sentence needs "<other word>"',
                   "every dictionary meaning", "the subject is an object, animal or weather", "the action fails"):
        assert phrase in line, phrase
    assert "where that other word is right" not in user
    assert "first pick one real word" not in user


def test_learn_card_prompt_word_parts_roots_depend_on_band():
    _, young = learn_card_prompt("gigantic", "3-5")
    _, older = learn_card_prompt("gigantic", "9-12")
    form = '"<part> (<meaning>) + <part> (<meaning>) = <combined meaning>"'
    assert form in young and form in older
    assert "Latin, Greek or French" not in young
    assert "Latin, Greek or French" in older


def test_learn_card_prompt_mentions_band_sentence_limit():
    _, user = learn_card_prompt("frugal", "9-12")
    assert "at most 28 words" in user


def test_learn_card_prompt_unknown_band_raises():
    with pytest.raises(KeyError):
        learn_card_prompt("frugal", "k-2")


def test_question_batch_prompt_contents():
    existing = ["What does \"frugal\" mean?", "Which kid is being frugal?"]
    system, user = question_batch_prompt("frugal", "6-8", CARD, MIX, existing)
    assert '"frugal"' in user
    assert BAND_GUIDE["6-8"] in user
    assert "Write exactly 12 questions" in user
    for line in ("- meaning: 1", "- pick_word: 1", "- fill_blank: 2", "- usage: 1", "- scenario: 2",
                 "- synonym: 1", "- antonym: 1", "- spell_it: 2", "- word_parts: 1"):
        assert line in user, line
    for prompt in existing:
        assert f"- {prompt}" in user
    # card material the writer needs
    assert "careful not to waste money or things" in user
    assert "simple and plain, costing little" in user
    assert "thrifty, economical" in user
    assert "wasteful, extravagant" in user
    assert CARD.word_parts in user
    assert "frugally, frugality" in user
    # every Learn sentence is listed as "do not reuse"
    for sentence in CARD.examples + [s.example for s in CARD.senses] + [CARD.right_use.sentence, CARD.wrong_use.sentence]:
        assert f"- {sentence}" in user
    # the rules
    for rule in ("exactly one defensible answer", "Ask only about the card's senses", "(means: <short meaning>)",
                 "copied exactly from the card's synonyms", "copied exactly from the card's antonyms",
                 "every choice contains the word", "exactly one ___", "160 characters", "plausible",
                 "never write that hint yourself", "fit this word and no other",
                 "synonym with the same first letter would also fit", "at most 6 plain words",
                 "never refer to a letter or position",
                 'Say "kid" only for band 3-5', "never reuse a Learn-card situation", "no two questions",
                 "Never copy the examples in these instructions"):
        assert rule in system, rule
    # round 3: fill_blank, scenario stems, comparatives
    for rule in ("Choose the three wrong choices first", "states the meaning in other plain words",
                 "makes the sentence false or silly", "Naming an activity is not a clue",
                 "a feeling, manner or action that could also describe the person or scene",
                 "a noun goes in a noun slot", "a verb keeps its preposition",
                 '"Which of these would you call <word>?" only for a noun or adjective',
                 "ask which sentence or situation shows it", 'Use "more/most <word>" only when two or more things'):
        assert rule in system, rule
    # round 4 (Qwen): wrong choices must tempt a student who half-knows the word; spell_it states the Q13 rule
    for rule in ("tempt a student who half-knows the word", "same part of speech, length and style",
                 "same topic or situation as the correct choice", "words this band knows",
                 "never nonsense, joke or obviously unrelated choices",
                 "realistic misuse (the word put where a look-alike or near-meaning word belongs, never nonsense)",
                 "without using the definition's words or a synonym", "the wrong choices are situations in the same setting",
                 "real words from the same topic", "the correct choice is never the odd one out",
                 "No word in the cue that has 4 or more letters may start with the answer's first letter"):
        assert rule in system, rule
    for qtype in MIX:
        assert f"- {qtype}:" in system
    assert 'spelled correctly every time, with no added hyphens, spaces or capital letters' in user


def test_question_batch_prompt_omits_zero_counts_and_handles_empty_lists():
    card = CARD.model_copy(update={"synonyms": [], "antonyms": [], "word_parts": "", "forms": []})
    _, user = question_batch_prompt("frugal", "3-5", card, {"meaning": 2, "antonym": 0, "spell_it": 1}, [])
    assert "Write exactly 3 questions" in user
    assert "- meaning: 2" in user and "- spell_it: 1" in user
    assert "- antonym:" not in user
    assert "(none yet)" in user
    assert "Synonyms (correct answers for synonym questions come from here): (none)" in user
    assert "15 words" in user


def test_question_batch_prompt_rejects_unknown_type():
    with pytest.raises(ValueError):
        question_batch_prompt("frugal", "6-8", CARD, {"essay": 1}, [])


def test_check_prompt_shows_only_what_the_learner_sees():
    items = [
        {"qid": "q1", "type": "meaning", "prompt": 'What does "frugal" mean?',
         "choices": ["careful with money", "very loud", "quick to anger", "full of energy"]},
        {"qid": "q2", "type": "spell_it", "prompt": "Mom stays ___ by using coupons. (means: careful with money)",
         "choices": []},
    ]
    system, user = check_prompt(items, "6-8")
    assert user.splitlines()[:2] == ["Learners are in grades 6-8.", "Questions (one JSON object per line):"]
    lines = user.splitlines()[2:]
    assert [json.loads(line) for line in lines] == items
    assert "independently" in system
    for field in ("passes", "tempting", "chosen_index", "fill", "alternatives", "ambiguous", "reason"):
        assert field in system, field
    for rule in ("tempting = one true or false per choice, in order", "rate every choice, including your answer",
                 "a learner in the stated grades who only half-knows the tested word could reasonably pick it",
                 "false if it is nonsense, a joke or obviously unrelated", "tempting = []"):
        assert rule in system, rule
    assert "-1" in system
    for rule in ("test EACH choice on its own", "one true or false per choice, in order",
                 "a careful teacher would mark that choice right, even when another choice is better",
                 "grammatical and true passes even if it is less precise", 'hardest or most "vocabulary-like"',
                 "passes = []", "alternatives = []", "every other word or form that fits the blank and the hint",
                 "the answer's first letter (and, for a phrase, the number of words)",
                 "try the words inside the hint and their synonyms first"):
        assert rule in system, rule
    for forbidden in ("answer_index", "accepted_answers", "explanation"):
        assert forbidden not in system
        assert forbidden not in user
    for sentence in CARD.examples + [s.example for s in CARD.senses]:
        assert sentence not in system and sentence not in user


def test_check_prompt_strips_answer_keys_even_if_passed():
    leaky = {"qid": "q7", "type": "synonym", "prompt": "Closest in meaning to \"frugal\"?",
             "choices": ["thrifty", "noisy", "brave", "sleepy"], "answer_index": 0,
             "accepted_answers": [], "explanation": "Thrifty people save money.", "word": "frugal"}
    system, user = check_prompt([leaky], "9-12")
    assert user.splitlines()[0] == "Learners are in grades 9-12."
    payload = json.loads(user.splitlines()[2])
    assert payload == {"qid": "q7", "type": "synonym", "prompt": "Closest in meaning to \"frugal\"?",
                       "choices": ["thrifty", "noisy", "brave", "sleepy"]}
    assert "Thrifty people save money." not in user
    assert "answer_index" not in user and "explanation" not in user

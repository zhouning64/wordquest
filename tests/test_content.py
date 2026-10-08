from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

import app.ai.content as content_mod
from app import clock
from app.ai.content import (
    INITIAL_MIX,
    ContentGenerator,
    initial_mix,
    pool_meets_minimum,
    shortfall_mix,
    topup_mix,
)
from app.ai.llm import InvalidOutput, TransientError
from app.ai.schemas import ANSWER_CHECK, LEARN_CARD, QUESTION_BATCH
from app.logs import JsonlLog
from app.models import DEFAULT_EMOJI_SCENE, TIER, LearnCard, Question, Sense
from tests.fakes import FakeLLM

WORD = "frugal"
BAND = "6-8"


# ---------------------------------------------------------------- builders


def card_dict(**over: object) -> dict:
    d: dict = {
        "pos": "adjective",
        "forms": ["frugally", "frugality"],
        "short_def": "careful not to waste money or things",
        "kid_def": "If you are frugal, you save money and use things wisely instead of buying stuff you do not need.",
        "senses": [
            {
                "pos": "adjective",
                "definition": "careful about spending money",
                "example": "My frugal aunt buys bread on sale.",
            }
        ],
        "examples": [
            "The frugal team reused old jerseys for practice.",
            "Being frugal helped Sam save for a new game.",
            "Our frugal neighbor fixes his bike instead of buying one.",
            "A frugal cook uses every vegetable in the fridge.",
        ],
        "word_parts": "frug- (useful, thrifty) + -al (having the quality of)",
        "memory_hook": "A frugal frog saves every fly.",
        "synonyms": ["thrifty", "economical"],
        "antonyms": ["wasteful"],
        "right_use": {"sentence": "Mia was frugal and saved half her allowance."},
        "wrong_use": {
            "sentence": "The frugal family threw away a mountain of good food.",
            "why": "Frugal people avoid waste, so throwing away good food is not frugal.",
        },
        "image_scene": "A smiling kid dropping coins into a piggy bank next to a patched backpack.",
        "emoji_scene": "🐷💰🎒",
    }
    d.update(over)
    return d


def make_card(**over: object) -> LearnCard:
    return LearnCard.model_validate(card_dict(**over))


def rq(qtype: str, **over: object) -> dict:
    """A raw question dict (as the model returns it) that passes Q1-Q8 for 'frugal' / 6-8."""
    base: dict[str, dict] = {
        "meaning": {
            "prompt": "What does frugal mean?",
            "choices": [
                "careful not to waste money",
                "very loud and noisy",
                "afraid of the dark",
                "happy to share secrets",
            ],
            "answer_index": 0,
        },
        "pick_word": {
            "prompt": "Which word means careful not to waste money?",
            "choices": ["frugal", "fragile", "furious", "famous"],
            "answer_index": 0,
        },
        "fill_blank": {
            "prompt": "Grandpa is ___, so he reuses every paper bag.",
            "choices": ["sleepy", "frugal", "noisy", "brave"],
            "answer_index": 1,
        },
        "usage": {
            "prompt": "Which sentence uses frugal correctly?",
            "choices": [
                "The frugal thunder shook the windows.",
                "She was frugal at jumping over the fence.",
                "The frugal boy saved his birthday money for a bike.",
                "The puppy felt frugal after its nap.",
            ],
            "answer_index": 2,
        },
        "scenario": {
            "prompt": "Which situation shows someone being frugal?",
            "choices": [
                "Max buys three copies of the same toy.",
                "Ava leaves every light on all night.",
                "Leo orders snacks he never eats.",
                "Lena patches her old jeans instead of buying new ones.",
            ],
            "answer_index": 3,
        },
        "synonym": {
            "prompt": "Which word is closest in meaning to frugal?",
            "choices": ["generous", "thrifty", "careless", "greedy"],
            "answer_index": 1,
        },
        "antonym": {
            "prompt": "Which word means the opposite of frugal?",
            "choices": ["thrifty", "quiet", "wasteful", "polite"],
            "answer_index": 2,
        },
        "spell_it": {
            "prompt": "Nina saved her allowance because she was ___. (means: careful with money)",
            "choices": [],
            "answer_index": -1,
            "accepted_answers": ["frugal"],
        },
        "word_parts": {
            "prompt": "In frugal, what does the ending -al mean?",
            "choices": ["having the quality of", "before", "not", "many"],
            "answer_index": 0,
        },
    }
    d = {"type": qtype, "accepted_answers": [], "explanation": f"Explanation for {qtype}."}
    d.update(base[qtype])
    d.update(over)
    return d


def ok(qid: str, chosen_index: int = -1, fill: str = "") -> dict:
    return {"qid": qid, "chosen_index": chosen_index, "fill": fill, "ambiguous": False, "reason": "clear"}


_ids = itertools.count(1)


def pool_q(qtype: str, verified: bool = True) -> Question:
    return Question(
        id=f"{qtype}-{next(_ids)}",
        word=WORD,
        band=BAND,
        content_version=1,
        type=qtype,
        tier=TIER[qtype],
        prompt=f"existing {qtype} prompt",
        choices=[] if qtype == "spell_it" else ["a", "b", "c", "d"],
        answer_index=-1 if qtype == "spell_it" else 0,
        accepted_answers=[WORD] if qtype == "spell_it" else [],
        verified=verified,
    )


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def logs(tmp_path: Path) -> tuple[JsonlLog, JsonlLog, Path, Path]:
    rej = tmp_path / "logs" / "ai-rejections.jsonl"
    use = tmp_path / "logs" / "ai-usage.jsonl"
    return JsonlLog(rej), JsonlLog(use), rej, use


def gen(llm: FakeLLM, logs: tuple[JsonlLog, JsonlLog, Path, Path]) -> ContentGenerator:
    return ContentGenerator(llm, model_name="test-model", rejection_log=logs[0], usage_log=logs[1])


# ---------------------------------------------------------------- make_card


async def test_make_card_valid_returns_validated_card_and_logs_usage(logs):
    llm = FakeLLM({LEARN_CARD: [card_dict()]})
    card = await gen(llm, logs).make_card(WORD, BAND)
    assert isinstance(card, LearnCard)
    assert card.short_def == "careful not to waste money or things"
    assert len(card.examples) == 4
    assert card.senses[0].definition == "careful about spending money"
    assert [c["name"] for c in llm.calls] == [LEARN_CARD]
    assert WORD in llm.calls[0]["system"] + llm.calls[0]["user"]
    usage = read_jsonl(logs[3])
    assert usage == [{"word": WORD, "band": BAND, "call": LEARN_CARD, "usage": {"completion_tokens": 10}}]
    assert read_jsonl(logs[2]) == []


async def test_make_card_returns_cleaned_copy_with_default_emoji(logs):
    llm = FakeLLM({LEARN_CARD: [card_dict(emoji_scene="piggy bank")]})
    card = await gen(llm, logs).make_card(WORD, BAND)
    assert card.emoji_scene == DEFAULT_EMOJI_SCENE


async def test_make_card_rejected_raises_and_logs_raw(logs):
    bad = card_dict(short_def="x" * 91)
    llm = FakeLLM({LEARN_CARD: [bad]})
    with pytest.raises(InvalidOutput) as exc:
        await gen(llm, logs).make_card(WORD, BAND)
    assert "rejected" in str(exc.value)
    lines = read_jsonl(logs[2])
    assert len(lines) == 1
    rec = lines[0]
    assert rec["word"] == WORD and rec["band"] == BAND and rec["kind"] == "learn_card"
    assert isinstance(rec["errors"], list) and rec["errors"]
    assert rec["raw"] == bad
    assert len(read_jsonl(logs[3])) == 1  # usage is logged even for rejected output


async def test_make_card_unparseable_raises_and_logs(logs):
    bad = card_dict()
    del bad["short_def"]
    llm = FakeLLM({LEARN_CARD: [bad]})
    with pytest.raises(InvalidOutput):
        await gen(llm, logs).make_card(WORD, BAND)
    lines = read_jsonl(logs[2])
    assert len(lines) == 1 and lines[0]["kind"] == "learn_card" and lines[0]["raw"] == bad


async def test_make_card_llm_errors_propagate(logs):
    llm = FakeLLM({LEARN_CARD: [TransientError("upstream 503")]})
    with pytest.raises(TransientError):
        await gen(llm, logs).make_card(WORD, BAND)


async def test_generator_works_without_logs():
    llm = FakeLLM({LEARN_CARD: [card_dict(short_def="")]})
    g = ContentGenerator(llm, model_name="m")
    assert g.model_name == "m"
    with pytest.raises(InvalidOutput):
        await g.make_card(WORD, BAND)


# ---------------------------------------------------------------- make_questions


async def test_make_questions_keeps_verified_and_builds_questions(logs):
    card = make_card()
    batch = {"questions": [rq("meaning"), rq("fill_blank"), rq("synonym"), rq("spell_it")]}
    check = {
        "results": [
            ok("q1", 0),
            ok("q2", 1),
            ok("q3", 1),
            ok("q4", fill="frugal"),
        ]
    }
    llm = FakeLLM({QUESTION_BATCH: [batch], ANSWER_CHECK: [check]})
    qs = await gen(llm, logs).make_questions(WORD, BAND, card, {"meaning": 1, "fill_blank": 1}, [], 3)
    assert [q.type for q in qs] == ["meaning", "fill_blank", "synonym", "spell_it"]
    for q in qs:
        assert isinstance(q, Question)
        assert q.word == WORD and q.band == BAND
        assert q.content_version == 3
        assert q.tier == TIER[q.type]
        assert q.verified is True
        assert q.source == "ai"
        assert len(q.id) == 12
        assert q.created_at.endswith("Z")
    assert len({q.id for q in qs}) == 4
    fill = qs[1]
    assert fill.prompt == "Grandpa is ___, so he reuses every paper bag."
    assert fill.choices == ["sleepy", "frugal", "noisy", "brave"]
    assert fill.answer_index == 1 and fill.accepted_answers == []
    assert fill.explanation == "Explanation for fill_blank."
    spell = qs[3]
    assert spell.choices == [] and spell.answer_index == -1 and spell.accepted_answers == ["frugal"]
    assert [c["name"] for c in llm.calls] == [QUESTION_BATCH, ANSWER_CHECK]
    assert [r["call"] for r in read_jsonl(logs[3])] == [QUESTION_BATCH, ANSWER_CHECK]
    assert read_jsonl(logs[2]) == []


async def test_created_at_is_one_shared_timestamp_from_app_clock(logs, monkeypatch):
    ticks = itertools.count()
    monkeypatch.setattr(clock, "utc_now_iso", lambda: f"2001-02-03T04:05:{next(ticks):02d}Z")
    batch = {"questions": [rq("meaning"), rq("fill_blank"), rq("synonym")]}
    check = {"results": [ok("q1", 0), ok("q2", 1), ok("q3", 1)]}
    llm = FakeLLM({QUESTION_BATCH: [batch], ANSWER_CHECK: [check]})
    qs = await gen(llm, logs).make_questions(WORD, BAND, make_card(), {"meaning": 1}, [], 1)
    assert len(qs) == 3
    assert len({q.created_at for q in qs}) == 1  # read once for the whole batch
    assert qs[0].created_at.startswith("2001-02-03T04:05:")  # read through the app.clock module (frozen here)


async def test_validator_drops_are_logged_and_never_checked(logs):
    card = make_card()
    batch = {
        "questions": [
            rq("meaning", choices=["careful with money", "loud", "scared"]),  # Q1: 3 choices
            rq("pick_word"),
            rq("fill_blank", prompt="Grandpa is frugal, so he reuses every paper bag."),  # Q2: no blank
            rq("antonym"),
        ]
    }
    # qids are assigned to the survivors only, in original order: pick_word=q1, antonym=q2
    check = {"results": [ok("q1", 0), ok("q2", 2)]}
    llm = FakeLLM({QUESTION_BATCH: [batch], ANSWER_CHECK: [check]})
    qs = await gen(llm, logs).make_questions(WORD, BAND, card, initial_mix(card), [], 1)
    assert [q.type for q in qs] == ["pick_word", "antonym"]
    rej = read_jsonl(logs[2])
    assert [r["kind"] for r in rej] == ["question", "question"]
    assert [r["raw"]["type"] for r in rej] == ["meaning", "fill_blank"]
    assert all(r["errors"] and r["word"] == WORD and r["band"] == BAND for r in rej)
    check_user = llm.calls[1]["user"]
    assert "Grandpa is frugal" not in check_user
    assert "scared" not in check_user


async def test_all_dropped_by_validator_skips_check_call(logs):
    card = make_card()
    batch = {"questions": [rq("meaning", choices=["same", "Same ", "other", "thing"])]}  # Q1: not distinct
    llm = FakeLLM({QUESTION_BATCH: [batch]})  # no ANSWER_CHECK script: a check call would fail
    qs = await gen(llm, logs).make_questions(WORD, BAND, card, {"meaning": 1}, [], 1)
    assert qs == []
    assert [c["name"] for c in llm.calls] == [QUESTION_BATCH]


async def test_empty_mix_makes_no_calls(logs):
    llm = FakeLLM({})
    qs = await gen(llm, logs).make_questions(WORD, BAND, make_card(), {"meaning": 0}, [], 1)
    assert qs == [] and llm.calls == []


async def test_checker_mismatch_ambiguous_missing_duplicate_are_dropped(logs):
    card = make_card()
    batch = {
        "questions": [
            rq("meaning"),  # q1: checker picks a different choice -> mismatch
            rq("pick_word"),  # q2: ambiguous -> dropped
            rq("fill_blank"),  # q3: no result -> dropped
            rq("usage"),  # q4: two results -> dropped
            rq("scenario"),  # q5: correct -> kept
            rq("spell_it"),  # q6: wrong fill -> dropped
        ]
    }
    check = {
        "results": [
            ok("q1", 2),
            {"qid": "q2", "chosen_index": 0, "fill": "", "ambiguous": True, "reason": "two fit"},
            ok("q4", 2),
            ok("q4", 2),
            ok("q5", 3),
            ok("q6", fill="frugel"),
            ok("q99", 0),  # unknown qid is ignored
        ]
    }
    llm = FakeLLM({QUESTION_BATCH: [batch], ANSWER_CHECK: [check]})
    qs = await gen(llm, logs).make_questions(WORD, BAND, card, initial_mix(card), [], 2)
    assert [q.type for q in qs] == ["scenario"]
    rej = read_jsonl(logs[2])
    assert all(r["kind"] == "question_check" for r in rej)
    reasons = {r["raw"]["qid"]: r["errors"][0] for r in rej}
    assert reasons == {
        "q1": "check: answer mismatch",
        "q2": "check: ambiguous",
        "q3": "check: no result",
        "q4": "check: duplicate result",
        "q6": "check: answer mismatch",
    }
    assert rej[0]["raw"]["question"]["type"] == "meaning"


@pytest.mark.parametrize("fill", ["frugal", "  FRUGAL ", "Frugal"])
async def test_spell_it_fill_is_normalized(logs, fill):
    card = make_card()
    batch = {"questions": [rq("spell_it")]}
    check = {"results": [ok("q1", fill=fill)]}
    llm = FakeLLM({QUESTION_BATCH: [batch], ANSWER_CHECK: [check]})
    qs = await gen(llm, logs).make_questions(WORD, BAND, card, {"spell_it": 1}, [], 1)
    assert len(qs) == 1 and qs[0].type == "spell_it"


async def test_spell_it_curly_apostrophe_and_accepted_answers_normalized(logs):
    word = "o'clock"
    card = LearnCard(
        pos="adverb",
        short_def="at an exact hour",
        kid_def="We say o'clock after a number to tell the exact hour.",
        senses=[Sense(pos="adverb", definition="at an exact hour", example="School starts at eight o'clock.")],
        examples=[
            "Lunch is at twelve o'clock.",
            "The bus comes at seven o'clock.",
            "We met at three o'clock.",
            "The show ends at nine o'clock.",
        ],
    )
    spell = {
        "type": "spell_it",
        "prompt": "Soccer practice starts at four ___ sharp. (means: at an exact hour)",
        "choices": [],
        "answer_index": -1,
        "accepted_answers": ["o'clock", "O'Clock"],
        "explanation": "We write o'clock after the hour.",
    }
    llm = FakeLLM({QUESTION_BATCH: [{"questions": [spell]}], ANSWER_CHECK: [{"results": [ok("q1", fill="O’CLOCK")]}]})
    qs = await gen(llm, logs).make_questions(word, "3-5", card, {"spell_it": 1}, [], 1)
    assert len(qs) == 1
    assert qs[0].accepted_answers == ["o'clock"]
    assert qs[0].band == "3-5" and qs[0].word == word


async def test_check_items_never_contain_answers(logs, monkeypatch):
    seen: list[list[dict]] = []
    real_check_prompt = content_mod.check_prompt

    def spy(items: list[dict]) -> tuple[str, str]:
        seen.append(items)
        return real_check_prompt(items)

    monkeypatch.setattr(content_mod, "check_prompt", spy)
    card = make_card()
    batch = {
        "questions": [
            rq("spell_it", explanation="The answer is frugal."),
            rq(
                "spell_it",
                prompt="Dad fixed the old lamp instead of buying one because he is ___. (means: careful with money)",
                explanation="Frugal fits because he saved money.",
            ),
        ]
    }
    check = {"results": [ok("q1", fill="frugal"), ok("q2", fill="frugal")]}
    llm = FakeLLM({QUESTION_BATCH: [batch], ANSWER_CHECK: [check]})
    qs = await gen(llm, logs).make_questions(WORD, BAND, card, {"spell_it": 2}, [], 1)
    assert len(qs) == 2
    assert len(seen) == 1
    assert [sorted(item) for item in seen[0]] == [["choices", "prompt", "qid", "type"]] * 2
    assert [item["qid"] for item in seen[0]] == ["q1", "q2"]
    check_call = llm.calls[1]
    assert check_call["name"] == ANSWER_CHECK
    user = check_call["user"].lower()
    assert "frugal" not in user  # neither the accepted answer, the word, nor the card leaks
    assert "the answer is" not in user  # explanations are not sent
    assert "careful not to waste money or things" not in user


async def test_existing_prompts_are_sent_and_repeats_dropped(logs, monkeypatch):
    captured: dict = {}
    real = content_mod.question_batch_prompt

    def spy(word, band, card, mix, existing_prompts):
        captured["mix"] = mix
        captured["existing_prompts"] = existing_prompts
        return real(word, band, card, mix, existing_prompts)

    monkeypatch.setattr(content_mod, "question_batch_prompt", spy)
    card = make_card()
    existing = [pool_q("meaning")]
    existing[0] = existing[0].model_copy(update={"prompt": "What does frugal mean?"})
    batch = {"questions": [rq("meaning"), rq("pick_word")]}  # meaning repeats an existing prompt (Q8)
    check = {"results": [ok("q1", 0)]}
    llm = FakeLLM({QUESTION_BATCH: [batch], ANSWER_CHECK: [check]})
    qs = await gen(llm, logs).make_questions(WORD, BAND, card, {"meaning": 1, "pick_word": 1}, existing, 1)
    assert captured["existing_prompts"] == ["What does frugal mean?"]
    assert captured["mix"] == {"meaning": 1, "pick_word": 1}
    assert [q.type for q in qs] == ["pick_word"]


async def test_unparseable_batch_raises_invalid_output(logs):
    llm = FakeLLM({QUESTION_BATCH: [{"items": []}]})
    with pytest.raises(InvalidOutput):
        await gen(llm, logs).make_questions(WORD, BAND, make_card(), {"meaning": 1}, [], 1)
    assert read_jsonl(logs[2])[0]["kind"] == "question_batch"


async def test_unparseable_check_raises_invalid_output(logs):
    batch = {"questions": [rq("meaning")]}
    llm = FakeLLM({QUESTION_BATCH: [batch], ANSWER_CHECK: [{"results": [{"qid": "q1"}]}]})
    with pytest.raises(InvalidOutput):
        await gen(llm, logs).make_questions(WORD, BAND, make_card(), {"meaning": 1}, [], 1)
    assert read_jsonl(logs[2])[-1]["kind"] == "answer_check"


# ---------------------------------------------------------------- mix helpers


def test_initial_mix_default_is_twelve():
    mix = initial_mix(make_card())
    assert mix == INITIAL_MIX
    assert sum(mix.values()) == 12


def test_initial_mix_substitutions():
    card = make_card(synonyms=[], antonyms=[], word_parts="")
    mix = initial_mix(card)
    assert mix == {"meaning": 1, "pick_word": 1, "fill_blank": 2, "usage": 2, "scenario": 3, "spell_it": 3}
    assert sum(mix.values()) == 12


def test_initial_mix_single_substitution_and_no_mutation():
    mix = initial_mix(make_card(antonyms=[]))
    assert "antonym" not in mix and mix["scenario"] == 3 and mix["synonym"] == 1
    assert INITIAL_MIX["antonym"] == 1 and INITIAL_MIX["scenario"] == 2


def test_pool_meets_minimum():
    good = [pool_q(t) for t in ("meaning", "pick_word", "usage", "spell_it", "spell_it", "word_parts")]
    assert pool_meets_minimum(good) is True
    assert pool_meets_minimum(good[:5]) is False  # only 5
    one_t1 = [pool_q(t) for t in ("meaning", "usage", "usage", "spell_it", "spell_it", "word_parts")]
    assert pool_meets_minimum(one_t1) is False
    no_t2 = [pool_q(t) for t in ("meaning", "pick_word", "fill_blank", "spell_it", "spell_it", "word_parts")]
    assert pool_meets_minimum(no_t2) is False
    unverified = good[:5] + [pool_q("scenario", verified=False)]
    assert pool_meets_minimum(unverified) is False


def test_shortfall_mix_empty_when_minimum_met():
    good = [pool_q(t) for t in ("meaning", "pick_word", "usage", "spell_it", "spell_it", "word_parts")]
    assert shortfall_mix(good, make_card()) == {}


def test_shortfall_mix_from_empty_pool():
    mix = shortfall_mix([], make_card())
    assert mix == {
        "meaning": 3,
        "pick_word": 2,
        "fill_blank": 2,
        "usage": 2,
        "scenario": 1,
        "synonym": 1,
        "antonym": 1,
    }
    assert sum(mix.values()) == 12


def test_shortfall_mix_missing_tiers_only():
    qs = [pool_q(t) for t in ("spell_it", "spell_it", "spell_it", "spell_it", "word_parts", "word_parts")]
    assert shortfall_mix(qs, make_card()) == {"meaning": 2, "pick_word": 1, "fill_blank": 1, "usage": 1, "scenario": 1}


def test_shortfall_mix_total_only_prefers_least_used_types():
    qs = [pool_q(t) for t in ("meaning", "meaning", "usage", "spell_it", "word_parts")]
    assert shortfall_mix(qs, make_card()) == {"pick_word": 1, "scenario": 1}


def test_shortfall_mix_ignores_unverified_and_respects_card():
    card = make_card(synonyms=[], antonyms=[])
    qs = [pool_q("meaning", verified=False), pool_q("usage", verified=False)]
    mix = shortfall_mix(qs, card)
    assert set(mix) <= {"meaning", "pick_word", "fill_blank", "usage", "scenario"}
    assert sum(v for t, v in mix.items() if TIER[t] == 1) == 7
    assert sum(v for t, v in mix.items() if TIER[t] == 2) == 5


def test_topup_mix_balanced_pool_spreads_across_tiers():
    pool = [pool_q(t) for t, n in INITIAL_MIX.items() for _ in range(n)]
    assert topup_mix(pool, make_card(), 6) == {
        "meaning": 1,
        "pick_word": 1,
        "usage": 1,
        "scenario": 1,
        "spell_it": 1,
        "word_parts": 1,
    }


def test_topup_mix_targets_lowest_relative_tier():
    pool = [pool_q(t) for t in ("meaning", "pick_word", "fill_blank", "fill_blank")]
    pool += [pool_q(t) for t in ("usage", "scenario", "scenario", "synonym", "antonym")]
    assert topup_mix(pool, make_card(), 3) == {"spell_it": 2, "word_parts": 1}


def test_topup_mix_respects_card_substitutions_and_zero():
    card = make_card(word_parts="")
    pool = [pool_q(t) for t in ("meaning", "pick_word", "fill_blank", "fill_blank")]
    pool += [pool_q(t) for t in ("usage", "scenario", "scenario", "synonym", "antonym")]
    assert topup_mix(pool, card, 3) == {"spell_it": 3}
    assert topup_mix(pool, card, 0) == {}

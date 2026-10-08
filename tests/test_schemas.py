from __future__ import annotations

import json

import pytest

from app.ai.llm import InvalidOutput
from app.ai.schemas import (
    ANSWER_CHECK,
    ANSWER_CHECK_SCHEMA,
    LEARN_CARD,
    LEARN_CARD_SCHEMA,
    QUESTION_BATCH,
    QUESTION_BATCH_SCHEMA,
    CheckResult,
    RawQuestion,
    parse_card,
    parse_check,
    parse_questions,
)
from app.models import QTYPES, LearnCard, RightUse, Sense, WrongUse

FORBIDDEN_KEYS = {
    "minItems", "maxItems", "pattern", "format", "oneOf", "allOf", "not",
    "if", "then", "else", "patternProperties",
}


def assert_strict(node, path: str = "$") -> None:
    """Walk a JSON schema and assert it only uses Cerebras strict-mode compatible constructs."""
    assert isinstance(node, dict), f"{path}: schema node must be an object"
    bad = FORBIDDEN_KEYS & set(node)
    assert not bad, f"{path}: forbidden keys {sorted(bad)}"
    kind = node.get("type")
    if kind == "object":
        props = node.get("properties")
        assert isinstance(props, dict) and props, f"{path}: object needs properties"
        assert node.get("additionalProperties") is False, f"{path}: additionalProperties must be False"
        assert node.get("required") == list(props), f"{path}: required must list every property in order"
        for key, child in props.items():
            assert_strict(child, f"{path}.{key}")
    elif kind == "array":
        assert "items" in node, f"{path}: array needs items"
        assert_strict(node["items"], f"{path}[]")
    else:
        assert kind in {"string", "integer", "boolean", "number"}, f"{path}: unexpected type {kind!r}"


@pytest.mark.parametrize("schema", [LEARN_CARD_SCHEMA, QUESTION_BATCH_SCHEMA, ANSWER_CHECK_SCHEMA])
def test_schema_is_strict_mode_compatible(schema):
    assert schema["type"] == "object"
    assert_strict(schema)
    json.dumps(schema)  # serializable as-is


def test_strict_walker_rejects_bad_schemas():
    with pytest.raises(AssertionError):
        assert_strict({"type": "object", "properties": {"a": {"type": "string"}}, "required": []})
    with pytest.raises(AssertionError):
        assert_strict({"type": "array", "items": {"type": "string"}, "maxItems": 3})
    with pytest.raises(AssertionError):
        assert_strict({"type": "array"})


def test_schema_names():
    assert (LEARN_CARD, QUESTION_BATCH, ANSWER_CHECK) == ("learn_card", "question_batch", "answer_check")


def test_learn_card_schema_matches_model_fields():
    props = LEARN_CARD_SCHEMA["properties"]
    assert set(props) == set(LearnCard.model_fields)
    assert set(props["senses"]["items"]["properties"]) == set(Sense.model_fields) == {"pos", "definition", "example"}
    assert set(props["right_use"]["properties"]) == set(RightUse.model_fields)
    assert set(props["wrong_use"]["properties"]) == set(WrongUse.model_fields)


def test_question_batch_schema_shape():
    assert list(QUESTION_BATCH_SCHEMA["properties"]) == ["questions"]
    item = QUESTION_BATCH_SCHEMA["properties"]["questions"]["items"]
    assert set(item["properties"]) == set(RawQuestion.model_fields)
    assert item["properties"]["type"] == {"type": "string", "enum": list(QTYPES)}
    assert item["properties"]["answer_index"] == {"type": "integer", "enum": [-1, 0, 1, 2, 3]}


def test_answer_check_schema_shape():
    assert list(ANSWER_CHECK_SCHEMA["properties"]) == ["results"]
    item = ANSWER_CHECK_SCHEMA["properties"]["results"]["items"]
    assert list(item["properties"]) == ["qid", "passes", "chosen_index", "fill", "alternatives", "ambiguous", "reason"]
    assert set(item["properties"]) == set(CheckResult.model_fields)
    assert item["properties"]["passes"] == {"type": "array", "items": {"type": "boolean"}}
    assert item["properties"]["alternatives"] == {"type": "array", "items": {"type": "string"}}
    assert item["properties"]["ambiguous"] == {"type": "boolean"}
    assert item["properties"]["chosen_index"]["enum"] == [-1, 0, 1, 2, 3]


@pytest.mark.parametrize("schema", [LEARN_CARD_SCHEMA, QUESTION_BATCH_SCHEMA, ANSWER_CHECK_SCHEMA])
def test_schema_stays_under_the_strict_mode_size_limit(schema):
    assert len(json.dumps(schema)) < 5000


CARD_DATA = {
    "pos": "adjective",
    "forms": ["frugally", "frugality"],
    "short_def": "careful with money; not wasteful",
    "kid_def": "A frugal person spends money carefully and does not waste things.",
    "senses": [{"pos": "adjective", "definition": "careful not to waste money", "example": "Mia is frugal with her allowance."}],
    "examples": ["a frugal one", "two frugal", "three frugal", "four frugal"],
    "word_parts": "",
    "memory_hook": "Frugal friends find free fun.",
    "synonyms": ["thrifty"],
    "antonyms": ["wasteful"],
    "right_use": {"sentence": "Being frugal, Sam fixed his old bike."},
    "wrong_use": {"sentence": "The frugal storm knocked over a tree.", "why": "Storms cannot be careful with money."},
    "image_scene": "A kid dropping coins into a piggy bank.",
    "emoji_scene": "🐷🪙",
}


def test_parse_card_valid():
    card = parse_card(CARD_DATA)
    assert isinstance(card, LearnCard)
    assert card.senses[0].definition == "careful not to waste money"
    assert card.wrong_use.why == "Storms cannot be careful with money."
    assert card.forms == ["frugally", "frugality"]


@pytest.mark.parametrize(
    "bad",
    [
        {**CARD_DATA, "senses": [{"pos": "adjective", "example": "no definition here"}]},
        {**CARD_DATA, "examples": "not a list"},
        {k: v for k, v in CARD_DATA.items() if k != "short_def"},
        ["not", "an", "object"],
    ],
)
def test_parse_card_invalid_raises_invalid_output(bad):
    with pytest.raises(InvalidOutput):
        parse_card(bad)


QUESTION = {
    "type": "spell_it",
    "prompt": "Mom stays ___ by using coupons. (means: careful with money)",
    "choices": [],
    "answer_index": -1,
    "accepted_answers": ["frugal"],
    "explanation": "Frugal means careful with money.",
}


def test_parse_questions_valid():
    qs = parse_questions({"questions": [QUESTION, {**QUESTION, "type": "meaning", "choices": ["a", "b", "c", "d"], "answer_index": 2, "accepted_answers": []}]})
    assert [q.type for q in qs] == ["spell_it", "meaning"]
    assert isinstance(qs[0], RawQuestion)
    assert qs[1].answer_index == 2


def test_parse_questions_empty_list_is_valid():
    assert parse_questions({"questions": []}) == []


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"questions": "nope"},
        {"questions": [{**QUESTION, "type": "essay"}]},
        {"questions": [{k: v for k, v in QUESTION.items() if k != "explanation"}]},
        {"questions": [{**QUESTION, "choices": [1, 2]}]},
    ],
)
def test_parse_questions_invalid(bad):
    with pytest.raises(InvalidOutput):
        parse_questions(bad)


CHECK = {"qid": "q1", "passes": [False, False, True, False], "chosen_index": 2, "fill": "", "alternatives": [],
         "ambiguous": False, "reason": "clear"}


def test_parse_check_valid():
    spell = {**CHECK, "qid": "q2", "passes": [], "chosen_index": -1, "fill": "huge", "alternatives": ["giant"]}
    results = parse_check({"results": [CHECK, spell]})
    assert results == [
        CheckResult(qid="q1", passes=[False, False, True, False], chosen_index=2, fill="", alternatives=[],
                    ambiguous=False, reason="clear"),
        CheckResult(qid="q2", passes=[], chosen_index=-1, fill="huge", alternatives=["giant"], ambiguous=False,
                    reason="clear"),
    ]


@pytest.mark.parametrize(
    "bad",
    [
        {"result": []},
        {"results": [{k: v for k, v in CHECK.items() if k != "ambiguous"}]},
        {"results": [{**CHECK, "ambiguous": "maybe"}]},
        {"results": [{k: v for k, v in CHECK.items() if k != "passes"}]},  # the old result shape
        {"results": [{k: v for k, v in CHECK.items() if k != "alternatives"}]},
        {"results": [{**CHECK, "passes": "yes"}]},
        {"results": [{**CHECK, "alternatives": [1, 2]}]},
        None,
    ],
)
def test_parse_check_invalid(bad):
    with pytest.raises(InvalidOutput):
        parse_check(bad)

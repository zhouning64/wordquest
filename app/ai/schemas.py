from __future__ import annotations

from pydantic import BaseModel, TypeAdapter, ValidationError

from app.ai.llm import InvalidOutput
from app.models import QTYPES, LearnCard, QType

# Schema names sent as response_format.json_schema.name (also the FakeLLM script keys).
LEARN_CARD = "learn_card"
QUESTION_BATCH = "question_batch"
ANSWER_CHECK = "answer_check"

# Strict-mode rules (Cerebras): root is an object; every object lists every property in "required" and sets
# additionalProperties false; no minItems/maxItems/pattern/format/oneOf/allOf. Counts, lengths and per-type
# shapes are enforced in code by app/ai/validate.py.
_INDEX_ENUM = [-1, 0, 1, 2, 3]


def _str() -> dict:
    return {"type": "string"}


def _str_list() -> dict:
    return {"type": "array", "items": {"type": "string"}}


def _bool_list() -> dict:
    return {"type": "array", "items": {"type": "boolean"}}


def _obj(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


LEARN_CARD_SCHEMA: dict = _obj(
    {
        "pos": _str(),
        "forms": _str_list(),
        "short_def": _str(),
        "kid_def": _str(),
        "senses": {
            "type": "array",
            "items": _obj({"pos": _str(), "definition": _str(), "example": _str()}),
        },
        "examples": _str_list(),
        "word_parts": _str(),
        "memory_hook": _str(),
        "synonyms": _str_list(),
        "antonyms": _str_list(),
        "right_use": _obj({"sentence": _str()}),
        "wrong_use": _obj({"sentence": _str(), "why": _str()}),
        "image_scene": _str(),
        "emoji_scene": _str(),
    }
)

QUESTION_BATCH_SCHEMA: dict = _obj(
    {
        "questions": {
            "type": "array",
            "items": _obj(
                {
                    "type": {"type": "string", "enum": list(QTYPES)},
                    "prompt": _str(),
                    "choices": _str_list(),
                    "answer_index": {"type": "integer", "enum": list(_INDEX_ENUM)},
                    "accepted_answers": _str_list(),
                    "explanation": _str(),
                }
            ),
        }
    }
)

ANSWER_CHECK_SCHEMA: dict = _obj(
    {
        "results": {
            "type": "array",
            "items": _obj(
                {
                    "qid": _str(),
                    "passes": _bool_list(),  # choice questions: one verdict per choice, in order; spell_it: []
                    "chosen_index": {"type": "integer", "enum": list(_INDEX_ENUM)},
                    "fill": _str(),
                    "alternatives": _str_list(),  # spell_it: other words that fit the blank and hint; else []
                    "ambiguous": {"type": "boolean"},
                    "reason": _str(),
                }
            ),
        }
    }
)


class RawQuestion(BaseModel):
    """One question exactly as the model produced it (before validation and the blind check)."""

    type: QType
    prompt: str
    choices: list[str]
    answer_index: int
    accepted_answers: list[str]
    explanation: str


class CheckResult(BaseModel):
    """One blind-check answer for the question with the given qid.

    passes: for a choice question, whether a careful teacher would mark each choice right (in choice order);
    alternatives: for spell_it, every other word or form the checker found that fits the blank and the hint."""

    qid: str
    passes: list[bool]
    chosen_index: int
    fill: str
    alternatives: list[str]
    ambiguous: bool
    reason: str


_QUESTION_LIST = TypeAdapter(list[RawQuestion])
_CHECK_LIST = TypeAdapter(list[CheckResult])


def _brief(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    more = exc.error_count() - len(parts)
    if more > 0:
        parts.append(f"and {more} more")
    return "; ".join(parts)


def _field(data: object, key: str, what: str) -> object:
    if not isinstance(data, dict) or key not in data:
        raise InvalidOutput(f"{what}: expected an object with a {key!r} field")
    return data[key]


def parse_card(data: dict) -> LearnCard:
    if not isinstance(data, dict):
        raise InvalidOutput("learn card: expected a JSON object")
    try:
        return LearnCard.model_validate(data)
    except ValidationError as exc:
        raise InvalidOutput(f"learn card does not match the schema: {_brief(exc)}") from None


def parse_questions(data: dict) -> list[RawQuestion]:
    items = _field(data, "questions", "question batch")
    try:
        return _QUESTION_LIST.validate_python(items)
    except ValidationError as exc:
        raise InvalidOutput(f"question batch does not match the schema: {_brief(exc)}") from None


def parse_check(data: dict) -> list[CheckResult]:
    items = _field(data, "results", "answer check")
    try:
        return _CHECK_LIST.validate_python(items)
    except ValidationError as exc:
        raise InvalidOutput(f"answer check does not match the schema: {_brief(exc)}") from None

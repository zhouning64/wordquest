"""Test builders shared by every test module. Import as `from tests.factories import make_card, ...`.

Every builder accepts keyword overrides for any model field. created_at values come from a
module-level counter, so objects built later always sort later (stable ordering in storage tests).
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.clock import iso
from app.models import (
    TIER,
    LearnCard,
    Profile,
    Question,
    QTYPES,
    RightUse,
    Sense,
    WordContent,
    WordList,
    WrongUse,
    new_id,
)

_BASE_TIME = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)
_seq = itertools.count()

SHORT_DEF = "careful with what you have"
SYNONYMS = ["thrifty", "careful"]
ANTONYMS = ["wasteful", "careless"]
DISTRACTOR_WORDS = ["noisy", "rapid", "gloomy"]


def next_timestamp() -> str:
    """A UTC ISO timestamp one second later than the previous call."""
    return iso(_BASE_TIME + timedelta(seconds=next(_seq)))


def _place(correct: str, distractors: list[str], index: int) -> list[str]:
    choices = list(distractors[:3])
    choices.insert(index, correct)
    return choices


def make_profile(**kw) -> Profile:
    data: dict = {
        "id": new_id(),
        "name": "Mia",
        "avatar": "🦊",
        "band": "6-8",
        "list_ids": [],
        "created_at": next_timestamp(),
    }
    data.update(kw)
    return Profile(**data)


def make_list(words: Iterable[str], **kw) -> WordList:
    ts = next_timestamp()
    data: dict = {"id": new_id(), "name": "Week 1", "words": list(words), "created_at": ts, "updated_at": ts}
    data.update(kw)
    return WordList(**data)


def make_card(word: str, **kw) -> LearnCard:
    """A Learn card that passes every §7.5 rule for any band (all sentences ≤ 15 words)."""
    data: dict = {
        "pos": "adjective",
        "forms": [f"{word}s"],
        "short_def": SHORT_DEF,
        "kid_def": "A person like this is careful with what they have and wastes nothing.",
        "senses": [
            Sense(pos="adjective", definition=SHORT_DEF, example=f"My aunt is {word} with her weekly allowance."),
            Sense(pos="adjective", definition="simple and plain", example=f"We ate a {word} lunch of bread and soup."),
        ],
        "examples": [
            f"The {word} coach saved the team's old jerseys.",
            f"Being {word} helped Sam buy a new skateboard.",
            f"Our {word} class reused paper for the science project.",
            f"Maya stayed {word} during the long camping trip.",
            f"A {word} gamer waits for sales before buying games.",
        ],
        "word_parts": f"{word[:3]}- (root) + ending = careful use",
        "memory_hook": f"Think of {word} when you drop coins in a piggy bank.",
        "synonyms": list(SYNONYMS),
        "antonyms": list(ANTONYMS),
        "right_use": RightUse(sentence=f"Dad was {word} and fixed the old bike instead of buying one."),
        "wrong_use": WrongUse(sentence=f"The {word} thunder shook the windows.", why="Thunder cannot be careful with money."),
        "image_scene": "A smiling kid dropping coins into a piggy bank on a desk.",
        "emoji_scene": "🐷💰",
    }
    data.update(kw)
    return LearnCard(**data)


def make_content(word: str, band: str = "6-8", status: str = "ready", version: int = 1, **kw) -> WordContent:
    """WordContent; a card is included only when status == "ready" (override with card=...)."""
    data: dict = {
        "word": word,
        "band": band,
        "status": status,
        "source": "ai",
        "content_version": version,
        "card": make_card(word) if status == "ready" else None,
        "model": "fake-model",
        "generated_at": next_timestamp() if status == "ready" else "",
    }
    data.update(kw)
    return WordContent(**data)


def _question_fields(word: str, qtype: str) -> dict:
    if qtype == "meaning":
        return {
            "prompt": f"What does “{word}” mean?",
            "choices": _place(SHORT_DEF, ["very loud", "full of energy", "hard to see"], 0),
            "answer_index": 0,
        }
    if qtype == "pick_word":
        return {
            "prompt": f"Which word means “{SHORT_DEF}”?",
            "choices": _place(word, DISTRACTOR_WORDS, 1),
            "answer_index": 1,
        }
    if qtype == "fill_blank":
        return {
            "prompt": "Lena was ___ and saved most of her birthday money.",
            "choices": _place(word, DISTRACTOR_WORDS, 2),
            "answer_index": 2,
        }
    if qtype == "usage":
        return {
            "prompt": f"Which sentence uses “{word}” correctly?",
            "choices": _place(
                f"Theo was {word} and kept his old shoes until they wore out.",
                [
                    f"The {word} rain made a loud noise.",
                    f"She ran {word} across the field.",
                    f"The {word} cloud ate a sandwich.",
                ],
                3,
            ),
            "answer_index": 3,
        }
    if qtype == "scenario":
        return {
            "prompt": f"Which situation shows someone being {word}?",
            "choices": _place(
                "Jo compares prices before buying a snack.",
                ["Jo shouts during the movie.", "Jo forgets her homework.", "Jo runs the fastest mile."],
                0,
            ),
            "answer_index": 0,
        }
    if qtype == "synonym":
        return {
            "prompt": f"Which word is closest in meaning to “{word}”?",
            "choices": _place(SYNONYMS[0], DISTRACTOR_WORDS, 1),
            "answer_index": 1,
        }
    if qtype == "antonym":
        return {
            "prompt": f"Which word is the opposite of “{word}”?",
            "choices": _place(ANTONYMS[0], ["quiet", "slow", "cheerful"], 2),
            "answer_index": 2,
        }
    if qtype == "spell_it":
        return {
            "prompt": f"Ana was so ___ that she reused every gift bag. (means: {SHORT_DEF})",
            "choices": [],
            "answer_index": -1,
            "accepted_answers": [word],
        }
    if qtype == "word_parts":
        return {
            "prompt": f"In “{word}”, what does the root “{word[:3]}-” point to?",
            "choices": _place("using things carefully", ["making noise", "moving fast", "feeling sad"], 3),
            "answer_index": 3,
        }
    raise ValueError(f"unknown question type: {qtype}")


def make_question(
    word: str, band: str = "6-8", type: str = "meaning", version: int = 1, verified: bool = True, **kw
) -> Question:
    """A question with a valid shape for its type: 4 choices + answer_index for choice types;
    choices [] / answer_index -1 / accepted_answers [word] for spell_it."""
    data: dict = {
        "id": new_id(),
        "word": word,
        "band": band,
        "content_version": version,
        "type": type,
        "tier": TIER[type],
        "accepted_answers": [],
        "explanation": f"“{word}” means {SHORT_DEF}.",
        "source": "ai",
        "verified": verified,
        "created_at": next_timestamp(),
    }
    data.update(_question_fields(word, type))
    data.update(kw)
    return Question(**data)


def make_pool(
    word: str, band: str = "6-8", version: int = 1, n_per_type: int | dict[str, int] | None = None
) -> list[Question]:
    """Verified questions covering tiers 1-3, in QTYPES order with unique prompts.

    n_per_type: None → one per type (9 questions); int → that many of every type;
    dict → count per type (types not listed get 0).
    """
    if n_per_type is None:
        counts = {t: 1 for t in QTYPES}
    elif isinstance(n_per_type, int):
        counts = {t: n_per_type for t in QTYPES}
    else:
        counts = {t: n_per_type.get(t, 0) for t in QTYPES}
    pool: list[Question] = []
    for qtype in QTYPES:
        for k in range(counts[qtype]):
            q = make_question(word, band=band, type=qtype, version=version)
            if k:
                q = q.model_copy(update={"prompt": f"Round {k + 1}: {q.prompt}"})
            pool.append(q)
    return pool

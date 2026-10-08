from __future__ import annotations

# Builders shared by the generation tests (tests/test_triggers.py and tests/test_jobs.py).

from typing import Sequence

from app import clock
from app.ai.schemas import RawQuestion
from app.ai.validate import validate_questions
from app.models import TIER, LearnCard, Question, RightUse, Sense, WordContent, WrongUse, new_id
from app.storage.base import Repository

WORD = "frugal"
BAND = "6-8"


def make_card(short_def: str = "careful with money; not wasteful") -> LearnCard:
    """A Learn card for "frugal" that passes validate_card for every band."""
    return LearnCard(
        pos="adjective",
        forms=["frugally"],
        short_def=short_def,
        kid_def="Someone who is frugal is careful not to waste money or things.",
        senses=[Sense(pos="adjective", definition="careful with money and resources",
                      example="My frugal aunt reuses every glass jar.")],
        examples=[
            "Being frugal helped Sam save for a new skateboard.",
            "The frugal team fixed old balls instead of buying new ones.",
            "A frugal cook turns leftover rice into a tasty lunch.",
            "Mia stayed frugal with her allowance all summer.",
        ],
        word_parts="",
        memory_hook="Frugal friends find free fun.",
        synonyms=["thrifty", "economical"],
        antonyms=["wasteful", "extravagant"],
        right_use=RightUse(sentence="Grandpa is frugal, so he fixes his old shoes instead of buying new ones."),
        wrong_use=WrongUse(sentence="The frugal boy bought ten new games he never played.",
                           why="Frugal people avoid spending money on things they do not need."),
        image_scene="a smiling kid dropping coins into a piggy bank next to a patched backpack",
        emoji_scene="🐷💰🎒",
    )


def _q(qtype: str, prompt: str, choices: list[str], answer_index: int, explanation: str) -> dict:
    return {"type": qtype, "prompt": prompt, "choices": choices, "answer_index": answer_index,
            "accepted_answers": [], "explanation": explanation}


# Six valid questions (3 tier-1, 3 tier-2): exactly the readiness minimum shape.
BATCH_A: list[dict] = [
    _q("meaning", "What does frugal mean?",
       ["very loud and noisy", "careful not to waste money", "afraid of the dark", "quick to get angry"], 1,
       "Frugal people are careful with money."),
    _q("pick_word", "Which word means careful not to waste money?",
       ["noisy", "timid", "frugal", "grumpy"], 2, "Frugal means careful not to waste money."),
    _q("fill_blank", "To save for a bike, Leo stayed ___ and skipped buying snacks.",
       ["frugal", "noisy", "timid", "grumpy"], 0, "Skipping snacks to save money is frugal."),
    _q("scenario", "Which kid is being frugal?",
       ["Ben buys three copies of one game.", "Cal throws away a phone that still works.",
        "Dee spends all her savings on candy.", "Ana packs lunch to save her allowance."], 3,
       "Packing lunch saves money, so Ana is frugal."),
    _q("synonym", "Which word is closest in meaning to frugal?",
       ["generous", "thrifty", "sleepy", "brave"], 1, "Thrifty also means careful with money."),
    _q("antonym", "Which word means the opposite of frugal?",
       ["careful", "quiet", "wasteful", "early"], 2, "Wasteful is the opposite of frugal."),
]

# Six more valid questions with different prompts, spread over all three tiers (used for top-ups).
# The spell_it cues already end with the first-letter hint validation adds, so stored prompts equal these.
BATCH_B: list[dict] = [
    _q("meaning", "Which meaning fits the word frugal best?",
       ["spending money with care", "jumping very high", "talking too fast", "feeling very sleepy"], 0,
       "Frugal means spending money with care."),
    _q("pick_word", "Which word describes someone who never wastes money?",
       ["rowdy", "frugal", "clumsy", "gloomy"], 1, "Someone who never wastes money is frugal."),
    _q("scenario", "Which plan for a party is frugal?",
       ["Rent a huge castle for one hour.", "Make decorations from old magazines.",
        "Buy a new outfit for every guest.", "Order ten cakes for five people."], 1,
       "Making decorations from old magazines saves money."),
    _q("synonym", "Pick the word that means almost the same as frugal.",
       ["economical", "careless", "noisy", "proud"], 0, "Economical also means not wasting money."),
    {"type": "spell_it",
     "prompt": 'Mom stayed ___ and bought the cheaper shoes. (means: careful with money; starts with "f")',
     "choices": [], "answer_index": -1, "accepted_answers": ["frugal"],
     "explanation": "Choosing the cheaper shoes is frugal."},
    {"type": "spell_it",
     "prompt": 'The ___ campers reused every plastic bag. (means: careful not to waste; starts with "f")',
     "choices": [], "answer_index": -1, "accepted_answers": ["frugal"],
     "explanation": "Reusing bags instead of buying new ones is frugal."},
]


def batch(items: Sequence[dict]) -> dict:
    """A question_batch model response."""
    return {"questions": [dict(item) for item in items]}


def check_all_match(card: LearnCard, items: Sequence[dict], *, word: str = WORD, band: str = BAND,
                    existing_prompts: Sequence[str] = ()) -> dict:
    """An answer_check response that agrees with every question: only the key passes, every choice is tempting,
    no spell_it alternatives (qids q1..qn over the kept questions)."""
    raws = [RawQuestion.model_validate(item) for item in items]
    kept, drops = validate_questions(word, band, card, raws, existing_prompts=list(existing_prompts))
    assert not drops, f"fixture questions must pass validation: {drops}"
    return {"results": [
        {"qid": f"q{i}",
         "passes": [] if raw.type == "spell_it" else [j == raw.answer_index for j in range(len(raw.choices))],
         "tempting": [] if raw.type == "spell_it" else [True] * len(raw.choices),
         "chosen_index": raw.answer_index,
         "fill": raw.accepted_answers[0] if raw.type == "spell_it" else "",
         "alternatives": [], "ambiguous": False, "reason": "matches"}
        for i, raw in enumerate(kept, start=1)
    ]}


def stored_questions(n: int, *, word: str = WORD, band: str = BAND, version: int = 1,
                     types: Sequence[str] = ("meaning", "pick_word", "synonym")) -> list[Question]:
    """n verified questions ready to store directly (types cycle; the default 6 meet the pool minimum)."""
    out: list[Question] = []
    for i in range(n):
        qtype = types[i % len(types)]
        spell = qtype == "spell_it"
        out.append(Question(
            id=new_id(), word=word, band=band, content_version=version, type=qtype, tier=TIER[qtype],
            prompt=f"Practice question {i + 1} about {word}?",
            choices=[] if spell else ["first", "second", "third", "fourth"],
            answer_index=-1 if spell else 0, accepted_answers=[word] if spell else [],
            explanation="", source="ai", verified=True, created_at=clock.utc_now_iso(),
        ))
    return out


def save_ready_content(repo: Repository, *, word: str = WORD, band: str = BAND, version: int = 1,
                       n_questions: int = 6, card: LearnCard | None = None) -> WordContent:
    """Store ready content (card + n verified questions) as if generation had finished."""
    content = WordContent(word=word, band=band, status="ready", content_version=version,
                          card=card or make_card(), generated_at=clock.utc_now_iso())
    repo.save_content(content)
    if n_questions:
        repo.add_questions(band, word, version, stored_questions(n_questions, word=word, band=band, version=version))
    return content

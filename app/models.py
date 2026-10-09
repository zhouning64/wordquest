"""Domain models shared by storage, AI, learning and API layers (spec §6.1; plan Appendix A §5)."""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

Band = Literal["3-5", "6-8", "9-12"]
BANDS: tuple[str, ...] = ("3-5", "6-8", "9-12")

QType = Literal[
    "meaning", "pick_word", "fill_blank", "usage", "scenario", "synonym", "antonym", "spell_it", "word_parts"
]
QTYPES: tuple[str, ...] = (
    "meaning", "pick_word", "fill_blank", "usage", "scenario", "synonym", "antonym", "spell_it", "word_parts"
)
TIER: dict[str, int] = {
    "meaning": 1, "pick_word": 1, "fill_blank": 1,
    "usage": 2, "scenario": 2, "synonym": 2, "antonym": 2,
    "spell_it": 3, "word_parts": 3,
}
CHOICE_TYPES: frozenset[str] = frozenset(t for t in QTYPES if t != "spell_it")
BAND_MAX_WORDS: dict[str, int] = {"3-5": 15, "6-8": 20, "9-12": 28}
POOL_CAP = 40
DEFAULT_EMOJI_SCENE = "📖✨"

JobKind = Literal["learn", "questions", "image", "topup"]
EventKind = Literal["answer", "unsure", "check_answer", "check_result", "intro_seen", "learn_open"]


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------- profiles and lists ----------

class ProfileSettings(BaseModel):
    session_minutes: int = Field(15, ge=5, le=30)
    new_words_per_session: int = Field(5, ge=0, le=30)
    break_reminder: bool = True
    break_message: str = "Take a 10-minute break — look at something far away."


class Profile(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=30)
    avatar: str = "🙂"
    band: Band
    list_ids: list[str] = Field(default_factory=list)
    settings: ProfileSettings = Field(default_factory=ProfileSettings)
    created_at: str = ""


class WordList(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=60)
    words: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


# ---------- content ----------

class Sense(BaseModel):
    pos: str
    definition: str
    example: str


class RightUse(BaseModel):
    sentence: str = ""


class WrongUse(BaseModel):
    sentence: str = ""
    why: str = ""


class LearnCard(BaseModel):
    pos: str
    forms: list[str] = Field(default_factory=list)
    short_def: str
    kid_def: str
    senses: list[Sense]
    examples: list[str]
    word_parts: str = ""
    memory_hook: str = ""
    synonyms: list[str] = Field(default_factory=list)
    antonyms: list[str] = Field(default_factory=list)
    right_use: RightUse = Field(default_factory=RightUse)
    wrong_use: WrongUse = Field(default_factory=WrongUse)
    image_scene: str = ""
    emoji_scene: str = DEFAULT_EMOJI_SCENE


class WordContent(BaseModel):
    word: str
    band: Band
    status: Literal["pending", "ready", "failed"] = "pending"
    error: str = ""
    source: Literal["ai", "legacy"] = "ai"
    content_version: int = 1
    card: LearnCard | None = None
    draft: LearnCard | None = None
    draft_version: int | None = None
    image_key: str | None = None
    image_status: Literal["none", "pending", "ready", "failed"] = "none"
    model: str = ""
    generated_at: str = ""

    @property
    def key(self) -> str:
        return f"{self.band}:{self.word}"


class Question(BaseModel):
    id: str
    word: str
    band: Band
    content_version: int
    type: QType
    tier: int
    prompt: str
    choices: list[str] = Field(default_factory=list)
    answer_index: int = -1
    accepted_answers: list[str] = Field(default_factory=list)
    explanation: str = ""
    source: Literal["ai", "legacy"] = "ai"
    verified: bool = False
    created_at: str = ""

    def public(self) -> dict:
        """What the browser receives for a question (includes the answer key for instant feedback)."""
        return {
            "id": self.id,
            "type": self.type,
            "tier": self.tier,
            "prompt": self.prompt,
            "choices": list(self.choices),
            "answer_index": self.answer_index,
            "accepted_answers": list(self.accepted_answers),
            "explanation": self.explanation,
        }


# ---------- learning state ----------

class WordProgress(BaseModel):
    profile_id: str
    word: str
    stage: int = 0
    due_date: str | None = None
    interval_days: int = 0
    introduced_on: str | None = None
    last_graded_on: str | None = None
    seen: int = 0
    correct: int = 0
    wrong: int = 0
    unsure: int = 0
    seen_question_ids: list[str] = Field(default_factory=list)
    updated_at: str = ""

    @property
    def key(self) -> str:
        return f"{self.profile_id}:{self.word}"


class Session(BaseModel):
    id: str
    profile_id: str
    mode: Literal["normal", "practice"]
    local_date: str
    started_at: str
    finished_at: str | None = None
    planned_minutes: int
    active_minutes: int = 0
    answered: int = 0
    correct: int = 0
    unsure: int = 0
    words: list[str] = Field(default_factory=list)  # words in this session's queue
    new_words: list[str] = Field(default_factory=list)
    stars_up: list[str] = Field(default_factory=list)
    missed: list[str] = Field(default_factory=list)
    learn_opened: int = 0
    checks_passed: int = 0
    checks_failed: int = 0


class EventIn(BaseModel):
    """An event as the browser sends it."""

    client_event_id: str = Field(min_length=8, max_length=64)
    word: str
    question_id: str | None = None
    question_type: str | None = None
    kind: EventKind
    correct: bool | None = None
    check_set: int | None = None
    correct_count: int | None = None
    passed: bool | None = None
    ms: int = 0
    local_date: str
    at: str


class AnswerEvent(EventIn):
    session_id: str
    profile_id: str


# ---------- background jobs ----------

class Job(BaseModel):
    kind: JobKind
    band: Band
    word: str
    target_version: int
    chain: list[str] = Field(default_factory=list)  # remaining kinds to enqueue, in order, after success
    status: Literal["pending", "running", "done", "failed"] = "pending"
    attempts: int = 0
    last_error: str = ""
    not_before: str = ""
    lease_until: str = ""
    lease_token: str = ""  # set by claim_next_job; finish/fail/defer only apply with the matching token
    created_at: str = ""
    updated_at: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.band}:{self.word}"

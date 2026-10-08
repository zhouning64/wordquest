from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from app.ai.inflect import contains_word, is_form_of, tokenize, valid_extra_forms, word_forms
from app.ai.safety import find_blocked
from app.ai.schemas import RawQuestion
from app.learning.grading import normalize_answer
from app.models import BAND_MAX_WORDS, CHOICE_TYPES, DEFAULT_EMOJI_SCENE, LearnCard

# Limits from spec §7.5 / §6.1.
SHORT_DEF_MAX = 90
KID_DEF_MAX = 220
MAX_SENSES = 3
MIN_EXAMPLES = 4
LEGACY_MIN_EXAMPLES = 1  # legacy cards are exempt from the 4-example minimum but must keep at least one
MAX_EXAMPLES = 6
MAX_RELATED = 5  # synonyms / antonyms
EMOJI_MAX_CODEPOINTS = 40
EXPLANATION_MAX = 160
LENGTH_TOLERANCE = 5  # sentences may exceed the band's max words by this much

_SENTENCE_SPLIT = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][\"'”’]))\s+")
_NON_WORD = re.compile(r"[^\w\s]")
_BLANK_RUN = re.compile(r"_{3,}")  # a fill-in blank: any run of 3+ underscores


def sentence_word_count(s: str) -> int:
    """Words in one sentence, counted with the same tokenizer the word matcher uses.

    Task 2's tokenize() treats "_" as a separator (so "___" alone yields no token); each blank is swapped
    for a placeholder word first, so a blank counts as one word.
    """
    return len(tokenize(_BLANK_RUN.sub(" blank ", s)))


def _sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_SPLIT.split(text.strip()) if part.strip()]


def _longest_over(text: str, limit: int) -> int | None:
    """Word count of the first sentence in text that is longer than limit, else None."""
    for sentence in _sentences(text):
        n = sentence_word_count(sentence)
        if n > limit:
            return n
    return None


def _norm(text: str) -> str:
    """Comparison key: case-folded, punctuation removed, whitespace collapsed."""
    text = text.casefold().replace("’", "'").replace("‘", "'")
    return " ".join(_NON_WORD.sub(" ", text).split())


# ---------------------------------------------------------------------------------------------
# Learn card (L1–L7)
# ---------------------------------------------------------------------------------------------
@dataclass
class CardCheck:
    card: LearnCard | None
    errors: list[str]


def _clean_related(items: list[str], word: str, extra: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = " ".join(item.split())
        if not text or is_form_of(text, word, extra) or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append(text)
    return out[:MAX_RELATED]


def _emoji_ok(scene: str) -> bool:
    scene = scene.strip()
    return (
        bool(scene)
        and len(scene) <= EMOJI_MAX_CODEPOINTS
        and not any(ch.isalpha() or ch.isdigit() for ch in scene)
    )


def _card_sentences(card: LearnCard) -> list[tuple[str, str]]:
    out = [(f"senses[{i}].example", s.example) for i, s in enumerate(card.senses)]
    out += [(f"examples[{i}]", e) for i, e in enumerate(card.examples)]
    out += [("right_use.sentence", card.right_use.sentence), ("wrong_use.sentence", card.wrong_use.sentence)]
    return [(label, text) for label, text in out if text.strip()]


def _card_texts(card: LearnCard) -> list[tuple[str, str]]:
    out = [("pos", card.pos), ("short_def", card.short_def), ("kid_def", card.kid_def)]
    out += [(f"forms[{i}]", f) for i, f in enumerate(card.forms)]
    for i, s in enumerate(card.senses):
        out += [(f"senses[{i}].pos", s.pos), (f"senses[{i}].definition", s.definition), (f"senses[{i}].example", s.example)]
    out += [(f"examples[{i}]", e) for i, e in enumerate(card.examples)]
    out += [("word_parts", card.word_parts), ("memory_hook", card.memory_hook)]
    out += [(f"synonyms[{i}]", s) for i, s in enumerate(card.synonyms)]
    out += [(f"antonyms[{i}]", a) for i, a in enumerate(card.antonyms)]
    out += [
        ("right_use.sentence", card.right_use.sentence),
        ("wrong_use.sentence", card.wrong_use.sentence),
        ("wrong_use.why", card.wrong_use.why),
        ("image_scene", card.image_scene),
    ]
    return [(label, text) for label, text in out if text.strip()]


def validate_card(word: str, band: str, card: LearnCard, *, legacy: bool = False) -> CardCheck:
    """Apply L1–L7. Returns a cleaned copy (examples filtered to ≤6 that use the word, synonyms/antonyms
    without the word and truncated to 5, default emoji scene if needed) or card=None with every error."""
    errors: list[str] = []
    extra = valid_extra_forms(word, card.forms)

    def uses_word(text: str) -> bool:
        return bool(text.strip()) and contains_word(text, word, extra)

    # L1 definitions
    for field, limit in (("short_def", SHORT_DEF_MAX), ("kid_def", KID_DEF_MAX)):
        value = getattr(card, field).strip()
        if not value:
            errors.append(f"L1: {field} is empty")
        elif len(value) > limit:
            errors.append(f"L1: {field} is {len(value)} characters (max {limit})")

    # L2 senses and examples
    if not 1 <= len(card.senses) <= MAX_SENSES:
        errors.append(f"L2: {len(card.senses)} senses (need 1 to {MAX_SENSES})")
    for i, sense in enumerate(card.senses):
        if not uses_word(sense.example):
            errors.append(f"L2: senses[{i}].example does not use the word")
    examples = [e.strip() for e in card.examples if uses_word(e)][:MAX_EXAMPLES]
    need = LEGACY_MIN_EXAMPLES if legacy else MIN_EXAMPLES
    if len(examples) < need:
        errors.append(f"L2: only {len(examples)} examples use the word (need {need})")

    # L3 synonyms / antonyms (cleaned, never a reason to reject)
    synonyms = _clean_related(card.synonyms, word, extra)
    antonyms = _clean_related(card.antonyms, word, extra)

    # L4 right / wrong use (legacy cards have none)
    if not legacy:
        if not uses_word(card.right_use.sentence):
            errors.append("L4: right_use.sentence does not use the word")
        if not uses_word(card.wrong_use.sentence):
            errors.append("L4: wrong_use.sentence does not use the word")
        if not card.wrong_use.why.strip():
            errors.append("L4: wrong_use.why is empty")

    # L7 emoji scene (substitute, never reject)
    emoji = card.emoji_scene.strip() if _emoji_ok(card.emoji_scene) else DEFAULT_EMOJI_SCENE

    cleaned = card.model_copy(
        deep=True,
        update={"examples": examples, "synonyms": synonyms, "antonyms": antonyms, "emoji_scene": emoji},
    )

    # L5 sentence length
    limit = BAND_MAX_WORDS[band] + LENGTH_TOLERANCE
    for label, text in _card_sentences(cleaned):
        n = _longest_over(text, limit)
        if n is not None:
            errors.append(f"L5: {label} has a {n}-word sentence (max {limit})")

    # L6 blocklist
    for label, text in _card_texts(cleaned):
        term = find_blocked(text)
        if term is not None:
            errors.append(f"L6: blocked term {term!r} in {label}")

    if errors:
        return CardCheck(card=None, errors=errors)
    return CardCheck(card=cleaned, errors=[])

# ---------------------------------------------------------------------------------------------
# Questions (Q1–Q10)
# ---------------------------------------------------------------------------------------------
@dataclass
class Drop:
    index: int
    reason: str


_CUE_AT_END = re.compile(r"\(\s*means\s*:[^()]*[^\s()][^()]*\)\s*\.?\s*$", re.IGNORECASE)
# The same closing cue split into its meaning and any letter hint after it ("starts with f", "it starts with f",
# "the word begins with f", "first letter: f", "first letter is f").
_CUE_PARTS = re.compile(
    r"\(\s*means\s*:(?P<meaning>[^()]*?)"
    r"(?:[;,.:\-–—]\s*"
    r"(?:(?:(?:it|the\s+(?:word|answer))\s+)?(?:starts|begins)\s+with|(?:the\s+)?first\s+letter)\b[^()]*)?"
    r"\)\s*\.?\s*$",
    re.IGNORECASE,
)
# An explanation that points at a choice by position: "the first sentence", "the last one", "option B",
# "Answer: B", "choice 2", "B is correct", "(A)", "(a)", "A)".
_POSITION_REF = re.compile(
    r"(?i:\bthe\s+(?:first|second|third|fourth|last)\s+"
    r"(?:sentence|choice|option|answer|one\b(?!\s+(?:to|who|that)\b)))"
    r"|(?i:\b(?:option|choice|answer|sentence))(?:\s*:\s*|\s+)(?:[A-D]|[1-4])\b"
    r"|\b[A-D]\s+is\s+(?i:correct|right|the\s+(?:best\s+)?answer)\b"
    r"|\(?\b[A-D]\)|(?i:\([a-d]\))"
)
# inflect.tokenize's token pattern with the case kept (typographic apostrophes and hyphens included).
_RAW_TOKEN = re.compile(r"[^\W_]+(?:['’‘ʼ\-‐‑][^\W_]+)*")


def _strip_cue(prompt: str) -> str:
    return _CUE_AT_END.sub("", prompt).strip()


def _without_hint(prompt: str) -> str:
    """The prompt with any "starts with ..." hint removed from its closing cue."""
    m = _CUE_PARTS.search(prompt)
    return f"{prompt[: m.start()]}(means:{m.group('meaning')})" if m else prompt


def _add_letter_hint(prompt: str, answer: str) -> str:
    """End the cue with the answer's first letter (and word count for a phrase), replacing any hint the
    model wrote: `(means: X; starts with "f")`. A cue with no meaning becomes "(means: )", which Q2 rejects."""
    m = _CUE_PARTS.search(prompt)
    if m is None:
        return prompt
    meaning = m.group("meaning").strip().rstrip(".").rstrip()
    words = len(answer.split())
    hint = f'starts with "{answer[0]}"' + (f", {words} words" if words > 1 else "")
    cue = f"(means: {meaning}; {hint})" if meaning else "(means: )"
    return f"{prompt[: m.start()].rstrip()} {cue}"


def _tidy(q: RawQuestion, *, legacy: bool = False) -> RawQuestion:
    """Trim whitespace, normalize any run of 3+ underscores to "___", normalize spell_it answers, and
    (AI questions only) add the first-letter hint to the spell_it cue so the blind check sees it."""
    update: dict = {
        "prompt": _BLANK_RUN.sub("___", q.prompt.strip()),
        "choices": [c.strip() for c in q.choices],
        "explanation": q.explanation.strip(),
    }
    if q.type == "spell_it":
        answers: list[str] = []
        for a in q.accepted_answers:
            norm = normalize_answer(a)
            if norm and norm not in answers:
                answers.append(norm)
        update["accepted_answers"] = answers
        if answers and not legacy:
            update["prompt"] = _add_letter_hint(update["prompt"], answers[0])
    return q.model_copy(update=update)


def _case_ok(token: str) -> bool:
    """lowercase, ALL CAPS, or only the first letter capitalized (sentence start)."""
    return token in (token.lower(), token.upper(), token[:1].upper() + token[1:].lower())


def _misspells_word(text: str, forms: set[str]) -> bool:
    """True if text writes the word (or a form) with a hyphen added or dropped, a space dropped, or capitals
    inside it ("ephem-eral", "ephemerAl", "selfesteem", "inlieu of"). An added space is not checked, so two
    separate words ("every day") never count as a misspelling of one ("everyday")."""
    squashed: dict[str, int] = {}  # form without hyphens/spaces -> its number of tokens
    for form in forms:
        key = form.replace("-", "").replace(" ", "")
        squashed[key] = max(squashed.get(key, 0), form.count(" ") + 1)
    raw = _RAW_TOKEN.findall(text)
    for size in range(1, max(squashed.values(), default=0) + 1):
        for i in range(len(raw) - size + 1):
            window = raw[i : i + size]
            norm = " ".join(tokenize(" ".join(window)))
            if squashed.get(norm.replace("-", "").replace(" ", ""), 0) < size:
                continue
            if norm not in forms or not all(_case_ok(t) for t in window):
                return True
    return False


@dataclass
class _Ctx:
    word: str
    band: str
    card: LearnCard
    extra: list[str]
    fills: list[str]
    learn: set[str]
    legacy: bool


def _problem(q: RawQuestion, ctx: _Ctx) -> str | None:
    word, extra, card = ctx.word, ctx.extra, ctx.card

    # Q1 shape
    if not q.prompt:
        return "Q1: empty prompt"
    if q.type in CHOICE_TYPES:
        if len(q.choices) != 4:
            return f"Q1: {len(q.choices)} choices (need exactly 4)"
        if any(not c for c in q.choices):
            return "Q1: empty choice"
        if len({c.casefold() for c in q.choices}) != 4:
            return "Q1: choices are not distinct"
        if not 0 <= q.answer_index <= 3:
            return f"Q1: answer_index {q.answer_index} is not 0-3"
        if q.accepted_answers:
            return "Q1: accepted_answers must be empty for a choice question"
    else:
        if q.choices:
            return "Q1: spell_it must have no choices"
        if q.answer_index != -1:
            return "Q1: spell_it answer_index must be -1"

    # Q2 blanks and cue
    if q.type in ("fill_blank", "spell_it"):
        blanks = q.prompt.count("___")
        if blanks != 1:
            return f"Q2: prompt has {blanks} blanks (need exactly 1)"
        if contains_word(_without_hint(q.prompt).replace("___", " "), word, extra):
            return "Q2: prompt contains the word outside the blank"
        if q.type == "spell_it" and not _CUE_AT_END.search(q.prompt):
            return 'Q2: spell_it prompt must end with a "(means: ...)" cue'

    # Q3 typed answers
    if q.type == "spell_it":
        if not q.accepted_answers:
            return "Q3: spell_it has no accepted_answers"
        for answer in q.accepted_answers:
            if not is_form_of(answer, word, extra):
                return f"Q3: accepted answer {answer!r} is not the word or a form of it"

    # Q4 type-specific answer keys
    if q.type in CHOICE_TYPES:
        correct = q.choices[q.answer_index]
        distractors = [c for i, c in enumerate(q.choices) if i != q.answer_index]
        if q.type in ("pick_word", "fill_blank"):
            if not is_form_of(correct, word, extra):
                return "Q4: the correct choice is not the word or a form of it"
            if any(is_form_of(d, word, extra) for d in distractors):
                return "Q4: a distractor is also a form of the word"
        elif q.type == "meaning":
            if _norm(correct) in {_norm(d) for d in distractors}:
                return "Q4: the correct definition is identical to a distractor"
        elif q.type == "usage":
            if not all(contains_word(c, word, extra) for c in q.choices):
                return "Q4: every usage choice must contain the word"
        elif q.type == "synonym":
            if _norm(correct) not in {_norm(s) for s in card.synonyms}:
                return "Q4: the correct choice is not one of the card's synonyms"
        elif q.type == "antonym":
            if _norm(correct) not in {_norm(a) for a in card.antonyms}:
                return "Q4: the correct choice is not one of the card's antonyms"
        elif q.type == "word_parts":
            if not card.word_parts.strip():
                return "Q4: word_parts question but the card has no word parts"

    # Q5 no reuse of Learn-card sentences (legacy exempt)
    if not ctx.legacy:
        for text in [_strip_cue(q.prompt), *q.choices]:
            variants = [text.replace("___", f) for f in ctx.fills] if "___" in text else [text]
            if any(_norm(v) in ctx.learn for v in variants):
                return "Q5: reuses a Learn-card sentence"

    # Q6 lengths
    limit = BAND_MAX_WORDS[ctx.band] + LENGTH_TOLERANCE
    for label, text in [("prompt", _strip_cue(q.prompt))] + [(f"choice {i + 1}", c) for i, c in enumerate(q.choices)]:
        n = _longest_over(text, limit)
        if n is not None:
            return f"Q6: {label} has a {n}-word sentence (max {limit})"
    if len(q.explanation) > EXPLANATION_MAX:
        return f"Q6: explanation is {len(q.explanation)} characters (max {EXPLANATION_MAX})"

    # Q7 blocklist
    for text in [q.prompt, *q.choices, *q.accepted_answers, q.explanation]:
        term = find_blocked(text)
        if term is not None:
            return f"Q7: blocked term {term!r}"

    # Q9 / Q10 (legacy exempt)
    if not ctx.legacy:
        if _POSITION_REF.search(q.explanation):
            return "Q9: explanation refers to a choice position"
        forms = set(ctx.fills)
        if any(_misspells_word(text, forms) for text in [q.prompt, *q.choices]):
            return "Q10: the word is misspelled"

    return None


def validate_questions(
    word: str,
    band: str,
    card: LearnCard,
    raw: list[RawQuestion],
    *,
    existing_prompts: Sequence[str] = (),
    legacy: bool = False,
) -> tuple[list[RawQuestion], list[Drop]]:
    """Apply Q1–Q10 to each question independently. Returns (kept in original order, drops)."""
    extra = valid_extra_forms(word, card.forms)
    learn_sentences = [s.example for s in card.senses] + list(card.examples)
    learn_sentences += [card.right_use.sentence, card.wrong_use.sentence]
    ctx = _Ctx(
        word=word,
        band=band,
        card=card,
        extra=extra,
        fills=sorted(word_forms(word) | set(extra) | {word}),
        learn={_norm(s) for s in learn_sentences if s.strip()},
        legacy=legacy,
    )
    seen_prompts = {_norm(_without_hint(p)) for p in existing_prompts if p.strip()}  # old prompts may lack a hint
    kept: list[RawQuestion] = []
    drops: list[Drop] = []
    for index, original in enumerate(raw):
        q = _tidy(original, legacy=legacy)
        reason = _problem(q, ctx)
        key = _norm(_without_hint(q.prompt))
        if reason is None and key in seen_prompts:
            reason = "Q8: repeats an existing question prompt"
        if reason is not None:
            drops.append(Drop(index=index, reason=reason))
            continue
        seen_prompts.add(key)
        kept.append(q)
    return kept, drops

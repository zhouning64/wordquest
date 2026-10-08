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
# AI text: typographic hyphens (U+2010, U+2011) become "-" and soft hyphens (U+00AD) are removed.
_HYPHEN_FIX = str.maketrans({"\u2010": "-", "\u2011": "-", "\u00ad": None})
# Function words that never count as content words (Q5 near-copies) or as typeable cue words (Q13).
_STOPWORDS = frozenset(
    """
    a about above across after again against all along also am among an and any anyone anybody anything are
    around as at be because been before behind being below beneath beside besides between beyond both but by can
    cannot could did do does doing done down during each either else even ever every everyone everybody
    everything except few for from had has have having he her here hers herself him himself his how i if in
    inside into is it its itself just least less let like lot lots many me more most much must my myself near
    neither never no nobody none nor not nothing now of off on once one ones only onto or other others our ours
    out outside over own quite rather really same she should since so some somebody someone something such than
    that the their theirs them then there these they this those though through to too toward towards under
    until up upon us very was we were what when where which while who whom whose why will with within without
    would yet you your yours
    """.split()
)


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


def _fix_hyphens(value: object) -> object:
    """value with _HYPHEN_FIX applied to every string inside it (str, list or dict)."""
    if isinstance(value, str):
        return value.translate(_HYPHEN_FIX)
    if isinstance(value, list):
        return [_fix_hyphens(v) for v in value]
    if isinstance(value, dict):
        return {k: _fix_hyphens(v) for k, v in value.items()}
    return value


def _clean_forms(word: str, forms: list[str]) -> list[str]:
    """L8: for a one-word target, drop a several-word form whose last word does not share the word's first 3
    letters ("most epoxy" for ephemeral; "more ephemeral" stays). One-word forms and phrase targets are kept."""
    base = tokenize(word)
    if len(base) != 1:
        return list(forms)
    prefix = base[0][:3]
    out: list[str] = []
    for form in forms:
        tokens = tokenize(form)
        if len(tokens) > 1 and not tokens[-1].startswith(prefix):
            continue
        out.append(form)
    return out


def _named_parts(word_parts: str) -> list[str]:
    """The text before each top-level "(" in word_parts, back to the previous "+" or ")": "frug- (fruit) + -al (like)
    = careful" names "frug- " and " -al ". Nothing after a top-level "=" (the combined meaning) is a part."""
    parts: list[str] = []
    current, depth = "", 0
    for ch in word_parts:
        if ch == "(":
            if depth == 0:
                parts.append(current)
            depth += 1
        elif ch == ")":
            depth, current = max(0, depth - 1), ""
        elif depth == 0:
            if ch == "=":
                break
            current = "" if ch == "+" else current + ch
    return parts


def _clean_word_parts(word: str, word_parts: str) -> str:
    """L10: "" for a phrase target, or when a part named in word_parts is not visible in the word's spelling
    (hyphens removed, case-insensitive, quotes ignored; "a/b" names alternatives, one of which must show)."""
    if not word_parts.strip():
        return word_parts
    if len(word.split()) > 1:
        return ""
    spelling = word.replace("-", "").casefold()
    for part in _named_parts(word_parts):
        options = [o.replace("-", "").strip().strip("\"'“”‘’").casefold() for o in part.split("/")]
        if not any(o in spelling for o in options):
            return ""
    return word_parts


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
    """Apply L1–L10. Returns a cleaned copy (examples filtered to ≤6 new sentences that use the word,
    synonyms/antonyms without the word and truncated to 5, default emoji scene if needed; for AI cards also
    typographic hyphens normalized, stray several-word forms dropped, and word_parts emptied for a phrase or a part
    not visible in the word) or card=None with every error."""
    errors: list[str] = []
    if not legacy:
        card = LearnCard.model_validate(_fix_hyphens(card.model_dump()))
    # L8 forms (cleaned, never a reason to reject; legacy forms are generated by code)
    forms = list(card.forms) if legacy else _clean_forms(word, card.forms)
    extra = valid_extra_forms(word, forms)

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
    # L9 an example that repeats a sense example, right_use or wrong_use is dropped (legacy reuses examples[0])
    repeats = set() if legacy else {
        _norm(t) for t in [*(s.example for s in card.senses), card.right_use.sentence, card.wrong_use.sentence]
        if t.strip()
    }
    examples = [e.strip() for e in card.examples if uses_word(e) and _norm(e) not in repeats][:MAX_EXAMPLES]
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

    # L10 word parts (cleaned, never a reason to reject; legacy notes are kept as written)
    word_parts = card.word_parts if legacy else _clean_word_parts(word, card.word_parts)

    cleaned = card.model_copy(
        deep=True,
        update={"forms": forms, "examples": examples, "synonyms": synonyms, "antonyms": antonyms,
                "emoji_scene": emoji, "word_parts": word_parts},
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
# Questions (Q1–Q13)
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
# An explanation that points at a choice by position: "the first sentence", "the second definition",
# "the last one", "option B", "Answer: B", "choice 2", "sentence 0", "B is correct", "(A)", "(a)", "A)".
_POSITION_REF = re.compile(
    r"(?i:\bthe\s+(?:first|second|third|fourth|last)\s+"
    r"(?:sentence|choice|option|answer|definition|one\b(?!\s+(?:to|who|that)\b)))"
    r"|(?i:\b(?:option|choice|answer|sentence|definition))(?:\s*:\s*|\s+)(?:[A-D]|[0-4])\b"
    r"|\b[A-D]\s+is\s+(?i:correct|right|the\s+(?:best\s+)?answer)\b"
    r"|\(?\b[A-D]\)|(?i:\([a-d]\))"
)
# A positional reference that _tidy can rewrite as "the correct sentence|choice|option|answer|definition" ("Only
# the first sentence shows…", "Sentence 1 uses…", "only sentence C") — not one that points at a wrong choice.
_POSITION_FIXABLE = re.compile(
    r"(?:(?i:\bthe\s+(?:first|second|third|fourth|last)\s+"
    r"(?P<ordinal>sentence|choice|option|answer|definition)\b)"
    r"|(?i:\b(?:the\s+)?(?P<numbered>sentence|choice|option|definition))\s+(?:[0-4]|[A-D])\b)"
    r"(?!\s+(?i:is|was)\s+(?i:wrong|incorrect|not)\b)"
)
# A negation in the sentence that names the choice ("The first sentence does not use…", "Not the first
# choice…"): rewriting it as "the correct sentence" would make the explanation false, so it is left for Q9.
_NEGATION = re.compile(r"(?i:\b(?:not|never|cannot)\b|n['’]t\b)")
# Where the negation scan after the reference stops: the sentence end, or "because", ":" or "," (what follows is
# about the scene, as in "The first sentence is right because Ava does not waste food."). A "," or ":" right after
# the reference opens an inserted phrase ("Sentence 2, however, does not…"), so then only _SENTENCE_STOP ends it.
_SCAN_STOP = re.compile(r"[.!?,:]|(?i:\bbecause\b)")
_SENTENCE_STOP = re.compile(r"[.!?]|(?i:\bbecause\b)")
# An ordinal used like a pronoun elsewhere in the explanation ("…, but the second does not", "not the last.") still
# points at a choice: it is followed by "one", a verb, a conjunction, punctuation or the end. "the last of her pay",
# "the first to save" and "the second time" are about the scene.
_ORDINAL_PRONOUN = re.compile(
    r"(?i:\bthe\s+(?:first|second|third|fourth|last)\b(?=\s*(?:$|[^\w\s]|(?:"
    r"ones?|and|or|but|is|was|are|were|does|did|do|has|have|had|can|could|would|will|won|should|may|might|must|"
    r"cannot|shows?|uses?|means?|fits?|says?|describes?|match(?:es)?|gives?|tells?|talks?|names?|needs?|puts?|"
    r"makes?|misuses?)(?:n['’]t)?\b)))"
)
_LEADING_NOT = re.compile(r"\s*Not\b")
# A spell_it prompt ending in a parenthetical cue without the "means:" label ("(makes trouble smaller)").
_BARE_CUE = re.compile(r"\((?!\s*means\s*:)\s*(?P<body>[^()_]*[^\s()_])\s*\)\s*\.?\s*$", re.IGNORECASE)
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


def _the_correct(m: re.Match[str]) -> str:
    before = m.string[: m.start()].rstrip()
    article = "The" if not before or before[-1] in ".!?" else "the"
    return f"{article} correct {(m.group('ordinal') or m.group('numbered')).lower()}"


def _negated(text: str, start: int, end: int) -> bool:
    """True if text starts with "Not" or the sentence holding text[start:end] has a negation before the next
    "because", ":" or "," (or the sentence end); a "," or ":" right after the reference does not end the scan."""
    if _LEADING_NOT.match(text):
        return True
    begin = max(text.rfind(mark, 0, start) for mark in ".!?") + 1
    stop = _SCAN_STOP.search(text, end)
    if stop and stop.group() in ",:" and not text[end : stop.start()].strip():
        stop = _SENTENCE_STOP.search(text, stop.end())
    return bool(_NEGATION.search(text[begin : stop.start() if stop else len(text)]))


def _repair_position(explanation: str) -> str:
    """Rewrite an explanation's one reference to a choice by position as "the correct sentence" (etc.). Left
    unchanged, for Q9 to reject, when there are several references (an ordinal used like a pronoun, "the second
    does not", counts), it points at a wrong choice, it is negated, or the result would be too long."""
    if sum(1 for _ in _POSITION_REF.finditer(explanation)) != 1:
        return explanation
    m = _POSITION_FIXABLE.search(explanation)
    if m is None or _negated(explanation, m.start(), m.end()):
        return explanation
    if _ORDINAL_PRONOUN.search(explanation[: m.start()] + " " + explanation[m.end() :]):
        return explanation
    fixed = explanation[: m.start()] + _the_correct(m) + explanation[m.end() :]
    return fixed if len(fixed) <= EXPLANATION_MAX else explanation


def _tidy(q: RawQuestion, *, legacy: bool = False) -> RawQuestion:
    """Trim whitespace, normalize any run of 3+ underscores to "___", normalize spell_it answers. AI questions
    (not legacy) also get typographic hyphens normalized and are repaired where it is safe: a positional
    explanation, a spell_it cue missing "means:", and the first-letter hint added to the spell_it cue so the
    blind check sees it."""
    if not legacy:
        q = q.model_copy(update={
            "prompt": q.prompt.translate(_HYPHEN_FIX),
            "choices": [c.translate(_HYPHEN_FIX) for c in q.choices],
            "accepted_answers": [a.translate(_HYPHEN_FIX) for a in q.accepted_answers],
            "explanation": q.explanation.translate(_HYPHEN_FIX),
        })
    update: dict = {
        "prompt": _BLANK_RUN.sub("___", q.prompt.strip()),
        "choices": [c.strip() for c in q.choices],
        "explanation": q.explanation.strip(),
    }
    if not legacy:
        update["explanation"] = _repair_position(update["explanation"])
    if q.type == "spell_it":
        answers: list[str] = []
        for a in q.accepted_answers:
            norm = normalize_answer(a)
            if norm and norm not in answers:
                answers.append(norm)
        update["accepted_answers"] = answers
        if not legacy:
            prompt = _BARE_CUE.sub(lambda m: f"(means: {m.group('body')})", update["prompt"])
            update["prompt"] = _add_letter_hint(prompt, answers[0]) if answers else prompt
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


# Q5 near-copies: a prompt or sentence choice that shares with a Learn-card sentence (or the kid_def) a first name
# and 3+ other content words, or 60%+ of its own content words (when it has at least 4; Learn-card sentences only,
# since a fill_blank clue states the meaning, so overlap with the kid_def is expected).
_NEAR_COPY_NAME_WORDS = 3
_NEAR_COPY_PERCENT = 60
_NEAR_COPY_MIN_WORDS = 4
_SENTENCE_CHOICES = ("usage", "scenario")  # types whose choices are sentences or situations
_GIVEAWAY_TYPES = ("meaning", "scenario", "synonym", "antonym", "word_parts")  # Q12
_DISTRACTOR_TYPES = ("synonym", "antonym", "pick_word")  # Q11


def _content_words(text: str, skip: set[str]) -> set[str]:
    """Case-folded tokens of 3+ letters, without stopwords or the target word's own tokens (skip)."""
    return {t for t in tokenize(text) if len(t) >= 3 and t not in _STOPWORDS and t not in skip}


def _names(text: str, skip: set[str]) -> set[str]:
    """Case-folded capitalised words (first names, in practice), without stopwords or the word's tokens."""
    out: set[str] = set()
    for raw in _RAW_TOKEN.findall(text):
        if raw[:1].isupper():
            out.update(t for t in tokenize(raw) if len(t) >= 2 and t not in _STOPWORDS and t not in skip)
    return out


def _fold(text: str) -> str:
    return " ".join(text.casefold().split())


@dataclass
class _Ctx:
    word: str
    band: str
    card: LearnCard
    extra: list[str]
    fills: list[str]
    learn: set[str]
    legacy: bool
    word_tokens: set[str]
    # (content words, names, whether the 60% rule applies) of each Learn-card sentence and the kid_def
    learn_words: list[tuple[set[str], set[str], bool]]


def _near_copy(text: str, ctx: _Ctx) -> bool:
    words = _content_words(text, ctx.word_tokens)
    names = _names(text, ctx.word_tokens)
    for learn_words, learn_names, percent_rule in ctx.learn_words:
        shared = words & learn_words
        shared_names = names & learn_names
        if shared_names and len(shared - shared_names) >= _NEAR_COPY_NAME_WORDS:
            return True
        if percent_rule and len(words) >= _NEAR_COPY_MIN_WORDS and 100 * len(shared) >= _NEAR_COPY_PERCENT * len(words):
            return True
    return False


def _cue_word_with_letter(q: RawQuestion) -> str | None:
    """Q13: a word of 4+ letters (not a stopword) in the spell_it cue meaning that starts with the answer's
    first letter, so a learner could type it instead ("(means: great size)" for gigantic)."""
    m = _CUE_PARTS.search(q.prompt)
    if m is None or not q.accepted_answers:
        return None
    letter = q.accepted_answers[0][:1]
    for token in tokenize(m.group("meaning")):
        if len(token) >= 4 and token.startswith(letter) and token not in _STOPWORDS:
            return token
    return None


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

    # Q5 no reuse of Learn-card sentences, not even a near-copy (legacy exempt)
    if not ctx.legacy:
        prompt = _strip_cue(q.prompt)
        for text in [prompt, *q.choices]:
            variants = [text.replace("___", f) for f in ctx.fills] if "___" in text else [text]
            if any(_norm(v) in ctx.learn for v in variants):
                return "Q5: reuses a Learn-card sentence"
        # definitions (a pick_word prompt, meaning and word_parts choices) are meant to echo the card's meaning
        sentences = [] if q.type == "pick_word" else [prompt]
        sentences += q.choices if q.type in _SENTENCE_CHOICES else []
        if any(_near_copy(text, ctx) for text in sentences):
            return "Q5: near-copy of a Learn-card sentence"

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

    # Q11 / Q12 / Q13 (legacy exempt)
    if not ctx.legacy:
        if q.type in _DISTRACTOR_TYPES:
            distractors = [c for i, c in enumerate(q.choices) if i != q.answer_index]
            if any(is_form_of(d, word, extra) for d in distractors):
                return "Q11: a wrong choice is the word or a form of it"
            synonyms = {_fold(s) for s in card.synonyms}
            if any(_fold(d) in synonyms for d in distractors):
                return "Q11: a wrong choice is one of the card's synonyms"
        if q.type in _GIVEAWAY_TYPES and any(contains_word(c, word, extra) for c in q.choices):
            return "Q12: a choice contains the word"
        if q.type == "spell_it":
            cue_word = _cue_word_with_letter(q)
            if cue_word is not None:
                return f"Q13: the cue word {cue_word!r} starts with the answer's first letter"

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
    """Apply Q1–Q13 to each question independently. Returns (kept in original order, drops)."""
    extra = valid_extra_forms(word, card.forms)
    learn_sentences = [s.example for s in card.senses] + list(card.examples)
    learn_sentences += [card.right_use.sentence, card.wrong_use.sentence]
    fills = sorted(word_forms(word) | set(extra) | {word})
    word_tokens = {t for f in fills for t in tokenize(f)}
    ctx = _Ctx(
        word=word,
        band=band,
        card=card,
        extra=extra,
        fills=fills,
        learn={_norm(s) for s in learn_sentences if s.strip()},
        legacy=legacy,
        word_tokens=word_tokens,
        learn_words=[
            (_content_words(s, word_tokens), _names(s, word_tokens), percent_rule)
            for s, percent_rule in [*((s, True) for s in learn_sentences), (card.kid_def, False)] if s.strip()
        ],
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

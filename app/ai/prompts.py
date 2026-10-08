from __future__ import annotations

import json

from app.models import BAND_MAX_WORDS, QTYPES, LearnCard

# ---------------------------------------------------------------------------------------------
# Band style guide (spec §7.6) — included in every generation prompt.
# ---------------------------------------------------------------------------------------------
_BAND_FACTS: dict[str, tuple[str, str, str]] = {
    "3-5": (
        "grades 3-5 (about ages 8 to 11)",
        "school, pets, the playground, family, cartoons",
        "very plain, everyday words. Never use a word that is harder than the target word itself.",
    ),
    "6-8": (
        "grades 6-8 (about ages 11 to 14)",
        "sports, gaming, science class, friendships, chores",
        "plain words. You may use one technical term if you explain it right away.",
    ),
    "9-12": (
        "grades 9-12 (about ages 14 to 18)",
        "history, literature, current events, jobs, debate",
        "precise, SAT-style definitions that are still clear on the first read.",
    ),
}

BAND_GUIDE: dict[str, str] = {
    band: (
        f"Learner band {band}: {grades}.\n"
        f"- Sentence length: every sentence you write has at most {BAND_MAX_WORDS[band]} words.\n"
        f"- Settings for examples and questions: {settings}. Rotate through them so sentences feel different.\n"
        f"- Definition style: {style}"
    )
    for band, (grades, settings, style) in _BAND_FACTS.items()
}

_SAFETY = """Safety and tone (always):
- Everything is for children: kind, encouraging, and school-appropriate.
- No violence, weapons, injuries, death, crime, scary or gross content, romance or dating, alcohol, drugs, gambling, or bad words.
- No real living people, celebrities, brand names, or products; no politics or religion; no stereotypes about any group. History examples may mention well-known events in a neutral way.
- Use a varied mix of everyday first names (for example Maya, Leo, Aisha, Ben, Priya, Diego, Kenji, Zoe)."""

_JSON_ONLY = "Reply with one JSON object that matches the provided schema. No markdown, no commentary."


# ---------------------------------------------------------------------------------------------
# 1. Learn card
# ---------------------------------------------------------------------------------------------
_LEARN_SYSTEM = f"""You are the writer for WordQuest, a vocabulary app for kids. For one English word you write a Learn card that helps a child understand the word deeply and remember it for a long time. Write like a great teacher talking to one curious student: warm, concrete, and accurate.

{_SAFETY}

Fill every field of the schema. Use "" or [] when a field does not apply.
{_JSON_ONLY}"""


def learn_card_prompt(word: str, band: str) -> tuple[str, str]:
    limit = BAND_MAX_WORDS[band]
    user = f"""Word: "{word}"

{BAND_GUIDE[band]}

Write the Learn card for "{word}". Field by field:
- pos: the part(s) of speech used in your senses, e.g. "adjective" or "noun / verb".
- forms: every other form of "{word}" a reader might meet (plurals, verb tenses, -ing forms, comparatives, adverbs), including irregular ones, e.g. strive -> strives, strove, striven, striving; mouse -> mice. For a phrase, inflect the phrase, e.g. give up -> gives up, gave up, given up, giving up. Lowercase. Do not repeat "{word}" itself. Use [] if there are no other forms.
- short_def: the core meaning in at most 90 characters, kid-friendly, without using "{word}" or its forms.
- kid_def: a fuller explanation in at most 220 characters: what it means and when or why people use it.
- senses: 1 to 3 different meanings that a learner in this band is likely to meet, most common first. If the word has one common meaning, give exactly one sense; never pad with rare meanings. Each sense has its own pos, a plain "definition", and an "example" sentence that uses "{word}" (or one of its forms) in that meaning.
- examples: 4 to 6 new sentences. Each one uses "{word}" or one of its forms and is set in a different setting from the band list, so the meaning is clear from the context alone. Vary the shape: statements, a question, a line of dialogue. If the word has several forms, use more than one.
- memory_hook: one short, vivid trick for remembering the meaning: a sound-alike, a funny picture, or a mini story that links the sound of the word to its meaning. One or two short sentences.
- word_parts: only if the word's parts truly help a kid remember the meaning. Name each part with its meaning and show how they combine, e.g. "bene- (good) + vol (wish) = wishing someone good". If the parts are not meaningful for a kid, use "". Never invent an etymology.
- synonyms: 0 to 5 words or short phrases with nearly the same meaning that a kid in this band would know, closest first. Never "{word}" or one of its forms. Use [] if nothing fits well.
- antonyms: 0 to 5 clear opposites, same rules. Use [] if the word has no clear opposite.
- right_use.sentence: one more new sentence that uses "{word}" correctly in a way that shows what it really means.
- wrong_use.sentence: a sentence that uses "{word}" the way kids commonly get it wrong (mixing it up with a similar-looking word, or stretching the meaning too far). wrong_use.why: one short sentence that says what is wrong and what would be right.
- image_scene: one concrete, kid-safe scene an illustrator could draw that shows the meaning at a glance: who is there, where they are, and what is happening, in 1 or 2 sentences. The picture must not contain any text, letters, numbers, signs, or speech bubbles, and no real people, famous characters, or brands.
- emoji_scene: 3 to 6 emoji that together hint at the meaning, like a tiny picture story. Emoji only: no letters, digits, or words.

Rules:
- Never reuse a sentence: the sense examples, the examples, right_use, and wrong_use are all different sentences.
- Every sentence has at most {limit} words.
- Spell "{word}" correctly every time."""
    return _LEARN_SYSTEM, user


# ---------------------------------------------------------------------------------------------
# 2. Question batch
# ---------------------------------------------------------------------------------------------
_QUESTION_SYSTEM = f"""You are the quiz writer for WordQuest, a vocabulary app for kids. You write questions that check whether a learner really understands one word. Every question has exactly one defensible answer: a student who knows the word gets it right, and a student who does not cannot guess it from clues in the wording.

{_SAFETY}

Question types (use exactly these "type" values):
- meaning: ask what the word means, e.g. 'What does "frugal" mean?' or 'In "The frugal team reused old jerseys," what does "frugal" mean?'. The 4 choices are short definitions; exactly one is correct.
- pick_word: give a definition (without the word) and ask which word fits it. The 4 choices are single words of the same part of speech; the correct one is the word itself.
- fill_blank: one new sentence with exactly one ___ (three underscores) where the word goes. The word must not appear anywhere else in the sentence. The 4 choices all fit the grammar of the blank; the correct one is the word (or the form of it that fits); no other choice may be any form of the word.
- usage: ask 'Which sentence uses "<word>" correctly?'. The 4 choices are full sentences and every choice contains the word (or a form of it). Exactly one uses it correctly; the others misuse its meaning in clearly wrong ways.
- scenario: a situation question, e.g. 'Which kid is being frugal?', or a short situation followed by a question about it. The 4 choices are short situations or reactions; exactly one fits the meaning.
- synonym: ask which choice is closest in meaning to the word. The correct choice is copied exactly from the card's synonyms; the other choices are not synonyms.
- antonym: ask which choice is most nearly the opposite of the word. The correct choice is copied exactly from the card's antonyms; the other choices are not opposites.
- spell_it: a typing question. One new sentence with exactly one ___ where the word goes (the word must not appear anywhere else), ending with a short definition cue in parentheses written exactly like this: (means: careful with money). The cue must be specific enough that this word, and no other, is clearly the answer. choices = [], answer_index = -1, accepted_answers = the exact form(s) that fit the blank, lowercase (usually just one).
- word_parts: ask what one part named in the card's word parts means, e.g. 'In "benevolent", the part "bene-" means...'. The 4 choices are short meanings; the correct one matches the card's word parts.

Field rules:
- Every type except spell_it: exactly 4 different choices, answer_index = the position (0 to 3) of the correct choice, accepted_answers = []. Spread the correct position across 0, 1, 2 and 3.
- Distractors are plausible for this band (same part of speech, similar length and style) but clearly wrong for anyone who knows the word. Never use "all of the above", "none of the above", or joke answers.
- explanation: at most 160 characters, kid-friendly, says why the correct answer is right.
- Test only the meanings listed in the card's senses.
- Never copy a sentence from the Learn card, not even with the word blanked out. Every sentence you write is new.
- Do not repeat or closely reword any existing question.

Write exactly the requested number of questions of each type, in any order.
{_JSON_ONLY}"""


def _bullets(items: list[str], empty: str = "(none)") -> str:
    clean = [i for i in items if i.strip()]
    if not clean:
        return f"- {empty}"
    return "\n".join(f"- {i}" for i in clean)


def question_batch_prompt(
    word: str, band: str, card: LearnCard, mix: dict[str, int], existing_prompts: list[str]
) -> tuple[str, str]:
    unknown = sorted(set(mix) - set(QTYPES))
    if unknown:
        raise ValueError(f"unknown question types in mix: {unknown}")
    wanted = [(t, int(mix.get(t, 0))) for t in QTYPES if int(mix.get(t, 0)) > 0]
    total = sum(n for _, n in wanted)
    senses = "\n".join(
        f"  {i}. ({s.pos}) {s.definition}" for i, s in enumerate(card.senses, start=1)
    ) or "  (none)"
    learn_sentences = (
        [s.example for s in card.senses] + list(card.examples) + [card.right_use.sentence, card.wrong_use.sentence]
    )
    mix_lines = "\n".join(f"- {t}: {n}" for t, n in wanted)
    user = f"""Word: "{word}"

{BAND_GUIDE[band]}

The learner has studied this Learn card:
- Part of speech: {card.pos}
- Forms: {", ".join(card.forms) if card.forms else "(none)"}
- Senses (test only these):
{senses}
- Synonyms (correct answers for synonym questions come from here): {", ".join(card.synonyms) if card.synonyms else "(none)"}
- Antonyms (correct answers for antonym questions come from here): {", ".join(card.antonyms) if card.antonyms else "(none)"}
- Word parts (word_parts questions ask about a part named here): {card.word_parts or "(none)"}

Learn-card sentences. Never reuse these, not even with the word blanked out:
{_bullets(learn_sentences)}

Write exactly {total} questions:
{mix_lines}

Existing questions. Do not repeat or closely reword these:
{_bullets(list(existing_prompts), empty="(none yet)")}

Remember: every sentence has at most {BAND_MAX_WORDS[band]} words, every explanation at most 160 characters."""
    return _QUESTION_SYSTEM, user


# ---------------------------------------------------------------------------------------------
# 3. Blind answer-key check
# ---------------------------------------------------------------------------------------------
_CHECK_SYSTEM = f"""You are a careful, strong student taking a vocabulary quiz. Solve every question yourself, independently, using only the question text and its choices. Then say whether the question allows exactly one good answer.

For each question:
- Multiple choice (it has 4 choices): chosen_index = the 0-based position of the one best choice (0 = first choice), fill = "".
- Typing question (type spell_it, no choices): chosen_index = -1, fill = the single word you would type in the ___, in the exact form that fits the sentence, lowercase.
- ambiguous = true if more than one choice could reasonably be defended, if no choice is right, if the question is confusing, or (typing) if a different word would fit the blank and the hint just as well. Otherwise ambiguous = false.
- reason: one short sentence on why you chose your answer, or what the problem is.

Return {{"results": [...]}} with exactly one result for every qid, using each qid exactly as given.
{_JSON_ONLY}"""

_CHECK_KEYS = ("qid", "type", "prompt", "choices")


def check_prompt(items: list[dict]) -> tuple[str, str]:
    lines = []
    for item in items:
        shown = {k: item.get(k, [] if k == "choices" else "") for k in _CHECK_KEYS}
        lines.append(json.dumps(shown, ensure_ascii=False))
    user = "Questions (one JSON object per line):\n" + "\n".join(lines)
    return _CHECK_SYSTEM, user

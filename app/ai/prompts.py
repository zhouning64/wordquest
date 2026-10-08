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
        "very plain, everyday words. Never use a word that is harder than the target word itself; this applies to "
        "everything you write: definitions, synonyms, antonyms, choices and explanations.",
    ),
    "6-8": (
        "grades 6-8 (about ages 11 to 14)",
        "sports, video games (puzzles, building, racing, levels; no battles, monsters or weapons), "
        "science class (safe lab habits only), friendships, chores",
        "plain words. You may use one technical term if you explain it right away.",
    ),
    "9-12": (
        "grades 9-12 (about ages 14 to 18)",
        "history, literature, current events, jobs, debate",
        'precise, SAT-style definitions that are still clear on the first read. Learners are teens: say "student" '
        'or "person", never "kid". Every factual claim must be true; never invent thoughts or deeds of real '
        "historical people (use an invented person in a historical setting).",
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
- Science scenes show safe habits (goggles, asking the teacher); never cloth near flames or a student handling a chemical spill alone.
- For feelings and character words, show effort, kindness and bouncing back from mistakes, not only winning.
- Use a varied mix of everyday first names (for example Maya, Leo, Aisha, Ben, Priya, Diego, Kenji, Zoe)."""

_JSON_ONLY = "Reply with one JSON object that matches the provided schema. No markdown, no commentary."
_NO_COPY = "Never copy the examples in these instructions into your content."


# ---------------------------------------------------------------------------------------------
# 1. Learn card
# ---------------------------------------------------------------------------------------------
_LEARN_SYSTEM = f"""You are the writer for WordQuest, a vocabulary app for kids. For one English word you write a Learn card that helps a child understand the word deeply and remember it for a long time. Write like a great teacher talking to one curious student: warm, concrete, and accurate.

{_SAFETY}

Fill every field of the schema. Use "" or [] when a field does not apply.
{_NO_COPY}
{_JSON_ONLY}"""


def learn_card_prompt(word: str, band: str) -> tuple[str, str]:
    limit = BAND_MAX_WORDS[band]
    roots = "" if band == "3-5" else (
        " or a well-known Latin, Greek or French root (mitigate: mitis (mild) + agere (to make); "
        "ephemeral: epi + hemera (day) = lasting a day; in lieu of: lieu (French: place) = in place of)"
    )
    user = f"""Word: "{word}"

{BAND_GUIDE[band]}

Write the Learn card for "{word}". Field by field:
- pos: the part(s) of speech used in your senses, e.g. "adjective" or "noun / verb".
- forms: every other form of "{word}" a reader might meet (plurals, verb tenses, -ing forms, comparatives, adverbs), including irregular ones, e.g. strive -> strives, strove, striven, striving; mouse -> mice. For a phrase, inflect the phrase, e.g. give up -> gives up, gave up, given up, giving up. Lowercase. Do not repeat "{word}" itself. Use [] if there are no other forms.
- short_def: the core meaning in at most 90 characters, kid-friendly, without using "{word}" or its forms.
- kid_def: a fuller explanation in at most 220 characters: what it means and when or why people use it. For a feeling or character word, describe the whole range, not only the good end (self-esteem: how you feel about yourself and your worth; it can be high or low).
- senses: 1 to 3 different meanings that a learner in this band is likely to meet, most common first. Include a second meaning when learners in this band meet it in books (curious = strange), but never pad with rare meanings. Every part of speech used in forms or in any example needs its own sense (no "braved" in an example when only the adjective is listed). Each sense has its own pos, a plain "definition", and an "example" sentence that uses "{word}" (or one of its forms) in that meaning.
- examples: 4 to 6 new sentences. Each one uses "{word}" or one of its forms and is set in a different setting from the band list, so the meaning is clear from the context alone. Vary the shape: statements, a question, a line of dialogue. Use another form only when it is common and natural, with a listed meaning, and grammatical (never "felt ephemerally", or "most curious" with nothing compared). Show the setting through details, not a label ("In the novel,", "During the Renaissance,"). Every sentence must make sense in the real world (no "a candid photo of bubbles", no hugging someone you cannot meet).
- memory_hook: one or two short sentences that make the meaning stick. Best: a true root from word_parts or a familiar word hidden inside it (gigantic has "giant"), turned into a picture; otherwise a picture or mini story of the meaning. Use a sound-alike only if it really sounds like part of the word said aloud (same stressed syllable and vowel sounds: "lieu" sounds like "loo", not "leaf" or "lie"; pragmatic is stressed on "MAT", gigantic on "GAN"). Never say the word sounds like itself. If unsure, use a picture story.
- word_parts: give the parts whenever a real root, prefix, base word or familiar hidden word points to the meaning: a hidden familiar word (gigantic: giant + -ic = like a giant){roots}. Name each part with its meaning and show how they combine. Use "" only if no such part exists or you are not certain it is real. Never invent an etymology.
- synonyms: 0 to 5 words or short phrases with nearly the same meaning that a learner in this band already uses, easiest first (3-5 learners say "huge", "scared", "unwilling", not "inquisitive", "loath", "apathetic"). Same part of speech as "{word}". Never "{word}", one of its forms, or an entry containing it ("low self-esteem"). Use [] if nothing fits well.
- antonyms: 0 to 5 true opposites, same rules (not "steady" for ephemeral). Use [] if the word has no clear opposite.
- right_use.sentence: one more new sentence that uses "{word}" correctly and contains the clue that shows the meaning (for brave: the scary or hard thing being faced). It must make sense in the real world.
- wrong_use.sentence: a misuse a real student in this band might make: a well-known mix-up ("in lieu of" used to mean "because of"), a look-alike word (candid / candied, mitigate / militate), or "{word}" used for a related but different idea; never a random pairing ("the brave cake"). It must be wrong in meaning, not only in grammar, under EVERY dictionary meaning and idiom of "{word}", including ones not listed ("a brave outfit" = bold is correct English; "tenacious" can mean sticky). wrong_use.why: one short, true sentence that says what is wrong and what would be right; it agrees with the rest of the card and never claims a synonym works where "{word}" does not.
- image_scene: one kid-safe moment an illustrator can draw in a single picture (no change over time) that shows the meaning at a glance: who is there, where they are, and what is happening, in 1 or 2 sentences. The picture must not contain any text, letters, numbers, signs, symbols, or speech bubbles, and no real people, famous characters, or brands.
- emoji_scene: 3 to 6 emoji that together hint at the meaning, like a tiny picture story. Emoji only: no letters, digits, or words.

Rules:
- Never reuse a sentence: the sense examples, the examples, right_use, and wrong_use are all different sentences.
- Every sentence has at most {limit} words.
- Spell "{word}" correctly every time, in the memory_hook too: never split it or respell it with letters changed or missing."""
    return _LEARN_SYSTEM, user


# ---------------------------------------------------------------------------------------------
# 2. Question batch
# ---------------------------------------------------------------------------------------------
_QUESTION_SYSTEM = f"""You are the quiz writer for WordQuest, a vocabulary app for kids. You write questions that check whether a learner really understands one word. Every question has exactly one defensible answer: a student who knows the word gets it right, and a student who does not cannot guess it from clues in the wording.

{_SAFETY}

Question types (use exactly these "type" values):
- meaning: ask what the word means, e.g. 'What does "frugal" mean?' or 'In "The frugal team reused old jerseys," what does "frugal" mean?'. The 4 choices are short definitions of similar length and style (the right one must not be the only long one); exactly one is correct.
- pick_word: give a definition (without the word) and ask which word fits it. The 4 choices are single words of the same part of speech; the correct one is the word itself. No wrong choice may also fit the definition: never the card's synonyms or near-synonyms ("pride" for self-esteem, "instead" for in lieu of). For a phrase, the choices are phrases of the same kind.
- fill_blank: one new sentence with exactly one ___ (three underscores) where the word goes, and a clue that only the word satisfies (e.g. 'Maya was ___ to join the club, so she waited by the door until a friend pulled her in'). The word must not appear anywhere else in the sentence. The 4 choices all fit the grammar of the blank; the correct one is the word (or the form of it that fits); no other choice may be any form of the word. Put each wrong choice into the blank: if it makes a true, sensible sentence, change the sentence or the choice. Never use the card's synonyms or a near-synonym as a wrong choice; use an opposite only if the clue clearly rules it out. With the answer the sentence is grammatical: an adjective goes before a noun or after is/was/felt/seemed, never in a verb or noun slot.
- usage: ask 'Which sentence uses "<word>" correctly?'. The 4 choices are grammatical sentences and every choice contains the word (or a form of it). Exactly one uses it correctly; each other choice gives the word a meaning it never has (e.g. 'The frugal paint was shiny') and stays wrong under every sense, including ones not on the card. A correct use in an odd situation is NOT a misuse ("The curious dog ran fast" is correct). For a replacement word like "in lieu of", wrong choices pair things that cannot stand in for each other, never two swappable activities.
- scenario: if the word describes a person's behaviour or feeling, ask which person fits ('Which person is being frugal?'); if it describes things, size, time, or an action on something, ask which thing or action fits ('Which of these would you call gigantic?', 'Which step would best mitigate the flood damage?'). The 4 choices are short situations; exactly one shows a listed sense plainly and literally (no figurative stretch like "a gigantic effort") and it has no giveaway synonym that the wrong choices lack. Use the word in natural English (never "being gigantic", "a mitigating attitude", "an ephemeral habit").
- synonym: ask which choice is closest in meaning to the word. The correct choice is copied exactly from the card's synonyms (the one easiest for the band); the other choices are not synonyms, not weaker forms of the same idea (for gigantic: not "large"), and not related words.
- antonym: ask which choice is most nearly the opposite of the word. The correct choice is copied exactly from the card's antonyms; the other choices are neither opposites nor synonyms of the word, and not synonyms of each other (no odd one out like massive / tiny / enormous / colossal).
- spell_it: a typing question. One new sentence with exactly one ___ where the word goes (the word must not appear anywhere else); the sentence itself shows the meaning (e.g. 'Maya was ___ with her allowance and saved half of it every week'). End with a cue in parentheses written like this: (means: careful with money). The cue is at most 6 plain words in your own words, not the card's definition, and never contains a synonym or family word of the answer (no "courage" for brave, "reduce" for mitigate, "instead of" for in lieu of, "practical" for pragmatic). Each spell_it question gets a different cue. The app adds the answer's first letter (and, for a phrase, the number of words) to the cue itself, so never write that hint yourself. The sentence, the cue and that first letter together must fit this word and no other: if a synonym with the same first letter would also fit, change the sentence. choices = [], answer_index = -1, accepted_answers = the form the blank's grammar needs, lowercase ('She answered ___.' needs "candidly", not "candid").
- word_parts: ask what one part named in the card's word parts means, e.g. 'In "benevolent", the part "bene-" means...'. The 4 choices are short meanings; the correct one matches the card's word parts.

Field rules:
- Every type except spell_it: exactly 4 different choices, answer_index = the position (0 to 3) of the correct choice, accepted_answers = []. Spread the correct position across 0, 1, 2 and 3.
- Distractors are plausible for this band (same part of speech, similar length and style) but clearly wrong for anyone who knows the word. Never use "all of the above", "none of the above", or joke answers.
- explanation: at most 160 characters, friendly and plain; says why the answer is right by naming or quoting it. Choices are shuffled before the learner sees them, so never refer to a letter or position ("A", "C", "the first sentence").
- Ask only about the card's senses, but make sure no wrong choice is a correct use of any other meaning of the word.
- Say "kid" only for band 3-5; otherwise say "student" or "person".
- Never copy a sentence from the Learn card, not even with the word blanked out, and never reuse a Learn-card situation with new names or a few words changed (if the card has Priya covering a spill with a cloth, no question is about a spill). Every sentence you write is new.
- Vary situations: no two questions in one batch share a scene.
- Do not repeat or closely reword any existing question.
- {_NO_COPY}

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

Learn-card sentences. Never reuse these or their situations, not even with the word blanked out or new names:
{_bullets(learn_sentences)}

Write exactly {total} questions:
{mix_lines}

Existing questions. Do not repeat or closely reword these:
{_bullets(list(existing_prompts), empty="(none yet)")}

Remember: every sentence has at most {BAND_MAX_WORDS[band]} words, every explanation at most 160 characters, and "{word}" is spelled correctly every time, with no added hyphens, spaces or capital letters inside it."""
    return _QUESTION_SYSTEM, user


# ---------------------------------------------------------------------------------------------
# 3. Blind answer-key check
# ---------------------------------------------------------------------------------------------
_CHECK_SYSTEM = f"""You are a careful, strong student taking a vocabulary quiz. Solve every question yourself, independently, using only the question text and its choices. Then say whether the question allows exactly one good answer.

For each question:
- Multiple choice (it has 4 choices): chosen_index = the 0-based position of the one best choice (0 = first choice), fill = "".
- Typing question (type spell_it, no choices): chosen_index = -1, fill = what you would type in the ___ (one word, or as many words as the hint says), in the exact form that fits the sentence, lowercase.
- Before deciding, put EACH choice into the blank (or test each sentence against the question) and ask whether a teacher would mark it right. Do not prefer a choice because it is the hardest or most "vocabulary-like" word.
- Typing questions: the hint ends with the answer's first letter (and, for a phrase, the number of words). First try the words inside the hint and their common synonyms that start with that letter.
- ambiguous = true if two or more choices pass; if no choice is right, or no choice makes a grammatical, sensible sentence; if the question is confusing; or (typing) if another word fits the blank and the hint as well as your answer. Otherwise ambiguous = false.
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

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
        "history, literature, science, jobs, school debate club (school or science topics; no elections, voting, "
        "protests or political causes)",
        'precise, SAT-style definitions that are still clear on the first read. Learners are teens: say "student" '
        'or "person", never "kid". Never open a sentence with a time or place phrase. Every factual claim must be '
        "true; mention real events with no death tolls or disasters. Never invent thoughts or deeds of real "
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
- Science scenes show safe habits (goggles, asking the teacher); no unsafe handling of materials or heat.
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
    parts = "prefix, suffix, base word or familiar hidden word" if band == "3-5" else (
        "prefix, suffix, base word, familiar hidden word, or Latin, Greek or French root"
    )
    user = f"""Word: "{word}"

{BAND_GUIDE[band]}

Write the Learn card for "{word}". Field by field:
- pos: the part(s) of speech used in your senses, e.g. "adjective" or "noun / verb".
- forms: the inflections and derived words of the senses you give (plurals, verb tenses, -ing forms, comparatives, adverbs), including irregular ones, e.g. strive -> strives, strove, striven, striving; mouse -> mice. For a phrase, inflect the phrase, e.g. give up -> gives up, gave up, given up, giving up. Every form is a real, correctly spelled dictionary word. Lowercase. Do not repeat "{word}" itself. Use [] if there are no other forms.
- short_def: the core meaning in at most 90 characters, kid-friendly, without using "{word}" or its forms.
- kid_def: a fuller explanation in at most 220 characters: what it means and when or why people use it. For a feeling or character word, describe the whole range, not only the good end.
- senses: 1 to 3 meanings of "{word}" itself that a learner in this band is likely to meet, most common first; only senses found in a standard learner's dictionary (never an invented noun use of an adjective). Derived words with another suffix (-ly, -ness, -ity, -ion, -ment, -ance, -ence, -ery) share the sense they come from and never get a sense or part of speech of their own; a sense gets a part of speech only when "{word}" itself, or a simple inflection of it (-s, -ed, -ing), is used that way. Include a second meaning when learners in this band meet it in books, but never a rare or technical sense, and never one that is the main sense without its key idea. Each sense has its own pos, a plain "definition", and an "example" sentence that uses "{word}" (or one of its forms) in that meaning.
- examples: 4 to 6 new sentences. Each one uses "{word}" or one of its forms and is set in a different setting from the band list, so the meaning is clear from the context alone. Vary the shape: statements, a question, a line of dialogue. Use another form only when it is common, natural and grammatical, with a listed meaning. Show the setting through details, not a label. Every sentence must make sense in the real world.
- memory_hook: one or two short sentences that make the meaning stick. Best: a familiar word hidden inside it or a root, turned into a picture (use a root only if it is the one in word_parts); otherwise a picture or mini story of the meaning. Use a sound-alike only if it really sounds like part of the word said aloud (same stressed syllable and vowel sounds: "lieu" sounds like "loo", not "leaf" or "lie"; pragmatic is stressed on "MAT", gigantic on "GAN"). Never say the word sounds like itself. If unsure, use a picture story.
- word_parts: give the parts whenever a real {parts} points to the meaning, written as "<part> (<meaning>) + <part> (<meaning>) = <combined meaning>". Name the real source word and its dictionary meaning; never guess a root's meaning from a modern English word it resembles. If you cannot name the source word with certainty, use "". Use "" for a phrase; every part you name must be visible in the spelling of "{word}".
- synonyms: 0 to 5 words or short phrases with nearly the same meaning that a learner in this band already uses, easiest first (3-5 learners say "huge", "scared", "unwilling", not "inquisitive", "loath", "apathetic"). Same part of speech as "{word}". Never "{word}", one of its forms, or an entry containing it. Use [] if nothing fits well.
- antonyms: 0 to 5 true opposites, same rules. Use [] if the word has no clear opposite.
- right_use.sentence: one more new sentence that uses "{word}" correctly and contains the clue that shows the meaning. It must make sense in the real world.
- wrong_use.sentence: a sentence that contains "{word}" itself (or one of its forms), misused (X used where Y belongs). Pick one real word students confuse with "{word}" (a look-alike or a near-meaning word) and put "{word}" in the spot where that other word belongs, so the sentence would be correct if "{word}" were replaced by the other word. The other word does not appear in the sentence. "{word}" is wrong there in meaning under every dictionary meaning and idiom of "{word}" (for a phrase, every meaning of the whole phrase), including ones not listed; never make it wrong only because the subject is an object, animal or weather, or because the action fails. wrong_use.why: one short, true sentence that names the other word: "{word}" means …; this sentence needs "<other word>".
- image_scene: one kid-safe moment an illustrator can draw in a single picture (no change over time) that shows the meaning at a glance: who is there, where they are, and what is happening, in 1 or 2 sentences. The picture must not contain any text, letters, numbers, signs, symbols, or speech bubbles, and no real people, famous characters, or brands.
- emoji_scene: 3 to 6 emoji that together hint at the meaning, like a tiny picture story. Emoji only: no letters, digits, or words.

Rules:
- Never reuse a sentence: the sense examples, the examples, right_use, and wrong_use are all different sentences.
- Every sentence has at most {limit} words.
- Use "more/most {word}" only when two or more things are compared.
- Spell "{word}" correctly every time, in the memory_hook too: never split it or respell it with letters changed or missing."""
    return _LEARN_SYSTEM, user


# ---------------------------------------------------------------------------------------------
# 2. Question batch
# ---------------------------------------------------------------------------------------------
_QUESTION_SYSTEM = f"""You are the quiz writer for WordQuest, a vocabulary app for kids. You write questions that check whether a learner really understands one word. Every question has exactly one defensible answer: a student who knows the word gets it right, and a student who does not cannot guess it from clues in the wording.

{_SAFETY}

Question types (use exactly these "type" values; <word> stands for the target word):
- meaning: ask 'What does "<word>" mean?', or quote one new sentence that uses it and ask what it means there. The 4 choices are short definitions of similar length and style (the right one must not be the only long one); exactly one is correct.
- pick_word: give a definition (without the word) and ask which word fits it. The 4 choices are single words of the same part of speech; the correct one is the word itself. No wrong choice may also fit the definition: never a card synonym or near-synonym. For a phrase, the choices are phrases of the same kind.
- fill_blank: one new sentence with exactly one ___ (three underscores) where the word goes; the word appears nowhere else in it. Choose the three wrong choices first (they fit the grammar of the blank and are never a form of the word, a card synonym or a near-synonym), then write a clue that states the meaning in other plain words so that each wrong choice makes the sentence false or silly. Naming an activity is not a clue. Never use as a wrong choice a feeling, manner or action that could also describe the person or scene. The correct choice is the word (or the form of it that fits), and with it the sentence is grammatical: an adjective goes before a noun or after is/was/felt/seemed, a noun goes in a noun slot, a verb keeps its preposition.
- usage: ask 'Which sentence uses "<word>" correctly?'. The 4 choices are grammatical sentences and every choice contains the word (or a form of it). Exactly one uses it correctly; each other choice is a realistic misuse (the word put where a look-alike or near-meaning word belongs, never nonsense) and stays wrong under every sense, including ones not on the card. A correct use in an odd or ordinary situation is NOT a misuse. For a word about replacing something, wrong choices pair things that cannot stand in for each other.
- scenario: if the word describes a person's behaviour or feeling, ask which person fits; otherwise ask which thing, action or situation fits. Use the word in natural English: ask "Which of these would you call <word>?" only for a noun or adjective; for anything else ask which sentence or situation shows it. The 4 choices are short situations; exactly one shows a listed sense plainly and literally (no figurative stretch) without using the definition's words or a synonym; the wrong choices are situations in the same setting.
- synonym: ask which choice is closest in meaning to the word. The correct choice is copied exactly from the card's synonyms (the one easiest for the band); the other choices are not synonyms or weaker forms of the same idea.
- antonym: ask which choice is most nearly the opposite of the word. The correct choice is copied exactly from the card's antonyms; the other choices are neither opposites nor synonyms of the word, and not synonyms of each other. For synonym and antonym, the wrong choices are real words from the same topic, so the correct choice is never the odd one out.
- spell_it: a typing question. One new sentence with exactly one ___ where the word goes (the word appears nowhere else); the sentence itself shows the meaning. End with a cue in parentheses written like this: (means: <short meaning>). The cue is at most 6 plain words in your own words, not the card's definition, and never contains a synonym or family word of the answer. No word in the cue that has 4 or more letters may start with the answer's first letter. Each spell_it question gets a different cue. The app adds the answer's first letter (and, for a phrase, the number of words) to the cue itself, so never write that hint yourself. The sentence, the cue and that first letter together must fit this word and no other: if a synonym with the same first letter would also fit, change the sentence. choices = [], answer_index = -1, accepted_answers = the form the blank's grammar needs, lowercase (an adverb slot needs the adverb form).
- word_parts: ask what one part named in the card's word parts means: 'In "<word>", the part "<part>" means...'. The 4 choices are short meanings; the correct one matches the card's word parts.

Field rules:
- Every type except spell_it: exactly 4 different choices, answer_index = the position (0 to 3) of the correct choice, accepted_answers = []. Spread the correct position across 0, 1, 2 and 3.
- Wrong choices are plausible: they tempt a student who half-knows the word (same part of speech, length and style, same topic or situation as the correct choice, and words this band knows) but are clearly wrong for anyone who knows it; never nonsense, joke or obviously unrelated choices, "all of the above" or "none of the above".
- explanation: at most 160 characters, friendly and plain; says why the answer is right by naming or quoting it. Choices are shuffled before the learner sees them, so never refer to a letter or position ("A", "C", "the first sentence").
- Ask only about the card's senses, but make sure no wrong choice is a correct use of any other meaning of the word.
- Say "kid" only for band 3-5; otherwise say "student" or "person".
- Use "more/most <word>" only when two or more things are compared.
- Never copy a sentence from the Learn card, not even with the word blanked out, and never reuse a Learn-card situation with new names or a few words changed. Every sentence you write is new.
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
_CHECK_SYSTEM = f"""You are a careful, strong student taking a vocabulary quiz. Solve every question yourself, independently, using only the question text and its choices.

For each question:
- Multiple choice (it has 4 choices): test EACH choice on its own: put it into the blank, or test it against the question. passes = one true or false per choice, in order: true if a careful teacher would mark that choice right, even when another choice is better. A choice that is grammatical and true passes even if it is less precise. Do not prefer the hardest or most "vocabulary-like" word. chosen_index = the 0-based position of the best choice (0 = first choice), fill = "", alternatives = [].
- tempting = one true or false per choice, in order (rate every choice, including your answer): true if a learner in the stated grades who only half-knows the tested word could reasonably pick it (the same kind of word or situation, believable); false if it is nonsense, a joke or obviously unrelated.
- Typing question (type spell_it, no choices): passes = [], tempting = [], chosen_index = -1, fill = what you would type in the ___ (one word, or as many words as the hint says), in the exact form that fits the sentence, lowercase. The hint ends with the answer's first letter (and, for a phrase, the number of words). alternatives = every other word or form that fits the blank and the hint as well as your fill (try the words inside the hint and their synonyms first), or [] if there is none.
- ambiguous = true if the question is confusing, or if no choice (or no word) makes a grammatical, sensible sentence; otherwise false.
- reason: one short sentence on why you chose your answer, or what the problem is.

Return {{"results": [...]}} with exactly one result for every qid, using each qid exactly as given.
{_JSON_ONLY}"""

_CHECK_KEYS = ("qid", "type", "prompt", "choices")


def check_prompt(items: list[dict], band: str) -> tuple[str, str]:
    """The blind check: only what the learner sees, plus the learners' grade band (for `tempting`)."""
    lines = []
    for item in items:
        shown = {k: item.get(k, [] if k == "choices" else "") for k in _CHECK_KEYS}
        lines.append(json.dumps(shown, ensure_ascii=False))
    user = f"Learners are in grades {band}.\nQuestions (one JSON object per line):\n" + "\n".join(lines)
    return _CHECK_SYSTEM, user

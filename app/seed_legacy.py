from __future__ import annotations

# Import the legacy 24-word starter set (spec §13).
#
# CLI: `python -m app.seed_legacy` seeds DATA_DIR. app.main also calls seed_legacy() on first start.
# Line 181 of legacy/index.html is a ~950 KB `const IMAGES = {...};` line; it is parsed with json.loads, never printed.

import base64
import json
import random
import re
from pathlib import Path

from app import clock
from app.ai.images.process import image_key, to_webp
from app.ai.inflect import is_form_of, word_forms
from app.ai.schemas import RawQuestion
from app.ai.validate import validate_card, validate_questions
from app.config import Settings
from app.learning.grading import normalize_answer
from app.models import TIER, LearnCard, Question, Sense, WordContent, WordList, new_id
from app.storage import make_storage
from app.storage.base import BlobStore, Repository

STARTER_LIST_NAME = "Starter set"
LEGACY_BAND = "6-8"
LEGACY_VERSION = 1
DEFAULT_LEGACY_PATH = Path(__file__).resolve().parents[1] / "legacy" / "index.html"

_WORD_BANK_START = "const WORD_BANK = ["
_IMAGES_PREFIX = "const IMAGES = "
_ENTRY_KEY_RE = re.compile(r"(?<=[{,])(w|pos|def|full|ex|parts|syn|img):")
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_POSSESSIVE_RE = re.compile(r"['’]s$")


# ---------------- parsing ----------------

def parse_word_bank(html: str) -> list[dict]:
    """WORD_BANK entries ({w,pos,def,full,ex,parts,syn,img}) as dicts, in file order."""
    entries: list[dict] = []
    inside = False
    for line in html.splitlines():
        text = line.strip()
        if not inside:
            inside = text.startswith(_WORD_BANK_START)
            continue
        if text.startswith("];"):
            break
        if text.startswith("{w:"):
            entries.append(json.loads(_ENTRY_KEY_RE.sub(r'"\1":', text.rstrip(","))))
    if not entries:
        raise ValueError("WORD_BANK not found in the legacy file")
    return entries


def parse_images(html: str) -> dict[str, str]:
    """The IMAGES object: name → data:image/jpeg;base64,... URI."""
    for line in html.splitlines():
        if line.startswith(_IMAGES_PREFIX):
            body = line[len(_IMAGES_PREFIX):].strip()
            return json.loads(body[:-1] if body.endswith(";") else body)
    return {}


def decode_data_uri(uri: str) -> bytes:
    header, _, payload = uri.partition(",")
    if not header.startswith("data:") or not header.endswith(";base64"):
        raise ValueError("not a base64 data URI")
    return base64.b64decode(payload)


# ---------------- content ----------------

def build_card(entry: dict) -> LearnCard:
    """Spec §13 field mapping: def→short_def, full→kid_def, ex→examples, parts→word_parts, syn→synonyms."""
    word = entry["w"].strip().lower()
    examples = [s for s in entry.get("ex") or [] if s]
    return LearnCard(
        pos=entry["pos"],
        forms=sorted(word_forms(word) - {word}),
        short_def=entry["def"],
        kid_def=entry.get("full") or entry["def"],
        senses=[Sense(pos=entry["pos"], definition=entry["def"], example=examples[0] if examples else "")],
        examples=examples,
        word_parts=entry.get("parts") or "",
        synonyms=list(entry.get("syn") or [])[:5],
    )


def _checked_card(word: str, entry: dict) -> LearnCard | None:
    card = build_card(entry)
    check = validate_card(word, LEGACY_BAND, card, legacy=True)
    if check.card is None and card.word_parts:
        # e.g. an etymology note that trips the kid-safety list: keep the word, drop the note
        check = validate_card(word, LEGACY_BAND, card.model_copy(update={"word_parts": ""}), legacy=True)
    return check.card


def blank_word(sentence: str, word: str, forms: list[str]) -> tuple[str, str] | None:
    """Replace the first occurrence of the word (or a form) with ___; returns (prompt, surface form)."""
    for match in _TOKEN_RE.finditer(sentence):
        core = _POSSESSIVE_RE.sub("", match.group(0))
        if is_form_of(core, word, forms):
            start = match.start()
            return sentence[:start] + "___" + sentence[start + len(core):], core
    return None


def _with_answer(correct: str, distractors: list[str], rng: random.Random) -> tuple[list[str], int]:
    choices = [correct, *distractors]
    rng.shuffle(choices)
    return choices, choices.index(correct)


def build_questions(entry: dict, card: LearnCard, others: list[dict]) -> list[RawQuestion]:
    """meaning, pick_word, fill_blank, spell_it and synonym questions; deterministic per word."""
    word = entry["w"].strip().lower()
    rng = random.Random(f"wordquest-legacy:{word}")
    definition = card.short_def
    explanation = f'"{word}" means {definition}.'
    other_words = [o["w"].strip().lower() for o in others]
    out: list[RawQuestion] = []

    # meaning: distractor definitions from words with the same part of speech when there are enough (as legacy did)
    candidates = [o for o in others if o["def"] != entry["def"]]
    same_pos = [o for o in candidates if o["pos"] == entry["pos"]]
    pool = same_pos if len(same_pos) >= 3 else candidates
    choices, answer = _with_answer(definition, [o["def"] for o in rng.sample(pool, 3)], rng)
    out.append(RawQuestion(type="meaning", prompt=f'What does "{word}" mean?', choices=choices,
                           answer_index=answer, accepted_answers=[], explanation=explanation))

    # pick_word: definition → word, distractors are other starter words
    choices, answer = _with_answer(word, rng.sample(other_words, 3), rng)
    out.append(RawQuestion(type="pick_word", prompt=f'Which word means "{definition}"?', choices=choices,
                           answer_index=answer, accepted_answers=[], explanation=explanation))

    examples = card.examples
    if examples:
        # fill_blank uses the second example when there is one (the intro screen shows the first)
        blanked = blank_word(examples[1] if len(examples) > 1 else examples[0], word, card.forms)
        if blanked:
            prompt, surface = blanked
            correct = normalize_answer(surface)
            choices, answer = _with_answer(correct, rng.sample([w for w in other_words if w != correct], 3), rng)
            out.append(RawQuestion(type="fill_blank", prompt=prompt, choices=choices, answer_index=answer,
                                   accepted_answers=[], explanation=explanation))
        # spell_it: first example with a definition cue; accepted = the exact form used in the sentence
        blanked = blank_word(examples[0], word, card.forms)
        if blanked:
            prompt, surface = blanked
            out.append(RawQuestion(type="spell_it", prompt=f"{prompt} (means: {definition})", choices=[],
                                   answer_index=-1, accepted_answers=[normalize_answer(surface)],
                                   explanation=explanation))

    # synonym: correct from this word's list, distractors from other words' first synonyms
    if card.synonyms:
        correct = card.synonyms[0]
        own = {s.lower() for s in card.synonyms} | {word}
        distractor_pool = sorted({o["syn"][0] for o in others if o.get("syn") and o["syn"][0].lower() not in own})
        if len(distractor_pool) >= 3:
            choices, answer = _with_answer(correct, rng.sample(distractor_pool, 3), rng)
            out.append(RawQuestion(type="synonym", prompt=f'Which word is closest in meaning to "{word}"?',
                                   choices=choices, answer_index=answer, accepted_answers=[],
                                   explanation=f'"{correct}" means almost the same as "{word}": {definition}.'))
    return out


def _to_question(word: str, raw: RawQuestion, created_at: str) -> Question:
    return Question(
        id=new_id(), word=word, band=LEGACY_BAND, content_version=LEGACY_VERSION, type=raw.type,
        tier=TIER[raw.type], prompt=raw.prompt, choices=list(raw.choices), answer_index=raw.answer_index,
        accepted_answers=list(raw.accepted_answers), explanation=raw.explanation, source="legacy",
        verified=True, created_at=created_at,
    )


def _store_image(repo: Repository, blobs: BlobStore, word: str, uri: str) -> None:
    try:
        webp = to_webp(decode_data_uri(uri))
    except (ValueError, OSError):  # bad base64 / unreadable image
        return  # the Learn page falls back to the emoji card
    key = image_key(LEGACY_BAND, word, LEGACY_VERSION)
    blobs.put(key, webp, "image/webp")
    repo.set_image(LEGACY_BAND, word, key, "ready", LEGACY_VERSION)


def seed_legacy(repo: Repository, blobs: BlobStore, legacy_path: Path) -> int:
    """Create the "Starter set" list and its band 6-8 legacy content; returns words seeded (0 if already present)."""
    if any(wl.name == STARTER_LIST_NAME for wl in repo.list_lists()):
        return 0
    html = Path(legacy_path).read_text(encoding="utf-8")
    entries = parse_word_bank(html)
    images = parse_images(html)
    now = clock.utc_now_iso()
    words: list[str] = []
    seeded = 0
    for entry in entries:
        word = entry["w"].strip().lower()
        if word in words:
            continue
        words.append(word)
        if repo.get_content(LEGACY_BAND, word) is not None:
            continue  # keep existing content (AI-generated, or from an interrupted earlier seed)
        card = _checked_card(word, entry)
        if card is None:
            continue
        others = [o for o in entries if o["w"].strip().lower() != word]
        kept, _drops = validate_questions(word, LEGACY_BAND, card, build_questions(entry, card, others), legacy=True)
        repo.save_content(WordContent(word=word, band=LEGACY_BAND, status="ready", source="legacy",
                                      content_version=LEGACY_VERSION, card=card, generated_at=now))
        repo.add_questions(LEGACY_BAND, word, LEGACY_VERSION, [_to_question(word, raw, now) for raw in kept])
        uri = images.get(entry.get("img") or "")
        if uri:
            _store_image(repo, blobs, word, uri)
        seeded += 1
    # Created last, so an interrupted seed is completed by the next run.
    repo.save_list(WordList(id=new_id(), name=STARTER_LIST_NAME, words=words, created_at=now, updated_at=now))
    return seeded


def main() -> None:
    settings = Settings()
    settings.ensure_dirs()
    if not DEFAULT_LEGACY_PATH.exists():
        raise SystemExit(f"Legacy file not found: {DEFAULT_LEGACY_PATH}")
    repo, blobs = make_storage(settings)
    count = seed_legacy(repo, blobs, DEFAULT_LEGACY_PATH)
    print(f"Seeded {count} legacy starter words into {settings.data_dir}")


if __name__ == "__main__":
    main()

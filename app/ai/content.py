from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Iterable

from pydantic import ValidationError

from app import clock
from app.ai.llm import InvalidOutput, LLMClient, LLMResult
from app.ai.prompts import check_prompt, learn_card_prompt, question_batch_prompt
from app.ai.schemas import (
    ANSWER_CHECK,
    ANSWER_CHECK_SCHEMA,
    LEARN_CARD,
    LEARN_CARD_SCHEMA,
    QUESTION_BATCH,
    QUESTION_BATCH_SCHEMA,
    CheckResult,
    RawQuestion,
    parse_card,
    parse_check,
    parse_questions,
)
from app.ai.validate import validate_card, validate_questions
from app.learning.grading import normalize_answer
from app.logs import JsonlLog
from app.models import QTYPES, TIER, LearnCard, Question, new_id

INITIAL_MIX: dict[str, int] = {
    "meaning": 1,
    "pick_word": 1,
    "fill_blank": 2,
    "usage": 1,
    "scenario": 2,
    "synonym": 1,
    "antonym": 1,
    "spell_it": 2,
    "word_parts": 1,
}

MIN_POOL = 6
MIN_TIER1 = 2
MIN_TIER2 = 1


def _ordered_mix(counts: dict[str, int]) -> dict[str, int]:
    """Keys in QTYPES order, zero/negative counts removed."""
    return {t: counts[t] for t in QTYPES if counts.get(t, 0) > 0}


def initial_mix(card: LearnCard) -> dict[str, int]:
    mix = dict(INITIAL_MIX)
    if not card.antonyms:
        mix["scenario"] += mix.pop("antonym")
    if not card.synonyms:
        mix["usage"] += mix.pop("synonym")
    if not card.word_parts.strip():
        mix["spell_it"] += mix.pop("word_parts")
    return _ordered_mix(mix)


def pool_meets_minimum(qs: list[Question]) -> bool:
    verified = [q for q in qs if q.verified]
    tier1 = sum(1 for q in verified if q.tier == 1)
    tier2 = sum(1 for q in verified if q.tier == 2)
    return len(verified) >= MIN_POOL and tier1 >= MIN_TIER1 and tier2 >= MIN_TIER2


def _tier_types(card: LearnCard, tier: int) -> list[str]:
    """Types of a tier that this card can support (same substitutions as initial_mix)."""
    return [t for t in initial_mix(card) if TIER[t] == tier]


def _least_used_first(types: list[str], verified: list[Question]) -> list[str]:
    counts = Counter(q.type for q in verified)
    return sorted(types, key=lambda t: (counts[t], QTYPES.index(t)))


def shortfall_mix(qs: list[Question], card: LearnCard) -> dict[str, int]:
    """Replacement request for a pool below the minimum: 2 questions per missing slot.

    Missing tier-1 slots are filled with tier-1 types, missing tier-2 slots with tier-2 types,
    and any remaining slots (to reach 6 total) alternate tier 1 / tier 2. Within a tier, types
    with the fewest verified questions come first. Returns {} when the minimum is already met.
    """
    verified = [q for q in qs if q.verified]
    tier1 = sum(1 for q in verified if q.tier == 1)
    tier2 = sum(1 for q in verified if q.tier == 2)
    need1 = max(0, MIN_TIER1 - tier1)
    need2 = max(0, MIN_TIER2 - tier2)
    need_other = max(0, MIN_POOL - len(verified) - need1 - need2)
    if need1 + need2 + need_other == 0:
        return {}
    cycle1 = itertools.cycle(_least_used_first(_tier_types(card, 1), verified))
    cycle2 = itertools.cycle(_least_used_first(_tier_types(card, 2), verified))
    picks = [next(cycle1) for _ in range(2 * need1)]
    picks += [next(cycle2) for _ in range(2 * need2)]
    for i in range(2 * need_other):
        picks.append(next(cycle1) if i % 2 == 0 else next(cycle2))
    return _ordered_mix(Counter(picks))


def topup_mix(pool: list[Question], card: LearnCard, n: int) -> dict[str, int]:
    """n questions, each given to the tier (then type) whose verified count is lowest relative
    to initial_mix(card). Ties go to the lower tier number, then QTYPES order."""
    if n <= 0:
        return {}
    target = initial_mix(card)
    tier_target: Counter[int] = Counter()
    for t, k in target.items():
        tier_target[TIER[t]] += k
    verified = [q for q in pool if q.verified]
    type_count: Counter[str] = Counter(q.type for q in verified if q.type in target)
    tier_count: Counter[int] = Counter(TIER[q.type] for q in verified if q.type in target)
    picks: Counter[str] = Counter()
    for _ in range(n):
        tier = min(
            (tr for tr in sorted(tier_target) if tier_target[tr] > 0),
            key=lambda tr: (tier_count[tr] / tier_target[tr], tr),
        )
        qtype = min(
            (t for t in target if TIER[t] == tier),
            key=lambda t: (type_count[t] / target[t], QTYPES.index(t)),
        )
        picks[qtype] += 1
        type_count[qtype] += 1
        tier_count[tier] += 1
    return _ordered_mix(picks)


def _check_matches(q: RawQuestion, r: CheckResult) -> bool:
    if q.type == "spell_it":
        fill = normalize_answer(r.fill)
        return bool(fill) and fill in {normalize_answer(a) for a in q.accepted_answers}
    return r.chosen_index == q.answer_index


def _accepted(q: RawQuestion) -> list[str]:
    out: list[str] = []
    for a in q.accepted_answers:
        n = normalize_answer(a)
        if n and n not in out:
            out.append(n)
    return out


class ContentGenerator:
    def __init__(
        self,
        llm: LLMClient,
        *,
        model_name: str,
        rejection_log: JsonlLog | None = None,
        usage_log: JsonlLog | None = None,
    ) -> None:
        self.llm = llm
        self.model_name = model_name
        self.rejection_log = rejection_log
        self.usage_log = usage_log

    # ---- logging helpers -------------------------------------------------
    def _log_usage(self, word: str, band: str, call: str, result: LLMResult) -> None:
        if self.usage_log is not None:
            self.usage_log.write({"word": word, "band": band, "call": call, "usage": result.usage})

    def _reject(self, word: str, band: str, kind: str, errors: Iterable[str], raw: object) -> None:
        if self.rejection_log is not None:
            self.rejection_log.write(
                {"word": word, "band": band, "kind": kind, "errors": list(errors), "raw": raw}
            )

    # ---- learn card ------------------------------------------------------
    async def make_card(self, word: str, band: str) -> LearnCard:
        system, user = learn_card_prompt(word, band)
        result = await self.llm.chat_json(
            name=LEARN_CARD, schema=LEARN_CARD_SCHEMA, system=system, user=user
        )
        self._log_usage(word, band, LEARN_CARD, result)
        try:
            card = parse_card(result.data)
        except InvalidOutput as e:
            self._reject(word, band, "learn_card", [str(e)], result.data)
            raise
        check = validate_card(word, band, card)
        if check.card is None:
            self._reject(word, band, "learn_card", check.errors, result.data)
            raise InvalidOutput("learn card rejected: " + "; ".join(check.errors))
        return check.card

    # ---- questions -------------------------------------------------------
    async def make_questions(
        self,
        word: str,
        band: str,
        card: LearnCard,
        mix: dict[str, int],
        existing: list[Question],
        version: int,
    ) -> list[Question]:
        mix = _ordered_mix(mix)
        if not mix:
            return []
        existing_prompts = [q.prompt for q in existing]

        # 1) batch call
        system, user = question_batch_prompt(word, band, card, mix, existing_prompts)
        result = await self.llm.chat_json(
            name=QUESTION_BATCH, schema=QUESTION_BATCH_SCHEMA, system=system, user=user
        )
        self._log_usage(word, band, QUESTION_BATCH, result)
        try:
            raw = parse_questions(result.data)
        except InvalidOutput as e:
            self._reject(word, band, "question_batch", [str(e)], result.data)
            raise
        except (ValidationError, KeyError, TypeError) as e:
            self._reject(word, band, "question_batch", [str(e)], result.data)
            raise InvalidOutput(f"question batch unparseable: {e}") from None

        # 2) code-level validation
        kept, drops = validate_questions(word, band, card, raw, existing_prompts=existing_prompts)
        for d in drops:
            self._reject(word, band, "question", [d.reason], raw[d.index].model_dump())
        if not kept:
            return []

        # 3) blind answer-key check: the checker sees only what the learner sees
        candidates = {f"q{i}": q for i, q in enumerate(kept, start=1)}
        items = [
            {"qid": qid, "type": q.type, "prompt": q.prompt, "choices": list(q.choices)}
            for qid, q in candidates.items()
        ]
        system, user = check_prompt(items)
        result = await self.llm.chat_json(
            name=ANSWER_CHECK, schema=ANSWER_CHECK_SCHEMA, system=system, user=user
        )
        self._log_usage(word, band, ANSWER_CHECK, result)
        try:
            results = parse_check(result.data)
        except InvalidOutput as e:
            self._reject(word, band, "answer_check", [str(e)], result.data)
            raise
        except (ValidationError, KeyError, TypeError) as e:
            self._reject(word, band, "answer_check", [str(e)], result.data)
            raise InvalidOutput(f"answer check unparseable: {e}") from None

        by_qid: dict[str, list[CheckResult]] = {}
        for r in results:
            by_qid.setdefault(r.qid, []).append(r)

        # 4) keep only questions the blind checker answered exactly once, unambiguously, correctly
        now = clock.utc_now_iso()  # through the module, so tests can freeze time
        verified: list[Question] = []
        for qid, q in candidates.items():
            rs = by_qid.get(qid, [])
            if not rs:
                reason = "check: no result"
            elif len(rs) > 1:
                reason = "check: duplicate result"
            elif rs[0].ambiguous:
                reason = "check: ambiguous"
            elif not _check_matches(q, rs[0]):
                reason = "check: answer mismatch"
            else:
                reason = ""
            if reason:
                self._reject(
                    word,
                    band,
                    "question_check",
                    [reason],
                    {"qid": qid, "question": q.model_dump(), "results": [r.model_dump() for r in rs]},
                )
                continue
            verified.append(
                Question(
                    id=new_id(),
                    word=word,
                    band=band,
                    content_version=version,
                    type=q.type,
                    tier=TIER[q.type],
                    prompt=q.prompt,
                    choices=list(q.choices),
                    answer_index=q.answer_index,
                    accepted_answers=_accepted(q) if q.type == "spell_it" else [],
                    explanation=q.explanation,
                    source="ai",
                    verified=True,
                    created_at=now,
                )
            )
        return verified

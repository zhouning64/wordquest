"""Session builder and question selection (spec §8.1–§8.2). Pure: no storage access."""
from __future__ import annotations

import random
from dataclasses import dataclass

from app.models import Profile, Question, WordContent, WordProgress

PRACTICE_MAX_WORDS = 12
RESERVE_TIERS = (1, 2)
CHECK_SET_SIZE = 3
MAX_REASKS = 2
INTRO_GAPS = (3, 4, 5)
# Tier mix by stage (§8.2): list of (tier, probability), probabilities sum to 1.
TIER_MIX: dict[int, tuple[tuple[int, float], ...]] = {
    0: ((1, 1.0),),
    1: ((1, 1.0),),
    2: ((1, 0.3), (2, 0.7)),
    3: ((1, 0.3), (2, 0.7)),
    4: ((2, 0.4), (3, 0.6)),
    5: ((2, 0.4), (3, 0.6)),
}


@dataclass
class SessionInputs:
    profile: Profile
    mode: str                                 # "normal" | "practice"
    local_date: str
    eligible_words: list[str]                 # ordered, deduped words from the profile's current lists
    contents: dict[str, WordContent]          # band contents for eligible words (any status)
    pools: dict[str, list[Question]]          # active-version pools (only verified questions are used)
    progress: dict[str, WordProgress]
    seed: int = 0


def capacity(session_minutes: int) -> int:
    return max(10, 2 * session_minutes)


# ---------- word selection ----------

def _verified(inp: SessionInputs, word: str) -> list[Question]:
    return [q for q in inp.pools.get(word, []) if q.verified]


def _is_ready(inp: SessionInputs, word: str) -> bool:
    c = inp.contents.get(word)
    return c is not None and c.status == "ready" and c.card is not None and bool(_verified(inp, word))


def _eligible(inp: SessionInputs) -> list[str]:
    return list(dict.fromkeys(inp.eligible_words))


def _select_words(inp: SessionInputs) -> tuple[list[str], list[str]]:
    """Returns (scheduled words in order, new words in order). Lists are already cut to fit capacity,
    so the queue never needs truncation and an intro is never separated from its question."""
    if inp.mode not in ("normal", "practice"):
        raise ValueError(f"unknown session mode: {inp.mode}")
    cap = capacity(inp.profile.settings.session_minutes)
    ready = [w for w in _eligible(inp) if _is_ready(inp, w)]
    prog = inp.progress
    if inp.mode == "practice":
        words = [w for w in ready if w in prog and 1 <= prog[w].stage <= 3]
        words.sort(key=lambda w: (prog[w].stage, -prog[w].wrong))
        return words[: min(PRACTICE_MAX_WORDS, cap)], []
    reviews = [
        w for w in ready
        if w in prog and prog[w].due_date is not None and prog[w].due_date <= inp.local_date
    ]
    reviews.sort(key=lambda w: (prog[w].due_date, prog[w].stage))
    reviews = reviews[:cap]
    if len(reviews) >= cap:
        n_new = 0
    else:
        n_new = min(inp.profile.settings.new_words_per_session, (cap - len(reviews)) // 2)
    new_words = [w for w in ready if w not in prog][:n_new]
    return reviews, new_words


# ---------- queue layout ----------

def _layout(reviews: list[str], new_words: list[str], rng: random.Random) -> list[tuple[str, str]]:
    """Deterministic interleave.
    1. Base order: reviews in order; new word i's intro goes just before review (i * len(reviews)) // len(new_words)
       (all intros first when there are no reviews).
    2. When an intro lands at queue index s, its question reserves index s + g, g = rng.randint(3, 5)
       (if taken, the other gaps 3, 4, 5 are tried in order; at most two are ever taken).
       Reserved indexes are filled by questions, the others by base items in order.
    3. Questions whose reserved index lies past the last base item are appended at the end in index order."""
    r, n = len(reviews), len(new_words)
    base: list[tuple[str, str]] = []
    ni = 0
    for ri in range(r + 1):
        while ni < n and (ni * r) // n == ri:
            base.append(("intro", new_words[ni]))
            ni += 1
        if ri < r:
            base.append(("question", reviews[ri]))
    queue: list[tuple[str, str]] = []
    reserved: dict[int, str] = {}
    bi = 0
    while bi < len(base):
        s = len(queue)
        if s in reserved:
            queue.append(("question", reserved.pop(s)))
            continue
        kind, word = base[bi]
        bi += 1
        queue.append((kind, word))
        if kind == "intro":
            first = rng.randint(INTRO_GAPS[0], INTRO_GAPS[-1])
            options = [first] + [g for g in INTRO_GAPS if g != first]
            gap = next(g for g in options if s + g not in reserved)
            reserved[s + gap] = word
    for slot in sorted(reserved):
        queue.append(("question", reserved[slot]))
    return queue


# ---------- question selection ----------

def _last_seen_index(seen_ids: list[str]) -> dict[str, int]:
    """Most recent position of each id; larger = seen more recently."""
    return {qid: i for i, qid in enumerate(seen_ids)}


def _by_recency(qs: list[Question], last_seen: dict[str, int]) -> list[Question]:
    """Never-seen first (pool order), then least recently seen first."""
    return sorted(qs, key=lambda q: last_seen.get(q.id, -1))


def _pick_tier(stage: int, rng: random.Random) -> int:
    mix = TIER_MIX[max(0, min(5, stage))]
    roll = rng.random()
    acc = 0.0
    for tier, weight in mix:
        acc += weight
        if roll < acc:
            return tier
    return mix[-1][0]


def _fallback_tiers(tier: int, stage: int) -> tuple[int, ...]:
    """The chosen tier, then its adjacent tier(s). For tier 2 the stage's other mix tier comes first."""
    if tier == 1 or tier == 3:
        return (tier, 2)
    return (2, 1, 3) if stage <= 3 else (2, 3, 1)


def _choose_main(pool: list[Question], stage: int, seen_ids: list[str], rng: random.Random) -> Question:
    """Unseen question in the stage's tier, else unseen in the adjacent tier, else least recently seen overall."""
    tier = _pick_tier(stage, rng)
    seen = set(seen_ids)
    for t in _fallback_tiers(tier, stage):
        unseen = [q for q in pool if q.tier == t and q.id not in seen]
        if unseen:
            return rng.choice(unseen)
    return _by_recency(pool, _last_seen_index(seen_ids))[0]


def _take(cands: list[Question], k: int, start: list[Question] | None = None) -> list[Question]:
    """Greedy pick of k more questions from cands, preferring a type not yet in the set."""
    chosen = list(start or [])
    chosen_ids = {q.id for q in chosen}
    remaining = [q for q in cands if q.id not in chosen_ids]
    target = len(chosen) + k
    while len(chosen) < target and remaining:
        types = {q.type for q in chosen}
        nxt = next((q for q in remaining if q.type not in types), remaining[0])
        chosen.append(nxt)
        remaining.remove(nxt)
    return chosen


def _reserves(pool: list[Question], main: Question, seen_ids: list[str]) -> tuple[list[Question], list[Question], list[Question]]:
    """Returns (check set 1, check set 2, re-asks), all from tiers 1–2.
    Priority: (1) no check question equals the main question; (2) the two sets are disjoint;
    (3) re-asks are distinct from the main question and both sets — re-asks are dropped first.
    Each set spans >= 2 types when possible. Rule (1) is relaxed only when fewer than 3 other
    tier-1/2 questions exist (the main question then fills the set); sets are shorter only when the
    whole tier-1/2 pool has fewer than 3 questions."""
    ordered = _by_recency([q for q in pool if q.tier in RESERVE_TIERS], _last_seen_index(seen_ids))
    others = [q for q in ordered if q.id != main.id]
    if len(others) >= 2 * CHECK_SET_SIZE:
        set1: list[Question] = []
        set2: list[Question] = []
        remaining = list(others)
        for target in (set1, set2) * CHECK_SET_SIZE:
            types = {q.type for q in target}
            nxt = next((q for q in remaining if q.type not in types), remaining[0])
            target.append(nxt)
            remaining.remove(nxt)
    else:
        set1 = _take(others, CHECK_SET_SIZE)
        if len(set1) < CHECK_SET_SIZE:
            set1 = _take(ordered, CHECK_SET_SIZE - len(set1), start=set1)
        set1_ids = {q.id for q in set1}
        rest = [q for q in others if q.id not in set1_ids]
        set2 = _take(rest, CHECK_SET_SIZE)
        if len(set2) < CHECK_SET_SIZE:
            set2 = _take(set1, CHECK_SET_SIZE - len(set2), start=set2)
    used = {main.id} | {q.id for q in set1} | {q.id for q in set2}
    reasks = [q for q in others if q.id not in used][:MAX_REASKS]
    return set1, set2, reasks


# ---------- payload ----------

def build_session(inp: SessionInputs) -> dict:
    rng = random.Random(inp.seed)
    scheduled, new_words = _select_words(inp)
    layout = _layout(scheduled, new_words, rng)

    order: list[str] = []
    for _, word in layout:
        if word not in order:
            order.append(word)

    mains: dict[str, Question] = {}
    for kind, word in layout:
        if kind == "question" and word not in mains:
            p = inp.progress.get(word)
            mains[word] = _choose_main(
                _verified(inp, word), p.stage if p else 0, list(p.seen_question_ids) if p else [], rng
            )

    queue: list[dict] = []
    for kind, word in layout:
        if kind == "intro":
            queue.append({"kind": "intro", "word": word})
        else:
            queue.append({"kind": "question", "word": word, "question": mains[word].public()})

    words: dict[str, dict] = {}
    for word in order:
        c = inp.contents[word]
        p = inp.progress.get(word)
        set1, set2, reasks = _reserves(_verified(inp, word), mains[word], list(p.seen_question_ids) if p else [])
        words[word] = {
            "card": c.card.model_dump(mode="json") if c.card is not None else None,
            "image_key": c.image_key,
            "source": c.source,
            "stage": p.stage if p else 0,
            "last_graded_on": p.last_graded_on if p else None,
            "reserves": {
                "reasks": [q.public() for q in reasks],
                "checks": [[q.public() for q in set1], [q.public() for q in set2]],
            },
        }

    eligible = _eligible(inp)
    ready_count = sum(1 for w in eligible if _is_ready(inp, w))
    if queue:
        empty_reason = None
    elif ready_count < len(eligible):
        empty_reason = "preparing"
    else:
        empty_reason = "nothing_due"

    s = inp.profile.settings
    return {
        "mode": inp.mode,
        "local_date": inp.local_date,
        "settings": {
            "session_minutes": s.session_minutes,
            "break_reminder": s.break_reminder,
            "break_message": s.break_message,
        },
        "queue": queue,
        "words": words,
        "empty_reason": empty_reason,
        "preparing": {"ready": ready_count, "total": len(eligible)},
    }


def topup_words(inp: SessionInputs) -> list[str]:
    """Session words (selection order: scheduled first, then new) whose profile has seen >= 75% of the
    word's verified pool, counting only seen ids that are still in that pool."""
    scheduled, new_words = _select_words(inp)
    out: list[str] = []
    for word in scheduled + new_words:
        pool_ids = {q.id for q in _verified(inp, word)}
        p = inp.progress.get(word)
        if not pool_ids or p is None:
            continue
        seen = pool_ids & set(p.seen_question_ids)
        if 4 * len(seen) >= 3 * len(pool_ids):
            out.append(word)
    return out

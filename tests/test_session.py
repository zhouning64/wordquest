from __future__ import annotations

import copy
import json
from collections import Counter

import pytest

from app.learning import session as session_module
from app.learning.session import SessionInputs, build_session, capacity, topup_words
from app.learning.srs import apply_intro
from app.models import TIER, LearnCard, Profile, ProfileSettings, Question, Sense, WordContent, WordProgress

TODAY = "2026-10-07"
FULL_MIX = [
    "meaning", "pick_word", "fill_blank", "fill_blank",
    "usage", "scenario", "scenario", "synonym", "antonym",
    "spell_it", "spell_it", "word_parts",
]
PUBLIC_KEYS = {"id", "type", "tier", "prompt", "choices", "answer_index", "accepted_answers", "explanation"}
MIN_BETWEEN = 4  # other items required between a new word's intro and its first question
NO_RESERVES = {"reasks": [], "checks": [[], []]}


# ---------- builders (local to this file) ----------

def card(word: str) -> LearnCard:
    return LearnCard(
        pos="adjective",
        short_def=f"short meaning of {word}",
        kid_def=f"a longer, kid-friendly meaning of {word}",
        senses=[Sense(pos="adjective", definition=f"meaning of {word}", example=f"The {word} cat waited.")],
        examples=[f"One {word} day.", f"Two {word} dogs.", f"Three {word} kids.", f"Four {word} birds."],
    )


def content(word: str, *, status: str = "ready", with_card: bool = True,
            image_key: str | None = None, source: str = "ai") -> WordContent:
    return WordContent(word=word, band="6-8", status=status, card=card(word) if with_card else None,
                       image_key=image_key, source=source)


def q(word: str, qtype: str, n: int = 0, *, verified: bool = True) -> Question:
    spell = qtype == "spell_it"
    return Question(
        id=f"{word}-{qtype}-{n}", word=word, band="6-8", content_version=1, type=qtype, tier=TIER[qtype],
        prompt=f"{qtype} #{n} for {word} ___ (means: something)" if spell else f"{qtype} #{n} for {word}",
        choices=[] if spell else ["w", "x", "y", "z"],
        answer_index=-1 if spell else 0,
        accepted_answers=[word] if spell else [],
        explanation=f"because {word}", verified=verified, created_at=f"2026-10-01T00:00:{n:02d}Z",
    )


def pool(word: str, types: list[str] = FULL_MIX, *, verified: bool = True) -> list[Question]:
    counts: Counter[str] = Counter()
    out = []
    for t in types:
        out.append(q(word, t, counts[t], verified=verified))
        counts[t] += 1
    return out


def prog(word: str, stage: int = 1, due: str | None = TODAY, *, last: str | None = "2026-10-01",
         wrong: int = 0, seen_ids: list[str] | None = None) -> WordProgress:
    return WordProgress(profile_id="p1", word=word, stage=stage, due_date=due, interval_days=1,
                        introduced_on="2026-09-01", last_graded_on=last, wrong=wrong,
                        seen_question_ids=list(seen_ids or []))


def make_profile(minutes: int = 15, new: int = 5) -> Profile:
    return Profile(id="p1", name="Ada", band="6-8", list_ids=["l1"],
                   settings=ProfileSettings(session_minutes=minutes, new_words_per_session=new))


def inputs(words: list[str], *, progress: list[WordProgress] | None = None, mode: str = "normal", minutes: int = 15,
           new: int = 5, seed: int = 0, contents: dict | None = None, pools: dict | None = None,
           local_date: str = TODAY) -> SessionInputs:
    return SessionInputs(
        profile=make_profile(minutes, new),
        mode=mode,
        local_date=local_date,
        eligible_words=list(words),
        contents=contents if contents is not None else {w: content(w) for w in words},
        pools=pools if pools is not None else {w: pool(w) for w in words},
        progress={p.word: p for p in progress or []},
        seed=seed,
    )


def qitems(payload: dict) -> list[dict]:
    return [it for it in payload["queue"] if it["kind"] == "question"]


def qwords(payload: dict) -> list[str]:
    return [it["word"] for it in qitems(payload)]


def intro_words(payload: dict) -> list[str]:
    return [it["word"] for it in payload["queue"] if it["kind"] == "intro"]


def main_q(payload: dict, word: str) -> dict:
    return next(it["question"] for it in qitems(payload) if it["word"] == word)


# ---------- capacity and word selection ----------

@pytest.mark.parametrize("minutes, cap", [(1, 10), (5, 10), (6, 12), (15, 30), (30, 60)])
def test_capacity(minutes, cap):
    assert capacity(minutes) == cap


def test_queue_never_exceeds_capacity():
    words = [f"w{i:02d}" for i in range(40)]
    progress = [prog(w, 2, f"2026-09-{(i % 28) + 1:02d}") for i, w in enumerate(words)]
    payload = build_session(inputs(words + ["fresh"], progress=progress, minutes=5))
    assert len(payload["queue"]) == 10
    assert intro_words(payload) == []
    assert len(set(qwords(payload))) == 10


def test_new_words_fit_capacity_without_truncation():
    words = [f"n{i:02d}" for i in range(20)]
    for reviews in (0, 3, 7):
        progress = [prog(w, 1, TODAY) for w in words[:reviews]]
        payload = build_session(inputs(words, progress=progress, minutes=5, new=10))
        assert len(payload["queue"]) <= 10
        intros = intro_words(payload)
        assert len(intros) == min(10, (10 - reviews) // 2)
        for w in intros:
            assert qwords(payload).count(w) <= 1  # a too-close first question is dropped, never duplicated
            between = items_between(payload, w)
            assert between is None or between >= MIN_BETWEEN
        assert set(payload["words"]) == {it["word"] for it in payload["queue"]}


def test_only_current_list_words_are_used():
    words = ["brave", "calm"]
    progress = [prog("brave", 2, TODAY), prog("orphan", 1, "2026-09-01")]
    contents = {w: content(w) for w in words + ["orphan"]}
    pools = {w: pool(w) for w in words + ["orphan"]}
    payload = build_session(inputs(words, progress=progress, contents=contents, pools=pools))
    assert "orphan" not in qwords(payload)
    assert "orphan" not in payload["words"]
    assert set(payload["words"]) == {"brave", "calm"}


def test_ready_filtering():
    words = ["ok", "pending", "nocard", "unverified", "failed", "nopool", "due_pending"]
    contents = {
        "ok": content("ok"),
        "pending": content("pending", status="pending", with_card=False),
        "nocard": content("nocard", with_card=False),
        "unverified": content("unverified"),
        "failed": content("failed", status="failed"),
        "nopool": content("nopool"),
        "due_pending": content("due_pending", status="pending"),
    }
    pools = {w: pool(w) for w in words}
    pools["unverified"] = pool("unverified", verified=False)
    pools["nopool"] = []
    progress = [prog("due_pending", 2, TODAY)]
    payload = build_session(inputs(words, progress=progress, contents=contents, pools=pools))
    assert intro_words(payload) == ["ok"]
    assert qwords(payload) == []  # a lone new word is too close to its intro: intro only this session
    assert payload["preparing"] == {"ready": 1, "total": 7}
    assert payload["empty_reason"] is None


def test_unverified_questions_are_never_used():
    mixed = pool("ok", ["meaning", "pick_word"], verified=False) + [q("ok", "fill_blank", 5)]
    for seed in range(20):
        payload = build_session(inputs(["ok"], progress=[prog("ok", 0, TODAY)], pools={"ok": mixed}, seed=seed))
        assert main_q(payload, "ok")["id"] == "ok-fill_blank-5"
        reserves = payload["words"]["ok"]["reserves"]
        ids = {x["id"] for s in reserves["checks"] for x in s} | {x["id"] for x in reserves["reasks"]}
        assert ids <= {"ok-fill_blank-5"}


def test_review_ordering_by_due_then_stage():
    words = ["w1", "w2", "w3", "w4", "w5"]
    progress = [
        prog("w1", 3, "2026-10-05"),
        prog("w2", 2, "2026-10-03"),
        prog("w3", 1, "2026-10-05"),
        prog("w4", 0, "2026-10-07"),
        prog("w5", 1, "2026-10-08"),  # not due yet
    ]
    payload = build_session(inputs(words, progress=progress, new=0))
    assert qwords(payload) == ["w2", "w3", "w1", "w4"]
    assert [it["kind"] for it in payload["queue"]] == ["question"] * 4


def test_words_introduced_today_and_never_asked_go_last_among_reviews():
    # An intro-only word (introduced today, never graded) is due today; asking it first in a session started right
    # after its intro would test short-term memory, so it comes after every other review.
    fresh = WordProgress(profile_id="p1", word="fresh", stage=0, due_date=TODAY, introduced_on=TODAY)
    progress = [fresh, prog("old1", 2, "2026-10-05"), prog("old2", 1, TODAY)]
    payload = build_session(inputs(["fresh", "old1", "old2"], progress=progress, new=0))
    assert qwords(payload) == ["old1", "old2", "fresh"]
    # Once it has been answered (last_graded_on set), it sorts normally again.
    graded = fresh.model_copy(update={"last_graded_on": TODAY})
    payload = build_session(inputs(["fresh", "old1", "old2"], progress=[graded, *progress[1:]], new=0))
    assert qwords(payload) == ["old1", "fresh", "old2"]


def test_no_new_words_when_reviews_fill_capacity():
    reviews = [f"r{i}" for i in range(10)]
    news = ["n1", "n2", "n3"]
    progress = [prog(w, 1, TODAY) for w in reviews]
    payload = build_session(inputs(reviews + news, progress=progress, minutes=5, new=5))
    assert intro_words(payload) == []
    assert qwords(payload) == reviews


def test_new_words_reduced_to_half_the_remaining_room():
    reviews = [f"r{i}" for i in range(5)]
    news = [f"n{i}" for i in range(6)]
    progress = [prog(w, 1, TODAY) for w in reviews]
    payload = build_session(inputs(reviews + news, progress=progress, minutes=5, new=5))
    assert intro_words(payload) == ["n0", "n1"]  # min(5, floor((10 - 5) / 2)) = 2, in list order
    assert len(payload["queue"]) == 9


def test_new_words_in_list_order_up_to_setting():
    reviews = ["r0", "r1"]
    news = [f"n{i}" for i in range(8)]
    progress = [prog(w, 1, TODAY) for w in reviews]
    payload = build_session(inputs(news[:4] + reviews + news[4:], progress=progress, minutes=15, new=5))
    assert intro_words(payload) == ["n0", "n1", "n2", "n3", "n4"]
    assert build_session(inputs(news, minutes=15, new=0))["queue"] == []


# ---------- queue layout ----------

def shape(payload: dict) -> list[str]:
    """The queue as compact tokens: "I:word" for an intro, "Q:word" for a question."""
    return [f"{'I' if it['kind'] == 'intro' else 'Q'}:{it['word']}" for it in payload["queue"]]


def mixed_session(n_reviews: int, n_new: int, *, seed: int = 0) -> tuple[dict, list[str], list[str]]:
    """n_reviews due reviews r0.. (stage 1) and n_new new words n0.., with room for all of them."""
    reviews = [f"r{i}" for i in range(n_reviews)]
    news = [f"n{i}" for i in range(n_new)]
    progress = [prog(w, 1, TODAY) for w in reviews]
    payload = build_session(inputs(reviews + news, progress=progress, minutes=30, new=n_new, seed=seed))
    return payload, reviews, news


def items_between(payload: dict, word: str) -> int | None:
    """Other items strictly between the word's intro and its question; None when it has no question."""
    queue = payload["queue"]
    i = next(k for k, it in enumerate(queue) if it["kind"] == "intro" and it["word"] == word)
    j = next((k for k, it in enumerate(queue) if it["kind"] == "question" and it["word"] == word), None)
    return None if j is None else j - i - 1


def test_spacing_constant_replaces_intro_gaps():
    assert session_module.MIN_ITEMS_BETWEEN_INTRO_AND_QUESTION == MIN_BETWEEN
    assert not hasattr(session_module, "INTRO_GAPS")


R = [f"Q:r{i}" for i in range(9)]


@pytest.mark.parametrize("n_reviews, n_new, expected", [
    # 0 reviews + 5 new: all intros, then the block; each question has exactly 4 items between → all kept
    (0, 5, ["I:n0", "I:n1", "I:n2", "I:n3", "I:n4", "Q:n0", "Q:n1", "Q:n2", "Q:n3", "Q:n4"]),
    # 0 reviews + 4 / 3 new: only 3 / 2 items between → intros only
    (0, 4, ["I:n0", "I:n1", "I:n2", "I:n3"]),
    (0, 3, ["I:n0", "I:n1", "I:n2"]),
    # 1 new + 0 reviews: intro only
    (0, 1, ["I:n0"]),
    # 1 new + 6 reviews: 6 items between → kept
    (6, 1, ["I:n0", *R[:6], "Q:n0"]),
    # 1 new + 4 reviews: exactly 4 between → kept; 1 new + 3 reviews: 3 between → dropped
    (4, 1, ["I:n0", *R[:4], "Q:n0"]),
    (3, 1, ["I:n0", *R[:3]]),
    # 3 reviews + 3 new: n0 has 5 between, n1 4, n2 would have 3 → only the block's tail (n2) is dropped
    (3, 3, ["I:n0", "Q:r0", "I:n1", "Q:r1", "I:n2", "Q:r2", "Q:n0", "Q:n1"]),
    # 9 reviews + 3 new: intros spread among the reviews as before; 11, 8 and 5 between → all kept
    (9, 3, ["I:n0", *R[0:3], "I:n1", *R[3:6], "I:n2", *R[6:9], "Q:n0", "Q:n1", "Q:n2"]),
])
def test_pinned_layouts(n_reviews, n_new, expected):
    payload, _, news = mixed_session(n_reviews, n_new)
    assert shape(payload) == expected
    for w in news:
        between = items_between(payload, w)
        assert between is None or between >= MIN_BETWEEN


def test_five_reviews_and_25_new_words():
    payload, reviews, news = mixed_session(5, 25)
    base = []
    for r in range(5):  # five intros before each review: intro i goes just before review (i * 5) // 25
        base += [f"I:n{5 * r + k}" for k in range(5)] + [f"Q:r{r}"]
    assert shape(payload) == base + [f"Q:{w}" for w in news]  # every question kept, block at the end in intro order
    assert len(payload["queue"]) == 55 <= capacity(30)
    assert min(items_between(payload, w) for w in news) == 25  # the last intro (n24) is the closest
    for w in news:
        assert items_between(payload, w) >= MIN_BETWEEN


@pytest.mark.parametrize("n_new", range(0, 21))
@pytest.mark.parametrize("n_reviews", range(0, 13))
def test_layout_rule_holds_for_every_mix(n_reviews, n_new):
    payload, reviews, news = mixed_session(n_reviews, n_new)
    queue = payload["queue"]
    # 1. new words' first questions form one block at the very end, in intro order (a prefix of the new words)
    block = 0
    while block < len(queue) and queue[-1 - block]["kind"] == "question" and queue[-1 - block]["word"] in news:
        block += 1
    base, tail = queue[: len(queue) - block], queue[len(queue) - block:]
    kept = [it["word"] for it in tail]
    assert kept == news[: len(kept)]
    # 2. base order unchanged: reviews in order, intro i just before review (i * reviews) // new (all first if none)
    assert [it["word"] for it in base if it["kind"] == "question"] == reviews
    assert [it["word"] for it in base if it["kind"] == "intro"] == news
    for i, w in enumerate(news):
        k = next(k for k, it in enumerate(base) if it["kind"] == "intro" and it["word"] == w)
        assert sum(1 for it in base[:k] if it["kind"] == "question") == (i * n_reviews) // n_new
    # 3. the rule, on the full block and on the final queue: kept iff >= 4 other items between
    intro_at = {it["word"]: k for k, it in enumerate(base) if it["kind"] == "intro"}
    for i, w in enumerate(news):
        between_in_full_block = len(base) + i - intro_at[w] - 1
        if w in kept:
            assert between_in_full_block >= MIN_BETWEEN
            assert items_between(payload, w) == between_in_full_block  # dropping the tail moved nothing
        else:
            assert between_in_full_block < MIN_BETWEEN
            assert items_between(payload, w) is None
    # 4. every queue word has its card; a word with no question this session has no reserves
    assert set(payload["words"]) == set(reviews) | set(news)
    for w in news:
        assert payload["words"][w]["card"] is not None
        if w not in kept:
            assert payload["words"][w]["reserves"] == NO_RESERVES


def test_layout_does_not_depend_on_the_seed():
    shapes = {tuple(shape(mixed_session(3, 3, seed=seed)[0])) for seed in range(20)}
    assert len(shapes) == 1


def test_intro_only_word_keeps_card_and_image_and_gets_no_reserves():
    words = ["n0", "n1", "n2"]
    contents = {w: content(w, image_key=f"images/6-8/{w}-v1.webp") for w in words}
    payload = build_session(inputs(words, contents=contents))  # 0 reviews + 3 new → intros only
    json.dumps(payload)
    assert shape(payload) == ["I:n0", "I:n1", "I:n2"]
    assert payload["empty_reason"] is None
    for w in words:
        entry = payload["words"][w]
        assert entry["card"] == contents[w].card.model_dump(mode="json")
        assert entry["image_key"] == f"images/6-8/{w}-v1.webp"
        assert (entry["stage"], entry["last_graded_on"]) == (0, None)
        assert entry["reserves"] == NO_RESERVES


def test_words_with_a_question_keep_full_reserves_next_to_an_intro_only_word():
    payload, reviews, news = mixed_session(3, 3)  # n2 is intro only (see test_pinned_layouts)
    assert payload["words"]["n2"]["reserves"] == NO_RESERVES
    for w in reviews + ["n0", "n1"]:
        res = payload["words"][w]["reserves"]
        main_id = main_q(payload, w)["id"]
        assert [len(s) for s in res["checks"]] == [3, 3]
        assert len(res["reasks"]) == 2
        assert main_id not in {x["id"] for s in res["checks"] for x in s} | {x["id"] for x in res["reasks"]}


def test_intro_only_word_is_quizzed_as_a_review_in_the_next_session():
    words = ["n0", "n1", "n2"]
    first = build_session(inputs(words))
    assert shape(first) == ["I:n0", "I:n1", "I:n2"]
    progress = []
    for w in words:  # the learner saw each intro: intro_seen makes the word stage 0, due today
        p = WordProgress(profile_id="p1", word=w)
        apply_intro(p, TODAY)
        assert (p.stage, p.due_date) == (0, TODAY)
        progress.append(p)
    later = build_session(inputs(words, progress=progress))
    assert shape(later) == ["Q:n0", "Q:n1", "Q:n2"]
    for w in words:
        assert main_q(later, w)["tier"] == 1


# ---------- practice mode ----------

def test_practice_mode_selection():
    words = ["s0", "s1a", "s1b", "s2", "s3", "s4", "s5", "fresh"]
    progress = [
        prog("s0", 0, TODAY),
        prog("s1a", 1, "2026-10-20", wrong=1),
        prog("s1b", 1, "2026-10-20", wrong=4),
        prog("s2", 2, "2026-10-20"),
        prog("s3", 3, "2026-10-20", wrong=2),
        prog("s4", 4, TODAY),
        prog("s5", 5, TODAY),
    ]
    payload = build_session(inputs(words, progress=progress, mode="practice"))
    assert payload["mode"] == "practice"
    assert qwords(payload) == ["s1b", "s1a", "s2", "s3"]
    assert intro_words(payload) == []


def test_practice_mode_caps_at_12_and_capacity():
    words = [f"p{i:02d}" for i in range(15)]
    progress = [prog(w, 2, "2026-10-20") for w in words]
    assert len(build_session(inputs(words, progress=progress, mode="practice", minutes=15))["queue"]) == 12
    assert len(build_session(inputs(words, progress=progress, mode="practice", minutes=5))["queue"]) == 10


def test_practice_mode_skips_words_that_are_not_ready():
    words = ["a", "b"]
    contents = {"a": content("a"), "b": content("b", status="pending")}
    progress = [prog("a", 2, "2026-10-20"), prog("b", 2, "2026-10-20")]
    payload = build_session(inputs(words, progress=progress, mode="practice", contents=contents))
    assert qwords(payload) == ["a"]


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        build_session(inputs(["a"], mode="speedrun"))


# ---------- main question tier choice ----------

@pytest.mark.parametrize("seed", range(30))
def test_stage_0_and_1_words_get_tier_1(seed):
    fillers = [f"f{i}" for i in range(4)]  # enough reviews for "fresh" to keep its first question
    words = ["fresh", "zero", "one"] + fillers
    progress = [prog("zero", 0, TODAY), prog("one", 1, TODAY)] + [prog(w, 1, TODAY) for w in fillers]
    payload = build_session(inputs(words, progress=progress, seed=seed))
    for w in words:
        assert main_q(payload, w)["tier"] == 1


def _tier_counts(stage: int, seeds: int = 400) -> Counter:
    counts: Counter[int] = Counter()
    for seed in range(seeds):
        payload = build_session(inputs(["w"], progress=[prog("w", stage, TODAY)], seed=seed))
        counts[main_q(payload, "w")["tier"]] += 1
    return counts


@pytest.mark.parametrize("stage", [2, 3])
def test_stage_2_3_mix_is_30_70(stage):
    counts = _tier_counts(stage)
    assert set(counts) <= {1, 2}
    assert 0.22 <= counts[1] / 400 <= 0.38


@pytest.mark.parametrize("stage", [4, 5])
def test_stage_4_5_mix_is_40_60(stage):
    counts = _tier_counts(stage)
    assert set(counts) <= {2, 3}
    assert 0.32 <= counts[2] / 400 <= 0.48


@pytest.mark.parametrize("seed", range(20))
def test_prefers_unseen_question(seed):
    seen = ["w-meaning-0", "w-fill_blank-0", "w-fill_blank-1"]
    payload = build_session(inputs(["w"], progress=[prog("w", 1, TODAY, seen_ids=seen)], seed=seed))
    assert main_q(payload, "w")["id"] == "w-pick_word-0"


@pytest.mark.parametrize("seed", range(20))
def test_falls_back_to_adjacent_tier_when_tier_all_seen(seed):
    seen = ["w-meaning-0", "w-pick_word-0", "w-fill_blank-0", "w-fill_blank-1"]
    payload = build_session(inputs(["w"], progress=[prog("w", 1, TODAY, seen_ids=seen)], seed=seed))
    chosen = main_q(payload, "w")
    assert chosen["tier"] == 2
    assert chosen["id"] not in seen


@pytest.mark.parametrize("seed", range(20))
def test_falls_back_when_tier_is_empty(seed):
    no_tier1 = pool("a", ["usage", "scenario", "spell_it"])
    no_tier3 = pool("b", ["meaning", "usage", "synonym"])
    payload = build_session(inputs(
        ["a", "b"], progress=[prog("a", 0, TODAY), prog("b", 5, TODAY)],
        pools={"a": no_tier1, "b": no_tier3}, seed=seed,
    ))
    assert main_q(payload, "a")["tier"] == 2
    assert main_q(payload, "b")["tier"] == 2


@pytest.mark.parametrize("seed", range(20))
def test_tier_2_fallback_follows_the_stage_mix(seed):
    no_tier2 = pool("a", ["meaning", "pick_word", "spell_it", "word_parts"])
    payload = build_session(inputs(
        ["a", "b"], progress=[prog("a", 2, TODAY), prog("b", 4, TODAY)],
        pools={"a": no_tier2, "b": pool("b", ["meaning", "pick_word", "spell_it", "word_parts"])}, seed=seed,
    ))
    assert main_q(payload, "a")["tier"] == 1  # stage 2-3 mix is tiers 1/2
    assert main_q(payload, "b")["tier"] == 3  # stage 4-5 mix is tiers 2/3


def test_least_recently_seen_when_nothing_unseen_nearby():
    small = pool("w", ["meaning", "pick_word", "usage", "spell_it"])
    seen = ["w-usage-0", "w-pick_word-0", "w-spell_it-0", "w-meaning-0"]
    payload = build_session(inputs(["w"], progress=[prog("w", 1, TODAY, seen_ids=seen)], pools={"w": small}))
    assert main_q(payload, "w")["id"] == "w-usage-0"
    seen_again = seen + ["w-usage-0"]  # usage was seen again most recently
    payload = build_session(inputs(["w"], progress=[prog("w", 1, TODAY, seen_ids=seen_again)], pools={"w": small}))
    assert main_q(payload, "w")["id"] == "w-pick_word-0"


def test_never_seen_question_in_far_tier_beats_seen_ones():
    small = pool("w", ["meaning", "usage", "word_parts"])
    seen = ["w-meaning-0", "w-usage-0"]
    payload = build_session(inputs(["w"], progress=[prog("w", 1, TODAY, seen_ids=seen)], pools={"w": small}))
    assert main_q(payload, "w")["id"] == "w-word_parts-0"


# ---------- reserves ----------

def _ids(qs: list[dict]) -> list[str]:
    return [x["id"] for x in qs]


@pytest.mark.parametrize("seed", range(15))
@pytest.mark.parametrize("stage", [0, 2, 5])
def test_reserves_with_large_pool(seed, stage):
    big = pool("w", FULL_MIX + ["meaning", "pick_word", "usage", "synonym"])
    payload = build_session(inputs(["w"], progress=[prog("w", stage, TODAY)], pools={"w": big}, seed=seed))
    main_id = main_q(payload, "w")["id"]
    res = payload["words"]["w"]["reserves"]
    set1, set2 = res["checks"]
    assert len(set1) == 3 and len(set2) == 3
    assert main_id not in _ids(set1) + _ids(set2)
    assert not set(_ids(set1)) & set(_ids(set2))
    assert len({x["type"] for x in set1}) >= 2 and len({x["type"] for x in set2}) >= 2
    assert len(res["reasks"]) == 2
    assert not set(_ids(res["reasks"])) & (set(_ids(set1)) | set(_ids(set2)) | {main_id})
    for x in set1 + set2 + res["reasks"]:
        assert x["tier"] in (1, 2)
        assert set(x) == PUBLIC_KEYS


def test_reserves_drop_reasks_first_with_exactly_six_others():
    seven = pool("w", ["meaning", "pick_word", "fill_blank", "usage", "scenario", "synonym", "antonym", "spell_it"])
    payload = build_session(inputs(["w"], progress=[prog("w", 0, TODAY)], pools={"w": seven}))
    main_id = main_q(payload, "w")["id"]
    set1, set2 = payload["words"]["w"]["reserves"]["checks"]
    assert len(set1) == len(set2) == 3
    assert not set(_ids(set1)) & set(_ids(set2))
    assert main_id not in _ids(set1) + _ids(set2)
    assert payload["words"]["w"]["reserves"]["reasks"] == []


def test_reserves_sets_overlap_before_repeating_main():
    six = pool("w", ["meaning", "pick_word", "fill_blank", "usage", "synonym", "spell_it"])
    payload = build_session(inputs(["w"], progress=[prog("w", 0, TODAY)], pools={"w": six}))
    main_id = main_q(payload, "w")["id"]
    res = payload["words"]["w"]["reserves"]
    set1, set2 = res["checks"]
    others = {"w-meaning-0", "w-pick_word-0", "w-fill_blank-0", "w-usage-0", "w-synonym-0"} - {main_id}
    assert len(set1) == len(set2) == 3
    assert len(set(_ids(set1))) == 3 and len(set(_ids(set2))) == 3
    assert main_id not in _ids(set1) + _ids(set2)
    assert set(_ids(set1)) | set(_ids(set2)) == others  # every other question is used
    assert res["reasks"] == []


def test_reserves_with_legacy_pool_of_five():
    legacy = pool("w", ["meaning", "pick_word", "fill_blank", "spell_it", "synonym"])
    for seed in range(20):
        payload = build_session(inputs(["w"], progress=[prog("w", 0, TODAY)], pools={"w": legacy}, seed=seed))
        main_id = main_q(payload, "w")["id"]
        assert main_q(payload, "w")["tier"] == 1
        res = payload["words"]["w"]["reserves"]
        set1, set2 = res["checks"]
        expected = {"w-meaning-0", "w-pick_word-0", "w-fill_blank-0", "w-synonym-0"} - {main_id}
        assert set(_ids(set1)) == expected and set(_ids(set2)) == expected
        assert len(_ids(set1)) == 3 and len(_ids(set2)) == 3
        assert res["reasks"] == []


def test_reserves_with_tiny_four_question_pool():
    tiny = pool("w", ["meaning", "pick_word", "fill_blank", "spell_it"])
    for seed in range(20):
        payload = build_session(inputs(["w"], progress=[prog("w", 0, TODAY)], pools={"w": tiny}, seed=seed))
        main_id = main_q(payload, "w")["id"]
        res = payload["words"]["w"]["reserves"]
        set1, set2 = res["checks"]
        tier1 = {"w-meaning-0", "w-pick_word-0", "w-fill_blank-0"}
        # Only 2 other tier-1/2 questions exist, so the main question fills the third slot (it comes last).
        assert set(_ids(set1)) == tier1 and set(_ids(set2)) == tier1
        assert _ids(set1)[-1] == main_id
        assert all(x["tier"] == 1 for x in set1 + set2)
        assert res["reasks"] == []


def test_reserves_when_tier_1_2_pool_is_smaller_than_a_set():
    two = pool("w", ["meaning", "pick_word", "spell_it"])
    payload = build_session(inputs(["w"], progress=[prog("w", 4, TODAY)], pools={"w": two}))
    set1, set2 = payload["words"]["w"]["reserves"]["checks"]
    assert sorted(_ids(set1)) == ["w-meaning-0", "w-pick_word-0"]
    assert sorted(_ids(set2)) == ["w-meaning-0", "w-pick_word-0"]


# ---------- payload ----------

def test_payload_shape():
    words = ["brave", "calm"]
    contents = {"brave": content("brave", image_key="images/6-8/brave-v1.webp", source="legacy"), "calm": content("calm")}
    progress = [prog("brave", 3, TODAY, last="2026-10-01").model_copy(
        update={"graded_today": 2, "last_graded_at": "2026-10-01T18:00:00Z"})]
    payload = build_session(inputs(words, progress=progress, contents=contents, minutes=20))
    json.dumps(payload)  # JSON-serializable
    assert set(payload) == {"mode", "local_date", "settings", "queue", "words", "empty_reason", "preparing"}
    assert payload["mode"] == "normal" and payload["local_date"] == TODAY
    assert payload["settings"] == {
        "session_minutes": 20,
        "break_reminder": True,
        "break_message": "Take a 10-minute break — look at something far away.",
    }
    for it in payload["queue"]:
        if it["kind"] == "intro":
            assert set(it) == {"kind", "word"}
        else:
            assert set(it) == {"kind", "word", "question"}
            assert set(it["question"]) == PUBLIC_KEYS
    assert list(payload["words"]) == list(dict.fromkeys(it["word"] for it in payload["queue"]))
    for w, entry in payload["words"].items():
        assert set(entry) == {"card", "image_key", "source", "stage", "last_graded_on", "graded_today",
                              "last_graded_at", "reserves"}
        assert entry["card"] == contents[w].card.model_dump(mode="json")
        assert set(entry["reserves"]) == {"reasks", "checks"}
        assert len(entry["reserves"]["checks"]) == 2
    b, c = payload["words"]["brave"], payload["words"]["calm"]
    assert (b["image_key"], b["source"], b["stage"], b["last_graded_on"]) == ("images/6-8/brave-v1.webp", "legacy", 3, "2026-10-01")
    assert (c["image_key"], c["source"], c["stage"], c["last_graded_on"]) == (None, "ai", 0, None)
    # the browser's star mirror (web/js/srs.js) needs the daily count and the last counted time
    assert (b["graded_today"], b["last_graded_at"]) == (2, "2026-10-01T18:00:00Z")
    assert (c["graded_today"], c["last_graded_at"]) == (0, None)
    assert payload["empty_reason"] is None
    assert payload["preparing"] == {"ready": 2, "total": 2}


def test_empty_reason_nothing_due():
    words = ["a", "b"]
    progress = [prog("a", 2, "2026-10-09"), prog("b", 3, "2026-10-12")]
    payload = build_session(inputs(words, progress=progress))
    assert payload["queue"] == [] and payload["words"] == {}
    assert payload["empty_reason"] == "nothing_due"
    assert payload["preparing"] == {"ready": 2, "total": 2}


def test_empty_reason_preparing():
    words = ["a", "b", "c"]
    contents = {"a": content("a"), "b": content("b", status="pending", with_card=False), "c": content("c", status="failed")}
    progress = [prog("a", 2, "2026-10-09")]
    payload = build_session(inputs(words, progress=progress, contents=contents))
    assert payload["queue"] == []
    assert payload["empty_reason"] == "preparing"
    assert payload["preparing"] == {"ready": 1, "total": 3}


def test_empty_reason_practice_with_no_shaky_words():
    payload = build_session(inputs(["a"], progress=[prog("a", 5, "2026-11-01")], mode="practice"))
    assert payload["queue"] == [] and payload["empty_reason"] == "nothing_due"


def test_tiny_and_exhausted_lists_still_build():
    """Review Focus 4: a one-word list (new or due) gives a short queue; an all-mastered list gives a reason."""
    new_solo = build_session(inputs(["solo"]))
    assert [it["kind"] for it in new_solo["queue"]] == ["intro"]  # first question too close: next session
    assert new_solo["empty_reason"] is None and list(new_solo["words"]) == ["solo"]
    assert new_solo["words"]["solo"]["reserves"] == NO_RESERVES
    json.dumps(new_solo)
    due_solo = build_session(inputs(["solo"], progress=[prog("solo", 2, TODAY)]))
    assert [it["kind"] for it in due_solo["queue"]] == ["question"]
    words = ["a", "b", "c"]
    mastered = [prog(w, 5, "2026-11-01") for w in words]
    for mode in ("normal", "practice"):
        payload = build_session(inputs(words, progress=mastered, mode=mode))
        assert payload["queue"] == [] and payload["words"] == {}
        assert payload["empty_reason"] == "nothing_due"
        assert payload["preparing"] == {"ready": 3, "total": 3}


def test_same_seed_same_payload_and_inputs_untouched():
    reviews = [f"r{i}" for i in range(8)]
    news = ["n0", "n1", "n2"]
    progress = [prog(w, i % 6, TODAY, seen_ids=[f"{w}-meaning-0"]) for i, w in enumerate(reviews)]
    inp = inputs(reviews + news, progress=progress, seed=1234)
    before = copy.deepcopy(inp.progress)
    first = build_session(inp)
    second = build_session(inputs(reviews + news, progress=progress, seed=1234))
    assert first == second
    assert inp.progress == before


# ---------- top-ups ----------

def test_topup_words_75_percent_rule():
    eight = ["meaning", "pick_word", "fill_blank", "usage", "scenario", "synonym", "antonym", "spell_it"]
    words = ["hit", "miss", "notdue", "oldids", "mixed"]
    pools = {w: pool(w, eight) for w in words}
    pools["mixed"] = pool("mixed", eight[:4]) + pool("mixed", eight[4:], verified=False)

    def ids(w: str, n: int) -> list[str]:
        return [x.id for x in pools[w][:n]]

    progress = [
        prog("hit", 2, TODAY, seen_ids=ids("hit", 6)),                      # 6/8 = 75% → top up
        prog("miss", 2, TODAY, seen_ids=ids("miss", 5)),                    # 5/8 → no
        prog("notdue", 2, "2026-10-20", seen_ids=ids("notdue", 8)),         # not a session word
        prog("oldids", 2, TODAY, seen_ids=ids("oldids", 3) + ["x1", "x2", "x3"]),  # stale ids don't count
        prog("mixed", 2, TODAY, seen_ids=ids("mixed", 3)),                  # 3/4 verified → top up
    ]
    inp = inputs(words, progress=progress, pools=pools)
    assert topup_words(inp) == ["hit", "mixed"]
    assert topup_words(inputs(["fresh"])) == []  # new words have seen nothing

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app import clock
from app.ai.content import ContentGenerator
from app.ai.images.base import ImageProvider
from app.ai.images.process import image_key
from app.ai.llm import DailyCapReached, LLMResult, RateLimited
from app.ai.schemas import ANSWER_CHECK, LEARN_CARD, QUESTION_BATCH
from app.config import Settings
from app.jobs import BACKOFF_S, LEASE_S, MAX_ATTEMPTS, Worker
from app.models import POOL_CAP, WordContent
from app.storage.sqlite_repo import SqliteRepository
from app.triggers import ensure_generation, maybe_enqueue_topup, regenerate
from tests.fakes import FakeImageProvider, FakeLLM, MemoryBlobStore
from tests.gen_helpers import BAND, BATCH_A, BATCH_B, WORD, batch, check_all_match, make_card, save_ready_content

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
LEARN_KEY = f"learn:{BAND}:{WORD}"
QUESTIONS_KEY = f"questions:{BAND}:{WORD}"
IMAGE_KEY = f"image:{BAND}:{WORD}"
TOPUP_KEY = f"topup:{BAND}:{WORD}"
BAD_CARD = {"pos": "adjective"}  # missing required fields → parse_card raises InvalidOutput


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class BrokenImages(ImageProvider):
    name = "broken"

    async def generate(self, prompt: str) -> bytes:
        raise RuntimeError("image service down")


class GateLLM:
    """Blocks inside chat_json until `release` is set, so the job stays in flight; `started` is set on entry."""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat_json(
        self, *, name: str, schema: dict, system: str, user: str, reasoning_effort: str | None = None
    ) -> LLMResult:
        self.started.set()
        await self.release.wait()
        return LLMResult(data=dict(self.data), usage={"completion_tokens": 10}, finish_reason="stop")


class SlowLLM:
    """Answers every call with the same card after a short await, recording how many calls overlap."""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.active = 0
        self.max_active = 0

    async def chat_json(
        self, *, name: str, schema: dict, system: str, user: str, reasoning_effort: str | None = None
    ) -> LLMResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.05)
        finally:
            self.active -= 1
        return LLMResult(data=dict(self.data), usage={"completion_tokens": 10}, finish_reason="stop")


@pytest.fixture
def repo(tmp_path):
    return SqliteRepository(tmp_path / "t.db")


@pytest.fixture
def clk():
    return Clock()


def make_worker(repo, clk, tmp_path, *, llm=None, images=None, blobs=None, limit=2000, concurrency=3):
    settings = Settings(_env_file=None, data_dir=tmp_path, gen_concurrency=concurrency, ai_daily_call_limit=limit)
    # wired like app.main, so the doubles below also see the default reasoning_effort on the card and batch calls
    generator = None if llm is None else ContentGenerator(
        llm, model_name="fake-model", generation_reasoning_effort=settings.llm_reasoning_effort or None)
    return Worker(repo=repo, blobs=blobs if blobs is not None else MemoryBlobStore(), generator=generator,
                  image_provider=images, settings=settings, now_fn=clk)


def first_time_script(card=None):
    card = card or make_card()
    return {
        LEARN_CARD: [card.model_dump()],
        QUESTION_BATCH: [batch(BATCH_A)],
        ANSWER_CHECK: [check_all_match(card, BATCH_A)],
    }


async def drain(worker, limit=20):
    count = 0
    while await worker.run_one():
        count += 1
        assert count <= limit, "worker never ran out of jobs"
    return count


async def test_first_time_chain_reaches_ready_with_image(repo, clk, tmp_path):
    llm = FakeLLM(first_time_script())
    blobs = MemoryBlobStore()
    worker = make_worker(repo, clk, tmp_path, llm=llm, images=FakeImageProvider(), blobs=blobs)
    ensure_generation(repo, BAND, WORD)

    assert await drain(worker) == 3  # learn → questions → image

    content = repo.get_content(BAND, WORD)
    assert (content.status, content.error, content.content_version) == ("ready", "", 1)
    assert content.card.short_def == make_card().short_def
    pool = repo.get_pool(BAND, WORD)
    assert len(pool) == len(BATCH_A)
    assert all(q.verified and q.content_version == 1 for q in pool)
    assert sorted(q.type for q in pool) == sorted(item["type"] for item in BATCH_A)
    assert (content.image_status, content.image_key) == ("ready", image_key(BAND, WORD, 1))
    assert blobs.exists(content.image_key)
    assert repo.job_counts() == {"pending": 0, "running": 0, "done": 3, "failed": 0}
    assert [call["name"] for call in llm.calls] == [LEARN_CARD, QUESTION_BATCH, ANSWER_CHECK]


async def test_invalid_card_three_times_marks_content_failed_with_backoff(repo, clk, tmp_path):
    worker = make_worker(repo, clk, tmp_path, llm=FakeLLM({LEARN_CARD: [BAD_CARD, BAD_CARD, BAD_CARD]}))
    ensure_generation(repo, BAND, WORD)

    assert await worker.run_one()
    job = repo.get_job(LEARN_KEY)
    assert (job.status, job.attempts, job.not_before) == ("pending", 1, "2026-10-07T12:00:05Z")
    assert job.last_error.startswith("InvalidOutput")
    assert not await worker.run_one()  # still backing off

    clk.advance(BACKOFF_S[0])
    assert await worker.run_one()
    job = repo.get_job(LEARN_KEY)
    assert (job.status, job.attempts, job.not_before) == ("pending", 2, "2026-10-07T12:00:35Z")

    clk.advance(BACKOFF_S[1])
    assert await worker.run_one()
    job = repo.get_job(LEARN_KEY)
    assert (job.status, job.attempts) == ("failed", 3)
    content = repo.get_content(BAND, WORD)
    assert (content.status, content.card) == ("failed", None)
    assert content.error.startswith("InvalidOutput")
    assert repo.get_job(QUESTIONS_KEY) is None
    assert not await worker.run_one()


@pytest.mark.parametrize("error, until", [
    (RateLimited("429 slow down", retry_after=20), "2026-10-07T12:00:20Z"),
    (RateLimited("429 slow down"), "2026-10-07T12:01:00Z"),
    (RateLimited("429 daily quota", daily=True), "2026-10-08T00:00:00Z"),
    (DailyCapReached("AI daily call limit reached"), "2026-10-08T00:00:00Z"),
], ids=["retry-after", "default-60s", "daily-quota", "daily-cap"])
async def test_rate_limits_defer_without_consuming_an_attempt(repo, clk, tmp_path, error, until):
    worker = make_worker(repo, clk, tmp_path, llm=FakeLLM({LEARN_CARD: [error]}))
    ensure_generation(repo, BAND, WORD)

    assert await worker.run_one()

    job = repo.get_job(LEARN_KEY)
    assert (job.status, job.attempts, job.not_before) == ("pending", 0, until)
    assert repo.get_content(BAND, WORD).status == "pending"


@pytest.mark.parametrize("retry_after, until", [
    (1e18, "2026-10-07T13:00:00Z"),  # used to raise OverflowError and leave the job running
    (3e11, "2026-10-07T13:00:00Z"),  # used to raise "date value out of range", same result
    (1e9, "2026-10-07T13:00:00Z"),   # used to defer the job to 2058
    (0, "2026-10-07T12:00:00Z"),     # Retry-After: 0 means retry now, not the 60 s default
], ids=["huge", "out-of-range-date", "decades", "zero"])
async def test_retry_after_is_capped_at_one_hour_and_zero_means_retry_now(repo, clk, tmp_path, retry_after, until):
    error = RateLimited("429 slow down", retry_after=retry_after)
    worker = make_worker(repo, clk, tmp_path, llm=FakeLLM({LEARN_CARD: [error]}))
    ensure_generation(repo, BAND, WORD)

    assert await worker.run_one()  # no exception escapes the worker

    job = repo.get_job(LEARN_KEY)
    assert (job.status, job.attempts, job.not_before) == ("pending", 0, until)
    assert repo.get_content(BAND, WORD).status == "pending"


async def test_daily_call_limit_defers_ai_jobs_until_next_utc_midnight(repo, clk, tmp_path):
    llm = FakeLLM({})
    worker = make_worker(repo, clk, tmp_path, llm=llm, limit=2)
    on_request = worker.make_on_request()
    on_request()
    on_request()
    with pytest.raises(DailyCapReached):
        on_request()
    assert repo.get_ai_calls("2026-10-07") == 3

    ensure_generation(repo, BAND, WORD)
    assert await worker.run_one()

    job = repo.get_job(LEARN_KEY)
    assert (job.status, job.attempts, job.not_before) == ("pending", 0, "2026-10-08T00:00:00Z")
    assert llm.calls == []


async def test_image_failure_marks_only_the_image_failed(repo, clk, tmp_path):
    worker = make_worker(repo, clk, tmp_path, llm=FakeLLM(first_time_script()), images=BrokenImages())
    ensure_generation(repo, BAND, WORD)

    assert await drain(worker) == 3  # learn, questions, image attempt 1
    clk.advance(BACKOFF_S[0])
    assert await worker.run_one()
    clk.advance(BACKOFF_S[1])
    assert await worker.run_one()

    content = repo.get_content(BAND, WORD)
    assert (content.status, content.image_status, content.image_key) == ("ready", "failed", None)
    job = repo.get_job(IMAGE_KEY)
    assert (job.status, job.attempts) == ("failed", 3)
    assert "image service down" in job.last_error


async def test_failed_picture_redraw_keeps_the_earlier_picture(repo, clk, tmp_path):
    old = image_key(BAND, WORD, 1)
    save_ready_content(repo)
    assert repo.set_image(BAND, WORD, old, "ready", 1)
    regenerate(repo, BAND, WORD, "image")  # Parent: ↻ Regenerate… → Picture only
    worker = make_worker(repo, clk, tmp_path, images=BrokenImages())

    for wait in (0, BACKOFF_S[0], BACKOFF_S[1]):
        clk.advance(wait)
        assert await worker.run_one()

    content = repo.get_content(BAND, WORD)
    assert (content.image_status, content.image_key) == ("ready", old)  # learners keep seeing the earlier picture
    job = repo.get_job(IMAGE_KEY)
    assert (job.status, job.attempts) == ("failed", 3)
    assert job.last_error == "RuntimeError: image service down"  # the failure is recorded on the job only


async def test_pool_shortfall_consumes_an_attempt_and_keeps_verified_questions(repo, clk, tmp_path):
    card = make_card()
    first, second = BATCH_A[:3], BATCH_A[3:]  # 3 tier-1 questions, then the 3 tier-2 ones
    llm = FakeLLM({
        LEARN_CARD: [card.model_dump()],
        QUESTION_BATCH: [batch(first), batch(second)],
        ANSWER_CHECK: [check_all_match(card, first),
                       check_all_match(card, second, existing_prompts=[item["prompt"] for item in first])],
    })
    worker = make_worker(repo, clk, tmp_path, llm=llm)  # no image provider
    ensure_generation(repo, BAND, WORD)

    assert await worker.run_one()  # learn
    assert await worker.run_one()  # questions attempt 1: no tier-2 question yet
    job = repo.get_job(QUESTIONS_KEY)
    assert (job.status, job.attempts, job.not_before) == ("pending", 1, "2026-10-07T12:00:05Z")
    assert job.last_error.startswith("PoolShortfall")
    assert len(repo.get_pool(BAND, WORD)) == 3
    assert repo.get_content(BAND, WORD).status == "pending"

    clk.advance(BACKOFF_S[0])
    assert await worker.run_one()  # attempt 2 tops the pool up to the minimum
    assert repo.get_content(BAND, WORD).status == "ready"
    assert len(repo.get_pool(BAND, WORD)) == 6

    assert await worker.run_one()  # image job with IMAGE_PROVIDER=none
    content = repo.get_content(BAND, WORD)
    assert (content.image_status, content.image_key) == ("none", None)
    assert repo.job_counts() == {"pending": 0, "running": 0, "done": 3, "failed": 0}


def trickle_script(card, batches):
    """A Learn card, then one questions attempt per entry of `batches` (lists of BATCH_A items).

    Each attempt's questions all verify, so the pool grows by len(batch) per attempt."""
    script = {LEARN_CARD: [card.model_dump()], QUESTION_BATCH: [], ANSWER_CHECK: []}
    seen: list[str] = []
    for items in batches:
        script[QUESTION_BATCH].append(batch(items))
        script[ANSWER_CHECK].append(check_all_match(card, items, existing_prompts=list(seen)))
        seen.extend(item["prompt"] for item in items)
    return script


async def test_pool_shortfall_gets_five_attempts_with_growing_backoff(repo, clk, tmp_path):
    card = make_card()
    llm = FakeLLM(trickle_script(card, [[item] for item in BATCH_A[:5]]))  # one verified question per attempt
    worker = make_worker(repo, clk, tmp_path, llm=llm)
    ensure_generation(repo, BAND, WORD)
    assert await worker.run_one()  # learn

    for attempt, wait in enumerate((5, 30, 60, 120), start=1):
        assert await worker.run_one()  # questions attempt `attempt`: still below the minimum
        job = repo.get_job(QUESTIONS_KEY)
        assert (job.status, job.attempts) == ("pending", attempt)
        assert job.not_before == clock.iso(clk.now + timedelta(seconds=wait))
        assert job.last_error.startswith("PoolShortfall")
        assert len(repo.get_pool(BAND, WORD)) == attempt  # every verified question is kept
        assert repo.get_content(BAND, WORD).status == "pending"
        clk.advance(wait - 1)
        assert not await worker.run_one()  # one second early: still backing off
        clk.advance(1)

    assert await worker.run_one()  # attempt 5 is the last one
    job = repo.get_job(QUESTIONS_KEY)
    assert (job.status, job.attempts) == ("failed", 5)
    assert job.last_error.startswith("PoolShortfall")
    content = repo.get_content(BAND, WORD)
    assert (content.status, content.card is not None) == ("failed", True)
    assert content.error.startswith("PoolShortfall")
    assert len(repo.get_pool(BAND, WORD)) == 5
    assert len(llm.calls_for(QUESTION_BATCH)) == 5
    assert repo.get_job(IMAGE_KEY) is None
    assert not await worker.run_one()


async def test_shortfall_text_mentions_a_next_attempt_only_when_one_is_scheduled(repo, clk, tmp_path):
    # The parent reads this text: after the last attempt there is no "next attempt" to keep anything for.
    card = make_card()
    worker = make_worker(repo, clk, tmp_path, llm=FakeLLM(trickle_script(card, [[item] for item in BATCH_A[:5]])))
    ensure_generation(repo, BAND, WORD)
    assert await worker.run_one()  # learn

    for wait in (5, 30, 60, 120):
        assert await worker.run_one()
        job = repo.get_job(QUESTIONS_KEY)
        assert job.status == "pending"
        assert job.last_error.endswith("; kept for next attempt")
        clk.advance(wait)

    assert await worker.run_one()  # attempt 5 is final
    job = repo.get_job(QUESTIONS_KEY)
    assert job.status == "failed"
    expected = "PoolShortfall: only 5 verified questions (need 6 incl. 2 tier-1 and 1 tier-2)"
    assert job.last_error == expected
    assert repo.get_content(BAND, WORD).error == expected


async def test_pool_built_over_five_attempts_is_ready_on_the_last_one(repo, clk, tmp_path):
    card = make_card()
    batches = [[item] for item in BATCH_A[:4]] + [BATCH_A[4:]]  # 1 + 1 + 1 + 1 + 2 verified questions
    llm = FakeLLM(trickle_script(card, batches))
    worker = make_worker(repo, clk, tmp_path, llm=llm)  # no image provider
    ensure_generation(repo, BAND, WORD)
    assert await worker.run_one()  # learn

    for wait in (5, 30, 60, 120):
        assert await worker.run_one()
        clk.advance(wait)
    assert await worker.run_one()  # attempt 5 tops the pool up to the minimum

    content = repo.get_content(BAND, WORD)
    assert (content.status, content.error) == ("ready", "")
    pool = repo.get_pool(BAND, WORD)
    assert [q.prompt for q in pool] == [item["prompt"] for item in BATCH_A]  # nothing from earlier attempts was lost
    assert repo.get_job(QUESTIONS_KEY).status == "done"
    last_request = llm.calls_for(QUESTION_BATCH)[-1]["user"]
    assert all(item["prompt"] in last_request for item in BATCH_A[:4])  # the model is told what it already wrote
    assert await worker.run_one()  # image job with IMAGE_PROVIDER=none
    assert repo.job_counts() == {"pending": 0, "running": 0, "done": 3, "failed": 0}


async def test_invalid_questions_still_fail_after_three_attempts(repo, clk, tmp_path):
    bad_batch = {"questions": "not a list"}  # parse_questions raises InvalidOutput
    llm = FakeLLM({LEARN_CARD: [make_card().model_dump()], QUESTION_BATCH: [bad_batch] * 3})
    worker = make_worker(repo, clk, tmp_path, llm=llm)
    ensure_generation(repo, BAND, WORD)
    assert await worker.run_one()  # learn

    for attempt, wait in enumerate((5, 30), start=1):
        assert await worker.run_one()
        job = repo.get_job(QUESTIONS_KEY)
        assert (job.status, job.attempts) == ("pending", attempt)
        assert job.not_before == clock.iso(clk.now + timedelta(seconds=wait))
        assert job.last_error.startswith("InvalidOutput")
        clk.advance(wait)

    assert await worker.run_one()  # only a pool shortfall earns more than 3 attempts
    job = repo.get_job(QUESTIONS_KEY)
    assert (job.status, job.attempts) == ("failed", 3)
    content = repo.get_content(BAND, WORD)
    assert (content.status, content.error.startswith("InvalidOutput")) == ("failed", True)
    assert len(llm.calls_for(QUESTION_BATCH)) == 3
    assert not await worker.run_one()


BAD_BATCH = {"questions": "not a list"}  # parse_questions raises InvalidOutput (no answer check is made)


async def test_mixed_failures_two_shortfalls_then_invalid_output_is_final_at_attempt_3(repo, clk, tmp_path):
    # The attempt budget is judged by the failure that just happened: invalid output allows 3 attempts in total.
    card = make_card()
    script = trickle_script(card, [[BATCH_A[0]], [BATCH_A[1]]])
    script[QUESTION_BATCH].append(BAD_BATCH)
    worker = make_worker(repo, clk, tmp_path, llm=FakeLLM(script))
    ensure_generation(repo, BAND, WORD)
    assert await worker.run_one()  # learn

    for attempt, wait in enumerate((5, 30), start=1):
        assert await worker.run_one()  # shortfall
        job = repo.get_job(QUESTIONS_KEY)
        assert (job.status, job.attempts, job.last_error.startswith("PoolShortfall")) == ("pending", attempt, True)
        clk.advance(wait)
    assert await worker.run_one()  # invalid output at attempt 3: 3 > len(BACKOFF_S), so it is final

    job = repo.get_job(QUESTIONS_KEY)
    assert (job.status, job.attempts, job.last_error.startswith("InvalidOutput")) == ("failed", 3, True)
    assert repo.get_content(BAND, WORD).status == "failed"
    assert not await worker.run_one()


async def test_mixed_failures_two_invalid_outputs_then_shortfall_retries_after_60s(repo, clk, tmp_path):
    card = make_card()
    llm = FakeLLM({
        LEARN_CARD: [card.model_dump()],
        QUESTION_BATCH: [BAD_BATCH, BAD_BATCH, batch([BATCH_A[0]])],
        ANSWER_CHECK: [check_all_match(card, [BATCH_A[0]])],
    })
    worker = make_worker(repo, clk, tmp_path, llm=llm)
    ensure_generation(repo, BAND, WORD)
    assert await worker.run_one()  # learn

    for wait in (5, 30):
        assert await worker.run_one()  # invalid output
        clk.advance(wait)
    assert await worker.run_one()  # shortfall at attempt 3: 3 <= len(SHORTFALL_BACKOFF_S), so it is retried

    job = repo.get_job(QUESTIONS_KEY)
    assert (job.status, job.attempts) == ("pending", 3)
    assert job.not_before == clock.iso(clk.now + timedelta(seconds=60))
    assert job.last_error.startswith("PoolShortfall") and job.last_error.endswith("; kept for next attempt")
    assert repo.get_content(BAND, WORD).status == "pending"


async def test_topup_fills_the_pool_only_up_to_the_cap(repo, clk, tmp_path):
    card = make_card()
    save_ready_content(repo, n_questions=POOL_CAP - 2)
    existing = [q.prompt for q in repo.get_pool(BAND, WORD)]
    llm = FakeLLM({
        QUESTION_BATCH: [batch(BATCH_B)],
        ANSWER_CHECK: [check_all_match(card, BATCH_B, existing_prompts=existing)],
    })
    worker = make_worker(repo, clk, tmp_path, llm=llm)
    assert maybe_enqueue_topup(repo, BAND, WORD, "2026-10-07")

    assert await worker.run_one()

    assert len(repo.get_pool(BAND, WORD)) == POOL_CAP  # 6 verified, only 2 fit
    assert repo.get_job(TOPUP_KEY).status == "done"
    assert [call["name"] for call in llm.calls] == [QUESTION_BATCH, ANSWER_CHECK]

    repo.enqueue_job("topup", BAND, WORD, 1, [])  # a top-up that finds the pool full does nothing
    assert await worker.run_one()
    assert len(llm.calls) == 2
    assert len(repo.get_pool(BAND, WORD)) == POOL_CAP
    assert repo.get_job(TOPUP_KEY).status == "done"


async def test_failed_topup_records_the_error_on_the_job_only(repo, clk, tmp_path):
    save_ready_content(repo)
    bad_batch = {"items": []}  # no "questions" key → InvalidOutput
    worker = make_worker(repo, clk, tmp_path, llm=FakeLLM({QUESTION_BATCH: [bad_batch, bad_batch, bad_batch]}))
    assert maybe_enqueue_topup(repo, BAND, WORD, "2026-10-07")

    assert await worker.run_one()
    clk.advance(BACKOFF_S[0])
    assert await worker.run_one()
    clk.advance(BACKOFF_S[1])
    assert await worker.run_one()

    job = repo.get_job(TOPUP_KEY)
    assert (job.status, job.attempts) == ("failed", 3)
    assert job.last_error.startswith("InvalidOutput")
    content = repo.get_content(BAND, WORD)
    assert (content.status, content.error, content.content_version) == ("ready", "", 1)
    assert len(repo.get_pool(BAND, WORD)) == 6


async def test_regenerate_all_keeps_current_version_until_swap(repo, clk, tmp_path):
    save_ready_content(repo)  # v1: original card + 6 questions
    old_ids = {q.id for q in repo.get_pool(BAND, WORD)}
    new_card = make_card(short_def="spends money only on what really matters")
    blobs = MemoryBlobStore()
    worker = make_worker(repo, clk, tmp_path, llm=FakeLLM(first_time_script(new_card)),
                         images=FakeImageProvider(), blobs=blobs)
    regenerate(repo, BAND, WORD, "all")

    assert await worker.run_one()  # learn → draft v2
    content = repo.get_content(BAND, WORD)
    assert (content.status, content.content_version, content.card.short_def) == ("ready", 1, make_card().short_def)
    assert (content.draft.short_def, content.draft_version) == (new_card.short_def, 2)
    assert {q.id for q in repo.get_pool(BAND, WORD)} == old_ids

    assert await worker.run_one()  # questions v2 → swap
    content = repo.get_content(BAND, WORD)
    assert (content.status, content.content_version, content.card.short_def) == ("ready", 2, new_card.short_def)
    assert (content.draft, content.draft_version) == (None, None)
    pool = repo.get_pool(BAND, WORD)
    assert len(pool) == len(BATCH_A) and all(q.content_version == 2 for q in pool)
    assert not old_ids & {q.id for q in pool}
    assert repo.get_pool(BAND, WORD, 1) == []

    assert await worker.run_one()  # image for v2
    content = repo.get_content(BAND, WORD)
    assert (content.image_status, content.image_key) == ("ready", image_key(BAND, WORD, 2))
    assert blobs.exists(image_key(BAND, WORD, 2))
    assert not await worker.run_one()


async def test_failed_regeneration_leaves_the_current_version_serving(repo, clk, tmp_path):
    save_ready_content(repo)
    worker = make_worker(repo, clk, tmp_path, llm=FakeLLM({LEARN_CARD: [BAD_CARD, BAD_CARD, BAD_CARD]}))
    regenerate(repo, BAND, WORD, "learn")

    assert await worker.run_one()
    clk.advance(BACKOFF_S[0])
    assert await worker.run_one()
    clk.advance(BACKOFF_S[1])
    assert await worker.run_one()

    content = repo.get_content(BAND, WORD)
    assert (content.status, content.content_version, content.card) == ("ready", 1, make_card())
    assert (content.draft, content.draft_version) == (None, None)
    assert content.error.startswith("Regeneration failed: InvalidOutput")
    assert len(repo.get_pool(BAND, WORD)) == 6
    assert repo.get_job(LEARN_KEY).status == "failed"


async def test_failed_regeneration_questions_are_not_reused_by_the_next_regeneration(repo, clk, tmp_path):
    save_ready_content(repo)  # v1: the served card + 6 questions
    card_a = make_card(short_def="card A: careful with money")
    card_b = make_card(short_def="card B: never wastes anything")
    partial = BATCH_A[:3]  # 3 verified questions for card A; the attempts after them fail
    invalid_batch = {"items": []}  # no "questions" key → InvalidOutput
    llm = FakeLLM({
        LEARN_CARD: [card_a.model_dump(), card_b.model_dump()],
        QUESTION_BATCH: [batch(partial), invalid_batch, invalid_batch, batch(BATCH_B)],
        ANSWER_CHECK: [check_all_match(card_a, partial), check_all_match(card_b, BATCH_B)],
    })
    worker = make_worker(repo, clk, tmp_path, llm=llm)  # no image provider

    regenerate(repo, BAND, WORD, "learn")  # regeneration 1: draft v2 = card A, then questions v2
    assert await worker.run_one()  # learn
    assert await worker.run_one()  # questions attempt 1: 3 verified stored, then PoolShortfall
    failed = repo.get_pool(BAND, WORD, 2)
    assert len(failed) == 3
    clk.advance(BACKOFF_S[0])
    assert await worker.run_one()  # attempt 2: invalid output
    clk.advance(BACKOFF_S[1])
    assert await worker.run_one()  # attempt 3: final failure
    content = repo.get_content(BAND, WORD)
    assert (content.draft, content.draft_version, content.content_version) == (None, None, 1)
    assert content.error.startswith("Regeneration failed: InvalidOutput")
    assert repo.get_pool(BAND, WORD, 2) == []  # the failed draft's questions go with it

    regenerate(repo, BAND, WORD, "learn")  # regeneration 2 targets v2 again, now for card B
    assert await drain(worker) == 2  # learn → questions → swap
    content = repo.get_content(BAND, WORD)
    assert (content.content_version, content.card.short_def) == (2, card_b.short_def)
    served = repo.get_pool(BAND, WORD)
    assert len(served) == 6
    assert {q.prompt for q in served} == {item["prompt"] for item in BATCH_B}
    assert not {q.id for q in failed} & {q.id for q in served}


async def test_superseded_draft_questions_are_never_served_by_a_later_regeneration(repo, clk, tmp_path):
    save_ready_content(repo)  # v1: the served card + 6 questions
    card_a, card_b, card_c = (make_card(short_def=s) for s in (
        "card A: careful with money", "card B: never wastes anything", "card C: spends on what matters"))
    stranded_batch = BATCH_A[:3]  # card A's verified questions, stranded when regeneration 2 supersedes v2
    invalid_batch = {"items": []}  # no "questions" key → InvalidOutput
    llm = FakeLLM({
        LEARN_CARD: [card_a.model_dump(), card_b.model_dump(), card_c.model_dump()],
        QUESTION_BATCH: [batch(stranded_batch), invalid_batch, invalid_batch, invalid_batch, batch(BATCH_B)],
        ANSWER_CHECK: [check_all_match(card_a, stranded_batch), check_all_match(card_c, BATCH_B)],
    })
    worker = make_worker(repo, clk, tmp_path, llm=llm)  # no image provider

    regenerate(repo, BAND, WORD, "learn")  # regeneration 1: draft v2 = card A
    assert await worker.run_one()  # learn v2
    assert await worker.run_one()  # questions v2 attempt 1: 3 verified stored, then PoolShortfall
    stranded = repo.get_pool(BAND, WORD, 2)
    assert len(stranded) == 3

    regenerate(repo, BAND, WORD, "learn")  # regeneration 2 supersedes v2: draft v3 = card B
    assert await worker.run_one()  # learn v3; its questions job replaces the v2 one, which never runs again
    for wait in (0, BACKOFF_S[0], BACKOFF_S[1]):  # questions v3 fails three times: final failure
        clk.advance(wait)
        assert await worker.run_one()
    content = repo.get_content(BAND, WORD)
    assert (content.draft, content.draft_version, content.content_version) == (None, None, 1)
    assert len(repo.get_pool(BAND, WORD, 2)) == 3  # card A's questions are still stored under v2

    regenerate(repo, BAND, WORD, "learn")  # regeneration 3 skips v2; v3 holds nothing, so it is free again
    assert repo.get_content(BAND, WORD).draft_version == 3
    assert await drain(worker) == 2  # learn v3 (card C) → questions → swap
    content = repo.get_content(BAND, WORD)
    assert (content.content_version, content.card.short_def) == (3, card_c.short_def)
    served = repo.get_pool(BAND, WORD)
    assert len(served) == 6
    assert {q.prompt for q in served} == {item["prompt"] for item in BATCH_B}  # nothing from card A or B
    assert not {q.id for q in stranded} & {q.id for q in served}
    assert repo.get_pool(BAND, WORD, 2) == []  # the swap removed the stranded questions


async def test_stale_job_is_discarded_without_calling_the_model(repo, clk, tmp_path):
    llm = FakeLLM({})
    worker = make_worker(repo, clk, tmp_path, llm=llm)
    ensure_generation(repo, BAND, WORD)  # learn job for first-time v1 ...
    save_ready_content(repo)  # ... but v1 became ready some other way

    assert await worker.run_one()

    job = repo.get_job(LEARN_KEY)
    assert (job.status, job.attempts) == ("done", 0)
    assert llm.calls == []
    assert repo.get_job(QUESTIONS_KEY) is None


async def test_result_is_discarded_when_the_version_moves_during_generation(repo, clk, tmp_path):
    card = make_card()

    def card_after_version_moved(system: str, user: str) -> dict:
        moved = repo.get_content(BAND, WORD)
        moved.content_version = 2
        repo.save_content(moved)
        return card.model_dump()

    worker = make_worker(repo, clk, tmp_path, llm=FakeLLM({LEARN_CARD: [card_after_version_moved]}))
    ensure_generation(repo, BAND, WORD)

    assert await worker.run_one()

    content = repo.get_content(BAND, WORD)
    assert (content.card, content.content_version) == (None, 2)
    assert repo.get_job(LEARN_KEY).status == "done"
    assert repo.get_job(QUESTIONS_KEY) is None


async def test_newer_request_made_while_a_job_runs_is_not_lost(repo, clk, tmp_path):
    save_ready_content(repo)
    regenerate(repo, BAND, WORD, "learn")  # learn job for draft v2

    def card_while_parent_regenerates_again(system: str, user: str) -> dict:
        regenerate(repo, BAND, WORD, "learn")  # parent clicks again: learn job for v3
        return make_card(short_def="first try").model_dump()

    llm = FakeLLM({LEARN_CARD: [card_while_parent_regenerates_again,
                                make_card(short_def="second try").model_dump()]})
    worker = make_worker(repo, clk, tmp_path, llm=llm)

    assert await worker.run_one()  # v2 result discarded, the v3 request survives
    job = repo.get_job(LEARN_KEY)
    assert (job.status, job.target_version) == ("pending", 3)
    content = repo.get_content(BAND, WORD)
    assert (content.draft, content.draft_version, content.content_version) == (None, 3, 1)

    assert await worker.run_one()  # v3
    content = repo.get_content(BAND, WORD)
    assert (content.draft.short_def, content.draft_version) == ("second try", 3)
    assert repo.get_job(QUESTIONS_KEY).target_version == 3


async def test_without_generator_ai_jobs_wait_and_image_jobs_still_run(repo, clk, tmp_path):
    save_ready_content(repo)
    repo.enqueue_job("image", BAND, WORD, 1, [])
    ensure_generation(repo, BAND, "lucid")
    worker = make_worker(repo, clk, tmp_path)  # AI not configured, IMAGE_PROVIDER=none

    assert await worker.run_one()
    assert await worker.run_one()
    assert not await worker.run_one()

    learn = repo.get_job(f"learn:{BAND}:lucid")
    # 5 minutes, not an hour: a key added later (and a restart, which wakes parked jobs) resumes the word quickly
    assert (learn.status, learn.attempts, learn.not_before) == ("pending", 0, "2026-10-07T12:05:00Z")
    assert repo.get_content(BAND, "lucid").status == "pending"
    assert repo.get_job(IMAGE_KEY).status == "done"
    content = repo.get_content(BAND, WORD)
    assert (content.image_status, content.image_key) == ("none", None)


async def test_expired_lease_is_reclaimed(repo, clk, tmp_path):
    worker = make_worker(repo, clk, tmp_path, llm=FakeLLM({LEARN_CARD: [make_card().model_dump()]}))
    ensure_generation(repo, BAND, WORD)
    crashed = repo.claim_next_job("2026-10-07T12:00:00Z", LEASE_S)  # a worker that died mid-job
    assert crashed.kind == "learn"

    assert not await worker.run_one()  # lease still valid
    clk.advance(LEASE_S + 1)
    assert await worker.run_one()

    content = repo.get_content(BAND, WORD)
    assert content.card is not None and content.status == "pending"
    assert repo.get_job(LEARN_KEY).status == "done"
    questions = repo.get_job(QUESTIONS_KEY)
    assert (questions.status, questions.target_version, questions.chain) == ("pending", 1, ["image"])


def test_job_lease_outlasts_a_worst_case_questions_job():
    # Two model calls x three tries x a 60 s timeout, plus the backoff sleeps, is about 366 s. A 300 s lease let
    # another loop re-claim a job that was still running.
    assert LEASE_S == 900


@pytest.mark.parametrize("used_attempts", [0, MAX_ATTEMPTS - 1], ids=["retry-attempt", "final-attempt"])
async def test_worker_whose_lease_was_reclaimed_cannot_finish_or_fail_the_job(repo, clk, tmp_path, used_attempts):
    llm = GateLLM(BAD_CARD)  # worker A's model call will end in InvalidOutput
    worker = make_worker(repo, clk, tmp_path, llm=llm)
    ensure_generation(repo, BAND, WORD)
    for _ in range(used_attempts):  # final-attempt case: earlier tries failed, so A's failure below is final
        earlier = repo.claim_next_job("2026-10-07T12:00:00Z", LEASE_S)
        assert repo.fail_job(earlier.key, earlier.lease_token, "InvalidOutput: earlier try", "2026-10-07T12:00:00Z")
    first = asyncio.create_task(worker.run_one())  # worker A claims, then blocks inside the model call
    await asyncio.wait_for(llm.started.wait(), timeout=2)
    a_token = repo.get_job(LEARN_KEY).lease_token
    expired = clock.iso(NOW + timedelta(seconds=LEASE_S + 1))  # A claimed at NOW, so its lease ends at NOW + LEASE_S
    reclaimed = repo.claim_next_job(expired, LEASE_S)  # A's lease expired; worker B takes over
    assert reclaimed.key == LEARN_KEY and reclaimed.lease_token != a_token

    llm.release.set()
    assert await asyncio.wait_for(first, timeout=2)  # A's fail_job is a no-op, so no final-failure side effects

    job = repo.get_job(LEARN_KEY)
    assert (job.status, job.lease_token, job.attempts) == ("running", reclaimed.lease_token, used_attempts)
    assert job.last_error == ("InvalidOutput: earlier try" if used_attempts else "")
    content = repo.get_content(BAND, WORD)
    assert (content.status, content.error) == ("pending", "")  # never "failed" while worker B still owns the job
    assert repo.finish_job(LEARN_KEY, a_token) is False
    assert repo.finish_job(LEARN_KEY, reclaimed.lease_token) is True


async def test_paused_worker_claims_nothing(repo, clk, tmp_path):
    worker = make_worker(repo, clk, tmp_path, llm=FakeLLM({}))
    ensure_generation(repo, BAND, WORD)
    worker.paused = True

    assert not await worker.run_one()
    assert repo.get_job(LEARN_KEY).status == "pending"


async def test_pause_and_drain_waits_for_the_in_flight_job(repo, clk, tmp_path):
    llm = GateLLM(make_card().model_dump())
    worker = make_worker(repo, clk, tmp_path, llm=llm)
    assert await worker.pause_and_drain(timeout=0) is True  # nothing in flight: returns at once
    worker.resume()
    ensure_generation(repo, BAND, WORD)

    running = asyncio.create_task(worker.run_one())
    await asyncio.wait_for(llm.started.wait(), timeout=2)
    assert worker.in_flight == 1
    drain = asyncio.create_task(worker.pause_and_drain(timeout=5))
    await asyncio.sleep(0.05)
    assert worker.paused is True and not drain.done()  # still waiting for the slow job

    llm.release.set()
    assert await asyncio.wait_for(drain, timeout=2) is True
    assert running.done() and running.result() is True
    assert worker.in_flight == 0
    assert repo.get_job(LEARN_KEY).status == "done"
    assert not await worker.run_one()  # paused: the chained questions job is not claimed
    assert repo.get_job(QUESTIONS_KEY).status == "pending"

    worker.resume()
    assert worker.paused is False


async def test_pause_cannot_miss_a_job_that_already_passed_the_paused_check(repo, clk, tmp_path):
    worker = make_worker(repo, clk, tmp_path, llm=FakeLLM({LEARN_CARD: [make_card().model_dump()]}))
    ensure_generation(repo, BAND, WORD)
    running = asyncio.create_task(worker.run_one())
    await asyncio.sleep(0)  # one loop turn: run_one passed the paused check and now awaits the claim
    assert worker.in_flight == 1  # counted before its first await

    assert await worker.pause_and_drain(timeout=2) is True
    assert running.done() and running.result() is True  # drain returned only after that job finished
    assert repo.get_job(LEARN_KEY).status == "done"
    worker.resume()


async def test_pause_and_drain_times_out_while_a_job_is_stuck(repo, clk, tmp_path):
    llm = GateLLM(make_card().model_dump())
    worker = make_worker(repo, clk, tmp_path, llm=llm)
    ensure_generation(repo, BAND, WORD)
    running = asyncio.create_task(worker.run_one())
    await asyncio.wait_for(llm.started.wait(), timeout=2)

    assert await worker.pause_and_drain(timeout=0.05) is False
    assert worker.paused is True and worker.in_flight == 1

    llm.release.set()
    assert await asyncio.wait_for(running, timeout=2) is True
    assert await worker.pause_and_drain(timeout=1) is True
    worker.resume()


async def test_run_processes_jobs_concurrently_and_stops(repo, clk, tmp_path):
    llm = SlowLLM(make_card().model_dump())
    worker = make_worker(repo, clk, tmp_path, llm=llm, concurrency=3)
    for band in ("3-5", "6-8", "9-12"):
        repo.save_content(WordContent(word=WORD, band=band))
        repo.enqueue_job("learn", band, WORD, 1, [])

    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    for _ in range(250):
        if repo.job_counts()["done"] == 3:
            break
        await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, timeout=3)

    assert repo.job_counts()["done"] == 3
    assert llm.max_active >= 2
    assert all(repo.get_content(band, WORD).card is not None for band in ("3-5", "6-8", "9-12"))

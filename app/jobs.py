from __future__ import annotations

# Background generation worker (spec §7.2).
#
# Runs GEN_CONCURRENCY asyncio loops inside the FastAPI process. Every Repository/BlobStore call goes through
# asyncio.to_thread so SQLite never blocks the event loop.

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app import clock
from app.ai.content import ContentGenerator, initial_mix, pool_meets_minimum, shortfall_mix, topup_mix
from app.ai.images.base import ImageProvider
from app.ai.images.process import build_image_prompt, image_key, to_webp
from app.ai.llm import DailyCapReached, RateLimited
from app.config import Settings
from app.models import POOL_CAP, Job, Question, WordContent
from app.security import redact
from app.storage.base import BlobStore, Repository

LEASE_S = 300
BACKOFF_S = (5, 30)  # attempt 1 fails → +5 s, attempt 2 fails → +30 s, attempt 3 fails → final
MAX_ATTEMPTS = len(BACKOFF_S) + 1
RATE_LIMIT_DEFAULT_S = 60
NO_GENERATOR_DEFER_S = 3600
IDLE_S = 1.0
TOPUP_MAX = 6
ERROR_MAX_CHARS = 500
AI_KINDS = frozenset({"learn", "questions", "topup"})

log = logging.getLogger("wordquest.jobs")


class StaleJob(Exception):
    """The content moved to another version (version guard): the job's result is discarded."""


class PoolShortfall(Exception):
    """A questions attempt ended below the readiness minimum; it consumes an attempt."""


def next_utc_midnight(now: datetime) -> datetime:
    start_of_day = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day + timedelta(days=1)


def job_mode(content: WordContent | None, job: Job) -> str | None:
    """Return "first" (first-time build), "draft" (regeneration), "active" (image/topup), or None if stale."""
    if content is None:
        return None
    if job.kind in ("image", "topup"):
        return "active" if content.content_version == job.target_version else None
    if content.content_version == job.target_version and content.status != "ready":
        return "first"
    if content.draft_version == job.target_version:
        return "draft"
    return None


def _verified(questions: list[Question]) -> list[Question]:
    return [q for q in questions if q.verified]


class Worker:
    def __init__(self, *, repo: Repository, blobs: BlobStore, generator: ContentGenerator | None,
                 image_provider: ImageProvider | None, settings: Settings,
                 now_fn: Callable[[], datetime] = clock.utc_now) -> None:
        self.repo = repo
        self.blobs = blobs
        self.generator = generator          # None → AI disabled: learn/questions/topup jobs wait
        self.image_provider = image_provider
        self.settings = settings
        self.now_fn = now_fn
        self.paused = False
        self._in_flight = 0                       # jobs currently inside run_one()
        self._idle: asyncio.Event | None = None   # created lazily in the running loop by pause_and_drain()

    # ---------------- public API ----------------

    @property
    def in_flight(self) -> int:
        return self._in_flight

    async def pause_and_drain(self, timeout: float = 30.0) -> bool:
        """Stop claiming new jobs and wait until no job is being processed.

        Returns False if jobs are still in flight after `timeout` seconds (the worker stays paused)."""
        self.paused = True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        while self._in_flight > 0:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            if self._idle is None:
                self._idle = asyncio.Event()
            try:
                await asyncio.wait_for(self._idle.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return self._in_flight == 0
        return True

    def resume(self) -> None:
        self.paused = False

    def make_on_request(self) -> Callable[[], None]:
        """Callback for LLM/image clients: counts every outbound AI request against AI_DAILY_CALL_LIMIT."""
        def on_request() -> None:
            limit = self.settings.ai_daily_call_limit
            count = self.repo.incr_ai_calls(clock.utc_date(self.now_fn()))
            if count > limit:
                raise DailyCapReached(f"AI daily call limit of {limit} requests reached")
        return on_request

    async def run(self, stop: asyncio.Event) -> None:
        loops = max(1, int(self.settings.gen_concurrency))
        await asyncio.gather(*(self._loop(stop) for _ in range(loops)))

    async def run_one(self) -> bool:
        """Claim and process one job. False when paused or nothing is claimable."""
        if self.paused:
            return False
        self._in_flight += 1  # no await since the paused check: pause_and_drain() cannot miss this job
        try:
            return await self._claim_and_process()
        finally:
            self._in_flight -= 1
            if self._in_flight == 0 and self._idle is not None:
                self._idle.set()
                self._idle = None

    async def _claim_and_process(self) -> bool:
        now = self.now_fn()
        job = await self._db(self.repo.claim_next_job, clock.iso(now), LEASE_S)
        if job is None:
            return False
        if job.kind in AI_KINDS and self.generator is None:
            await self._defer(job, clock.add_seconds_iso(now, NO_GENERATOR_DEFER_S))
            return True
        if self._calls_ai(job) and await self._over_daily_limit(now):
            await self._defer(job, clock.iso(next_utc_midnight(now)))
            return True
        try:
            await self._process(job)
        except StaleJob:
            await self._finish(job)
        except DailyCapReached:
            await self._defer(job, clock.iso(next_utc_midnight(self.now_fn())))
        except RateLimited as exc:
            await self._defer(job, self._rate_limited_until(exc))
        except Exception as exc:  # InvalidOutput, TransientError, LLMError, PoolShortfall, anything else
            await self._fail(job, exc)
        return True

    # ---------------- loop ----------------

    async def _loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                worked = await self.run_one()
            except Exception as exc:  # e.g. the database was briefly unavailable
                log.error("job loop error: %s", redact(f"{type(exc).__name__}: {exc}"))
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=IDLE_S)
                except asyncio.TimeoutError:
                    pass

    # ---------------- processing ----------------

    async def _process(self, job: Job) -> None:
        if job.kind == "learn":
            await self._learn(job)
        elif job.kind == "questions":
            await self._questions(job)
        elif job.kind == "image":
            await self._image(job)
        elif job.kind == "topup":
            await self._topup(job)
        else:
            raise StaleJob(f"unknown job kind {job.kind!r}")

    async def _load(self, job: Job) -> tuple[WordContent, str]:
        content = await self._db(self.repo.get_content, job.band, job.word)
        mode = job_mode(content, job)
        if content is None or mode is None:
            raise StaleJob()
        return content, mode

    async def _learn(self, job: Job) -> None:
        await self._load(job)
        card = await self.generator.make_card(job.word, job.band)
        content, mode = await self._load(job)  # version guard: discard if the content moved on
        if mode == "first":
            content.card = card
            content.error = ""
            content.model = self._model_name()
            content.generated_at = clock.iso(self.now_fn())
            await self._db(self.repo.save_content, content)
        else:
            await self._db(self.repo.save_draft, job.band, job.word, card, job.target_version)
        await self._succeed(job)

    async def _questions(self, job: Job) -> None:
        content, mode = await self._load(job)
        card = content.card if mode == "first" else content.draft
        if card is None:
            raise ValueError("no Learn card to write questions for")
        stored = await self._db(self.repo.get_pool, job.band, job.word, job.target_version)
        pool = _verified(stored)
        if not pool_meets_minimum(pool):
            mix = shortfall_mix(pool, card) if pool else initial_mix(card)
            new = await self.generator.make_questions(job.word, job.band, card, mix, pool, job.target_version)
            content, mode = await self._load(job)
            new = new[: max(0, POOL_CAP - len(stored))]
            if new:
                await self._db(self.repo.add_questions, job.band, job.word, job.target_version, new)
            pool = pool + new
            if not pool_meets_minimum(pool):
                raise PoolShortfall(
                    f"only {len(pool)} verified questions (need 6 incl. 2 tier-1 and 1 tier-2); kept for next attempt")
        if mode == "first":
            content.status = "ready"
            content.error = ""
            content.model = content.model or self._model_name()
            content.generated_at = clock.iso(self.now_fn())
            await self._db(self.repo.save_content, content)
        elif not await self._db(self.repo.swap_draft, job.band, job.word, job.target_version):
            raise StaleJob()
        await self._succeed(job)

    async def _image(self, job: Job) -> None:
        content, _ = await self._load(job)
        if self.image_provider is None or content.card is None:
            await self._db(self.repo.set_image, job.band, job.word, None, "none", job.target_version)
            await self._succeed(job)
            return
        scene = content.card.image_scene.strip() or f"a simple scene that shows the idea: {content.card.short_def}"
        raw = await self.image_provider.generate(build_image_prompt(scene))
        webp = await asyncio.to_thread(to_webp, raw)
        await self._load(job)
        key = image_key(job.band, job.word, job.target_version)
        await self._db(self.blobs.put, key, webp, "image/webp")
        if not await self._db(self.repo.set_image, job.band, job.word, key, "ready", job.target_version):
            raise StaleJob()
        await self._succeed(job)

    async def _topup(self, job: Job) -> None:
        content, _ = await self._load(job)
        card = content.card
        if card is None:
            raise StaleJob()
        stored = await self._db(self.repo.get_pool, job.band, job.word, job.target_version)
        room = POOL_CAP - len(stored)
        if room > 0:
            pool = _verified(stored)
            mix = topup_mix(pool, card, min(TOPUP_MAX, room))
            if sum(mix.values()) > 0:
                new = await self.generator.make_questions(job.word, job.band, card, mix, stored, job.target_version)
                await self._load(job)
                current = await self._db(self.repo.get_pool, job.band, job.word, job.target_version)
                new = new[: max(0, POOL_CAP - len(current))]
                if new:
                    await self._db(self.repo.add_questions, job.band, job.word, job.target_version, new)
        await self._succeed(job)

    # ---------------- job bookkeeping ----------------

    async def _succeed(self, job: Job) -> None:
        if job.chain:
            await self._db(self.repo.enqueue_job, job.chain[0], job.band, job.word, job.target_version,
                           list(job.chain[1:]))
        await self._finish(job)

    # finish/defer/fail pass the claim's lease_token: the repository ignores them (returns False) when the
    # record was re-enqueued for a newer version while this job ran, or re-claimed after our lease expired.

    async def _finish(self, job: Job) -> None:
        await self._db(self.repo.finish_job, job.key, job.lease_token)

    async def _defer(self, job: Job, not_before: str) -> None:
        await self._db(self.repo.defer_job, job.key, job.lease_token, not_before)

    async def _fail(self, job: Job, exc: Exception) -> None:
        message = redact(f"{type(exc).__name__}: {exc}")[:ERROR_MAX_CHARS]
        attempt = job.attempts + 1
        if attempt < MAX_ATTEMPTS:
            retry_at = clock.add_seconds_iso(self.now_fn(), BACKOFF_S[attempt - 1])
            await self._db(self.repo.fail_job, job.key, job.lease_token, message, retry_at)
            return
        if not await self._db(self.repo.fail_job, job.key, job.lease_token, message, None):
            return  # no longer ours: the newer request or the other worker decides the outcome
        await self._db(self._record_final_failure, job, message)
        log.warning("job %s failed after %d attempts: %s", job.key, attempt, message)

    def _record_final_failure(self, job: Job, message: str) -> None:
        """Spec §7.2 'final failure, by kind' (runs in a worker thread)."""
        if job.kind == "topup":
            return  # only the job records last_error
        content = self.repo.get_content(job.band, job.word)
        mode = job_mode(content, job)
        if content is None or mode is None:
            return
        if job.kind == "image":
            self.repo.set_image(job.band, job.word, content.image_key, "failed", job.target_version)
        elif mode == "first":
            content.status = "failed"
            content.error = message
            self.repo.save_content(content)
        else:  # draft: the current version keeps serving
            content.error = f"Regeneration failed: {message}"
            content.draft = None
            content.draft_version = None
            self.repo.save_content(content)

    # ---------------- helpers ----------------

    def _calls_ai(self, job: Job) -> bool:
        return job.kind in AI_KINDS or (job.kind == "image" and self.image_provider is not None)

    async def _over_daily_limit(self, now: datetime) -> bool:
        used = await self._db(self.repo.get_ai_calls, clock.utc_date(now))
        return used >= self.settings.ai_daily_call_limit

    def _rate_limited_until(self, exc: RateLimited) -> str:
        now = self.now_fn()
        if getattr(exc, "daily", False):
            return clock.iso(next_utc_midnight(now))
        delay = getattr(exc, "retry_after", None) or RATE_LIMIT_DEFAULT_S
        return clock.add_seconds_iso(now, float(delay))

    def _model_name(self) -> str:
        return str(getattr(self.generator, "model_name", "") or "")

    @staticmethod
    async def _db(fn: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.to_thread(fn, *args)

"""Task 5: job queue semantics — enqueue, claim (leases, tokens, priority, atomicity), finish/fail/defer
(only with the owning lease token), counts."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app import clock
from app.models import Job
from app.security import register_secrets
from app.storage.base import Repository
from app.storage.sqlite_repo import SqliteRepository

NOW = "2026-10-07T12:00:00Z"
LATER = "2026-10-07T12:10:00Z"


@pytest.fixture
def repo(tmp_path: Path):
    r = SqliteRepository(tmp_path / "wq.db")
    yield r
    r.close()


def set_clock(monkeypatch, value: str) -> None:
    monkeypatch.setattr(clock, "utc_now_iso", lambda: value)


# ---- enqueue_job ----------------------------------------------------------------------------

def test_enqueue_creates_a_pending_job(repo):
    repo.enqueue_job("learn", "6-8", "brave", 1, ["questions", "image"])
    job = repo.get_job("learn:6-8:brave")
    assert isinstance(job, Job)
    assert job.key == "learn:6-8:brave"
    assert (job.kind, job.band, job.word, job.target_version) == ("learn", "6-8", "brave", 1)
    assert job.chain == ["questions", "image"]
    assert job.status == "pending" and job.attempts == 0
    assert job.last_error == "" and job.not_before == "" and job.lease_until == ""
    assert job.created_at != "" and job.updated_at == job.created_at
    assert repo.get_job("learn:6-8:calm") is None


def test_enqueue_is_a_noop_for_pending_job_with_same_or_newer_version(repo):
    repo.enqueue_job("learn", "6-8", "brave", 2, ["questions", "image"])
    repo.enqueue_job("learn", "6-8", "brave", 2, [])
    repo.enqueue_job("learn", "6-8", "brave", 1, [])
    job = repo.get_job("learn:6-8:brave")
    assert job.target_version == 2 and job.chain == ["questions", "image"]


def test_enqueue_newer_version_replaces_pending_job(repo):
    repo.enqueue_job("questions", "6-8", "brave", 1, ["image"])
    claimed = repo.claim_next_job(NOW, 300)
    assert repo.fail_job("questions:6-8:brave", claimed.lease_token, "bad batch", "2026-10-07T12:00:05Z")
    repo.enqueue_job("questions", "6-8", "brave", 2, [])
    job = repo.get_job("questions:6-8:brave")
    assert (job.status, job.target_version, job.chain) == ("pending", 2, [])
    assert (job.attempts, job.last_error, job.not_before, job.lease_until, job.lease_token) == (0, "", "", "", "")


def test_enqueue_respects_running_jobs_unless_version_is_newer(repo):
    repo.enqueue_job("learn", "6-8", "brave", 1, [])
    repo.claim_next_job(NOW, 300)
    repo.enqueue_job("learn", "6-8", "brave", 1, ["questions"])
    assert repo.get_job("learn:6-8:brave").status == "running"
    repo.enqueue_job("learn", "6-8", "brave", 2, ["questions"])
    job = repo.get_job("learn:6-8:brave")
    assert (job.status, job.target_version, job.lease_until, job.lease_token) == ("pending", 2, "", "")


@pytest.mark.parametrize("final", ["done", "failed"])
def test_enqueue_resets_done_or_failed_job(repo, final):
    repo.enqueue_job("image", "6-8", "brave", 1, [])
    token = repo.claim_next_job(NOW, 300).lease_token
    if final == "done":
        assert repo.finish_job("image:6-8:brave", token)
    else:
        assert repo.fail_job("image:6-8:brave", token, "provider down", None)
    repo.enqueue_job("image", "6-8", "brave", 1, [])
    job = repo.get_job("image:6-8:brave")
    assert (job.status, job.attempts, job.last_error) == ("pending", 0, "")


def test_enqueue_rejects_unknown_kind(repo):
    with pytest.raises(ValueError):
        repo.enqueue_job("dance", "6-8", "brave", 1, [])


# ---- claim_next_job -------------------------------------------------------------------------

def test_claim_returns_none_when_queue_is_empty(repo):
    assert repo.claim_next_job(NOW, 300) is None


def test_claim_marks_running_with_lease(repo):
    repo.enqueue_job("learn", "6-8", "brave", 1, ["questions", "image"])
    job = repo.claim_next_job(NOW, 300)
    assert job.key == "learn:6-8:brave"
    assert job.status == "running"
    assert job.lease_until == "2026-10-07T12:05:00Z"
    assert len(job.lease_token) == 32 and int(job.lease_token, 16) >= 0  # uuid4 hex
    assert job.chain == ["questions", "image"]
    assert repo.get_job("learn:6-8:brave") == job
    assert repo.claim_next_job(NOW, 300) is None  # leased, not claimable again


def test_claim_respects_not_before(repo):
    repo.enqueue_job("learn", "6-8", "brave", 1, [])
    token = repo.claim_next_job(NOW, 300).lease_token
    assert repo.defer_job("learn:6-8:brave", token, "2026-10-07T12:01:00Z") is True
    assert repo.claim_next_job(NOW, 300) is None
    assert repo.claim_next_job("2026-10-07T12:01:00Z", 300).key == "learn:6-8:brave"  # not_before <= now


def test_expired_lease_is_claimable_again(repo):
    repo.enqueue_job("learn", "6-8", "brave", 1, [])
    first = repo.claim_next_job(NOW, 300)  # lease until 12:05:00
    assert repo.claim_next_job("2026-10-07T12:05:00Z", 300) is None  # not yet expired (strictly less)
    again = repo.claim_next_job("2026-10-07T12:05:01Z", 300)
    assert again.key == "learn:6-8:brave"
    assert again.lease_until == "2026-10-07T12:10:01Z"
    assert again.lease_token != first.lease_token  # every claim gets a fresh token
    assert again.attempts == 0


def test_claim_prefers_non_topup_jobs(repo):
    repo.enqueue_job("topup", "6-8", "brave", 1, [])
    repo.enqueue_job("image", "6-8", "calm", 1, [])
    repo.enqueue_job("topup", "6-8", "eager", 1, [])
    repo.enqueue_job("learn", "3-5", "zany", 1, ["questions", "image"])
    order = [repo.claim_next_job(NOW, 300).key for _ in range(4)]
    assert order == ["image:6-8:calm", "learn:3-5:zany", "topup:6-8:brave", "topup:6-8:eager"]
    assert repo.claim_next_job(NOW, 300) is None


def test_claim_orders_by_created_at(repo, monkeypatch):
    set_clock(monkeypatch, "2026-10-07T11:00:02Z")
    repo.enqueue_job("learn", "6-8", "second", 1, [])
    set_clock(monkeypatch, "2026-10-07T11:00:01Z")
    repo.enqueue_job("learn", "6-8", "first", 1, [])
    assert repo.claim_next_job(NOW, 300).word == "first"
    assert repo.claim_next_job(NOW, 300).word == "second"


def test_concurrent_claims_never_hand_out_a_job_twice(repo):
    for i in range(30):
        repo.enqueue_job("learn", "6-8", f"word{i:02d}", 1, [])
    claimed: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        while True:
            job = repo.claim_next_job(NOW, 300)
            if job is None:
                return
            with lock:
                claimed.append(job.key)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(claimed) == 30
    assert len(set(claimed)) == 30


# ---- finish / fail / defer ------------------------------------------------------------------

def test_finish_job_marks_done_and_clears_lease(repo, monkeypatch):
    repo.enqueue_job("image", "6-8", "brave", 1, [])
    token = repo.claim_next_job(NOW, 300).lease_token
    set_clock(monkeypatch, "2026-10-07T12:03:00Z")
    assert repo.finish_job("image:6-8:brave", token) is True
    job = repo.get_job("image:6-8:brave")
    assert (job.status, job.lease_until, job.lease_token, job.updated_at) == (
        "done", "", "", "2026-10-07T12:03:00Z")
    assert repo.claim_next_job(LATER, 300) is None


def test_fail_job_with_retry_goes_back_to_pending(repo):
    repo.enqueue_job("questions", "6-8", "brave", 1, ["image"])
    token = repo.claim_next_job(NOW, 300).lease_token
    assert repo.fail_job("questions:6-8:brave", token, "pool shortfall", "2026-10-07T12:00:05Z") is True
    job = repo.get_job("questions:6-8:brave")
    assert (job.status, job.attempts, job.last_error) == ("pending", 1, "pool shortfall")
    assert (job.not_before, job.lease_until, job.lease_token) == ("2026-10-07T12:00:05Z", "", "")
    assert job.chain == ["image"]
    assert repo.claim_next_job("2026-10-07T12:00:04Z", 300) is None
    again = repo.claim_next_job("2026-10-07T12:00:05Z", 300)
    assert again.key == "questions:6-8:brave"
    assert repo.fail_job("questions:6-8:brave", again.lease_token, "pool shortfall again",
                         "2026-10-07T12:00:35Z") is True
    assert repo.get_job("questions:6-8:brave").attempts == 2


def test_fail_job_without_retry_is_final(repo):
    repo.enqueue_job("learn", "6-8", "brave", 1, [])
    token = repo.claim_next_job(NOW, 300).lease_token
    assert repo.fail_job("learn:6-8:brave", token, "invalid output", None) is True
    job = repo.get_job("learn:6-8:brave")
    assert (job.status, job.attempts, job.last_error, job.lease_until) == ("failed", 1, "invalid output", "")
    assert repo.claim_next_job(LATER, 300) is None


def test_fail_job_redacts_secrets(repo):
    register_secrets(["csk-JOBS-SECRET-987654"])
    repo.enqueue_job("learn", "6-8", "brave", 1, [])
    token = repo.claim_next_job(NOW, 300).lease_token
    repo.fail_job("learn:6-8:brave", token, "401 for https://x?key=csk-JOBS-SECRET-987654", None)
    assert repo.get_job("learn:6-8:brave").last_error == "401 for https://x?key=[REDACTED]"


def test_defer_job_keeps_attempts(repo):
    repo.enqueue_job("learn", "6-8", "brave", 1, [])
    token = repo.claim_next_job(NOW, 300).lease_token
    repo.fail_job("learn:6-8:brave", token, "timeout", "2026-10-07T12:00:05Z")
    token = repo.claim_next_job("2026-10-07T12:00:05Z", 300).lease_token
    assert repo.defer_job("learn:6-8:brave", token, "2026-10-08T00:00:00Z") is True
    job = repo.get_job("learn:6-8:brave")
    assert (job.status, job.attempts, job.not_before, job.lease_until, job.lease_token) == (
        "pending", 1, "2026-10-08T00:00:00Z", "", "")
    assert repo.claim_next_job("2026-10-07T23:59:59Z", 300) is None
    assert repo.claim_next_job("2026-10-08T00:00:00Z", 300).key == "learn:6-8:brave"


def test_updates_to_missing_jobs_are_noops(repo):
    assert repo.finish_job("learn:6-8:ghost", "t" * 32) is False
    assert repo.fail_job("learn:6-8:ghost", "t" * 32, "x", None) is False
    assert repo.defer_job("learn:6-8:ghost", "t" * 32, LATER) is False
    assert repo.get_job("learn:6-8:ghost") is None
    assert repo.job_counts() == {"pending": 0, "running": 0, "done": 0, "failed": 0}


def test_updates_need_a_running_job_with_the_same_token(repo):
    repo.enqueue_job("learn", "6-8", "brave", 1, [])
    pending = repo.get_job("learn:6-8:brave")
    # never claimed: a pending job has no owner, even for an empty token
    assert repo.finish_job("learn:6-8:brave", "") is False
    assert repo.fail_job("learn:6-8:brave", pending.lease_token, "x", None) is False
    token = repo.claim_next_job(NOW, 300).lease_token
    running = repo.get_job("learn:6-8:brave")
    assert repo.finish_job("learn:6-8:brave", "0" * 32) is False
    assert repo.fail_job("learn:6-8:brave", "0" * 32, "x", None) is False
    assert repo.defer_job("learn:6-8:brave", "0" * 32, LATER) is False
    assert repo.get_job("learn:6-8:brave") == running  # nothing changed
    assert repo.finish_job("learn:6-8:brave", token) is True
    assert repo.finish_job("learn:6-8:brave", token) is False  # done is no longer running


def test_reenqueue_while_running_keeps_the_new_pending_job(repo):
    """A Regenerate (enqueue with a newer target_version) while a worker runs the old version: the old
    worker's finish/fail/defer must not overwrite the new pending job."""
    repo.enqueue_job("learn", "6-8", "brave", 1, ["questions", "image"])
    old = repo.claim_next_job(NOW, 300)
    repo.enqueue_job("learn", "6-8", "brave", 2, ["questions", "image"])  # Regenerate
    assert repo.finish_job(old.key, old.lease_token) is False
    assert repo.fail_job(old.key, old.lease_token, "late failure", None) is False
    assert repo.defer_job(old.key, old.lease_token, LATER) is False
    job = repo.get_job("learn:6-8:brave")
    assert (job.status, job.target_version, job.chain) == ("pending", 2, ["questions", "image"])
    assert (job.attempts, job.last_error, job.not_before, job.lease_token) == (0, "", "", "")
    new = repo.claim_next_job(NOW, 300)
    assert (new.target_version, new.lease_token != old.lease_token) == (2, True)


def test_expired_lease_reclaimed_by_another_worker_makes_the_first_worker_a_noop(repo):
    repo.enqueue_job("questions", "6-8", "brave", 1, ["image"])
    first = repo.claim_next_job(NOW, 300)                      # worker A, lease until 12:05:00
    second = repo.claim_next_job("2026-10-07T12:05:01Z", 300)  # A stalled; worker B re-claims
    assert second.key == first.key and second.lease_token != first.lease_token
    assert repo.finish_job(first.key, first.lease_token) is False
    assert repo.fail_job(first.key, first.lease_token, "A timed out", "2026-10-07T12:06:00Z") is False
    assert repo.defer_job(first.key, first.lease_token, LATER) is False
    job = repo.get_job(first.key)
    assert (job.status, job.lease_token, job.lease_until, job.attempts) == (
        "running", second.lease_token, "2026-10-07T12:10:01Z", 0)
    assert repo.finish_job(second.key, second.lease_token) is True
    assert repo.get_job(first.key).status == "done"


# ---- wake_jobs ------------------------------------------------------------------------------

TOMORROW = "2026-10-08T00:00:00Z"


def _parked(repo, kind: str, word: str) -> str:
    """Enqueue a job and defer it to tomorrow, like a run without a key or over the daily cap leaves it."""
    repo.enqueue_job(kind, "6-8", word, 1, [])
    job = repo.claim_next_job(NOW, 300)
    assert job.key == f"{kind}:6-8:{word}"
    assert repo.defer_job(job.key, job.lease_token, TOMORROW) is True
    return job.key


def test_wake_jobs_makes_parked_pending_jobs_of_the_given_kinds_claimable_now(repo, monkeypatch):
    learn = _parked(repo, "learn", "a1")
    topup = _parked(repo, "topup", "b2")
    image = _parked(repo, "image", "c3")
    repo.enqueue_job("questions", "6-8", "d4", 1, [])                # a retry after a failed attempt
    retry = repo.claim_next_job(NOW, 300)
    assert repo.fail_job(retry.key, retry.lease_token, "InvalidOutput: bad", "2026-10-07T12:00:30Z") is True
    repo.enqueue_job("learn", "6-8", "e5", 1, [])                    # running: owned by a worker, left alone
    running = repo.claim_next_job(NOW, 300)
    repo.enqueue_job("learn", "6-8", "f6", 1, [])                    # failed for good: left alone
    gone = repo.claim_next_job(NOW, 300)
    assert repo.fail_job(gone.key, gone.lease_token, "InvalidOutput: final", None) is True
    repo.enqueue_job("questions", "6-8", "g7", 1, [])                # pending and already due: nothing to wake
    before = {key: repo.get_job(key) for key in (image, running.key, gone.key, "questions:6-8:g7")}

    set_clock(monkeypatch, LATER)
    assert repo.wake_jobs(["learn", "questions", "topup"]) == 3

    for key in (learn, topup, retry.key):
        job = repo.get_job(key)
        assert (job.status, job.not_before, job.updated_at) == ("pending", "", LATER), key
    woken_retry = repo.get_job(retry.key)
    assert (woken_retry.attempts, woken_retry.last_error) == (1, "InvalidOutput: bad")  # no attempt given back
    assert {key: repo.get_job(key) for key in before} == before  # other kinds, running, failed: unchanged
    assert repo.get_job(image).not_before == TOMORROW
    claimed = {repo.claim_next_job(NOW, 300).key for _ in range(4)}
    assert claimed == {learn, topup, retry.key, "questions:6-8:g7"}
    assert repo.claim_next_job(NOW, 300) is None  # the image job still waits for tomorrow


def test_wake_jobs_counts_only_jobs_it_changed(repo):
    assert "wake_jobs" in Repository.__abstractmethods__  # part of the storage interface (Phase 2 implements it too)
    _parked(repo, "image", "a1")
    assert repo.wake_jobs([]) == 0
    assert repo.wake_jobs(["learn"]) == 0
    assert repo.wake_jobs(["image"]) == 1
    assert repo.wake_jobs(["image"]) == 0  # already claimable
    assert repo.get_job("image:6-8:a1").not_before == ""


def test_job_counts_always_has_all_four_keys(repo):
    assert repo.job_counts() == {"pending": 0, "running": 0, "done": 0, "failed": 0}
    for w in ("a1", "b2", "c3", "d4", "e5"):
        repo.enqueue_job("learn", "6-8", w, 1, [])
    repo.claim_next_job(NOW, 300)                                       # a1 running
    b2 = repo.claim_next_job(NOW, 300)                                  # b2 running
    c3 = repo.claim_next_job(NOW, 300)                                  # c3 running
    assert repo.finish_job("learn:6-8:b2", b2.lease_token)              # b2 done
    assert repo.fail_job("learn:6-8:c3", c3.lease_token, "bad", None)   # c3 failed
    assert repo.job_counts() == {"pending": 2, "running": 1, "done": 1, "failed": 1}

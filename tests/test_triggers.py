from __future__ import annotations

import pytest

from app import clock
from app.models import POOL_CAP, Profile, WordContent, WordList, new_id
from app.storage.sqlite_repo import SqliteRepository
from app.triggers import (
    bands_for_list,
    ensure_generation,
    maybe_enqueue_topup,
    on_lists_changed,
    on_profile_changed,
    regenerate,
)
from tests.gen_helpers import BAND, WORD, make_card, save_ready_content, stored_questions

NO_JOBS = {"pending": 0, "running": 0, "done": 0, "failed": 0}


@pytest.fixture
def repo(tmp_path):
    return SqliteRepository(tmp_path / "t.db")


def job_of(repo, kind, band=BAND, word=WORD):
    return repo.get_job(f"{kind}:{band}:{word}")


def add_profile(repo, name, band, list_ids):
    profile = Profile(id=new_id(), name=name, band=band, list_ids=list(list_ids), created_at=clock.utc_now_iso())
    repo.save_profile(profile)
    return profile


def add_list(repo, name, words):
    wordlist = WordList(id=new_id(), name=name, words=list(words))
    repo.save_list(wordlist)
    return wordlist


# ---------- ensure_generation ----------

def test_ensure_generation_creates_pending_content_and_learn_job(repo):
    ensure_generation(repo, BAND, WORD)

    content = repo.get_content(BAND, WORD)
    assert (content.status, content.content_version, content.source, content.card) == ("pending", 1, "ai", None)
    job = job_of(repo, "learn")
    assert (job.status, job.target_version, job.chain) == ("pending", 1, ["questions", "image"])
    assert repo.job_counts() == {**NO_JOBS, "pending": 1}


def test_ensure_generation_is_noop_for_ready_content(repo):
    save_ready_content(repo)

    ensure_generation(repo, BAND, WORD)

    assert repo.get_content(BAND, WORD).status == "ready"
    assert repo.job_counts() == NO_JOBS


def test_ensure_generation_is_noop_for_pending_content_with_live_job(repo):
    ensure_generation(repo, BAND, WORD)
    claimed = repo.claim_next_job(clock.utc_now_iso(), 300)
    assert claimed is not None and claimed.kind == "learn"

    ensure_generation(repo, BAND, WORD)

    assert job_of(repo, "learn").status == "running"
    assert repo.job_counts() == {**NO_JOBS, "running": 1}
    assert repo.get_content(BAND, WORD).status == "pending"


def test_ensure_generation_requeues_pending_content_that_lost_its_job(repo):
    # e.g. after a backup import, which clears the jobs table
    repo.save_content(WordContent(word=WORD, band=BAND, status="pending"))

    ensure_generation(repo, BAND, WORD)

    job = job_of(repo, "learn")
    assert (job.status, job.target_version, job.chain) == ("pending", 1, ["questions", "image"])


def test_failed_without_card_resumes_from_learn(repo):
    repo.save_content(WordContent(word=WORD, band=BAND, status="failed", error="InvalidOutput: bad card",
                                  content_version=2))

    ensure_generation(repo, BAND, WORD)

    job = job_of(repo, "learn")
    assert (job.status, job.target_version, job.chain) == ("pending", 2, ["questions", "image"])
    assert job_of(repo, "questions") is None
    content = repo.get_content(BAND, WORD)
    assert (content.status, content.error) == ("pending", "")


def test_failed_with_card_and_short_pool_resumes_from_questions(repo):
    repo.save_content(WordContent(word=WORD, band=BAND, status="failed", error="PoolShortfall: 3", card=make_card()))
    repo.add_questions(BAND, WORD, 1, stored_questions(3))

    ensure_generation(repo, BAND, WORD)

    assert job_of(repo, "learn") is None
    job = job_of(repo, "questions")
    assert (job.status, job.target_version, job.chain) == ("pending", 1, ["image"])
    assert len(repo.get_pool(BAND, WORD)) == 3  # verified questions are kept
    content = repo.get_content(BAND, WORD)
    assert content.card == make_card()
    assert content.status == "pending"


def test_failed_with_card_and_full_pool_resumes_from_image(repo):
    repo.save_content(WordContent(word=WORD, band=BAND, status="failed", error="x", card=make_card()))
    repo.add_questions(BAND, WORD, 1, stored_questions(6))

    ensure_generation(repo, BAND, WORD)

    assert job_of(repo, "learn") is None and job_of(repo, "questions") is None
    job = job_of(repo, "image")
    assert (job.status, job.target_version, job.chain) == ("pending", 1, [])
    content = repo.get_content(BAND, WORD)
    assert (content.status, content.error) == ("ready", "")


def test_failed_with_full_pool_and_ready_image_just_becomes_ready(repo):
    repo.save_content(WordContent(word=WORD, band=BAND, status="failed", error="x", card=make_card(),
                                  image_key="images/6-8/frugal-v1.webp", image_status="ready"))
    repo.add_questions(BAND, WORD, 1, stored_questions(6))

    ensure_generation(repo, BAND, WORD)

    assert repo.get_content(BAND, WORD).status == "ready"
    assert repo.job_counts() == NO_JOBS


# ---------- list / profile triggers ----------

def test_bands_for_list_collects_bands_of_assigned_profiles(repo):
    wordlist = add_list(repo, "Week 1", ["frugal"])
    add_profile(repo, "Ava", "6-8", [wordlist.id])
    add_profile(repo, "Ben", "9-12", [wordlist.id])
    add_profile(repo, "Cy", "3-5", [])

    assert bands_for_list(repo, wordlist.id) == {"6-8", "9-12"}
    assert bands_for_list(repo, "nosuchlist") == set()


def test_on_lists_changed_covers_every_word_and_band(repo):
    wordlist = add_list(repo, "Week 1", ["frugal", "lucid"])
    add_profile(repo, "Ava", "6-8", [wordlist.id])
    add_profile(repo, "Ben", "9-12", [wordlist.id])
    add_profile(repo, "Cy", "3-5", [])

    on_lists_changed(repo, [wordlist.id])

    for band in ("6-8", "9-12"):
        for word in ("frugal", "lucid"):
            assert repo.get_content(band, word).status == "pending"
            job = job_of(repo, "learn", band, word)
            assert (job.target_version, job.chain) == (1, ["questions", "image"])
    assert repo.get_content("3-5", "frugal") is None
    assert repo.job_counts() == {**NO_JOBS, "pending": 4}


def test_on_lists_changed_ignores_unassigned_and_unknown_lists(repo):
    wordlist = add_list(repo, "Spare words", ["frugal", "lucid"])
    add_profile(repo, "Ava", "6-8", [])

    on_lists_changed(repo, [wordlist.id, "nosuchlist"])

    assert repo.list_contents() == []
    assert repo.job_counts() == NO_JOBS


def test_on_profile_changed_after_band_change_enqueues_new_band(repo):
    wordlist = add_list(repo, "Week 1", ["frugal", "lucid"])
    profile = add_profile(repo, "Ava", "6-8", [wordlist.id])

    on_profile_changed(repo, profile)
    assert {(c.band, c.word) for c in repo.list_contents()} == {("6-8", "frugal"), ("6-8", "lucid")}

    profile.band = "9-12"
    repo.save_profile(profile)
    on_profile_changed(repo, profile)

    assert {(c.band, c.word) for c in repo.list_contents()} == {
        ("6-8", "frugal"), ("6-8", "lucid"), ("9-12", "frugal"), ("9-12", "lucid"),
    }
    assert job_of(repo, "learn", "9-12", "lucid").status == "pending"
    assert repo.job_counts() == {**NO_JOBS, "pending": 4}


# ---------- regenerate ----------

@pytest.mark.parametrize("part, chain", [("all", ["questions", "image"]), ("learn", ["questions"])])
def test_regenerate_card_builds_next_version_alongside_current(repo, part, chain):
    save_ready_content(repo)

    regenerate(repo, BAND, WORD, part)

    content = repo.get_content(BAND, WORD)
    assert (content.status, content.content_version, content.draft, content.draft_version) == ("ready", 1, None, 2)
    assert content.card == make_card()
    assert len(repo.get_pool(BAND, WORD)) == 6  # current pool keeps serving
    job = job_of(repo, "learn")
    assert (job.status, job.target_version, job.chain) == ("pending", 2, chain)


def test_regenerate_questions_copies_card_into_draft(repo):
    save_ready_content(repo)

    regenerate(repo, BAND, WORD, "questions")

    content = repo.get_content(BAND, WORD)
    assert (content.content_version, content.draft_version) == (1, 2)
    assert content.draft == content.card
    job = job_of(repo, "questions")
    assert (job.status, job.target_version, job.chain) == ("pending", 2, [])
    assert job_of(repo, "learn") is None


def test_regenerate_image_marks_pending_for_current_version(repo):
    save_ready_content(repo)
    repo.set_image(BAND, WORD, "images/6-8/frugal-v1.webp", "ready", 1)

    regenerate(repo, BAND, WORD, "image")

    content = repo.get_content(BAND, WORD)
    assert (content.image_status, content.image_key) == ("pending", "images/6-8/frugal-v1.webp")
    assert content.draft_version is None
    job = job_of(repo, "image")
    assert (job.status, job.target_version, job.chain) == ("pending", 1, [])


def test_regenerate_while_a_draft_is_in_progress_moves_to_a_newer_version(repo):
    save_ready_content(repo)
    regenerate(repo, BAND, WORD, "all")

    regenerate(repo, BAND, WORD, "learn")

    content = repo.get_content(BAND, WORD)
    assert (content.content_version, content.draft_version) == (1, 3)
    job = job_of(repo, "learn")
    assert (job.target_version, job.chain) == (3, ["questions"])


def test_regenerate_rejects_unknown_part_and_missing_card(repo):
    with pytest.raises(ValueError):
        regenerate(repo, BAND, WORD, "all")  # no content at all
    ensure_generation(repo, BAND, WORD)
    with pytest.raises(ValueError):
        regenerate(repo, BAND, WORD, "learn")  # card not generated yet
    save_ready_content(repo)
    with pytest.raises(ValueError):
        regenerate(repo, BAND, WORD, "everything")


# ---------- maybe_enqueue_topup ----------

def test_maybe_enqueue_topup_enqueues_for_ready_word(repo):
    save_ready_content(repo)

    assert maybe_enqueue_topup(repo, BAND, WORD, clock.utc_date()) is True

    job = job_of(repo, "topup")
    assert (job.status, job.target_version, job.chain) == ("pending", 1, [])


def test_maybe_enqueue_topup_skips_when_pool_is_at_cap(repo):
    save_ready_content(repo, n_questions=POOL_CAP)

    assert maybe_enqueue_topup(repo, BAND, WORD, clock.utc_date()) is False
    assert job_of(repo, "topup") is None


def test_maybe_enqueue_topup_skips_when_one_is_pending(repo):
    save_ready_content(repo)
    today = clock.utc_date()
    assert maybe_enqueue_topup(repo, BAND, WORD, today) is True

    assert maybe_enqueue_topup(repo, BAND, WORD, today) is False


def test_maybe_enqueue_topup_runs_at_most_once_per_utc_day(repo):
    save_ready_content(repo)
    today = clock.utc_date()
    assert maybe_enqueue_topup(repo, BAND, WORD, today) is True
    job = repo.claim_next_job(clock.utc_now_iso(), 300)
    assert job.kind == "topup"
    assert repo.finish_job(job.key, job.lease_token)

    assert maybe_enqueue_topup(repo, BAND, WORD, today) is False  # done today
    tomorrow = clock.add_days(today, 1)
    assert maybe_enqueue_topup(repo, BAND, WORD, tomorrow) is True  # last one ran yesterday
    assert job_of(repo, "topup").status == "pending"


def test_maybe_enqueue_topup_skips_words_not_ready_or_regenerating(repo):
    ensure_generation(repo, BAND, WORD)  # pending, no card yet
    assert maybe_enqueue_topup(repo, BAND, WORD, clock.utc_date()) is False

    save_ready_content(repo)
    regenerate(repo, BAND, WORD, "all")  # draft in progress
    assert maybe_enqueue_topup(repo, BAND, WORD, clock.utc_date()) is False
    assert job_of(repo, "topup") is None

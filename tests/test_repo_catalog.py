"""Task 4: SQLite schema/transactions and the catalog half of SqliteRepository
(profiles, lists, content, drafts, images, question pools)."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from app.models import TIER, LearnCard, Profile, Question, Sense, WordContent, WordList
from app.security import register_secrets
from app.storage.base import BlobStore, Repository
from app.storage.sqlite_repo import SqliteRepository

T1 = "2026-10-01T09:00:00Z"
T2 = "2026-10-02T09:00:00Z"
T3 = "2026-10-03T09:00:00Z"

EXPECTED_REPOSITORY_METHODS = {
    "list_profiles", "get_profile", "save_profile", "delete_profile",
    "list_lists", "get_list", "save_list", "delete_list",
    "get_content", "get_contents", "save_content", "list_content_by_status", "list_contents",
    "save_draft", "swap_draft", "set_image",
    "get_pool", "get_pools", "add_questions", "delete_questions",
    "get_progress", "list_progress",
    "save_session", "get_session", "list_sessions",
    "apply_events", "list_events",
    "enqueue_job", "claim_next_job", "get_job", "finish_job", "fail_job", "defer_job", "job_counts",
    "incr_ai_calls", "get_ai_calls",
    "incr_auth_failure", "clear_auth_failures",
    "export_all", "import_all",
}


@pytest.fixture
def repo(tmp_path: Path):
    r = SqliteRepository(tmp_path / "wq.db")
    yield r
    r.close()


def make_profile(pid: str, created_at: str = T1, list_ids: list[str] | None = None) -> Profile:
    return Profile(id=pid, name=f"Kid {pid}", band="6-8", list_ids=list_ids or [], created_at=created_at)


def make_list(lid: str, created_at: str = T1, words: list[str] | None = None) -> WordList:
    return WordList(id=lid, name=f"List {lid}", words=words or ["brave"], created_at=created_at)


def make_card(word: str, tag: str = "A") -> LearnCard:
    return LearnCard(
        pos="adjective",
        short_def=f"{tag}: short meaning of {word}",
        kid_def=f"{tag}: longer kid meaning of {word}",
        senses=[Sense(pos="adjective", definition=f"{tag} sense", example=f"The {word} cat waited.")],
        examples=[f"A {word} dog.", f"So {word} today.", f"Be {word} now.", f"Very {word} kids."],
    )


def make_content(band: str, word: str, *, version: int = 1, status: str = "ready", tag: str = "A") -> WordContent:
    return WordContent(word=word, band=band, status=status, content_version=version, card=make_card(word, tag))


def make_question(qid: str, band: str, word: str, version: int, created_at: str = T1,
                  qtype: str = "meaning") -> Question:
    return Question(
        id=qid, word=word, band=band, content_version=version, type=qtype, tier=TIER[qtype],
        prompt=f"What does {word} mean? [{qid}]", choices=["one", "two", "three", "four"],
        answer_index=0, verified=True, created_at=created_at,
    )


def ids(questions: list[Question]) -> list[str]:
    return [q.id for q in questions]


def insert_activity_rows(repo: SqliteRepository, profile_id: str) -> None:
    """Raw rows in the Task 5 tables, so the Task 4 cascade can be tested before ActivityMixin exists."""
    with repo._tx() as conn:
        conn.execute(
            "INSERT INTO progress (key, profile_id, word, data) VALUES (?, ?, ?, '{}')",
            (f"{profile_id}:brave", profile_id, "brave"),
        )
        conn.execute(
            "INSERT INTO sessions (id, profile_id, started_at, data) VALUES (?, ?, ?, '{}')",
            (f"s-{profile_id}", profile_id, T1),
        )
        conn.execute(
            "INSERT INTO events (client_event_id, profile_id, session_id, local_date, at, data) "
            "VALUES (?, ?, ?, ?, ?, '{}')",
            (f"evt-{profile_id}-0001", profile_id, f"s-{profile_id}", "2026-10-01", T1),
        )


def count_rows(repo: SqliteRepository, table: str, profile_id: str) -> int:
    return repo._conn().execute(f"SELECT COUNT(*) FROM {table} WHERE profile_id = ?", (profile_id,)).fetchone()[0]


# ---- interface ------------------------------------------------------------------------------

def test_repository_abc_declares_exactly_the_contract_methods():
    assert Repository.__abstractmethods__ == EXPECTED_REPOSITORY_METHODS
    assert BlobStore.__abstractmethods__ == {"put", "url_for", "exists", "delete"}


# ---- schema, connections, transactions ------------------------------------------------------

def test_schema_tables_indexes_and_pragmas(repo):
    conn = repo._conn()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"profiles", "lists", "contents", "questions", "progress", "sessions", "events",
            "jobs", "ai_usage", "auth_failures"} <= tables
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_questions_band_word_version", "idx_progress_profile", "idx_sessions_profile",
            "idx_events_profile_date"} <= indexes
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert conn.isolation_level is None


def test_reopening_the_file_keeps_data_and_creates_parent_dirs(tmp_path):
    db = tmp_path / "nested" / "dir" / "wq.db"
    first = SqliteRepository(db)
    first.save_profile(make_profile("p1"))
    first.close()
    second = SqliteRepository(db)
    assert second.get_profile("p1").name == "Kid p1"
    second.close()


def test_newer_schema_version_is_refused(tmp_path):
    db = tmp_path / "wq.db"
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA user_version = 7")
    raw.close()
    with pytest.raises(RuntimeError, match="newer"):
        SqliteRepository(db)


def test_tx_rolls_back_everything_on_error(repo):
    with pytest.raises(RuntimeError, match="boom"):
        with repo._tx():
            repo.save_profile(make_profile("p1"))  # nested _tx joins the outer transaction
            repo.save_list(make_list("l1"))
            raise RuntimeError("boom")
    assert repo.get_profile("p1") is None
    assert repo.get_list("l1") is None
    assert repo._conn().in_transaction is False
    repo.save_profile(make_profile("p1"))  # still usable afterwards
    assert repo.get_profile("p1") is not None


def test_nested_tx_commits_once_with_the_outer_transaction(repo):
    with repo._tx():
        repo.save_profile(make_profile("p1"))
        with repo._tx():
            repo.save_list(make_list("l1"))
        assert repo._conn().in_transaction is True  # inner exit did not COMMIT
    assert repo._conn().in_transaction is False
    assert repo.get_profile("p1") is not None
    assert repo.get_list("l1") is not None


def test_each_thread_gets_its_own_connection(repo):
    main_conn = repo._conn()
    seen: dict[str, object] = {}

    def work() -> None:
        seen["conn"] = repo._conn()
        repo.save_profile(make_profile("from-thread"))

    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert seen["conn"] is not main_conn
    assert repo.get_profile("from-thread") is not None


def test_write_transactions_are_serialized(repo):
    order: list[str] = []
    holding_lock = threading.Event()

    def other_writer() -> None:
        holding_lock.wait()
        repo.save_list(make_list("l-other"))  # BEGIN IMMEDIATE waits for the first writer
        order.append("other committed")

    t = threading.Thread(target=other_writer)
    t.start()
    with repo._tx():
        repo.save_profile(make_profile("p1"))
        holding_lock.set()
        time.sleep(0.3)
        order.append("first committing")
    t.join(timeout=10)
    assert order == ["first committing", "other committed"]
    assert repo.get_list("l-other") is not None


def test_connections_of_exited_threads_are_closed_and_dropped(repo):
    repo.save_profile(make_profile("p1"))  # the main thread holds one connection
    conns: list[sqlite3.Connection] = []
    results: list[object] = []

    def read() -> None:
        conns.append(repo._conn())
        results.append(repo.get_profile("p1"))

    for _ in range(20):  # sequential short-lived threads, like anyio's worker turnover
        t = threading.Thread(target=read)
        t.start()
        t.join()
    assert len(results) == 20 and all(r is not None for r in results)
    # the main thread (1 live user) + the last exited thread's connection, not yet pruned
    assert len(repo._conns) <= 2
    for c in conns[:-1]:  # every exited thread but the last has had its connection closed
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")
    assert repo.get_profile("p1") is not None
    repo.save_profile(make_profile("p2"))
    assert repo.get_profile("p2") is not None


def test_close_closes_connections_held_by_threads_still_alive(repo):
    main_conn = repo._conn()
    held: list[sqlite3.Connection] = []
    ready = threading.Event()
    release = threading.Event()

    def hold() -> None:
        held.append(repo._conn())
        ready.set()
        release.wait(timeout=10)

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert ready.wait(timeout=10)
        repo.close()
        for c in (main_conn, held[0]):
            with pytest.raises(sqlite3.ProgrammingError):
                c.execute("SELECT 1")
        assert len(repo._conns) == 0
    finally:
        release.set()
        t.join(timeout=10)


# ---- profiles -------------------------------------------------------------------------------

def test_profile_round_trip_and_missing(repo):
    p = make_profile("p1", list_ids=["l1", "l2"])
    repo.save_profile(p)
    assert repo.get_profile("p1") == p
    assert repo.get_profile("nope") is None


def test_list_profiles_orders_by_created_at_then_insertion(repo):
    repo.save_profile(make_profile("b", T2))
    repo.save_profile(make_profile("a", T1))
    repo.save_profile(make_profile("c", T2))
    repo.save_profile(make_profile("b", T2).model_copy(update={"name": "Renamed"}))  # update keeps position
    profiles = repo.list_profiles()
    assert [p.id for p in profiles] == ["a", "b", "c"]
    assert profiles[1].name == "Renamed"


def test_delete_profile_cascades_only_that_profile(repo):
    repo.save_profile(make_profile("p1"))
    repo.save_profile(make_profile("p2"))
    insert_activity_rows(repo, "p1")
    insert_activity_rows(repo, "p2")
    repo.delete_profile("p1")
    assert repo.get_profile("p1") is None
    assert [count_rows(repo, t, "p1") for t in ("progress", "sessions", "events")] == [0, 0, 0]
    assert [count_rows(repo, t, "p2") for t in ("progress", "sessions", "events")] == [1, 1, 1]
    assert repo.get_profile("p2") is not None


# ---- lists ----------------------------------------------------------------------------------

def test_list_round_trip_and_order(repo):
    repo.save_list(make_list("l2", T2, ["calm"]))
    repo.save_list(make_list("l1", T1, ["brave", "in lieu of"]))
    assert repo.get_list("l1").words == ["brave", "in lieu of"]
    assert repo.get_list("missing") is None
    assert [wl.id for wl in repo.list_lists()] == ["l1", "l2"]
    repo.save_list(make_list("l1", T1, ["brave"]))
    assert repo.get_list("l1").words == ["brave"]
    assert len(repo.list_lists()) == 2


def test_delete_list_removes_its_id_from_every_profile(repo):
    repo.save_list(make_list("l1"))
    repo.save_list(make_list("l2"))
    repo.save_profile(make_profile("p1", list_ids=["l1", "l2"]))
    repo.save_profile(make_profile("p2", list_ids=["l2"]))
    repo.save_profile(make_profile("p3", list_ids=["l1"]))
    repo.delete_list("l1")
    assert repo.get_list("l1") is None
    assert repo.get_list("l2") is not None
    assert repo.get_profile("p1").list_ids == ["l2"]
    assert repo.get_profile("p2").list_ids == ["l2"]
    assert repo.get_profile("p3").list_ids == []
    assert repo.get_profile("p3").name == "Kid p3"


def test_delete_missing_list_is_a_noop(repo):
    repo.save_profile(make_profile("p1", list_ids=["l1"]))
    repo.delete_list("nope")
    assert repo.get_profile("p1").list_ids == ["l1"]


# ---- content --------------------------------------------------------------------------------

def test_content_is_keyed_by_band_and_word(repo):
    repo.save_content(make_content("6-8", "brave", tag="six"))
    repo.save_content(make_content("3-5", "brave", tag="three"))
    assert repo.get_content("6-8", "brave").card.short_def.startswith("six")
    assert repo.get_content("3-5", "brave").card.short_def.startswith("three")
    assert repo.get_content("9-12", "brave") is None


def test_get_contents_returns_existing_words_in_request_order(repo):
    for w in ("brave", "calm", "eager"):
        repo.save_content(make_content("6-8", w))
    repo.save_content(make_content("3-5", "zany"))
    got = repo.get_contents("6-8", ["eager", "zany", "brave", "ghost", "eager"])
    assert list(got) == ["eager", "brave"]
    assert got["brave"].word == "brave"
    assert repo.get_contents("6-8", []) == {}


def test_get_contents_handles_more_words_than_one_chunk(repo):
    words = [f"word{i:04d}" for i in range(1203)]
    with repo._tx():
        for w in words:
            repo.save_content(make_content("6-8", w))
    got = repo.get_contents("6-8", words)
    assert list(got) == words


def test_list_contents_and_list_by_status_order_by_band_then_word(repo):
    repo.save_content(make_content("9-12", "abate", status="pending"))
    repo.save_content(make_content("6-8", "calm", status="failed"))
    repo.save_content(make_content("3-5", "zany", status="pending"))
    repo.save_content(make_content("6-8", "brave", status="pending"))
    assert [c.key for c in repo.list_contents()] == ["3-5:zany", "6-8:brave", "6-8:calm", "9-12:abate"]
    assert [c.key for c in repo.list_content_by_status("pending")] == ["3-5:zany", "6-8:brave", "9-12:abate"]
    repo.save_content(make_content("6-8", "brave", status="ready"))  # status column follows the record
    assert [c.key for c in repo.list_content_by_status("pending")] == ["3-5:zany", "9-12:abate"]
    assert [c.key for c in repo.list_content_by_status("ready")] == ["6-8:brave"]
    assert repo.list_content_by_status("failed")[0].word == "calm"


def test_save_content_redacts_secrets_in_error(repo):
    register_secrets(["sk-test-CATALOG-123456"])
    c = make_content("6-8", "brave", status="failed").model_copy(
        update={"error": "401 Unauthorized for key sk-test-CATALOG-123456"}
    )
    repo.save_content(c)
    stored = repo.get_content("6-8", "brave").error
    assert "sk-test-CATALOG-123456" not in stored
    assert stored == "401 Unauthorized for key [REDACTED]"


def test_phrase_and_apostrophe_words_round_trip(repo):
    for w in ("in lieu of", "o'clock", "self-esteem"):
        repo.save_content(make_content("6-8", w))
        repo.add_questions("6-8", w, 1, [make_question(f"q-{len(w)}", "6-8", w, 1)])
    assert set(repo.get_contents("6-8", ["in lieu of", "o'clock", "self-esteem"])) == {
        "in lieu of", "o'clock", "self-esteem"}
    assert ids(repo.get_pool("6-8", "o'clock")) == ["q-7"]
    assert ids(repo.get_pools("6-8", ["in lieu of"])["in lieu of"]) == ["q-10"]


# ---- drafts, swap, images -------------------------------------------------------------------

def test_save_draft_sets_draft_and_keeps_active_card(repo):
    repo.save_content(make_content("6-8", "brave", tag="A"))
    repo.save_draft("6-8", "brave", make_card("brave", "B"), 2)
    c = repo.get_content("6-8", "brave")
    assert c.card.short_def.startswith("A")
    assert c.draft.short_def.startswith("B")
    assert c.draft_version == 2
    assert c.content_version == 1
    assert c.status == "ready"


def test_save_draft_without_content_raises_key_error(repo):
    with pytest.raises(KeyError):
        repo.save_draft("6-8", "ghost", make_card("ghost"), 2)


def test_swap_draft_promotes_draft_and_deletes_older_questions(repo):
    c = make_content("6-8", "brave", tag="A").model_copy(update={"error": "regeneration failed: timeout",
                                                               "image_key": "images/6-8/brave-v1.webp",
                                                               "image_status": "ready"})
    repo.save_content(c)
    repo.add_questions("6-8", "brave", 1, [make_question("q1", "6-8", "brave", 1),
                                           make_question("q2", "6-8", "brave", 1)])
    repo.save_draft("6-8", "brave", make_card("brave", "B"), 2)
    repo.add_questions("6-8", "brave", 2, [make_question("q3", "6-8", "brave", 2)])
    repo.add_questions("6-8", "brave", 3, [make_question("q4", "6-8", "brave", 3)])
    repo.save_content(make_content("6-8", "calm"))
    repo.add_questions("6-8", "calm", 1, [make_question("c1", "6-8", "calm", 1)])

    assert repo.swap_draft("6-8", "brave", 2) is True

    got = repo.get_content("6-8", "brave")
    assert got.card.short_def.startswith("B")
    assert got.content_version == 2
    assert got.draft is None and got.draft_version is None
    assert got.status == "ready" and got.error == ""
    assert got.image_key == "images/6-8/brave-v1.webp" and got.image_status == "ready"  # image untouched
    assert ids(repo.get_pool("6-8", "brave")) == ["q3"]
    assert repo.get_pool("6-8", "brave", 1) == []
    assert ids(repo.get_pool("6-8", "brave", 3)) == ["q4"]  # only OLDER versions are deleted
    assert ids(repo.get_pool("6-8", "calm")) == ["c1"]       # other words untouched


def test_swap_draft_refuses_wrong_version_missing_draft_and_missing_content(repo):
    repo.save_content(make_content("6-8", "brave", tag="A"))
    repo.add_questions("6-8", "brave", 1, [make_question("q1", "6-8", "brave", 1)])
    assert repo.swap_draft("6-8", "brave", 2) is False  # no draft yet
    repo.save_draft("6-8", "brave", make_card("brave", "B"), 2)
    assert repo.swap_draft("6-8", "brave", 3) is False  # draft is version 2
    c = repo.get_content("6-8", "brave")
    assert c.card.short_def.startswith("A") and c.content_version == 1
    assert c.draft_version == 2
    assert ids(repo.get_pool("6-8", "brave")) == ["q1"]
    assert repo.swap_draft("6-8", "ghost", 2) is False


def test_set_image_applies_only_to_the_active_version(repo):
    repo.save_content(make_content("6-8", "brave", version=2))
    assert repo.set_image("6-8", "brave", "images/6-8/brave-v1.webp", "ready", 1) is False
    c = repo.get_content("6-8", "brave")
    assert c.image_key is None and c.image_status == "none"

    assert repo.set_image("6-8", "brave", "images/6-8/brave-v2.webp", "ready", 2) is True
    c = repo.get_content("6-8", "brave")
    assert c.image_key == "images/6-8/brave-v2.webp" and c.image_status == "ready"
    assert c.status == "ready" and c.card.short_def.startswith("A")

    assert repo.set_image("6-8", "brave", None, "none", 2) is True
    c = repo.get_content("6-8", "brave")
    assert c.image_key is None and c.image_status == "none"


def test_set_image_on_missing_content_or_bad_status(repo):
    assert repo.set_image("6-8", "ghost", "k", "ready", 1) is False
    repo.save_content(make_content("6-8", "brave"))
    with pytest.raises(ValueError):
        repo.set_image("6-8", "brave", "k", "sparkly", 1)
    assert repo.get_content("6-8", "brave").image_status == "none"


# ---- questions ------------------------------------------------------------------------------

def test_get_pool_defaults_to_the_active_version(repo):
    repo.save_content(make_content("6-8", "brave", version=1))
    repo.add_questions("6-8", "brave", 1, [make_question("q1", "6-8", "brave", 1),
                                           make_question("q2", "6-8", "brave", 1)])
    repo.add_questions("6-8", "brave", 2, [make_question("q3", "6-8", "brave", 2)])  # a draft's pool
    assert ids(repo.get_pool("6-8", "brave")) == ["q1", "q2"]
    assert ids(repo.get_pool("6-8", "brave", 2)) == ["q3"]
    assert repo.get_pool("6-8", "ghost") == []
    assert repo.get_pool("3-5", "brave") == []


def test_get_pool_orders_by_created_at_then_insertion(repo):
    repo.save_content(make_content("6-8", "brave"))
    repo.add_questions("6-8", "brave", 1, [make_question("qB", "6-8", "brave", 1, T2)])
    repo.add_questions("6-8", "brave", 1, [make_question("qA", "6-8", "brave", 1, T1)])
    repo.add_questions("6-8", "brave", 1, [make_question("qC", "6-8", "brave", 1, T2)])
    pool = repo.get_pool("6-8", "brave")
    assert ids(pool) == ["qA", "qB", "qC"]
    assert pool[0] == make_question("qA", "6-8", "brave", 1, T1)


def test_get_pools_uses_each_words_active_version(repo):
    repo.save_content(make_content("6-8", "brave", version=1))
    repo.save_content(make_content("6-8", "calm", version=2))
    repo.save_content(make_content("3-5", "brave", version=1))
    repo.add_questions("6-8", "brave", 1, [make_question("b1", "6-8", "brave", 1, T2),
                                           make_question("b0", "6-8", "brave", 1, T1)])
    repo.add_questions("6-8", "calm", 1, [make_question("c-old", "6-8", "calm", 1)])
    repo.add_questions("6-8", "calm", 2, [make_question("c-new", "6-8", "calm", 2)])
    repo.add_questions("3-5", "brave", 1, [make_question("kid", "3-5", "brave", 1)])
    pools = repo.get_pools("6-8", ["brave", "calm", "ghost", "brave"])
    assert list(pools) == ["brave", "calm", "ghost"]
    assert ids(pools["brave"]) == ["b0", "b1"]
    assert ids(pools["calm"]) == ["c-new"]
    assert pools["ghost"] == []


def test_add_questions_rejects_a_mismatched_question_and_stores_nothing(repo):
    repo.save_content(make_content("6-8", "brave"))
    good = make_question("q1", "6-8", "brave", 1)
    wrong_version = make_question("q2", "6-8", "brave", 2)
    with pytest.raises(ValueError, match="q2"):
        repo.add_questions("6-8", "brave", 1, [good, wrong_version])
    with pytest.raises(ValueError):
        repo.add_questions("6-8", "brave", 1, [make_question("q3", "6-8", "calm", 1)])
    assert repo.get_pool("6-8", "brave") == []


def test_add_questions_is_idempotent_per_question_id(repo):
    repo.save_content(make_content("6-8", "brave"))
    q = make_question("q1", "6-8", "brave", 1)
    repo.add_questions("6-8", "brave", 1, [q])
    repo.add_questions("6-8", "brave", 1, [q])
    assert ids(repo.get_pool("6-8", "brave")) == ["q1"]


def test_delete_questions_removes_only_that_version_of_that_word(repo):
    repo.save_content(make_content("6-8", "brave", version=1))
    repo.save_content(make_content("6-8", "calm", version=1))
    repo.save_content(make_content("3-5", "brave", version=1))
    repo.add_questions("6-8", "brave", 1, [make_question("b1", "6-8", "brave", 1)])
    repo.add_questions("6-8", "brave", 2, [make_question("b2", "6-8", "brave", 2),
                                           make_question("b3", "6-8", "brave", 2, T2)])
    repo.add_questions("6-8", "calm", 2, [make_question("c2", "6-8", "calm", 2)])
    repo.add_questions("3-5", "brave", 2, [make_question("k2", "3-5", "brave", 2)])

    assert repo.delete_questions("6-8", "brave", 2) == 2  # the count of removed rows
    assert repo.get_pool("6-8", "brave", 2) == []
    assert ids(repo.get_pool("6-8", "brave", 1)) == ["b1"]  # other versions of the word stay
    assert ids(repo.get_pool("6-8", "calm", 2)) == ["c2"]   # other words stay
    assert ids(repo.get_pool("3-5", "brave", 2)) == ["k2"]  # the same word in another band stays
    assert repo.delete_questions("6-8", "brave", 2) == 0    # nothing left to delete
    assert repo.delete_questions("6-8", "ghost", 2) == 0

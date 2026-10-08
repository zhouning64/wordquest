"""Task 5: LocalBlobStore, the MemoryBlobStore fake, and make_storage()."""
from __future__ import annotations

import pytest

from app.config import Settings
from app.storage import make_storage
from app.storage.base import BlobStore, Repository
from app.storage.local_blobs import LocalBlobStore
from app.storage.sqlite_repo import SqliteRepository
from tests.fakes import MemoryBlobStore

KEY = "images/6-8/in-lieu-of-v1.webp"


def test_local_blob_store_put_exists_url_delete(tmp_path):
    store = LocalBlobStore(tmp_path)
    assert store.exists(KEY) is False
    store.put(KEY, b"RIFF-webp-bytes", "image/webp")
    assert (tmp_path / "images" / "6-8" / "in-lieu-of-v1.webp").read_bytes() == b"RIFF-webp-bytes"
    assert store.exists(KEY) is True
    assert store.url_for(KEY) == "/media/images/6-8/in-lieu-of-v1.webp"
    assert store.path_for(KEY) == tmp_path / KEY
    store.put(KEY, b"second", "image/webp")  # overwrite in place
    assert (tmp_path / KEY).read_bytes() == b"second"
    assert [p.name for p in (tmp_path / "images" / "6-8").iterdir()] == ["in-lieu-of-v1.webp"]  # no temp files left
    store.delete(KEY)
    assert store.exists(KEY) is False
    store.delete(KEY)  # deleting a missing key is fine


def test_local_blob_store_custom_url_prefix(tmp_path):
    store = LocalBlobStore(tmp_path, url_prefix="/files")
    assert store.url_for("images/3-5/o'clock-v2.webp") == "/files/images/3-5/o'clock-v2.webp"


@pytest.mark.parametrize("bad", ["", "/etc/passwd", "../secret_key", "images/../../secret_key", "images/..",
                                 "images\\6-8\\x.webp", "images/6-8/x\x00.webp"])
def test_local_blob_store_rejects_unsafe_keys(tmp_path, bad):
    store = LocalBlobStore(tmp_path / "data")
    for call in (lambda: store.put(bad, b"x", "image/webp"), lambda: store.url_for(bad),
                 lambda: store.exists(bad), lambda: store.delete(bad)):
        with pytest.raises(ValueError):
            call()
    assert not (tmp_path / "secret_key").exists()


def test_memory_blob_store_fake(tmp_path):
    store = MemoryBlobStore()
    assert isinstance(store, BlobStore)
    store.put(KEY, b"abc", "image/webp")
    assert store.exists(KEY) and store.get(KEY) == b"abc"
    assert store.data == {KEY: b"abc"} and store.content_types == {KEY: "image/webp"}
    assert store.url_for(KEY) == "/media/" + KEY
    store.delete(KEY)
    assert not store.exists(KEY)
    store.delete(KEY)


def test_make_storage_local(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", storage="local")
    repo, blobs = make_storage(settings)
    assert isinstance(repo, SqliteRepository) and isinstance(repo, Repository)
    assert repo.db_path == tmp_path / "data" / "wordquest.db"
    assert repo.db_path.exists()
    assert isinstance(blobs, LocalBlobStore) and blobs.root == tmp_path / "data"
    blobs.put("images/6-8/brave-v1.webp", b"x", "image/webp")
    assert (tmp_path / "data" / "images" / "6-8" / "brave-v1.webp").exists()
    repo.close()


def test_make_storage_rejects_phase2_backends(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, storage="gcp")
    with pytest.raises(ValueError, match="STORAGE=gcp is Phase 2"):
        make_storage(settings)

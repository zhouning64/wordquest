"""Backup import (spec §6.3 "Backup semantics"). Export is Repository.export_all() as-is."""
from __future__ import annotations

from app.storage.base import BlobStore, Repository
from app.triggers import ensure_generation

BACKUP_FORMAT = "wordquest-backup"
BACKUP_VERSION = 1
COLLECTIONS = ("profiles", "lists", "contents", "questions", "progress", "sessions", "events")


def _check_shape(data: object) -> dict:
    if not isinstance(data, dict) or data.get("format") != BACKUP_FORMAT or data.get("version") != BACKUP_VERSION:
        raise ValueError("not a WordQuest backup (expected format 'wordquest-backup', version 1)")
    for name in COLLECTIONS:
        if not isinstance(data.get(name), list):
            raise ValueError(f"backup is missing the '{name}' list")
    return data


def _blob_exists(blobs: BlobStore, key: str) -> bool:
    """A key that cannot be looked up counts as missing: LocalBlobStore raises ValueError for unsafe keys such as
    "../x", and the filesystem raises OSError (ENAMETOOLONG) for a name longer than it allows. A crafted backup
    must not fail the import after import_all() already replaced the data."""
    try:
        return blobs.exists(key)
    except (ValueError, OSError):
        return False


def import_backup(repo: Repository, blobs: BlobStore, data: dict) -> dict:
    """Replace all data with the backup, fix missing pictures, and re-enqueue unfinished generation.

    Raises ValueError when `data` is not a WordQuest backup. The caller pauses and drains the job worker.
    """
    _check_shape(data)
    repo.import_all(data)  # also clears the jobs table: jobs are never exported

    images_missing = 0
    for c in repo.list_contents():
        if c.image_key and not _blob_exists(blobs, c.image_key):
            repo.set_image(c.band, c.word, None, "none", c.content_version)
            images_missing += 1

    jobs_requeued = 0
    for c in repo.list_contents():
        if c.draft_version is not None:
            # A regeneration was in flight at export time. Its jobs are gone, so it could never finish and the
            # word would show "updating" (and get no top-ups) forever: drop the half-built draft. The current
            # version keeps serving; the parent can press Regenerate again.
            current = repo.get_content(c.band, c.word)
            current.draft = None
            current.draft_version = None
            repo.save_content(current)
        if c.status in ("pending", "failed"):
            # Task 13: ensure_generation resumes failed content, and pending content that has no live job
            # (none survive an import), from the stage that is missing.
            ensure_generation(repo, c.band, c.word)
            jobs_requeued += 1

    counts = {name: len(data[name]) for name in COLLECTIONS}
    counts["images_missing"] = images_missing
    counts["jobs_requeued"] = jobs_requeued
    return counts

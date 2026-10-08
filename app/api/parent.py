"""Parent area API (spec §9 "Parent", §11). Every route needs the site cookie and the parent cookie."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app import clock
from app.api.learner import eligible_words
from app.auth import require_parent, require_site
from app.backup import import_backup
from app.learning.stats import profile_stats, window_start
from app.models import BANDS, Band, Job, Profile, ProfileSettings, WordContent, WordList, new_id
from app.security import redact
from app.storage.base import Repository
from app.triggers import bands_for_list, on_lists_changed, on_profile_changed, regenerate
from app.words import MAX_LIST_WORDS, Rejected, normalize_entries

router = APIRouter(prefix="/api/parent", dependencies=[Depends(require_site), Depends(require_parent)])

JOB_KINDS = ("learn", "questions", "image", "topup")
STATS_SESSION_LIMIT = 1000
NOT_ASSIGNED_NOTE = "This list is not assigned to any profile yet, so it has no grade band and nothing is generated."


# ---------- request bodies ----------

class ProfileCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=30)
    avatar: str = Field("🙂", min_length=1, max_length=16)
    band: Band
    settings: ProfileSettings = Field(default_factory=ProfileSettings)
    list_ids: list[str] = []


class ProfilePatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str | None = Field(None, min_length=1, max_length=30)
    avatar: str | None = Field(None, min_length=1, max_length=16)
    band: Band | None = None
    settings: dict[str, Any] | None = None     # partial: merged into the current settings


class ProfileListsIn(BaseModel):
    list_ids: list[str]


class ListCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=60)
    words: str | list[str] = ""
    assign_profile_ids: list[str] = []


class ListPatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str | None = Field(None, min_length=1, max_length=60)
    add_words: str | list[str] | None = None
    remove_words: str | list[str] | None = None
    assign_profile_ids: list[str] | None = None   # replaces the set of profiles that have this list


class RegenerateIn(BaseModel):
    part: str


# ---------- helpers ----------

def _repo(request: Request) -> Repository:
    return request.app.state.repo


def _dump(model: BaseModel) -> dict:
    return model.model_dump(mode="json")


def _profile_or_404(repo: Repository, profile_id: str) -> Profile:
    profile = repo.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile_not_found")
    return profile


def _list_or_404(repo: Repository, list_id: str) -> WordList:
    wl = repo.get_list(list_id)
    if wl is None:
        raise HTTPException(status_code=404, detail="list_not_found")
    return wl


def _check_band(band: str) -> str:
    if band not in BANDS:
        raise HTTPException(status_code=400, detail="unknown_band")
    return band


def _profiles_or_400(repo: Repository, profile_ids: list[str]) -> list[Profile]:
    found = [(pid, repo.get_profile(pid)) for pid in profile_ids]
    unknown = [pid for pid, p in found if p is None]
    if unknown:
        raise HTTPException(status_code=400, detail="unknown profile ids: " + ", ".join(unknown))
    return [p for _, p in found if p is not None]


def _check_list_ids(repo: Repository, list_ids: list[str]) -> None:
    unknown = [lid for lid in list_ids if repo.get_list(lid) is None]
    if unknown:
        raise HTTPException(status_code=400, detail="unknown list ids: " + ", ".join(unknown))


def _rejected(items: list[Rejected]) -> list[dict]:
    return [{"entry": r.entry, "reason": r.reason} for r in items]


def _ordered_bands(bands: set[str] | list[str]) -> list[str]:
    return [b for b in BANDS if b in bands]


def _image_error(repo: Repository, content: WordContent | None) -> str:
    """Why the picture failed: the image job's last_error (stored redacted) while image_status is "failed", else ""."""
    if content is None or content.image_status != "failed":
        return ""
    job = repo.get_job(f"image:{content.band}:{content.word}")
    return redact(job.last_error) if job is not None else ""


def _content_row(word: str, band: str, content: WordContent | None, pool_size: int, image_error: str = "") -> dict:
    if content is None:
        return {"word": word, "band": band, "status": "missing", "image_status": "none", "pool_size": 0,
                "error": "", "image_error": "", "regenerating": False, "source": None, "content_version": None}
    return {
        "word": word,
        "band": band,
        "status": content.status,
        "image_status": content.image_status,
        "pool_size": pool_size,
        "error": redact(content.error),
        "image_error": image_error,
        "regenerating": content.draft_version is not None,
        "source": content.source,
        "content_version": content.content_version,
    }


def _list_summary(repo: Repository, wl: WordList, profiles: list[Profile]) -> dict:
    bands = _ordered_bands(bands_for_list(repo, wl.id))
    ready = 0
    if bands and wl.words:
        per_band = [repo.get_contents(b, wl.words) for b in bands]
        for word in wl.words:
            contents = [m.get(word) for m in per_band]
            if all(c is not None and c.status == "ready" for c in contents):
                ready += 1
    out = _dump(wl)
    out.update(
        word_count=len(wl.words),
        ready_count=ready,
        bands=bands,
        profile_ids=[p.id for p in profiles if wl.id in p.list_ids],
    )
    return out


def _all_jobs(repo: Repository) -> list[Job]:
    """Every job record. Jobs exist only for words that have WordContent, so walk the contents."""
    jobs: list[Job] = []
    for c in repo.list_contents():
        for kind in JOB_KINDS:
            job = repo.get_job(f"{kind}:{c.band}:{c.word}")
            if job is not None:
                jobs.append(job)
    return jobs


def _at_or_after(stamp: str, moment: datetime) -> bool:
    if not stamp:
        return False
    try:
        return clock.parse_iso(stamp) >= moment
    except ValueError:
        return False


# ---------- profiles ----------

@router.get("/profiles")
def list_profiles(request: Request):
    return [_dump(p) for p in _repo(request).list_profiles()]


@router.post("/profiles")
def create_profile(body: ProfileCreate, request: Request):
    repo = _repo(request)
    list_ids = list(dict.fromkeys(body.list_ids))
    _check_list_ids(repo, list_ids)
    profile = Profile(
        id=new_id(), name=body.name, avatar=body.avatar, band=body.band,
        list_ids=list_ids, settings=body.settings, created_at=clock.utc_now_iso(),
    )
    repo.save_profile(profile)
    on_profile_changed(repo, profile)
    return _dump(profile)


@router.patch("/profiles/{profile_id}")
def update_profile(profile_id: str, body: ProfilePatch, request: Request):
    repo = _repo(request)
    profile = _profile_or_404(repo, profile_id)
    band_changed = body.band is not None and body.band != profile.band
    if body.settings is not None:
        try:
            profile.settings = ProfileSettings.model_validate({**profile.settings.model_dump(), **body.settings})
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())
            raise HTTPException(status_code=422, detail=f"invalid settings: {problems}") from None
    if body.name is not None:
        profile.name = body.name
    if body.avatar is not None:
        profile.avatar = body.avatar
    if body.band is not None:
        profile.band = body.band
    repo.save_profile(profile)
    if band_changed:
        on_profile_changed(repo, profile)
    return _dump(profile)


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str, request: Request):
    repo = _repo(request)
    _profile_or_404(repo, profile_id)
    repo.delete_profile(profile_id)
    return {"ok": True}


@router.put("/profiles/{profile_id}/lists")
def set_profile_lists(profile_id: str, body: ProfileListsIn, request: Request):
    repo = _repo(request)
    profile = _profile_or_404(repo, profile_id)
    list_ids = list(dict.fromkeys(body.list_ids))
    _check_list_ids(repo, list_ids)
    profile.list_ids = list_ids
    repo.save_profile(profile)
    on_profile_changed(repo, profile)
    return _dump(profile)


@router.get("/profiles/{profile_id}/stats")
def get_profile_stats(profile_id: str, request: Request, local_date: str | None = None):
    repo = _repo(request)
    profile = _profile_or_404(repo, profile_id)
    today = local_date or clock.utc_date()
    try:
        since = window_start(today)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid local_date") from None
    return profile_stats(
        profile,
        repo.list_progress(profile.id),
        repo.list_sessions(profile.id, STATS_SESSION_LIMIT),
        repo.list_events(profile.id, since),
        today,
        eligible_words(repo, profile),
    )


# ---------- word lists ----------

@router.get("/lists")
def list_lists(request: Request):
    repo = _repo(request)
    profiles = repo.list_profiles()
    return [_list_summary(repo, wl, profiles) for wl in repo.list_lists()]


@router.post("/lists")
def create_list(body: ListCreate, request: Request):
    repo = _repo(request)
    profiles = _profiles_or_400(repo, list(dict.fromkeys(body.assign_profile_ids)))
    words, rejected = normalize_entries(body.words)
    now = clock.utc_now_iso()
    wl = WordList(id=new_id(), name=body.name, words=words, created_at=now, updated_at=now)
    repo.save_list(wl)
    for profile in profiles:
        if wl.id not in profile.list_ids:
            profile.list_ids.append(wl.id)
            repo.save_profile(profile)
    on_lists_changed(repo, [wl.id])
    return {"list": _list_summary(repo, wl, repo.list_profiles()), "rejected": _rejected(rejected)}


@router.patch("/lists/{list_id}")
def update_list(list_id: str, body: ListPatch, request: Request):
    repo = _repo(request)
    wl = _list_or_404(repo, list_id)
    assign: list[str] | None = None
    if body.assign_profile_ids is not None:
        assign = list(dict.fromkeys(body.assign_profile_ids))
        _profiles_or_400(repo, assign)

    words = list(wl.words)
    if body.remove_words is not None:
        to_remove = set(normalize_entries(body.remove_words)[0])
        words = [w for w in words if w not in to_remove]
    rejected: list[Rejected] = []
    added: list[str] = []
    if body.add_words is not None:
        candidates, rejected = normalize_entries(body.add_words)
        present = set(words)
        for word in candidates:
            if word in present:
                continue
            if len(words) >= MAX_LIST_WORDS:
                rejected.append(Rejected(entry=word, reason=f"list is full ({MAX_LIST_WORDS} max)"))
                continue
            words.append(word)
            present.add(word)
            added.append(word)

    changed = words != wl.words or (body.name is not None and body.name != wl.name)
    if body.name is not None:
        wl.name = body.name
    wl.words = words
    if changed:
        wl.updated_at = clock.utc_now_iso()
    repo.save_list(wl)

    if assign is not None:
        wanted = set(assign)
        for profile in repo.list_profiles():
            has_list = wl.id in profile.list_ids
            if profile.id in wanted and not has_list:
                profile.list_ids.append(wl.id)
                repo.save_profile(profile)
            elif profile.id not in wanted and has_list:
                profile.list_ids = [x for x in profile.list_ids if x != wl.id]
                repo.save_profile(profile)
    if added or assign is not None:
        on_lists_changed(repo, [wl.id])
    return {"list": _list_summary(repo, wl, repo.list_profiles()), "rejected": _rejected(rejected)}


@router.delete("/lists/{list_id}")
def delete_list(list_id: str, request: Request):
    repo = _repo(request)
    _list_or_404(repo, list_id)
    repo.delete_list(list_id)
    return {"ok": True}


# ---------- content ----------

@router.get("/content")
def list_content(request: Request, list_id: str, band: str | None = None):
    repo = _repo(request)
    wl = _list_or_404(repo, list_id)
    bands = [_check_band(band)] if band is not None else _ordered_bands(bands_for_list(repo, wl.id))
    items: list[dict] = []
    if wl.words:
        for b in bands:
            contents = repo.get_contents(b, wl.words)
            pools = repo.get_pools(b, wl.words)
            for word in wl.words:
                content = contents.get(word)
                items.append(_content_row(word, b, content, len(pools.get(word, [])), _image_error(repo, content)))
    return {"list_id": wl.id, "bands": bands, "items": items, "note": None if bands else NOT_ASSIGNED_NOTE}


@router.get("/content/{band}/{word}")
def content_detail(band: str, word: str, request: Request):
    repo = _repo(request)
    _check_band(band)
    content = repo.get_content(band, word)
    if content is None:
        raise HTTPException(status_code=404, detail="content_not_found")
    data = _dump(content)
    data["error"] = redact(content.error)
    image_url = None
    if content.image_key and content.image_status == "ready":
        image_url = request.app.state.blobs.url_for(content.image_key)
    return {
        "content": data,
        "image_url": image_url,
        "image_error": _image_error(repo, content),
        "pool": [_dump(q) for q in repo.get_pool(band, word)],
    }


@router.post("/content/{band}/{word}/regenerate")
def regenerate_content(band: str, word: str, body: RegenerateIn, request: Request):
    repo = _repo(request)
    _check_band(band)
    if repo.get_content(band, word) is None:
        raise HTTPException(status_code=404, detail="content_not_found")
    try:
        regenerate(repo, band, word, body.part)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=redact(str(exc))) from None
    content = repo.get_content(band, word)
    return _content_row(word, band, content, len(repo.get_pool(band, word)), _image_error(repo, content))


# ---------- queue, status, backup ----------

@router.get("/queue")
def queue(request: Request):
    repo = _repo(request)
    now = clock.utc_now()
    today = clock.utc_date(now)
    counts = repo.job_counts()
    deferred = False
    if counts.get("pending", 0):
        midnight = clock.parse_iso(clock.add_days(today, 1) + "T00:00:00Z")
        deferred = any(j.status == "pending" and _at_or_after(j.not_before, midnight) for j in _all_jobs(repo))
    return {
        "counts": counts,
        "ai_calls_today": repo.get_ai_calls(today),
        "ai_daily_limit": request.app.state.settings.ai_daily_call_limit,
        "deferred_to_tomorrow": deferred,
    }


@router.get("/status")
def status(request: Request):
    state = request.app.state
    settings = state.settings
    provider = getattr(state, "image_provider", None)
    return {
        "ai_enabled": bool(settings.ai_enabled or getattr(state, "generator", None) is not None),
        "model": settings.cerebras_model,
        "image_provider": getattr(provider, "name", settings.image_provider) if provider is not None else "none",
        "image_model": str(getattr(provider, "model", "") or "") if provider is not None else "",
        # Why pictures are off although IMAGE_PROVIDER asks for them (Task 16 lifespan); "" when the settings are fine.
        "image_config_error": redact(getattr(state, "image_config_error", "") or ""),
        "parent_enabled": state.auth.parent_enabled(),
    }


@router.get("/export")
def export_backup(request: Request):
    filename = f"wordquest-backup-{clock.utc_date()}.json"
    return JSONResponse(
        content=_repo(request).export_all(),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


IMPORT_DRAIN_TIMEOUT_S = 30.0


@router.post("/import")
async def import_data(request: Request, file: UploadFile = File(...), confirm: str = Form("")):
    if confirm.strip().lower() != "true":
        raise HTTPException(status_code=400, detail="confirm_required")
    try:
        data = json.loads(await file.read())
    except ValueError:  # includes UnicodeDecodeError
        raise HTTPException(status_code=400, detail="invalid_json") from None
    worker = getattr(request.app.state, "worker", None)
    # Stop claiming jobs AND wait for the running ones, so no job writes into the data being replaced.
    if worker is not None and not await worker.pause_and_drain(IMPORT_DRAIN_TIMEOUT_S):
        worker.resume()
        raise HTTPException(status_code=503, detail="busy")
    try:
        counts = await asyncio.to_thread(import_backup, _repo(request), request.app.state.blobs, data)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid_backup: {redact(str(exc))}") from None
    finally:
        if worker is not None:
            worker.resume()
    return {"ok": True, "counts": counts}

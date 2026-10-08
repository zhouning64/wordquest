"""FastAPI application factory: lifespan wiring, static files, media, routers (spec §4, §9, §12)."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from app.ai.content import ContentGenerator
from app.ai.images import make_image_provider
from app.ai.images.base import ImageProvider
from app.ai.llm import CerebrasClient, LLMClient
from app.api.learner import router as learner_router
from app.api.parent import router as parent_router
from app.auth import Auth, require_site
from app.auth import router as auth_router
from app.config import Settings
from app.jobs import AI_KINDS, Worker
from app.logs import JsonlLog
from app.security import redact, register_secrets
from app.seed_legacy import seed_legacy
from app.storage import make_storage
from app.storage.base import BlobStore, Repository
from app.storage.local_blobs import LocalBlobStore

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
LEGACY_PATH = ROOT / "legacy" / "index.html"
WORKER_STOP_TIMEOUT_S = 5.0
MEDIA_TYPES = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
MEDIA_DIR = "images"  # the only blob prefix /media serves (DATA_DIR also holds secret_key, wordquest.db, logs/)

log = logging.getLogger("wordquest")


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class OpenAccessWarning:
    """ASGI middleware: warn once when a non-loopback client arrives and SITE_ACCESS_CODE is empty."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.warned = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not self.warned and not self.settings.site_access_code:
            client = scope.get("client")
            host = client[0] if client else ""
            if not _is_loopback(host):
                self.warned = True
                log.warning(
                    "WordQuest was reached from %s but SITE_ACCESS_CODE is empty: anyone on this "
                    "network can use it. Set SITE_ACCESS_CODE in .env.",
                    host,
                )
        await self.app(scope, receive, send)


def _seed_if_empty(repo: Repository, blobs: BlobStore) -> int:
    """First start only (no word lists yet): import the legacy starter set (Task 15). Runs in a worker thread."""
    if repo.list_lists():
        return 0
    return seed_legacy(repo, blobs, LEGACY_PATH)


def _log_worker_exit(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("Job worker stopped unexpectedly", exc_info=task.exception())


def _media_path(blobs: BlobStore | None, key: str) -> Path | None:
    """The image file for a /media key, or None (→ 404) for anything that is not a stored picture."""
    if not isinstance(blobs, LocalBlobStore):
        return None
    parts = key.split("/")
    if not key or key.startswith("/") or "\\" in key or "\x00" in key or any(p in ("", ".", "..") for p in parts):
        return None
    if len(parts) < 2 or parts[0] != MEDIA_DIR or Path(parts[-1]).suffix.lower() not in MEDIA_TYPES:
        return None
    try:
        images = (Path(blobs.root) / MEDIA_DIR).resolve()
        path = (Path(blobs.root) / key).resolve()  # resolves symlinks too
        if images not in path.parents or not path.is_file():
            return None
    except (OSError, RuntimeError):
        # OSError: a name the filesystem refuses (ENAMETOOLONG for a segment over 255 bytes; is_file() re-raises it).
        # RuntimeError: a symlink loop (Path.resolve() on Python 3.10-3.12). Either way: not a picture, so 404.
        return None
    return path


def _close_quietly(obj: object, what: str) -> None:
    close = getattr(obj, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:  # never mask the shutdown
            log.warning("Closing the %s failed: %s", what, redact(f"{type(exc).__name__}: {exc}"))


async def _aclose_quietly(obj: object, what: str) -> None:
    aclose = getattr(obj, "aclose", None)
    if callable(aclose):
        try:
            await aclose()
        except Exception as exc:  # never mask the shutdown
            log.warning("Closing the %s failed: %s", what, redact(f"{type(exc).__name__}: {exc}"))


def create_app(
    settings: Settings | None = None,
    *,
    repo: Repository | None = None,
    blobs: BlobStore | None = None,
    llm: LLMClient | None = None,
    image_provider: ImageProvider | None = None,
    start_worker: bool = True,
    seed: bool = True,
) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        # secret_values() lists only configured values; a key generated into DATA_DIR/secret_key is added here.
        register_secrets([*settings.secret_values(), settings.resolved_secret_key()])

        repo_, blobs_ = repo, blobs
        owned_repo: Repository | None = None  # closed on shutdown; injected objects belong to the caller
        if repo_ is None or blobs_ is None:
            made_repo, made_blobs = make_storage(settings)
            if repo_ is None:
                repo_ = owned_repo = made_repo
            else:
                _close_quietly(made_repo, "unused repository")
            blobs_ = blobs_ if blobs_ is not None else made_blobs

        if seed and LEGACY_PATH.exists():
            seeded = await asyncio.to_thread(_seed_if_empty, repo_, blobs_)
            if seeded:
                log.info("Seeded %d legacy starter words", seeded)

        # Task 14 wiring: the Worker exists first, because its make_on_request() hook (count every outbound AI
        # request, raise DailyCapReached past AI_DAILY_CALL_LIMIT) goes into the AI clients the Worker then uses.
        worker = Worker(repo=repo_, blobs=blobs_, generator=None, image_provider=None, settings=settings)
        on_request = worker.make_on_request()
        owned_llm: CerebrasClient | None = None
        llm_ = llm
        if llm_ is None and settings.ai_enabled:
            owned_llm = CerebrasClient(
                api_key=settings.cerebras_api_key,
                model=settings.cerebras_model,
                base_url=settings.cerebras_base_url,
                max_completion_tokens=settings.llm_max_completion_tokens,
                timeout_s=settings.llm_timeout_s,
                on_request=on_request,
            )
            llm_ = owned_llm

        generator: ContentGenerator | None = None
        if llm_ is not None:
            logs_dir = settings.data_dir / "logs"
            generator = ContentGenerator(
                llm_,
                model_name=getattr(llm_, "model", settings.cerebras_model),
                rejection_log=JsonlLog(logs_dir / "ai-rejections.jsonl"),
                usage_log=JsonlLog(logs_dir / "ai-usage.jsonl"),
                generation_reasoning_effort=settings.llm_reasoning_effort or None,
            )

        owned_provider: ImageProvider | None = None
        provider = image_provider
        image_config_error = ""
        if provider is None:
            try:
                provider = owned_provider = make_image_provider(settings, on_request)
            except ValueError as exc:  # bad IMAGE_* settings: start anyway, with pictures off (emoji scenes)
                image_config_error = redact(str(exc))
                log.error("Pictures are off because the image settings are invalid: %s", image_config_error)
        worker.generator = generator
        worker.image_provider = provider

        # Jobs parked by an earlier run (no key: up to NO_GENERATOR_DEFER_S; daily cap: until UTC midnight) resume
        # now, so "add the key / raise AI_DAILY_CALL_LIMIT, then restart" takes effect immediately.
        wake_kinds = sorted(AI_KINDS) if generator is not None else []
        if provider is not None:
            wake_kinds.append("image")
        if wake_kinds:
            woken = await asyncio.to_thread(repo_.wake_jobs, wake_kinds)
            if woken:
                log.info("Resumed %d waiting generation jobs", woken)

        app.state.repo = repo_
        app.state.blobs = blobs_
        app.state.auth = Auth(settings, repo_)
        app.state.generator = generator
        app.state.image_provider = provider
        app.state.image_config_error = image_config_error  # shown by GET /api/parent/status (Task 18)
        app.state.worker = worker

        stop = asyncio.Event()
        task: asyncio.Task | None = None
        if start_worker:
            task = asyncio.create_task(worker.run(stop))
            task.add_done_callback(_log_worker_exit)
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                try:
                    await asyncio.wait_for(task, timeout=WORKER_STOP_TIMEOUT_S)
                except asyncio.TimeoutError:
                    log.warning("Job worker did not stop within %.0fs and was cancelled", WORKER_STOP_TIMEOUT_S)
                except Exception:  # already logged by _log_worker_exit
                    pass
            # Close what this lifespan opened, the database last (the worker has stopped using it).
            if owned_llm is not None:
                await _aclose_quietly(owned_llm, "Cerebras client")
            if owned_provider is not None:
                await _aclose_quietly(owned_provider, "image provider")
            if owned_repo is not None:
                _close_quietly(owned_repo, "repository")

    # No /docs, /redoc or /openapi.json: they would list every route to anyone on the network.
    app = FastAPI(title="WordQuest", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.repo = repo
    app.state.blobs = blobs
    app.state.auth = None
    app.state.generator = None
    app.state.image_provider = image_provider
    app.state.image_config_error = ""
    app.state.worker = None

    app.add_middleware(OpenAccessWarning, settings=settings)
    app.include_router(auth_router)
    app.include_router(learner_router)
    app.include_router(parent_router)
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html", media_type="text/html", headers={"Cache-Control": "no-cache"})

    # Pictures only, and behind the site gate: same-origin <img> requests carry the wq_site cookie.
    @app.get("/media/{key:path}", include_in_schema=False, dependencies=[Depends(require_site)])
    def media(key: str, request: Request) -> FileResponse:
        path = _media_path(request.app.state.blobs, key)
        if path is None:
            raise HTTPException(status_code=404, detail="not_found")
        return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"))

    return app


app = create_app()

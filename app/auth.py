"""Site access code + parent passcode: signed cookies and attempt limiting (spec §9, §12)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, TimestampSigner
from pydantic import BaseModel, Field

from app import clock
from app.config import Settings
from app.security import safe_equal
from app.storage.base import Repository

SITE_COOKIE = "wq_site"
PARENT_COOKIE = "wq_parent"
SITE_MAX_AGE = 180 * 24 * 3600
PARENT_MAX_AGE = 12 * 3600
MAX_FAILURES = 5
WINDOW_S = 15 * 60

_COOKIE_NAMES = {"site": SITE_COOKIE, "parent": PARENT_COOKIE}
_MAX_AGES = {"site": SITE_MAX_AGE, "parent": PARENT_MAX_AGE}


def _client_ip(request: Request) -> str:
    # Phase 1: the socket peer address (spec §9). Phase 2 switches to X-Forwarded-For.
    return request.client.host if request.client else "unknown"


def _window_start() -> str:
    epoch = int(clock.utc_now().timestamp())
    return clock.iso(datetime.fromtimestamp(epoch - epoch % WINDOW_S, tz=timezone.utc))


class Auth:
    def __init__(self, settings: Settings, repo: Repository) -> None:
        self.settings = settings
        self.repo = repo
        self.signer = TimestampSigner(settings.resolved_secret_key())

    def site_required(self) -> bool:
        return bool(self.settings.site_access_code)

    def site_ok(self, request: Request) -> bool:
        if not self.site_required():
            return True
        return self._cookie_ok(request, "site")

    def parent_enabled(self) -> bool:
        return bool(self.settings.parent_passcode)

    def parent_ok(self, request: Request) -> bool:
        if not self.parent_enabled():
            return False
        return self._cookie_ok(request, "parent")

    def check_code(self, scope: str, given: str, request: Request) -> bool:
        """Every attempt counts against the window first; success clears the count.

        Counting before comparing means the right code is also refused once the limit is hit,
        so a blocked client learns nothing from further guesses.
        """
        expected = self._expected(scope)
        ip = _client_ip(request)
        count = self.repo.incr_auth_failure(scope, ip, _window_start())
        if count > MAX_FAILURES:
            raise HTTPException(status_code=429, detail="too_many_attempts")
        if expected and safe_equal(given, expected):
            self.repo.clear_auth_failures(scope, ip)
            return True
        return False

    def set_cookie(self, response: Response, scope: str, request: Request) -> None:
        response.set_cookie(
            _COOKIE_NAMES[scope],
            self.signer.sign(scope).decode("ascii"),
            max_age=_MAX_AGES[scope],
            path="/",
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )

    def clear_cookie(self, response: Response, scope: str) -> None:
        response.delete_cookie(_COOKIE_NAMES[scope], path="/", httponly=True, samesite="lax")

    def _expected(self, scope: str) -> str:
        if scope == "site":
            return self.settings.site_access_code
        if scope == "parent":
            return self.settings.parent_passcode
        raise ValueError(f"unknown auth scope: {scope!r}")

    def _cookie_ok(self, request: Request, scope: str) -> bool:
        raw = request.cookies.get(_COOKIE_NAMES[scope])
        if not raw:
            return False
        try:
            value = self.signer.unsign(raw, max_age=_MAX_AGES[scope])
        except BadSignature:  # includes SignatureExpired
            return False
        return value == scope.encode("ascii")


def _auth(request: Request) -> Auth:
    return request.app.state.auth


def require_site(request: Request) -> None:
    if not _auth(request).site_ok(request):
        raise HTTPException(status_code=401, detail="access_code_required")


def require_parent(request: Request) -> None:
    auth = _auth(request)
    if not auth.parent_enabled():
        raise HTTPException(status_code=403, detail="parent_disabled")
    if not auth.parent_ok(request):
        raise HTTPException(status_code=401, detail="parent_login_required")


class SiteLogin(BaseModel):
    code: str = Field(default="", max_length=200)


class ParentLogin(BaseModel):
    passcode: str = Field(default="", max_length=200)


router = APIRouter(prefix="/api/auth")


@router.post("/site")
def login_site(body: SiteLogin, request: Request, response: Response) -> dict:
    auth = _auth(request)
    if not auth.site_required():
        return {"ok": True}
    if not auth.check_code("site", body.code, request):
        raise HTTPException(status_code=401, detail="wrong_code")
    auth.set_cookie(response, "site", request)
    return {"ok": True}


@router.post("/parent", dependencies=[Depends(require_site)])
def login_parent(body: ParentLogin, request: Request, response: Response) -> dict:
    auth = _auth(request)
    if not auth.parent_enabled():
        raise HTTPException(status_code=403, detail="parent_disabled")
    if not auth.check_code("parent", body.passcode, request):
        raise HTTPException(status_code=401, detail="wrong_code")
    auth.set_cookie(response, "parent", request)
    return {"ok": True}


@router.post("/parent/logout", dependencies=[Depends(require_site)])
def logout_parent(request: Request, response: Response) -> dict:
    _auth(request).clear_cookie(response, "parent")
    return {"ok": True}

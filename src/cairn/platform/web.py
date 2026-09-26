"""HTTP surface of the platform: the auth gate for the whole server, FastAPI dependencies, and the routes under
``/api/auth``, ``/api/invites``, ``/api/teams``, ``/api/tokens``, ``/api/projects``, ``/api/users`` and ``/api/audit``.

Wire it with ``install(app, platform, config, on_sync=...)``.

* Local mode: every request acts as the local owner (a bearer token, if sent, narrows it). The Host header must be
  a loopback name (DNS-rebinding defence) and state changes from a foreign browser Origin are refused.
* Team mode: everything under ``/api``, ``/mcp`` and ``/openapi.json`` needs a session cookie or a bearer token,
  except health, login/logout, invite links and git webhooks. Cookie-authenticated state changes must carry
  ``X-CSRF-Token`` (from the login or ``/api/auth/me`` response); foreign Origins are refused as well.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection

from .config import LOCAL, ServerConfig, is_loopback
from .errors import AccountDisabled, AuthError, NotFound, PlatformError
from .models import ProjectRecord
from .rbac import ACTIONS, Principal
from .service import Platform

log = logging.getLogger("cairn.platform")

SESSION_COOKIE = "cairn_session"
SECURE_SESSION_COOKIE = "__Host-cairn_session"
CSRF_HEADER = "x-csrf-token"
STATE_KEY = "cairn_principal"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
PROTECTED_PREFIXES = ("/api/", "/mcp", "/openapi.json")
_WEBHOOK = r"^/api/projects/[^/]+/hooks/git$"
PUBLIC_PATHS = tuple(re.compile(p) for p in (
    r"^/api/health$", r"^/api/auth/login$", r"^/api/auth/logout$",
    r"^/api/session$",  # answers for the caller only (401 unless ?optional=1), and works mid password change
    r"^/api/invites/[^/]+$", r"^/api/invites/[^/]+/accept$", _WEBHOOK))
CSRF_EXEMPT = tuple(re.compile(p) for p in (r"^/api/auth/login$", _WEBHOOK))
WEBHOOK_RE = re.compile(_WEBHOOK)
MAX_WEBHOOK_BODY = 10 * 1024 * 1024
SECURITY_HEADERS = ((b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"same-origin"))


# ---- request inspection ------------------------------------------------------------------------------------
def is_protected(path: str) -> bool:
    if not (path == "/api" or path.startswith(PROTECTED_PREFIXES)):
        return False
    return not any(p.match(path) for p in PUBLIC_PATHS)


def _host_name(host_header: str) -> str:
    h = (host_header or "").strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def host_allowed(host_header: str, config: ServerConfig) -> bool:
    host = _host_name(host_header)
    patterns = [p.strip().lower() for p in config.allowed_hosts if p.strip()]
    if config.public_url:
        patterns.append((urlsplit(config.public_url).hostname or "").lower())
    if config.mode == LOCAL or config.public_url:
        if is_loopback(host):
            return True  # local mode, or a health check from the team server's own machine
        patterns += LOOPBACK_HOSTS
    elif not patterns:
        return True  # team mode with neither allowed_hosts nor public_url: any Host (install() warns)
    for pat in patterns:
        if pat == "*" or host == pat:
            return True
        if pat.startswith("*.") and host.endswith(pat[1:]):
            return True
        if pat.startswith(".") and (host.endswith(pat) or host == pat[1:]):
            return True
    return False


def request_scheme(conn: HTTPConnection, config: ServerConfig) -> str:
    if config.trust_proxy and conn.headers.get("x-forwarded-proto"):
        return conn.headers["x-forwarded-proto"].split(",")[0].strip().lower()
    return {"ws": "http", "wss": "https"}.get(conn.url.scheme, conn.url.scheme)


def client_ip(conn: HTTPConnection, config: ServerConfig) -> str:
    if config.trust_proxy and conn.headers.get("x-forwarded-for"):
        return conn.headers["x-forwarded-for"].split(",")[0].strip()[:64]
    return conn.client.host if conn.client else ""


def _origin(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else None


def origin_allowed(conn: HTTPConnection, config: ServerConfig) -> bool:
    """Browsers send Origin (or at least Referer) on state-changing requests; it must be this server."""
    origin = conn.headers.get("origin")
    if origin is None:
        referer = conn.headers.get("referer")
        if not referer:
            return True  # not a browser (curl, agents, CI)
        origin = _origin(referer)
        if origin is None:
            return False
    origin = origin.strip().lower().rstrip("/")
    if origin == "null":
        return False
    host = conn.headers.get("host", "").strip().lower()
    allowed = {f"http://{host}", f"https://{host}"}
    if config.public_url:
        allowed.add(_origin(config.public_url) or "")
    allowed |= {o.strip().lower().rstrip("/") for o in config.allowed_origins}
    return origin in allowed


def session_token(conn: HTTPConnection) -> str | None:
    return conn.cookies.get(SECURE_SESSION_COOKIE) or conn.cookies.get(SESSION_COOKIE)


def cookie_secure(conn: HTTPConnection, config: ServerConfig) -> bool:
    v = config.cookie_secure.lower()
    if v in ("true", "false"):
        return v == "true"
    return not (request_scheme(conn, config) == "http" and is_loopback(_host_name(conn.headers.get("host", ""))))


@dataclass
class Gate:
    principal: Principal | None = None
    status: int = 0
    detail: str = ""
    headers: dict | None = None


def evaluate(platform: Platform, config: ServerConfig, conn: HTTPConnection) -> Gate:
    """Authenticate one request and apply the host, origin, CSRF and login-required rules."""
    path = conn.url.path
    method = str(conn.scope.get("method", "GET")).upper()
    if not host_allowed(conn.headers.get("host", ""), config):
        return Gate(status=400, detail="unrecognised Host header")
    unsafe = method not in SAFE_METHODS
    if unsafe and not WEBHOOK_RE.match(path) and not origin_allowed(conn, config):
        return Gate(status=403, detail="cross-origin request refused")
    ip = client_ip(conn, config)
    protected = is_protected(path)
    principal: Principal | None
    if conn.headers.get("authorization"):
        principal = platform.principal_from_bearer(conn.headers["authorization"], ip=ip)
        if principal is None and protected:
            return Gate(status=401, detail="invalid or expired token",
                        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
    elif config.mode == LOCAL:
        principal = platform.local_principal(ip=ip)
    else:
        principal = platform.session_principal(session_token(conn), ip=ip)
    if (principal is not None and principal.kind == "session" and unsafe
            and not any(p.match(path) for p in CSRF_EXEMPT)
            and not platform.check_csrf(principal, conn.headers.get(CSRF_HEADER))):
        return Gate(status=403, detail="missing or invalid CSRF token")
    if protected and principal is None:
        return Gate(status=401, detail="authentication required", headers={"WWW-Authenticate": "Bearer"})
    if (principal is not None and principal.kind == "session" and principal.must_change_password and protected
            and not path.startswith("/api/auth/")):
        return Gate(status=403, detail="password change required")
    return Gate(principal=principal)


class AuthGate:
    """ASGI middleware: runs :func:`evaluate` on every HTTP/WebSocket request, rejects or stores the principal
    in ``scope["state"]["cairn_principal"]`` (mounted sub-apps such as the MCP endpoint read it from there)."""

    def __init__(self, app: Any, platform: Platform, config: ServerConfig):
        self.app, self.platform, self.config = app, platform, config

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        gate = await run_in_threadpool(evaluate, self.platform, self.config, HTTPConnection(scope))
        if gate.status:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": gate.detail})
                return
            await JSONResponse({"detail": gate.detail}, status_code=gate.status, headers=gate.headers)(
                scope, receive, send)
            return
        scope.setdefault("state", {})[STATE_KEY] = gate.principal
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        no_store = scope["path"].startswith("/api/auth/") or scope["path"] == "/api/session"  # identity, CSRF token

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in SECURITY_HEADERS:
                    if k.decode() not in headers:
                        headers.append(k.decode(), v.decode())
                if no_store:
                    headers["cache-control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ---- dependencies ------------------------------------------------------------------------------------------
def get_platform(request: HTTPConnection) -> Platform:
    plat = getattr(request.app.state, "cairn_platform", None)
    if plat is None:
        raise RuntimeError("cairn.platform.web.install() has not been called on this app")
    return plat


def get_config(request: HTTPConnection) -> ServerConfig:
    return getattr(request.app.state, "cairn_config", None) or get_platform(request).config


def optional_principal(request: Request) -> Principal | None:
    state = request.scope.get("state") or {}
    if STATE_KEY in state:
        return state[STATE_KEY]
    gate = evaluate(get_platform(request), get_config(request), request)  # the gate middleware is not installed
    if gate.status:
        raise HTTPException(gate.status, gate.detail, headers=gate.headers)
    request.scope.setdefault("state", {})[STATE_KEY] = gate.principal
    return gate.principal


def current_principal(principal: Principal | None = Depends(optional_principal)) -> Principal:
    if principal is None:
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
    return principal


def require(action: str, project_param: str = "pid", *, team_param: str = "tid",
            project_id: str | None = None) -> Callable[..., Principal]:
    """Dependency factory: ``Depends(require("project.write"))`` checks the action against the project named by
    the ``pid`` path/query parameter (or the fixed ``project_id``), else the team named by ``tid``. Returns the
    principal; answers 404 for projects the caller cannot see and 403 for insufficient roles."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")

    def dependency(request: Request, principal: Principal = Depends(current_principal)) -> Principal:
        pid = project_id or request.path_params.get(project_param) or request.query_params.get(project_param)
        tid = None if pid else (request.path_params.get(team_param) or request.query_params.get(team_param))
        try:
            get_platform(request).require(principal, action, project_id=pid, team_id=tid)
        except PlatformError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        return principal

    return dependency


def principal_from_bearer(platform: Platform, header: str | None, ip: str = "") -> Principal | None:
    """For MCP over HTTP and other non-FastAPI surfaces: ``Authorization`` header → Principal or None."""
    return platform.principal_from_bearer(header, ip=ip)


# ---- login throttling --------------------------------------------------------------------------------------
class Limiter:
    """Sliding-window failure counter per key, with bounded memory."""

    def __init__(self, window: float, *, max_keys: int = 20000, clock: Callable[[], float] = time.monotonic):
        self.window, self.max_keys, self.clock = float(window), max_keys, clock
        self._hits: OrderedDict[str, deque] = OrderedDict()
        self._lock = threading.Lock()

    def _live(self, key: str, now: float) -> deque:
        q = self._hits.get(key)
        if q is None:
            return deque()
        while q and now - q[0] >= self.window:
            q.popleft()
        return q

    def retry_after(self, keys: list[tuple[str, int]]) -> float:
        now = self.clock()
        with self._lock:
            waits = [self.window - (now - q[0]) for key, limit in keys if len(q := self._live(key, now)) >= limit]
        return max(waits, default=0.0)

    def hit(self, keys: list[tuple[str, int]]) -> None:
        now = self.clock()
        with self._lock:
            for key, _ in keys:
                q = self._hits.setdefault(key, deque())
                q.append(now)
                self._hits.move_to_end(key)
            while len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


class LoginGuard:
    """Per client+account limit, plus a looser per-client limit across accounts."""

    def __init__(self, attempts: int, window: float, **kw: Any):
        self.attempts = max(1, attempts)
        self.limiter = Limiter(window, **kw)

    def _keys(self, ip: str, subject: str) -> list[tuple[str, int]]:
        return [(f"pair:{ip}|{subject}", self.attempts), (f"ip:{ip}", self.attempts * 5)]

    def check(self, ip: str, subject: str) -> None:
        wait = self.limiter.retry_after(self._keys(ip, subject))
        if wait > 0:
            raise HTTPException(429, "too many attempts; try again later",
                                headers={"Retry-After": str(max(1, math.ceil(wait)))})

    def failed(self, ip: str, subject: str) -> None:
        self.limiter.hit(self._keys(ip, subject))

    def succeeded(self, ip: str, subject: str) -> None:
        self.limiter.reset(f"pair:{ip}|{subject}")


def _guard(request: Request) -> LoginGuard:
    g = getattr(request.app.state, "cairn_login_guard", None)
    if g is None:
        cfg = get_config(request)
        g = request.app.state.cairn_login_guard = LoginGuard(cfg.login_attempts, cfg.login_window)
    return g


# ---- background refresh + sync -----------------------------------------------------------------------------
class Syncer:
    """Runs refresh+sync for a project in a worker thread, coalescing bursts (e.g. several pushes) into one
    extra run instead of piling up concurrent syncs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, str] = {}

    def busy(self, project_id: str) -> bool:
        with self._lock:
            return project_id in self._state

    def run(self, app: Any, project_id: str, reason: str, refresh: bool = True) -> bool:
        with self._lock:
            if project_id in self._state:
                self._state[project_id] = "again"
                return False
            self._state[project_id] = "running"
        try:
            while True:
                self._once(app, project_id, reason, refresh)
                with self._lock:
                    if self._state.get(project_id) == "again":
                        self._state[project_id] = "running"
                        continue
                    self._state.pop(project_id, None)
                    return True
        except BaseException:
            with self._lock:
                self._state.pop(project_id, None)
            raise

    @staticmethod
    def _once(app: Any, project_id: str, reason: str, refresh: bool) -> None:
        platform: Platform = app.state.cairn_platform
        if refresh:
            try:
                platform.refresh_project(project_id)
            except PlatformError as exc:
                log.warning("refresh of project %s failed: %s", project_id, exc)
                return
        on_sync = getattr(app.state, "cairn_on_sync", None)
        project = platform.get_project(project_id)
        if on_sync is None or project is None:
            return
        try:
            on_sync(project, reason)
            platform.mark_synced(project_id)
        except Exception:
            log.exception("sync of project %s failed", project_id)


def _syncer(request: Request) -> Syncer:
    s = getattr(request.app.state, "cairn_syncer", None)
    if s is None:
        s = request.app.state.cairn_syncer = Syncer()
    return s


# ---- request bodies ----------------------------------------------------------------------------------------
class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(_In):
    email: str = Field(max_length=320)
    password: str = Field(max_length=4096)


class PasswordIn(_In):
    current_password: str | None = Field(default=None, max_length=4096)
    new_password: str = Field(max_length=4096)


class AcceptIn(_In):
    name: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=4096)


class TeamIn(_In):
    name: str = Field(max_length=200)
    slug: str | None = Field(default=None, max_length=63)


class TeamPatch(_In):
    name: str | None = Field(default=None, max_length=200)
    slug: str | None = Field(default=None, max_length=63)
    settings: dict | None = None


class InviteIn(_In):
    email: str = Field(max_length=320)
    role: str = "member"
    days: float | None = None


class RoleIn(_In):
    role: str


class ProjectRoleIn(_In):
    role: str | None = None


class TokenIn(_In):
    name: str = Field(max_length=200)
    team_id: str | None = None
    project_id: str | None = None
    scopes: list[str] | None = None
    expires_days: float | None = None


class ProjectIn(_In):
    team_id: str | None = None
    name: str | None = Field(default=None, max_length=200)
    slug: str | None = Field(default=None, max_length=63)
    path: str | None = Field(default=None, max_length=4096)
    git_url: str | None = Field(default=None, max_length=2048)
    branch: str | None = Field(default=None, max_length=255)


class ProjectPatch(_In):
    name: str | None = Field(default=None, max_length=200)
    slug: str | None = Field(default=None, max_length=63)
    settings: dict | None = None
    branch: str | None = Field(default=None, max_length=255)


# ---- routes ------------------------------------------------------------------------------------------------
class _Route(APIRoute):
    """Turns service errors into their HTTP status without needing an app-level exception handler."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def run(request: Request) -> Response:
            try:
                return await handler(request)
            except PlatformError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=exc.status)

        return run


router = APIRouter(route_class=_Route)


def _base_url(request: Request, config: ServerConfig) -> str:
    if config.public_url:
        return config.public_url.rstrip("/")
    return f"{request_scheme(request, config)}://{request.headers.get('host') or request.url.netloc}"


def _set_session(response: Response, request: Request, platform: Platform, token: str,
                 principal: Principal) -> None:
    config = get_config(request)
    secure = cookie_secure(request, config)
    expires = platform.session_expiry(principal.session_id or "")
    max_age = max(1, int(expires - platform.now())) if expires else int(config.session_days * 86400)
    response.set_cookie(SECURE_SESSION_COOKIE if secure else SESSION_COOKIE, token, max_age=max_age, path="/",
                        secure=secure, httponly=True, samesite="lax")


def _clear_session(response: Response, request: Request) -> None:
    response.delete_cookie(SECURE_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    response.delete_cookie(SESSION_COOKIE, path="/", secure=cookie_secure(request, get_config(request)),
                           httponly=True, samesite="lax")


def _me(platform: Platform, principal: Principal, config: ServerConfig) -> dict:
    return {**platform.describe(principal), "mode": config.mode, "csrf_token": platform.csrf_token(principal)}


def _project_json(platform: Platform, principal: Principal, p: ProjectRecord, config: ServerConfig) -> dict:
    perms = sorted(a for a in ACTIONS if a.startswith("project.") and platform.can(principal, a, project_id=p.id))
    out = p.public(include_paths="project.admin" in perms or config.mode == LOCAL)
    out["role"] = p.role or platform.effective_role(principal.user_id, project_id=p.id)
    out["permissions"] = perms
    return out


# auth -------------------------------------------------------------------------------------------------------
@router.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response) -> dict:
    platform, config = get_platform(request), get_config(request)
    if config.mode == LOCAL:
        raise HTTPException(400, "this server runs in local mode; no sign-in is needed")
    ip, subject = client_ip(request, config), body.email.strip().lower()[:254]
    guard = _guard(request)
    guard.check(ip, subject)
    try:
        token, principal = platform.login(body.email, body.password, user_agent=request.headers.get("user-agent", ""),
                                          ip=ip)
    except AccountDisabled as exc:  # right password, switched-off account: say so (403), still count the attempt
        guard.failed(ip, subject)
        raise HTTPException(403, str(exc)) from None
    except AuthError:
        guard.failed(ip, subject)
        raise HTTPException(401, "invalid email or password") from None
    guard.succeeded(ip, subject)
    old = session_token(request)
    if old:
        platform.logout(old)  # never keep a session id that existed before authentication
    _set_session(response, request, platform, token, principal)
    return _me(platform, principal, config)


@router.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    get_platform(request).logout(session_token(request))
    _clear_session(response, request)
    return {"ok": True}


@router.get("/api/auth/me")
def me(request: Request, principal: Principal = Depends(current_principal)) -> dict:
    return _me(get_platform(request), principal, get_config(request))


@router.post("/api/auth/password")
def change_password(body: PasswordIn, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    platform, config = get_platform(request), get_config(request)
    if principal.kind == "token":
        raise HTTPException(403, "API tokens cannot change passwords")
    ip, subject = client_ip(request, config), f"password:{principal.user_id}"
    guard = _guard(request)
    guard.check(ip, subject)
    try:
        platform.change_password(principal.user_id, body.current_password, body.new_password,
                                 keep_session_id=principal.session_id, actor=principal)
    except AuthError:
        guard.failed(ip, subject)
        raise HTTPException(403, "current password is incorrect") from None
    guard.succeeded(ip, subject)
    return {"ok": True}


@router.get("/api/auth/sessions")
def sessions(request: Request, principal: Principal = Depends(current_principal)) -> list[dict]:
    if principal.kind == "token":
        raise HTTPException(403, "API tokens cannot manage sessions")
    return [s.public(principal.session_id) for s in get_platform(request).list_sessions(principal.user_id)]


@router.delete("/api/auth/sessions/{sid}")
def end_session(sid: str, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    if principal.kind == "token":
        raise HTTPException(403, "API tokens cannot manage sessions")
    get_platform(request).revoke_session(principal.user_id, sid)
    return {"ok": True}


# invitations ------------------------------------------------------------------------------------------------
@router.get("/api/invites/{token}")
def view_invite(token: str, request: Request) -> dict:
    platform = get_platform(request)
    inv = platform.invitation_by_token(token)
    team = platform.team(inv.team_id)
    return {"team": {"id": team.id, "slug": team.slug, "name": team.name}, "email": inv.email, "role": inv.role,
            "expires_at": inv.expires_at, "account_exists": platform.user_by_email(inv.email) is not None}


@router.post("/api/invites/{token}/accept")
def accept_invite(token: str, request: Request, response: Response, body: AcceptIn | None = None,
                  principal: Principal | None = Depends(optional_principal)) -> dict:
    platform, config = get_platform(request), get_config(request)
    body = body or AcceptIn()
    if principal is not None and principal.kind == "token":
        raise HTTPException(403, "sign in (not with an API token) to accept an invitation")
    ip = client_ip(request, config)
    guard = _guard(request)
    guard.check(ip, "invite")
    inv = platform.invitation_by_token(token)
    try:
        user, role, created = platform.accept_invitation(token, user_id=principal.user_id if principal else None,
                                                         name=body.name, password=body.password)
    except AuthError:
        guard.failed(ip, "invite")
        raise
    out: dict[str, Any] = {"user": user.public(), "team": platform.team(inv.team_id).public(), "role": role,
                           "created": created}
    if principal is None and config.mode != LOCAL:
        tok, p = platform.create_session(user.id, user_agent=request.headers.get("user-agent", ""), ip=ip)
        _set_session(response, request, platform, tok, p)
        out["csrf_token"] = platform.csrf_token(p)
    return out


# teams ------------------------------------------------------------------------------------------------------
@router.get("/api/teams")
def list_teams(request: Request, principal: Principal = Depends(current_principal)) -> list[dict]:
    platform = get_platform(request)
    return [{**t.public(), **platform.team_stats(t.id)} for t in platform.list_teams(principal)]


@router.post("/api/teams", status_code=201)
def create_team(body: TeamIn, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    platform = get_platform(request)
    team = platform.create_team(body.name, slug=body.slug, actor=principal)
    return {**team.public(), "role": "owner", **platform.team_stats(team.id)}


@router.get("/api/teams/{tid}")
def get_team(tid: str, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    platform = get_platform(request)
    platform.require(principal, "team.read", team_id=tid)
    perms = sorted(a for a in ACTIONS if platform.can(principal, a, team_id=tid))
    return {**platform.team(tid).public(), "role": platform.member_role(tid, principal.user_id),
            "permissions": perms, **platform.team_stats(tid)}


@router.patch("/api/teams/{tid}")
def update_team(tid: str, body: TeamPatch, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    return get_platform(request).update_team(tid, name=body.name, slug=body.slug, settings=body.settings,
                                             actor=principal).public()


@router.delete("/api/teams/{tid}")
def delete_team(tid: str, request: Request, confirm: str = Query(""),
                principal: Principal = Depends(current_principal)) -> dict:
    platform = get_platform(request)
    platform.require(principal, "team.delete", team_id=tid)
    if confirm != platform.team(tid).slug:
        raise HTTPException(400, "pass ?confirm=<team slug> to delete a team")
    platform.delete_team(tid, actor=principal)
    return {"deleted": tid}


@router.get("/api/teams/{tid}/members")
def members(tid: str, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    platform = get_platform(request)
    out: dict[str, Any] = {"members": [m.public() for m in platform.list_members(tid, actor=principal)],
                           "invitations": []}
    if platform.can(principal, "team.members", team_id=tid):
        now = platform.now()
        out["invitations"] = [i.public(now) for i in platform.list_invitations(tid)]
    return out


@router.post("/api/teams/{tid}/members", status_code=201)
def invite(tid: str, body: InviteIn, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    platform, config = get_platform(request), get_config(request)
    inv, token = platform.invite(tid, body.email, body.role, actor=principal, days=body.days)
    return {"invitation": inv.public(platform.now()), "token": token,
            "url": f"{_base_url(request, config)}/?invite={token}"}


@router.patch("/api/teams/{tid}/members/{uid}")
def change_role(tid: str, uid: str, body: RoleIn, request: Request,
                principal: Principal = Depends(current_principal)) -> dict:
    get_platform(request).change_role(tid, uid, body.role, actor=principal)
    return {"ok": True}


@router.delete("/api/teams/{tid}/members/{uid}")
def remove_member(tid: str, uid: str, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    get_platform(request).remove_member(tid, uid, actor=principal)
    return {"ok": True}


@router.delete("/api/teams/{tid}/invitations/{iid}")
def revoke_invitation(tid: str, iid: str, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    platform = get_platform(request)
    platform.require(principal, "team.members", team_id=tid)
    if platform.invitation(iid).team_id != tid:
        raise NotFound("invitation not found")
    platform.revoke_invitation(iid, actor=principal)
    return {"ok": True}


# tokens -----------------------------------------------------------------------------------------------------
@router.get("/api/tokens")
def list_tokens(request: Request, team: str | None = None, all: bool = False, include_revoked: bool = False,
                principal: Principal = Depends(current_principal)) -> list[dict]:
    platform = get_platform(request)
    if all:
        if not team:
            raise HTTPException(400, "name a team (?team=) to list everyone's tokens")
        toks = platform.list_tokens(team_id=team, include_revoked=include_revoked, actor=principal)
    else:
        toks = platform.list_tokens(user_id=principal.user_id, team_id=team, include_revoked=include_revoked,
                                    actor=principal)
    now = platform.now()
    return [t.public(now) for t in toks]


@router.post("/api/tokens", status_code=201)
def issue_token(body: TokenIn, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    platform = get_platform(request)
    team_id = body.team_id
    if body.project_id and not team_id:
        p = platform.get_project(body.project_id)
        if p is None:
            raise NotFound("project not found")
        team_id = p.team_id
    if not team_id:
        teams = platform.list_teams(principal)
        if len(teams) != 1:
            raise HTTPException(400, "choose a team (team_id) for this token")
        team_id = teams[0].id
    tok, secret = platform.issue_token(principal.user_id, team_id, body.name, project_id=body.project_id,
                                       scopes=body.scopes, expires_days=body.expires_days, actor=principal)
    return {"token": tok.public(platform.now()), "secret": secret}


@router.delete("/api/tokens/{token_id}")
def revoke_token(token_id: str, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    platform = get_platform(request)
    return platform.revoke_token(token_id, actor=principal).public(platform.now())


# projects ---------------------------------------------------------------------------------------------------
@router.get("/api/projects")
def list_projects(request: Request, team: str | None = None,
                  principal: Principal = Depends(current_principal)) -> list[dict]:
    platform, config = get_platform(request), get_config(request)
    return [_project_json(platform, principal, p, config) for p in platform.list_projects(principal, team_id=team)]


@router.post("/api/projects", status_code=201)
def register_project(body: ProjectIn, request: Request, background: BackgroundTasks,
                     principal: Principal = Depends(current_principal)) -> dict:
    platform, config = get_platform(request), get_config(request)
    if bool(body.path) == bool(body.git_url):
        raise HTTPException(400, "give exactly one of path or git_url")
    team_id = body.team_id
    if not team_id and config.mode == LOCAL:
        platform.ensure_local_owner()
        team_id = platform.local_team_id
    if not team_id:
        raise HTTPException(400, "choose a team (team_id) for this project")
    if body.path:
        p = platform.register_local_project(body.path, team_id=team_id, name=body.name, slug=body.slug,
                                            actor=principal)
        if p.last_synced_at is None:
            background.add_task(_syncer(request).run, request.app, p.id, "registered", False)
    else:
        p = platform.register_git_project(team_id, body.git_url or "", branch=body.branch, name=body.name,
                                          slug=body.slug, clone=False, actor=principal)
        background.add_task(_syncer(request).run, request.app, p.id, "registered", True)
    return _project_json(platform, principal, p, config)


@router.get("/api/projects/{pid}")
def get_project(pid: str, request: Request, principal: Principal = Depends(require("project.read"))) -> dict:
    platform = get_platform(request)
    return _project_json(platform, principal, platform.project(pid), get_config(request))


@router.patch("/api/projects/{pid}")
def update_project(pid: str, body: ProjectPatch, request: Request,
                   principal: Principal = Depends(current_principal)) -> dict:
    platform = get_platform(request)
    p = platform.update_project(pid, name=body.name, slug=body.slug, settings=body.settings, branch=body.branch,
                                actor=principal)
    return _project_json(platform, principal, p, get_config(request))


@router.delete("/api/projects/{pid}")
def delete_project(pid: str, request: Request, purge: bool = True,
                   principal: Principal = Depends(current_principal)) -> dict:
    get_platform(request).delete_project(pid, purge=purge, actor=principal)
    return {"deleted": pid}


@router.post("/api/projects/{pid}/refresh", status_code=202)
def refresh_project(pid: str, request: Request, background: BackgroundTasks,
                    principal: Principal = Depends(require("project.sync"))) -> dict:
    syncer = _syncer(request)
    queued = syncer.busy(pid)
    background.add_task(syncer.run, request.app, pid, "manual", True)
    return {"accepted": True, "queued_after_current": queued}


@router.post("/api/projects/{pid}/webhook")
def rotate_webhook(pid: str, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    platform, config = get_platform(request), get_config(request)
    secret = platform.rotate_webhook_secret(pid, actor=principal)
    return {"secret": secret, "url": f"{_base_url(request, config)}/api/projects/{pid}/hooks/git",
            "content_type": "application/json", "events": ["push"],
            "note": "GitHub/Gitea/Bitbucket: use it as the webhook secret. GitLab: use it as the secret token."}


@router.get("/api/projects/{pid}/members")
def project_members(pid: str, request: Request, principal: Principal = Depends(current_principal)) -> list[dict]:
    return get_platform(request).project_members(pid, actor=principal)


@router.put("/api/projects/{pid}/members/{uid}")
def set_project_role(pid: str, uid: str, body: ProjectRoleIn, request: Request,
                     principal: Principal = Depends(current_principal)) -> dict:
    get_platform(request).set_project_role(pid, uid, body.role, actor=principal)
    return {"ok": True}


@router.delete("/api/projects/{pid}/members/{uid}")
def clear_project_role(pid: str, uid: str, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    get_platform(request).set_project_role(pid, uid, None, actor=principal)
    return {"ok": True}


@router.post("/api/projects/{pid}/hooks/git")
async def git_webhook(pid: str, request: Request, background: BackgroundTasks) -> Response:
    platform = get_platform(request)
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_WEBHOOK_BODY:
        return JSONResponse({"detail": "payload too large"}, status_code=413)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_WEBHOOK_BODY:
            return JSONResponse({"detail": "payload too large"}, status_code=413)
    try:
        result = await run_in_threadpool(platform.verify_webhook, pid, dict(request.headers), bytes(body))
    except (AuthError, NotFound):
        return JSONResponse({"detail": "invalid webhook signature"}, status_code=401)
    if result.sync:
        background.add_task(_syncer(request).run, request.app, pid, "webhook", True)
    return JSONResponse(result.public(), status_code=202 if result.sync else 200, background=background)


# users (server admins) --------------------------------------------------------------------------------------
@router.get("/api/users")
def list_users(request: Request, principal: Principal = Depends(current_principal)) -> list[dict]:
    return [u.public() for u in get_platform(request).list_users(actor=principal)]


@router.post("/api/users/{uid}/disable")
def disable_user(uid: str, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    get_platform(request).disable_user(uid, actor=principal)
    return {"ok": True}


@router.post("/api/users/{uid}/enable")
def enable_user(uid: str, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    get_platform(request).enable_user(uid, actor=principal)
    return {"ok": True}


@router.post("/api/users/{uid}/reset-password")
def reset_password(uid: str, request: Request, principal: Principal = Depends(current_principal)) -> dict:
    return {"password": get_platform(request).reset_password(uid, actor=principal), "must_change": True}


# audit ------------------------------------------------------------------------------------------------------
@router.get("/api/audit")
def audit(request: Request, team: str | None = None, project: str | None = None,
          limit: int = Query(100, ge=1, le=1000), before: float | None = None,
          principal: Principal = Depends(current_principal)) -> list[dict]:
    entries = get_platform(request).audit_entries(team_id=team, project_id=project, limit=limit, before=before,
                                                  actor=principal)
    return [e.public() for e in entries]


# ---- wiring ------------------------------------------------------------------------------------------------
async def _error_handler(_request: Request, exc: Exception) -> JSONResponse:
    status = exc.status if isinstance(exc, PlatformError) else 500
    return JSONResponse({"detail": str(exc)}, status_code=status)


def install(app: FastAPI, platform: Platform, config: ServerConfig | None = None, *,
            on_sync: Callable[[ProjectRecord, str], Any] | None = None, include_router: bool = True) -> None:
    """Attach the platform to ``app``: auth gate middleware, service-error handler, and the routes.

    ``on_sync(project, reason)`` is called in a worker thread after a webhook, a manual refresh or a new
    registration (reason ``webhook`` | ``manual`` | ``registered``), once the managed clone is up to date. It should
    run the Cairn sync for ``project.root`` to completion; the project is then marked synced."""
    config = config or platform.config
    if config.mode != LOCAL and not (config.allowed_hosts or config.public_url):
        log.warning("team mode without server.public_url or server.allowed_hosts accepts any Host header; "
                    "set public_url to pin it")
    app.state.cairn_platform = platform
    app.state.cairn_config = config
    app.state.cairn_on_sync = on_sync
    app.state.cairn_login_guard = LoginGuard(config.login_attempts, config.login_window)
    app.state.cairn_syncer = Syncer()
    if config.mode == LOCAL:
        platform.ensure_local_owner()
    app.add_middleware(AuthGate, platform=platform, config=config)
    app.add_exception_handler(PlatformError, _error_handler)
    if include_router:
        app.include_router(router)

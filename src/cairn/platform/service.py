"""The platform service: users, teams, members, projects, roles, API tokens, web sessions, webhooks and audit.

Every mutating method takes ``actor``: the :class:`Principal` on whose behalf it runs. ``actor=None`` means a
trusted local operator (the CLI, which already has the database file) and skips permission checks; the web
layer always passes the request's principal, so authorisation is enforced here, not only at the edge.
"""
from __future__ import annotations

import getpass
import json
import logging
import re
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from . import gitops, security
from .config import ServerConfig, cairn_home, ensure_home
from .db import connect
from .errors import (AccountDisabled, AuthError, Conflict, Expired, GitError, InvalidInput, NotFound,
                     PermissionDenied)
from .models import (
    ApiToken,
    AuditEntry,
    Invitation,
    Member,
    ProjectRecord,
    Team,
    User,
    WebhookResult,
    WebSession,
)
from .rbac import ACTIONS, OVERRIDE_ROLES, RANK, ROLES, Principal, normalize_scopes, role_allows

log = logging.getLogger("cairn.platform")

DAY = 86400.0
TOUCH_EVERY = 60.0          # seconds between last_used / last_seen writes
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
LOCAL_EMAIL = "owner@localhost"
MAX_SETTINGS = 64 * 1024


# ---- validation helpers ------------------------------------------------------------------------------------
def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:63].strip("-")
    return s or "item"


def normalize_email(email: str | None) -> str:
    e = (email or "").strip().lower()
    if len(e) > 254 or not EMAIL_RE.match(e):
        raise InvalidInput("a valid email address is required")
    return e


def check_password(password: Any, *, email: str | None = None) -> str:
    if not isinstance(password, str) or len(password) < security.MIN_PASSWORD:
        raise InvalidInput(f"passwords need at least {security.MIN_PASSWORD} characters")
    if len(password) > security.MAX_PASSWORD:
        raise InvalidInput(f"passwords are limited to {security.MAX_PASSWORD} characters")
    if not password.strip():
        raise InvalidInput("password must not be blank")
    if email and password.strip().lower() == email.lower():
        raise InvalidInput("password must not be your email address")
    return password


def _name(value: str | None, what: str, *, required: bool = True) -> str:
    v = " ".join((value or "").split())
    if required and not v:
        raise InvalidInput(f"{what} is required")
    if len(v) > 120:
        raise InvalidInput(f"{what} is limited to 120 characters")
    return v


def _slug(value: str) -> str:
    v = (value or "").strip().lower()
    if not SLUG_RE.match(v):
        raise InvalidInput("slugs use lowercase letters, digits and dashes (1-63 characters)")
    return v


def _settings(value: Any) -> str:
    if not isinstance(value, dict):
        raise InvalidInput("settings must be an object")
    text = json.dumps(value, sort_keys=True)
    if len(text) > MAX_SETTINGS:
        raise InvalidInput("settings are too large")
    return text


def _local_name() -> str:
    try:
        return getpass.getuser() or "Local owner"
    except Exception:
        return "Local owner"


class Platform:
    def __init__(self, home: Path | str | None = None, *, config: ServerConfig | None = None,
                 clock: Callable[[], float] = time.time):
        self.home = ensure_home(Path(home).expanduser().resolve() if home else cairn_home())
        self.config = config or ServerConfig.load(home=self.home)
        self._deleted_listeners: list = []
        self.db_path = self.home / "platform.db"
        self._clock = clock
        self._lock = threading.RLock()
        self._depth = 0
        self._conn = connect(self.db_path)
        self._key = security.load_server_key(self.home)
        self._git_locks: dict[str, threading.Lock] = {}

    @classmethod
    def open(cls, home: Path | str | None = None, **kw: Any) -> Platform:
        return cls(home, **kw)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def now(self) -> float:
        return self._clock()

    @property
    def repos_dir(self) -> Path:
        return Path(self.config.repos_dir).expanduser().resolve() if self.config.repos_dir else self.home / "repos"

    # ---- database plumbing ---------------------------------------------------------------------------------
    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield self._conn
                finally:
                    self._depth -= 1
                return
            self._conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")
            finally:
                self._depth = 0

    def _rows(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def _row(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    def _meta(self, key: str) -> str | None:
        r = self._row("SELECT value FROM meta WHERE key = ?", (key,))
        return r["value"] if r else None

    def _set_meta(self, c: sqlite3.Connection, key: str, value: str | None) -> None:
        if value is None:
            c.execute("DELETE FROM meta WHERE key = ?", (key,))
        else:
            c.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (key, value))

    # ---- audit ---------------------------------------------------------------------------------------------
    def _audit(self, c: sqlite3.Connection, action: str, actor: Principal | None, *, target: str = "",
               team_id: str | None = None, project_id: str | None = None, detail: dict | None = None,
               ip: str | None = None) -> None:
        c.execute("INSERT INTO audit_log(ts, actor_id, actor, action, target, team_id, project_id, ip, detail) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (self.now(), actor.user_id if actor else None, actor.label if actor else "system", action, target,
                   team_id, project_id, ip if ip is not None else (actor.ip if actor else ""),
                   json.dumps(detail or {}, sort_keys=True, default=str)))

    def audit(self, action: str, *, actor: Principal | None = None, target: str = "", team_id: str | None = None,
              project_id: str | None = None, detail: dict | None = None, ip: str | None = None) -> None:
        """Record an event (the lead can log its own actions, e.g. ``project.sync``). Never pass secrets."""
        with self._tx() as c:
            self._audit(c, action, actor, target=target, team_id=team_id, project_id=project_id, detail=detail,
                        ip=ip)

    def audit_entries(self, *, team_id: str | None = None, project_id: str | None = None, limit: int = 100,
                      before: float | None = None, actor: Principal | None = None) -> list[AuditEntry]:
        if actor is not None:
            if team_id:
                self.require(actor, "team.audit", team_id=team_id)
            elif project_id:
                self.require(actor, "project.admin", project_id=project_id)
            elif not actor.is_admin or actor.kind == "token":
                raise PermissionDenied("only server admins can read the whole audit log")
        where: list[str] = []
        args: list[Any] = []
        if team_id:
            # Sign-ins are recorded without a team: show those of the team's current members (a failed attempt
            # counts when the account it tried is a member). One query, so no row appears twice.
            where.append("(team_id = ? OR (team_id IS NULL AND action LIKE 'auth.%' AND ("
                         "actor_id IN (SELECT user_id FROM memberships WHERE team_id = ?) OR "
                         "(action = 'auth.login_failed' AND actor_id IS NULL AND target IN "
                         "(SELECT 'email:' || u.email FROM memberships m JOIN users u ON u.id = m.user_id "
                         "WHERE m.team_id = ?)))))")
            args += [team_id, team_id, team_id]
        if project_id:
            where.append("project_id = ?")
            args.append(project_id)
        if before is not None:
            where.append("ts < ?")
            args.append(before)
        sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        return [AuditEntry.from_row(r) for r in self._rows(sql, (*args, max(1, min(int(limit), 1000))))]

    # ---- principals and permissions ------------------------------------------------------------------------
    def _principal(self, r: sqlite3.Row, kind: str, **extra: Any) -> Principal:
        return Principal(user_id=r["id"], email=r["email"], name=r["name"], kind=kind, is_admin=bool(r["is_admin"]),
                         must_change_password=bool(r["must_change_password"]), **extra)

    def effective_role(self, user_id: str, *, project_id: str | None = None,
                       team_id: str | None = None) -> str | None:
        """The user's role for a project (team role, replaced by a per-project override) or a team."""
        if project_id:
            r = self._row("SELECT p.team_id, m.role AS team_role, pr.role AS override FROM projects p "
                          "LEFT JOIN memberships m ON m.team_id = p.team_id AND m.user_id = ? "
                          "LEFT JOIN project_roles pr ON pr.project_id = p.id AND pr.user_id = ? WHERE p.id = ?",
                          (user_id, user_id, project_id))
            if r is None or not r["team_role"] or (team_id and team_id != r["team_id"]):
                return None
            if r["team_role"] == "owner" or not r["override"]:
                return r["team_role"]
            return None if r["override"] == "none" else r["override"]
        if team_id:
            r = self._row("SELECT role FROM memberships WHERE team_id = ? AND user_id = ?", (team_id, user_id))
            return r["role"] if r else None
        return None

    def can(self, principal: Principal | None, action: str, project_id: str | None = None,
            team_id: str | None = None) -> bool:
        """May ``principal`` perform ``action`` on the project (or, without one, the team)?"""
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        if principal is None or (project_id is None and team_id is None):
            return False
        if action.startswith("team.") and project_id:
            # Team actions are decided by the team role only; a project override never grants them.
            p = self._row("SELECT team_id FROM projects WHERE id = ?", (project_id,))
            if p is None or (team_id and team_id != p["team_id"]):
                return False
            team_id, project_id = p["team_id"], None
        u = self._row("SELECT disabled FROM users WHERE id = ?", (principal.user_id,))
        if u is None or u["disabled"]:
            return False
        if principal.kind == "token":
            if not principal.scope_allows(action):
                return False
            if principal.token_project_id and (action.startswith("team.") or project_id != principal.token_project_id):
                return False
            tid = team_id
            if project_id:
                p = self._row("SELECT team_id FROM projects WHERE id = ?", (project_id,))
                tid = p["team_id"] if p else None
            if tid != principal.token_team_id:
                return False
        return role_allows(self.effective_role(principal.user_id, project_id=project_id, team_id=team_id), action)

    def require(self, principal: Principal | None, action: str, project_id: str | None = None,
                team_id: str | None = None) -> None:
        """Raise unless allowed. Things the principal cannot even see are reported as not found."""
        if self.can(principal, action, project_id, team_id):
            return
        if project_id and not self.can(principal, "project.read", project_id):
            raise NotFound("project not found")
        if not project_id and team_id and not self.can(principal, "team.read", team_id=team_id):
            raise NotFound("team not found")
        raise PermissionDenied(f"your role does not allow {action}")

    def _require_server_admin(self, actor: Principal) -> None:
        if not actor.is_admin or actor.kind == "token":
            raise PermissionDenied("only server admins can do this")

    def describe(self, principal: Principal) -> dict:
        """``/api/auth/me`` payload: who this is and what they belong to."""
        user = self.user(principal.user_id)
        return {**principal.public(), "user": user.public(),
                "teams": [t.public() for t in self.list_teams(principal)]}

    # ---- users ---------------------------------------------------------------------------------------------
    def get_user(self, user_id: str) -> User | None:
        r = self._row("SELECT * FROM users WHERE id = ?", (user_id,))
        return User.from_row(r) if r else None

    def user(self, user_id: str) -> User:
        u = self.get_user(user_id)
        if u is None:
            raise NotFound("user not found")
        return u

    def user_by_email(self, email: str) -> User | None:
        try:
            e = normalize_email(email)
        except InvalidInput:
            return None
        r = self._row("SELECT * FROM users WHERE email = ?", (e,))
        return User.from_row(r) if r else None

    def resolve_user(self, ref: str) -> User:
        u = self.get_user(ref) or self.user_by_email(ref)
        if u is None:
            raise NotFound(f"no user {ref!r}")
        return u

    def list_users(self, *, actor: Principal | None = None) -> list[User]:
        if actor is not None:
            self._require_server_admin(actor)
        return [User.from_row(r) for r in self._rows("SELECT * FROM users ORDER BY created_at")]

    def create_user(self, email: str, name: str = "", password: str | None = None, *, is_admin: bool = False,
                    must_change_password: bool = False, actor: Principal | None = None) -> User:
        if actor is not None:
            self._require_server_admin(actor)
        email = normalize_email(email)
        name = _name(name, "name", required=False) or email.split("@")[0]
        pw_hash = security.hash_password(check_password(password, email=email)) if password is not None else None
        uid = security.new_id("u")
        with self._tx() as c:
            if c.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise Conflict("an account with this email already exists")
            c.execute("INSERT INTO users(id, email, name, password_hash, is_admin, must_change_password, created_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (uid, email, name, pw_hash, int(is_admin), int(must_change_password), self.now()))
            self._audit(c, "user.create", actor, target=f"user:{uid}", detail={"email": email, "admin": is_admin})
        return self.user(uid)

    def authenticate(self, email: str, password: str, *, disabled_error: bool = False) -> User | None:
        """Check a login. Costs one scrypt whether or not the account exists, is disabled or has a password:
        a disabled account's password is checked like any other. None for a wrong password, a missing account or
        (unless ``disabled_error``) a disabled one; with ``disabled_error`` a disabled account whose password is
        right raises :class:`AccountDisabled`, which reveals nothing to someone without the password."""
        try:
            e = normalize_email(email)
        except InvalidInput:
            e = None
        r = self._row("SELECT * FROM users WHERE email = ?", (e,)) if e else None
        stored = r["password_hash"] if r is not None else None
        if not security.verify_password(password if isinstance(password, str) else "", stored) or r is None:
            return None
        if r["disabled"]:
            if disabled_error:
                raise AccountDisabled()
            return None
        if security.needs_rehash(stored):
            new = security.hash_password(password)
            with self._tx() as c:
                c.execute("UPDATE users SET password_hash = ? WHERE id = ? AND password_hash = ?", (new, r["id"], stored))
        return User.from_row(r)

    def set_password(self, user_id: str, password: str, *, must_change: bool = False,
                     keep_session_id: str | None = None, actor: Principal | None = None,
                     _action: str = "user.password_set") -> None:
        """Set a password and sign out every other browser session of that user."""
        user = self.user(user_id)
        if actor is not None and actor.kind == "token":
            raise PermissionDenied("API tokens cannot change passwords")
        if actor is not None and actor.user_id != user_id:
            self._require_server_admin(actor)
        pw_hash = security.hash_password(check_password(password, email=user.email))
        with self._tx() as c:
            c.execute("UPDATE users SET password_hash = ?, must_change_password = ? WHERE id = ?",
                      (pw_hash, int(must_change), user_id))
            c.execute("DELETE FROM web_sessions WHERE user_id = ? AND id IS NOT ?", (user_id, keep_session_id))
            self._audit(c, _action, actor, target=f"user:{user_id}")

    def change_password(self, user_id: str, current: str | None, new: str, *, keep_session_id: str | None = None,
                        actor: Principal | None = None) -> None:
        if actor is not None and actor.kind == "token":
            raise PermissionDenied("API tokens cannot change passwords")
        r = self._row("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        if r is None:
            raise NotFound("user not found")
        if r["password_hash"] and not security.verify_password(current or "", r["password_hash"]):
            raise AuthError("current password is incorrect")
        if current is not None and current == new:
            raise InvalidInput("the new password must differ from the current one")
        self.set_password(user_id, new, keep_session_id=keep_session_id, actor=actor, _action="user.password_change")

    def reset_password(self, user_id: str, *, actor: Principal | None = None) -> str:
        """Replace a password with a generated one the user must change at next login. Returns it once."""
        if actor is not None:
            self._require_server_admin(actor)
        otp = security.one_time_password()
        self.set_password(user_id, otp, must_change=True, _action="user.password_reset", actor=actor)
        return otp

    def _last_active_owner_teams(self, c: sqlite3.Connection, user_id: str) -> list[str]:
        rows = c.execute(
            "SELECT t.slug FROM memberships m JOIN teams t ON t.id = m.team_id WHERE m.user_id = ? AND m.role = 'owner' "
            "AND NOT EXISTS (SELECT 1 FROM memberships o JOIN users u ON u.id = o.user_id WHERE o.team_id = m.team_id "
            "AND o.role = 'owner' AND o.user_id != m.user_id AND u.disabled = 0)", (user_id,)).fetchall()
        return [r["slug"] for r in rows]

    def disable_user(self, user_id: str, *, actor: Principal | None = None) -> None:
        if actor is not None:
            self._require_server_admin(actor)
            if actor.user_id == user_id:
                raise Conflict("you cannot disable your own account")
        with self._tx() as c:
            r = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if r is None:
                raise NotFound("user not found")
            teams = self._last_active_owner_teams(c, user_id)
            if teams:
                raise Conflict(f"this user is the only owner of {', '.join(teams)}; make someone else owner first")
            if r["is_admin"] and c.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND disabled = 0 "
                                           "AND id != ?", (user_id,)).fetchone()[0] == 0:
                raise Conflict("this is the last active server admin")
            c.execute("UPDATE users SET disabled = 1 WHERE id = ?", (user_id,))
            c.execute("DELETE FROM web_sessions WHERE user_id = ?", (user_id,))
            self._audit(c, "user.disable", actor, target=f"user:{user_id}")

    def enable_user(self, user_id: str, *, actor: Principal | None = None) -> None:
        if actor is not None:
            self._require_server_admin(actor)
        with self._tx() as c:
            if c.execute("UPDATE users SET disabled = 0 WHERE id = ?", (user_id,)).rowcount == 0:
                raise NotFound("user not found")
            self._audit(c, "user.enable", actor, target=f"user:{user_id}")

    def set_server_admin(self, user_id: str, value: bool, *, actor: Principal | None = None) -> None:
        if actor is not None:
            self._require_server_admin(actor)
        with self._tx() as c:
            if not value and c.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND disabled = 0 AND id != ?",
                                       (user_id,)).fetchone()[0] == 0:
                raise Conflict("this is the last active server admin")
            if c.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(value), user_id)).rowcount == 0:
                raise NotFound("user not found")
            self._audit(c, "user.admin", actor, target=f"user:{user_id}", detail={"admin": value})

    # ---- solo / local mode ---------------------------------------------------------------------------------
    def ensure_local_owner(self) -> Principal:
        """Provision (once) the local owner and their "Personal" team so a solo developer never logs in."""
        name = _local_name()
        with self._tx() as c:
            uid = (c.execute("SELECT value FROM meta WHERE key = 'local_owner_id'").fetchone() or [None])[0]
            r = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone() if uid else None
            if r is None:
                uid, email, n = security.new_id("u"), LOCAL_EMAIL, 1
                while c.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                    n += 1
                    email = f"owner{n}@localhost"
                c.execute("INSERT INTO users(id, email, name, is_admin, is_local, created_at) VALUES (?, ?, ?, 1, 1, ?)",
                          (uid, email, name, self.now()))
                self._set_meta(c, "local_owner_id", uid)
                self._audit(c, "user.create", None, target=f"user:{uid}", detail={"email": email, "local": True})
                r = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            tid = (c.execute("SELECT value FROM meta WHERE key = 'local_team_id'").fetchone() or [None])[0]
            if not tid or not c.execute("SELECT 1 FROM teams WHERE id = ?", (tid,)).fetchone():
                tid = self._insert_team(c, "Personal", None, uid, None)
                self._set_meta(c, "local_team_id", tid)
            elif not c.execute("SELECT 1 FROM memberships WHERE team_id = ? AND user_id = ?", (tid, uid)).fetchone():
                c.execute("INSERT INTO memberships(team_id, user_id, role, created_at) VALUES (?, ?, 'owner', ?)",
                          (tid, uid, self.now()))
        return self._principal(r, "local")

    @property
    def local_team_id(self) -> str | None:
        return self._meta("local_team_id")

    def local_principal(self, ip: str = "") -> Principal:
        uid = self._meta("local_owner_id")
        r = self._row("SELECT * FROM users WHERE id = ?", (uid,)) if uid else None
        if r is None or not self.local_team_id:
            return replace(self.ensure_local_owner(), ip=ip)
        return self._principal(r, "local", ip=ip)

    def bootstrap_owner(self, email: str, name: str, *, password: str | None = None,
                        team_name: str | None = None) -> tuple[User, Team, str | None]:
        """First owner for team mode (``cairn team init``). Adopts the solo-mode owner (keeping their projects)
        when there is one. Returns ``(user, team, one_time_password_or_None)``."""
        email = normalize_email(email)
        name = _name(name, "name")
        otp = None
        if password is None:
            otp = password = security.one_time_password()
        pw_hash = security.hash_password(check_password(password, email=email))
        with self._tx() as c:
            if c.execute("SELECT 1 FROM users WHERE is_admin = 1 AND is_local = 0").fetchone():
                raise Conflict("this server already has an admin; use `cairn user` and `cairn team` commands")
            if c.execute("SELECT 1 FROM users WHERE email = ? AND is_local = 0", (email,)).fetchone():
                raise Conflict("an account with this email already exists")
            local_uid = (c.execute("SELECT value FROM meta WHERE key = 'local_owner_id'").fetchone() or [None])[0]
            local = c.execute("SELECT * FROM users WHERE id = ?", (local_uid,)).fetchone() if local_uid else None
            if local is not None:
                uid = local["id"]
                c.execute("UPDATE users SET email = ?, name = ?, password_hash = ?, is_admin = 1, is_local = 0, "
                          "must_change_password = ?, disabled = 0 WHERE id = ?",
                          (email, name, pw_hash, int(otp is not None), uid))
            else:
                uid = security.new_id("u")
                c.execute("INSERT INTO users(id, email, name, password_hash, is_admin, must_change_password, "
                          "created_at) VALUES (?, ?, ?, ?, 1, ?, ?)", (uid, email, name, pw_hash, int(otp is not None),
                                                                       self.now()))
            local_tid = (c.execute("SELECT value FROM meta WHERE key = 'local_team_id'").fetchone() or [None])[0]
            if local is not None and local_tid and c.execute("SELECT 1 FROM teams WHERE id = ?", (local_tid,)).fetchone():
                tid = local_tid
                if team_name:
                    new_name = _name(team_name, "team name")
                    slug = self._unique_slug(c, "SELECT 1 FROM teams WHERE slug = ? AND id != ?", (tid,), slugify(new_name))
                    c.execute("UPDATE teams SET name = ?, slug = ? WHERE id = ?", (new_name, slug, tid))
            else:
                tid = self._insert_team(c, team_name or f"{name}'s team", None, uid, None)
                if local_uid is None:
                    self._set_meta(c, "local_owner_id", uid)
                    self._set_meta(c, "local_team_id", tid)
            self._audit(c, "server.bootstrap", None, target=f"user:{uid}", team_id=tid,
                        detail={"email": email, "adopted_local_owner": local is not None})
        return self.user(uid), self.team(tid), otp

    # ---- teams ---------------------------------------------------------------------------------------------
    def _unique_slug(self, c: sqlite3.Connection, exists_sql: str, extra: tuple, base: str) -> str:
        base = base[:58].strip("-") or "item"
        for i in range(1, 1000):
            cand = base if i == 1 else f"{base}-{i}"
            if not c.execute(exists_sql, (cand, *extra)).fetchone():
                return cand
        raise Conflict("could not find a free slug")

    def _insert_team(self, c: sqlite3.Connection, name: str, slug: str | None, owner_id: str,
                     actor: Principal | None) -> str:
        name = _name(name, "team name")
        if slug is None:
            slug = self._unique_slug(c, "SELECT 1 FROM teams WHERE slug = ?", (), slugify(name))
        else:
            slug = _slug(slug)
            if c.execute("SELECT 1 FROM teams WHERE slug = ?", (slug,)).fetchone():
                raise Conflict(f"team slug {slug!r} is taken")
        tid, now = security.new_id("t"), self.now()
        c.execute("INSERT INTO teams(id, slug, name, created_at) VALUES (?, ?, ?, ?)", (tid, slug, name, now))
        c.execute("INSERT INTO memberships(team_id, user_id, role, created_at) VALUES (?, ?, 'owner', ?)",
                  (tid, owner_id, now))
        self._audit(c, "team.create", actor, target=f"team:{tid}", team_id=tid, detail={"name": name, "slug": slug})
        return tid

    def create_team(self, name: str, *, slug: str | None = None, owner_id: str | None = None,
                    actor: Principal | None = None) -> Team:
        if actor is not None:
            if actor.kind == "token":
                raise PermissionDenied("API tokens cannot create teams")
            if self.config.team_creation != "anyone" and not actor.is_admin:
                raise PermissionDenied("only server admins can create teams on this server")
            owner_id = owner_id or actor.user_id
            if owner_id != actor.user_id and not actor.is_admin:
                raise PermissionDenied("you can only create teams you own")
        if not owner_id:
            raise InvalidInput("a team needs an owner")
        owner = self.user(owner_id)
        if owner.disabled:
            raise InvalidInput("the owner's account is disabled")
        with self._tx() as c:
            tid = self._insert_team(c, name, slug, owner_id, actor)
        return self.team(tid)

    def get_team(self, team_id: str) -> Team | None:
        r = self._row("SELECT * FROM teams WHERE id = ?", (team_id,))
        return Team.from_row(r) if r else None

    def team(self, team_id: str) -> Team:
        t = self.get_team(team_id)
        if t is None:
            raise NotFound("team not found")
        return t

    def resolve_team(self, ref: str) -> Team:
        r = self._row("SELECT * FROM teams WHERE id = ? OR slug = ?", (ref, (ref or "").lower()))
        if r is None:
            raise NotFound(f"no team {ref!r}")
        return Team.from_row(r)

    def list_teams(self, principal: Principal | None = None) -> list[Team]:
        if principal is None:
            return [Team.from_row(r) for r in self._rows("SELECT * FROM teams ORDER BY created_at")]
        rows = self._rows("SELECT t.*, m.role AS my_role FROM teams t JOIN memberships m ON m.team_id = t.id "
                          "WHERE m.user_id = ? ORDER BY t.created_at", (principal.user_id,))
        if principal.kind == "token":
            rows = [r for r in rows if r["id"] == principal.token_team_id]
        return [Team.from_row(r, role=r["my_role"]) for r in rows]

    def team_stats(self, team_id: str) -> dict:
        r = self._row("SELECT (SELECT COUNT(*) FROM memberships WHERE team_id = ?) AS members, "
                      "(SELECT COUNT(*) FROM projects WHERE team_id = ?) AS projects", (team_id, team_id))
        return {"members": r["members"], "projects": r["projects"]}

    def update_team(self, team_id: str, *, name: str | None = None, slug: str | None = None,
                    settings: dict | None = None, actor: Principal | None = None) -> Team:
        if actor is not None:
            self.require(actor, "team.admin", team_id=team_id)
        with self._tx() as c:
            if not c.execute("SELECT 1 FROM teams WHERE id = ?", (team_id,)).fetchone():
                raise NotFound("team not found")
            changes: dict[str, Any] = {}
            if name is not None:
                changes["name"] = _name(name, "team name")
            if slug is not None:
                s = _slug(slug)
                if c.execute("SELECT 1 FROM teams WHERE slug = ? AND id != ?", (s, team_id)).fetchone():
                    raise Conflict(f"team slug {s!r} is taken")
                changes["slug"] = s
            if settings is not None:
                changes["settings"] = _settings(settings)
            if changes:
                c.execute(f"UPDATE teams SET {', '.join(f'{k} = ?' for k in changes)} WHERE id = ?",
                          (*changes.values(), team_id))
                self._audit(c, "team.update", actor, target=f"team:{team_id}", team_id=team_id,
                            detail={k: v for k, v in changes.items() if k != "settings"})
        return self.team(team_id)

    def delete_team(self, team_id: str, *, actor: Principal | None = None) -> None:
        """Delete a team, its memberships, projects, invitations and tokens. Clones the platform made for its
        projects are removed; developers' own repositories are never touched."""
        if actor is not None:
            self.require(actor, "team.delete", team_id=team_id)
        with self._tx() as c:
            t = c.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
            if t is None:
                raise NotFound("team not found")
            managed = [r["root"] for r in c.execute("SELECT root FROM projects WHERE team_id = ? AND managed = 1",
                                                    (team_id,)).fetchall() if r["root"]]
            self._audit(c, "team.delete", actor, target=f"team:{team_id}", team_id=team_id,
                        detail={"slug": t["slug"], "name": t["name"]})
            c.execute("DELETE FROM teams WHERE id = ?", (team_id,))
            if (c.execute("SELECT value FROM meta WHERE key = 'local_team_id'").fetchone() or [None])[0] == team_id:
                self._set_meta(c, "local_team_id", None)
        for root in managed:
            self._remove_clone(Path(root))

    # ---- members and invitations ---------------------------------------------------------------------------
    def member_role(self, team_id: str, user_id: str) -> str | None:
        return self.effective_role(user_id, team_id=team_id)

    def list_members(self, team_id: str, *, actor: Principal | None = None) -> list[Member]:
        if actor is not None:
            self.require(actor, "team.read", team_id=team_id)
        rows = self._rows("SELECT m.team_id, m.user_id, m.role, m.created_at AS joined_at, u.email, u.name, "
                          "u.disabled, u.last_login_at FROM memberships m JOIN users u ON u.id = m.user_id "
                          "WHERE m.team_id = ? ORDER BY m.created_at", (team_id,))
        return [Member.from_row(r) for r in rows]

    def _check_manage(self, c: sqlite3.Connection, actor: Principal | None, team_id: str, target_role: str | None,
                      new_role: str | None) -> None:
        """Admins manage people at or below their own role; only owners touch owners."""
        if actor is None:
            return
        self.require(actor, "team.members", team_id=team_id)
        mine = self.effective_role(actor.user_id, team_id=team_id)
        for role in (target_role, new_role):
            if role and RANK[role] > RANK[mine or "none"]:
                raise PermissionDenied("you cannot manage a role above your own")

    def _other_active_owners(self, c: sqlite3.Connection, team_id: str, user_id: str) -> int:
        return c.execute("SELECT COUNT(*) FROM memberships m JOIN users u ON u.id = m.user_id WHERE m.team_id = ? "
                         "AND m.role = 'owner' AND m.user_id != ? AND u.disabled = 0", (team_id, user_id)).fetchone()[0]

    def add_member(self, team_id: str, user_id: str, role: str = "member", *,
                   actor: Principal | None = None) -> Member:
        if role not in ROLES:
            raise InvalidInput(f"role must be one of {', '.join(ROLES)}")
        with self._tx() as c:
            self._check_manage(c, actor, team_id, None, role)
            self.team(team_id)
            self.user(user_id)
            if c.execute("SELECT 1 FROM memberships WHERE team_id = ? AND user_id = ?", (team_id, user_id)).fetchone():
                raise Conflict("already a member of this team")
            c.execute("INSERT INTO memberships(team_id, user_id, role, created_at) VALUES (?, ?, ?, ?)",
                      (team_id, user_id, role, self.now()))
            self._audit(c, "member.add", actor, target=f"user:{user_id}", team_id=team_id, detail={"role": role})
        return next(m for m in self.list_members(team_id) if m.user_id == user_id)

    def change_role(self, team_id: str, user_id: str, role: str, *, actor: Principal | None = None) -> None:
        if role not in ROLES:
            raise InvalidInput(f"role must be one of {', '.join(ROLES)}")
        with self._tx() as c:
            if actor is not None:
                self.require(actor, "team.members", team_id=team_id)
            current = self.effective_role(user_id, team_id=team_id)
            if current is None:
                raise NotFound("not a member of this team")
            self._check_manage(c, actor, team_id, current, role)
            if current == "owner" and role != "owner" and self._other_active_owners(c, team_id, user_id) == 0:
                raise Conflict("a team needs at least one owner; promote someone else first")
            c.execute("UPDATE memberships SET role = ? WHERE team_id = ? AND user_id = ?", (role, team_id, user_id))
            self._audit(c, "member.role", actor, target=f"user:{user_id}", team_id=team_id,
                        detail={"from": current, "to": role})

    def remove_member(self, team_id: str, user_id: str, *, actor: Principal | None = None) -> None:
        """Remove someone (or leave, when ``actor`` is that user). Their per-project roles in the team and their
        tokens for it are revoked with them."""
        with self._tx() as c:
            leaving = actor is not None and actor.user_id == user_id and actor.kind != "token"
            if actor is not None and not leaving:
                self.require(actor, "team.members", team_id=team_id)
            current = self.effective_role(user_id, team_id=team_id)
            if current is None:
                raise NotFound("not a member of this team")
            if not leaving:
                self._check_manage(c, actor, team_id, current, None)
            if current == "owner" and self._other_active_owners(c, team_id, user_id) == 0:
                raise Conflict("a team needs at least one owner; promote someone else first")
            c.execute("DELETE FROM memberships WHERE team_id = ? AND user_id = ?", (team_id, user_id))
            c.execute("DELETE FROM project_roles WHERE user_id = ? AND project_id IN "
                      "(SELECT id FROM projects WHERE team_id = ?)", (user_id, team_id))
            c.execute("UPDATE api_tokens SET revoked_at = ? WHERE user_id = ? AND team_id = ? AND revoked_at IS NULL",
                      (self.now(), user_id, team_id))
            self._audit(c, "member.leave" if leaving else "member.remove", actor, target=f"user:{user_id}",
                        team_id=team_id, detail={"role": current})

    def invite(self, team_id: str, email: str, role: str = "member", *, actor: Principal | None = None,
               days: float | None = None) -> tuple[Invitation, str]:
        """Create an invitation. Returns it with the one-time token for the invite link (shown once)."""
        if role not in ROLES:
            raise InvalidInput(f"role must be one of {', '.join(ROLES)}")
        email = normalize_email(email)
        days = self.config.invite_days if days is None else float(days)
        if not 0 < days <= 90:
            raise InvalidInput("invitations last between a moment and 90 days")
        self.team(team_id)
        token = security.new_opaque_token()
        iid, now = security.new_id("inv"), self.now()
        with self._tx() as c:
            self._check_manage(c, actor, team_id, None, role)
            if c.execute("SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id WHERE m.team_id = ? "
                         "AND u.email = ?", (team_id, email)).fetchone():
                raise Conflict(f"{email} is already a member of this team")
            c.execute("UPDATE invitations SET revoked_at = ? WHERE team_id = ? AND email = ? AND accepted_at IS NULL "
                      "AND revoked_at IS NULL", (now, team_id, email))
            c.execute("INSERT INTO invitations(id, team_id, email, role, token_hash, invited_by, created_at, expires_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (iid, team_id, email, role, security.digest(token), actor.user_id if actor else None, now,
                       now + days * DAY))
            self._audit(c, "member.invite", actor, target=f"invitation:{iid}", team_id=team_id,
                        detail={"email": email, "role": role})
        return self.invitation(iid), token

    def invitation(self, invitation_id: str) -> Invitation:
        r = self._row("SELECT * FROM invitations WHERE id = ?", (invitation_id,))
        if r is None:
            raise NotFound("invitation not found")
        return Invitation.from_row(r)

    def list_invitations(self, team_id: str, *, include_closed: bool = False,
                         actor: Principal | None = None) -> list[Invitation]:
        if actor is not None:
            self.require(actor, "team.members", team_id=team_id)
        sql = "SELECT * FROM invitations WHERE team_id = ?"
        if not include_closed:
            sql += " AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > ?"
        rows = self._rows(sql + " ORDER BY created_at DESC", (team_id,) if include_closed else (team_id, self.now()))
        return [Invitation.from_row(r) for r in rows]

    def revoke_invitation(self, invitation_id: str, *, actor: Principal | None = None) -> None:
        inv = self.invitation(invitation_id)
        with self._tx() as c:
            self._check_manage(c, actor, inv.team_id, None, inv.role)
            c.execute("UPDATE invitations SET revoked_at = ? WHERE id = ? AND accepted_at IS NULL AND revoked_at IS NULL",
                      (self.now(), invitation_id))
            self._audit(c, "invitation.revoke", actor, target=f"invitation:{invitation_id}", team_id=inv.team_id)

    def invitation_by_token(self, token: str) -> Invitation:
        """Look up a pending invitation by its link token (raises if unknown, used, revoked or expired)."""
        r = self._row("SELECT * FROM invitations WHERE token_hash = ?", (security.digest(token or ""),))
        if r is None:
            raise NotFound("invitation not found")
        inv = Invitation.from_row(r)
        status = inv.status(self.now())
        if status == "revoked":
            raise NotFound("invitation not found")
        if status == "accepted":
            raise Conflict("this invitation has already been used")
        if status == "expired":
            raise Expired("this invitation has expired; ask for a new one")
        return inv

    def accept_invitation(self, token: str, *, user_id: str | None = None, name: str | None = None,
                          password: str | None = None) -> tuple[User, str, bool]:
        """Join a team from an invite link. Signed in (``user_id``): the account's email must match. Otherwise an
        existing account proves itself with ``password``, or a new one is created with ``name`` + ``password``.
        Returns ``(user, role, created_account)``."""
        inv = self.invitation_by_token(token)
        created = False
        if user_id is not None:
            user = self.user(user_id)
            if user.email != inv.email:
                raise PermissionDenied("this invitation was sent to a different email address")
        else:
            existing = self.user_by_email(inv.email)
            if existing is not None:
                if self.authenticate(inv.email, password or "", disabled_error=True) is None:
                    raise AuthError("sign in with the password for this account to accept")
                user = existing
            else:
                pw_hash = security.hash_password(check_password(password, email=inv.email))
                user = None
        if user is not None and user.disabled:
            raise PermissionDenied("this account is disabled")
        with self._tx() as c:
            r = c.execute("SELECT accepted_at, revoked_at, expires_at FROM invitations WHERE id = ?", (inv.id,)).fetchone()
            if r is None or r["revoked_at"] or r["accepted_at"] or r["expires_at"] <= self.now():
                raise Conflict("this invitation is no longer valid")
            if user is None:
                uid = security.new_id("u")
                if c.execute("SELECT 1 FROM users WHERE email = ?", (inv.email,)).fetchone():
                    raise Conflict("an account with this email already exists; sign in to accept")
                c.execute("INSERT INTO users(id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                          (uid, inv.email, _name(name, "name", required=False) or inv.email.split("@")[0], pw_hash,
                           self.now()))
                created = True
            else:
                uid = user.id
            current = c.execute("SELECT role FROM memberships WHERE team_id = ? AND user_id = ?",
                                (inv.team_id, uid)).fetchone()
            role = inv.role
            if current is None:
                c.execute("INSERT INTO memberships(team_id, user_id, role, created_at) VALUES (?, ?, ?, ?)",
                          (inv.team_id, uid, role, self.now()))
            else:
                role = current["role"]
            c.execute("UPDATE invitations SET accepted_at = ?, accepted_by = ? WHERE id = ?", (self.now(), uid, inv.id))
            actor = self._principal(c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone(), "session")
            self._audit(c, "member.join", actor, target=f"invitation:{inv.id}", team_id=inv.team_id,
                        detail={"role": role, "new_account": created})
        return self.user(uid), role, created

    # ---- projects ------------------------------------------------------------------------------------------
    def get_project(self, project_id: str) -> ProjectRecord | None:
        r = self._row("SELECT * FROM projects WHERE id = ?", (project_id,))
        return ProjectRecord.from_row(r) if r else None

    def project(self, project_id: str) -> ProjectRecord:
        p = self.get_project(project_id)
        if p is None:
            raise NotFound("project not found")
        return p

    def project_by_root(self, root: Path | str) -> ProjectRecord | None:
        r = self._row("SELECT * FROM projects WHERE root = ?", (str(Path(root).expanduser().resolve()),))
        return ProjectRecord.from_row(r) if r else None

    def resolve_project(self, ref: str, *, team_id: str | None = None) -> ProjectRecord:
        """By id, by slug (within ``team_id`` when given, else unique across teams), or by local path."""
        p = self.get_project(ref)
        if p:
            return p
        if team_id:
            rows = self._rows("SELECT * FROM projects WHERE slug = ? AND team_id = ?", (ref.lower(), team_id))
        else:
            rows = self._rows("SELECT * FROM projects WHERE slug = ?", (ref.lower(),))
        if len(rows) == 1:
            return ProjectRecord.from_row(rows[0])
        if len(rows) > 1:
            raise Conflict(f"{ref!r} matches projects in several teams; name the team")
        p = self.project_by_root(ref) if ref.startswith(("/", "~", ".")) else None
        if p:
            return p
        raise NotFound(f"no project {ref!r}")

    def list_projects(self, principal: Principal | None = None, *, team_id: str | None = None) -> list[ProjectRecord]:
        """Projects the principal can read (all of them for the local operator), with their effective role."""
        if principal is None:
            sql, args = "SELECT * FROM projects", ()
            if team_id:
                sql, args = sql + " WHERE team_id = ?", (team_id,)
            return [ProjectRecord.from_row(r) for r in self._rows(sql + " ORDER BY created_at", args)]
        sql = ("SELECT p.*, m.role AS team_role, pr.role AS override FROM projects p "
               "JOIN memberships m ON m.team_id = p.team_id AND m.user_id = ? "
               "JOIN users u ON u.id = m.user_id AND u.disabled = 0 "
               "LEFT JOIN project_roles pr ON pr.project_id = p.id AND pr.user_id = m.user_id")
        args: tuple = (principal.user_id,)
        if team_id:
            sql, args = sql + " WHERE p.team_id = ?", (*args, team_id)
        out = []
        for r in self._rows(sql + " ORDER BY p.created_at", args):
            role = r["team_role"] if r["team_role"] == "owner" or not r["override"] else r["override"]
            if role == "none":
                continue
            if principal.kind == "token" and (
                    r["team_id"] != principal.token_team_id or not principal.scope_allows("project.read")
                    or (principal.token_project_id and r["id"] != principal.token_project_id)):
                continue
            out.append(ProjectRecord.from_row(r, role=role))
        return out

    def _insert_project(self, c: sqlite3.Connection, *, team_id: str, name: str, slug: str | None, root: str | None,
                        git_url: str | None, branch: str | None, data_dir: str | None, managed: bool, status: str,
                        actor: Principal | None, pid: str | None = None) -> str:
        if not c.execute("SELECT 1 FROM teams WHERE id = ?", (team_id,)).fetchone():
            raise NotFound("team not found")
        name = _name(name, "project name")
        if slug is None:
            slug = self._unique_slug(c, "SELECT 1 FROM projects WHERE slug = ? AND team_id = ?", (team_id,),
                                     slugify(name))
        else:
            slug = _slug(slug)
            if c.execute("SELECT 1 FROM projects WHERE slug = ? AND team_id = ?", (slug, team_id)).fetchone():
                raise Conflict(f"project slug {slug!r} is taken in this team")
        pid, now = pid or security.new_id("p"), self.now()
        c.execute("INSERT INTO projects(id, team_id, slug, name, root, git_url, git_branch, data_dir, managed, status, "
                  "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (pid, team_id, slug, name, root, git_url, branch, data_dir or str(Path(root or "") / ".cairn"),
                   int(managed), status, now, now))
        self._audit(c, "project.register", actor, target=f"project:{pid}", team_id=team_id, project_id=pid,
                    detail={"name": name, "slug": slug, "kind": "git" if git_url else "local",
                            **({"git_url": git_url, "branch": branch} if git_url else {"root": root})})
        return pid

    def register_local_project(self, root: Path | str, *, team_id: str | None = None, name: str | None = None,
                               slug: str | None = None, actor: Principal | None = None) -> ProjectRecord:
        """Register a repository already on this machine. Idempotent by resolved path. Without ``team_id`` the
        project joins the local owner's Personal team."""
        path = Path(root).expanduser().resolve()
        if actor is not None and actor.kind != "local":
            self._require_server_admin(actor)  # server paths are the operator's to hand out (checked first: no probing)
        existing = self.project_by_root(path)
        if existing is not None:
            if actor is not None:
                self.require(actor, "project.read", project_id=existing.id)
            if team_id and existing.team_id != team_id:
                raise Conflict("this folder is already registered in another team")
            return existing
        if team_id is None:
            self.ensure_local_owner()
            team_id = self.local_team_id
        if actor is not None:
            self.require(actor, "project.admin", team_id=team_id)
        if not path.is_dir():
            raise InvalidInput(f"{path} is not a directory")
        try:
            with self._tx() as c:
                pid = self._insert_project(c, team_id=team_id, name=name or path.name, slug=slug, root=str(path),
                                           git_url=None, branch=None, data_dir=str(path / ".cairn"), managed=False,
                                           status="ready", actor=actor)
        except sqlite3.IntegrityError:
            existing = self.project_by_root(path)
            if existing is None:
                raise
            return existing
        return self.project(pid)

    def register_git_project(self, team_id: str, url: str, *, branch: str | None = None, name: str | None = None,
                             slug: str | None = None, clone: bool = True, allow_local: bool | None = None,
                             actor: Principal | None = None) -> ProjectRecord:
        """Register a remote repository; the platform clones it under ``repos_dir``. With ``clone=False`` the row
        is created in ``pending`` state and :meth:`clone_project` does the work (e.g. in a background task)."""
        if allow_local is None:
            allow_local = self.config.allow_local_git or actor is None
        url = gitops.validate_url(url, allow_local=allow_local, allow_insecure=self.config.allow_insecure_git)
        branch = gitops.validate_branch(branch)
        if actor is not None:
            self.require(actor, "project.admin", team_id=team_id)
        pid = security.new_id("p")
        root = self.repos_dir / pid
        with self._tx() as c:
            if c.execute("SELECT 1 FROM projects WHERE team_id = ? AND git_url = ? AND git_branch IS ?",
                         (team_id, url, branch)).fetchone():
                raise Conflict("this repository is already a project in this team")
            self._insert_project(c, team_id=team_id, name=name or gitops.repo_name(url), slug=slug, root=str(root),
                                 git_url=url, branch=branch, data_dir=str(root / ".cairn"), managed=True,
                                 status="pending", actor=actor, pid=pid)
        if clone:
            self.clone_project(pid)
        return self.project(pid)

    def _git_lock(self, project_id: str) -> threading.Lock:
        with self._lock:
            return self._git_locks.setdefault(project_id, threading.Lock())

    def _set_status(self, project_id: str, status: str, detail: str = "", **cols: Any) -> None:
        sets = ["status = ?", "status_detail = ?", "updated_at = ?"] + [f"{k} = ?" for k in cols]
        with self._tx() as c:
            c.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?",
                      (status, detail[:500], self.now(), *cols.values(), project_id))

    def _allow_local_for(self, p: ProjectRecord) -> bool:
        return bool(p.git_url and (p.git_url.startswith("file://") or p.git_url.startswith("/")))

    def clone_project(self, project_id: str) -> ProjectRecord:
        p = self.project(project_id)
        if not p.managed or not p.git_url or not p.root:
            raise InvalidInput("only projects registered from a git URL are cloned")
        with self._git_lock(project_id):
            self._set_status(project_id, "cloning")
            try:
                root = Path(p.root)
                if root.exists() and not (root / ".git").exists():
                    self._remove_clone(root)
                if not (root / ".git").exists():
                    gitops.clone(p.git_url, root, branch=p.git_branch, allow_local=self._allow_local_for(p))
                self._set_status(project_id, "ready")
            except GitError as exc:
                self._set_status(project_id, "error", str(exc))
                raise
        return self.project(project_id)

    def refresh_project(self, project_id: str, *, actor: Principal | None = None) -> dict:
        """Pull the latest commits into a managed clone (cloning first if needed). Local projects are the
        developer's working tree and are never touched. The caller re-syncs Cairn afterwards."""
        if actor is not None:
            self.require(actor, "project.sync", project_id=project_id)
        p = self.project(project_id)
        if not p.managed or not p.git_url:
            return {"project_id": project_id, "pulled": False, "changed": None}
        if not (Path(p.root or "") / ".git").exists():
            p = self.clone_project(project_id)
            return {"project_id": project_id, "pulled": True, "changed": True, "cloned": True,
                    "branch": p.git_branch or gitops.current_branch(Path(p.root or ""))}
        with self._git_lock(project_id):
            try:
                res = gitops.refresh(Path(p.root), p.git_branch, allow_local=self._allow_local_for(p))
            except GitError as exc:
                self._set_status(project_id, "error", str(exc))
                raise
            self._set_status(project_id, "ready")
        return {"project_id": project_id, "pulled": True, **res}

    def mark_synced(self, project_id: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE projects SET last_synced_at = ? WHERE id = ?", (self.now(), project_id))

    def update_project(self, project_id: str, *, name: str | None = None, slug: str | None = None,
                       settings: dict | None = None, branch: str | None = None,
                       actor: Principal | None = None) -> ProjectRecord:
        if actor is not None:
            self.require(actor, "project.admin", project_id=project_id)
        p = self.project(project_id)
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = _name(name, "project name")
        if settings is not None:
            changes["settings"] = _settings(settings)
        if branch is not None:
            if not p.git_url:
                raise InvalidInput("only git projects follow a branch")
            changes["git_branch"] = gitops.validate_branch(branch)
        with self._tx() as c:
            if slug is not None:
                s = _slug(slug)
                if c.execute("SELECT 1 FROM projects WHERE slug = ? AND team_id = ? AND id != ?",
                             (s, p.team_id, project_id)).fetchone():
                    raise Conflict(f"project slug {s!r} is taken in this team")
                changes["slug"] = s
            if changes:
                c.execute(f"UPDATE projects SET {', '.join(f'{k} = ?' for k in changes)}, updated_at = ? WHERE id = ?",
                          (*changes.values(), self.now(), project_id))
                self._audit(c, "project.update", actor, target=f"project:{project_id}", team_id=p.team_id,
                            project_id=project_id, detail={k: v for k, v in changes.items() if k != "settings"})
        return self.project(project_id)

    def _remove_clone(self, root: Path) -> bool:
        """Delete a platform-made clone. Anything outside ``repos_dir`` is refused (left in place)."""
        base = self.repos_dir.resolve()
        target = root.resolve()
        if target == base or base not in target.parents:
            return False
        shutil.rmtree(target, ignore_errors=True)
        return True

    def delete_project(self, project_id: str, *, purge: bool = True, actor: Principal | None = None) -> None:
        """Unregister a project. ``purge`` also deletes a platform-made clone (never a local repository)."""
        if actor is not None:
            self.require(actor, "project.admin", project_id=project_id)
        p = self.project(project_id)
        with self._tx() as c:
            self._audit(c, "project.delete", actor, target=f"project:{project_id}", team_id=p.team_id,
                        project_id=project_id, detail={"slug": p.slug, "kind": p.kind})
            c.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        for listener in list(self._deleted_listeners):  # e.g. the server stops the project's threads first
            try:
                listener(project_id)
            except Exception:
                log.warning("project delete listener failed", exc_info=True)
        if purge and p.managed and p.root:
            with self._git_lock(project_id):
                self._remove_clone(Path(p.root))

    def on_project_deleted(self, listener) -> None:
        """Call ``listener(project_id)`` when a project is unregistered (before a managed clone is removed)."""
        self._deleted_listeners.append(listener)

    def set_project_role(self, project_id: str, user_id: str, role: str | None, *,
                         actor: Principal | None = None) -> None:
        """Override a member's role for one project (``none`` hides it); ``role=None`` clears the override."""
        if role is not None and role not in OVERRIDE_ROLES:
            raise InvalidInput(f"project roles are {', '.join(OVERRIDE_ROLES)}")
        if actor is not None:
            self.require(actor, "project.admin", project_id=project_id)
        p = self.project(project_id)
        with self._tx() as c:
            team_role = self.effective_role(user_id, team_id=p.team_id)
            if team_role is None:
                raise InvalidInput("project roles apply to members of the project's team")
            if team_role == "owner":
                raise Conflict("team owners always have full access to every project")
            if actor is not None:
                # New role: at most the actor's role on this project. Target: not above the actor's *team*
                # role, so someone made project admin by an override cannot lock out a team admin.
                mine = self.effective_role(actor.user_id, project_id=project_id) or "none"
                mine_team = self.effective_role(actor.user_id, team_id=p.team_id) or "none"
                if RANK[team_role] > RANK[mine_team] or RANK[role or "none"] > RANK[mine]:
                    raise PermissionDenied("you cannot manage a role above your own")
            if role is None:
                c.execute("DELETE FROM project_roles WHERE project_id = ? AND user_id = ?", (project_id, user_id))
            else:
                c.execute("INSERT INTO project_roles(project_id, user_id, role, created_at) VALUES (?, ?, ?, ?) "
                          "ON CONFLICT(project_id, user_id) DO UPDATE SET role = excluded.role",
                          (project_id, user_id, role, self.now()))
            self._audit(c, "project.role", actor, target=f"user:{user_id}", team_id=p.team_id, project_id=project_id,
                        detail={"role": role})

    def project_members(self, project_id: str, *, actor: Principal | None = None) -> list[dict]:
        if actor is not None:
            self.require(actor, "project.read", project_id=project_id)
        p = self.project(project_id)
        rows = self._rows("SELECT m.user_id, m.role AS team_role, pr.role AS override, u.email, u.name, u.disabled "
                          "FROM memberships m JOIN users u ON u.id = m.user_id LEFT JOIN project_roles pr "
                          "ON pr.project_id = ? AND pr.user_id = m.user_id WHERE m.team_id = ? ORDER BY m.created_at",
                          (project_id, p.team_id))
        out = []
        for r in rows:
            eff = r["team_role"] if r["team_role"] == "owner" or not r["override"] else r["override"]
            out.append({"user_id": r["user_id"], "email": r["email"], "name": r["name"], "disabled": bool(r["disabled"]),
                        "team_role": r["team_role"], "override": r["override"],
                        "role": None if eff == "none" else eff})
        return out

    # ---- webhooks ------------------------------------------------------------------------------------------
    def rotate_webhook_secret(self, project_id: str, *, actor: Principal | None = None) -> str:
        """New webhook secret for the project (shown once). Only its digest and a nonce are stored; the secret
        itself is re-derived from the server key when a delivery arrives."""
        if actor is not None:
            self.require(actor, "project.admin", project_id=project_id)
        p = self.project(project_id)
        nonce = security.new_opaque_token()
        secret = security.webhook_secret(self._key, project_id, nonce)
        with self._tx() as c:
            c.execute("UPDATE projects SET webhook_nonce = ?, webhook_secret_hash = ?, updated_at = ? WHERE id = ?",
                      (nonce, security.digest(secret), self.now(), project_id))
            self._audit(c, "project.webhook_rotate", actor, target=f"project:{project_id}", team_id=p.team_id,
                        project_id=project_id)
        return secret

    def verify_webhook(self, project_id: str, headers: Mapping[str, str], body: bytes) -> WebhookResult:
        """Authenticate a push delivery (GitHub/Gitea/Bitbucket ``X-Hub-Signature-256`` HMAC, or GitLab's
        ``X-Gitlab-Token``) and decide whether the project needs a refresh + re-sync."""
        h = {k.lower(): v for k, v in headers.items()}
        r = self._row("SELECT id, team_id, git_branch, webhook_nonce, webhook_secret_hash FROM projects WHERE id = ?",
                      (project_id,))
        if r is None or not r["webhook_secret_hash"]:
            raise AuthError("webhook is not configured for this project")
        if "x-gitlab-token" in h:
            provider = "gitlab"
            ok = security.same(security.digest(h["x-gitlab-token"]), r["webhook_secret_hash"])
        else:
            sig = h.get("x-hub-signature-256") or h.get("x-gitea-signature") or h.get("x-gogs-signature")
            if not sig:
                raise AuthError("delivery is not signed")
            provider = "gitea" if ("x-gitea-event" in h or "x-gogs-event" in h) else "github"
            secret = security.webhook_secret(self._key, r["id"], r["webhook_nonce"] or "")
            ok = (security.same(security.digest(secret), r["webhook_secret_hash"])
                  and security.verify_signature(secret, body, sig))
        if not ok:
            raise AuthError("invalid webhook signature")
        event = (h.get("x-github-event") or h.get("x-gitea-event") or h.get("x-gogs-event") or h.get("x-gitlab-event")
                 or h.get("x-event-key") or "").strip()
        delivery = h.get("x-github-delivery") or h.get("x-gitea-delivery") or h.get("x-gitlab-event-uuid")
        payload = _payload(body, h.get("content-type", ""))
        ref = payload.get("ref") if isinstance(payload.get("ref"), str) else None
        sync, reason = _should_sync(event, ref, payload, r["git_branch"])
        result = WebhookResult(project_id=r["id"], provider=provider, event=event or "unknown", ref=ref, sync=sync,
                               reason=reason, delivery=delivery)
        self.audit("project.webhook", target=f"project:{r['id']}", team_id=r["team_id"], project_id=r["id"],
                   detail={"provider": provider, "event": result.event, "ref": ref, "sync": sync, "reason": reason})
        return result

    # ---- API tokens ----------------------------------------------------------------------------------------
    def issue_token(self, user_id: str, team_id: str, name: str, *, project_id: str | None = None,
                    scopes: list[str] | tuple[str, ...] | None = None, expires_days: float | None = None,
                    actor: Principal | None = None) -> tuple[ApiToken, str]:
        """Issue a token acting as ``user_id`` within one team (optionally one project). Returns the record and
        the full ``cairn_<prefix>_<secret>`` value, which is never retrievable again. A token can never do more
        than its user's role allows; scopes only narrow it."""
        if actor is not None:
            if actor.kind == "token":
                raise PermissionDenied("API tokens cannot issue tokens")
            if actor.user_id != user_id:
                raise PermissionDenied("you can only issue tokens for yourself")
        name = _name(name, "token name")
        try:
            scopes_t = normalize_scopes(scopes)
        except ValueError as exc:
            raise InvalidInput(str(exc)) from exc
        days = self.config.token_days if expires_days is None else float(expires_days)
        if days < 0 or days > 3650:
            raise InvalidInput("token lifetime must be between 0 (never expires) and 3650 days")
        user = self.user(user_id)
        if user.disabled:
            raise InvalidInput("this account is disabled")
        if self.effective_role(user_id, team_id=team_id) is None:
            raise NotFound("team not found")
        if project_id is not None:
            p = self.get_project(project_id)
            if p is None or p.team_id != team_id or self.effective_role(user_id, project_id=project_id) is None:
                raise NotFound("project not found")
        now = self.now()
        with self._tx() as c:
            for _ in range(10):
                prefix, secret, full = security.new_api_token()
                if not c.execute("SELECT 1 FROM api_tokens WHERE prefix = ?", (prefix,)).fetchone():
                    break
            else:  # pragma: no cover - 36^8 prefixes
                raise Conflict("could not allocate a token prefix")
            tid = security.new_id("tok")
            c.execute("INSERT INTO api_tokens(id, prefix, secret_hash, name, user_id, team_id, project_id, scopes, "
                      "created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (tid, prefix, security.digest(secret), name, user_id, team_id, project_id, json.dumps(scopes_t),
                       actor.user_id if actor else None, now, now + days * DAY if days else None))
            self._audit(c, "token.issue", actor, target=f"token:{tid}", team_id=team_id, project_id=project_id,
                        detail={"name": name, "prefix": prefix, "scopes": list(scopes_t), "user_id": user_id,
                                "expires_days": days or None})
        return self.token(tid), full

    def token(self, token_id: str) -> ApiToken:
        r = self._row("SELECT t.*, u.email AS user_email FROM api_tokens t JOIN users u ON u.id = t.user_id "
                      "WHERE t.id = ?", (token_id,))
        if r is None:
            raise NotFound("token not found")
        return ApiToken.from_row(r)

    def resolve_token(self, ref: str) -> ApiToken:
        """By id, prefix, or the full token value."""
        parsed = security.parse_api_token(ref)
        key = parsed[0] if parsed else ref.strip()
        r = self._row("SELECT id FROM api_tokens WHERE id = ? OR prefix = ?", (key, key))
        if r is None:
            raise NotFound("token not found")
        return self.token(r["id"])

    def list_tokens(self, *, user_id: str | None = None, team_id: str | None = None, include_revoked: bool = False,
                    actor: Principal | None = None) -> list[ApiToken]:
        if actor is not None and (user_id is None or user_id != actor.user_id):
            if not team_id:
                raise InvalidInput("name a team to list other members' tokens")
            self.require(actor, "team.tokens", team_id=team_id)
        where, args = [], []
        if user_id:
            where.append("t.user_id = ?")
            args.append(user_id)
        if team_id:
            where.append("t.team_id = ?")
            args.append(team_id)
        if not include_revoked:
            where.append("t.revoked_at IS NULL")
        sql = ("SELECT t.*, u.email AS user_email FROM api_tokens t JOIN users u ON u.id = t.user_id"
               + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY t.created_at DESC")
        return [ApiToken.from_row(r) for r in self._rows(sql, tuple(args))]

    def revoke_token(self, ref: str, *, actor: Principal | None = None) -> ApiToken:
        tok = self.resolve_token(ref)
        if actor is not None and actor.user_id != tok.user_id:
            try:
                self.require(actor, "team.tokens", team_id=tok.team_id)
            except (PermissionDenied, NotFound):
                raise NotFound("token not found") from None
        with self._tx() as c:
            c.execute("UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (self.now(), tok.id))
            self._audit(c, "token.revoke", actor, target=f"token:{tok.id}", team_id=tok.team_id,
                        project_id=tok.project_id, detail={"prefix": tok.prefix, "user_id": tok.user_id})
        return self.token(tok.id)

    def verify_token(self, value: str | None, *, ip: str = "") -> Principal | None:
        """Principal for a live ``cairn_…`` token, or None. Records when it was last used."""
        parsed = security.parse_api_token(value)
        if parsed is None:
            return None
        prefix, secret = parsed
        r = self._row("SELECT t.id AS token_id, t.prefix, t.secret_hash, t.team_id, t.project_id, t.scopes, "
                      "t.expires_at, t.revoked_at, t.last_used_at, u.* FROM api_tokens t JOIN users u ON u.id = t.user_id "
                      "WHERE t.prefix = ?", (prefix,))
        if r is None or not security.same(security.digest(secret), r["secret_hash"]):
            return None
        now = self.now()
        if r["revoked_at"] or r["disabled"] or (r["expires_at"] is not None and now >= r["expires_at"]):
            return None
        if r["last_used_at"] is None or now - r["last_used_at"] >= TOUCH_EVERY:
            with self._tx() as c:
                c.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (now, r["token_id"]))
        return self._principal(r, "token", token_id=r["token_id"], token_prefix=r["prefix"],
                               token_team_id=r["team_id"], token_project_id=r["project_id"],
                               scopes=frozenset(json.loads(r["scopes"] or "[]")), ip=ip)

    def principal_from_bearer(self, header: str | None, *, ip: str = "") -> Principal | None:
        """``Authorization: Bearer cairn_…`` → Principal (for MCP over HTTP and API clients)."""
        return self.verify_token(security.parse_bearer(header), ip=ip)

    # ---- web sessions --------------------------------------------------------------------------------------
    def login(self, email: str, password: str, *, user_agent: str = "", ip: str = "",
              ttl_days: float | None = None) -> tuple[str, Principal]:
        """Password login. Returns ``(cookie_token, principal)``; the token is stored only as a digest."""
        target = f"email:{(email or '')[:254].strip().lower()}"
        try:
            user = self.authenticate(email, password, disabled_error=True)
        except AccountDisabled:
            self.audit("auth.login_failed", target=target, ip=ip, detail={"reason": "account disabled"})
            raise
        if user is None:
            self.audit("auth.login_failed", target=target, ip=ip)
            raise AuthError("invalid email or password")
        token, principal = self.create_session(user.id, user_agent=user_agent, ip=ip, ttl_days=ttl_days)
        with self._tx() as c:
            c.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (self.now(), user.id))
            self._audit(c, "auth.login", principal, target=f"user:{user.id}")
        return token, principal

    def create_session(self, user_id: str, *, user_agent: str = "", ip: str = "",
                       ttl_days: float | None = None) -> tuple[str, Principal]:
        token = security.new_opaque_token()
        sid, now = security.new_id("ses"), self.now()
        ttl = (self.config.session_days if ttl_days is None else ttl_days) * DAY
        with self._tx() as c:
            c.execute("DELETE FROM web_sessions WHERE expires_at <= ?", (now,))
            c.execute("INSERT INTO web_sessions(id, token_hash, user_id, created_at, expires_at, last_seen_at, "
                      "user_agent, ip) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (sid, security.digest(token), user_id, now, now + ttl, now, (user_agent or "")[:300], ip or ""))
            r = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return token, self._principal(r, "session", session_id=sid, ip=ip)

    def session_principal(self, token: str | None, *, ip: str = "") -> Principal | None:
        if not token:
            return None
        r = self._row("SELECT s.id AS sid, s.expires_at, s.last_seen_at, u.* FROM web_sessions s "
                      "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (security.digest(token),))
        if r is None:
            return None
        now = self.now()
        if now >= r["expires_at"] or r["disabled"]:
            with self._tx() as c:
                c.execute("DELETE FROM web_sessions WHERE id = ?", (r["sid"],))
            return None
        if now - r["last_seen_at"] >= TOUCH_EVERY:
            with self._tx() as c:
                c.execute("UPDATE web_sessions SET last_seen_at = ? WHERE id = ?", (now, r["sid"]))
        return self._principal(r, "session", session_id=r["sid"], ip=ip)

    def session_expiry(self, session_id: str) -> float | None:
        r = self._row("SELECT expires_at FROM web_sessions WHERE id = ?", (session_id,))
        return r["expires_at"] if r else None

    def logout(self, token: str | None) -> bool:
        if not token:
            return False
        with self._tx() as c:
            r = c.execute("SELECT s.id AS sid, u.* FROM web_sessions s JOIN users u ON u.id = s.user_id "
                          "WHERE s.token_hash = ?", (security.digest(token),)).fetchone()
            if r is None:
                return False
            c.execute("DELETE FROM web_sessions WHERE id = ?", (r["sid"],))
            self._audit(c, "auth.logout", self._principal(r, "session", session_id=r["sid"]), target=f"user:{r['id']}")
        return True

    def list_sessions(self, user_id: str) -> list[WebSession]:
        rows = self._rows("SELECT * FROM web_sessions WHERE user_id = ? AND expires_at > ? ORDER BY last_seen_at DESC",
                          (user_id, self.now()))
        return [WebSession.from_row(r) for r in rows]

    def revoke_session(self, user_id: str, session_id: str) -> None:
        with self._tx() as c:
            if c.execute("DELETE FROM web_sessions WHERE id = ? AND user_id = ?", (session_id, user_id)).rowcount == 0:
                raise NotFound("session not found")

    def revoke_sessions(self, user_id: str, *, except_session_id: str | None = None) -> int:
        with self._tx() as c:
            return c.execute("DELETE FROM web_sessions WHERE user_id = ? AND id IS NOT ?",
                             (user_id, except_session_id)).rowcount

    def csrf_token(self, principal: Principal | None) -> str | None:
        """Per-session anti-forgery token (HMAC of the session id with the server key); None without a session."""
        if principal is None or principal.kind != "session" or not principal.session_id:
            return None
        return security.csrf_token(self._key, principal.session_id)

    def check_csrf(self, principal: Principal, value: str | None) -> bool:
        expected = self.csrf_token(principal)
        return bool(expected and value) and security.same(expected, value or "")

    def purge_expired(self) -> dict:
        now = self.now()
        with self._tx() as c:
            s = c.execute("DELETE FROM web_sessions WHERE expires_at <= ?", (now,)).rowcount
        return {"sessions": s}


# ---- webhook payload helpers ---------------------------------------------------------------------------------
def _payload(body: bytes, content_type: str) -> dict:
    try:
        if "application/x-www-form-urlencoded" in content_type:
            data = json.loads(parse_qs(body.decode("utf-8", "replace")).get("payload", ["{}"])[0])
        else:
            data = json.loads(body.decode("utf-8", "replace") or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _should_sync(event: str, ref: str | None, payload: dict, branch: str | None) -> tuple[bool, str]:
    e = event.lower()
    if e == "ping":
        return False, "ping"
    if e and e not in ("push", "push hook", "repo:push"):
        return False, f"ignored event {event!r}"
    if ref is None:
        return True, "push"
    if not ref.startswith("refs/heads/"):
        return False, "not a branch push"
    if payload.get("deleted") is True:
        return False, "branch deleted"
    pushed = ref[len("refs/heads/"):]
    repo = payload.get("repository") if isinstance(payload.get("repository"), dict) else {}
    proj = payload.get("project") if isinstance(payload.get("project"), dict) else {}
    follow = branch or repo.get("default_branch") or proj.get("default_branch")
    if follow and pushed != follow:
        return False, f"push to {pushed}, following {follow}"
    return True, f"push to {pushed}"

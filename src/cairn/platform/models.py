"""Records returned by the platform service. ``public()`` gives the JSON the HTTP API returns; no record ever
carries a secret or a secret's hash."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def _json(text: str | None) -> Any:
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {}


@dataclass(frozen=True)
class User:
    id: str
    email: str
    name: str
    is_admin: bool
    is_local: bool
    must_change_password: bool
    disabled: bool
    has_password: bool
    created_at: float
    last_login_at: float | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> User:
        return cls(id=r["id"], email=r["email"], name=r["name"], is_admin=bool(r["is_admin"]),
                   is_local=bool(r["is_local"]), must_change_password=bool(r["must_change_password"]),
                   disabled=bool(r["disabled"]), has_password=bool(r["password_hash"]),
                   created_at=r["created_at"], last_login_at=r["last_login_at"])

    def public(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Team:
    id: str
    slug: str
    name: str
    settings: dict
    created_at: float
    role: str | None = None   # the viewing principal's role, when listed for someone

    @classmethod
    def from_row(cls, r: sqlite3.Row, role: str | None = None) -> Team:
        return cls(id=r["id"], slug=r["slug"], name=r["name"], settings=_json(r["settings"]),
                   created_at=r["created_at"], role=role)

    def public(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Member:
    team_id: str
    user_id: str
    email: str
    name: str
    role: str
    joined_at: float
    disabled: bool
    last_login_at: float | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> Member:
        return cls(team_id=r["team_id"], user_id=r["user_id"], email=r["email"], name=r["name"], role=r["role"],
                   joined_at=r["joined_at"], disabled=bool(r["disabled"]), last_login_at=r["last_login_at"])

    def public(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProjectRecord:
    id: str
    team_id: str
    slug: str
    name: str
    root: str | None
    git_url: str | None
    git_branch: str | None
    data_dir: str
    managed: bool          # the platform cloned it under $CAIRN_HOME and may delete it
    status: str            # ready | pending | cloning | error
    status_detail: str
    settings: dict
    created_at: float
    updated_at: float
    last_synced_at: float | None
    webhook_configured: bool
    role: str | None = None  # the viewing principal's effective role

    @classmethod
    def from_row(cls, r: sqlite3.Row, role: str | None = None) -> ProjectRecord:
        return cls(id=r["id"], team_id=r["team_id"], slug=r["slug"], name=r["name"], root=r["root"],
                   git_url=r["git_url"], git_branch=r["git_branch"], data_dir=r["data_dir"],
                   managed=bool(r["managed"]), status=r["status"], status_detail=r["status_detail"],
                   settings=_json(r["settings"]), created_at=r["created_at"], updated_at=r["updated_at"],
                   last_synced_at=r["last_synced_at"], webhook_configured=bool(r["webhook_secret_hash"]),
                   role=role)

    @property
    def kind(self) -> str:
        return "git" if self.git_url else "local"

    @property
    def root_path(self) -> Path | None:
        return Path(self.root) if self.root else None

    def public(self, include_paths: bool = True) -> dict:
        out = asdict(self)
        out["kind"] = self.kind
        if not include_paths:
            out["root"] = out["data_dir"] = None
        return out


@dataclass(frozen=True)
class Invitation:
    id: str
    team_id: str
    email: str
    role: str
    invited_by: str | None
    created_at: float
    expires_at: float
    accepted_at: float | None
    accepted_by: str | None
    revoked_at: float | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> Invitation:
        return cls(id=r["id"], team_id=r["team_id"], email=r["email"], role=r["role"], invited_by=r["invited_by"],
                   created_at=r["created_at"], expires_at=r["expires_at"], accepted_at=r["accepted_at"],
                   accepted_by=r["accepted_by"], revoked_at=r["revoked_at"])

    def status(self, now: float) -> str:
        if self.revoked_at:
            return "revoked"
        if self.accepted_at:
            return "accepted"
        return "expired" if now >= self.expires_at else "pending"

    def public(self, now: float) -> dict:
        return {**asdict(self), "status": self.status(now)}


@dataclass(frozen=True)
class ApiToken:
    id: str
    prefix: str
    name: str
    user_id: str
    team_id: str
    project_id: str | None
    scopes: tuple[str, ...]
    created_by: str | None
    created_at: float
    last_used_at: float | None
    expires_at: float | None
    revoked_at: float | None
    user_email: str | None = None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> ApiToken:
        keys = r.keys()
        scopes = json.loads(r["scopes"] or "[]")
        return cls(id=r["id"], prefix=r["prefix"], name=r["name"], user_id=r["user_id"], team_id=r["team_id"],
                   project_id=r["project_id"], scopes=tuple(scopes), created_by=r["created_by"],
                   created_at=r["created_at"], last_used_at=r["last_used_at"], expires_at=r["expires_at"],
                   revoked_at=r["revoked_at"], user_email=r["user_email"] if "user_email" in keys else None)

    def status(self, now: float) -> str:
        if self.revoked_at:
            return "revoked"
        return "expired" if self.expires_at is not None and now >= self.expires_at else "active"

    @property
    def display(self) -> str:
        return f"cairn_{self.prefix}_…"

    def public(self, now: float) -> dict:
        return {**asdict(self), "scopes": list(self.scopes), "status": self.status(now), "display": self.display}


@dataclass(frozen=True)
class WebSession:
    id: str
    user_id: str
    created_at: float
    expires_at: float
    last_seen_at: float
    user_agent: str
    ip: str

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> WebSession:
        return cls(id=r["id"], user_id=r["user_id"], created_at=r["created_at"], expires_at=r["expires_at"],
                   last_seen_at=r["last_seen_at"], user_agent=r["user_agent"], ip=r["ip"])

    def public(self, current_id: str | None = None) -> dict:
        return {**asdict(self), "current": self.id == current_id}


@dataclass(frozen=True)
class AuditEntry:
    id: int
    ts: float
    actor_id: str | None
    actor: str
    action: str
    target: str
    team_id: str | None
    project_id: str | None
    ip: str
    detail: dict

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> AuditEntry:
        return cls(id=r["id"], ts=r["ts"], actor_id=r["actor_id"], actor=r["actor"], action=r["action"],
                   target=r["target"], team_id=r["team_id"], project_id=r["project_id"], ip=r["ip"],
                   detail=_json(r["detail"]))

    def public(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WebhookResult:
    project_id: str
    provider: str       # github | gitlab | gitea
    event: str
    ref: str | None
    sync: bool          # True: the caller should refresh and re-sync this project
    reason: str
    delivery: str | None = None

    def public(self) -> dict:
        return asdict(self)

"""Roles, actions, the permission matrix, token scopes and the Principal making a request.

Roles are ordered ``owner > admin > member > viewer``. Each action names the lowest role allowed to do it;
the matrix below is derived from that and is the single source of truth. A project-level override replaces
a member's team role for one project (``none`` removes access); team owners are never overridden. API
tokens are further limited to their scopes and, optionally, to one project.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

ROLES = ("viewer", "member", "admin", "owner")
RANK = {"none": 0, "viewer": 1, "member": 2, "admin": 3, "owner": 4}
OVERRIDE_ROLES = ("none", "viewer", "member", "admin")

ACTIONS: dict[str, str] = {
    "project.read": "viewer",      # map, specs, timeline, memory, sessions, search, context packs
    "project.write": "member",     # memories, spec edits from the UI, task status
    "project.sync": "member",      # trigger a sync / refresh from the remote
    "project.capture": "member",   # agents posting session events
    "project.admin": "admin",      # settings, rename, delete, webhook, per-project roles; at team level: add projects
    "team.read": "viewer",         # see the team and its member list
    "team.members": "admin",       # invite, remove, change roles
    "team.tokens": "admin",        # list and revoke other members' tokens
    "team.audit": "admin",         # read the team's audit log
    "team.admin": "owner",         # rename the team, team settings
    "team.delete": "owner",
}

MATRIX: dict[str, frozenset[str]] = {
    role: frozenset(a for a, least in ACTIONS.items() if RANK[role] >= RANK[least]) for role in ROLES
}

SCOPE_PRESETS: dict[str, tuple[str, ...]] = {
    "read": ("project.read", "team.read"),
    "agent": ("project.read", "project.write", "project.capture", "team.read"),
    "ci": ("project.read", "project.sync"),
    "all": ("*",),
}
DEFAULT_SCOPES = ("agent",)


def role_allows(role: str | None, action: str) -> bool:
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    return bool(role) and action in MATRIX.get(role, frozenset())  # type: ignore[arg-type]


def normalize_scopes(scopes: Iterable[str] | None) -> tuple[str, ...]:
    """Expand presets, validate and de-duplicate. ``*`` means whatever the user's role allows."""
    out: set[str] = set()
    for raw in scopes if scopes is not None else DEFAULT_SCOPES:
        s = raw.strip()
        if s in SCOPE_PRESETS:
            out.update(SCOPE_PRESETS[s])
        elif s == "*" or s in ACTIONS:
            out.add(s)
        else:
            raise ValueError(f"unknown scope {s!r}; use an action ({', '.join(ACTIONS)}) or a preset "
                             f"({', '.join(SCOPE_PRESETS)})")
    if not out:
        raise ValueError("a token needs at least one scope")
    return ("*",) if "*" in out else tuple(sorted(out))


@dataclass(frozen=True)
class Principal:
    """Who is making a request. ``kind`` is ``local`` (solo mode owner), ``session`` (browser login) or ``token``."""
    user_id: str
    email: str
    name: str
    kind: str
    is_admin: bool = False               # server administrator: users, team creation, server paths
    must_change_password: bool = False
    session_id: str | None = None
    token_id: str | None = None
    token_prefix: str | None = None
    token_team_id: str | None = None
    token_project_id: str | None = None
    scopes: frozenset[str] | None = None  # None = not limited by scopes
    ip: str = field(default="", compare=False)

    @property
    def label(self) -> str:
        return f"{self.email} (token {self.token_prefix})" if self.kind == "token" else self.email

    def scope_allows(self, action: str) -> bool:
        return self.scopes is None or "*" in self.scopes or action in self.scopes

    def public(self) -> dict:
        out = {"user_id": self.user_id, "email": self.email, "name": self.name, "kind": self.kind,
               "is_admin": self.is_admin, "must_change_password": self.must_change_password}
        if self.kind == "token":
            out["token"] = {"id": self.token_id, "prefix": self.token_prefix, "team_id": self.token_team_id,
                            "project_id": self.token_project_id, "scopes": sorted(self.scopes or ())}
        return out

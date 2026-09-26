"""Scope identifiers.

Every memory belongs to at least one scope. The engine keys scoping, filtering, session context and
entity links on these payload fields:

  project_id   one repository (Cairn's default scope)
  team_id      shared across a team's projects
  user_id      one developer, across projects
  agent_id     one coding agent / assistant persona
  run_id       one agent session

Cairn's user-facing scope names map onto them with ``SCOPE_FIELD``.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

SCOPE_KEYS: tuple[str, ...] = ("user_id", "agent_id", "run_id", "project_id", "team_id")
ENTITY_PARAMS = frozenset(SCOPE_KEYS)

# Cairn scope name -> payload field
SCOPE_FIELD: Dict[str, str] = {
    "project": "project_id",
    "team": "team_id",
    "user": "user_id",
    "session": "run_id",
    "agent": "agent_id",
}


def scope_ids(filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The scope identifiers present (and non-empty) in a filter or payload dict."""
    if not filters:
        return {}
    return {k: filters[k] for k in SCOPE_KEYS if filters.get(k)}


def scope_of(payload: Optional[Dict[str, Any]]) -> str:
    """The narrowest Cairn scope name a payload belongs to."""
    ids = scope_ids(payload)
    for name in ("session", "agent", "user", "team", "project"):
        if SCOPE_FIELD[name] in ids:
            return name
    return "project"

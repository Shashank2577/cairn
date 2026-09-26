"""Metadata filter evaluation for stores that filter payloads in-process (the local faiss store).

Supports the full filter language accepted by ``Memory.search`` / ``get_all``:

  {"key": "value"}                       equality (a list value means "one of")
  {"key": "*"}                           key present with any value
  {"key": {"eq"|"ne"|"gt"|"gte"|"lt"|"lte"|"in"|"nin"|"contains"|"icontains": value}}
  {"$or": [cond, ...]}, {"$not": [cond, ...]}   (and the unprocessed AND / OR / NOT forms)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

_MISSING = object()


def _cmp(op: str, have: Any, want: Any) -> bool:
    try:
        if op == "gt":
            return have > want
        if op == "gte":
            return have >= want
        if op == "lt":
            return have < want
        if op == "lte":
            return have <= want
    except TypeError:
        try:
            h, w = float(have), float(want)
        except (TypeError, ValueError):
            return False
        return _cmp(op, h, w)
    return False


def _eq(have: Any, want: Any) -> bool:
    if isinstance(have, (list, tuple, set)) and not isinstance(want, (list, tuple, set)):
        return want in have
    return have == want


def _condition(have: Any, cond: Any) -> bool:
    if not isinstance(cond, dict):
        if cond == "*":
            return have is not _MISSING and have is not None
        if have is _MISSING:
            return False
        if isinstance(cond, list):
            return any(_eq(have, c) for c in cond)
        return _eq(have, cond)
    for op, want in cond.items():
        if op in ("ne", "nin"):
            if have is _MISSING:
                continue
            if op == "ne" and _eq(have, want):
                return False
            if op == "nin" and any(_eq(have, w) for w in (want or [])):
                return False
            continue
        if have is _MISSING or have is None:
            return False
        if op == "eq":
            ok = _eq(have, want)
        elif op == "in":
            ok = any(_eq(have, w) for w in (want or []))
        elif op in ("gt", "gte", "lt", "lte"):
            ok = _cmp(op, have, want)
        elif op == "contains":
            ok = (str(want) in have) if isinstance(have, str) else _eq(have, want)
        elif op == "icontains":
            ok = (str(want).lower() in have.lower()) if isinstance(have, str) else any(
                str(want).lower() in str(h).lower() for h in (have if isinstance(have, (list, tuple)) else [have]))
        else:
            raise ValueError(f"Unsupported metadata filter operator: {op}")
        if not ok:
            return False
    return True


def matches(payload: Optional[Dict[str, Any]], filters: Optional[Dict[str, Any]]) -> bool:
    """True when ``payload`` satisfies every clause of ``filters``."""
    if not filters:
        return True
    payload = payload or {}
    for key, cond in filters.items():
        if key in ("$or", "OR"):
            if cond and not any(matches(payload, c) for c in cond):
                return False
        elif key in ("$not", "NOT"):
            if any(matches(payload, c) for c in (cond or [])):
                return False
        elif key in ("$and", "AND"):
            if not all(matches(payload, c) for c in (cond or [])):
                return False
        elif not _condition(payload.get(key, _MISSING), cond):
            return False
    return True

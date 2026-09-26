"""Relationship memory through Cairn's temporal fact graph.

Cairn keeps exactly one graph of facts — the temporal engine (``cairn.engines.temporal``). When a
memory configuration enables ``graph_store``, memory writes are also added to that graph as episodes
(one namespace per memory scope) and searches return the related facts as ``relations``. Nothing here
stores relationships itself.

The temporal engine is used lazily through its ``TemporalService``; when it (or a model for its
extraction) is unavailable, every call returns an empty result instead of failing the memory operation.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional

from cairn.engines.memstore.scopes import SCOPE_KEYS

logger = logging.getLogger(__name__)

EMPTY_ADD: Dict[str, List] = {"deleted_entities": [], "added_entities": []}


def group_id_for(filters: Dict[str, Any]) -> str:
    """Stable graph namespace for a memory scope, e.g. ``mem_project-shop_user-ada_1a2b3c4d``.

    Namespaces may only use ASCII letters, digits, ``-`` and ``_``; the hash keeps distinct scopes
    distinct after sanitising."""
    ids = [(k, str(filters[k])) for k in sorted(SCOPE_KEYS) if filters.get(k)]
    canonical = "&".join(f"{k}={v}" for k, v in ids) or "default"
    readable = "_".join(f"{k.split('_')[0]}-{re.sub(r'[^A-Za-z0-9-]', '-', v)}" for k, v in ids) or "default"
    return f"mem_{readable[:60]}_{hashlib.sha1(canonical.encode()).hexdigest()[:8]}"


def _relation(fact: Dict[str, Any], names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """A graph fact as {source, relationship, destination, fact, valid_at, invalid_at, ...}."""
    names = names or {}
    src, dst = fact.get("source_node_uuid"), fact.get("target_node_uuid")
    out = {
        "source": names.get(src, src or ""),
        "relationship": fact.get("name") or "",
        "destination": names.get(dst, dst or ""),
        "fact": fact.get("fact") or "",
    }
    for key in ("uuid", "valid_at", "invalid_at", "score"):
        if fact.get(key) is not None:
            out[key] = fact[key]
    return out


class GraphMemory:
    def __init__(self, config=None, router_getter: Optional[Callable[[], Any]] = None):
        self.config = config
        self._router_getter = router_getter
        self._service = None
        self._missing: Optional[str] = None

    def _svc(self):
        if self._service is None and self._missing is None:
            try:
                from cairn.engines.temporal import TemporalService

                router = self._router_getter() if self._router_getter else None
                project = getattr(router, "project", None)
                if project is None:
                    raise RuntimeError("no project for the temporal fact graph")
                opts = dict(getattr(self.config, "config", None) or {})
                self._service = TemporalService(project, router, getattr(router, "brain", None), **opts)
            except Exception as exc:
                self._missing = f"{type(exc).__name__}: {exc}"[:200]
                logger.info("temporal fact graph unavailable; relations disabled: %s", self._missing)
        return self._service

    @staticmethod
    def _run(coro):
        from cairn.engines.temporal import run_sync

        return run_sync(coro)

    @property
    def available(self) -> bool:
        return self._svc() is not None

    def _threshold(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        threshold = getattr(self.config, "threshold", None)
        if threshold is None:
            return rows
        return [r for r in rows if r.get("score") is None or r["score"] >= threshold]

    def add(self, data: str, filters: Dict[str, Any], timestamp: Optional[str] = None) -> Dict[str, List]:
        svc = self._svc()
        if svc is None or not data.strip() or not getattr(svc, "available", False):
            return {"deleted_entities": [], "added_entities": []}
        res = self._run(svc.add_episode(
            f"memory-{uuid.uuid4().hex[:8]}", data, source="text", source_description="memory",
            reference_time=timestamp, group_id=group_id_for(filters),
            custom_extraction_instructions=getattr(self.config, "custom_prompt", None)))
        names = {n.get("uuid"): n.get("name") for n in res.get("nodes", [])}
        invalid = {f.get("uuid") for f in res.get("invalidated", [])}
        return {"deleted_entities": [_relation(f, names) for f in res.get("invalidated", [])],
                "added_entities": [_relation(f, names) for f in res.get("facts", []) if f.get("uuid") not in invalid]}

    def search(self, query: str, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
        svc = self._svc()
        if svc is None:
            return []
        rows = self._run(svc.search_facts(query, group_ids=[group_id_for(filters)], max_facts=max(1, limit)))
        return self._threshold([_relation(f) for f in rows or []])

    def get_all(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
        svc = self._svc()
        if svc is None:
            return []
        return [_relation(f) for f in self._run(svc.list_facts(group_id=group_id_for(filters), limit=limit)) or []]

    def delete_all(self, filters: Dict[str, Any]) -> None:
        svc = self._svc()
        if svc is not None:
            self._run(svc.delete_group(group_id_for(filters)))

    def reset(self, scopes: Iterable[Dict[str, Any]] = ()) -> None:
        """Clear the graph namespaces of the given memory scopes (never the rest of the fact graph)."""
        svc = self._svc()
        if svc is None:
            return
        for gid in sorted({group_id_for(s) for s in scopes}):
            self._run(svc.delete_group(gid))

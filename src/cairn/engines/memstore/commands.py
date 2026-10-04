"""Command handlers for ``cairn memory <verb>`` — the memory engine from the terminal.

Each handler takes a ``Memory`` and plain arguments and returns JSON-ready data; the CLI layer only
parses flags and prints. Scope flags map to ``project_id`` / ``team_id`` / ``user_id`` / ``agent_id`` /
``run_id``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from cairn.engines.memstore.api import delete_entity as _delete_entity
from cairn.engines.memstore.api import list_entities as _list_entities
from cairn.engines.memstore.scopes import SCOPE_KEYS
from cairn.engines.memstore.utils.lemmatization import lemmatize_for_bm25


class CommandError(Exception):
    pass


def _scope(**ids: Optional[str]) -> Dict[str, str]:
    return {k: v for k, v in ids.items() if k in SCOPE_KEYS and v}


def _json_arg(value: Optional[str], what: str) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise CommandError(f"{what} must be valid JSON: {exc}") from exc


def add(memory, text: Optional[str] = None, *, messages: Optional[str] = None, file: Optional[str] = None,
        metadata: Optional[str] = None, infer: bool = True, expires: Optional[str] = None,
        timestamp: Optional[Any] = None, instructions: Optional[str] = None, **ids: Optional[str]) -> Dict:
    """Add from text, a JSON message list (``--messages``), a JSON file (``--file``) or stdin."""
    scope = _scope(**ids)
    if not scope:
        raise CommandError("give a scope: --project/--team/--user/--agent/--run")
    if messages is not None:
        payload = _json_arg(messages, "--messages")
    elif file is not None:
        try:
            payload = json.loads(Path(file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"cannot read {file}: {exc}") from exc
    elif text is not None:
        payload = text
    elif not sys.stdin.isatty():
        payload = sys.stdin.read()
    else:
        raise CommandError("nothing to add: pass text, --messages, --file or pipe stdin")
    if isinstance(payload, str) and not payload.strip():
        raise CommandError("nothing to add: empty text")
    return memory.add(payload, metadata=_json_arg(metadata, "--metadata"), infer=infer, expiration_date=expires,
                      timestamp=timestamp, prompt=instructions, **scope)


def search(memory, query: str, *, top_k: int = 10, threshold: float = 0.1, rerank: bool = False,
           keyword: bool = False, filters: Optional[str] = None, show_expired: bool = False,
           explain: bool = False, **ids: Optional[str]) -> Dict:
    """Hybrid search (default) or pure keyword (BM25) search within a scope."""
    flt = {**(_json_arg(filters, "--filter") or {}), **_scope(**ids)}
    if keyword:
        rows = memory.vector_store.keyword_search(query=lemmatize_for_bm25(query), top_k=top_k, filters=flt)
        if rows is None:
            raise CommandError("the configured vector store has no keyword search")
        return {"results": [{"id": r.id, "memory": (r.payload or {}).get("data"), "score": r.score}
                            for r in rows]}
    return memory.search(query, filters=flt, top_k=top_k, threshold=threshold, rerank=rerank,
                         show_expired=show_expired, explain=explain)


def list_memories(memory, *, page: int = 1, page_size: int = 100, show_expired: bool = False,
                  filters: Optional[str] = None, **ids: Optional[str]) -> Dict:
    flt = {**(_json_arg(filters, "--filter") or {}), **_scope(**ids)}
    page, page_size = max(1, page), max(1, page_size)
    res = memory.get_all(filters=flt, top_k=page * page_size, show_expired=show_expired)
    rows = res["results"][(page - 1) * page_size: page * page_size]
    return {**res, "results": rows, "page": page, "page_size": page_size}


def get(memory, memory_id: str) -> Dict:
    item = memory.get(memory_id)
    if item is None:
        raise CommandError(f"no memory {memory_id}")
    return item


def update(memory, memory_id: str, text: Optional[str] = None, *, metadata: Optional[str] = None,
           expires: Optional[str] = None, clear_expiry: bool = False) -> Dict:
    params: Dict[str, Any] = {}
    if text is not None:
        params["text"] = text
    if metadata is not None:
        params["metadata"] = _json_arg(metadata, "--metadata")
    if expires is not None or clear_expiry:
        params["expiration_date"] = None if clear_expiry else expires
    if not params:
        raise CommandError("nothing to update: pass text, --metadata or --expires")
    return memory.update(memory_id, **params)


def delete(memory, memory_id: Optional[str] = None, *, all_: bool = False, dry_run: bool = False,
           **ids: Optional[str]) -> Dict:
    """Delete one memory, or every memory in a scope with ``all_``."""
    if memory_id:
        if dry_run:
            return {"would_delete": [memory_id] if memory.get(memory_id) else []}
        return memory.delete(memory_id)
    scope = _scope(**ids)
    if not all_ or not scope:
        raise CommandError("pass a memory id, or --all with a scope")
    if dry_run:
        rows = memory.get_all(filters=scope, top_k=100_000, show_expired=True)["results"]
        return {"would_delete": [r["id"] for r in rows]}
    return memory.delete_all(**scope)


def import_file(memory, path: str, *, infer: bool = False, **ids: Optional[str]) -> Dict:
    """Import memories from a JSON file: a list of objects with ``memory``/``text``/``content`` and
    optional scope ids and ``metadata``. Scope flags override the file's ids."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CommandError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, list):
        data = [data]
    overrides = _scope(**ids)
    added, failed = 0, 0
    errors: List[str] = []
    for item in data:
        if not isinstance(item, dict):
            failed += 1
            continue
        content = item.get("memory", item.get("text", item.get("content", "")))
        scope = {**{k: item[k] for k in SCOPE_KEYS if item.get(k)}, **overrides}
        if not content or not scope:
            failed += 1
            continue
        try:
            memory.add(content, metadata=item.get("metadata"), infer=infer, **scope)
            added += 1
        except Exception as exc:
            failed += 1
            errors.append(str(exc)[:200])
    return {"added": added, "failed": failed, "errors": errors[:10]}


def entities(memory) -> List[Dict]:
    return _list_entities(memory)


def delete_entity(memory, entity_type: str, entity_id: str) -> Dict:
    return _delete_entity(memory, entity_type, entity_id)


def history(memory, memory_id: str) -> List[Dict]:
    return memory.history(memory_id)

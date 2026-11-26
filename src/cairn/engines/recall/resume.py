"""The resume snapshot: what an agent needs to pick its work back up after the host compacts the conversation.

Built from ``sessions.db`` alone, by fixed rules (no model call): the last request, the files changed, the
tasks still open, and the decisions and rejected approaches recorded so far. It is a short list of pointers,
not the content: each line names the ``cairn_session_*`` tool that returns the details. The snapshot never
exceeds ``SNAPSHOT_MAX_BYTES``. Standard library only.
"""
from __future__ import annotations

import json
from typing import Any

from . import fallback
from .tags import redact
from .store import Store

SNAPSHOT_MAX_BYTES = 2048
KV_PREFIX = "resume."
MAX_FILES = 8
MAX_TASKS = 5
MAX_DECISIONS = 4
LAST_REQUEST_CHARS = 220
TASK_TOOLS = ("TodoWrite",)
HEADER = "Cairn resume snapshot (the conversation was just compacted; these are pointers, fetch details with the tools named)."


def kv_key(content_session_id: str) -> str:
    return KV_PREFIX + content_session_id


def _clip(text: Any, width: int) -> str:
    one = redact(" ".join(str(text or "").split()))
    return one if len(one) <= width else one[: width - 1].rstrip() + "…"


def open_tasks(tool_uses: list[dict]) -> list[str]:
    """The tasks not yet completed in the agent's latest task list (``TodoWrite``), in order."""
    latest = None
    for row in tool_uses:
        if row["tool_name"] in TASK_TOOLS:
            latest = row
    if latest is None:
        return []
    try:
        data = json.loads(latest["tool_input"] or "{}")
    except ValueError:
        return []
    todos = data.get("todos") if isinstance(data, dict) else None
    if not isinstance(todos, list):
        return []
    return [_clip(t.get("content") or t.get("subject"), 90) for t in todos
            if isinstance(t, dict) and t.get("status") in ("pending", "in_progress") and (t.get("content") or t.get("subject"))]


def changed_files(tool_uses: list[dict], root: str | None) -> list[str]:
    calls = [{"tool_name": r["tool_name"], "tool_input": r["tool_input"], "cwd": r["cwd"]} for r in tool_uses]
    return fallback.turn_evidence(calls, root)["modified"]


def _fit(lines: list[str]) -> str:
    """Whole lines while they fit; the header always stays."""
    out: list[str] = []
    used = 0
    for ln in lines:
        size = len(ln.encode("utf-8")) + 1
        if out and used + size > SNAPSHOT_MAX_BYTES:
            break
        out.append(ln)
        used += size
    text = "\n".join(out)
    while len(text.encode("utf-8")) > SNAPSHOT_MAX_BYTES:  # a single oversized line
        text = text[: max(1, len(text) - 40)].rstrip()
    return text


def build_snapshot(store: Store, content_session_id: str, root: str | None = None) -> str:
    """The resume snapshot for one session, or '' when there is nothing recorded to resume from."""
    sess = store.db.execute("SELECT id, memory_session_id FROM sdk_sessions WHERE content_session_id=?"
                            " ORDER BY id DESC LIMIT 1", (content_session_id,)).fetchone()
    if sess is None:
        return ""
    sid, memory_id = sess[0], sess[1]
    last = store.db.execute("SELECT prompt_text FROM user_prompts WHERE session_db_id=? AND prompt_text != ''"
                            " ORDER BY prompt_number DESC LIMIT 1", (sid,)).fetchone()
    uses = [dict(r) for r in store.db.execute(
        "SELECT tool_name, tool_input, cwd FROM tool_uses WHERE content_session_id=? ORDER BY id", (content_session_id,))]
    files = changed_files(uses, root)
    tasks = open_tasks(uses)
    notes = []
    if memory_id:
        notes = [dict(r) for r in store.db.execute(
            "SELECT id, title, metadata FROM observations WHERE memory_session_id=? AND type='decision'"
            " ORDER BY id DESC LIMIT ?", (memory_id, MAX_DECISIONS))]
    if not (last or files or tasks or notes):
        return ""
    lines = [HEADER]
    if last:
        lines.append(f"Last request: {_clip(last[0], LAST_REQUEST_CHARS)}")
    if files:
        shown = ", ".join(files[:MAX_FILES]) + (f" (+{len(files) - MAX_FILES} more)" if len(files) > MAX_FILES else "")
        lines.append(f"Files changed ({len(files)}): {shown}")
        lines.append(f'  -> cairn_session_search(query="{files[0].rsplit("/", 1)[-1]}") for what was done to them')
    if tasks:
        lines.append(f"Open tasks ({len(tasks)}): " + "; ".join(tasks[:MAX_TASKS])
                     + (f" (+{len(tasks) - MAX_TASKS} more)" if len(tasks) > MAX_TASKS else ""))
    if notes:
        lines.append("Decisions and rejected approaches:")
        lines += [f"  [obs {n['id']}] {_clip(n['title'], 110)}" for n in notes]
        ids = [n["id"] for n in notes]
        lines.append(f"  -> cairn_session_observations(ids={ids}) for the detail; cairn_session_timeline(anchor={ids[0]}) for"
                     " what surrounded the latest")
    return _fit(lines)


def save(store: Store, content_session_id: str, text: str) -> None:
    key = kv_key(content_session_id)
    if text:
        store.set_kv(key, text)
    else:
        store.del_kv(key)


def take(store: Store, content_session_id: str, root: str | None = None) -> str:
    """The snapshot saved at compaction (and forgotten once handed over); built now when none was saved."""
    key = kv_key(content_session_id)
    text = store.get_kv(key) or build_snapshot(store, content_session_id, root)
    store.del_kv(key)
    return text or ""


__all__ = ["SNAPSHOT_MAX_BYTES", "build_snapshot", "save", "take", "open_tasks", "changed_files"]

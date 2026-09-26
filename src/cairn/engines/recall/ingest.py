"""The one choke point every capture path goes through (hooks, transcript replay, imports).

Each function does the bookkeeping the original worker did on receipt (session row, numbered
prompts, privacy decision, duplicate suppression, raw tool-use side index) and appends the work to
the durable queue; nothing here calls a model. Standard library only.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .platforms import normalize_platform_source
from .projects import is_project_excluded, project_context
from .settings import csv
from .store import USER_PROMPT_DEDUPE_WINDOW_MS, Store
from .tags import is_internal_protocol_payload, redact_value, strip_memory_tags

log = logging.getLogger("cairn.recall")

MAX_USER_PROMPT_BYTES = 256 * 1024
MEDIA_PROMPT = "[media prompt]"
FILE_OPERATION_TOOLS = {"Edit", "Write", "Read", "NotebookEdit", "MultiEdit"}


def _truncate_bytes(text: str, max_bytes: int) -> str:
    data = text.encode("utf-8")
    return text if len(data) <= max_bytes else data[:max_bytes].decode("utf-8", errors="ignore")


def check_prompt_privacy(store: Store, content_session_id: str, prompt_number: int, session_db_id: int) -> dict:
    """Allow work for a prompt unless the user redacted that whole prompt. A missing prompt row is
    not a privacy signal (the prompt hook may simply not have run)."""
    text = store.get_user_prompt(content_session_id, prompt_number, session_db_id)
    if text is None:
        return {"allow": True, "prompt": ""}
    if not text.strip():
        return {"allow": False, "reason": "private"}
    return {"allow": True, "prompt": text}


def session_init(store: Store, *, content_session_id: str, project: str | None = None, prompt: str | None = None,
                 platform_source: str | None = None, custom_title: str | None = None, cwd: str | None = None) -> dict:
    """UserPromptSubmit: create/find the session, number and store the prompt (privacy-stripped)."""
    src = normalize_platform_source(platform_source)
    raw = prompt if isinstance(prompt, str) else None
    if raw and is_internal_protocol_payload(raw):
        return {"skipped": True, "reason": "internal_protocol"}
    text = _truncate_bytes(raw or MEDIA_PROMPT, MAX_USER_PROMPT_BYTES) if (raw and raw.strip()) else MEDIA_PROMPT
    cleaned = strip_memory_tags(text)
    project = project or "unknown"
    with store.tx():
        sid = store.create_sdk_session(content_session_id, project, cleaned, custom_title, src, cwd)
        store.db.execute("UPDATE sdk_sessions SET status='active', completed_at=NULL, completed_at_epoch=NULL"
                         " WHERE id=? AND status='completed'", (sid,))
        number = store.get_prompt_number(content_session_id, sid) + 1
        if not cleaned.strip():
            # An entirely private prompt is recorded empty so the observations of this turn are withheld.
            store.save_user_prompt(content_session_id, number, "", sid, raw=True)
            return {"sessionDbId": sid, "promptNumber": number, "skipped": True, "reason": "private"}
        dup = store.find_recent_duplicate_user_prompt(content_session_id, cleaned, USER_PROMPT_DEDUPE_WINDOW_MS, sid)
        if dup:
            return {"sessionDbId": sid, "promptNumber": dup["prompt_number"], "skipped": True, "reason": "duplicate"}
        pid = store.save_user_prompt(content_session_id, number, cleaned, sid)
    return {"sessionDbId": sid, "promptNumber": number, "promptId": pid, "skipped": False, "status": "initialized",
            "platformSource": src, "project": project}


def _json_text(value: Any) -> str:
    if value is None:
        return "{}"
    try:
        return strip_memory_tags(json.dumps(redact_value(value), ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return strip_memory_tags(json.dumps(str(value)))


def ingest_observation(store: Store, settings: dict, *, content_session_id: str, tool_name: str,
                       tool_input: Any = None, tool_response: Any = None, cwd: str | None = None,
                       platform_source: str | None = None, agent_id: str | None = None,
                       agent_type: str | None = None, tool_use_id: str | None = None,
                       project: str | None = None, prompt_number: int | None = None) -> dict:
    """PostToolUse: queue one tool event for the observer (and back it up in the tool-use index)."""
    src = normalize_platform_source(platform_source)
    cwd = cwd if isinstance(cwd, str) else ""
    if not project:
        project = project_context(cwd).primary if cwd.strip() else ""
    if cwd and is_project_excluded(cwd, str(settings.get("excluded_projects") or "")):
        return {"ok": True, "status": "skipped", "reason": "project_excluded"}
    if tool_name in set(csv(settings.get("skip_tools") or "")):
        return {"ok": True, "status": "skipped", "reason": "tool_excluded"}
    if tool_name in FILE_OPERATION_TOOLS and isinstance(tool_input, dict):
        fp = tool_input.get("file_path") or tool_input.get("notebook_path")
        if isinstance(fp, str) and "session-memory" in fp:
            return {"ok": True, "status": "skipped", "reason": "session_memory_meta"}
    with store.tx():
        sid = store.create_sdk_session(content_session_id, project, "", None, src, cwd or None)
        number = prompt_number if prompt_number else store.get_prompt_number(content_session_id, sid)
        privacy = check_prompt_privacy(store, content_session_id, number, sid)
        if not privacy["allow"]:
            return {"ok": True, "status": "skipped", "reason": "private"}
        ti, tr = _json_text(tool_input), _json_text(tool_response)
        if tool_use_id:
            try:
                store.upsert_tool_use(tool_use_id=tool_use_id, content_session_id=content_session_id, session_db_id=sid,
                                      project=project, platform_source=src, tool_name=tool_name, tool_input=ti,
                                      tool_response=tr, cwd=cwd or None, prompt_number=number, agent_type=agent_type,
                                      agent_id=agent_id)
            except Exception as exc:  # the backup index never costs the observation
                log.warning("tool_uses backup write failed: %s", exc)
        mid = store.enqueue(sid, content_session_id, "observation", tool_name=tool_name, tool_input=ti,
                            tool_response=tr, prompt_number=number, cwd=cwd, agent_id=agent_id, agent_type=agent_type,
                            tool_use_id=tool_use_id)
    if not mid:
        return {"ok": True, "status": "skipped", "reason": "duplicate", "sessionDbId": sid}
    return {"ok": True, "status": "queued", "sessionDbId": sid, "messageId": mid}


def queue_summarize(store: Store, *, content_session_id: str, last_assistant_message: str | None,
                    platform_source: str | None = None, agent_id: str | None = None,
                    observed_model: str | None = None, cwd: str | None = None, project: str | None = None,
                    prompt_number: int | None = None) -> dict:
    """Stop: queue a progress summary of the prompt that just finished."""
    if agent_id:
        return {"status": "skipped", "reason": "subagent_context"}
    src = normalize_platform_source(platform_source)
    with store.tx():
        sid = store.create_sdk_session(content_session_id, project or (project_context(cwd).primary if cwd else ""),
                                       "", None, src, cwd)
        store.set_session_observed_model(sid, observed_model)
        number = prompt_number if prompt_number else store.get_prompt_number(content_session_id, sid)
        if not check_prompt_privacy(store, content_session_id, number, sid)["allow"]:
            return {"status": "skipped", "reason": "private"}
        cleaned = strip_memory_tags(str(last_assistant_message)) if last_assistant_message else last_assistant_message
        mid = store.enqueue(sid, content_session_id, "summarize", last_assistant_message=cleaned, prompt_number=number,
                            cwd=cwd)
    return {"status": "queued", "sessionDbId": sid, "messageId": mid}


def session_end(store: Store, *, content_session_id: str, platform_source: str | None = None) -> dict:
    sid = store.find_session_db_id(content_session_id, platform_source)
    if sid is None:
        return {"status": "unknown_session"}
    store.mark_session_completed(sid)
    return {"status": "accepted", "sessionDbId": sid}


def file_edit(store: Store, settings: dict, *, content_session_id: str, file_path: str, edits: Any, cwd: str,
              platform_source: str | None = None, project: str | None = None) -> dict:
    """An editor-reported write (Cursor/Windsurf) recorded as a ``write_file`` tool event."""
    return ingest_observation(store, settings, content_session_id=content_session_id, tool_name="write_file",
                              tool_input={"filePath": file_path, "edits": edits}, tool_response={"success": True},
                              cwd=cwd, platform_source=platform_source, project=project)

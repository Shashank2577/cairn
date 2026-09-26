"""Read the last user/assistant message (and model) out of an agent's JSONL transcript.

Supports Claude Code (``{"type": "assistant", "message": {"content": ...}}``), Cursor
(``{"role": ...}``) and Antigravity (``{"type": "PLANNER_RESPONSE", "content": ...}``) line shapes.
Malformed lines are skipped. Standard library only.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path

from .tags import SYSTEM_REMINDER_RE

log = logging.getLogger("cairn.recall")
_ANTIGRAVITY = {"USER_INPUT": "user", "PLANNER_RESPONSE": "assistant"}


def _read(path: str | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        log.warning("transcript missing or unreadable: %s", path)
        return ""


def _lines_backward(content: str) -> Iterator[dict]:
    for raw in reversed(content.split("\n")):
        if not raw:
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if isinstance(line, dict):
            yield line


def content_to_text(content) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c["text"] for c in content if isinstance(c, dict) and isinstance(c.get("text"), str)
                         and c.get("type") in (None, "text"))
    return None


def last_message_from_jsonl(content: str, role: str, strip_reminders: bool = False) -> str:
    found, empty = False, None
    for line in _lines_backward(content):
        ag = _ANTIGRAVITY.get(line.get("type")) if isinstance(line.get("type"), str) else None
        line_role = ag or line.get("type") or line.get("role")
        if line_role != role:
            continue
        found = True
        msg = line.get("content") if ag else (line.get("message") or {}).get("content") \
            if isinstance(line.get("message"), dict) else None
        if msg is None:
            continue
        text = content_to_text(msg)
        if text is None:
            continue
        if strip_reminders:
            text = re.sub(r"\n{3,}", "\n\n", SYSTEM_REMINDER_RE.sub("", text)).strip()
        if text and text.strip():
            return text
        if empty is None:
            empty = text
    return (empty or "") if found else ""


def last_assistant_model_from_jsonl(content: str) -> str | None:
    for line in _lines_backward(content):
        if (line.get("type") or line.get("role")) != "assistant":
            continue
        model = (line.get("message") or {}).get("model") if isinstance(line.get("message"), dict) else None
        if isinstance(model, str) and model:
            return model
    return None


def extract_last_message(path: str | None, role: str, strip_reminders: bool = False) -> str:
    content = _read(path)
    return last_message_from_jsonl(content, role, strip_reminders) if content else ""


def extract_last_assistant_turn(path: str | None, strip_reminders: bool = False) -> tuple[str, str | None]:
    content = _read(path)
    if not content:
        return "", None
    return last_message_from_jsonl(content, "assistant", strip_reminders), last_assistant_model_from_jsonl(content)


def extract_last_assistant_model(path: str | None) -> str | None:
    content = _read(path)
    return last_assistant_model_from_jsonl(content) if content else None

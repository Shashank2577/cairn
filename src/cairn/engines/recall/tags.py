"""Privacy and protocol tag handling (standard library only).

Anything a user wraps in ``<private>...</private>`` never reaches the store. Cairn's own injected
context (``<cairn-context>`` and the AGENTS.md memory block), system reminders and persisted tool output
are stripped too, so memory is never built out of memory.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("cairn.recall")

TAG_NAMES = (
    "private",
    "cairn-context",
    "system_instruction",
    "system-instruction",
    "persisted-output",
    "system-reminder",
)
STRIP_RE = re.compile(r"<(" + "|".join(re.escape(t) for t in TAG_NAMES) + r")\b[^>]*>[\s\S]*?</\1>")
# the memory block Cairn keeps in AGENTS.md (comment-delimited, so every agent's instruction loader keeps it)
MEMORY_BLOCK_RE = re.compile(r"<!-- cairn:memory:begin -->[\s\S]*?<!-- cairn:memory:end -->")
SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>[\s\S]*?</system-reminder>")
MAX_TAG_COUNT = 100

PROTOCOL_ONLY_TAGS = ("task-notification",)
_PROTOCOL_RE = re.compile(
    r"^\s*<(" + "|".join(PROTOCOL_ONLY_TAGS) + r")\b[^>]*>(?:(?!<\1\b|</\1\b)[\s\S])*</\1>\s*$")
MAX_PROTOCOL_PAYLOAD_CHARS = 256 * 1024

MAX_STORED_PROMPT_CHARS = 4000


def strip_tags(text: str) -> tuple[str, dict[str, int]]:
    """Remove every tagged block; returns (stripped text, count per tag)."""
    counts = {name: 0 for name in TAG_NAMES}

    def drop(m: re.Match) -> str:
        counts[m.group(1)] = counts.get(m.group(1), 0) + 1
        return ""

    stripped = MEMORY_BLOCK_RE.sub("", STRIP_RE.sub(drop, text or ""))
    total = sum(counts.values())
    if total > MAX_TAG_COUNT:
        log.warning("tag count exceeds limit: %s (max %s, %s chars)", total, MAX_TAG_COUNT, len(text or ""))
    return stripped.strip(), counts


def strip_memory_tags(text: str) -> str:
    return strip_tags(text)[0]


def is_internal_protocol_payload(text: str) -> bool:
    """A prompt that is only an agent-protocol envelope (e.g. a task notification) is not user work."""
    if not text or len(text) > MAX_PROTOCOL_PAYLOAD_CHARS:
        return False
    return bool(_PROTOCOL_RE.match(text))


def normalize_stored_prompt_text(prompt: str) -> str:
    """The prompt as stored: private blocks removed, bounded in size."""
    raw = (prompt or "").strip()
    stripped = strip_memory_tags(prompt or "").strip()
    preferred = stripped or raw
    if len(preferred) <= MAX_STORED_PROMPT_CHARS:
        return preferred
    return preferred[: MAX_STORED_PROMPT_CHARS - 1] + "…"


# Values that look like credentials are masked in anything recorded from tool input.
SECRET_RE = re.compile(
    r"(?i)\b([\w-]*(?:token|secret|password|passwd|api[_-]?key)[\w-]*)(\s*[=:]\s*)(['\"]?)[^\s'\"]+\3"
    r"|\b(bearer)\s+[\w.~+/-]+=*|(--[\w-]*(?:token|secret|password|api-key)[\w-]*)\s+\S+")


def redact(text: str) -> str:
    def mask(m: re.Match) -> str:
        if m.group(4):
            return f"{m.group(4)} ***"
        if m.group(5):
            return f"{m.group(5)} ***"
        return f"{m.group(1)}{m.group(2)}***"
    return SECRET_RE.sub(mask, text or "")


def redact_value(value, _depth: int = 0):
    """Redact every string inside a JSON-shaped value (dicts, lists), leaving its structure intact."""
    if _depth > 12:
        return value
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_value(v, _depth + 1) for v in value]
    if isinstance(value, dict):
        return {k: redact_value(v, _depth + 1) for k, v in value.items()}
    return value

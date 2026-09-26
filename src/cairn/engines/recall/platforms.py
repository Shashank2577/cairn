"""Which agent a session came from ("platform source"). Standard library only."""
from __future__ import annotations

DEFAULT_PLATFORM_SOURCE = "claude"
_PRIORITY = ("claude", "codex", "cursor")


def normalize_platform_source(value: str | None) -> str:
    if not value:
        return DEFAULT_PLATFORM_SOURCE
    source = "-".join(str(value).strip().lower().split())
    if not source:
        return DEFAULT_PLATFORM_SOURCE
    if source == "transcript" or "codex" in source:
        return "codex"
    if "cursor" in source:
        return "cursor"
    if "claude" in source:
        return "claude"
    return source


def normalize_platform_source_or_none(value) -> str | None:
    return normalize_platform_source(value) if isinstance(value, str) else None


def sort_platform_sources(sources: list[str]) -> list[str]:
    def key(s: str):
        return (0, _PRIORITY.index(s), "") if s in _PRIORITY else (1, 0, s)
    return sorted(sources, key=key)

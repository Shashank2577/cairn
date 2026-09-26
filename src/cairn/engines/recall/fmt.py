"""Shared text formatting for timelines, search results and context (standard library only).
Dates render in local time, US style ("Sep 25, 2026", "3:07 PM"), as the timeline has always read."""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, TypeVar

T = TypeVar("T")
CHARS_PER_TOKEN = 4


def _dt(value: Any) -> datetime:
    """A local datetime from an epoch (ms or s) or an ISO string."""
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(secs)
    s = str(value or "")
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.astimezone() if d.tzinfo else d
    except ValueError:
        return datetime.now()


def _hour(d: datetime) -> str:
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def format_date(value: Any) -> str:
    d = _dt(value)
    return f"{d:%b} {d.day}, {d.year}"


def format_time(value: Any) -> str:
    return _hour(_dt(value))


def format_datetime(value: Any) -> str:
    d = _dt(value)
    return f"{d:%b} {d.day}, {_hour(d)}"


def compact_time(t: str) -> str:
    return t.lower().replace(" am", "a").replace(" pm", "p")


def header_datetime() -> str:
    d = datetime.now().astimezone()
    tz = d.tzname() or ""
    return f"{d:%Y-%m-%d} {compact_time(_hour(d)).replace(' ', '')} {tz}".strip()


def parse_json_array(value: Any) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def to_relative(path: str, cwd: str) -> str:
    if os.path.isabs(path):
        try:
            return os.path.relpath(path, cwd)
        except ValueError:
            return path
    return path


def extract_first_file(files_modified: Any, cwd: str, files_read: Any = None) -> str:
    mod = parse_json_array(files_modified)
    if mod:
        return to_relative(str(mod[0]), cwd)
    read = parse_json_array(files_read)
    if read:
        return to_relative(str(read[0]), cwd)
    return "General"


def estimate_tokens(text: str | None) -> int:
    return -(-len(text) // CHARS_PER_TOKEN) if text else 0


def group_by_date(items: Iterable[T], get_date: Callable[[T], Any]) -> list[tuple[str, list[T]]]:
    groups: dict[str, list[T]] = {}
    first: dict[str, float] = {}
    for it in items:
        d = _dt(get_date(it))
        key = format_date(get_date(it))
        groups.setdefault(key, []).append(it)
        first.setdefault(key, datetime(d.year, d.month, d.day).timestamp())
    return sorted(groups.items(), key=lambda kv: first[kv[0]])


def thousands(n: int) -> str:
    return f"{n:,}"

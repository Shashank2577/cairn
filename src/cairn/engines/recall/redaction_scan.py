"""Read-only count of stored rows that still hold a credential shape. Never prints or rewrites values."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .tags import redact_hits

# table -> text columns that can hold captured or derived text
COLUMNS = {
    "user_prompts": ("prompt_text",),
    "sdk_sessions": ("user_prompt",),
    "pending_messages": ("tool_input", "tool_response", "last_user_message", "last_assistant_message"),
    "tool_uses": ("tool_input", "tool_response"),
    "observations": ("text", "title", "subtitle", "facts", "narrative"),
    "session_summaries": ("request", "investigated", "learned", "completed", "next_steps", "notes"),
    "observer_conversations": ("history",),
    "remote_outbox": ("payload",),
}


def scan_store(db_path: Path | str) -> dict[str, dict[str, int]]:
    """``{pattern id: {table: affected row count}}``. Opens the database read-only."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    counts: dict[str, dict[str, int]] = {}
    try:
        for table, cols in COLUMNS.items():
            try:
                rows = db.execute(f"SELECT {', '.join(cols)} FROM {table}")
            except sqlite3.Error:
                continue  # table missing in an older store
            for row in rows:
                ids = {pid for v in row if isinstance(v, str) and v for pid in redact_hits(v)}
                for pid in ids:
                    counts.setdefault(pid, {}).setdefault(table, 0)
                    counts[pid][table] += 1
    finally:
        db.close()
    return counts

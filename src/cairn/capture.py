"""Session capture: the agent hook entry point for Cairn's session memory (recall engine).

Runs on every prompt and tool call, so it stays tiny: standard library only, a few SQLite writes into
``<repo>/.cairn/sessions.db``, no model, and it never breaks the agent (bad input or a locked store
is ignored, exit code 0). The Stop and SessionEnd events start the recall worker in the background,
which turns the queued events into observations and summaries (``engines/recall/worker.py``).

Usage (from an agent hook, JSON payload on stdin):
  python -m cairn.capture [--platform claude-code|codex|cursor|windsurf|antigravity|gemini|raw] <event>
  events: context, session-init, observation, file-context, summarize, session-end, user-message, file-edit
  (the earlier names prompt, tool and stop still work)
"""
from __future__ import annotations

from pathlib import Path

from .engines.recall.hooks import main, record  # noqa: F401  (re-exported entry points)
from .engines.recall.projects import find_store_root as find_root  # noqa: F401
from .engines.recall.schema import store_path  # noqa: F401
from .engines.recall.tags import redact  # noqa: F401


def _rel(path: str, root: Path, cwd: str) -> str:
    """``path`` relative to the repository root ('' when it lies outside)."""
    if not path:
        return ""
    p = Path(path)
    p = p if p.is_absolute() else Path(cwd) / p
    try:
        return p.resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return ""


if __name__ == "__main__":
    raise SystemExit(main())

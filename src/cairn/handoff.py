"""``cairn handoff``: a resume briefing for the next agent, built from a captured session plus project memory.

A handoff is not a transcript dump. It carries what the session was after, where it stopped, the files it
touched with their blast radius, the conventions that apply, and a one-paragraph prompt tailored to the agent
that will pick it up.
"""
from __future__ import annotations

import time
from pathlib import Path

from .engines.recall import api as recall_api

TARGETS = ("claude", "codex", "gemini", "opencode", "cline", "cursor")
MAX_IMPACT_FILES = 5
MAX_PROMPTS = 3
MAX_MEMORIES = 12
CLIP = 600

# How each agent is started with an initial prompt (printed, never executed).
LAUNCH = {
    "claude": "claude '{prompt}' --add-dir '{dir}'",
    "codex": "codex '{prompt}'",
    "gemini": "gemini --prompt-interactive '{prompt}' --include-directories '{dir}'",
    "opencode": "opencode --prompt '{prompt}'",
    "cline": "cline '{prompt}'",
    "cursor": "cursor-agent '{prompt}'",
}
RESUME = ("This file is a handoff from an earlier AI coding session on this project. Continue the work from "
          "where it stopped, follow the conventions it lists, and check the impact notes before editing the "
          "files named there.")


class HandoffError(ValueError):
    pass


def _clip(text: str | None, n: int = CLIP) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _pick_session(root: Path, session_id: str | None) -> dict:
    if session_id:
        found = recall_api.session(root, session_id)
    else:
        items = recall_api.sessions(root, limit=1)["items"]
        found = recall_api.session(root, items[0]["db_id"]) if items else None
    if not found:
        raise HandoffError(f"No captured session {session_id!r}." if session_id else "No captured sessions yet.")
    return found


def _files_touched(data: dict) -> list[str]:
    seen: dict[str, None] = {}
    for obs in data["observations"]:
        for f in obs["files_modified"]:
            seen.setdefault(f, None)
    for s in data["summaries"]:
        for f in s["files_edited"]:
            seen.setdefault(f, None)
    return list(seen)


def build_handoff(cairn, root: Path, session_id: str | None = None, to: str | None = None,
                  impact_budget: int = 500) -> tuple[str, dict]:
    """The Markdown briefing and its metadata (``session``, ``files``, ``prompt``)."""
    if to is not None and to not in TARGETS:
        raise HandoffError(f"Unknown agent {to!r}; choose from {', '.join(TARGETS)}.")
    data = _pick_session(root, session_id)
    sess, prompts, summaries = data["session"], data["prompts"], data["summaries"]
    files = _files_touched(data)
    last = summaries[-1] if summaries else None
    asked = [p for p in prompts if (p["text"] or "").strip()]

    out = [f"# Handoff: {_clip(sess['title'], 100)}", "",
           f"Session `{sess['id']}` ({sess.get('agent') or 'agent'}"
           + (f", branch `{sess['branch']}`" if sess.get("branch") else "") + f", status {sess.get('status')}).", ""]

    out += ["## What was asked", ""]
    out += [f"- {_clip(p['text'])}" for p in asked[:1] + (asked[-(MAX_PROMPTS - 1):] if len(asked) > 1 else [])]
    out.append("")

    out += ["## Where it stopped", ""]
    if last:
        for label, key in (("Completed", "completed"), ("Next steps", "next_steps"), ("Learned", "learned")):
            if last.get(key):
                out.append(f"- **{label}:** {_clip(last[key])}")
    else:
        out.append("- No summary was captured for this session; the latest observations follow.")
        for o in data["observations"][-5:]:
            out.append(f"- {_clip(o.get('title') or o.get('narrative'), 200)}")
    out.append("")

    if files:
        out += ["## Files touched and their blast radius", ""]
        for f in files[:MAX_IMPACT_FILES]:
            try:
                pack = cairn.impact(f, 1, impact_budget).render().strip()
            except ValueError:
                pack = ""
            out += [f"### `{f}`", "", pack or "_No impact data._", ""]
        if len(files) > MAX_IMPACT_FILES:
            out += [f"Also touched: {', '.join(f'`{f}`' for f in files[MAX_IMPACT_FILES:MAX_IMPACT_FILES + 20])}", ""]

    try:
        mems = cairn.brain.memories(limit=MAX_MEMORIES)
    except Exception:  # memory is optional context; a handoff is still useful without it
        mems = []
    if mems:
        out += ["## Team conventions and decisions", ""]
        out += [f"- ({m['kind']}) {_clip(m['text'], 300)}" for m in mems]
        out.append("")

    out += ["## Resume", "", RESUME, ""]
    return "\n".join(out), {"session": sess["id"], "files": files, "title": sess["title"]}


def launch_line(target: str, path: Path) -> str:
    """The shell command that starts ``target`` on the handoff file (single quotes in the prompt are escaped)."""
    prompt = f"Read the file {path} completely. {RESUME}".replace("'", "'\\''")
    return LAUNCH[target].format(prompt=prompt, dir=str(path.parent).replace("'", "'\\''"))


def write_handoff(root: Path, text: str, session: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch == "-" else "_" for ch in session)[:40]
    path = root / ".cairn" / "handoffs" / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path

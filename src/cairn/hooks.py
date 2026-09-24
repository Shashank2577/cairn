"""Git hooks and agent hook entry points (session-start briefing, status line)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys

from .project import Project

MARK = "# cairn-hook"
GIT_EVENTS = ("post-commit", "post-merge", "post-checkout", "post-rewrite")


def _line() -> str:
    exe = "cairn" if shutil.which("cairn") else f'"{sys.executable}" -m cairn'
    return f'{exe} hook git >/dev/null 2>&1 & {MARK}'


def install_git_hooks(project: Project) -> list[str]:
    if not project.is_git:
        return []
    hooks_dir = project.root / ".git" / "hooks"
    custom = project.git("config", "--get", "core.hooksPath").strip()
    if custom:
        hooks_dir = (project.root / custom).resolve()
    hooks_dir.mkdir(parents=True, exist_ok=True)
    added = []
    for ev in GIT_EVENTS:
        p = hooks_dir / ev
        text = p.read_text() if p.exists() else "#!/bin/sh\n"
        if MARK in text:
            continue
        p.write_text(text.rstrip("\n") + "\n" + _line() + "\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        added.append(ev)
    return added


def remove_git_hooks(project: Project) -> list[str]:
    removed = []
    hooks_dir = project.root / ".git" / "hooks"
    for ev in GIT_EVENTS:
        p = hooks_dir / ev
        if p.exists() and MARK in p.read_text():
            lines = [ln for ln in p.read_text().splitlines() if MARK not in ln]
            if [ln for ln in lines if ln.strip() and not ln.startswith("#!")]:
                p.write_text("\n".join(lines) + "\n")
            else:
                p.unlink()
            removed.append(ev)
    return removed


def session_start(cairn) -> str:
    """Claude Code SessionStart hook: inject a compact project brief as additional context."""
    brief = cairn.brief(max_tokens=550)
    return json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": brief}})


def statusline(cairn, stdin_json: str = "") -> str:
    """One line for the agent status bar: layers, active spec, drift, model."""
    try:
        info = json.loads(stdin_json) if stdin_json.strip() else {}
    except json.JSONDecodeError:
        info = {}
    b = cairn.brain
    c = b.counts()
    drift = json.loads(b.get_kv("drift.last", "[]") or "[]")
    high = sum(d["severity"] == "high" for d in drift)
    active = cairn.active_spec()
    color = os.environ.get("NO_COLOR") is None
    dim, amber, rose, rst = ("\033[2m", "\033[38;5;179m", "\033[38;5;174m", "\033[0m") if color else ("",) * 4
    parts = [f"{amber}▲ cairn{rst}", f"{c.get('file', 0):,} files", f"{c.get('memory_active', 0)} memories"]
    if active:
        parts.append(f"{active['id'][5:9]} {active['done']}/{active['total']}")
    if drift:
        parts.append(f"{rose}{len(drift)} drift{' (' + str(high) + ' high)' if high else ''}{rst}")
    model = (info.get("model") or {}).get("display_name")
    if model:
        parts.append(f"{dim}{model}{rst}")
    return f" {dim}·{rst} ".join(parts)

"""Git hooks and agent hook entry points (session-start briefing, status line)."""
from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

from . import shellcmd
from .project import Project
from .router import estimate_tokens

MARK = "# cairn-hook"
GIT_EVENTS = ("post-commit", "post-merge", "post-checkout", "post-rewrite")
# Hooks Cairn may add its line to: POSIX shell scripts (git runs them with sh; Git for Windows with its own sh).
_SHELL_SHEBANG = re.compile(r"#!\s*(?:.*[/\\])?(?:env\s+(?:-\S+\s+)*)?(?:sh|bash|dash|ash|ksh|mksh|zsh)(?:\.exe)?(?:\s|$)")


def hook_line(python: str | None = None) -> str:
    """The line Cairn puts in a git hook: start ``cairn hook git`` in the background and return at once.

    It runs the interpreter Cairn is installed in, never ``cairn`` from PATH (git GUIs often run hooks with a
    bare PATH), quoted for POSIX sh, which is also what Git for Windows runs hooks with, so a Windows path is
    written with forward slashes. stdin/stdout/stderr are detached so git never waits on it; a missing
    interpreter fails silently. The hook can never block or fail a commit."""
    py = shellcmd.python_prefix(python or sys.executable, "posix")
    return f"{py} -m cairn hook git </dev/null >/dev/null 2>&1 & {MARK}"


def _read(path: Path) -> str:
    """A hook file as text, byte-exact on the way back (it may not be UTF-8, and Windows' locale is not)."""
    return path.read_bytes().decode("utf-8", "surrogateescape")


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8", "surrogateescape"))  # bytes: no CRLF translation on Windows


def with_hook_line(text: str | None, line: str) -> str | None:
    """``text`` with exactly one Cairn line, placed right after the shebang so an ``exit`` further down can't
    skip it. None when the hook is written for another interpreter (``#!/usr/bin/env python``, …)."""
    if not text or not text.strip():
        return f"#!/bin/sh\n{line}\n"
    newline = "\r\n" if "\r\n" in text else "\n"  # keep the file's own line endings
    lines = [ln for ln in text.splitlines(keepends=True) if MARK not in ln]
    head: list[str] = []
    if lines and lines[0].startswith("#!"):
        if not _SHELL_SHEBANG.match(lines[0].strip()):
            return None
        head = [lines[0] if lines[0].endswith(("\n", "\r")) else lines[0] + newline]
        lines = lines[1:]
    return "".join([*head, line + newline, *lines])


def install_git_hooks(project: Project) -> list[str]:
    """Add Cairn's line to each post-* hook, creating hooks as needed. Returns the hooks added or updated;
    hooks for another interpreter are left alone."""
    if not project.is_git:
        return []
    try:
        line = hook_line()
    except ValueError:  # an interpreter path that can't be written on one line
        return []
    hooks_dir = _hooks_dir(project)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    added = []
    for ev in GIT_EVENTS:
        p = hooks_dir / ev
        old = _read(p) if p.exists() else None
        new = with_hook_line(old, line)
        if new is None or new == old:
            continue
        _write(p, new)
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        added.append(ev)
    return added


def _hooks_dir(project: Project) -> Path:
    """Where git runs this repository's hooks: git resolves core.hooksPath (including `~`) itself. Read as
    UTF-8 so a non-ASCII path survives a Windows console code page."""
    try:
        res = subprocess.run(["git", "-C", str(project.root), "rev-parse", "--git-path", "hooks"], capture_output=True,
                             encoding="utf-8", errors="surrogateescape", timeout=30, check=False)
        path = res.stdout.strip() if res.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        path = ""
    if not path:
        return project.root / ".git" / "hooks"
    p = Path(path).expanduser()
    return p if p.is_absolute() else (project.root / p).resolve()


def remove_git_hooks(project: Project) -> list[str]:
    removed = []
    hooks_dir = _hooks_dir(project)
    for ev in GIT_EVENTS:
        p = hooks_dir / ev
        if not p.exists():
            continue
        text = _read(p)
        if MARK not in text:
            continue
        kept = [ln for ln in text.splitlines(keepends=True) if MARK not in ln]
        if any(ln.strip() and not ln.startswith("#!") for ln in kept):
            _write(p, "".join(kept))
        else:
            p.unlink()
        removed.append(ev)
    return removed


def session_start(cairn) -> str:
    """Claude Code SessionStart hook: inject a compact project brief as additional context."""
    brief = cairn.brief(max_tokens=550)
    # The briefing is context Cairn adds to every session: count it, with no file baseline.
    with contextlib.suppress(sqlite3.Error):
        cairn.brain.log_query("hook", "brief", "session start", sum(estimate_tokens(ln) for ln in brief.splitlines()),
                              None)
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
        sid = active["id"].removeprefix("spec:")
        num = re.match(r"\d+", sid)
        parts.append(f"{num.group() if num else sid} {active['done']}/{active['total']}")
    if drift:
        parts.append(f"{rose}{len(drift)} drift{' (' + str(high) + ' high)' if high else ''}{rst}")
    model = (info.get("model") or {}).get("display_name")
    if model:
        parts.append(f"{dim}{model}{rst}")
    return f" {dim}·{rst} ".join(parts)

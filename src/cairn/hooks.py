"""Git hooks and agent hook entry points (session-start briefing, ambient context on prompt submit, status
line)."""
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


# ---- ambient context (the enforced read path) --------------------------------------------------------------
AMBIENT_CAP = 2000  # bytes/chars of nugget (~500 tokens): this runs on every prompt, so it must stay small
_AMBIENT_MORE = "… (cairn: run `cairn context <task>` for more)"
_AMBIENT_MIN = 20  # shorter prompts are chat ("hi", "ok done") — nothing to ground
# A path-ish token (two chars, then an extension, or a path separator), CamelCase, snake_case or ANY-CAPS.
_AMBIENT_CODEISH = re.compile(r"[A-Za-z0-9_][\w-]+\.[A-Za-z0-9]{1,6}\b|\w/\w|[a-z][A-Z]|\w_\w|\b[A-Z]{2,}\b")
_AMBIENT_VERBS = frozenset(("add", "build", "change", "create", "debug", "delete", "extend", "extract", "fix",
                            "implement", "make", "migrate", "patch", "refactor", "remove", "rework", "revert",
                            "test", "update", "write"))


def _prompt_of(payload: str) -> str:
    """The prompt text in Claude Code's UserPromptSubmit payload (``{"prompt": …, "session_id": …, "cwd": …}``).
    Defensive like `statusline`'s reader: a non-object payload or a missing prompt yields ''; a payload that is
    not JSON at all is taken as the bare prompt itself."""
    if not payload or not payload.strip():
        return ""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload
    if not isinstance(data, dict):  # Claude Code can pipe non-object payloads ("null", a list)
        return ""
    prompt = data.get("prompt")
    return prompt if isinstance(prompt, str) else ""


def _plausibly_code(prompt: str) -> bool:
    """A cheap deterministic gate: does the prompt plausibly touch code? Greetings, short chat and questions
    like "what's the weather" stay out. No model calls."""
    if len(prompt.strip()) < _AMBIENT_MIN:
        return False
    if _AMBIENT_CODEISH.search(prompt):
        return True
    words = set(re.findall(r"[a-z]+", prompt.lower()))
    return any(w in _AMBIENT_VERBS
               or (w.endswith("ing") and w[:-3] in _AMBIENT_VERBS)
               or (w.endswith("ed") and w[:-2] in _AMBIENT_VERBS)
               or (w.endswith("s") and w[:-1] in _AMBIENT_VERBS) for w in words)


def ambient(cairn, payload: str = "") -> dict | None:
    """Claude Code UserPromptSubmit hook: a small, budget-capped context nugget for the prompt just submitted.

    UserPromptSubmit output is injected into the model's context, so this is the enforced read path: agents get
    Cairn's memory without choosing to consult it. Guardrails: the ``[context] ambient`` kill-switch (off by
    default), a code-relevance gate, a hard size cap, and strictly read-only access (no brain writes, no model
    calls). Nothing resolves → None. Any exception → None: a hook must never block a prompt."""
    try:
        if not cairn.project.cfg("context.ambient", False):
            return None
        text = _prompt_of(payload)
        if not text or not _plausibly_code(text):
            return None
        lines = []
        targets = cairn.infer_targets(text, limit=2)  # already stopword-filtered and map-resolved
        if targets:
            lines.append(f"Targets: {', '.join(targets)}")
        for m in cairn.memory.recall(text, limit=3):
            lines.append(f"- [{m['kind']}] {' '.join(str(m['text']).split())[:160]} [memory:{m['id']}]")
        if not lines:
            return None
        nugget = "Cairn context for this prompt:\n" + "\n".join(lines)
        if len(nugget) > AMBIENT_CAP:
            nugget = nugget[:AMBIENT_CAP - len(_AMBIENT_MORE)].rstrip() + _AMBIENT_MORE
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": nugget}}
    except Exception:  # noqa: BLE001 - a failing hook must never block the prompt
        return None


def statusline(cairn, stdin_json: str = "") -> str:
    """One line for the agent status bar: layers, active spec, drift, model."""
    try:
        info = json.loads(stdin_json) if stdin_json.strip() else {}
    except json.JSONDecodeError:
        info = {}
    if not isinstance(info, dict):  # Claude Code can pipe non-object payloads ("null", a list)
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


def global_session_hint(payload: str = "") -> dict | None:
    """User-level SessionStart hook (registered by ``cairn global install``): where Cairn is installed
    but the repository is not initialised, hand the agent one line so it can offer the setup. Runs with
    no project and must never create state — pure filesystem checks only."""
    try:
        data = json.loads(payload) if payload.strip() else {}
    except json.JSONDecodeError:
        data = {}
    cwd = data.get("cwd") if isinstance(data, dict) else None
    try:
        root = Path(cwd).resolve() if cwd else Path.cwd().resolve()
        if root == Path.home().resolve():
            return None
    except OSError:
        return None
    if not (root / ".git").exists() or (root / ".cairn").exists():
        return None  # not a repository, or already initialised (any cairn state counts)
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext":
            "Cairn (local project memory: impact, history, conventions for coding agents) is installed "
            "but not initialized in this repository. Offer the user, once: `cairn init --no-deep` sets it "
            "up in ~15s, entirely local — do not run it without their go-ahead."}}


def recap(cairn) -> str:
    """What cairn has done for this repo so far — the visible receipt. Pure reads, no model calls."""
    import time as _t

    from .core import ago
    b = cairn.brain
    n_mem = b.q("SELECT COUNT(*) FROM memories")[0]["COUNT(*)"]
    n_commits = b.q("SELECT COUNT(*) FROM events WHERE kind='commit'")[0]["COUNT(*)"]
    n_sessions = b.q("SELECT COUNT(*) FROM events WHERE kind='session'")[0]["COUNT(*)"]
    first = b.q("SELECT MIN(ts) AS t FROM events")[0]["t"]
    q = b.q("SELECT COUNT(*) AS n, COALESCE(SUM(sent_tokens),0) AS sent, "
            "COALESCE(SUM(source_tokens),0) AS src FROM queries")[0]
    drift = len(json.loads(b.get_kv("drift.last", "[]") or "[]"))
    lines = [f"▲ cairn  recap — tracking since {ago(first)}" if first else "▲ cairn  recap",
             f"  learned     {n_mem} memories from this repo's history",
             f"  watched     {n_commits:,} commits, {n_sessions} agent sessions",
             f"  watching    {drift} drift findings right now"]
    if q["n"]:
        saved = max(0, q["src"] - q["sent"])
        pct = round(saved * 100 / q["src"]) if q["src"] else 0
        lines.append(f"  answered    {q['n']} queries — saved ~{saved:,} tokens ({pct}% vs reading the files)")
    else:
        lines.append("  answered    no queries yet — the savings meter starts with your first impact/why/ask")
    lines.append(f"  last activity {_t.strftime('%b %d', _t.localtime(first))}" if first else "")

    def _clip(text: str, n: int = 96) -> str:
        one = " ".join(str(text).split())
        return one if len(one) <= n else one[: n - 1] + "…"

    # receipts: the actual data, so the counts above are checkable at a glance
    receipts = []
    for m in b.q("SELECT kind, text, id FROM memories WHERE NOT forgotten AND superseded_by IS NULL "
                 "ORDER BY created_at DESC LIMIT 2"):
        receipts.append(f"  · [{m['kind']}] {_clip(m['text'])}  [{m['id']}]")
    if receipts:
        lines.append("")
        lines.append("  memories it learned (newest):")
        lines += receipts
    qrows = b.q("SELECT kind, target, sent_tokens, COALESCE(source_tokens,0) AS src FROM queries "
                "ORDER BY ts DESC LIMIT 3")
    if qrows:
        lines.append("")
        lines.append("  last answers (what they cost vs reading):")
        for r in qrows:
            saved = max(0, r["src"] - r["sent_tokens"])
            lines.append(f"  · {r['kind']} {r['target']} — sent {r['sent_tokens']:,}, saved {saved:,}")
    last_commit = b.q("SELECT title FROM events WHERE kind='commit' ORDER BY ts DESC LIMIT 1")
    if last_commit:
        lines.append("")
        lines.append(f"  newest commit watched: {_clip(last_commit[0]['title'], 80)}")
    return "\n".join(ln for ln in lines if ln)

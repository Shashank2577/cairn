"""Write the repository's documentation by running the documentation skill, on demand.

One run is one Claude Code session in the repository: it follows the skill (architecture, data flow, API,
security, ... into ``docs/``), finding things through Cairn first. It is the only place Cairn starts an agent
that edits files, so it never starts on its own: the page offers it, says what it costs, and the caller has to
ask. The run uses the user's own Claude sign-in, is capped in dollars by the CLI itself, and can be cancelled.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .router import find_claude

MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}
DEFAULT_SKILL = "code-documentation"
DEFAULT_BUDGET_USD = 3.0
# Measured in Oct 2026 on a ~300-file slice: Haiku used about 220k-300k tokens, Sonnet about 210k-300k.
ESTIMATE = {"haiku": "about 200k-300k tokens, 10-20 minutes", "sonnet": "about 200k-300k tokens, 10-20 minutes"}
TOOLS = "Read,Write,Edit,Glob,Grep,Bash(python3:*),Bash(python:*),Bash(ls:*),mcp__cairn__*"

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def prompt(skill: str) -> str:
    return (f"Use the {skill} skill to document this repository into docs/. Find things with the Cairn tools "
            "first (cairn_context, cairn_search, cairn_impact) and read only the file ranges they cite; fall back to "
            "grep only when Cairn has nothing. Cite evidence for every claim, write every document the skill "
            "calls for, and run its validator until it passes. Do not change source code.")


def status(root: Path) -> dict:
    with _lock:
        job = _jobs.get(str(root))
        return dict(job, tail=_tail(job)) if job else {"state": "idle"}


def _tail(job: dict) -> str:
    try:
        return Path(job["log"]).read_text(encoding="utf-8", errors="replace")[-600:]
    except (OSError, KeyError):
        return ""


def start(root: Path, model: str = "haiku", skill: str = DEFAULT_SKILL, budget: float = DEFAULT_BUDGET_USD) -> dict:
    """Start a run; raises RuntimeError with a sentence the page can show when it cannot."""
    if model not in MODELS:
        raise RuntimeError(f"unknown model {model!r}; choose {', '.join(MODELS)}")
    claude = find_claude()
    if not claude:
        raise RuntimeError("the Claude Code CLI was not found: install or sign in to Claude Code (`claude`)")
    key = str(root)
    with _lock:
        if _jobs.get(key, {}).get("state") == "running":
            raise RuntimeError("a documentation run is already in progress for this project")
        log = tempfile.NamedTemporaryFile("w", suffix=".log", prefix="cairn-docgen-", delete=False)
        args = [claude, "-p", prompt(skill), "--model", MODELS[model], "--output-format", "json",
                "--permission-mode", "acceptEdits", "--allowedTools", TOOLS, "--max-budget-usd", str(budget),
                "--no-session-persistence"]
        proc = subprocess.Popen(args, cwd=root, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        job = {"state": "running", "model": model, "skill": skill, "budget_usd": budget, "started": time.time(),
               "pid": proc.pid, "log": log.name}
        _jobs[key] = job
    threading.Thread(target=_wait, args=(key, proc, job), daemon=True).start()
    return status(root)


def _wait(key: str, proc: subprocess.Popen, job: dict) -> None:
    code = proc.wait()
    result = {}
    try:
        result = json.loads(Path(job["log"]).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        pass
    with _lock:
        if job["state"] == "cancelled":
            return
        failed = code != 0 or result.get("is_error")
        job.update(state="failed" if failed else "done", ended=time.time(), cost_usd=result.get("total_cost_usd"),
                   turns=result.get("num_turns"), usage=result.get("usage"),
                   message=(result.get("result") or "")[:600])


def cancel(root: Path) -> dict:
    with _lock:
        job = _jobs.get(str(root))
        if job and job["state"] == "running":
            job.update(state="cancelled", ended=time.time())
            try:
                import os
                import signal
                os.kill(job["pid"], signal.SIGTERM)
            except OSError:
                pass
    return status(root)

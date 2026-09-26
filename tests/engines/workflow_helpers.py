"""Shared helpers for the workflow-engine tests (imported by tests/engines/test_workflow_*.py)."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# Names of the projects Cairn absorbed must never surface in Cairn's code or in what it generates.
# The list is assembled at runtime so this file itself stays clean under the same check.
_WORDS = ["graph" + "ify", "spec" + "-kit", "spec" + "kit", "spec" + " kit", "specify" + "_cli", "specify" + "-cli",
          "specify" + " cli", r"(?<![\w])\." + "specify", "claude" + "-mem", "claude" + "_mem", r"\bc" + r"mem\b",
          "thedot" + "mack", "graph" + "iti", r"\bz" + r"ep\b", "mem" + "0"]
FORBIDDEN = re.compile("|".join(_WORDS), re.I)

# The legacy layout, for building fixture repositories the migration must convert.
OLD_NS = "spec" + "kit"
OLD_DIR = "." + "spec" + "ify"

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
           "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.com"}


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True, env=GIT_ENV)
    (path / "README.md").write_text("# demo\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, env=GIT_ENV)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True, env=GIT_ENV)
    return path


def spec(root: Path, *args: str, stdin: str | None = None) -> tuple[int, str]:
    """Run ``cairn spec <args>`` in-process from *root*."""
    from cairn.engines import specs

    return specs.run(list(args), root, stdin=stdin)


def init(root: Path, integration: str = "claude", *extra: str) -> str:
    code, out = spec(root, "init", "--here", "--integration", integration, "--non-interactive", "--force",
                     "--ignore-agent-tools", *extra)
    assert code == 0, out
    return out


def forbidden_hits(root: Path, *, skip: tuple[str, ...] = (".git",)) -> list[str]:
    """Every file path or line under *root* that mentions an absorbed project's name."""
    hits = []
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root)
        if any(part in skip for part in rel.parts) or "__pycache__" in rel.parts:
            continue
        if FORBIDDEN.search(rel.as_posix()):
            hits.append(f"path: {rel.as_posix()}")
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN.search(line):
                hits.append(f"{rel.as_posix()}:{n}: {line.strip()[:120]}")
    return hits

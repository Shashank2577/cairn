"""The files of one repository that the system model may read, bounded by ``limits``.

Read-only and defensive: tracked files from ``git ls-files`` (falling back to a walk that never follows
symbolic links), nothing larger than ``MAX_FILE_BYTES``, no binary files, text decoded as UTF-8 with
replacement, unreadable files and symlinks skipped and counted. Paths are POSIX and relative to the root.
"""
from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..limits import MAX_FILE_BYTES, MAX_FILES_SCANNED

# Never part of any deploy unit's own code.
SKIP_DIRS = frozenset({".git", ".cairn", ".hg", ".svn", "node_modules", "bower_components", "vendor", "third_party",
                       ".venv", "venv", "__pycache__", "dist", "build", "target", "obj", ".tox", ".nox",
                       ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vscode", ".next", ".nuxt",
                       ".gradle", "coverage", ".terraform", ".pytest-fixture-cache", "site-packages",
                       ".github", ".gitlab", ".devcontainer", ".circleci"})
# Test, example and documentation trees: their deploy files and calls describe test rigs, not the system.
# Singular "example"/"sample" are left out: they are common package names (``com/example/...``).
TEST_DIRS = frozenset({"test", "tests", "testing", "__tests__", "__test__", "e2e", "fixtures", "fixture", "testdata",
                       "test-data", "test_data", "examples", "samples", "sandbox", "docs", "doc", "__mocks__",
                       "mocks", "benchmarks", "integration-tests"})
_TEST_FILE_MARKERS = (".test.", ".spec.", "_test.", "-test.", ".e2e.", ".ci.")


def is_test_path(rel: str) -> bool:
    p = PurePosixPath(rel)
    parts = p.parts[:-1]
    if "main" in parts and "src" in parts:  # Maven/Gradle layout: production code lives under src/main
        parts = parts[: parts.index("src")]
    if any(part.lower() in TEST_DIRS for part in parts):
        return True
    name = p.name.lower()
    if name.startswith("test_") or name.startswith("conftest") or any(m in name for m in _TEST_FILE_MARKERS):
        return True
    stem = p.stem
    return stem.endswith(("Test", "Tests", "IT")) and p.suffix in (".java", ".kt", ".cs")


@dataclass
class RepoFiles:
    root: Path
    paths: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    _cache: dict[str, str | None] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def read(self, rel: str) -> str | None:
        """The file's text, or None when it is missing, too large, binary or unreadable."""
        if rel in self._cache:
            return self._cache[rel]
        text = None
        p = self.root / rel
        try:
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                self.skip("not a regular file")
            elif st.st_size > MAX_FILE_BYTES:
                self.skip("larger than the file limit")
            else:
                with open(p, "rb") as fh:
                    data = fh.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    self.skip("larger than the file limit")
                elif b"\0" in data[:8192]:
                    self.skip("binary")
                else:
                    text = data.decode("utf-8", "replace")
        except OSError:
            self.skip("unreadable")
        if len(self._cache) < 4096:
            self._cache[rel] = text
        return text

    def raw_contains(self, rel: str, needles) -> bool:
        text = self.read(rel)
        return bool(text) and any(n in text for n in needles)

    def named(self, *names: str) -> list[str]:
        want = {n.lower() for n in names}
        return [p for p in self.paths if PurePosixPath(p).name.lower() in want]


def scan(root: Path) -> RepoFiles:
    root = Path(root)
    rf = RepoFiles(root=root)
    listed = _git_files(root)
    candidates = listed if listed is not None else _walk(root, rf)
    for rel in candidates:
        parts = PurePosixPath(rel).parts
        if any(x in SKIP_DIRS for x in parts[:-1]):
            continue
        if len(rf.paths) >= MAX_FILES_SCANNED:
            rf.skip("over the file-count limit")
            break
        rf.paths.append(rel)
    rf.paths.sort()
    return rf


def _git_files(root: Path) -> list[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, timeout=60, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    return [x for x in out.decode("utf-8", "replace").split("\0") if x]


def _walk(root: Path, rf: RepoFiles) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=lambda e: rf.skip("unreadable")):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d)))
        rel_dir = os.path.relpath(dirpath, root)
        for name in filenames:
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            out.append(rel.replace(os.sep, "/"))
            if len(out) > MAX_FILES_SCANNED:
                return out
    return out

"""Resolve what a review reads: the change between a base and a head, minus files that are not worth reading.

Everything here is plain git through the project's runner, and text is decoded as UTF-8 explicitly.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import PurePosixPath

LOCK_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock", "Pipfile.lock",
              "Cargo.lock", "go.sum", "Gemfile.lock", "composer.lock", "mix.lock", "pubspec.lock", "bun.lockb",
              "Podfile.lock", "gradle.lockfile", "flake.lock"}
VENDORED_DIRS = ("node_modules/", "vendor/", "third_party/", "dist/", "build/", ".venv/", "__pycache__/",
                 "site-packages/", "Pods/")
GENERATED_GLOBS = ("*.min.js", "*.min.css", "*.map", "*_pb2.py", "*.pb.go", "*_generated.*", "*.generated.*",
                   "*.snap", "*.lcov")
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2", ".ttf", ".otf",
              ".mp4", ".mov", ".so", ".dylib", ".dll", ".exe", ".pyc", ".whl", ".db", ".sqlite"}
MAX_FILE_LINES = 5000  # a single file this large is almost certainly data


class DiffError(RuntimeError):
    """The change cannot be resolved; the message says what to do."""


@dataclass
class ChangedFile:
    path: str
    status: str                                            # A | M | D | R
    added: list[tuple[int, str]] = field(default_factory=list)    # (line number in the new file, text)
    removed: list[str] = field(default_factory=list)
    old_path: str | None = None

    @property
    def size(self) -> int:
        return sum(len(t) for _, t in self.added) + sum(len(t) for t in self.removed)


@dataclass
class Diff:
    branch: str
    base_ref: str
    base_sha: str
    head_sha: str
    files: list[ChangedFile] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)       # [{path, reason}]
    commits: list[str] = field(default_factory=list)        # shas on the branch, newest first
    kind: str = "branch"                                    # branch | staged | working | pr
    pr_number: int | None = None

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]

    @property
    def empty(self) -> bool:
        return not self.files and not self.skipped


def skip_reason(path: str, nlines: int = 0) -> str | None:
    p = PurePosixPath(path)
    if p.name in LOCK_FILES:
        return "lock file"
    if any(path.startswith(d) or f"/{d}" in f"/{path}" for d in VENDORED_DIRS):
        return "vendored or built output"
    if any(fnmatch(p.name, g) for g in GENERATED_GLOBS):
        return "generated file"
    if p.suffix.lower() in BINARY_EXT:
        return "binary file"
    if nlines > MAX_FILE_LINES:
        return "very large file"
    return None


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_patch(raw: str) -> list[ChangedFile]:
    files: list[ChangedFile] = []
    cur: ChangedFile | None = None
    new_line = 0
    for line in raw.splitlines():
        if line.startswith("diff --git "):
            cur = None
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            if m:
                cur = ChangedFile(path=m.group(2), status="M", old_path=m.group(1))
                files.append(cur)
        elif cur is None:
            continue
        elif line.startswith("new file mode"):
            cur.status = "A"
        elif line.startswith("deleted file mode"):
            cur.status = "D"
        elif line.startswith("rename to "):
            cur.status, cur.path = "R", line[10:]
        elif line.startswith("Binary files"):
            cur.status = cur.status if cur.status != "M" else "B"
        elif line.startswith("+++ ") or line.startswith("--- "):
            continue
        elif (m := _HUNK.match(line)):
            new_line = int(m.group(1))
        elif line.startswith("+"):
            cur.added.append((new_line, line[1:]))
            new_line += 1
        elif line.startswith("-"):
            cur.removed.append(line[1:])
        elif line.startswith(" "):
            new_line += 1
    return files


def _default_base(project, head: str) -> str:
    for ref in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
        sha = project.git("rev-parse", "--verify", "--quiet", ref).strip()
        if sha and sha != head:
            return ref
    return ""


def _filter(files: list[ChangedFile]) -> tuple[list[ChangedFile], list[dict]]:
    keep, skipped = [], []
    for f in files:
        reason = skip_reason(f.path, len(f.added))
        if reason:
            skipped.append({"path": f.path, "reason": reason})
        elif f.status == "B":
            skipped.append({"path": f.path, "reason": "binary file"})
        else:
            keep.append(f)
    return keep, skipped


def _pr_ref(project, number: int) -> str:
    for ref in (f"refs/pull/{number}/head", f"refs/remotes/origin/pr/{number}", f"refs/remotes/origin/pull/{number}/head",
                f"refs/heads/pr/{number}", f"refs/heads/pr-{number}"):
        if project.git("rev-parse", "--verify", "--quiet", ref).strip():
            return ref
    raise DiffError(f"Pull request {number} is not in this clone. Fetch it first: "
                    f"git fetch origin pull/{number}/head:refs/pull/{number}/head")


def resolve(project, target: str | None = None, base: str | None = None, staged: bool = False) -> Diff:
    """The change to review: a branch (default: the current one), a pull request number, or staged changes."""
    head_ref, kind, pr = "HEAD", "branch", None
    if target and target.isdigit():
        head_ref, kind, pr = _pr_ref(project, int(target)), "pr", int(target)
    elif target:
        if not project.git("rev-parse", "--verify", "--quiet", target).strip():
            raise DiffError(f"'{target}' is not a branch, commit or pull request number in this repository.")
        head_ref = target
    head = project.git("rev-parse", head_ref).strip()
    if not head:
        raise DiffError("This repository has no commits yet, so there is nothing to review.")
    current = project.git("rev-parse", "--abbrev-ref", head_ref).strip()
    branch = f"pr/{pr}" if pr else (current if current and current != "HEAD" else head[:10])

    if staged:
        raw = project.git("-c", "core.quotepath=off", "diff", "--cached", "--unified=0", "--no-color", "--no-ext-diff", "-M")
        files, skipped = _filter(parse_patch(raw))
        stamp = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]
        return Diff(branch=branch, base_ref="staged", base_sha=head, head_sha=f"staged-{stamp}", files=files,
                    skipped=skipped, kind="staged")

    base_ref = base or _default_base(project, project.git("rev-parse", head_ref).strip())
    if not base_ref:
        raise DiffError("No base to compare with (no main or master branch). Pass one: cairn review --base <ref>")
    base_sha = project.git("rev-parse", "--verify", "--quiet", base_ref).strip()
    if not base_sha:
        raise DiffError(f"'{base_ref}' is not a branch or commit in this repository.")
    merge_base = project.git("merge-base", base_sha, head).strip() or base_sha
    raw = project.git("-c", "core.quotepath=off", "diff", "--unified=0", "--no-color", "--no-ext-diff", "-M", f"{merge_base}..{head}", timeout=120)
    files, skipped = _filter(parse_patch(raw))
    commits = project.git("rev-list", f"{merge_base}..{head}").split()
    return Diff(branch=branch, base_ref=base_ref, base_sha=merge_base, head_sha=head, files=files, skipped=skipped,
                commits=commits, kind=kind, pr_number=pr)

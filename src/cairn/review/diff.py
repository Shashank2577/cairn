"""Resolve what a review reads: the change between a base and a head, minus files that are not worth reading.

Everything here is plain git through the project's runner, and text is decoded as UTF-8 explicitly.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
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
    binary: bool = False

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
    skipped_files: list[ChangedFile] = field(default_factory=list)  # their content, for the secret scan only
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


_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_ESC = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}


def _unquote(name: str) -> str:
    """Git's C-style quoting (used for tabs, quotes, backslashes and, unless disabled, non-ASCII)."""
    name = name.rstrip("\t")
    if not (name.startswith('"') and name.endswith('"') and len(name) >= 2):
        return name
    body, out, i = name[1:-1], bytearray(), 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in "01234567":
                j = i + 1
                while j < len(body) and j < i + 4 and body[j] in "01234567":
                    j += 1
                out.append(int(body[i + 1:j], 8) & 255)
                i = j
                continue
            out.append(_ESC.get(nxt, ord(nxt)))
            i += 2
            continue
        out += ch.encode("utf-8")
        i += 1
    return out.decode("utf-8", "replace")


def _strip_prefix(name: str) -> str:
    name = _unquote(name)
    return name[2:] if name[:2] in ("a/", "b/") else name


def _header_paths(rest: str) -> tuple[str, str]:
    """`"a/x" "b/y"` or `a/x b/y` from a `diff --git` line (unquoted names may hold spaces: split on the b/ half)."""
    if rest.startswith('"'):
        m = re.match(r'("(?:[^"\\]|\\.)*")\s+(.*)$', rest)
        if m:
            return _strip_prefix(m.group(1)), _strip_prefix(m.group(2))
    half = len(rest) // 2
    if len(rest) % 2 == 1 and rest[:half].startswith("a/") and rest[half] == " " and rest[half + 1:].startswith("b/"):
        return _strip_prefix(rest[:half]), _strip_prefix(rest[half + 1:])
    m = re.match(r"(a/.*?) (b/.*)$", rest)
    return (_strip_prefix(m.group(1)), _strip_prefix(m.group(2))) if m else (rest, rest)


def parse_patch(raw: str) -> list[ChangedFile]:
    """Parse `git diff --unified=0`. Hunk line counts decide what is content, so a content line that starts with
    `---` or `+++` is never mistaken for a file header."""
    files: list[ChangedFile] = []
    cur: ChangedFile | None = None
    new_line = old_left = new_left = 0
    for line in raw.splitlines():
        if old_left > 0 or new_left > 0:
            if line.startswith("+"):
                cur.added.append((new_line, line[1:]))  # type: ignore[union-attr]
                new_line += 1
                new_left -= 1
                continue
            if line.startswith("-"):
                cur.removed.append(line[1:])  # type: ignore[union-attr]
                old_left -= 1
                continue
            if line.startswith(" "):
                new_line += 1
                old_left -= 1
                new_left -= 1
                continue
            if line.startswith("\\"):
                continue
            old_left = new_left = 0
        if line.startswith("diff --git "):
            old, new = _header_paths(line[11:])
            cur = ChangedFile(path=new, status="M", old_path=old)
            files.append(cur)
        elif cur is None:
            continue
        elif line.startswith("new file mode"):
            cur.status = "A"
        elif line.startswith("deleted file mode"):
            cur.status = "D"
        elif line.startswith("rename from "):
            cur.old_path = _unquote(line[12:])
        elif line.startswith("rename to "):
            cur.status, cur.path = "R", _unquote(line[10:])
        elif line.startswith("Binary files") or line.startswith("GIT binary patch"):
            cur.binary = True
        elif line.startswith("+++ "):
            if not line.startswith("+++ /dev/null"):
                cur.path = _strip_prefix(line[4:])
        elif line.startswith("--- "):
            continue
        elif (m := _HUNK.match(line)):
            old_left = int(m.group(1)) if m.group(1) is not None else 1
            new_line = int(m.group(2))
            new_left = int(m.group(3)) if m.group(3) is not None else 1
    return files


def _default_base(project, head: str) -> str:
    for ref in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
        sha = project.git("rev-parse", "--verify", "--quiet", ref).strip()
        if sha and sha != head:
            return ref
    return ""


def _filter(files: list[ChangedFile]) -> tuple[list[ChangedFile], list[dict], list[ChangedFile]]:
    keep, skipped, content = [], [], []
    for f in files:
        reason = "binary file" if f.binary else skip_reason(f.path, len(f.added))
        if reason:
            skipped.append({"path": f.path, "reason": reason})
            if not f.binary:
                content.append(f)  # skipped for review, still scanned for secrets
        else:
            keep.append(f)
    return keep, skipped, content


def _pr_ref(project, number: int) -> str:
    for ref in (f"refs/pull/{number}/head", f"refs/remotes/origin/pr/{number}", f"refs/remotes/origin/pull/{number}/head",
                f"refs/heads/pr/{number}", f"refs/heads/pr-{number}"):
        if project.git("rev-parse", "--verify", "--quiet", ref).strip():
            return ref
    raise DiffError(f"Pull request {number} is not in this clone. Fetch it first: "
                    f"git fetch origin pull/{number}/head:refs/pull/{number}/head")


def _git(project, *args: str, timeout: int = 60, check: bool = True) -> str:
    """Run git and return stdout. A failure or timeout is a DiffError: it must never look like an empty change."""
    cmd = ["git", "-C", str(project.root), *args]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DiffError(f"git {args[0] if args[0] != '-c' else args[2]} timed out after {timeout}s; "
                        "try a smaller change or --base.") from None
    except OSError as exc:
        raise DiffError(f"Could not run git: {exc}") from None
    if res.returncode != 0 and check:
        verb = next((a for a in args if not a.startswith("-") and "=" not in a), args[0])
        msg = res.stderr.decode("utf-8", "replace").strip().splitlines()
        raise DiffError(f"git {verb} failed" + (f": {msg[-1][:200]}" if msg else "."))
    return res.stdout.decode("utf-8", "replace") if res.returncode == 0 else ""


def resolve(project, target: str | None = None, base: str | None = None, staged: bool = False) -> Diff:
    """The change to review: a branch (default: the current one), a pull request number, or staged changes."""
    head_ref, kind, pr = "HEAD", "branch", None
    if target and target.isascii() and target.isdecimal():
        head_ref, kind, pr = _pr_ref(project, int(target)), "pr", int(target)
    elif target:
        if not project.git("rev-parse", "--verify", "--quiet", target).strip():
            raise DiffError(f"'{target}' is not a branch, commit or pull request number in this repository.")
        head_ref = target
    head = _git(project, "rev-parse", "--verify", "--quiet", head_ref, check=False).strip()
    if not head:
        raise DiffError("This repository has no commits yet, so there is nothing to review.")
    current = project.git("rev-parse", "--abbrev-ref", head_ref).strip()
    branch = f"pr/{pr}" if pr else (current if current and current != "HEAD" else head[:10])
    flags = ("-c", "core.quotepath=off", "diff", "--unified=0", "--no-color", "--no-ext-diff", "-M")

    if staged:
        raw = _git(project, *flags, "--cached", timeout=120)
        files, skipped, content = _filter(parse_patch(raw))
        stamp = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]
        return Diff(branch=branch, base_ref="staged", base_sha=head, head_sha=f"staged-{stamp}", files=files,
                    skipped=skipped, skipped_files=content, kind="staged")

    base_ref = base or _default_base(project, head)
    if not base_ref:
        raise DiffError("No base to compare with (no main or master branch). Pass one: cairn review --base <ref>")
    base_sha = _git(project, "rev-parse", "--verify", "--quiet", base_ref, check=False).strip()
    if not base_sha:
        raise DiffError(f"'{base_ref}' is not a branch or commit in this repository.")
    merge_base = _git(project, "merge-base", base_sha, head, check=False).strip() or base_sha
    raw = _git(project, *flags, f"{merge_base}..{head}", timeout=120)
    files, skipped, content = _filter(parse_patch(raw))
    commits = _git(project, "rev-list", f"{merge_base}..{head}", timeout=120).split()
    return Diff(branch=branch, base_ref=base_ref, base_sha=merge_base, head_sha=head, files=files, skipped=skipped,
                skipped_files=content, commits=commits, kind=kind, pr_number=pr)

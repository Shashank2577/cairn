"""Project identity, git worktrees and exclusion rules (standard library only).

A project is named after its git repository root, so a session started in a sub-folder belongs to
the same project. A linked git worktree is ``<parent>/<worktree>`` and reads context from both
names; its records are stored in the parent repository's ``.cairn/sessions.db`` when the worktree
has no Cairn folder of its own.
"""
from __future__ import annotations

import contextvars
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

UNKNOWN_PROJECT = "unknown-project"

# Set while recording an event pushed from another machine: its paths are not on this disk, so the
# branch it declared is used instead of reading one.
_DECLARED_BRANCH: contextvars.ContextVar[tuple[str | None] | None] = contextvars.ContextVar(
    "recall_declared_branch", default=None)


@contextmanager
def declared_branch(branch: str | None) -> Iterator[None]:
    token = _DECLARED_BRANCH.set((branch,))
    try:
        yield
    finally:
        _DECLARED_BRANCH.reset(token)


def expand_home(p: str) -> str:
    if p == "~" or p.startswith("~/") or p.startswith("~\\"):
        return str(Path.home()) + p[1:]
    return p


def git_root(start: str | Path) -> Path | None:
    """The working-tree root containing ``start`` (a ``.git`` folder or file), or None."""
    try:
        here = Path(expand_home(str(start))).resolve()
    except (OSError, RuntimeError):
        return None
    for p in (here, *here.parents):
        if (p / ".git").exists():
            return p
    return None


@dataclass
class WorktreeInfo:
    is_worktree: bool = False
    worktree_name: str | None = None
    parent_repo_path: str | None = None
    parent_project_name: str | None = None


_WORKTREES = re.compile(r"^(.+)[/\\]\.git[/\\]worktrees[/\\]([^/\\]+)$")


def detect_worktree(cwd: str | Path) -> WorktreeInfo:
    git = Path(cwd) / ".git"
    try:
        if not git.is_file():
            return WorktreeInfo()
        content = git.read_text(errors="replace", encoding="utf-8").strip()
    except OSError:
        return WorktreeInfo()
    m = re.match(r"^gitdir:\s*(.+)$", content)
    if not m:
        return WorktreeInfo()
    gitdir = os.path.normpath(os.path.join(os.path.dirname(str(git)), m.group(1).strip()))
    wm = _WORKTREES.match(gitdir)
    if not wm:
        return WorktreeInfo()
    parent = wm.group(1)
    return WorktreeInfo(True, Path(cwd).name, parent, Path(parent).name)


def git_branch(cwd: str | None) -> str | None:
    """The checked-out branch of the repository containing ``cwd`` (read from HEAD; no git process)."""
    declared = _DECLARED_BRANCH.get()
    if declared is not None:
        return declared[0]
    root = git_root(cwd) if cwd else None
    if root is None:
        return None
    git = root / ".git"
    try:
        if git.is_file():
            m = re.match(r"^gitdir:\s*(.+)$", git.read_text(errors="replace", encoding="utf-8").strip())
            if not m:
                return None
            git = Path(os.path.normpath(os.path.join(str(root), m.group(1).strip())))
        head = (git / "HEAD").read_text(errors="replace", encoding="utf-8").strip()
    except OSError:
        return None
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else (head[:12] or None)


def project_name(cwd: str | None) -> str:
    if not cwd or not str(cwd).strip():
        return UNKNOWN_PROJECT
    expanded = expand_home(str(cwd))
    source = git_root(expanded) or Path(expanded)
    name = Path(source).name
    if not name:
        m = re.match(r"^([A-Z]):\\", str(cwd), re.IGNORECASE)
        return f"drive-{m.group(1).upper()}" if m and os.name == "nt" else UNKNOWN_PROJECT
    return name


@dataclass
class ProjectContext:
    primary: str
    parent: str | None = None
    is_worktree: bool = False
    all_projects: list[str] = field(default_factory=list)


def project_context(cwd: str | None) -> ProjectContext:
    name = project_name(cwd)
    if not cwd:
        return ProjectContext(name, None, False, [name])
    root = git_root(cwd) or Path(expand_home(str(cwd)))
    wt = detect_worktree(root)
    if wt.is_worktree and wt.parent_project_name:
        composite = f"{wt.parent_project_name}/{name}"
        return ProjectContext(composite, wt.parent_project_name, True, [wt.parent_project_name, composite])
    return ProjectContext(name, None, False, [name])


def hook_project_path(cwd: str | None) -> str | None:
    """The directory an agent declares for its session wins over a (possibly temporary) hook cwd."""
    declared = (os.environ.get("CLAUDE_PROJECT_DIR") or "").strip()
    if declared:
        return declared
    return cwd if cwd and str(cwd).strip() else None


def find_store_root(cwd: str | None) -> Path | None:
    """The repository whose ``.cairn/sessions.db`` records this cwd: the nearest folder with
    ``.cairn/``, else the main checkout of a linked worktree when that has one."""
    try:
        here = Path(expand_home(str(cwd or os.getcwd()))).resolve()
    except (OSError, RuntimeError):
        return None
    def has_store(p: Path) -> bool:
        return (p / ".cairn" / "sessions.db").exists() or (p / ".cairn" / "brain.db").exists()

    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None
    folder = None  # nearest .cairn/ without a store yet (e.g. only the committed config.toml)
    root = git_root(here)
    for p in (here, *here.parents):
        if p == home:  # ~/.cairn is Cairn's own store, never a repository's: stop before it
            break
        if has_store(p):
            return p
        if folder is None and (p / ".cairn").is_dir():
            folder = p
        if p == root:  # stores in folders above the repository belong to other projects
            break
    if root is not None:  # a linked worktree records with its main checkout's store, not a fresh one of its own
        wt = detect_worktree(root)
        parent = Path(wt.parent_repo_path) if wt.is_worktree and wt.parent_repo_path else None
        if parent and has_store(parent):
            return parent
    return folder


# ---- exclusion globs -------------------------------------------------------------------------------
def _glob_to_regex(pattern: str) -> re.Pattern:
    expanded = expand_home(pattern.replace("\\", "/")).replace("\\", "/")
    rx = re.sub(r"[.+^${}()|[\]\\]", lambda m: "\\" + m.group(0), expanded)
    rx = rx.replace("**", "\0").replace("*", "[^/]*").replace("?", "[^/]").replace("\0", ".*")
    return re.compile(f"^{rx}$")


def matches_any_glob(folder: str, patterns: list[str]) -> bool:
    norm = folder.replace("\\", "/")
    base = norm.rstrip("/").rsplit("/", 1)[-1]
    for raw in patterns:
        pat = raw.strip()
        if not pat:
            continue
        try:
            rx = _glob_to_regex(pat)
        except re.error:
            continue
        if rx.match(norm) or rx.match(base):
            return True
    return False


def is_project_excluded(project_path: str, patterns_csv: str) -> bool:
    if not patterns_csv or not patterns_csv.strip():
        return False
    return matches_any_glob(project_path, [p for p in patterns_csv.split(",") if p.strip()])


def should_track(cwd: str | None, settings: dict) -> bool:
    """Whether work in ``cwd`` is recorded at all."""
    if os.environ.get("CAIRN_INTERNAL"):
        return False  # Cairn's own model calls are not agent work
    if not settings.get("capture", True):
        return False
    if not cwd:
        return True
    tmp = os.path.basename(str(cwd))
    if tmp.startswith("cairn-model-"):
        return False
    return not is_project_excluded(str(cwd), str(settings.get("excluded_projects") or ""))


def is_direct_child(file_path: str, folder: str) -> bool:
    """``file_path`` sits directly inside ``folder`` (not deeper)."""
    f = file_path.replace("\\", "/").rstrip("/")
    d = folder.replace("\\", "/").rstrip("/")
    if not d:
        return "/" not in f
    if f.startswith(d + "/"):
        return "/" not in f[len(d) + 1:]
    # tolerate absolute/relative mismatch: compare by suffix
    idx = f.find("/" + d + "/")
    return idx != -1 and "/" not in f[idx + len(d) + 2:]

"""Merged-worktree adoption: fold a merged git worktree's records into its parent project.

Work done in a linked worktree is recorded under the project ``<parent>/<worktree>``. Once the
worktree's branch is merged into the parent's main line, its observations and session summaries are
tagged ``merged_into_project = <parent>`` so the parent's context, search and timeline include them.
The row's own ``project`` is never rewritten: it stays the provenance, and the tag is a pointer, not a
data move.

Merges are detected from git: ``git worktree list --porcelain`` for the worktrees, then whether each
worktree's HEAD is an ancestor of the parent's HEAD / origin/HEAD / origin/main / origin/master. A
worktree checked out detached at the parent's exact tip is a fresh inspection checkout, not merged
work. ``only_branch`` adopts one branch regardless of ancestry (for squash merges, which leave no
ancestry behind). Records live in the parent repository's ``.cairn/sessions.db``; the matching rows of
the local vector index metadata (``vector_docs``) are re-tagged too, and the adopted ids are returned
so the caller can re-index them. Standard library only.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import schema
from .projects import find_store_root, project_context
from .store import Store

log = logging.getLogger("cairn.recall")

GIT_TIMEOUT_S = 15
CANDIDATE_REFS = ("HEAD", "origin/HEAD", "origin/main", "origin/master")
USAGE = "Usage: adopt [--dry-run] [--branch <branch>] [--cwd <path>]"


@dataclass
class WorktreeEntry:
    path: str
    branch: str | None
    head: str | None


@dataclass
class GitResult:
    status: int | None
    stdout: str
    error: Exception | None


class _DryRunRollback(Exception):
    """Raised inside the adoption transaction so a dry run's writes are rolled back."""


def format_adoption_errors(errors: list[dict]) -> str:
    """Per-worktree errors as one log-friendly line: ``<worktree>: <error>; ...``."""
    return "; ".join(f"{e['worktree']}: {e['error']}" for e in errors)


# ---- git -------------------------------------------------------------------------------------------
def git_run(cwd: str | os.PathLike, args: list[str]) -> GitResult:
    start = time.monotonic()
    extra: dict[str, Any] = {}
    if os.name == "nt":
        extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cmd = ["git", "-C", str(cwd), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=GIT_TIMEOUT_S, check=False, **extra)
    except subprocess.TimeoutExpired as exc:
        log.warning("git operation failed: %s (timed out)", " ".join(cmd))
        return GitResult(None, "", exc)
    except OSError as exc:
        log.warning("git operation failed: %s (%s)", " ".join(cmd), exc)
        return GitResult(None, "", exc)
    duration = time.monotonic() - start
    if duration > 1:
        log.debug("slow git operation: %s took %dms", " ".join(cmd), int(duration * 1000))
    if r.returncode != 0:
        log.debug("git returned non-zero exit code %s: %s (%s)", r.returncode, " ".join(cmd), (r.stderr or "").strip())
        return GitResult(r.returncode, "", None)
    return GitResult(0, (r.stdout or "").strip(), None)


def git_capture(cwd: str | os.PathLike, args: list[str]) -> str | None:
    result = git_run(cwd, args)
    return result.stdout if result.status == 0 else None


def resolve_main_repo_path(cwd: str | os.PathLike) -> str | None:
    """The main checkout of the repository containing ``cwd`` (also from inside a linked worktree)."""
    common = git_capture(cwd, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    if not common:
        return None
    main_root = os.path.dirname(common) if common.endswith("/.git") else re.sub(r"\.git$", "", common)
    return main_root if os.path.exists(main_root) else None


def list_worktrees(main_repo: str) -> list[WorktreeEntry]:
    raw = git_capture(main_repo, ["worktree", "list", "--porcelain"])
    if not raw:
        return []
    entries: list[WorktreeEntry] = []
    current: dict[str, Any] = {}

    def push() -> None:
        entries.append(WorktreeEntry(current["path"], current.get("branch"), current.get("head")))

    for line in raw.split("\n"):
        if line.startswith("worktree "):
            if current.get("path"):
                push()
            current = {"path": line[len("worktree "):].strip(), "branch": None, "head": None}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip() or None
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            current["branch"] = ref.removeprefix("refs/heads/")
        elif line == "" and current.get("path"):
            push()
            current = {}
    if current.get("path"):
        push()
    return entries


def resolve_candidate_oids(main_repo: str) -> list[str]:
    """Commits a merged branch must be an ancestor of (the parent's HEAD and its usual remote heads)."""
    oids: list[str] = []
    for ref in CANDIDATE_REFS:
        result = git_run(main_repo, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
        if result.status == 0 and result.stdout and result.stdout not in oids:
            oids.append(result.stdout)
    return oids


def has_proven_ancestry(main_repo: str, worktree_head: str, candidate_oids: list[str]) -> bool:
    """True when ``worktree_head`` is an ancestor of one of the candidates. Any other outcome (not an
    ancestor, git failing to run) leaves the worktree unselected."""
    for oid in candidate_oids:
        result = git_run(main_repo, ["merge-base", "--is-ancestor", worktree_head, oid])
        if result.status == 0 and result.error is None:
            return True
    return False


# ---- adoption --------------------------------------------------------------------------------------
def _new_result(repo_path: str, parent_project: str, dry_run: bool) -> dict:
    return {"repo_path": repo_path, "parent_project": parent_project, "scanned_worktrees": 0, "merged_branches": [],
            "adopted_observations": 0, "adopted_summaries": 0, "vector_updates": 0, "vector_failed": 0,
            "dry_run": dry_run, "errors": [], "adopted_observation_ids": [], "adopted_summary_ids": []}


def _has_column(db: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1] == column for r in db.execute(f"PRAGMA table_info({table})"))


def _adopt_worktree(db: sqlite3.Connection, wt: WorktreeEntry, parent_project: str, result: dict) -> None:
    worktree_project = project_context(wt.path).primary
    select = ("SELECT id FROM {t} WHERE project = ? AND (merged_into_project IS NULL OR merged_into_project = ?)"
              " ORDER BY id")
    obs_ids = [r[0] for r in db.execute(select.format(t="observations"), (worktree_project, parent_project))]
    sum_ids = [r[0] for r in db.execute(select.format(t="session_summaries"), (worktree_project, parent_project))]
    update = "UPDATE {t} SET merged_into_project = ? WHERE project = ? AND merged_into_project IS NULL"
    obs_changes = db.execute(update.format(t="observations"), (parent_project, worktree_project)).rowcount
    sum_changes = db.execute(update.format(t="session_summaries"), (parent_project, worktree_project)).rowcount
    result["adopted_observation_ids"].extend(obs_ids)
    result["adopted_summary_ids"].extend(sum_ids)
    result["adopted_observations"] += obs_changes
    result["adopted_summaries"] += sum_changes


def _patch_vector_docs(store: Store, obs_ids: list[int], sum_ids: list[int], parent_project: str) -> int:
    """Re-tag the vector index metadata rows of the adopted records; returns the rows changed."""
    db = store.db
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vector_docs'").fetchone():
        return 0
    changed = 0
    with store.tx():
        for doc_type, ids in (("observation", obs_ids), ("session_summary", sum_ids)):
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                marks = ",".join("?" * len(chunk))
                changed += db.execute(
                    f"UPDATE vector_docs SET merged_into_project = ? WHERE doc_type = ? AND sqlite_id IN ({marks})",
                    (parent_project, doc_type, *chunk)).rowcount
    return changed


def _store_root(main_repo: str, start_cwd: str, store_root: str | os.PathLike | None) -> Path | None:
    if store_root is not None:
        root = Path(store_root)
        return root if schema.store_path(root).exists() else None
    for candidate in (Path(main_repo), find_store_root(start_cwd)):
        if candidate is not None and schema.store_path(candidate).exists():
            return candidate
    return None


def adopt_merged_worktrees(repo_path: str | os.PathLike | None = None, dry_run: bool = False,
                           only_branch: str | None = None, *, store_root: str | os.PathLike | None = None) -> dict:
    """Tag the records of every merged worktree of ``repo_path``'s repository with the parent project.

    ``dry_run`` reports what would change and changes nothing; ``only_branch`` adopts that branch's
    worktree whether or not git can prove the merge. ``store_root`` names the folder holding
    ``.cairn/sessions.db`` (default: the main checkout). Returns the counters, the merged branches,
    per-worktree ``errors`` and the adopted observation / summary ids (for re-indexing vectors)."""
    start_cwd = str(repo_path) if repo_path is not None else os.getcwd()
    main_repo = resolve_main_repo_path(start_cwd)
    parent_project = project_context(main_repo).primary if main_repo else ""
    result = _new_result(main_repo or start_cwd, parent_project, dry_run)
    if not main_repo:
        log.debug("worktree adoption skipped (not a git repo): %s", start_cwd)
        return result

    root = _store_root(main_repo, start_cwd, store_root)
    if root is None:
        log.debug("worktree adoption skipped (no session store yet) for %s", main_repo)
        return result

    children = [w for w in list_worktrees(main_repo) if w.path != main_repo]
    result["scanned_worktrees"] = len(children)
    if not children:
        return result

    if only_branch:
        targets = [w for w in children if w.branch == only_branch]
    else:
        oids = resolve_candidate_oids(main_repo)
        # A branch at the parent's tip is a valid worktree; a detached checkout of the exact tip is a fresh
        # inspection worktree, not merged work.
        targets = [w for w in children if w.head is not None and (w.branch is not None or w.head not in oids)
                   and has_proven_ancestry(main_repo, w.head, oids)]
    result["merged_branches"] = [t.branch for t in targets if t.branch is not None]
    if not targets:
        return result

    store = Store.open(root)
    try:
        db = store.db
        if not (_has_column(db, "observations", "merged_into_project")
                and _has_column(db, "session_summaries", "merged_into_project")):
            log.debug("worktree adoption skipped (merged_into_project column missing)")
            return result
        try:
            with store.tx():
                for wt in targets:
                    try:
                        _adopt_worktree(db, wt, parent_project, result)
                    except Exception as exc:  # one worktree failing must not stop the others
                        log.warning("worktree adoption skipped branch %s (%s): %s", wt.branch, wt.path, exc)
                        result["errors"].append({"worktree": wt.path, "error": str(exc)})
                if dry_run:
                    raise _DryRunRollback()
        except _DryRunRollback:
            pass  # rolled back as intended; the counts still say what would change

        adopted = len(result["adopted_observation_ids"]) + len(result["adopted_summary_ids"])
        if not dry_run and adopted:
            try:
                result["vector_updates"] = _patch_vector_docs(store, result["adopted_observation_ids"],
                                                              result["adopted_summary_ids"], parent_project)
            except sqlite3.Error as exc:
                log.error("worktree adoption vector metadata patch failed (records already committed): %s", exc)
                result["vector_failed"] = adopted
    finally:
        store.close()

    if result["adopted_observations"] or result["adopted_summaries"] or result["vector_updates"] or result["errors"]:
        log.info("worktree adoption applied: parent=%s dry_run=%s scanned=%d branches=%s observations=%d "
                 "summaries=%d vector_updates=%d vector_failed=%d errors=%d", parent_project, dry_run,
                 result["scanned_worktrees"], ",".join(result["merged_branches"]), result["adopted_observations"],
                 result["adopted_summaries"], result["vector_updates"], result["vector_failed"], len(result["errors"]))
    return result


def adopt_merged_worktrees_for_all_known_repos(store_root: str | os.PathLike, dry_run: bool = False) -> list[dict]:
    """Adopt merged worktrees for every repository this store has recorded work in (run at worker start).

    The repositories are the main checkouts of the store's own folder and of every working directory
    seen in its queue and sessions; a failure for one repository is logged and the rest continue."""
    root = Path(store_root)
    path = schema.store_path(root)
    results: list[dict] = []
    if not path.exists():
        log.debug("worktree adoption skipped (no session store yet): %s", path)
        return results
    cwds: list[str] = [str(root)]
    try:
        with Store.at(path, readonly=True) as store:
            db = store.db
            for table in ("pending_messages", "sdk_sessions"):
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    continue
                cwds += [r[0] for r in db.execute(
                    f"SELECT cwd FROM {table} WHERE cwd IS NOT NULL AND cwd != '' GROUP BY cwd")]
    except sqlite3.Error as exc:
        log.warning("worktree adoption could not read known working directories: %s", exc)
    parents: list[str] = []
    for cwd in cwds:
        if not os.path.isdir(cwd):
            continue
        main = resolve_main_repo_path(cwd)
        if main and main not in parents:
            parents.append(main)
    if not parents:
        log.debug("worktree adoption found no known parent repositories")
        return results
    for repo in parents:
        try:
            results.append(adopt_merged_worktrees(repo, dry_run=dry_run, store_root=root))
        except Exception as exc:
            log.warning("worktree adoption failed for %s (continuing): %s", repo, exc)
    return results


# ---- CLI -------------------------------------------------------------------------------------------
def _flag_value(argv: list[str], flag: str) -> tuple[bool, str | None]:
    """(present, value) for ``flag <value>``; present with a None value means the value is missing."""
    if flag not in argv:
        return False, None
    i = argv.index(flag)
    value = argv[i + 1] if i + 1 < len(argv) else None
    if value is None or value.startswith("--"):
        return True, None
    return True, value


def cli_adopt(argv: list[str]) -> int:
    """``adopt [--dry-run] [--branch <branch>] [--cwd <path>]``: adopt merged worktrees and print a report."""
    dry_run = "--dry-run" in argv
    has_branch, branch = _flag_value(argv, "--branch")
    has_cwd, cwd = _flag_value(argv, "--cwd")
    if (has_branch and branch is None) or (has_cwd and cwd is None):
        print(USAGE, file=sys.stderr)
        return 1
    result = adopt_merged_worktrees(cwd or os.getcwd(), dry_run=dry_run, only_branch=branch)
    tag = "(dry-run)" if result["dry_run"] else "(applied)"
    print(f"\nWorktree adoption {tag}")
    print(f"  Parent project:       {result['parent_project'] or '(unknown)'}")
    print(f"  Repo:                 {result['repo_path']}")
    print(f"  Worktrees scanned:    {result['scanned_worktrees']}")
    print(f"  Merged branches:      {', '.join(result['merged_branches']) or '(none)'}")
    print(f"  Observations adopted: {result['adopted_observations']}")
    print(f"  Summaries adopted:    {result['adopted_summaries']}")
    print(f"  Vector docs updated:  {result['vector_updates']}")
    if result["vector_failed"] > 0:
        print(f"  Vector sync failures: {result['vector_failed']} (will retry on next run)")
    for err in result["errors"]:
        print(f"  ! {err['worktree']}: {err['error']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli_adopt(sys.argv[1:]))

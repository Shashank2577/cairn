"""History ingest vs git rewrites: filestats/cochange are fully derived from git log, so a rewritten
cursor (amend/rebase) must rebuild them from scratch — never increment on top of the stale counts."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cairn.engines import history
from cairn.project import Project
from cairn.store import Brain


@pytest.fixture()
def brain(repo):
    """Project + read model on the fixture repo; history.ingest is driven directly, no full sync."""
    project = Project(root=repo)
    project.ensure_dir()
    db = Brain(project.db_path)
    yield project, db
    db.close()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def true_counts(repo: Path) -> dict[str, int]:
    """Per-file commit counts read straight from git log — what filestats must equal after any sync."""
    counts: dict[str, int] = {}
    for line in git(repo, "log", "--no-merges", "--numstat", "--format=").splitlines():
        adds, _, rest = line.partition("\t")
        _dels, _, path = rest.partition("\t")
        if path and (adds.isdigit() or adds == "-"):
            counts[path] = counts.get(path, 0) + 1
    return counts


def filestats(brain: Brain) -> dict[str, int]:
    return {r["path"]: r["commits"] for r in brain.q("SELECT path, commits FROM filestats")}


def amend_head(repo: Path) -> None:
    # a fixed committer date guarantees the amended sha differs from the original, even in the same second
    env = {**os.environ, "GIT_COMMITTER_DATE": "2020-01-01T00:00:00+00:00"}
    subprocess.run(["git", "-C", str(repo), "commit", "--amend", "--no-edit"], check=True,
                   capture_output=True, text=True, env=env)


def test_fresh_ingest_matches_git_log_counts(brain, repo):
    project, db = brain
    assert history.ingest(project, db)["commits"] == 5
    assert filestats(db) == true_counts(repo)


def test_amend_rebuilds_counts_instead_of_doubling(brain, repo):
    project, db = brain
    history.ingest(project, db)
    before = filestats(db)
    amend_head(repo)
    assert history.ingest(project, db)["commits"] == 5        # the rewritten cursor forces a full re-read
    assert filestats(db) == before == true_counts(repo)       # rebuilt exactly, not doubled
    row = db.one("SELECT count FROM cochange WHERE a='shop/api.py' AND b='shop/payments.py'")
    assert row and row["count"] == 1                          # cochange rebuilt too, not 2


def test_amend_leaves_no_orphaned_commit_rows(brain, repo):
    """Events/entities for commits the rewrite removed must be dropped, not left to linger forever."""
    project, db = brain
    history.ingest(project, db)
    old_ids = {r["id"] for r in db.q("SELECT id FROM events WHERE id LIKE 'commit:%'")}
    assert len(old_ids) == 5
    amend_head(repo)
    assert history.ingest(project, db)["commits"] == 5        # the rewritten cursor forces a full re-read
    reachable = {f"commit:{sha}" for sha in git(repo, "log", "--no-merges", "--format=%H").split()}
    event_ids = {r["id"] for r in db.q("SELECT id FROM events WHERE id LIKE 'commit:%'")}
    entity_ids = {r["id"] for r in db.q("SELECT id FROM entities WHERE kind='commit'")}
    assert len(event_ids) == len(reachable)                   # no orphaned commit events
    assert event_ids == reachable == entity_ids               # exact rebuild in both tables
    assert len(old_ids - event_ids) == 1                      # the amended-away sha is gone


def test_resync_without_changes_keeps_counts(brain):
    project, db = brain
    history.ingest(project, db)
    before = filestats(db)
    assert history.ingest(project, db)["commits"] == 0
    assert filestats(db) == before


def test_new_commit_after_rewrite_increments_from_rebuilt_baseline(brain, repo):
    project, db = brain
    history.ingest(project, db)
    amend_head(repo)
    history.ingest(project, db)
    (repo / "shop" / "ledger.py").write_text("LEDGER = {}\n")  # cursor is an ancestor again: incremental path
    git(repo, "add", "shop/ledger.py")
    git(repo, "commit", "-qm", "Add ledger")
    assert history.ingest(project, db)["commits"] == 1
    assert filestats(db) == true_counts(repo)

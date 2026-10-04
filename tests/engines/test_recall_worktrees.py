"""Merged-worktree adoption against a real temporary git repository with linked worktrees."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cairn.engines.recall import worktrees as wt
from cairn.engines.recall.store import Store


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@example.com", "-c", "user.name=Test",
                    "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}", *args],
                   check=True, capture_output=True)


def commit(cwd: Path, name: str) -> None:
    (cwd / name).write_text(name + "\n", encoding="utf-8")
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", f"add {name}")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """parent-repo (main) with worktrees: wt-merged (feature, merged), wt-open (wip, not merged),
    wt-tip (branch at the parent's tip) and wt-inspect (detached at the parent's tip)."""
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    base = Path(os.path.realpath(tmp_path))
    main = base / "parent-repo"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    commit(main, "README.md")
    merged, open_, tip, inspect = (base / n for n in ("wt-merged", "wt-open", "wt-tip", "wt-inspect"))
    git(main, "worktree", "add", "-q", "-b", "feature", str(merged))
    commit(merged, "feature.txt")
    git(main, "merge", "-q", "--no-ff", "-m", "merge feature", "feature")
    git(main, "worktree", "add", "-q", "-b", "wip", str(open_))
    commit(open_, "wip.txt")
    git(main, "worktree", "add", "-q", "-b", "tip", str(tip))
    git(main, "worktree", "add", "-q", "--detach", str(inspect))
    return {"base": base, "main": main, "merged": merged, "open": open_, "tip": tip, "inspect": inspect}


def seed(root: Path, project: str, n: int = 2, summary: bool = True, cwd: str | None = None) -> dict:
    with Store.open(root) as store:
        sid = store.create_sdk_session(f"session-{project}", project, "prompt", cwd=cwd)
        mid = store.ensure_memory_session_id(sid)
        return store.store_observations(mid, project, [{"title": f"{project} #{i}", "narrative": f"n{i}"}
                                                       for i in range(n)],
                                        summary={"request": f"{project} request"} if summary else None)


def merged_into(root: Path, table: str, project: str) -> list:
    with Store.open(root) as store:
        return [r[0] for r in store.db.execute(f"SELECT merged_into_project FROM {table} WHERE project=? ORDER BY id",
                                               (project,))]


@pytest.fixture
def seeded(repo):
    main = repo["main"]
    return {
        "merged": seed(main, "parent-repo/wt-merged"),
        "open": seed(main, "parent-repo/wt-open", n=1),
        "tip": seed(main, "parent-repo/wt-tip", n=1, summary=False),
        "inspect": seed(main, "parent-repo/wt-inspect", n=1, summary=False),
        "parent": seed(main, "parent-repo", n=1),
    }


def test_git_helpers(repo):
    main = repo["main"].as_posix()
    assert wt.resolve_main_repo_path(repo["open"]) == main
    assert wt.resolve_main_repo_path(main) == main
    assert wt.resolve_main_repo_path(repo["base"]) is None
    entries = {e.path: e for e in wt.list_worktrees(main)}
    assert set(entries) == {main, *(repo[k].as_posix() for k in ("merged", "open", "tip", "inspect"))}
    assert entries[repo["merged"].as_posix()].branch == "feature"
    assert entries[repo["inspect"].as_posix()].branch is None and entries[repo["inspect"].as_posix()].head
    oids = wt.resolve_candidate_oids(main)
    assert len(oids) == 1  # HEAD only: there is no remote
    assert wt.has_proven_ancestry(main, entries[repo["merged"].as_posix()].head, oids)
    assert not wt.has_proven_ancestry(main, entries[repo["open"].as_posix()].head, oids)


def test_adopts_merged_worktrees(repo, seeded):
    main = repo["main"]
    res = wt.adopt_merged_worktrees(main)
    assert res["repo_path"] == main.as_posix() and res["parent_project"] == "parent-repo"
    assert res["scanned_worktrees"] == 4
    assert sorted(res["merged_branches"]) == ["feature", "tip"]  # the detached tip checkout is not merged work
    assert (res["adopted_observations"], res["adopted_summaries"]) == (3, 1)
    assert res["adopted_observation_ids"] == sorted(seeded["merged"]["observation_ids"] +
                                                    seeded["tip"]["observation_ids"])
    assert res["adopted_summary_ids"] == [seeded["merged"]["summary_id"]]
    assert (res["vector_updates"], res["vector_failed"], res["errors"], res["dry_run"]) == (0, 0, [], False)

    assert merged_into(main, "observations", "parent-repo/wt-merged") == ["parent-repo", "parent-repo"]
    assert merged_into(main, "session_summaries", "parent-repo/wt-merged") == ["parent-repo"]
    assert merged_into(main, "observations", "parent-repo/wt-tip") == ["parent-repo"]
    for project in ("parent-repo/wt-open", "parent-repo/wt-inspect", "parent-repo"):
        assert merged_into(main, "observations", project) == [None]
    # provenance is never rewritten, and the parent's reads now include the adopted records
    with Store.open(main) as store:
        ids = res["adopted_observation_ids"]
        assert {o["project"] for o in store.get_observations_by_ids(ids)} == {"parent-repo/wt-merged",
                                                                             "parent-repo/wt-tip"}
        assert len(store.get_observations_by_ids(ids, project="parent-repo")) == 3


def test_adoption_is_idempotent(repo, seeded):
    wt.adopt_merged_worktrees(repo["main"])
    again = wt.adopt_merged_worktrees(repo["main"])
    assert (again["adopted_observations"], again["adopted_summaries"]) == (0, 0)
    # already-adopted rows are still reported so their vectors can be re-tagged / re-indexed
    assert len(again["adopted_observation_ids"]) == 3


def test_dry_run_changes_nothing(repo, seeded):
    main = repo["main"]
    res = wt.adopt_merged_worktrees(main, dry_run=True)
    assert res["dry_run"] is True
    assert (res["adopted_observations"], res["adopted_summaries"]) == (3, 1)
    assert merged_into(main, "observations", "parent-repo/wt-merged") == [None, None]
    assert merged_into(main, "session_summaries", "parent-repo/wt-merged") == [None]


def test_only_branch_adopts_regardless_of_ancestry(repo, seeded):
    main = repo["main"]
    res = wt.adopt_merged_worktrees(main, only_branch="wip")
    assert res["merged_branches"] == ["wip"]
    assert (res["adopted_observations"], res["adopted_summaries"]) == (1, 1)
    assert merged_into(main, "observations", "parent-repo/wt-open") == ["parent-repo"]
    assert merged_into(main, "observations", "parent-repo/wt-merged") == [None, None]
    none = wt.adopt_merged_worktrees(main, only_branch="no-such-branch")
    assert none["merged_branches"] == [] and none["adopted_observations"] == 0


def test_runs_from_inside_a_worktree(repo, seeded):
    res = wt.adopt_merged_worktrees(repo["open"])
    assert res["repo_path"] == repo["main"].as_posix() and res["adopted_observations"] == 3


def test_vector_metadata_is_retagged(repo, seeded):
    main = repo["main"]
    obs_id = seeded["merged"]["observation_ids"][0]
    sum_id = seeded["merged"]["summary_id"]
    other = seeded["open"]["observation_ids"][0]
    with Store.open(main) as store:
        for doc_id, doc_type, sqlite_id, project in (("o1", "observation", obs_id, "parent-repo/wt-merged"),
                                                     ("s1", "session_summary", sum_id, "parent-repo/wt-merged"),
                                                     ("o2", "observation", other, "parent-repo/wt-open")):
            store.db.execute("INSERT INTO vector_docs(doc_id, doc_type, sqlite_id, project) VALUES(?,?,?,?)",
                             (doc_id, doc_type, sqlite_id, project))
    res = wt.adopt_merged_worktrees(main)
    assert res["vector_updates"] == 2
    with Store.open(main) as store:
        rows = dict(store.db.execute("SELECT doc_id, merged_into_project FROM vector_docs").fetchall())
    assert rows == {"o1": "parent-repo", "s1": "parent-repo", "o2": None}


def test_dry_run_leaves_vector_metadata(repo, seeded):
    main = repo["main"]
    with Store.open(main) as store:
        store.db.execute("INSERT INTO vector_docs(doc_id, doc_type, sqlite_id, project) VALUES('o1','observation',?,?)",
                         (seeded["merged"]["observation_ids"][0], "parent-repo/wt-merged"))
    res = wt.adopt_merged_worktrees(main, dry_run=True)
    assert res["vector_updates"] == 0
    with Store.open(main) as store:
        assert store.db.execute("SELECT merged_into_project FROM vector_docs").fetchone()[0] is None


def test_per_worktree_errors_are_collected(repo, seeded, monkeypatch):
    real = wt.project_context

    def flaky(path):
        if str(path).endswith("wt-merged"):
            raise RuntimeError("database is locked")
        return real(path)

    monkeypatch.setattr(wt, "project_context", flaky)
    res = wt.adopt_merged_worktrees(repo["main"])
    assert res["errors"] == [{"worktree": repo["merged"].as_posix(), "error": "database is locked"}]
    assert (res["adopted_observations"], res["adopted_summaries"]) == (1, 0)  # wt-tip still adopted
    assert merged_into(repo["main"], "observations", "parent-repo/wt-tip") == ["parent-repo"]
    assert wt.format_adoption_errors(res["errors"]) == f"{repo['merged'].as_posix()}: database is locked"


def test_not_a_repository_or_no_store(tmp_path, repo):
    plain = Path(os.path.realpath(tmp_path)) / "plain"
    plain.mkdir()
    res = wt.adopt_merged_worktrees(plain)
    assert (res["repo_path"], res["parent_project"], res["scanned_worktrees"]) == (str(plain), "", 0)
    # a repository with no session store yet: nothing is scanned or written
    res = wt.adopt_merged_worktrees(repo["main"])
    assert res["parent_project"] == "parent-repo" and res["scanned_worktrees"] == 0
    assert not (repo["main"] / ".cairn").exists()


def test_explicit_store_root(repo, tmp_path):
    other = Path(os.path.realpath(tmp_path)) / "elsewhere"
    seed(other, "parent-repo/wt-merged")
    res = wt.adopt_merged_worktrees(repo["main"], store_root=other)
    assert res["adopted_observations"] == 2
    assert merged_into(other, "observations", "parent-repo/wt-merged") == ["parent-repo", "parent-repo"]


def test_format_adoption_errors():
    assert wt.format_adoption_errors([
        {"worktree": "/repos/app/.worktrees/feat-x", "error": "database is locked"},
        {"worktree": "/repos/app/.worktrees/feat-y", "error": "no such table: observations"},
    ]) == "/repos/app/.worktrees/feat-x: database is locked; /repos/app/.worktrees/feat-y: no such table: observations"
    assert f"errors={wt.format_adoption_errors([{'worktree': '/w', 'error': 'boom'}])}" == "errors=/w: boom"
    assert wt.format_adoption_errors([]) == ""


def test_all_known_repositories(repo, seeded):
    main = repo["main"]
    seed(main, "parent-repo/wt-open", n=1, cwd=str(repo["open"]))
    results = wt.adopt_merged_worktrees_for_all_known_repos(main)
    assert len(results) == 1 and results[0]["repo_path"] == main.as_posix()
    assert results[0]["adopted_observations"] == 3
    assert wt.adopt_merged_worktrees_for_all_known_repos(repo["base"] / "missing") == []


def test_cli_adopt(repo, seeded, capsys, monkeypatch):
    main = repo["main"]
    assert wt.cli_adopt(["--dry-run", "--cwd", str(main)]) == 0
    out = capsys.readouterr().out
    assert "Worktree adoption (dry-run)" in out
    assert "  Parent project:       parent-repo" in out
    assert f"  Repo:                 {main.as_posix()}" in out
    assert "  Worktrees scanned:    4" in out
    assert "  Observations adopted: 3" in out and "  Summaries adopted:    1" in out
    assert "  Vector docs updated:  0" in out
    assert merged_into(main, "observations", "parent-repo/wt-merged") == [None, None]

    monkeypatch.chdir(main)
    assert wt.cli_adopt(["--branch", "wip"]) == 0
    out = capsys.readouterr().out
    assert "Worktree adoption (applied)" in out and "  Merged branches:      wip" in out
    assert merged_into(main, "observations", "parent-repo/wt-open") == ["parent-repo"]

    for bad in (["--branch"], ["--branch", "--dry-run"], ["--cwd"], ["--cwd", "--dry-run"]):
        assert wt.cli_adopt(bad) == 1
        assert capsys.readouterr().err.strip() == wt.USAGE


def test_cli_reports_errors(repo, seeded, capsys, monkeypatch):
    real = wt.project_context

    def flaky(path):
        if str(path).endswith("wt-tip"):
            raise RuntimeError("boom")
        return real(path)

    monkeypatch.setattr(wt, "project_context", flaky)
    assert wt.cli_adopt(["--cwd", str(repo["main"])]) == 0
    out = capsys.readouterr().out
    assert f"  ! {repo['tip'].as_posix()}: boom" in out
    assert "  Observations adopted: 2" in out

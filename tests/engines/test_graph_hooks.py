"""Graph engine: in-process rebuilds on git events, the graph.json merge driver, the
global multi-repo graph, merge-graphs, watch helpers and the PR dashboard plumbing."""
from __future__ import annotations

import json
import subprocess

from test_graph_support import (  # noqa: F401  (fixtures)
    FILES, git, isolated_env, load_graph, make_repo, node_ids, write,
)


def _commit(root, files, msg):
    write(root, files)
    git(root, "add", "-A")
    git(root, "commit", "-qm", msg)


def test_post_commit_rebuilds_only_changed_files(tmp_path):
    from cairn.engines.graph import api, hooks
    root = make_repo(tmp_path / "repo")
    assert api.build(root)["ok"]
    _commit(root, {"shop/audit.py": "def audit(x):\n    return x\n"}, "add audit")
    res = hooks.post_commit(root)
    assert res["status"] == "rebuilt" and res["changed"] == 1, res  # graph output never counts
    assert "shop_audit_audit" in node_ids(root)


def test_post_commit_skips(tmp_path, monkeypatch):
    from cairn.engines.graph import hooks
    root = make_repo(tmp_path / "repo")
    assert hooks.post_commit(tmp_path / "not-a-repo")["status"] == "skipped"
    assert hooks.post_commit(root, changed=[".cairn/graph/graph.json"])["reason"] == "only graph output changed"
    monkeypatch.setenv("CAIRN_GRAPH_SKIP_HOOK", "1")
    assert hooks.post_commit(root)["reason"] == "CAIRN_GRAPH_SKIP_HOOK=1"
    monkeypatch.delenv("CAIRN_GRAPH_SKIP_HOOK")
    (root / ".git" / "MERGE_HEAD").write_text("0" * 40)
    assert hooks.post_commit(root)["reason"] == "merge in progress"


def test_post_checkout(tmp_path):
    from cairn.engines.graph import api, hooks
    root = make_repo(tmp_path / "repo")
    assert hooks.post_checkout(root, "a", "b", "1")["reason"] == "graph not built yet"
    assert api.build(root)["ok"]
    assert hooks.post_checkout(root, "a", "b", "0")["reason"] == "file checkout"
    assert hooks.post_checkout(root, "a", "a", "1")["reason"] == "no-op checkout"
    git(root, "checkout", "-q", "-b", "feature")
    _commit(root, {"web/tax.ts": "export function tax(x: number): number { return x * 2; }\n"}, "tax")
    res = hooks.post_checkout(root, "prev", "new", "1")
    assert res["status"] == "rebuilt", res
    assert "web_tax_tax" in node_ids(root)


def test_linked_worktree_is_skipped(tmp_path):
    from cairn.engines.graph import hooks
    root = make_repo(tmp_path / "repo")
    wt = tmp_path / "wt"
    git(root, "worktree", "add", "-q", str(wt), "-b", "side")
    assert hooks.post_commit(wt)["reason"] == "linked worktree"


def test_merge_driver_install_status_uninstall(tmp_path):
    from cairn.engines.graph import hooks
    root = make_repo(tmp_path / "repo")
    assert "merge driver: not registered" in hooks.status(root)
    msg = hooks.install(root)
    assert "rebuild on commit/checkout: not installed" in msg and "registered" in msg
    attrs = (root / ".gitattributes").read_text()
    assert ".cairn/graph/graph.json merge=cairn-graph" in attrs
    driver = git(root, "config", "--get", "merge.cairn-graph.driver")
    assert "cairn.engines.graph merge-driver" in driver or "cairn graph merge-driver" in driver
    assert "merge driver: registered" in hooks.status(root)
    (root / ".git" / "hooks" / "post-commit").write_text("#!/bin/sh\ncairn hook git & # cairn-hook\n")
    assert "installed (Cairn git hooks)" in hooks.status(root)
    hooks.uninstall(root)
    assert not (root / ".gitattributes").exists()
    assert "merge driver: not registered" in hooks.status(root)


def test_merge_driver_unions_graphs(tmp_path):
    from cairn.engines.graph.cli import main
    def g(nodes, links):
        return {"directed": False, "multigraph": False, "graph": {}, "nodes": [{"id": n, "label": n} for n in nodes],
                "links": [{"source": a, "target": b, "relation": "calls", "confidence": "EXTRACTED"} for a, b in links]}
    base, cur, other = tmp_path / "base.json", tmp_path / "cur.json", tmp_path / "other.json"
    base.write_text(json.dumps(g(["a"], [])))
    cur.write_text(json.dumps(g(["a", "b"], [("a", "b")])))
    other.write_text(json.dumps(g(["a", "c"], [("a", "c")])))
    assert main(["merge-driver", str(base), str(cur), str(other)]) == 0
    merged = json.loads(cur.read_text())
    assert {n["id"] for n in merged["nodes"]} == {"a", "b", "c"}


def test_git_merge_uses_driver_end_to_end(tmp_path):
    """Two branches rebuild graph.json differently; git merges it with the driver."""
    import sys
    from cairn.engines.graph import api, hooks
    root = make_repo(tmp_path / "repo")
    assert api.build(root)["ok"]
    hooks.install(root)
    # commit only graph.json so git merges it (teams that share their graph)
    (root / ".gitignore").write_text(".cairn/\n")
    git(root, "add", "-f", ".cairn/graph/graph.json", ".gitattributes", ".gitignore")
    git(root, "commit", "-qm", "graph")
    git(root, "config", "merge.cairn-graph.driver",
        f'"{sys.executable}" -m cairn.engines.graph merge-driver %O %A %B')
    git(root, "checkout", "-q", "-b", "left")
    _commit(root, {"shop/left.py": "def left_fn():\n    return 1\n"}, "left")
    assert api.build(root)["ok"]
    git(root, "add", "-f", ".cairn/graph/graph.json")
    git(root, "commit", "-qm", "left graph")
    git(root, "checkout", "-q", "main")
    _commit(root, {"shop/right.py": "def right_fn():\n    return 1\n"}, "right")
    assert api.build(root)["ok"]
    git(root, "add", "-f", ".cairn/graph/graph.json")
    git(root, "commit", "-qm", "right graph")
    env_ok = subprocess.run(["git", "-C", str(root), "merge", "-q", "--no-edit", "left"], capture_output=True, text=True)
    assert env_ok.returncode == 0, env_ok.stderr
    ids = node_ids(root)
    assert "shop_left_left_fn" in ids and "shop_right_right_fn" in ids


def test_global_graph_lives_in_cairn_home(tmp_path):
    from cairn.engines.graph import api, global_graph
    a = make_repo(tmp_path / "alpha")
    b = make_repo(tmp_path / "beta", {"lib/util.py": "def helper():\n    return 1\n"})
    assert api.build(a)["ok"] and api.build(b)["ok"]
    for root in (a, b):
        res = api.run(["global", "add", str(api.graph_json(root))])
        assert res["code"] == 0, res
    gp = global_graph.global_path()
    assert gp == tmp_path / "cairn-home" / "graph" / "global-graph.json" and gp.exists()
    listing = api.run(["global", "list"])["stdout"]
    assert "alpha" in listing and "beta" in listing
    data = json.loads(gp.read_text())
    ids = {n["id"] for n in data["nodes"]}
    assert any(i.startswith("alpha::") for i in ids) and any(i.startswith("beta::") for i in ids)
    assert api.run(["global", "remove", "beta"])["code"] == 0
    assert "beta" not in api.run(["global", "list"])["stdout"]


def test_merge_graphs_prefixes_repos(tmp_path):
    from cairn.engines.graph import api
    a = make_repo(tmp_path / "alpha")
    b = make_repo(tmp_path / "beta")
    assert api.build(a)["ok"] and api.build(b)["ok"]
    out = tmp_path / "merged.json"
    res = api.run(["merge-graphs", str(api.graph_json(a)), str(api.graph_json(b)), "--out", str(out)])
    assert res["code"] == 0, res
    ids = {n["id"] for n in json.loads(out.read_text())["nodes"]}
    assert any(i.startswith("alpha::") for i in ids) and any(i.startswith("beta::") for i in ids)


def test_check_update_flag(tmp_path, capsys):
    from cairn.engines.graph.watch import check_update, _notify_only
    root = make_repo(tmp_path / "repo")
    assert check_update(root) is True  # cron-safe: always True, prints only when pending
    assert capsys.readouterr().out == ""
    _notify_only(root)
    assert (root / ".cairn" / "graph" / "needs_update").exists()
    capsys.readouterr()
    check_update(root)
    assert "cairn graph extract ." in capsys.readouterr().out


def test_ingest_url_is_ssrf_guarded(tmp_path):
    from cairn.engines.graph.security import validate_url
    import pytest
    for bad in ("file:///etc/passwd", "http://169.254.169.254/latest/meta-data", "ftp://x"):
        with pytest.raises(ValueError):
            validate_url(bad)


def test_prs_command_without_gh_is_graceful(tmp_path, monkeypatch):
    from cairn.engines.graph import api
    monkeypatch.setenv("PATH", str(tmp_path))  # no gh on PATH
    root = make_repo(tmp_path / "repo")
    assert api.build(root)["ok"]
    res = api.run(["prs", "--graph", str(api.graph_json(root))])
    assert res["code"] != 0 or "gh" in (res["stdout"] + res["stderr"]).lower()


def test_post_commit_with_explicit_output_dir(tmp_path):
    from cairn.engines.graph import api, hooks
    root = make_repo(tmp_path / "repo")
    out = tmp_path / "map"
    assert api.build(root, out=out)["ok"]
    _commit(root, {"shop/audit.py": "def audit(x):\n    return x\n"}, "add audit")
    assert hooks.post_commit(root, out=out)["status"] == "rebuilt"
    ids = {n["id"] for n in json.loads((out / "graph.json").read_text())["nodes"]}
    assert "shop_audit_audit" in ids and not (root / ".cairn" / "graph" / "graph.json").exists()

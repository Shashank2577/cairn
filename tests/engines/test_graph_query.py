"""Graph engine: query / path / explain / affected / hubs, the in-process tool set,
the CLI entry point and the map bridge Cairn's core uses."""
from __future__ import annotations

import json

from test_graph_support import built_repo, isolated_env, make_repo  # noqa: F401  (fixtures)


def test_query_returns_scoped_subgraph(built_repo):
    from cairn.engines.graph import api
    text = api.query(built_repo, "payment charge", budget=2000)
    assert text.startswith("Graph:")
    assert "charge()" in text and "NODE" in text


def test_path_and_explain(built_repo):
    from cairn.engines.graph import api
    p = api.path(built_repo, "checkout", "charge")
    assert "Shortest path" in p and "checkout()" in p and "charge()" in p
    e = api.explain(built_repo, "PaymentService")
    assert "Node: PaymentService" in e and "checkout()" in e


def test_affected_is_structured_and_textual(built_repo):
    from cairn.engines.graph import api
    res = api.affected(built_repo, "charge")
    assert res["seed"] == "shop_gateway_charge"
    hit_labels = {h["label"] for h in res["hits"]}
    assert {"refund()", ".process()"} <= hit_labels
    assert all(h["depth"] >= 1 and h["relation"] for h in res["hits"])
    assert "Affected nodes for charge()" in res["text"]
    assert api.affected(built_repo, "no_such_symbol_xyz")["seed"] is None


def test_god_nodes_and_stats(built_repo):
    from cairn.engines.graph import api
    hubs = api.god_nodes(built_repo, top=3)
    assert len(hubs) == 3 and {"id", "label", "degree"} <= set(hubs[0])
    s = api.stats(api.graph_json(built_repo))
    assert s["nodes"] > 10 and s["edges"] > 10 and s["communities"] >= 2
    assert "EXTRACTED" in s["confidence"]


def test_in_process_tools(built_repo):
    from cairn.engines.graph import api
    from cairn.engines.graph.serve import ToolError
    t = api.tools(built_repo)
    assert set(t.names()) >= {"query_graph", "get_node", "get_neighbors", "get_community", "god_nodes",
                              "graph_stats", "shortest_path", "list_prs", "get_pr_impact", "triage_prs"}
    schema = next(x for x in t.tools() if x["name"] == "query_graph")["input_schema"]
    assert "project_path" in schema["properties"] and schema["required"] == ["question"]
    assert "Nodes:" in t.call("graph_stats")
    assert "PaymentService" in t.call("get_node", {"label": "PaymentService"})
    assert "Neighbors of" in t.call("get_neighbors", {"label": "charge"})
    assert "Community" in t.call("get_community", {"community_id": 0})
    assert "God nodes" in t.call("god_nodes", {"top_n": 3})
    assert "Shortest path" in t.call("shortest_path", {"source": "checkout", "target": "charge"})
    assert "charge()" in t.call("query_graph", {"question": "charge"})
    assert t.call("nope").startswith("Unknown tool")
    uris = {r["uri"] for r in t.resources()}
    assert {"cairn-graph://report", "cairn-graph://stats", "cairn-graph://god-nodes"} <= uris
    assert t.read_resource("cairn-graph://report").startswith("#")
    try:
        t.read_resource("cairn-graph://nope")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown resource must raise")
    assert ToolError  # handler-signalled errors surface as ToolError


def test_tools_select_other_project(built_repo, tmp_path):
    from cairn.engines.graph import api
    t = api.tools()  # no default graph at the cwd
    out = t.call("graph_stats", {"project_path": str(built_repo)})
    assert "Nodes:" in out


def test_cli_main_returns_status_and_never_exits(built_repo, capsys):
    from cairn.engines.graph.cli import main
    gj = str(built_repo / ".cairn" / "graph" / "graph.json")
    assert main(["god-nodes", "--graph", gj, "--json"]) == 0
    hubs = json.loads(capsys.readouterr().out)
    assert hubs and "label" in hubs[0]
    assert main(["explain", "PaymentService", "--graph", gj]) == 0
    assert "PaymentService" in capsys.readouterr().out
    assert main(["no-such-command"]) == 1
    assert main(["query"]) == 1
    assert main(["--help"]) == 0
    usage = capsys.readouterr().out
    for sub in ("update", "extract", "query", "path", "explain", "affected", "god-nodes", "export html",
                "tree", "watch", "global add", "merge-graphs", "prs", "save-result", "reflect", "benchmark"):
        assert sub in usage, sub


def test_cli_update_and_bare_path_shorthand(tmp_path, capsys):
    from cairn.engines.graph.cli import main
    root = make_repo(tmp_path / "repo")
    assert main(["update", str(root)]) == 0
    assert (root / ".cairn" / "graph" / "graph.json").exists()
    assert "Code graph updated" in capsys.readouterr().out


def test_api_run_captures_output(built_repo):
    from cairn.engines.graph import api
    res = api.run(["path", "checkout", "charge", "--graph", str(api.graph_json(built_repo))])
    assert res["code"] == 0 and "Shortest path" in res["stdout"]


def test_save_result_and_reflect(built_repo):
    from cairn.engines.graph import api
    mem = built_repo / ".cairn" / "graph" / "memory"
    res = api.run(["save-result", "--question", "who charges?", "--answer", "PaymentService.process",
                   "--nodes", "charge()", "--outcome", "useful", "--memory-dir", str(mem)])
    assert res["code"] == 0, res
    assert list(mem.glob("*.md"))
    lessons = built_repo / ".cairn" / "graph" / "reflections" / "LESSONS.md"
    res = api.run(["reflect", "--memory-dir", str(mem), "--out", str(lessons),
                   "--graph", str(api.graph_json(built_repo))])
    assert res["code"] == 0, res
    assert lessons.exists() and "charge" in lessons.read_text()


def test_mapper_bridge_builds_and_indexes(tmp_path):
    from cairn.engines import mapper
    root = make_repo(tmp_path / "repo")
    out = tmp_path / "map"
    ok, summary = mapper.build(root, out_dir=out)
    assert ok, summary
    assert "nodes" in summary
    assert (root / ".cairn" / "graphignore").read_text().startswith("# cairn:")
    idx = mapper.MapIndex.load(out / "graph.json")
    assert len(idx) > 10 and idx.edge_count() > 10
    [pay] = idx.resolve("PaymentService")
    deps = idx.dependents([pay])
    assert any(d["label"] == "checkout()" for d in deps)
    assert any("idempotency" in r["text"] for r in idx.rationale(idx.resolve("PaymentService.process")))
    assert idx.areas() and idx.hubs(3)
    assert "Shortest path" in mapper.engine_text(root, "path", "checkout", "charge", out_dir=out)
    assert "Node: PaymentService" in mapper.engine_text(root, "explain", "PaymentService", out_dir=out)
    assert mapper.engine_text(root, "query", "charge", "--budget", "500", out_dir=out).startswith("Graph:")


def test_mapper_default_output_follows_project(tmp_path):
    from cairn.engines import mapper
    from cairn.project import Project
    root = make_repo(tmp_path / "repo")
    ok, _ = mapper.build(root)
    assert ok
    assert (Project(root=root.resolve()).map_json).exists()


def test_hook_guard_nudges_toward_the_graph(built_repo):
    from cairn.engines.graph import api
    out = api.hook_guard(built_repo, "search", {"tool_input": {"command": "grep -rn charge ."}})
    assert "cairn graph query" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    out = api.hook_guard(built_repo, "read", {"tool_name": "Read",
                                              "tool_input": {"file_path": str(built_repo / "shop/api.py")}})
    assert "PreToolUse" in out
    assert api.hook_guard(built_repo, "read", {"tool_input": {"file_path": "/etc/hosts"}}) == ""
    assert api.hook_guard(built_repo, "search", {"tool_input": {"command": "ls -la"}}) == ""
    assert json.loads(api.hook_guard(built_repo, "gemini"))["decision"] == "allow"


def test_tools_are_safe_across_threads_and_projects(built_repo, tmp_path):
    import threading
    from cairn.engines.graph import api
    other = make_repo(tmp_path / "other", {"lib/lonely.py": "def lonely():\n    return 1\n"})
    assert api.build(other)["ok"]
    t = api.tools(built_repo)
    errors = []

    def ask(project, label, expect):
        for _ in range(20):
            out = t.call("get_node", {"label": label, "project_path": str(project)})
            if expect not in out:
                errors.append(out[:120])

    threads = [threading.Thread(target=ask, args=(built_repo, "PaymentService", "Node: PaymentService")),
               threading.Thread(target=ask, args=(other, "lonely", "Node: lonely()"))]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=60)
    assert not errors, errors[:3]

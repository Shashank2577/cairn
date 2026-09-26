"""Graph engine: extraction on a multi-language repo, incremental update, clustering,
ignore rules and the output directory."""
from __future__ import annotations

import json

from test_graph_support import (  # noqa: F401  (fixtures)
    FILES, built_repo, isolated_env, load_graph, make_repo, node_ids, write,
)


def test_extracts_every_language_with_structure_and_calls(built_repo):
    data = load_graph(built_repo)
    ids = {n["id"] for n in data["nodes"]}
    # python, typescript, go and markdown all land in one graph
    assert {"shop_payments_paymentservice", "shop_gateway_charge", "shop_api_checkout"} <= ids
    assert {"web_cart_cart", "web_price_total"} <= ids
    assert {"svc_main_greet", "svc_main_main"} <= ids
    assert "docs_architecture" in ids
    rels = {(e["source"], e["relation"], e["target"]) for e in data["links"]}
    assert ("shop_api_checkout", "calls", "shop_payments_paymentservice") in rels
    assert ("shop_payments_paymentservice_process", "calls", "shop_gateway_charge") in rels
    assert ("web_cart_cart_sum", "calls", "web_price_total") in rels
    assert ("svc_main_main", "calls", "svc_main_greet") in rels
    assert ("shop_payments_paymentservice", "method", "shop_payments_paymentservice_process") in rels
    # rationale comments become nodes attached to their file
    assert any(n.get("file_type") == "rationale" and "idempotency" in n["label"] for n in data["nodes"])
    # every edge carries provenance
    assert {e["confidence"] for e in data["links"]} <= {"EXTRACTED", "INFERRED", "AMBIGUOUS"}
    assert data["built_at_commit"]


def test_output_folder_layout(built_repo):
    out = built_repo / ".cairn" / "graph"
    for name in ("graph.json", "GRAPH_REPORT.md", "graph.html", "manifest.json", ".graph_root"):
        assert (out / name).exists(), name
    assert (out / "cache").is_dir()
    # nothing is written at the repository root
    assert not [p.name for p in built_repo.iterdir() if p.name not in {".git", ".cairn", *{r.split("/")[0] for r in FILES}}]


def test_clustering_assigns_communities_and_report(built_repo):
    data = load_graph(built_repo)
    comms = {n.get("community") for n in data["nodes"]}
    assert None not in comms and len(comms) >= 2
    # without a model, communities are named after their hub, never left blank
    assert all(n.get("community_name") for n in data["nodes"])
    report = (built_repo / ".cairn" / "graph" / "GRAPH_REPORT.md").read_text()
    assert "God Nodes" in report or "god nodes" in report.lower()
    assert "Communit" in report


def test_cluster_module_is_deterministic():
    import networkx as nx
    from cairn.engines.graph.cluster import cluster
    G = nx.Graph()
    for a, b in [("a", "b"), ("b", "c"), ("c", "a"), ("x", "y"), ("y", "z"), ("z", "x"), ("c", "x")]:
        G.add_edge(a, b)
    first, second = cluster(G), cluster(G)
    assert first == second
    assert sorted(len(v) for v in first.values()) == [3, 3]


def test_incremental_update_changes_and_deletes(tmp_path):
    from cairn.engines.graph import api
    root = make_repo(tmp_path / "repo")
    assert api.build(root)["ok"]
    before = node_ids(root)
    write(root, {"shop/gateway.py": FILES["shop/gateway.py"] + "\n\ndef void(order_id: str) -> str:\n    return refund(order_id)\n"})
    (root / "svc" / "main.go").unlink()
    res = api.build(root, changed=["shop/gateway.py", "svc/main.go"])
    assert res["ok"], res
    after = node_ids(root)
    assert "shop_gateway_void" in after and "shop_gateway_void" not in before
    assert not any(i.startswith("svc_main") for i in after)
    assert "shop_payments_paymentservice" in after  # untouched files keep their nodes


def test_update_is_a_noop_when_nothing_changed(built_repo):
    from cairn.engines.graph import api
    before = (built_repo / ".cairn" / "graph" / "graph.json").read_text()
    assert api.build(built_repo)["ok"]
    assert (built_repo / ".cairn" / "graph" / "graph.json").read_text() == before


def test_graphignore_and_config_ignore(tmp_path):
    from cairn.engines.graph import api
    root = make_repo(tmp_path / "repo")
    (root / ".cairn").mkdir(exist_ok=True)
    (root / ".cairn" / "graphignore").write_text("web/\n")
    (root / ".cairn" / "config.toml").write_text('[map]\nignore = ["svc/"]\n')
    assert api.build(root)["ok"]
    ids = node_ids(root)
    assert not any(i.startswith("web_") for i in ids)
    assert not any(i.startswith("svc_") for i in ids)
    assert "shop_api_checkout" in ids


def test_cairn_dir_is_never_indexed(tmp_path):
    from cairn.engines.graph import api
    root = make_repo(tmp_path / "repo")
    write(root, {".cairn/workflow/templates/plan.py": "def planned():\n    return 1\n"})
    assert api.build(root)["ok"]
    assert not any("planned" in i for i in node_ids(root))


def test_explicit_output_dir(tmp_path):
    from cairn.engines.graph import api, paths
    root = make_repo(tmp_path / "repo")
    out = tmp_path / "elsewhere" / "map"
    res = api.build(root, out=out)
    assert res["ok"] and (out / "graph.json").exists()
    assert not (root / ".cairn" / "graph" / "graph.json").exists()
    assert paths.GRAPH_OUT == paths.DEFAULT_OUT  # the override is scoped to the call
    assert api.query(root, "charge", out=out).startswith("Graph:")


def test_out_dir_helpers():
    from cairn.engines.graph import paths
    assert paths.is_out_dir("/r/.cairn/graph") and not paths.is_out_dir("/r/graph")
    assert str(paths.out_root("/r/.cairn/graph")) == "/r"
    assert str(paths.graph_root("/r/.cairn/graph/graph.json")) == "/r"
    assert str(paths.out_root("/r/backup")) == "/r"
    with paths.output_dir("/tmp/x/out"):
        assert paths.GRAPH_OUT == "/tmp/x/out" and paths.GRAPH_OUT_NAME == "out"
    assert paths.GRAPH_OUT == ".cairn/graph"


def test_manifest_and_cache_make_second_build_cheap(tmp_path):
    from cairn.engines.graph import api
    root = make_repo(tmp_path / "repo")
    assert api.build(root)["ok"]
    manifest = json.loads((root / ".cairn" / "graph" / "manifest.json").read_text())
    assert any("gateway.py" in k for k in (manifest.get("files") or manifest))
    assert list((root / ".cairn" / "graph" / "cache").rglob("*.json"))


def test_config_viz_node_limit(tmp_path):
    from cairn.engines.graph import api
    root = make_repo(tmp_path / "repo")
    (root / ".cairn").mkdir(exist_ok=True)
    (root / ".cairn" / "config.toml").write_text("[map]\nviz_node_limit = 0\n")
    assert api.build(root)["ok"]
    assert not (root / ".cairn" / "graph" / "graph.html").exists()
    assert (root / ".cairn" / "graph" / "graph.json").exists()


def test_concurrent_builds_keep_their_own_output_dirs(tmp_path):
    """An explicit output dir rewrites a process-wide value; builds at the normal
    location running in other threads must still write to their own folders."""
    import threading
    from cairn.engines.graph import api
    a = make_repo(tmp_path / "a")
    b = make_repo(tmp_path / "b", {"lib/only_b.py": "def only_b():\n    return 2\n"})
    out_b = tmp_path / "b-map"
    results = {}

    def run(name, fn):
        results[name] = fn()

    threads = [threading.Thread(target=run, args=("a", lambda: api.build(a))),
               threading.Thread(target=run, args=("b", lambda: api.build(b, out=out_b))),
               threading.Thread(target=run, args=("q", lambda: api.run(["--help"])))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert results["a"]["ok"] and results["b"]["ok"] and results["q"]["code"] == 0
    assert "shop_api_checkout" in node_ids(a)
    ids_b = {n["id"] for n in json.loads((out_b / "graph.json").read_text())["nodes"]}
    assert "lib_only_b_only_b" in ids_b
    assert not (b / ".cairn" / "graph" / "graph.json").exists()
    assert not (a / "b-map").exists()

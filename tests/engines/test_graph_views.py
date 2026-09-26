"""Graph engine: HTML views (offline, bundled libraries) and exporters."""
from __future__ import annotations

import json
import re

import pytest

from test_graph_support import built_repo, isolated_env  # noqa: F401  (fixtures)

CDN = re.compile(r"(unpkg\.com|jsdelivr\.net|d3js\.org|cdnjs|googleapis)")


def _no_remote_scripts(html: str) -> None:
    assert not re.search(r'<script[^>]+src="https?://', html), "views must not load scripts from the network"
    assert not CDN.search(html.split("<script>", 1)[0])


def test_bundled_assets_exist_and_are_served_by_name():
    from cairn.engines.graph import api
    from cairn.engines.graph.assets import ASSETS, asset_path, script_tag
    for name, meta in ASSETS.items():
        body, media = api.asset(name)
        assert len(body) > 50_000 and media == "text/javascript"
        assert meta["version"] and asset_path(name).is_file()
    with pytest.raises(KeyError):
        asset_path("../../etc/passwd")
    assert script_tag("d3.v7.min.js", "/assets/") == '<script src="/assets/d3.v7.min.js"></script>'
    inline = script_tag("d3.v7.min.js")
    assert inline.startswith("<script>\n") and "d3js.org v7" in inline


def test_graph_view_inline_and_served(built_repo):
    from cairn.engines.graph import api
    html = api.graph_view_html(built_repo)
    assert html.startswith("<!DOCTYPE html>") and "vis.Network" in html or "new vis" in html
    assert "PaymentService" in html
    _no_remote_scripts(html)
    served = api.graph_view_html(built_repo, asset_base="/api/graph/assets/")
    assert '<script src="/api/graph/assets/vis-network.min.js"></script>' in served
    assert len(served) < len(html) / 5


def test_graph_view_aggregates_large_graphs(built_repo):
    from cairn.engines.graph import api
    full = api.graph_view_html(built_repo, asset_base="/a/")
    agg = api.graph_view_html(built_repo, node_limit=3, asset_base="/a/")
    assert agg is not None and "refund()" in full and "refund()" not in agg
    assert "cross-community edges" in agg


def test_tree_view(built_repo):
    from cairn.engines.graph import api
    html = api.tree_view_html(built_repo)
    assert "d3.hierarchy" in html and "shop" in html
    _no_remote_scripts(html)
    assert '<script src="/x/d3.v7.min.js"></script>' in api.tree_view_html(built_repo, asset_base="/x/")


def test_callflow_view(built_repo):
    from cairn.engines.graph import api
    html = api.callflow_view_html(built_repo, asset_base="/x/")
    assert '<script src="/x/mermaid.min.js"></script>' in html
    assert 'class="mermaid"' in html
    full = api.callflow_view_html(built_repo)
    _no_remote_scripts(full)


def test_written_graph_html_is_offline(built_repo):
    html = (built_repo / ".cairn" / "graph" / "graph.html").read_text()
    _no_remote_scripts(html)
    assert "vis-network" in html


@pytest.mark.parametrize("fmt,expect", [
    ("graphml", "graph.graphml"),
    ("neo4j", "cypher.txt"),
    ("html", "graph.html"),
])
def test_file_exporters(built_repo, fmt, expect):
    from cairn.engines.graph import api
    gj = api.graph_json(built_repo)
    res = api.run(["export", fmt, "--graph", str(gj)])
    assert res["code"] == 0, res
    assert (gj.parent / expect).exists()


def test_wiki_and_obsidian_exports(built_repo, tmp_path):
    from cairn.engines.graph import api
    gj = api.graph_json(built_repo)
    res = api.run(["export", "wiki", "--graph", str(gj)])
    assert res["code"] == 0, res
    assert (gj.parent / "wiki" / "index.md").exists()
    vault = tmp_path / "vault"
    res = api.run(["export", "obsidian", "--graph", str(gj), "--dir", str(vault)])
    assert res["code"] == 0, res
    assert list(vault.rglob("*.md")) and list(vault.rglob("*.canvas"))


def test_callflow_and_tree_cli(built_repo):
    from cairn.engines.graph import api
    gj = api.graph_json(built_repo)
    res = api.run(["export", "callflow-html", "--graph", str(gj)])
    assert res["code"] == 0, res
    assert list(gj.parent.glob("*-callflow.html"))
    out = built_repo / "tree.html"
    res = api.run(["tree", "--graph", str(gj), "--output", str(out)])
    assert res["code"] == 0, res
    assert out.exists()
    out.unlink()


def test_cypher_is_valid_shape(built_repo):
    import networkx as nx
    from networkx.readwrite import json_graph
    from cairn.engines.graph import api
    from cairn.engines.graph.export import to_cypher
    data = json.loads(api.graph_json(built_repo).read_text())
    G = json_graph.node_link_graph(data, edges="links")
    assert isinstance(G, nx.Graph)
    target = built_repo / ".cairn" / "graph" / "test.cypher"
    to_cypher(G, str(target))
    text = target.read_text()
    assert "MERGE" in text and "Cairn" in text.splitlines()[0]


def test_benchmark_and_diagnostics(built_repo):
    from cairn.engines.graph import api
    gj = api.graph_json(built_repo)
    res = api.run(["benchmark", str(gj)])
    assert res["code"] == 0 and "x]" in res["stdout"]
    res = api.run(["diagnose", "multigraph", "--graph", str(gj), "--json"])
    assert res["code"] == 0 and json.loads(res["stdout"])

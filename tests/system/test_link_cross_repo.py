"""Cross-repo linking: the rules that match across repositories, and the graph engine's own cross-repo
passes restated between containers (FR-013 to FR-015, FR-029b; T021).

Covers what ``test_link_rules`` does not: the sibling-package and shared-store rules as they behave
across repositories with several containers each, the ``cross-repo-call`` and ``cross-repo-type``
relationships built from a merged graph's cross-repo edges (with their rule name and evidence), and
that reading the merged graph never writes to it or to any repository.
"""
from __future__ import annotations

import json
from pathlib import Path

from cairn.system.link import UNRESOLVED_ID, cross_repo_edges, link

from .util import repo, store

SVC = {"Dockerfile": "FROM x\n", "package.json": '{"name": "%s", "dependencies": {"express": "4"}}',
       "server.js": "const express = require('express');\nconst app = express();\napp.get('/x', h);\n"}


def product(tmp_path: Path, repos: dict[str, dict], viewer: str, extra: str = "", base: dict | None = None):
    """Stored models for several repositories, declared as one product from the viewing repository."""
    for name, files in repos.items():
        store(repo(tmp_path, name, {**(SVC if base is None else base), **files}))
    paths = "".join(f"  - path: ../{n}\n" for n in repos)
    (tmp_path / viewer / ".cairn" / "system.yaml").write_text(f"system: P\nrepos:\n{paths}{extra}",
                                                               encoding="utf-8")
    return link(tmp_path / viewer)


def by_rule(m, rule):
    names = {e.id: e.name for e in m.elements.values()}
    return [(names.get(r.from_id, r.from_id), names.get(r.to_id, r.to_id), r) for r in m.relationships.values()
            if r.rule == rule]


# ----------------------------------------------------------------------- sibling package and shared store
def test_a_sibling_package_is_build_time_and_names_the_import_site(tmp_path):
    """FR-013: a container depends on a sibling's package at build time; the evidence is the import."""
    lib = {"pyproject.toml": '[project]\nname = "acme-types"\n', "acme_types/__init__.py": "X = 1\n"}
    app = {"Dockerfile": "FROM x\n", "pyproject.toml": '[project]\nname = "app"\ndependencies = ["acme-types"]\n',
           "app/__init__.py": "",
           "app/main.py": "import os\nfrom acme_types import X\nprint(os.environ['MODE'], X)\n"}
    m = product(tmp_path, {"app": app, "lib": lib}, "app", base={})
    rels = by_rule(m, "package-dep")
    assert [(a, b) for a, b, _ in rels] == [("app", "acme-types")]
    rel = rels[0][2]
    assert rel.style == "build" and rel.how == "package acme-types" and rel.what is None
    assert rel.evidence[0].ref() == "app/app/main.py:2"
    assert rel.evidence[0].kind == "import"


def test_a_compose_service_built_from_a_sibling_directory_is_that_siblings_container(tmp_path):
    """FR-013, FR-006: the deploy unit is the sibling's, so the link is between the two repositories."""
    web = {"Dockerfile": "FROM node\n", "package.json": '{"name":"web","dependencies":{"express":"4"}}',
           "server.js": "const express = require('express');\nconst app = express();\n",
           "docker-compose.yml": ("services:\n  web:\n    build: .\n    ports: ['80:80']\n"
                                  "  api:\n    build: ../api\n    environment:\n      API_URL: http://api:8080\n"),
           "client.js": "fetch(`${process.env.API_URL}/orders`);\n"}
    api = {"Dockerfile": "FROM node\n", "package.json": '{"name":"api","dependencies":{"express":"4"}}',
           "server.js": "const express = require('express');\nconst app = express();\napp.get('/orders', h);\n"}
    m = product(tmp_path, {"web": web, "api": api}, "web")
    http = {(a, b, r.rule) for a, b, r in by_rule(m, "http-route-match") + by_rule(m, "http-config")}
    assert ("web", "api", "http-route-match") in http
    assert "api" in {e.name for e in m.elements.values()} and "web" in {e.name for e in m.elements.values()}


def test_one_shared_store_across_repositories_keeps_both_ends_evidence(tmp_path):
    """FR-013, SC-002: the merged store element carries an evidence reference from each repository."""
    a = {"Dockerfile": "FROM x\n", "a.py": "import psycopg\npsycopg.connect('postgresql://db/orders')\n"}
    b = {"Dockerfile": "FROM x\n", "b.py": "import psycopg\npsycopg.connect(dsn)\n"}
    m = product(tmp_path, {"a": a, "b": b}, "a")
    stores = [e for e in m.elements.values() if e.type == "data-store"]
    assert len(stores) == 1
    assert {ev.repo for ev in stores[0].evidence} == {"a", "b"}
    assert {r.from_id.split(":")[-1] for r in m.relationships.values() if r.to_id == stores[0].id} == {"a", "b"}
    assert UNRESOLVED_ID not in m.elements


def test_a_url_resolved_through_configuration_stores_the_sibling_identifier_not_the_host(tmp_path):
    """FR-029b: a sibling link reached through a URL never keeps the URL or the host string."""
    web = {"Dockerfile": "FROM node\n", "package.json": '{"name":"web","dependencies":{"express":"4"}}',
           "server.js": "const express = require('express');\nconst app = express();\n",
           "docker-compose.yml": ("services:\n  web:\n    build: .\n    environment:\n"
                                  "      ORDERS_URL: http://orders.internal.example:8080\n"),
           "client.js": "fetch(`${process.env.ORDERS_URL}/orders`);\n"}
    orders = {"Dockerfile": "FROM node\n", "package.json": '{"name":"orders","dependencies":{"express":"4"}}',
              "server.js": "const express = require('express');\nconst app = express();\napp.get('/orders', h);\n"}
    m = product(tmp_path, {"web": web, "orders": orders}, "web")
    text = json.dumps({"e": {e.id: e.meta for e in m.elements.values()},
                       "r": [{k: v for k, v in r.meta.items()} for r in m.relationships.values()]})
    assert "internal.example" not in text and "http://" not in text
    assert ("web", "orders") in {(r.from_id.split(":")[-1], r.to_id.split(":")[-1])
                                for r in m.relationships.values()}


# ------------------------------------------------------------- the graph engine's cross-repo passes
def merged(tmp_path: Path, monkeypatch, edges: list[dict], nodes: list[dict], repos: dict[str, Path]):
    """A ``CAIRN_HOME`` holding a merged graph and the manifest that says which repo each tag is."""
    home = tmp_path / "home"
    (home / "graph").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CAIRN_HOME", str(home))
    (home / "graph" / "global-graph.json").write_text(
        json.dumps({"directed": False, "multigraph": False, "nodes": nodes, "links": edges}), encoding="utf-8")
    (home / "graph" / "global-manifest.json").write_text(json.dumps(
        {"version": 1, "repos": {tag: {"source_path": str(root / ".cairn" / "graph" / "graph.json")}
                                  for tag, root in repos.items()}}), encoding="utf-8")
    return home / "graph" / "global-graph.json"


def test_cross_repo_call_and_shared_type_become_container_relationships(tmp_path, monkeypatch):
    """FR-013: what the engine's cross-repo passes already resolved is restated between containers,
    each edge carrying its rule name and the file and line it came from."""
    a_root, b_root = tmp_path / "a", tmp_path / "b"
    merged(tmp_path, monkeypatch,
           edges=[{"source": "a::c", "target": "b::d", "relation": "calls", "context": "cross_repo",
                   "source_file": "client.js", "source_location": "L12", "_src": "a::c", "_tgt": "b::d"},
                  {"source": "a::t1", "target": "b::t2", "relation": "same_type_as", "context": "cross_repo",
                   "source_file": "orders.py", "source_location": "L3", "_src": "a::t1", "_tgt": "b::t2"}],
           nodes=[{"id": "a::c", "label": "OrderClient.fetch", "repo": "a", "source_file": "client.js"},
                  {"id": "b::d", "label": "Orders.list", "repo": "b", "source_file": "handlers.js"},
                  {"id": "a::t1", "label": "OrderUpserted", "repo": "a", "source_file": "orders.py"},
                  {"id": "b::t2", "label": "OrderUpserted", "repo": "b", "source_file": "consumer.py"}],
           repos={"a": a_root, "b": b_root})
    m = product(tmp_path, {"a": {"Dockerfile": "FROM x\n", "client.js": "fetch('/orders');\n"},
                           "b": {"Dockerfile": "FROM x\n", "handlers.js": "app.get('/orders', h);\n"}}, "a")
    calls = by_rule(m, "cross-repo-call")
    types = by_rule(m, "cross-repo-type")
    assert [(a, b) for a, b, _ in calls] == [("a", "b")]
    assert [(a, b) for a, b, _ in types] == [("a", "b")]
    assert calls[0][2].evidence[0].ref() == "a/client.js:12"
    assert types[0][2].evidence[0].ref() == "a/orders.py:3"
    assert calls[0][2].style == "sync" and types[0][2].style == "build"
    assert calls[0][2].meta["resolution"] == "cross-repo graph edge"


def test_an_ordinary_edge_in_the_merged_graph_is_not_a_cross_repo_link(tmp_path, monkeypatch):
    """FR-010: only the passes' own edges count; an ordinary call inside one repository is not a link."""
    roots = {"a": tmp_path / "a", "b": tmp_path / "b"}
    merged(tmp_path, monkeypatch,
           edges=[{"source": "a::c", "target": "a::d", "relation": "calls", "context": "call"},
                  {"source": "a::c", "target": "b::d", "relation": "reads", "context": "read"}],
           nodes=[{"id": "a::c", "label": "c", "repo": "a", "source_file": "x.js"},
                  {"id": "a::d", "label": "d", "repo": "a", "source_file": "y.js"},
                  {"id": "b::d", "label": "d", "repo": "b", "source_file": "y.js"}],
           repos=roots)
    m = product(tmp_path, {"a": {"Dockerfile": "FROM x\n", "x.js": "f();\n"},
                           "b": {"Dockerfile": "FROM x\n", "y.js": "g();\n"}}, "a")
    assert not by_rule(m, "cross-repo-call") and not by_rule(m, "cross-repo-type")


def test_a_graph_that_cannot_be_read_contributes_nothing(tmp_path, monkeypatch):
    """A missing, truncated or oversized merged graph is skipped: linking still produces the rest."""
    for body in (None, "{not json", "[]"):
        merged(tmp_path, monkeypatch, edges=[], nodes=[], repos={"a": tmp_path / "a"})
        path = tmp_path / "home" / "graph" / "global-graph.json"
        if body is None:
            path.unlink()
        else:
            path.write_text(body, encoding="utf-8")
        m = product(tmp_path, {"a": {"Dockerfile": "FROM x\n", "a.py": "import psycopg\npsycopg.connect(d)\n"},
                               "b": {"Dockerfile": "FROM x\n", "b.py": "import psycopg\npsycopg.connect(d)\n"}}, "a")
        assert m.elements and not by_rule(m, "cross-repo-call")


def test_reading_the_merged_graph_writes_nothing(tmp_path, monkeypatch):
    """FR-015: linking stays read-only, including when it reads the graph engine's merged output."""
    merged(tmp_path, monkeypatch, edges=[], nodes=[],
           repos={"a": tmp_path / "a", "b": tmp_path / "b"})
    graph = tmp_path / "home" / "graph" / "global-graph.json"
    before = (graph.stat().st_mtime_ns, sorted(p.name for p in graph.parent.iterdir()))
    for name in ("a", "b"):
        store(repo(tmp_path, name, {"Dockerfile": "FROM x\n", "main.py": "import psycopg\npsycopg.connect(d)\n"}))
    db = tmp_path / "b" / ".cairn" / "brain.db"
    db_before = (db.stat().st_mtime_ns, sorted(p.name for p in db.parent.iterdir()))
    (tmp_path / "a" / ".cairn" / "system.yaml").write_text("repos:\n  - path: ../b\n", encoding="utf-8")
    link(tmp_path / "a")
    assert (graph.stat().st_mtime_ns, sorted(p.name for p in graph.parent.iterdir())) == before
    assert (db.stat().st_mtime_ns, sorted(p.name for p in db.parent.iterdir())) == db_before


def test_a_repository_the_merged_graph_does_not_know_contributes_nothing(tmp_path, monkeypatch):
    """One sibling in the manifest is enough to leave the pair alone: both ends must be known."""
    merged(tmp_path, monkeypatch, edges=[], nodes=[], repos={"a": tmp_path / "a"})
    m = product(tmp_path, {"a": {"Dockerfile": "FROM x\n", "a.py": "import psycopg\npsycopg.connect(d)\n"},
                           "b": {"Dockerfile": "FROM x\n", "b.py": "import psycopg\npsycopg.connect(d)\n"}}, "a")
    assert m.product["repos"] == ["a", "b"] and not by_rule(m, "cross-repo-call")


def test_cross_repo_edges_are_bounded_and_ordered(tmp_path, monkeypatch):
    """The merged graph is not trusted for size: an oversized file yields nothing instead of loading."""
    graph = merged(tmp_path, monkeypatch, edges=[], nodes=[], repos={"a": tmp_path / "a", "b": tmp_path / "b"})
    monkeypatch.setenv("CAIRN_GRAPH_MAX_GRAPH_BYTES", "16")
    assert cross_repo_edges(graph, {tmp_path / "a", tmp_path / "b"}) == []


def test_link_is_deterministic_for_the_same_inputs(tmp_path, monkeypatch):
    """FR-005: the same repositories give byte-identical ids, run after run."""
    merged(tmp_path, monkeypatch,
           edges=[{"source": "a::c", "target": "b::d", "relation": "calls", "context": "cross_repo",
                   "source_file": "x.js", "source_location": "L1", "_src": "a::c", "_tgt": "b::d"}],
           nodes=[{"id": "a::c", "label": "c", "repo": "a", "source_file": "x.js"},
                  {"id": "b::d", "label": "d", "repo": "b", "source_file": "y.js"}],
           repos={"a": tmp_path / "a", "b": tmp_path / "b"})
    repos = {"a": {"Dockerfile": "FROM x\n", "x.js": "f();\n"}, "b": {"Dockerfile": "FROM x\n", "y.js": "g();\n"}}
    for name, files in repos.items():
        store(repo(tmp_path, name, files))
    (tmp_path / "a" / ".cairn" / "system.yaml").write_text("repos:\n  - path: ../b\n", encoding="utf-8")
    first, second = link(tmp_path / "a"), link(tmp_path / "a")
    assert sorted(first.relationships) == sorted(second.relationships)
    assert sorted(first.elements) == sorted(second.elements)
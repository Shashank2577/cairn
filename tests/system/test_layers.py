"""Dependency layers (FR-026, T031a/T031b): first-party only, collapsed cycles, per-layer budget, real entry
points, deterministic and model-free. IDs in docstrings map to specs/002-system-model/traceability.md."""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

import pytest

from cairn.engines.mapper import CYCLE_COLLAPSE, LAYER_BUDGET, Edge, MapIndex


def make_index(files: list[str], edges: list[tuple[str, str]], shuffle: int | None = None) -> MapIndex:
    """A map with one code node per file; ``edges`` are file-to-file imports."""
    idx = MapIndex()
    order = list(files)
    pairs = list(edges)
    if shuffle is not None:
        random.Random(shuffle).shuffle(order)
        random.Random(shuffle).shuffle(pairs)
    for f in order:
        idx.nodes[f] = {"id": f, "label": f, "source_file": f, "file_type": "code"}
        idx.by_file[f].append(f)
    for a, b in pairs:
        idx.out[a].append(Edge(b, "imports", "EXTRACTED", None))
        idx.inc[b].append(Edge(a, "imports", "EXTRACTED", None))
    return idx


def node(g: dict, nid: str) -> dict:
    return next(n for n in g["nodes"] if n["id"] == nid)


def ids(g: dict) -> set[str]:
    return {n["id"] for n in g["nodes"]}


def test_vendored_folders_are_hidden_by_default_and_counted(tmp_path):
    """FR-026: vendor/, third_party/, node_modules/, map.vendored, .gitattributes and NOTICE hide folders and count them."""
    (tmp_path / ".gitattributes").write_text("attrs_lib/** linguist-vendored\n")
    (tmp_path / "NOTICE").write_text("Cairn\n\n- notice_lib/   copied from Acme, Apache-2.0\n- app/   our own code, written here\n")
    files = ["app/a.py", "app/b.py", "svc/s.py",
             "vendor/x/x.py", "third_party/y/y.py", "web/node_modules/z/z.js",
             "cfg_lib/c.py", "attrs_lib/a.py", "notice_lib/n.py"]
    edges = [("app/a.py", "svc/s.py"), ("app/a.py", "vendor/x/x.py"), ("svc/s.py", "third_party/y/y.py"),
             ("app/b.py", "cfg_lib/c.py"), ("app/b.py", "attrs_lib/a.py"), ("svc/s.py", "notice_lib/n.py")]
    g = make_index(files, edges).file_graph(max_files=1, root=tmp_path, vendored=["cfg_lib"])
    assert g["level"] == "folder"
    assert ids(g) <= {"app", "svc"}
    v = g["vendored"]
    assert v["count"] == 6
    assert {f["id"] for f in v["folders"]} == {"vendor/x", "third_party/y", "web/node_modules/z", "cfg_lib", "attrs_lib", "notice_lib"}
    assert {f["reason"] for f in v["folders"]} >= {"vendor/ folder", "listed in map.vendored", "linguist-vendored in .gitattributes"}
    assert any(f["reason"] == "listed in NOTICE" for f in v["folders"])
    # first-party code that uses vendored code is recorded, but only against the vendored side
    assert {(ln["source"], ln["target"]) for ln in v["links"]} >= {("app", "vendor/x"), ("svc", "third_party/y")}
    assert {ln["target"] for ln in g["links"]} <= {"app", "svc"}


def test_vendored_marker_file_hides_a_folder(tmp_path):
    """FR-026: a VENDORED marker file in a folder marks it as third-party."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "VENDORED").write_text("from upstream\n")
    g = make_index(["app/a.py", "lib/l.py"], [("app/a.py", "lib/l.py")]).file_graph(max_files=1, root=tmp_path)
    assert [f["id"] for f in g["vendored"]["folders"]] == ["lib"] and "app" not in {f["id"] for f in g["vendored"]["folders"]}


def test_a_twenty_folder_cycle_is_one_labelled_block_that_expands():
    """FR-026: a large import cycle is one block ("cycle of 20 folders") with its members and links kept for expansion."""
    ring = [f"pkg/m{i:02d}/f.py" for i in range(20)]
    files = ring + ["top/t.py", "base/b.py", "s1/a.py", "s2/b.py", "s3/c.py"]
    edges = [(ring[i], ring[(i + 1) % 20]) for i in range(20)]
    edges += [("top/t.py", ring[0]), (ring[7], "base/b.py"), ("top/t.py", "base/b.py"),
              ("s1/a.py", "s2/b.py"), ("s2/b.py", "s3/c.py"), ("s3/c.py", "s1/a.py")]  # a 3-cycle stays as units
    g = make_index(files, edges).file_graph(max_files=3)
    cyc = [n for n in g["nodes"] if n["kind"] == "cycle"]
    assert len(cyc) == 1 and cyc[0]["label"] == "cycle of 20 folders" and 20 >= CYCLE_COLLAPSE > 3
    assert not (set(cyc[0]["members"]) & ids(g))  # no member on its own row
    assert len(cyc[0]["members"]) == 20 and g["cycles"][0]["size"] == 20
    internal = [ln for ln in g["cycles"][0]["links"] if ln["source"] in cyc[0]["members"] and ln["target"] in cyc[0]["members"]]
    assert len(internal) == 20  # the ring is kept for the expanded view
    assert {"source": "top", "target": cyc[0]["id"], "weight": 1} in g["links"]
    assert {"source": cyc[0]["id"], "target": "base", "weight": 1} in g["links"]
    assert {"source": "top", "target": "pkg/m00", "weight": 1} in g["cycles"][0]["links"]  # expands to member-level edges
    assert {"s1", "s2", "s3"} <= ids(g) and node(g, "s1")["cycle"] == ["s1", "s2", "s3"]  # small cycles stay visible
    # the block is placed by the layering of the rest: above base, below top
    assert node(g, "base")["layer"] < cyc[0]["layer"] < node(g, "top")["layer"]


def test_per_layer_budget_with_a_plus_n_remainder():
    """FR-026: at most the node budget per layer; the rest are listed as "+N" (rest)."""
    users = [f"u{i:02d}/u.py" for i in range(30)]
    edges = [(u, "core/c.py") for u in users]
    edges += [(u, "core/c.py") for u in users[:3]]  # u00-u02 carry the heaviest links
    idx = make_index(users + ["core/c.py"], edges)
    for u in users[:3]:  # a second file under the same imports raises their weight
        idx.out[u].append(Edge("core/c.py", "calls", "EXTRACTED", None))
    g = idx.file_graph(max_files=3)
    top = next(ly for ly in g["layers"] if ly["total"] == 30)
    assert LAYER_BUDGET == 12 and len(top["shown"]) == 12 and len(top["rest"]) == 18
    assert set(top["shown"]).isdisjoint(top["rest"])
    assert set(top["shown"]) >= {"u00", "u01", "u02"}  # heaviest links are kept in view
    assert idx.file_graph(max_files=3, layer_budget=5)["layers"][0]["shown"].__len__() == 5


def test_entry_points_come_from_pyproject_scripts_and_main_modules(tmp_path):
    """FR-026: entry points are detected from [project.scripts], __main__.py and server modules, not from position."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n[project.scripts]\nxcli = "pkg.cli:main"\n')
    files = ["src/pkg/cli.py", "src/pkg/__main__.py", "src/pkg/server.py", "src/pkg/core/c.py", "src/pkg/util/u.py",
             "src/pkg/orphan_top/o.py"]
    edges = [("src/pkg/cli.py", "src/pkg/core/c.py"), ("src/pkg/__main__.py", "src/pkg/cli.py"),
             ("src/pkg/server.py", "src/pkg/core/c.py"), ("src/pkg/core/c.py", "src/pkg/util/u.py"),
             ("src/pkg/orphan_top/o.py", "src/pkg/util/u.py")]
    g = make_index(files, edges).file_graph(max_files=3, root=tmp_path)
    assert g["level"] == "folder"
    kinds = {n["id"]: {s["kind"] for s in n["entry"]} for n in g["nodes"] if n["entry"]}
    assert kinds == {"src/pkg": {"script", "main", "server"}}
    sigs = {s["detail"] for s in node(g, "src/pkg")["entry"]}
    assert "script xcli in [project.scripts]" in sigs and "__main__.py" in sigs
    # a folder that sits at the top of the layers but has no signal is not an entry point
    assert node(g, "src/pkg/orphan_top")["entry"] == []
    assert [e["id"] for e in g["entries"]] == ["src/pkg"]


def test_entry_points_from_system_model_containers_and_package_json(tmp_path):
    """FR-026: containers from the system model (hints) and package.json bin/main count as entry signals."""
    (tmp_path / "package.json").write_text(json.dumps({"name": "w", "bin": {"w": "./bin/w.js"}, "main": "lib/index.js"}))
    files = ["bin/w.js", "lib/index.js", "svc/handler.py", "svc/db.py"]
    edges = [("bin/w.js", "lib/index.js"), ("svc/handler.py", "svc/db.py")]
    g = make_index(files, edges).file_graph(max_files=100, root=tmp_path, hints={"svc/handler.py": "Orders API"})
    assert g["level"] == "file"
    assert node(g, "bin/w.js")["entry"][0]["detail"] == "script w in package.json bin"
    assert node(g, "lib/index.js")["entry"][0]["kind"] == "main"
    assert node(g, "svc/handler.py")["entry"][0] == {"kind": "container", "detail": "container Orders API", "file": "svc/handler.py"}
    assert node(g, "svc/db.py")["entry"] == []


def test_evidence_lists_files_and_import_counts():
    """FR-026/FR-027: every folder carries its files and import counts for the click-through."""
    g = make_index(["a/x.py", "a/y.py", "b/z.py"], [("a/x.py", "b/z.py"), ("a/y.py", "b/z.py")]).file_graph(max_files=1)
    a, b = node(g, "a"), node(g, "b")
    assert a["evidence"]["files_total"] == 2 and {f["path"] for f in a["evidence"]["files"]} == {"a/x.py", "a/y.py"}
    assert (a["evidence"]["imports_out"], a["evidence"]["imports_in"]) == (2, 0)
    assert (b["evidence"]["imports_out"], b["evidence"]["imports_in"]) == (0, 2)
    assert g["links"] == [{"source": "a", "target": "b", "weight": 2}]


def _synthetic(n_files: int, seed: int = 7) -> tuple[list[str], list[tuple[str, str]]]:
    rnd = random.Random(seed)
    per = 10
    folders = [f"pkg{i // 25}/mod{i}" for i in range(n_files // per)]
    files = [f"{d}/f{j}.py" for d in folders for j in range(per)]
    edges: list[tuple[str, str]] = []
    for i, f in enumerate(files):
        for _ in range(3):
            j = rnd.randrange(max(0, i - 400), min(len(files), i + 400))
            if files[j] != f:
                edges.append((f, files[j]))
    return files, edges


def test_output_is_deterministic_regardless_of_input_order():
    """FR-026: the same input gives the same JSON, whatever order the map lists its nodes and edges in."""
    files, edges = _synthetic(600)
    a = json.dumps(make_index(files, edges).file_graph(), sort_keys=False)
    b = json.dumps(make_index(files, edges, shuffle=1).file_graph(), sort_keys=False)
    c = json.dumps(make_index(files, edges, shuffle=2).file_graph(), sort_keys=False)
    assert a == b == c


def test_no_model_calls(monkeypatch, cairn):
    """FR-026: building the layers never calls a model; the router is patched to raise."""
    from cairn import router

    def boom(*a, **k):
        raise AssertionError("a model was called")
    monkeypatch.setattr(router.Router, "complete", boom)
    g = cairn.architecture()
    assert g["level"] == "file" and any(n["id"] == "shop/api.py" for n in g["nodes"])
    assert {"nodes", "links", "layers", "cycles", "entries", "vendored", "isolated"} <= set(g)


def test_architecture_hides_vendored_folders_from_config(cairn):
    """FR-026: map.vendored in .cairn/config.toml hides a folder in the page's data and counts it."""
    cairn.project.cfg = (lambda orig: lambda k, d=None: ["shop"] if k == "map.vendored" else orig(k, d))(cairn.project.cfg)
    g = cairn.architecture()
    assert g["vendored"]["count"] == 1 and g["vendored"]["folders"][0]["id"] == "shop"
    assert "shop/api.py" not in ids(g)


def _timed(n: int) -> float:
    files, edges = _synthetic(n)
    idx = make_index(files, edges)
    best = 1e9
    for _ in range(3):
        t = time.perf_counter()
        g = idx.file_graph()
        best = min(best, time.perf_counter() - t)
    assert g["level"] == "folder" and g["scope"]["files"] == n
    return best


def test_performance_on_a_5000_file_repo_and_near_linear_scaling():
    """FR-026 / SC performance: 5,000 files in under 1.5 s (typically ~0.1 s); 1x/2x/4x grows near-linearly."""
    t1, t2, t4 = _timed(1250), _timed(2500), _timed(5000)
    assert t4 < 1.5, f"5,000 files took {t4:.2f}s"
    # near-linear: doubling the input costs well under 3x (a quadratic step would cost 4x)
    assert t2 / max(t1, 1e-3) < 3.2 and t4 / max(t2, 1e-3) < 3.2, (t1, t2, t4)


@pytest.mark.parametrize("path", ["src/cairn/ui/app/components/archmap.js"])
def test_component_is_titled_dependency_layers(path):
    """FR-026: the component says "Dependency layers", not "Architecture"."""
    text = (Path(__file__).resolve().parents[2] / path).read_text(encoding="utf-8")
    assert "Dependency layers" in text and "Architecture" not in text

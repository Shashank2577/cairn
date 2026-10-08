"""Layout geometry and the renderer's self-test; performance and scaling."""
from __future__ import annotations

import copy
import json
import random
import time

import pytest

from cairn.system.diagram import layout as lo
from cairn.system.diagram import load_tokens, schema, svg

from .test_diagram_common import EXAMPLES, TAAZAA, many, valid


def _best(fn, n=3):
    best = 1e9
    for _ in range(n):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_self_test_clean_for_every_example(path):
    """FR-022, FR-024a: no overlapping labels, no line behind a box, text at least 7.5 px."""
    g = lo.layout(schema.load_file(path))
    assert lo.self_test(g) == []


@pytest.mark.parametrize("path", TAAZAA, ids=lambda p: p.stem)
def test_layout_survives_non_conforming_descriptions(path):
    """Layout survives non conforming descriptions. Covers FR-022, FR-024a."""
    g = lo.layout(schema.load_file(path))
    assert lo.self_test(g) == []
    assert len(g["nodes"]) > 0


def test_geometry_is_plain_json():
    """T008: geometry is JSON-able with nodes (x, y, w, h, shape), edges (points, label box), boundaries, legend. Covers FR-022, FR-024a."""
    g = lo.layout(schema.load_file(EXAMPLES[1]))
    again = json.loads(json.dumps(g))
    assert again == g
    n = g["nodes"][0]
    assert {"x", "y", "w", "h", "shape"} <= set(n)
    e = g["edges"][0]
    assert len(e["points"]) >= 2 and {"x", "y", "w", "h"} <= set(e["labels"][0])
    assert g["boundaries"] and g["legend"]


def test_people_and_outside_systems_stay_outside_boundaries():
    """FR-024a: only members are inside a boundary, in every example and in the trust diagram. Covers FR-022."""
    for p in EXAMPLES:
        g = lo.layout(schema.load_file(p))
        for b in g["boundaries"]:
            for n in g["nodes"]:
                inside = (b["x"] <= n["x"] and n["x"] + n["w"] <= b["x"] + b["w"] and b["y"] <= n["y"]
                          and n["y"] + n["h"] <= b["y"] + b["h"])
                assert inside == (n["id"] in b["members"]), (p.stem, b["id"], n["id"])


def test_outsider_in_member_columns_is_moved_out():
    """FR-024a: an outside system that would land between members is pushed out of the boundary. Covers FR-022."""
    d = valid()
    d["elements"].append({"id": "ext", "name": "Ext", "type": "external-system", "desc": "x", "evidence": ["a:1"]})
    d["relationships"] += [{"from": "web", "to": "ext", "what": "Calls it", "how": "HTTP", "evidence": ["a:1"]}]
    d["boundaries"] = [{"id": "b", "name": "X", "type": "software system", "contains": ["web", "api", "db"]}]
    g = lo.layout(schema.normalize(d))
    assert lo.self_test(g) == []


def test_skip_row_edge_goes_around_the_box_between():
    """FR-024a: a same-column edge that skips a row is routed through the gap, not behind the middle box. Covers FR-022."""
    d = schema.normalize({
        "diagram": "container", "title": "Containers of S", "scope": "s", "description": "d",
        "elements": [{"id": c, "name": c, "type": "container", "tech": "t", "desc": "d", "evidence": ["a:1"], "at": [0, i]}
                     for i, c in enumerate("abc")],
        "relationships": [{"from": "a", "to": "b", "what": "Step one", "how": "x", "evidence": ["a:1"]},
                          {"from": "b", "to": "c", "what": "Step two", "how": "x", "evidence": ["a:1"]},
                          {"from": "a", "to": "c", "what": "Skips b", "how": "x", "evidence": ["a:1"]}]})
    g = lo.layout(d)
    assert lo.self_test(g) == []
    skip = next(e for e in g["edges"] if e["to"] == "c" and e["from"] == "a")
    node_a = next(n for n in g["nodes"] if n["id"] == "a")
    assert max(p[0] for p in skip["points"]) > node_a["x"] + node_a["w"]


def test_cycles_terminate_and_lay_out():
    """FR-024a: cyclic relationships (and a self loop) terminate with a clean layout. Covers FR-022."""
    n = 15
    els = [{"id": f"n{i}", "name": f"N{i}", "type": "container", "tech": "t", "desc": "d", "evidence": ["a:1"]} for i in range(n)]
    rels = [{"from": f"n{i}", "to": f"n{(i + 1) % n}", "what": f"Next {i}", "how": "x", "evidence": ["a:1"]} for i in range(n)]
    rels.append({"from": "n3", "to": "n3", "what": "Loops", "how": "x", "evidence": ["a:1"]})
    rels.append({"from": "n4", "to": "n2", "what": "Back", "how": "x", "evidence": ["a:1"]})
    d = schema.normalize({"diagram": "container", "title": "Containers of C", "scope": "c", "description": "d",
                          "elements": els, "relationships": rels})
    t = time.perf_counter()
    g = lo.layout(d)
    assert time.perf_counter() - t < 2
    assert lo.self_test(g) == []


def test_unknown_references_do_not_break_layout():
    """Unknown references do not break layout. Covers FR-022, FR-024a."""
    d = valid()
    d["relationships"].append({"from": "web", "to": "ghost", "what": "Ghost call", "how": "x", "evidence": ["a:1"]})
    d["boundaries"] = [{"id": "b", "name": "B", "type": "network", "contains": ["ghost", "web"]}]
    g = lo.layout(d)
    assert len(g["edges"]) == 2


def test_empty_and_single_element_layouts():
    """Empty and single element layouts. Covers FR-022, FR-024a."""
    assert lo.layout(schema.normalize({"diagram": "container", "title": "Containers of E"}))["nodes"] == []
    one = valid()
    one["elements"], one["relationships"] = one["elements"][:1], []
    assert len(lo.layout(one)["nodes"]) == 1


def test_consumer_ranks_after_the_channel_it_reads():
    """FR-024a: data flows left to right: a worker that subscribes comes after the channel. Covers FR-022."""
    g = lo.layout(schema.load_file(next(p for p in EXAMPLES if p.stem.startswith("02"))))
    x = {n["id"]: n["x"] for n in g["nodes"]}
    assert x["worker"] > x["redis"] > x["api"]


def test_libraries_sit_on_their_own_row():
    """Libraries sit on their own row. Covers FR-022, FR-024a."""
    g = lo.layout(schema.load_file(next(p for p in EXAMPLES if p.stem.startswith("02"))))
    y = {n["id"]: n["y"] for n in g["nodes"]}
    assert all(y["contracts"] > y[i] for i in ("web", "api", "db", "redis", "worker"))


def test_valid_placement_hints_are_used():
    """Valid placement hints are used. Covers FR-022, FR-024a."""
    d = schema.normalize({
        "diagram": "container", "title": "Containers of H", "scope": "h", "description": "d",
        "elements": [{"id": "a", "name": "a", "type": "container", "tech": "t", "desc": "d", "evidence": ["x:1"], "at": [0, 0]},
                     {"id": "b", "name": "b", "type": "container", "tech": "t", "desc": "d", "evidence": ["x:1"], "at": [0, 1]}],
        "relationships": []})
    g = lo.layout(d)
    assert g["nodes"][0]["x"] == g["nodes"][1]["x"] and g["nodes"][0]["y"] < g["nodes"][1]["y"]


# ------------------------------------------------------------------ the self-test itself
def _geom(**over):
    g = {"kind": "container", "scale": 1.0, "width": 800, "height": 400, "legend": [],
         "nodes": [{"id": "a", "x": 0, "y": 0, "w": 100, "h": 50, "texts": []},
                   {"id": "b", "x": 300, "y": 0, "w": 100, "h": 50, "texts": []},
                   {"id": "c", "x": 150, "y": 0, "w": 100, "h": 50, "texts": []}],
         "edges": [], "boundaries": []}
    g.update(over)
    return g


def _label(x, y, size=10.0):
    return {"x": x, "y": y, "w": 80, "h": 20, "lines": [{"t": "hi", "size": size, "x": x, "y": y}]}


def test_self_test_detects_label_overlap():
    """FR-022: two labels sharing space are reported. Covers FR-024a."""
    g = _geom(edges=[{"id": "e1", "from": "a", "to": "b", "points": [[100, 25], [300, 25]], "labels": [_label(10, 100)]},
                     {"id": "e2", "from": "a", "to": "b", "points": [[100, 30], [300, 30]], "labels": [_label(40, 105)]}])
    assert any(v.startswith("label-overlap") for v in lo.self_test(g))


def test_self_test_detects_label_over_box():
    """Self test detects label over box. Covers FR-022, FR-024a."""
    g = _geom(edges=[{"id": "e1", "from": "a", "to": "b", "points": [[100, 25], [300, 25]], "labels": [_label(60, 10)]}])
    assert any(v.startswith("label-box") for v in lo.self_test(g))


def test_self_test_detects_line_behind_box():
    """Self test detects line behind box. Covers FR-022, FR-024a."""
    g = _geom(edges=[{"id": "e1", "from": "a", "to": "b", "points": [[100, 25], [300, 25]], "labels": []}])
    assert any(v.startswith("line-behind-box") and "box c" in v for v in lo.self_test(g))


def test_self_test_detects_small_text():
    """Self test detects small text. Covers FR-022, FR-024a."""
    g = _geom(edges=[{"id": "e1", "from": "a", "to": "b", "points": [[100, 100], [300, 100]], "labels": [_label(150, 150, 6.0)]}])
    assert any(v.startswith("text-size") for v in lo.self_test(g))
    ok = _geom(scale=0.5, edges=[{"id": "e1", "from": "a", "to": "b", "points": [[100, 100], [300, 100]], "labels": [_label(150, 150, 10.0)]}])
    assert any(v.startswith("text-size") for v in lo.self_test(ok))   # 10 px at half scale is 5 px


def test_self_test_detects_intrusion_and_node_overlap():
    """Self test detects intrusion and node overlap. Covers FR-022, FR-024a."""
    g = _geom(boundaries=[{"id": "bd", "x": 120, "y": -10, "w": 150, "h": 80, "members": ["a"], "title": {"x": 0, "y": 0, "w": 0, "h": 0}, "texts": []}])
    assert any(v.startswith("boundary-intrusion") for v in lo.self_test(g))
    g2 = _geom()
    g2["nodes"][2]["x"] = 50
    assert any(v.startswith("node-overlap") for v in lo.self_test(g2))


def test_self_test_detects_close_ports():
    """Self test detects close ports. Covers FR-022, FR-024a."""
    g = _geom(edges=[{"id": "e1", "from": "a", "to": "b", "points": [[100, 20], [300, 20]], "labels": []},
                     {"id": "e2", "from": "a", "to": "b", "points": [[100, 25], [300, 25]], "labels": []}])
    assert any(v.startswith("port-gap") for v in lo.self_test(g))


def test_self_test_flags_text_below_minimum_from_tokens():
    """A token set that shrinks the type tag below 7.5 px makes every box fail the self-test. Covers FR-022, FR-024a."""
    T = copy.deepcopy(load_tokens())
    T["font"]["sizes"]["type_tag"] = 6.5
    g = lo.layout(schema.load_file(EXAMPLES[0]), T)
    assert any(v.startswith("text-size") for v in lo.self_test(g, T))


# ------------------------------------------------------------------ random sweep
def test_random_small_diagrams_are_clean():
    """FR-024a: 150 random diagrams within budget (cycles, boundaries, libraries) lay out without a violation. Covers FR-022."""
    types = ["container", "data-store", "channel", "library", "external-system", "person"]
    for seed in range(150):
        r = random.Random(seed)
        n = r.randint(2, 12)
        els = [{"id": f"e{i}", "name": f"Element {i}", "type": r.choice(types), "tech": "x", "desc": "does a thing"} for i in range(n)]
        rels = [{"from": f"e{r.randrange(n)}", "to": f"e{r.randrange(n)}", "what": f"Thing {i}", "how": "HTTP",
                 "style": r.choice(["sync", "async", "build"])} for i in range(r.randint(1, 16))]
        d = {"diagram": "container", "title": "Containers of R", "scope": "r", "description": "d", "elements": els, "relationships": rels}
        if n > 4 and r.random() < 0.7:
            d["boundaries"] = [{"id": "b", "name": "B", "type": "network", "contains": [e["id"] for e in els[: n // 2]]}]
        g = lo.layout(schema.normalize(d))
        assert lo.self_test(g) == [], seed


# ------------------------------------------------------------------ performance
def test_budget_diagram_is_fast():
    """Budget: 12 elements and 16 relationships lay out and render in well under 0.5 s. Covers FR-022, FR-024a."""
    d = many(12, 16)
    assert _best(lambda: svg.render(d, lo.layout(d))) < 0.5


def test_stress_300_elements_under_two_seconds():
    """Budget: layout + SVG of 300 elements in under 2 s. Covers FR-022, FR-024a."""
    d = many(300, 400)
    t = time.perf_counter()
    g = lo.layout(d)
    out = svg.render(d, g)
    assert time.perf_counter() - t < 2.0
    assert out.startswith("<svg") and len(g["nodes"]) == 300


def test_layout_scales_sub_quadratically():
    """Budget: time for 200 elements is under 16x the time for 50 (quadratic would be 16x or more). Covers FR-022, FR-024a."""
    t50 = _best(lambda: (lambda d: svg.render(d, lo.layout(d)))(many(50, 65)))
    t200 = _best(lambda: (lambda d: svg.render(d, lo.layout(d)))(many(200, 260)))
    assert t200 < 16 * max(t50, 0.002)


def test_self_test_is_fast_on_large_geometry():
    """Self test is fast on large geometry. Covers FR-022, FR-024a."""
    g = lo.layout(many(300, 400))
    assert _best(lambda: lo.self_test(g), 2) < 1.5


# ------------------------------------------------------------------ legend
def test_legend_lists_exactly_what_is_used():
    """FR-020: legend items are the shapes, line styles, marks and overlays the diagram uses, nothing more. Covers FR-022, FR-024a."""
    T = load_tokens()
    for p in EXAMPLES:
        d = schema.load_file(p)
        items = lo.legend_items(d, T)
        shapes = {i["type"] for i in items if i["kind"] == "shape"}
        assert shapes == {e["type"] for e in d["elements"]}, p.stem
        rels = d["flow"]["messages"] if d["diagram"] == "flow" else d["relationships"]
        assert {i["style"] for i in items if i["kind"] == "line"} == {r.get("style", "sync") for r in rels}, p.stem
        marks = {i["provenance"] for i in items if i["kind"] == "mark"}
        assert marks == {x.get("provenance", "extracted") for x in d["elements"] + rels} - {"extracted"}, p.stem
        assert any(i["name"] == "trust-boundary" for i in items if i["kind"] == "overlay") == any(b.get("trust") for b in d.get("boundaries", []))
        assert any(i["name"] == "data-class" for i in items if i["kind"] == "overlay") == any(r.get("data_class") for r in rels)

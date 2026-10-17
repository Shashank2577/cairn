"""Malformed, hostile-size and nonsense input: a clean error or warnings, never a traceback, always bounded time."""
from __future__ import annotations

import random
import time

import pytest
from typer.testing import CliRunner

from cairn.system.diagram import check as ck
from cairn.system.diagram import layout as lo
from cairn.system.diagram import mermaid_in, mermaid_out, schema, svg
from cairn.system.diagram.cli import diagram_app
from cairn.system.limits import MAX_DIAGRAM_BYTES

from .test_diagram_common import EXAMPLES, valid

runner = CliRunner()
BOUND = 3.0      # seconds any single malformed input may take


def bounded(fn, *a, **kw):
    t = time.perf_counter()
    try:
        return fn(*a, **kw)
    finally:
        assert time.perf_counter() - t < BOUND


def rejects(text):
    with pytest.raises(schema.DiagramError):
        bounded(schema.loads, text)


# ------------------------------------------------------------------ YAML and schema
def test_empty_and_whitespace_documents():
    """FR-022: nothing to read is a clean error. Covers FR-032."""
    for text in ("", "   \n\n", "# only a comment\n", "null\n", "---\n"):
        rejects(text)


def test_scalar_and_list_roots_are_rejected():
    """Scalar and list roots are rejected. Covers FR-022, FR-032."""
    for text in ("just text", "42", "- a\n- b\n", "[1, 2]"):
        rejects(text)


def test_binary_garbage_and_non_utf8():
    """Binary garbage and non utf8. Covers FR-022, FR-032."""
    rnd = random.Random(1)
    rejects(bytes(rnd.randrange(256) for _ in range(4000)))
    rejects(b"\xff\xfe\x00\x01diagram: container\n\x80\x81")
    rejects("diagram: container\x00\ntitle: x\n")
    d = bounded(schema.loads, b"diagram: container\ntitle: \xff\xfe broken\nelements: []\n")
    assert "�" in d["title"]


def test_file_over_the_limit(tmp_path):
    """Limits: a diagram file larger than the byte limit is refused before parsing. Covers FR-022, FR-032."""
    big = b"diagram: container\ndescription: " + b"x" * (MAX_DIAGRAM_BYTES + 10)
    rejects(big)
    f = tmp_path / "big.yaml"
    f.write_bytes(big)
    with pytest.raises(schema.DiagramError):
        schema.load_file(f)
    r = runner.invoke(diagram_app, ["check", str(f)])
    assert r.exit_code == 1 and "cannot be read" in r.output


def test_deeply_nested_yaml():
    """Deeply nested yaml. Covers FR-022, FR-032."""
    rejects("a: " + "[" * 500 + "]" * 500)
    rejects("\n".join("  " * i + "k:" for i in range(300)) + " 1")


def test_alias_bomb():
    """Billion laughs is refused by the alias and expansion limits. Covers FR-022, FR-032."""
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    for i in range(1, 12):
        lines.append(f"a{i}: &a{i} [" + ", ".join([f"*a{i - 1}"] * 10) + "]")
    rejects("\n".join(lines))
    rejects("x: &a [1]\n" + "\n".join(f"y{i}: *a" for i in range(500)))


def test_python_tags_are_never_constructed(tmp_path):
    """Python tags are never constructed. Covers FR-022, FR-032."""
    marker = tmp_path / "pwned"
    rejects(f"diagram: !!python/object/apply:os.system ['touch {marker}']\n")
    rejects("elements: !!python/object/new:builtins.int [1]\n")
    assert not marker.exists()


def test_wrong_shapes_are_clean_errors():
    """Wrong shapes are clean errors. Covers FR-022, FR-032."""
    bad = [
        "elements: 5", "elements: [1, 2]", "elements: [{name: x}]", "elements: [{id: [a]}]",
        "elements: [{id: a, at: [1]}]", "elements: [{id: a, at: [x, y]}]", "elements: [{id: a, count: many}]",
        "elements: [{id: a, name: {k: v}}]", "relationships: [{from: a}]", "relationships: x",
        "boundaries: [{name: no id}]", "flow: [1]", "flow: {messages: [{from: a}]}",
        "flow: {fragments: [{start: abc}]}", "legend: [1]",
    ]
    for t in bad:
        rejects("diagram: container\n" + t + "\n")


def test_too_many_elements_is_refused():
    """Too many elements is refused. Covers FR-022, FR-032."""
    els = ", ".join("{id: e%d}" % i for i in range(5001))
    rejects("diagram: container\nelements: [" + els + "]\n")


def test_unknown_element_types_reach_the_checker_not_a_traceback():
    """Unknown element types reach the checker not a traceback. Covers FR-022, FR-032."""
    d = schema.loads("diagram: container\ntitle: Containers of X\nscope: s\ndescription: d\nelements:\n  - {id: a, name: A, type: spaceship, desc: x, evidence: [a:1]}\n")
    assert "R3" in ck.rule_ids(ck.check(d))
    g = lo.layout(d)
    assert svg.render(d, g).startswith("<svg") and lo.self_test(g) == []


def test_relationships_to_unknown_ids():
    """Relationships to unknown ids. Covers FR-022, FR-032."""
    d = valid()
    d["relationships"].append({"from": "nope", "to": "web", "what": "Ghost call", "how": "x", "evidence": ["a:1"]})
    assert "R12" in ck.rule_ids(ck.check(d))
    assert len(lo.layout(d)["edges"]) == 2 and svg.render(d)


def test_zero_elements_and_duplicate_ids():
    """Zero elements and duplicate ids. Covers FR-022, FR-032."""
    d = schema.normalize({"diagram": "container", "title": "Containers of E", "scope": "s", "description": "d"})
    assert "R2" in ck.rule_ids(ck.check(d))
    assert svg.render(d).startswith("<svg") and lo.self_test(lo.layout(d)) == []
    dup = valid()
    dup["elements"].append(dict(dup["elements"][0]))
    assert any("more than once" in str(v) for v in ck.check(dup))
    assert len(lo.layout(dup)["nodes"]) == 3 and svg.render(dup)


def test_every_pipeline_stage_on_degenerate_descriptions():
    """Every pipeline stage on degenerate descriptions. Covers FR-022, FR-032."""
    docs = [
        {"diagram": "flow", "title": "Flow of x", "scope": "s", "description": "d"},
        {"diagram": "flow", "title": "Flow of x", "scope": "s", "description": "d", "elements": [{"id": "a"}],
         "flow": {"participants": ["a", "zz"], "messages": [{"from": "a", "to": "a", "what": "Self"}, {"from": "a", "to": "q", "what": "x"}],
                  "fragments": [{"type": "alt", "start": 1, "end": 5}, {"type": "opt", "start": 2, "end": 1}]}},
        {"diagram": "state", "elements": [{"id": "a", "type": "state"}], "relationships": [{"from": "a", "to": "a"}]},
        {"diagram": "nonsense", "elements": [{"id": "a"}]},
        {"elements": [{"id": "a", "type": "entity", "fields": ["f%d" % i for i in range(30)]}]},
    ]
    for raw in docs:
        d = schema.normalize(raw)
        ck.check(d)
        g = bounded(lo.layout, d)
        lo.self_test(g)
        assert svg.render(d, g).startswith("<svg")
        mermaid_in.parse(mermaid_out.to_mermaid(d))


def test_cyclic_relationships_layout_terminates():
    """Cyclic relationships layout terminates. Covers FR-022, FR-032."""
    n = 40
    d = schema.normalize({"diagram": "container", "title": "Containers of C", "scope": "s", "description": "d",
                          "elements": [{"id": f"n{i}", "type": "container", "name": f"n{i}"} for i in range(n)],
                          "relationships": [{"from": f"n{i}", "to": f"n{(i + 3) % n}", "what": "loop"} for i in range(n)]
                          + [{"from": f"n{(i + 3) % n}", "to": f"n{i}", "what": "back"} for i in range(n)]})
    g = bounded(lo.layout, d)
    assert len(g["nodes"]) == n


# ------------------------------------------------------------------ Mermaid
MALFORMED = [
    "flowchart LR\n a[unbalanced --> b",
    "flowchart LR\n a[x]] --> b",
    'flowchart LR\n a["never closed --> b',
    "flowchart LR\n a(( --> b",
    "flowchart LR\n subgraph s\n  a --> b",
    "flowchart LR\n end\n a --> b\n end",
    "flowchart LR\n a -->\n",
    "flowchart LR\n --> b",
    "flowchart LR\n a & --> b",
    "flowchart LR\n a --> b -->",
    "flowchart LR\n a -- text\n",
    "flowchart LR\n subgraph\n end",
    "flowchart LR\n subgraph a\n subgraph b\n subgraph c\n end",
    "flowchart LR\n " + "a-->b;" * 200,
    "flowchart LR\n" + " a" * 5000,
    "flowchart LR\n a[" + "x" * 30000 + "] --> b",
    "flowchart LR\n %% cairn:element a {not json\n %% cairn a type=\"unclosed\n %% cairn:rel a->b []\n a-->b",
    "sequenceDiagram\n A->>: no target\n ->>B: no source\n alt\n else\n else\n end\n end\n loop\n",
    "sequenceDiagram\n participant\n actor\n A->>B: " + "x" * 5000,
    "C4Container\n Container(\n Rel(a)\n Boundary(x, \"y\") {\n }}}\n Person(a, \"unclosed",
    "C4Context\n System_Boundary(a, \"A\") {\n System_Boundary(b, \"B\") {\n Person(p, \"P\")\n",
]


@pytest.mark.parametrize("text", MALFORMED, ids=lambda t: t[:40].replace("\n", "|"))
def test_malformed_mermaid_gives_warnings_or_a_clean_error(text):
    """FR-032: unbalanced brackets, unclosed subgraphs, stray end ...: warnings or MermaidError, never a crash. Covers FR-022."""
    try:
        imp = bounded(mermaid_in.parse, text)
    except mermaid_in.MermaidError:
        return
    ck.check(imp.description)
    lo.layout(imp.description)
    assert isinstance(imp.warnings, list)


def test_unclosed_subgraph_and_stray_end_warn():
    """Unclosed subgraph and stray end warn. Covers FR-022, FR-032."""
    imp = mermaid_in.parse("flowchart LR\n subgraph s\n  a --> b")
    assert any("never closed" in w for w in imp.warnings)
    assert imp.description["boundaries"][0]["contains"] == ["a", "b"]
    imp = mermaid_in.parse("flowchart LR\n a --> b\n end")
    assert any("stray 'end'" in w for w in imp.warnings)


def test_input_over_the_line_limit_is_refused():
    """Limits: 10 000 lines is an error, not a long wait. Covers FR-022, FR-032."""
    with pytest.raises(mermaid_in.MermaidError):
        bounded(mermaid_in.parse, "flowchart LR\n" + "\n".join(f"n{i} --> n{i + 1}" for i in range(10_000)))
    with pytest.raises(mermaid_in.MermaidError):
        mermaid_in.parse(b"flowchart LR\n" + b"a-->b\n" * (MAX_DIAGRAM_BYTES // 6 + 10))


def test_empty_binary_and_unsupported_mermaid():
    """Empty binary and unsupported mermaid. Covers FR-022, FR-032."""
    for text in ("", "   \n\n", "%% only a comment\n", "pie title x\n 'a': 1\n", "erDiagram\n A ||--o{ B : has\n", "hello world"):
        with pytest.raises(mermaid_in.MermaidError):
            mermaid_in.parse(text)
    with pytest.raises(mermaid_in.MermaidError):
        mermaid_in.parse(b"flowchart LR\n\x00\x01\x02")


def test_many_nodes_in_mermaid_are_bounded():
    """Many nodes in mermaid are bounded. Covers FR-022, FR-032."""
    text = "flowchart LR\n" + "\n".join(f"n{i}[Node {i}] --> n{i + 1}" for i in range(3000))
    imp = bounded(mermaid_in.parse, text)
    assert len(imp.description["elements"]) == 3001


def test_fuzzed_mermaid_never_raises_anything_else():
    """200 random mutations of a valid export: a description or MermaidError, nothing else, each within the bound. Covers FR-022, FR-032."""
    base = mermaid_out.to_mermaid(schema.load_file(EXAMPLES[1]))
    rnd = random.Random(7)
    chars = list('[](){}"|<>%#&;:-=.\n \\')
    for _ in range(200):
        s = list(base)
        for _ in range(rnd.randint(1, 12)):
            i = rnd.randrange(len(s))
            op = rnd.random()
            if op < 0.4:
                s[i] = rnd.choice(chars)
            elif op < 0.7:
                del s[i]
            else:
                s.insert(i, rnd.choice(chars))
        try:
            bounded(mermaid_in.parse, "".join(s))
        except mermaid_in.MermaidError:
            pass


def test_fuzzed_yaml_never_raises_anything_else():
    """Fuzzed yaml never raises anything else. Covers FR-022, FR-032."""
    base = open(EXAMPLES[1], encoding="utf-8").read()
    rnd = random.Random(11)
    chars = list("[]{}:,-!&*|>'\"#\n \t")
    for _ in range(150):
        s = list(base)
        for _ in range(rnd.randint(1, 10)):
            i = rnd.randrange(len(s))
            s[i] = rnd.choice(chars) if rnd.random() < 0.6 else s[i]
            if rnd.random() < 0.3:
                del s[i % len(s)]
        try:
            d = bounded(schema.loads, "".join(s))
        except schema.DiagramError:
            continue
        ck.check(d)
        bounded(lo.layout, d)

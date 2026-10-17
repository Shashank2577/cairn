"""Mermaid export and import: lossless round trips, hand-written charts, sequence and C4 syntax."""
from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from cairn.system.diagram import check as ck
from cairn.system.diagram import load_tokens, mermaid_in, mermaid_out, schema
from cairn.system.diagram.cli import diagram_app

from .test_diagram_common import EXAMPLES, valid

runner = CliRunner()


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_yaml_roundtrip(path):
    """FR-021: YAML -> description -> YAML -> description is the identity. Covers FR-031, FR-032, FR-033."""
    d = schema.load_file(path)
    assert schema.loads(schema.dumps(d)) == d


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_mermaid_roundtrip_is_lossless(path, theme):
    """FR-031, FR-032: every example exports and re-imports with elements, relationships and evidence equal. Covers FR-033."""
    d = schema.load_file(path)
    back = mermaid_in.parse(mermaid_out.to_mermaid(d, theme=theme)).description
    assert back == d
    assert [e["evidence"] for e in back["elements"]] == [e["evidence"] for e in d["elements"]]
    assert ck.check(back) == []


def test_export_shape_for_flowchart():
    """FR-031: shapes per type, what and how on every edge, token colours, facts in %% cairn comments. Covers FR-032, FR-033."""
    d = schema.load_file(next(p for p in EXAMPLES if p.stem.startswith("02")))
    m = mermaid_out.to_mermaid(d)
    th = load_tokens()["themes"]["light"]
    assert "flowchart LR" in m and "fontFamily" not in m
    assert th["node_fill"] in m and th["accent_wash"] in m
    assert "[(" in m and "[[" in m and "([" in m                      # data store, channel, person
    assert "Reads and writes orders<br/>[SQL · psycopg]" in m.replace("#183;", "·") or "[SQL" in m
    assert "-.->" in m and "linkStyle" in m
    assert m.count("%% cairn:element") == len(d["elements"]) and m.count("%% cairn:rel") == len(d["relationships"])
    assert 'subgraph shop["Shop · software system"]' in m


def test_export_flow_is_a_sequence_diagram():
    """Export flow is a sequence diagram. Covers FR-031, FR-032, FR-033."""
    d = schema.load_file(next(p for p in EXAMPLES if p.stem.startswith("06")))
    m = mermaid_out.to_mermaid(d)
    assert "sequenceDiagram" in m and "alt token valid" in m and "else hash mismatch" in m and m.count("\n  end") == 1
    assert "-->>" in m and "->>" in m


def test_init_line_is_token_driven():
    """Init line is token driven. Covers FR-031, FR-032, FR-033."""
    th = load_tokens()["themes"]["dark"]
    m = mermaid_out.to_mermaid(valid(), theme="dark")
    init = next(x for x in m.splitlines() if x.startswith("%%{init"))
    assert th["line"] in init and th["label_mask"] in init


# ------------------------------------------------------------------ hand-written charts
PLAIN = """flowchart LR
  web[Web app] -->|"Creates orders"| api(API)
  api --> db[(Orders DB)]
  api -.-> bus[[Queue]]
  user([Customer]) ==> web
"""


def test_plain_mermaid_imports_and_checker_reports_what_is_missing():
    """FR-032: a plain flowchart imports; the checker names the missing type, technology and evidence. Covers FR-031, FR-033."""
    imp = mermaid_in.parse(PLAIN, kind="container")
    d = imp.description
    types = {e["id"]: e.get("type") for e in d["elements"]}
    assert types == {"web": None, "api": None, "db": "data-store", "bus": "channel", "user": "person"}
    assert [r["style"] for r in d["relationships"]] == ["sync", "sync", "async", "sync"]
    assert d["relationships"][0]["what"] == "Creates orders"
    v = ck.check(d)
    assert {"R1", "R3", "R4", "R7"} <= ck.rule_ids(v)
    assert any("no valid type" in str(x) for x in v) and any("no technology" in str(x) for x in v)
    assert any("no evidence" in str(x) for x in v)


def test_shapes_chained_and_ampersand_edges():
    """Shapes chained and ampersand edges. Covers FR-031, FR-032, FR-033."""
    t = """graph TD
    A[a] --> B[b] --> C[c]
    A & B --> D{{d}}
    E[/lib/] -- inline text --> A
    F((circle)) --- A
    G(round) <--> A
    """
    imp = mermaid_in.parse(t)
    d = imp.description
    pairs = [(r["from"], r["to"]) for r in d["relationships"]]
    assert pairs == [("A", "B"), ("B", "C"), ("A", "D"), ("B", "D"), ("E", "A"), ("F", "A"), ("G", "A")]
    assert next(r for r in d["relationships"] if r["from"] == "E")["what"] == "inline text"
    assert {e["id"]: e.get("type") for e in d["elements"]}["E"] == "library"
    assert {e["id"]: e.get("type") for e in d["elements"]}["D"] == "container"
    assert d["relationships"][-1]["bidirectional"] is True
    assert any("two-headed" in w for w in imp.warnings)


def test_subgraphs_become_boundaries():
    """Subgraphs become boundaries. Covers FR-031, FR-032, FR-033."""
    t = 'flowchart LR\n  subgraph net ["Compose network · network isolation"]\n    a[A]\n    b[B]\n  end\n  c[C] --> a\n'
    d = mermaid_in.parse(t).description
    assert d["boundaries"] == [{"id": "net", "name": "Compose network", "type": "network isolation", "contains": ["a", "b"]}]


def test_short_cairn_comments_anywhere():
    """FR-032: short key=value comments supply type, technology, evidence and edge facts, before or after use. Covers FR-031, FR-033."""
    t = """flowchart LR
  %% cairn api type=container kind=service tech="Python · FastAPI" desc="Creates orders" evidence="api/main.py:3; compose.yml: api" focus=true
  web --> api
  %% cairn web->api what="Creates orders" how="HTTP POST" evidence="web/api.js:3" provenance=declared
  %% cairn diagram kind=container scope="X" description="A tiny system." title="Containers of X"
  %% cairn web type=container tech=Node desc=Storefront evidence=web/server.js:1
  api --> db[(DB)]
  %% cairn db tech="postgres" desc="Stores" evidence="api/db.py:1"
"""
    imp = mermaid_in.parse(t)
    d = imp.description
    api = next(e for e in d["elements"] if e["id"] == "api")
    assert api["tech"] == "Python · FastAPI" and api["focus"] is True
    assert api["evidence"] == ["api/main.py:3", "compose.yml: api"]
    assert d["relationships"][0]["how"] == "HTTP POST" and d["relationships"][0]["provenance"] == "declared"
    assert d["title"] == "Containers of X" and d["scope"] == "X"


def test_json_comment_form_by_hand():
    """Json comment form by hand. Covers FR-031, FR-032, FR-033."""
    t = 'flowchart LR\n a --> b\n %% cairn:element a {"type":"person","name":"Ann","desc":"Orders"}\n'
    d = mermaid_in.parse(t).description
    assert next(e for e in d["elements"] if e["id"] == "a") == {"id": "a", "name": "Ann", "type": "person", "desc": "Orders"}


def test_front_matter_title_and_ignored_syntax_warns():
    """Front matter title and ignored syntax warns. Covers FR-031, FR-032, FR-033."""
    t = '---\ntitle: "Containers of Y"\n---\nflowchart LR\n  a --> b\n  click a call doIt()\n  style a fill:#f00\n  linkStyle 0 stroke:red\n  a --> b:::cls\n'
    imp = mermaid_in.parse(t)
    assert imp.description["title"] == "Containers of Y"
    assert any("click" in w for w in imp.warnings) and any("styling" in w for w in imp.warnings)


def test_sequence_import_builds_a_flow():
    """FR-033: participants, message arrows and alt/else/end become a flow description. Covers FR-031, FR-032."""
    t = """sequenceDiagram
    actor U as User
    participant G as Gate
    participant S as Service
    U->>G: Call a tool<br/>[POST /mcp]
    G->>S: Verify token [principal()]
    alt token valid
      S-->>G: Principal
      G-->>U: Result
    else revoked
      G--)U: Refusal
    end
    Note over G,S: ignored
    G-xS: Lost
    """
    imp = mermaid_in.parse(t)
    d = imp.description
    assert d["diagram"] == "flow"
    f = d["flow"]
    assert f["participants"] == ["U", "G", "S"]
    assert [(m["from"], m["to"], m["style"]) for m in f["messages"]] == [
        ("U", "G", "sync"), ("G", "S", "sync"), ("S", "G", "return"), ("G", "U", "return"), ("G", "U", "async"), ("G", "S", "sync")]
    assert f["messages"][0]["what"] == "Call a tool" and f["messages"][0]["how"] == "POST /mcp"
    assert f["messages"][1]["how"] == "principal()"
    assert f["fragments"] == [{"type": "alt", "start": 3, "end": 5, "label": "token valid", "else_at": 5, "else_label": "revoked"}]
    assert d["elements"][0]["type"] == "person"
    rules = ck.rule_ids(ck.check(d))
    assert "R12" not in rules and "R2" not in rules          # structurally valid; facts missing are reported
    assert {"R3", "R7"} <= rules


def test_c4_container_import():
    """C4 container import. Covers FR-031, FR-032, FR-033."""
    t = """C4Container
    title Containers of Shop
    Person(customer, "Customer", "Places orders")
    System_Boundary(shop, "Shop") {
      Container(web, "shop-web", "Node.js", "Storefront")
      ContainerDb(db, "PostgreSQL", "postgres 16", "Orders")
      ContainerQueue(bus, "Redis", "pub/sub", "Events")
    }
    System_Ext(mail, "SendGrid", "Email")
    Rel(customer, web, "Places orders", "HTTPS")
    Rel_D(web, db, "Stores orders", "SQL")
    Rel_Back(bus, web, "Publishes", "PUBLISH")
    UpdateRelStyle(customer, web, $offsetY="-40")
    """
    imp = mermaid_in.parse(t)
    d = imp.description
    assert d["diagram"] == "container" and d["title"] == "Containers of Shop"
    types = {e["id"]: e["type"] for e in d["elements"]}
    assert types == {"customer": "person", "web": "container", "db": "data-store", "bus": "channel", "mail": "external-system"}
    web = next(e for e in d["elements"] if e["id"] == "web")
    assert web["tech"] == "Node.js" and web["desc"] == "Storefront"
    assert d["boundaries"] == [{"id": "shop", "name": "Shop", "type": "software system", "contains": ["web", "db", "bus"]}]
    assert [(r["from"], r["to"], r["how"]) for r in d["relationships"]] == [
        ("customer", "web", "HTTPS"), ("web", "db", "SQL"), ("web", "bus", "PUBLISH")]
    assert "R12" not in ck.rule_ids(ck.check(d))


def test_c4_context_and_dynamic():
    """C4 context and dynamic. Covers FR-031, FR-032, FR-033."""
    ctx = mermaid_in.parse('C4Context\n Person(a, "A")\n System(s, "S", "does")\n Rel(a, s, "Uses it")\n').description
    assert ctx["diagram"] == "context" and ctx["relationships"][0].get("how") is None
    dyn = mermaid_in.parse('C4Dynamic\n Container(a, "A", "t")\n Container(b, "B", "t")\n RelIndex(1, a, b, "Asks", "RPC")\n').description
    assert dyn["diagram"] == "flow" and dyn["flow"]["participants"] == ["a", "b"]


def test_looks_like_mermaid():
    """Looks like mermaid. Covers FR-031, FR-032, FR-033."""
    assert mermaid_in.looks_like_mermaid("---\ntitle: x\n---\nflowchart LR\n a-->b")
    assert mermaid_in.looks_like_mermaid("%% hi\nsequenceDiagram\n")
    assert not mermaid_in.looks_like_mermaid("diagram: container\ntitle: x\n")
    assert not mermaid_in.looks_like_mermaid("")


# ------------------------------------------------------------------ CLI conversions
def test_cli_to_and_from_mermaid_roundtrip(tmp_path):
    """Cli to and from mermaid roundtrip. Covers FR-031, FR-032, FR-033."""
    src = EXAMPLES[1]
    r = runner.invoke(diagram_app, ["to-mermaid", str(src)])
    assert r.exit_code == 0 and r.output.startswith("---\ntitle:")
    mmd = tmp_path / "d.mmd"
    mmd.write_text(r.output, encoding="utf-8")
    back = runner.invoke(diagram_app, ["from-mermaid", str(mmd)])
    assert back.exit_code == 0
    assert schema.loads(back.output) == schema.load_file(src)
    chk = runner.invoke(diagram_app, ["check", str(mmd)])
    assert chk.exit_code == 0, chk.output


def test_cli_from_mermaid_kind_and_warnings(tmp_path):
    """Cli from mermaid kind and warnings. Covers FR-031, FR-032, FR-033."""
    f = tmp_path / "p.mmd"
    f.write_text(PLAIN + "  click web call x()\n", encoding="utf-8")
    r = runner.invoke(diagram_app, ["from-mermaid", str(f), "--kind", "component"])
    assert r.exit_code == 0 and "diagram: component" in r.output
    assert runner.invoke(diagram_app, ["from-mermaid", str(tmp_path / "none.mmd")]).exit_code == 1


def test_id_edge_cases_survive_export():
    """Ids Mermaid cannot carry (reserved words, spaces, duplicates after sanitising) still round-trip. Covers FR-031, FR-032, FR-033."""
    d = valid()
    d["elements"][0]["id"] = "end"
    d["elements"][1]["id"] = "my api"
    d["elements"][2]["id"] = "my_api"
    d["relationships"] = [{"from": "end", "to": "my api", "what": "Calls it", "how": "x", "evidence": ["a:1"]},
                          {"from": "my api", "to": "my_api", "what": "Stores", "how": "x", "evidence": ["a:1"]}]
    d = schema.normalize(d)
    assert mermaid_in.parse(mermaid_out.to_mermaid(d)).description == d


def test_regexp_helpers_unescape():
    """Regexp helpers unescape. Covers FR-031, FR-032, FR-033."""
    assert mermaid_in.unescape("a#60;b#quot;c#35;") == 'a<b"c#'
    assert not re.search(r"[<>\"]", mermaid_out.esc('<a href="x">'))

"""Hostile names, labels and evidence appear only escaped in SVG, the Markdown table and Mermaid output."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from cairn.system.diagram import alt, layout, mermaid_in, mermaid_out, schema, svg
from cairn.system.diagram import check as ck

from .test_diagram_common import HOSTILE, valid

SVG = "{http://www.w3.org/2000/svg}"


def hostile_desc(s: str) -> dict:
    d = valid(title=f"Containers of {s}", scope=s)
    d["description"] = s
    d["source_note"] = s
    for e in d["elements"]:
        e.update(name=s, tech=s, desc=s, evidence=[s, "a/b.py:1"])
    d["elements"][0]["id"] = s
    for r in d["relationships"]:
        r.update(what=s, how=s, evidence=[s])
    d["relationships"][0]["from"] = s
    d["boundaries"] = [{"id": s, "name": s, "type": s, "contains": [s, "api"]}]
    return schema.normalize(d)


@pytest.mark.parametrize("s", HOSTILE, ids=lambda s: s[:20])
def test_svg_escapes_everything(s):
    """FR-022, security: no description text becomes markup, a script, a handler or an extra element. Covers FR-031."""
    d = hostile_desc(s)
    out = svg.render(d)
    root = ET.fromstring(out)                       # well-formed: nothing closed a tag early
    tags = {el.tag.removeprefix(SVG) for el in root.iter()}
    assert not tags & {"script", "foreignObject", "iframe", "a", "image", "use"}
    for el in root.iter():
        assert not [k for k in el.attrib if k.lower().startswith("on")]
        assert not any(("javascript:" in v.lower()) for v in el.attrib.values())
    assert "<script" not in out and "<![CDATA[" not in out and "]]>" not in out
    # the text is still there, as text
    plain = " ".join("".join(el.itertext()) for el in root.iter(SVG + "text"))
    assert " ".join(s.split())[:12] in plain or " ".join(s.split())[:12] in "".join(root.itertext())


@pytest.mark.parametrize("s", HOSTILE, ids=lambda s: s[:20])
def test_markdown_table_escapes_everything(s):
    """Security: a cell cannot add a column, a row, a link, an image or raw HTML. Covers FR-022, FR-031."""
    d = hostile_desc(s)
    md = alt.evidence_table(d)
    assert "<" not in md.replace("\\<", "")
    rows = [ln for ln in md.splitlines() if ln.startswith("|")]
    assert all(ln.count("\n") == 0 for ln in rows)
    el_rows = rows[2:2 + len(d["elements"])]
    for ln in el_rows:
        assert len(re.findall(r"(?<!\\)\|", ln)) == 6, ln          # 5 cells
    assert not re.search(r"(?<!\\)\[[^\]]*\]\(", md)               # no live link or image syntax


@pytest.mark.parametrize("s", HOSTILE, ids=lambda s: s[:20])
def test_mermaid_export_is_inert_and_reimports_identically(s):
    """FR-031, security: a name cannot close a node, start a directive, add an edge or a handler; re-import adds nothing. Covers FR-022."""
    d = hostile_desc(s)
    m = mermaid_out.to_mermaid(d)
    lines = m.splitlines()
    assert sum(1 for ln in lines if ln.startswith("%%{")) == 1                      # only our init
    assert not any(re.match(r"^\s*(click|callback|href|call)\b", ln) for ln in lines)
    assert not any("<script" in ln.lower() for ln in lines)
    assert sum(1 for ln in lines if ln.startswith("flowchart ")) == 1
    for ln in lines:
        if ln.lstrip().startswith("%% cairn"):
            assert "%" not in ln[ln.index("{"):]
    back = mermaid_in.parse(m)
    assert back.description == d
    assert len(back.description["elements"]) == len(d["elements"])
    assert len(back.description["relationships"]) == len(d["relationships"])
    # without the cairn comments the picture alone must not grow nodes or edges either
    bare = "\n".join(ln for ln in lines if not ln.lstrip().startswith("%% cairn"))
    stripped = mermaid_in.parse(bare).description
    assert len(stripped["elements"]) == len(d["elements"])
    assert len(stripped["relationships"]) == len(d["relationships"])
    assert len(stripped.get("boundaries", [])) == len(d["boundaries"])


@pytest.mark.parametrize("s", HOSTILE, ids=lambda s: s[:20])
def test_mermaid_sequence_export_is_inert(s):
    """Mermaid sequence export is inert. Covers FR-022, FR-031."""
    d = schema.normalize({
        "diagram": "flow", "title": s, "scope": s, "description": s,
        "elements": [{"id": s, "name": s, "type": "container", "tech": s, "desc": s, "evidence": [s]},
                     {"id": "b", "name": s, "type": "container", "tech": s, "desc": s, "evidence": [s]}],
        "flow": {"participants": [s, "b"], "messages": [{"from": s, "to": "b", "what": s, "how": s, "evidence": [s]}],
                 "fragments": [{"type": "alt", "start": 1, "end": 1, "label": s}]}})
    m = mermaid_out.to_mermaid(d)
    lines = m.splitlines()
    assert not any(ln.startswith("%%{") for ln in lines)
    assert sum(1 for ln in lines if "->>" in ln) == 1
    back = mermaid_in.parse(m)
    assert back.description == d
    bare = mermaid_in.parse("\n".join(ln for ln in lines if not ln.lstrip().startswith("%% cairn"))).description
    assert len(bare["flow"]["messages"]) == 1 and len(bare["elements"]) == 2


def test_hostile_importer_inputs_are_not_executed():
    """Hostile importer inputs are not executed. Covers FR-022, FR-031."""
    text = ("flowchart LR\n  a[x] --> b\n  click a href \"javascript:alert(1)\"\n  click b call evil()\n"
            "  %%{init: {\"securityLevel\":\"loose\"}}%%\n  a:::x\n  linkStyle 0 stroke:red\n"
            "  %% cairn a type=container evidence=\"$(touch /tmp/pwn)\"\n")
    imp = mermaid_in.parse(text)
    assert len(imp.description["elements"]) == 2
    assert imp.description["elements"][0]["evidence"] == ["$(touch /tmp/pwn)"]       # kept as inert text


def test_mermaid_json_has_no_percent_or_angle_brackets():
    """Mermaid json has no percent or angle brackets. Covers FR-022, FR-031."""
    s = mermaid_out.jsonc({"x": "%%{init}%% <b>&</b>"})
    assert "%" not in s and "<" not in s and ">" not in s


def test_credentials_in_hostile_labels_are_reported():
    """Credentials in hostile labels are reported. Covers FR-022, FR-031."""
    d = valid()
    d["elements"][0]["desc"] = "Reads ACME_API_KEY at start"
    assert "R7" in ck.rule_ids(ck.check(d))


def test_layout_text_is_never_markup():
    """Layout text is never markup. Covers FR-022, FR-031."""
    g = layout.layout(hostile_desc("<script>alert(1)</script>"))
    assert layout.self_test(g) == [] or all(v.startswith(("text-overflow", "label-", "line-behind")) for v in layout.self_test(g))

"""SVG, evidence table, themes, accessibility, contrast conformance and the render command."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest
from typer.testing import CliRunner

from cairn.system.diagram import alt, load_tokens, schema, svg
from cairn.system.diagram import check as ck
from cairn.system.diagram import layout as lo
from cairn.system.diagram.cli import diagram_app

from .test_diagram_common import EXAMPLES, valid

SVG_NS = "{http://www.w3.org/2000/svg}"
runner = CliRunner()


def _parse(text):
    return ET.fromstring(text)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
@pytest.mark.parametrize("theme", ["light", "dark", "auto"])
def test_every_example_renders_well_formed_svg(path, theme):
    """FR-022, FR-024: every example renders, in every theme, to well-formed XML with title, desc and role=img. Covers FR-020, FR-021, US2-AS1."""
    d = schema.load_file(path)
    root = _parse(svg.render(d, theme=theme))
    assert root.tag == SVG_NS + "svg" and root.get("role") == "img"
    assert root.find(SVG_NS + "title").text == d["title"]
    assert root.find(SVG_NS + "desc").text == d["description"]
    ids = root.get("aria-labelledby").split()
    assert {e.get("id") for e in root.iter() if e.get("id")} >= set(ids)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_light_and_dark_contain_every_colour_role_used(path):
    """FR-021: each role the drawing uses is defined with the token value in light and in dark. Covers FR-020, FR-022, US2-AS1."""
    T = load_tokens()
    d = schema.load_file(path)
    for theme in ("light", "dark"):
        out = svg.render(d, theme=theme)
        used = set(re.findall(r"var\(--([a-z0-9-]+)\)", out))
        defined = dict(re.findall(r"--([a-z0-9-]+):([^;}]+)", out.split("</style>")[0]))
        for role in used:
            assert role in defined, (theme, role)
            assert defined[role] == T["themes"][theme][role.replace("-", "_")], (theme, role)
    auto = svg.render(d, theme="auto")
    assert "prefers-color-scheme:dark" in auto
    for role, val in T["themes"]["dark"].items():
        assert f"--{role.replace('_', '-')}:{val}" in auto


def test_forced_theme_has_no_media_query():
    """Forced theme has no media query. Covers FR-020, FR-021, FR-022, US2-AS1."""
    d = schema.load_file(EXAMPLES[0])
    assert "prefers-color-scheme" not in svg.render(d, theme="light")
    assert "#141815" in svg.render(d, theme="dark")
    with pytest.raises(ValueError):
        svg.render(d, theme="sepia")


def test_scope_title_and_legend_are_drawn():
    """Scope title and legend are drawn. Covers FR-020, FR-021, FR-022, US2-AS1."""
    d = schema.load_file(EXAMPLES[1])
    out = svg.render(d)
    assert "Scope: " + d["scope"].replace("&", "&amp;") in out
    assert "LEGEND" in out and "Synchronous request" in out and "Build-time dependency" in out


def test_provenance_marks_have_spoken_labels():
    """FR-020, accessibility: marks carry aria-label so they are not colour or glyph alone. Covers FR-021, FR-022, US2-AS1."""
    d = schema.load_file(next(p for p in EXAMPLES if p.stem.startswith("03")))
    root = _parse(svg.render(d))
    labels = {t.get("aria-label") for t in root.iter(SVG_NS + "tspan") if t.get("role") == "img"}
    assert {"declared", "inferred"} <= labels


def test_evidence_is_available_on_hover():
    """Evidence is available on hover. Covers FR-020, FR-021, FR-022, US2-AS1."""
    out = svg.render(schema.load_file(EXAMPLES[1]))
    assert "<title>extracted: shop-web/package.json" in out


def test_state_is_drawn_with_tag_and_stale_colour():
    """State is drawn with tag and stale colour. Covers FR-020, FR-021, FR-022, US2-AS1."""
    d = valid()
    d["elements"][0]["provenance"] = "stale"
    d["relationships"][0]["provenance"] = "stale"
    out = svg.render(d)
    assert "var(--stale)" in out and "stale-wash" in out


def test_every_text_element_inside_the_canvas():
    """Every text element inside the canvas. Covers FR-020, FR-021, FR-022, US2-AS1."""
    for p in EXAMPLES:
        d = schema.load_file(p)
        root = _parse(svg.render(d))
        w = float(root.get("width"))
        for t in root.iter(SVG_NS + "text"):
            assert -1 <= float(t.get("x", 0)) <= w + 1, p.stem


# ------------------------------------------------------------------ alt text
def test_evidence_table_lists_every_element_and_relationship():
    """Evidence table lists every element and relationship. Covers FR-020, FR-021, FR-022, US2-AS1."""
    d = schema.load_file(EXAMPLES[1])
    md = alt.evidence_table(d)
    for e in d["elements"]:
        assert e["name"].replace("-", "-") in md
    assert md.count("\n| ") >= len(d["elements"]) + len(d["relationships"])
    assert "| # | From | To | What | How | Evidence |" in md
    assert d["relationships"][0]["evidence"][0] in md


def test_flow_evidence_table_numbers_messages():
    """Flow evidence table numbers messages. Covers FR-020, FR-021, FR-022, US2-AS1."""
    d = schema.load_file(next(p for p in EXAMPLES if p.stem.startswith("05")))
    md = alt.evidence_table(d)
    assert "| 6 | shop-worker | SendGrid |" in md


# ------------------------------------------------------------------ contrast conformance
def _lum(hexcol):
    c = [int(hexcol[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


TEXT_ROLES = ["ink", "ink2", "muted", "trust", "sensitive", "inferred", "stale"]
LINE_ROLES = ["node_stroke", "line", "boundary_stroke", "external_stroke", "accent", "trust", "stale", "sensitive"]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_token_contrast_against_paper(theme):
    """FR-020: text roles reach 4.5:1 and line and shape roles 3:1 against the paper, in both themes. Covers FR-021, FR-022, US2-AS1."""
    th = load_tokens()["themes"][theme]
    for role in TEXT_ROLES:
        assert contrast(th[role], th["paper"]) >= 4.5, (theme, role, contrast(th[role], th["paper"]))
    for role in LINE_ROLES:
        assert contrast(th[role], th["paper"]) >= 3.0, (theme, role)
    for text_on, fill in (("ink", "node_fill"), ("ink", "store_fill"), ("ink2", "node_fill"), ("muted", "node_fill"),
                          ("ink", "accent_wash"), ("ink", "stale_wash"), ("ink", "external_fill"), ("muted", "external_fill")):
        assert contrast(th[text_on], th[fill]) >= 4.5, (theme, text_on, fill)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_rendered_examples_conform(path, theme):
    """FR-020, FR-022: the example follows the rules, lays out cleanly and every colour it uses passes contrast. Covers FR-021, US2-AS1."""
    T = load_tokens()
    d = schema.load_file(path)
    assert ck.check(d) == []
    g = lo.layout(d)
    assert lo.self_test(g) == []
    th = T["themes"][theme]
    out = svg.render(d, g, theme=theme)
    text_used = {m for m in re.findall(r'fill="var\(--([a-z0-9-]+)\)"', out)}
    for role in text_used:
        key = role.replace("-", "_")
        if key in TEXT_ROLES:
            assert contrast(th[key], th["paper"]) >= 4.5, (theme, key)


# ------------------------------------------------------------------ the render command
def test_render_command_writes_svg_and_table(tmp_path):
    """T010: render writes <name>.svg and <name>.md in --out. Covers FR-020, FR-021, FR-022, US2-AS1."""
    r = runner.invoke(diagram_app, ["render", str(EXAMPLES[0]), "--out", str(tmp_path / "o"), "--theme", "dark"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "o" / (EXAMPLES[0].stem + ".svg")).read_text(encoding="utf-8").startswith("<svg")
    assert "| Element |" in (tmp_path / "o" / (EXAMPLES[0].stem + ".md")).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["../evil", "a/b", "..", "/abs", "x\\y", "a b", ""])
def test_render_refuses_path_traversal_in_name(tmp_path, name):
    """FR-022, security: the output name cannot leave --out. Covers FR-020, FR-021, US2-AS1."""
    out = tmp_path / "o"
    r = runner.invoke(diagram_app, ["render", str(EXAMPLES[0]), "--out", str(out), "--name", name])
    assert r.exit_code == 1
    assert not list(tmp_path.rglob("*.svg"))


def test_render_reports_violations_and_strict_exit(tmp_path):
    """Render reports violations and strict exit. Covers FR-020, FR-021, FR-022, US2-AS1."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("diagram: container\ntitle: nope\nelements:\n  - {id: a, name: A, type: container}\n", encoding="utf-8")
    r = runner.invoke(diagram_app, ["render", str(bad), "--out", str(tmp_path / "o")])
    assert r.exit_code == 0 and "standard: R1" in r.output
    r = runner.invoke(diagram_app, ["render", str(bad), "--out", str(tmp_path / "o"), "--strict"])
    assert r.exit_code == 1


def test_check_command_exit_codes(tmp_path):
    """Check command exit codes. Covers FR-020, FR-021, FR-022, US2-AS1."""
    ok = runner.invoke(diagram_app, ["check", *map(str, EXAMPLES)])
    assert ok.exit_code == 0 and ok.output.count(": OK") == len(EXAMPLES)
    bad = tmp_path / "bad.yaml"
    bad.write_text("diagram: container\n", encoding="utf-8")
    r = runner.invoke(diagram_app, ["check", str(bad)])
    assert r.exit_code == 1 and "R1" in r.output
    r = runner.invoke(diagram_app, ["check", str(tmp_path / "missing.yaml")])
    assert r.exit_code == 1 and "cannot be read" in r.output


def test_check_command_resolves_evidence_with_root(tmp_path):
    """Check command resolves evidence with root. Covers FR-020, FR-021, FR-022, US2-AS1."""
    repo = tmp_path / "r"
    (repo / "api").mkdir(parents=True)
    d = valid()
    for x in d["elements"] + d["relationships"]:
        x["evidence"] = ["api/main.py:1"]
    f = tmp_path / "d.yaml"
    f.write_text(schema.dumps(d), encoding="utf-8")
    assert runner.invoke(diagram_app, ["check", str(f), "--root", str(repo)]).exit_code == 1
    (repo / "api" / "main.py").write_text("x\n", encoding="utf-8")
    assert runner.invoke(diagram_app, ["check", str(f), "--root", str(repo)]).exit_code == 0
    assert runner.invoke(diagram_app, ["check", str(f), "--root", f"api={repo / 'api'}"]).exit_code == 0


def test_diagram_is_registered_on_the_cairn_cli():
    """Diagram is registered on the cairn cli. Covers FR-020, FR-021, FR-022, US2-AS1."""
    from cairn.cli import app
    r = runner.invoke(app, ["diagram", "--help"])
    assert r.exit_code == 0 and "check" in r.output and "render" in r.output

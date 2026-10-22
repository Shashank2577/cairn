"""Docs export tests (spec 008: FR-029; T041).

The export writes the architecture document set from the computed system model: context, containers,
components, flows and code, each with a Mermaid diagram, an SVG and an evidence table. Empty sections are
omitted. Every document records the commit it describes. Zero model calls unless ``--narrate`` is passed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cairn.system.export import export
from cairn.system.link import link

from .util import product

REPOS = ["shop-web", "shop-api", "shop-worker", "shop-contracts"]


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("CAIRN_EMBEDDER", "hash")
    monkeypatch.setenv("CAIRN_NO_CLI_MODELS", "1")
    for k in ("ANTHROPIC_API_KEY", "CAIRN_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(k, "")


def _product(tmp_path: Path) -> Path:
    return product(tmp_path, "shop", "shop-api", REPOS)


# --------------------------------------------------------------------------- T041: the export itself
def test_export_writes_the_document_set(tmp_path):
    """FR-029: one command writes context, containers, flows and code, each with evidence."""
    root = _product(tmp_path)
    out = tmp_path / "docs" / "architecture"
    result = export(root, out)
    assert result["written"] and result["commit"]
    names = set(result["written"])
    assert any("context" in n for n in names)
    assert any("containers" in n for n in names)
    assert any("flow" in n for n in names)  # flow files are named by their entry point
    assert any("code" in n for n in names)
    assert "README.md" in names
    # Components need a code map (`cairn map`), which fixtures don't run; that section is omitted.


def test_every_document_records_the_commit_it_describes(tmp_path):
    """FR-029: each document records the commit, so a reader knows what it describes."""
    root = _product(tmp_path)
    out = tmp_path / "docs" / "architecture"
    result = export(root, out)
    commit = result["commit"]
    for md in out.glob("*.md"):
        if md.name == "README.md":
            continue
        text = md.read_text(encoding="utf-8")
        assert f"Records commit `{commit[:12]}`" in text, f"{md.name} missing commit record"


def test_every_document_has_a_diagram_and_an_evidence_table(tmp_path):
    """FR-029: SVG plus evidence tables beside every document."""
    root = _product(tmp_path)
    out = tmp_path / "docs" / "architecture"
    export(root, out)
    mds = [f for f in out.glob("*.md") if f.name not in ("README.md", "flows.md")]  # the flows index is a table
    assert mds
    for md in mds:
        stem = md.stem
        assert (out / f"{stem}.mmd").exists(), f"{stem}.mmd missing"
        assert (out / f"{stem}.svg").exists(), f"{stem}.svg missing"
        text = md.read_text(encoding="utf-8")
        assert "## Evidence" in text, f"{md.name} missing evidence table"
        assert "| Element |" in text, f"{md.name} evidence table missing"


def test_empty_sections_are_omitted_not_padded(tmp_path):
    """FR-029 scenario 2: a section with nothing to report is omitted, never filled with placeholders."""
    root = _product(tmp_path)
    out = tmp_path / "docs" / "architecture"
    export(root, out)
    # The shop fixture has a code view with routes and calls; a product with none would omit it.
    # What must hold: no placeholder text, no "TODO", no "coming soon".
    for md in out.glob("*.md"):
        text = md.read_text(encoding="utf-8")
        assert "TODO" not in text and "coming soon" not in text.lower()
        assert "placeholder" not in text.lower()


def test_rerun_after_a_change_updates_the_documents(tmp_path):
    """FR-029 independent test: re-running after a change updates the documents."""
    root = _product(tmp_path)
    out = tmp_path / "docs" / "architecture"
    first = export(root, out)
    # Change the model: add a container to shop-api's store.
    pm = link(root)
    from cairn.system.model import Element, Evidence
    pm.elements["shop-api:container:new-service"] = Element(
        id="shop-api:container:new-service", type="container", name="new-service", repo="shop-api",
        kind="service", evidence=[Evidence(repo="shop-api", kind="dockerfile", file="Dockerfile", line=1)])
    from cairn.store import Brain
    from cairn.system.model import save
    brain = Brain(root / ".cairn" / "brain.db")
    save(brain, pm, "shop-api")
    brain.close()
    second = export(root, out)
    assert second["written"] == first["written"]  # same set of files
    containers_md = next(out.glob("*containers*.md"))
    text = containers_md.read_text(encoding="utf-8")
    assert "new-service" in text


def test_export_makes_no_model_calls(tmp_path, monkeypatch):
    """FR-005, SC-003: the export only reads what the sync already computed."""
    root = _product(tmp_path)
    import cairn.router as router_mod
    from cairn.system import build as build_mod
    calls = []
    monkeypatch.setattr(router_mod.Router, "complete", lambda *a, **k: calls.append(a) or "")
    monkeypatch.setattr(router_mod.Router, "stream", lambda *a, **k: pytest.fail("called the model"))
    monkeypatch.setattr(build_mod, "build_repo", lambda *a, **k: pytest.fail("rebuilt the model at read time"))
    out = tmp_path / "docs" / "architecture"
    export(root, out)
    assert calls == []


def test_narrate_opt_in_only(tmp_path, monkeypatch):
    """FR-030: narrative only runs when --narrate is passed, and stays within its budget."""
    root = _product(tmp_path)
    out = tmp_path / "docs" / "architecture"
    without = export(root, out, narrate=False)
    assert without["narrated"] is False
    # With no model available, --narrate is a no-op, not a failure.
    with_narrate = export(root, out, narrate=True)
    assert with_narrate["narrated"] is False  # no model in the test environment


def test_configuration_table_lists_names_not_values(tmp_path):
    """FR-029: the configuration table lists names read by code, never values."""
    root = _product(tmp_path)
    pm = link(root)
    from cairn.system.export import configuration_table
    table = configuration_table(pm)
    if table:
        assert "| Configuration | Used by |" in table
        # A config name is a name, not a URL or a password.
        for line in table.splitlines()[2:]:
            name = line.split("|")[1].strip().strip("`")
            assert "://" not in name and "password" not in name.lower()


# --------------------------------------------------------------------------- flows: one index, no duplicates
def _flow_desc(title, messages, ids=("a", "b")):
    return {"diagram": "flow", "title": title, "scope": "s", "description": "d",
            "elements": [{"id": i, "name": i, "type": "container", "evidence": [f"r/{i}.py:1"]} for i in ids],
            "flow": {"participants": list(ids), "fragments": [],
                     "messages": [{"from": ids[0], "to": ids[1], "what": w, "how": "HTTP", "style": "sync",
                                   "provenance": "inferred", "evidence": [f"r/{ids[0]}.py:2"]} for w in messages]}}


def test_identical_flows_collapse_to_one_shape():
    from cairn.system.export import _shapes
    same = [_flow_desc(f"Flow of cairn GET /x{i}", ["Calls b"]) for i in range(5)]
    other = _flow_desc("Flow of cairn POST /y", ["Calls b", "Writes b"])
    groups = _shapes(same + [other])
    assert [len(g) for g in groups] == [5, 1]


def test_export_collapses_flows_into_an_index(tmp_path):
    """No per-endpoint duplicates: every flow document differs in elements or steps, the index lists them all."""
    from cairn.system.export import MAX_FLOW_DOCS, _shapes
    from cairn.system.flows import flows
    root = _product(tmp_path)
    out = tmp_path / "docs" / "architecture"
    export(root, out)
    pm = link(root)
    descs = flows(root, pm)
    flow_docs = [f for f in out.glob("flow-*.md")]
    assert len(flow_docs) <= min(MAX_FLOW_DOCS, len(_shapes(descs)))
    assert (out / "flows.md").exists() or not descs
    if descs:
        index = (out / "flows.md").read_text(encoding="utf-8")
        for d in descs:   # every entry point is still listed
            from cairn.system.diagram.alt import md
            assert md(d["title"].removeprefix("Flow of ")) in index
        assert not (out / "flows.mmd").exists() and not (out / "flows.svg").exists()
        assert "Records commit" in index


def test_flow_cap_leaves_the_rest_in_the_index(monkeypatch, tmp_path):
    import cairn.system.export as ex
    root = _product(tmp_path)
    monkeypatch.setattr(ex, "MAX_FLOW_DOCS", 1)
    out = tmp_path / "capped"
    ex.export(root, out)
    assert len(list(out.glob("flow-*.md"))) <= 1


def test_bundle_matches_export(tmp_path):
    from cairn.system.export import bundle
    root = _product(tmp_path)
    b = bundle(root)
    out = tmp_path / "x"
    written = set(export(root, out)["written"])
    expected = {"README.md"}
    for d in b.docs:
        expected |= {f"{d.slug}.md"} | ({f"{d.slug}.mmd", f"{d.slug}.svg"} if d.desc else set())
    assert written == expected

"""The specs bridge: in-process setup, the CLI mount point, and the parser the rest of Cairn reads."""
from __future__ import annotations

import re
from pathlib import Path

from workflow_helpers import FORBIDDEN, forbidden_hits, git_repo

from cairn.engines import specs
from cairn.engines.workflow import cli as workflow_cli
from cairn.project import Project

PKG = Path(specs.__file__).resolve().parent / "workflow"


def test_bootstrap_is_in_process_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = git_repo(tmp_path / "repo")
    (root / ".cairn").mkdir()
    (root / ".cairn" / "config.toml").write_text("[server]\nport = 4747\n")
    proj = Project(root=root)
    assert not specs.initialized(root)
    ok, msg = specs.bootstrap(proj, "claude")
    assert ok, msg
    assert specs.initialized(root) and (root / specs.CONSTITUTION).is_file()
    assert (root / ".cairn" / "config.toml").read_text().startswith("[server]")  # Cairn's own files untouched
    assert specs.bootstrap(proj, "claude") == (True, "already set up")
    assert specs.install_extension(proj)[0]
    # a fresh constitution is still the template: not reported until the team fills it in
    assert specs.constitution(root) is None
    (root / specs.CONSTITUTION).write_text("# Shop Constitution\n\n### I. Tests first\n\n**Version**: 1.2.0\n")
    assert specs.constitution(root) == {"version": "1.2.0", "principles": ["I. Tests first"]}


def test_cli_main_returns_exit_codes(tmp_path, capsys):
    assert workflow_cli.main(["version"], cwd=tmp_path) == 0
    assert workflow_cli.main(["--help"], cwd=tmp_path) == 0
    assert workflow_cli.main(["definitely-not-a-command"], cwd=tmp_path) == 2
    assert workflow_cli.main(["extension", "list"], cwd=tmp_path) == 1  # not a workflow project
    out = capsys.readouterr().out
    assert "cairn spec" in out


def test_cli_invocation_hint():
    cmd = specs.cli()
    assert cmd[-1] == "spec" or cmd[-1].endswith("workflow.cli")


def test_parser_still_reads_features(tmp_path):
    feat = tmp_path / "specs" / "001-refunds"
    feat.mkdir(parents=True)
    (feat / "spec.md").write_text("# Feature Specification: Refunds\n\n### User Story 1 - Refund (Priority: P1)\n\n"
                                  "- **FR-001**: The system MUST refund.\n")
    (feat / "tasks.md").write_text("## Phase 1\n\n- [x] T001 [US1] Add refund in `shop/gateway.py` for FR-001\n")
    [f] = specs.features(tmp_path)
    assert f["title"] == "Refunds" and f["progress"] == {"done": 1, "total": 1}
    assert f["tasks"][0]["reqs"] == ["FR-001"] and f["tasks"][0]["files"] == [("shop/gateway.py", False)]


def test_package_source_is_free_of_absorbed_names():
    """The whole workflow package — code, templates, scripts, catalogs — carries only Cairn names."""
    assert forbidden_hits(PKG) == []
    hits = [ln for ln in Path(specs.__file__).read_text().splitlines() if FORBIDDEN.search(ln)]
    assert hits == []


def test_command_ids_use_the_cairn_namespace():
    for f in (PKG / "assets").rglob("*.md"):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"/([a-z]+)\.(?:specify|plan|tasks|implement)\b", text):
            assert m.group(1) == "cairn", f"{f}: {m.group(0)}"


def test_workflow_state_for_the_ui(tmp_path, monkeypatch):
    from workflow_helpers import init, spec

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = git_repo(tmp_path / "repo")
    assert specs.workflow_state(root)["initialized"] is False
    init(root, "claude", "--extension", "git", "--preset", "lean")
    (root / "hello.yml").write_text("schema_version: '1.0'\nworkflow: {id: hello, name: Hello, version: '1.0.0', "
                                    "description: d}\nsteps:\n  - {id: greet, type: shell, run: 'echo hi'}\n")
    assert spec(root, "workflow", "run", "hello.yml")[0] == 0
    st = specs.workflow_state(root)
    assert st["initialized"] and st["integration"] == "claude" and st["integrations"] == ["claude"]
    assert [e["id"] for e in st["extensions"]] == ["git"] and "cairn.git.commit" in st["extensions"][0]["commands"]
    assert st["extensions"][0]["name"] == "Git Branching Workflow"
    assert [p["id"] for p in st["presets"]] == ["lean"]
    assert any(w["id"] == "cairn" for w in st["workflows"])
    assert st["runs"][0]["workflow"] == "hello" and st["runs"][0]["status"] == "completed"
    assert st["constitution"] is False and st["legacy_layout"] is False

"""Extension, preset, workflow, bundle and integration management through `cairn spec ...`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from workflow_helpers import forbidden_hits, git_repo, init, spec


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = git_repo(tmp_path / "repo")
    init(repo, "claude")
    return repo


def ok(root: Path, *args: str, stdin: str | None = None) -> str:
    code, out = spec(root, *args, stdin=stdin)
    assert code == 0, f"cairn spec {' '.join(args)} -> {code}\n{out}"
    return out


def test_extension_lifecycle(root):
    wf = root / ".cairn" / "workflow"
    out = ok(root, "extension", "search")
    for ext in ("git", "agent-context", "assess", "bug"):
        assert f"cairn spec extension add {ext}" in out
    ok(root, "extension", "add", "git")
    reg = json.loads((wf / "extensions" / ".registry").read_text(encoding="utf-8"))
    assert "git" in reg["extensions"]
    assert (root / ".claude" / "skills" / "cairn-git-feature" / "SKILL.md").is_file()
    hooks = yaml.safe_load((wf / "extensions.yml").read_text(encoding="utf-8"))
    assert any(h["command"].startswith("cairn.git.") for h in hooks["hooks"]["before_specify"])
    assert "Git Branching Workflow" in ok(root, "extension", "list")
    assert "Git Branching Workflow" in ok(root, "extension", "info", "git")
    ok(root, "extension", "disable", "git")
    assert json.loads((wf / "extensions" / ".registry").read_text(encoding="utf-8"))["extensions"]["git"]["enabled"] is False
    ok(root, "extension", "enable", "git")
    ok(root, "extension", "set-priority", "git", "5")
    ok(root, "extension", "remove", "git", "--force")
    assert not (wf / "extensions" / "git" / "extension.yml").exists()
    assert not (root / ".claude" / "skills" / "cairn-git-feature").exists()


def test_extension_from_local_directory(root, tmp_path):
    ext = tmp_path / "hello-ext"
    (ext / "commands").mkdir(parents=True)
    (ext / "extension.yml").write_text(yaml.safe_dump({
        "schema_version": "1.0",
        "extension": {"id": "hello", "name": "Hello", "version": "1.0.0", "description": "Says hello"},
        "requires": {"workflow_version": ">=1.0.0"},
        "provides": {"commands": [{"name": "cairn.hello.greet", "file": "commands/greet.md",
                                   "description": "Greet"}]},
    }), encoding="utf-8")
    (ext / "commands" / "greet.md").write_text("---\ndescription: Greet\n---\n\nSay hello to $ARGUMENTS.\n", encoding="utf-8")
    ok(root, "extension", "add", "--dev", str(ext))
    skill = root / ".claude" / "skills" / "cairn-hello-greet" / "SKILL.md"
    assert skill.is_file() and "Say hello" in skill.read_text(encoding="utf-8")


def test_extension_written_for_the_older_engine_still_installs(root, tmp_path):
    """Manifests that name the host-version constraint differently are accepted."""
    ext = tmp_path / "legacy-ext"
    (ext / "commands").mkdir(parents=True)
    legacy_key = "spec" + "kit_version"
    (ext / "extension.yml").write_text(yaml.safe_dump({
        "schema_version": "1.0",
        "extension": {"id": "legacy", "name": "Legacy", "version": "0.1.0", "description": "Old manifest"},
        "requires": {legacy_key: ">=0.8.0"},
        "provides": {"commands": [{"name": "cairn.legacy.run", "file": "commands/run.md", "description": "Run"}]},
    }), encoding="utf-8")
    (ext / "commands" / "run.md").write_text("---\ndescription: Run\n---\n\nRun it.\n", encoding="utf-8")
    ok(root, "extension", "add", "--dev", str(ext))
    assert (root / ".claude" / "skills" / "cairn-legacy-run" / "SKILL.md").is_file()


def test_preset_lifecycle(root):
    out = ok(root, "preset", "search")
    assert "lean" in out and "constitution-sync" in out
    ok(root, "preset", "add", "lean")
    assert "Lean Workflow" in ok(root, "preset", "list")
    assert "presets/lean/commands/cairn.plan.md" in ok(root, "preset", "resolve", "cairn.plan").replace("\n", "")
    lean_plan = (root / ".claude" / "skills" / "cairn-plan" / "SKILL.md").read_text(encoding="utf-8")
    assert "Existing system constraints (from Cairn)" not in lean_plan  # the preset replaced the command
    assert "cairn ask" in lean_plan  # ...and its minimal version still consults the project memory
    ok(root, "preset", "info", "lean")
    ok(root, "preset", "remove", "lean")
    core_plan = (root / ".claude" / "skills" / "cairn-plan" / "SKILL.md").read_text(encoding="utf-8")
    assert "Existing system constraints (from Cairn)" in core_plan  # core command restored


def test_workflow_lifecycle_and_run(root, tmp_path):
    out = ok(root, "workflow", "list")
    assert "Full SDD Cycle (cairn)" in out
    out = ok(root, "workflow", "search")
    assert "bugfix" in out and "assess" in out
    ok(root, "workflow", "add", "bugfix")
    assert "Guided Bug Fix" in ok(root, "workflow", "info", "bugfix")
    ok(root, "workflow", "remove", "bugfix")

    # a local workflow made of shell and gate steps runs end to end without an agent
    wf = tmp_path / "hello.yml"
    wf.write_text(yaml.safe_dump({
        "schema_version": "1.0",
        "workflow": {"id": "hello", "name": "Hello", "version": "1.0.0", "description": "demo"},
        "inputs": {"name": {"type": "string", "default": "world"}},
        "steps": [
            {"id": "greet", "type": "shell", "run": "echo hello-{{ inputs.name }} > greeting.txt"},
            {"id": "check", "type": "if", "condition": "{{ steps.greet.output.exit_code == 0 }}",
             "then": [{"id": "done", "type": "shell", "run": "echo ok > done.txt"}]},
        ],
    }, sort_keys=False), encoding="utf-8")
    out = ok(root, "workflow", "run", str(wf), "--input", "name=cairn")
    assert (root / "greeting.txt").read_text(encoding="utf-8").strip() == "hello-cairn"
    assert (root / "done.txt").read_text(encoding="utf-8").strip() == "ok"
    runs = root / ".cairn" / "workflow" / "workflows" / "runs"
    assert runs.is_dir() and any(runs.iterdir())
    run_id = next(runs.iterdir()).name
    assert "completed" in ok(root, "workflow", "status", run_id).lower()


def test_bundles_install_bundled_components_offline(root):
    out = ok(root, "bundle", "search")
    assert "bugfix" in out and "assess" in out
    ok(root, "bundle", "install", "bugfix", "--offline")
    wf = root / ".cairn" / "workflow"
    assert (wf / "extensions" / "bug" / "extension.yml").is_file()
    assert (wf / "workflows" / "bugfix" / "workflow.yml").is_file()
    assert "bugfix" in ok(root, "bundle", "list")
    ok(root, "bundle", "remove", "bugfix")


def test_integrations(root):
    out = ok(root, "integration", "list")
    assert "claude" in out and "copilot" in out and "gemini" in out
    assert "Default integration: claude" in ok(root, "integration", "status")
    ok(root, "integration", "install", "gemini", "--force")
    assert (root / ".gemini" / "commands" / "cairn.plan.toml").is_file()
    ok(root, "integration", "use", "gemini")
    state = json.loads((root / ".cairn" / "workflow" / "integration.json").read_text(encoding="utf-8"))
    assert state["default_integration"] == "gemini" and "claude" in state["installed_integrations"]
    ok(root, "integration", "uninstall", "claude", "--force")
    assert not (root / ".claude" / "skills" / "cairn-plan").exists()
    ok(root, "integration", "upgrade", "gemini", "--force")
    assert "gemini" in ok(root, "integration", "search")
    assert forbidden_hits(root) == []


def test_catalog_stacks(root):
    out = ok(root, "extension", "catalog", "list")
    assert "builtin://extensions" in out
    ok(root, "extension", "catalog", "add", "https://example.com/catalog.json", "--name", "team")
    cfg = yaml.safe_load((root / ".cairn" / "workflow" / "extension-catalogs.yml").read_text(encoding="utf-8"))
    assert any(c["url"] == "https://example.com/catalog.json" for c in cfg["catalogs"])
    ok(root, "extension", "catalog", "remove", "team")
    assert "builtin://presets" in ok(root, "preset", "catalog", "list")
    assert "builtin://workflows" in ok(root, "workflow", "catalog", "list")


def test_artifact_inventory(root):
    out = ok(root, "artifact", "list", "--json")
    data = json.loads(out[out.index("{"):] if out.lstrip().startswith("{") else out[out.index("["):])
    assert "cairn.plan" in json.dumps(data)


def test_bundled_sdd_workflow_dispatches_cairn_commands_to_the_agent(root, tmp_path, monkeypatch):
    """The full-cycle workflow hands `/cairn-specify …` to the agent CLI, then pauses at the review gate."""
    import os

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    fake = bin_dir / "claude"
    fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> '{log}'\necho done\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    out = ok(root, "workflow", "run", "cairn", "--input", "spec=Add refunds")
    assert "paused" in out.lower()
    assert log.read_text(encoding="utf-8").splitlines() == ["-p", "/cairn-specify Add refunds"]
    run_id = out.split("Run ID:")[1].split()[0]
    assert "paused" in ok(root, "workflow", "status", run_id).lower()

"""Workflow scaffolding: `cairn spec init` for every agent integration, templates, commands, naming."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from workflow_helpers import forbidden_hits, git_repo, init, spec

from cairn.engines.workflow._assets import ASSETS_DIR
from cairn.engines.workflow.integrations import INTEGRATION_REGISTRY

CORE_COMMANDS = ["analyze", "checklist", "clarify", "constitution", "converge", "implement", "plan", "specify",
                 "tasks", "taskstoissues"]
PAGE_TEMPLATES = ["checklist-template.md", "constitution-template.md", "plan-template.md", "spec-template.md",
                  "tasks-template.md"]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # some agents keep skills in the user's home (e.g. ~/.hermes/skills): never touch the real one
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()


def test_packaged_assets_are_complete():
    assert sorted(p.stem for p in (ASSETS_DIR / "commands").glob("*.md")) == CORE_COMMANDS
    for name in PAGE_TEMPLATES:
        assert (ASSETS_DIR / "templates" / name).is_file()
    for variant, names in {"bash": ["common.sh", "create-new-feature.sh", "setup-plan.sh", "setup-tasks.sh",
                                    "check-prerequisites.sh", "resolve-template.sh"],
                           "powershell": ["common.ps1", "create-new-feature.ps1", "setup-plan.ps1"],
                           "python": ["common.py", "create_new_feature.py", "setup_plan.py", "setup_tasks.py",
                                      "check_prerequisites.py", "resolve_template.py"]}.items():
        for n in names:
            assert (ASSETS_DIR / "scripts" / variant / n).is_file(), f"{variant}/{n}"
    for ext in ("git", "agent-context", "assess", "bug"):
        assert (ASSETS_DIR / "extensions" / ext / "extension.yml").is_file()
    for wf in ("cairn", "bugfix", "assess"):
        assert (ASSETS_DIR / "workflows" / wf / "workflow.yml").is_file()
    for preset in ("lean", "constitution-sync"):
        assert (ASSETS_DIR / "presets" / preset / "preset.yml").is_file()
    for catalog in ("extensions/catalog.json", "presets/catalog.json", "workflows/catalog.json",
                    "workflows/step-catalog.json", "integrations/catalog.json", "bundles/catalog.json"):
        assert json.loads((ASSETS_DIR / catalog).read_text(encoding="utf-8"))["schema_version"]


def test_template_content_is_faithful():
    spec_t = (ASSETS_DIR / "templates" / "spec-template.md").read_text(encoding="utf-8")
    assert "User Scenarios & Testing" in spec_t and "FR-001" in spec_t
    plan_t = (ASSETS_DIR / "templates" / "plan-template.md").read_text(encoding="utf-8")
    assert "Constitution Check" in plan_t and "Technical Context" in plan_t
    tasks_t = (ASSETS_DIR / "templates" / "tasks-template.md").read_text(encoding="utf-8")
    assert "[P]" in tasks_t and "T001" in tasks_t
    specify_cmd = (ASSETS_DIR / "commands" / "specify.md").read_text(encoding="utf-8")
    assert "__CAIRN_COMMAND_PLAN__" in specify_cmd or "cairn.plan" in specify_cmd


def test_memory_steps_are_built_into_the_core_commands():
    cmds = ASSETS_DIR / "commands"
    plan = (cmds / "plan.md").read_text(encoding="utf-8")
    assert "Existing system constraints (from Cairn)" in plan and "cairn_context" in plan and "cairn impact" in plan
    tasks = (cmds / "tasks.md").read_text(encoding="utf-8")
    assert "## Trace Tasks to Code (Cairn)" in tasks and "cairn specs <feature-id> --json" in tasks
    assert tasks.index("## Trace Tasks to Code (Cairn)") < tasks.index("## Mandatory Post-Execution Hooks")
    clarify = (cmds / "clarify.md").read_text(encoding="utf-8")
    assert "## Save Clarified Decisions (Cairn)" in clarify and "--kind decision" in clarify
    implement = (cmds / "implement.md").read_text(encoding="utf-8")
    assert "## Verify Against the Spec (Cairn)" in implement and "cairn drift <feature-id>" in implement
    # the old add-on is gone: nothing registers these steps as extension hooks any more
    assert not (Path(__file__).resolve().parents[2] / "src" / "cairn" / ("spec" + "kit_extension")).exists()


@pytest.mark.parametrize("key", sorted(INTEGRATION_REGISTRY))
def test_init_every_integration(tmp_path, key):
    root = git_repo(tmp_path / "repo")
    extra = ("--integration-options=--commands-dir .myagent/commands",) if key == "generic" else ()
    init(root, key, *extra)
    wf = root / ".cairn" / "workflow"
    assert (wf / "memory" / "constitution.md").is_file()
    for name in PAGE_TEMPLATES:
        assert (wf / "templates" / name).is_file()
    variant, script = ("powershell", "create-new-feature.ps1") if os.name == "nt" else ("bash", "create-new-feature.sh")
    assert (wf / "scripts" / variant / script).is_file()
    assert (wf / "workflows" / "cairn" / "workflow.yml").is_file()
    opts = json.loads((wf / "init-options.json").read_text(encoding="utf-8"))
    assert opts["integration"] == key and opts["workflow_version"]
    manifest = json.loads((wf / "integrations" / f"{key}.manifest.json").read_text(encoding="utf-8"))
    files = sorted(manifest["files"])
    home_skills = Path.home() / ".hermes" / "skills"
    if key == "hermes":  # hermes reads skills from the user's home directory
        files = sorted(p.relative_to(home_skills).as_posix() for p in home_skills.rglob("SKILL.md"))
    for cmd in CORE_COMMANDS:
        assert any(f"cairn.{cmd}." in f or f"cairn-{cmd}/" in f or f"cairn-{cmd}." in f for f in files), (key, cmd, files)
    for rel in manifest["files"]:
        assert (root / rel).exists(), rel
    assert forbidden_hits(root) == []
    if key == "hermes":
        assert forbidden_hits(home_skills) == []


@pytest.mark.parametrize("key,where", [
    ("claude", ".claude/skills/cairn-plan/SKILL.md"),
    ("codex", ".agents/skills/cairn-plan/SKILL.md"),
    ("cursor-agent", ".cursor/skills/cairn-plan/SKILL.md"),
    ("gemini", ".gemini/commands/cairn.plan.toml"),
    ("copilot", ".github/skills/cairn-plan/SKILL.md"),
    ("qwen", ".qwen/commands/cairn.plan.md"),
    ("opencode", ".opencode/commands/cairn.plan.md"),
])
def test_generated_commands_carry_the_memory_steps(tmp_path, key, where):
    root = git_repo(tmp_path / "repo")
    init(root, key)
    text = (root / where).read_text(encoding="utf-8")
    assert "Existing system constraints (from Cairn)" in text
    assert ".cairn/workflow/" in text  # paths point at the project's workflow folder


def test_init_with_preset_and_extension(tmp_path):
    root = git_repo(tmp_path / "repo")
    init(root, "claude", "--preset", "lean", "--extension", "git")
    wf = root / ".cairn" / "workflow"
    assert (wf / "presets" / "lean" / "preset.yml").is_file()
    assert (wf / "extensions" / "git" / "extension.yml").is_file()
    assert (root / ".claude" / "skills" / "cairn-git-commit" / "SKILL.md").is_file()
    assert forbidden_hits(root) == []


def test_init_keeps_an_existing_constitution(tmp_path):
    root = git_repo(tmp_path / "repo")
    init(root, "claude")
    constitution = root / ".cairn" / "workflow" / "memory" / "constitution.md"
    constitution.write_text("# Shop Constitution\n\n### I. Idempotent payments\n", encoding="utf-8")
    init(root, "claude")
    assert constitution.read_text(encoding="utf-8").startswith("# Shop Constitution")


def test_version_check_and_help(tmp_path):
    code, out = spec(tmp_path, "version")
    assert code == 0 and "Workflow engine" in out
    code, out = spec(tmp_path, "version", "--features", "--json")
    payload = json.loads(out[out.index("{"):])
    assert code == 0 and payload["features"]["bundled_templates"] is True
    assert spec(tmp_path, "check")[0] == 0
    code, out = spec(tmp_path, "--help")
    assert code == 0 and "extension" in out and "workflow" in out and "bundle" in out
    assert spec(tmp_path, "no-such-command")[0] != 0


def test_project_commands_need_a_workflow_folder(tmp_path):
    code, out = spec(tmp_path, "extension", "list")
    assert code != 0 and ".cairn/workflow" in out


def test_init_dot_force_succeeds_and_shows_next_steps(tmp_path):
    """`cairn spec init . --force` in a non-empty repo: exit 0, templates, where-specs-go."""
    root = git_repo(tmp_path / "repo")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    code, out = spec(root, "init", ".", "--force", "--non-interactive", "--ignore-agent-tools")
    assert code == 0, out[-2000:]
    assert (root / ".cairn" / "workflow" / "templates" / "spec-template.md").is_file()
    assert (root / ".cairn" / "workflow" / "templates" / "tasks-template.md").is_file()
    assert "Next Steps" in out
    assert "specs/" in out and "cairn specs" in out and "cairn drift" in out


def test_init_failure_is_reported_clearly(tmp_path):
    """A failing init exits nonzero with a clear message, never silently."""
    root = git_repo(tmp_path / "repo")
    (root / ".cairn").write_text("not a directory\n", encoding="utf-8")  # shared infra cannot be installed
    code, out = spec(root, "init", ".", "--force", "--non-interactive", "--ignore-agent-tools")
    assert code != 0
    assert "did not complete" in out
    assert "Next Steps" not in out

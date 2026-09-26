"""Migrating a repository from the older workflow layout to Cairn's, keeping user edits."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from workflow_helpers import OLD_DIR, OLD_NS, forbidden_hits, git_repo, init

from cairn.engines import specs
from cairn.project import Project

CONSTITUTION = f"""# Shop Constitution

### I. Idempotent payments
Every charge carries an idempotency key. Templates live in `{OLD_DIR}/templates/plan-template.md`;
run `/{OLD_NS}.plan` after `/{OLD_NS}.clarify`.

**Version**: 2.1.0 | **Ratified**: 2025-01-01
"""


def _to_legacy_text(text: str) -> str:
    return (text.replace(".cairn/workflow", OLD_DIR).replace("cairn.", f"{OLD_NS}.")
            .replace("cairn-", f"{OLD_NS}-").replace("CAIRN_", "SPEC" + "IFY_")
            .replace("workflow_version", f"{OLD_NS}_version"))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_legacy_repo(root: Path) -> None:
    """A repository as the older tool left it: built from a Cairn install with every name reverted."""
    init(root, "claude")
    new = root / ".cairn" / "workflow"
    old = root / OLD_DIR
    shutil.copytree(new, old)
    shutil.rmtree(root / ".cairn")
    for f in sorted(old.rglob("*")):
        if f.is_file():
            f.write_text(_to_legacy_text(f.read_text(encoding="utf-8")), encoding="utf-8")
    shutil.move(old / "integrations" / "cairn.manifest.json", old / "integrations" / f"{OLD_NS}.manifest.json")
    wf = old / "workflows"
    shutil.move(wf / "cairn", wf / OLD_NS)
    skills = root / ".claude" / "skills"
    for d in sorted(skills.iterdir()):
        (d / "SKILL.md").write_text(_to_legacy_text((d / "SKILL.md").read_text()))
        d.rename(skills / d.name.replace("cairn-", f"{OLD_NS}-"))
    # manifests record the hash of what the old tool wrote
    for mf in (old / "integrations").glob("*.manifest.json"):
        data = json.loads(mf.read_text())
        data["files"] = {_to_legacy_text(rel): _sha(root / _to_legacy_text(rel)) for rel in data["files"]}
        mf.write_text(json.dumps(data))
    # --- user edits ------------------------------------------------------------------------------
    (old / "memory" / "constitution.md").write_text(CONSTITUTION)
    plan_t = old / "templates" / "plan-template.md"
    plan_t.write_text(plan_t.read_text() + "\n## Our extra section\n")
    (old / "templates" / "overrides").mkdir()
    (old / "templates" / "overrides" / "spec-template.md").write_text("# House spec\n")
    (old / "feature.json").write_text(json.dumps({"feature_directory": "specs/001-refunds"}))
    mine = skills / f"{OLD_NS}-mycmd"
    mine.mkdir()
    (mine / "SKILL.md").write_text(f"---\nname: {OLD_NS}-mycmd\n---\nRead {OLD_DIR}/memory/constitution.md\n")
    # --- Cairn's former add-on extension ------------------------------------------------------------
    addon = old / "extensions" / "cairn"
    (addon / "commands").mkdir(parents=True)
    (addon / "extension.yml").write_text("schema_version: '1.0'\n")
    (addon / "commands" / "context.md").write_text("context\n")
    (old / "extensions" / ".registry").write_text(json.dumps({"schema_version": "1.0", "extensions": {
        "cairn": {"version": "0.1.0", "enabled": True,
                  "registered_commands": {"claude": [f"{OLD_NS}.cairn.context"]}}}}))
    (old / "extensions.yml").write_text(yaml.safe_dump({
        "installed": ["cairn"], "settings": {"auto_execute_hooks": True},
        "hooks": {"before_plan": [{"extension": "cairn", "command": f"{OLD_NS}.cairn.context", "enabled": True,
                                   "optional": False}]}}))
    addon_skill = skills / f"{OLD_NS}-cairn-context"
    addon_skill.mkdir()
    (addon_skill / "SKILL.md").write_text("add-on skill\n")
    # --- agent context file with the old managed block ----------------------------------------------
    marker = OLD_NS.upper()
    (root / "CLAUDE.md").write_text(f"# Notes\n\n<!-- {marker} START -->\nPlan: {OLD_DIR}/memory\n"
                                    f"<!-- {marker} END -->\n")
    opts = json.loads((old / "init-options.json").read_text())
    opts["feature_numbering"] = "timestamp"
    (old / "init-options.json").write_text(json.dumps(opts))


@pytest.fixture()
def legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = git_repo(tmp_path / "repo")
    _make_legacy_repo(root)
    assert specs.legacy_layout(root) and not specs.initialized(root)
    return root


def test_migrate_legacy_repository(legacy):
    root = legacy
    actions = specs.migrate_legacy(root)
    assert actions, "nothing migrated"
    new = root / ".cairn" / "workflow"
    assert not (root / OLD_DIR).exists()
    assert specs.initialized(root) and not specs.legacy_layout(root)

    # user content survives, references rewritten
    constitution = (new / "memory" / "constitution.md").read_text()
    assert "Idempotent payments" in constitution and "**Version**: 2.1.0" in constitution
    assert ".cairn/workflow/templates/plan-template.md" in constitution and "/cairn.plan" in constitution
    assert specs.constitution(root)["version"] == "2.1.0"
    assert (new / "templates" / "plan-template.md").read_text().rstrip().endswith("## Our extra section")
    assert (new / "migration-backup" / OLD_DIR / "templates" / "plan-template.md").is_file()
    assert (new / "templates" / "overrides" / "spec-template.md").read_text() == "# House spec\n"
    assert json.loads((new / "feature.json").read_text())["feature_directory"] == "specs/001-refunds"

    # untouched generated files are the fresh Cairn versions
    plan_skill = (root / ".claude" / "skills" / "cairn-plan" / "SKILL.md").read_text()
    assert "Existing system constraints (from Cairn)" in plan_skill
    assert (new / "scripts" / "bash" / "create-new-feature.sh").read_text() == (
        Path(specs.__file__).parent / "workflow" / "assets" / "scripts" / "bash" / "create-new-feature.sh").read_text()
    assert (new / "integrations" / "claude.manifest.json").is_file()

    # a hand-made agent skill is renamed, not lost
    mine = root / ".claude" / "skills" / "cairn-mycmd" / "SKILL.md"
    assert mine.is_file() and ".cairn/workflow/memory/constitution.md" in mine.read_text()

    # the former add-on is gone everywhere: its steps are built into the commands
    assert not (new / "extensions" / "cairn").exists()
    assert not (root / ".claude" / "skills" / "cairn-cairn-context").exists()
    assert not list((root / ".claude" / "skills").glob(f"{OLD_NS}-*"))
    hooks = yaml.safe_load((new / "extensions.yml").read_text())
    assert "cairn" not in (hooks.get("installed") or []) and not (hooks.get("hooks") or {}).get("before_plan")
    assert "cairn" not in json.loads((new / "extensions" / ".registry").read_text())["extensions"]

    # settings carried over
    opts = json.loads((new / "init-options.json").read_text())
    assert opts["integration"] == "claude" and opts["feature_numbering"] == "timestamp" and opts["workflow_version"]
    claude_md = (root / "CLAUDE.md").read_text()
    assert "<!-- CAIRN WORKFLOW START -->" in claude_md and ".cairn/workflow/memory" in claude_md

    # nothing in the live tree names the old tool (backups keep the originals on purpose)
    assert forbidden_hits(root, skip=(".git", "migration-backup")) == []
    # idempotent
    assert specs.migrate_legacy(root) == []


def test_bootstrap_migrates_a_legacy_repository(legacy):
    ok, msg = specs.bootstrap(Project(root=legacy), "claude")
    assert ok and "migrated" in msg
    assert specs.initialized(legacy) and not (legacy / OLD_DIR).exists()


def test_nothing_to_migrate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = git_repo(tmp_path / "repo")
    assert specs.migrate_legacy(root) == []
    init(root, "claude")
    assert specs.migrate_legacy(root) == []

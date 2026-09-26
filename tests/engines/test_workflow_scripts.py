"""The workflow's helper scripts, run for real: Bash and Python variants, plus the bridge that calls them."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from workflow_helpers import GIT_ENV, git_repo, init, spec

from cairn.engines import specs
from cairn.project import Project

BASH = shutil.which("bash")


def _json(stdout: str) -> dict:
    return json.loads(next(ln for ln in stdout.splitlines() if ln.startswith("{")))


def _run(cmd: list[str], root: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in GIT_ENV.items() if not k.startswith("CAIRN_FEATURE")}
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=60, env=env)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = git_repo(tmp_path / "repo")
    init(root, "claude", "--script", "sh")
    return root


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_bash_scripts_are_executable_and_drive_a_feature(project):
    scripts = project / ".cairn" / "workflow" / "scripts" / "bash"
    for sh in scripts.glob("*.sh"):
        assert os.access(sh, os.X_OK), sh.name

    res = _run([BASH, str(scripts / "create-new-feature.sh"), "--json", "Add user authentication"], project)
    assert res.returncode == 0, res.stderr
    created = _json(res.stdout)
    assert created["BRANCH_NAME"] == "001-user-authentication" and created["FEATURE_NUM"] == "001"
    spec_file = Path(created["SPEC_FILE"])
    assert spec_file.is_file() and "User Scenarios & Testing" in spec_file.read_text()
    pointer = json.loads((project / ".cairn" / "workflow" / "feature.json").read_text())
    assert pointer["feature_directory"] == "specs/001-user-authentication"
    assert "CAIRN_FEATURE=" in res.stderr  # persistence hint uses the Cairn env var

    res = _run([BASH, str(scripts / "setup-plan.sh"), "--json"], project)
    assert res.returncode == 0, res.stderr
    plan = _json(res.stdout)
    assert Path(plan["IMPL_PLAN"]).is_file() and plan["BRANCH"] == "001-user-authentication"

    res = _run([BASH, str(scripts / "check-prerequisites.sh"), "--json"], project)
    assert res.returncode == 0, res.stderr
    assert _json(res.stdout)["FEATURE_DIR"].endswith("specs/001-user-authentication")

    res = _run([BASH, str(scripts / "check-prerequisites.sh"), "--json", "--paths-only"], project)
    paths = _json(res.stdout)
    assert paths["TASKS"].endswith("tasks.md") and paths["FEATURE_SPEC"] == str(spec_file)

    res = _run([BASH, str(scripts / "setup-tasks.sh"), "--json"], project)
    assert res.returncode == 0, res.stderr
    assert "Tasks: [FEATURE NAME]" in _json(res.stdout)["TASKS_TEMPLATE_CONTENT"]

    res = _run([BASH, str(scripts / "resolve-template.sh"), "spec-template"], project)
    assert res.returncode == 0 and "Feature Specification" in res.stdout

    # second feature gets the next number
    res = _run([BASH, str(scripts / "create-new-feature.sh"), "--json", "--short-name", "billing", "Bill customers"],
               project)
    assert _json(res.stdout)["BRANCH_NAME"] == "002-billing"


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_template_override_wins_over_core(project):
    overrides = project / ".cairn" / "workflow" / "templates" / "overrides"
    overrides.mkdir(parents=True)
    (overrides / "spec-template.md").write_text("# Our house spec format\n")
    scripts = project / ".cairn" / "workflow" / "scripts" / "bash"
    res = _run([BASH, str(scripts / "resolve-template.sh"), "spec-template"], project)
    assert res.returncode == 0 and res.stdout.startswith("# Our house spec format")


def test_python_scripts_drive_a_feature(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = git_repo(tmp_path / "repo")
    init(root, "codex", "--script", "py")
    scripts = root / ".cairn" / "workflow" / "scripts" / "python"
    assert (scripts / "create_new_feature.py").is_file()
    res = _run([sys.executable, str(scripts / "create_new_feature.py"), "--json", "Export reports as CSV"], root)
    assert res.returncode == 0, res.stderr
    created = _json(res.stdout)
    assert Path(created["SPEC_FILE"]).is_file()
    res = _run([sys.executable, str(scripts / "setup_plan.py"), "--json"], root)
    assert res.returncode == 0, res.stderr
    assert Path(_json(res.stdout)["IMPL_PLAN"]).is_file()
    res = _run([sys.executable, str(scripts / "check_prerequisites.py"), "--json", "--paths-only"], root)
    assert res.returncode == 0 and _json(res.stdout)["BRANCH"] == created["BRANCH_NAME"]
    res = _run([sys.executable, str(scripts / "setup_tasks.py"), "--json"], root)
    assert res.returncode == 0, res.stderr


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_new_feature_bridge(project):
    res = specs.new_feature(Project(root=project), "Refund an order")
    assert res["BRANCH_NAME"] == "001-refund-order"
    assert (project / "specs" / "001-refund-order" / "spec.md").is_file()
    feats = specs.features(project)
    assert [f["id"] for f in feats] == ["001-refund-order"]


@pytest.mark.skipif(BASH is None or shutil.which("git") is None, reason="bash/git not available")
def test_git_extension_creates_feature_branches(project):
    code, out = spec(project, "extension", "add", "git")
    assert code == 0, out
    ext_scripts = project / ".cairn" / "workflow" / "extensions" / "git" / "scripts" / "bash"
    res = _run([BASH, str(ext_scripts / "create-new-feature-branch.sh"), "--json", "Add search"], project)
    assert res.returncode == 0, res.stderr
    branch = _json(res.stdout)["BRANCH_NAME"]
    current = subprocess.run(["git", "branch", "--show-current"], cwd=project, capture_output=True, text=True)
    assert current.stdout.strip() == branch

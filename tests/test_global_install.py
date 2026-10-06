"""One install for every agent (`cairn global install`): user-level MCP, memory and capture that stand aside in
repositories wiring their own, and create nothing where Cairn is not set up."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cairn import agents
from cairn.cli import app
from cairn.engines.recall import hooks as recall_hooks
from cairn.engines.recall import schema


@pytest.fixture()
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(agents, "_claude_user_mcp", lambda add: None)  # never touch the real Claude CLI config
    return h


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _commands(settings: dict) -> list[str]:
    return [h["command"] for groups in settings.get("hooks", {}).values() for g in groups for h in g.get("hooks", [])]


def test_global_install_wires_every_agent_at_user_level_and_removes_cleanly(home):
    (home / ".claude").mkdir()
    mine = {"hooks": [{"type": "command", "command": "echo mine"}]}
    (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [mine]}, "theme": "dark"}))
    chosen = ["claude", "gemini", "opencode", "cursor", "copilot"]
    changed = agents.global_install_agents(chosen)
    assert set(chosen) <= set(changed)

    claude = _read(home / ".claude" / "settings.json")
    cmds = _commands(claude)
    assert "echo mine" in cmds and claude["theme"] == "dark"  # the person's own settings stay
    capture = [c for c in cmds if "cairn.capture" in c]
    assert capture and all("--scope user" in c for c in capture)
    assert any(c.endswith("hook session-start --scope user") for c in cmds)
    assert any(c.endswith("hook ambient --scope user") for c in cmds)
    assert any(c.endswith("hook global-session") for c in cmds)  # the setup offer, where Cairn is missing

    gemini = _read(home / ".gemini" / "settings.json")
    assert gemini["mcpServers"]["cairn"]["args"] == ["mcp"]
    assert all("--scope user" in c for c in _commands(gemini) if "cairn.capture" in c)
    opencode = _read(home / ".config" / "opencode" / "opencode.json")
    assert opencode["mcp"]["cairn"]["command"][-1] == "mcp"
    plugin = next((home / ".config" / "opencode").rglob("cairn*.js"))
    assert '["--scope", "user"]' in plugin.read_text(encoding="utf-8")
    assert _read(home / ".cursor" / "mcp.json")["mcpServers"]["cairn"]
    assert _read(home / ".copilot" / "mcp-config.json")["mcpServers"]["cairn"]["tools"] == ["*"]

    assert agents.global_install_agents(chosen) == {}  # idempotent: a second run changes nothing

    agents.global_install_agents(chosen, remove=True)
    after = _read(home / ".claude" / "settings.json")
    assert _commands(after) == ["echo mine"] and after["theme"] == "dark"
    assert "cairn" not in _read(home / ".gemini" / "settings.json").get("mcpServers", {})
    assert "cairn" not in _read(home / ".config" / "opencode" / "opencode.json").get("mcp", {})


def _payload(cwd: Path, **extra) -> str:
    return json.dumps({"session_id": "s-global", "cwd": str(cwd), "prompt": "fix PaymentService refunds", **extra})


def _prompts(root: Path) -> int:
    db = schema.connect(schema.store_path(root), readonly=True)
    try:
        return db.execute("SELECT COUNT(*) FROM user_prompts").fetchone()[0]
    finally:
        db.close()


def test_user_scope_capture_records_where_the_repo_has_no_hooks_of_its_own(cairn):
    root = cairn.project.root
    recall_hooks.main(["--platform", "claude-code", "--scope", "user", "session-init"], stdin_text=_payload(root))
    assert _prompts(root) == 1


def test_user_scope_capture_stands_aside_where_the_repo_wires_its_own(cairn):
    root = cairn.project.root
    agents.install(cairn.project, ["claude"])  # what `cairn init` writes: project-level capture hooks
    recall_hooks.main(["--platform", "claude-code", "--scope", "user", "session-init"], stdin_text=_payload(root))
    assert not schema.store_path(root).exists() or _prompts(root) == 0
    recall_hooks.main(["--platform", "claude-code", "session-init"], stdin_text=_payload(root))  # the project's own
    assert _prompts(root) == 1  # recorded exactly once


def test_user_scope_capture_creates_nothing_where_cairn_is_not_set_up(tmp_path):
    repo = tmp_path / "plain"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for event in ("context", "session-init", "observation", "summarize"):
        recall_hooks.main(["--platform", "claude-code", "--scope", "user", event], stdin_text=_payload(repo))
    assert not (repo / ".cairn").exists()


def test_user_scope_brief_stands_aside_where_the_repo_wires_its_own(cairn):
    runner = CliRunner()
    out = runner.invoke(app, ["hook", "session-start", "--scope", "user"])
    assert out.exit_code == 0 and json.loads(out.stdout)["systemMessage"].startswith("▲ cairn")
    agents.install(cairn.project, ["claude"])
    assert runner.invoke(app, ["hook", "session-start", "--scope", "user"]).stdout == ""
    assert json.loads(runner.invoke(app, ["hook", "session-start"]).stdout)["hookSpecificOutput"]  # project's own

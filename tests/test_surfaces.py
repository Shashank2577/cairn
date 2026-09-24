"""CLI (zero-prompt, idempotent init), agents, hooks, HTTP API and MCP tools."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from cairn import agents, hooks
from cairn.cli import app
from cairn.project import Project


def test_init_is_zero_prompt_and_idempotent(repo):
    (repo / "CLAUDE.md").write_text("# House rules\nKeep it simple.\n")
    (repo / ".claude").mkdir()
    r = CliRunner().invoke(app, ["init", "--no-ui", "--no-capture", "--no-specs"], input="")
    assert r.exit_code == 0, r.output
    snapshot = {p: p.read_text() for p in repo.rglob("*") if p.is_file() and ".git/" not in str(p)
                and ".cairn" not in str(p) and "graphify-out" not in str(p)}
    r2 = CliRunner().invoke(app, ["init", "--no-ui", "--no-capture", "--no-specs"])
    assert r2.exit_code == 0
    for p, text in snapshot.items():
        assert p.read_text() == text, f"{p} changed on second init"
    claude_md = (repo / "CLAUDE.md").read_text()
    assert claude_md.startswith("# House rules") and "cairn:begin" in claude_md
    assert json.loads((repo / ".mcp.json").read_text())["mcpServers"]["cairn"]
    assert (repo / ".claude" / "agents" / "cairn-scout.md").exists()
    assert "cairn-hook" in (repo / ".git" / "hooks" / "post-commit").read_text()


def test_uninstall_removes_only_ours(repo):
    (repo / "CLAUDE.md").write_text("# House rules\n")
    (repo / ".claude").mkdir()
    proj = Project.discover(repo)
    proj.ensure_dir()
    agents.install(proj, ["claude"])
    hooks.install_git_hooks(proj)
    agents.uninstall(proj)
    hooks.remove_git_hooks(proj)
    assert (repo / "CLAUDE.md").read_text().strip() == "# House rules"
    assert "cairn" not in (repo / ".mcp.json").read_text()
    assert not (repo / ".claude" / "agents" / "cairn-scout.md").exists()


def test_status_and_questions_via_cli(cairn):
    runner = CliRunner()
    assert runner.invoke(app, ["status", "--json"]).exit_code == 0
    out = runner.invoke(app, ["impact", "PaymentService", "--json"])
    assert out.exit_code == 0 and json.loads(out.output)["risk"]
    assert runner.invoke(app, ["remember", "Refunds go through the gateway", "--kind", "decision"]).exit_code == 0
    assert "gateway" in runner.invoke(app, ["recall", "refunds"]).output


def test_hooks_output(cairn):
    data = json.loads(hooks.session_start(cairn))
    assert data["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "cairn" in hooks.statusline(cairn, "{}")


def test_http_api(cairn):
    from fastapi.testclient import TestClient
    from cairn.server import create_app
    c = TestClient(create_app(cairn))
    assert c.get("/api/health").json()["ok"]
    assert c.get("/api/overview").json()["layers"]["map"]["nodes"] > 0
    assert c.get("/api/map").json()["level"] == "areas"
    assert c.get("/api/impact", params={"target": "PaymentService"}).json()["risk"]
    assert c.get("/api/specs").json()["features"]
    assert c.post("/api/memories", json={"text": "Ledger rows are append-only", "kind": "convention"}).json()["id"]
    assert c.get("/api/search", params={"q": "ledger"}).json()
    assert "<title>Cairn</title>" in c.get("/").text


def test_mcp_tools(cairn, monkeypatch):
    from cairn import mcp_server
    mcp_server._cairn.cache_clear()
    monkeypatch.setattr(mcp_server, "_cairn", lambda: cairn)
    assert "Impact" in mcp_server.cairn_impact("PaymentService")
    assert "Context" in mcp_server.cairn_context("refund an order")
    assert json.loads(mcp_server.cairn_remember("Use cents for money", "convention"))["id"]
    assert "cents" in mcp_server.cairn_recall("money")
    assert "missing" in mcp_server.cairn_specs(drift=True) or "does not exist" in mcp_server.cairn_specs(drift=True)
    server = mcp_server.build()
    assert server is not None and len(mcp_server.TOOLS) == 8

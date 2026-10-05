"""CLI (zero-prompt, idempotent init), agents, hooks, HTTP API and MCP tools."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cairn import agents, hooks
from cairn.cli import app
from cairn.project import Project


def test_init_is_zero_prompt_and_idempotent(repo):
    (repo / "CLAUDE.md").write_text("# House rules\nKeep it simple.\n", encoding="utf-8")
    (repo / ".claude").mkdir()
    r = CliRunner().invoke(app, ["init", "--no-ui", "--no-specs", "--agents", "claude"], input="")
    assert r.exit_code == 0, r.output
    snapshot = {p: p.read_text(encoding="utf-8") for p in repo.rglob("*") if p.is_file() and ".git/" not in p.as_posix()
                and ".cairn" not in str(p)}
    r2 = CliRunner().invoke(app, ["init", "--no-ui", "--no-specs", "--agents", "claude"])
    assert r2.exit_code == 0
    for p, text in snapshot.items():
        assert p.read_text(encoding="utf-8") == text, f"{p} changed on second init"
    claude_md = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude_md.startswith("# House rules") and "cairn:begin" in claude_md
    assert json.loads((repo / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["cairn"]
    assert (repo / ".claude" / "agents" / "cairn-scout.md").exists()
    assert "cairn-hook" in (repo / ".git" / "hooks" / "post-commit").read_text(encoding="utf-8")
    hooks_cfg = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]
    for event in ("UserPromptSubmit", "PostToolUse", "Stop"):  # built-in session capture, no extra install
        assert "cairn.capture" in json.dumps(hooks_cfg[event])


def test_uninstall_removes_only_ours(repo):
    (repo / "CLAUDE.md").write_text("# House rules\n", encoding="utf-8")
    (repo / ".claude").mkdir()
    proj = Project.discover(repo)
    proj.ensure_dir()
    agents.install(proj, ["claude"])
    hooks.install_git_hooks(proj)
    agents.uninstall(proj)
    hooks.remove_git_hooks(proj)
    assert (repo / "CLAUDE.md").read_text(encoding="utf-8").strip() == "# House rules"
    assert "cairn" not in (repo / ".mcp.json").read_text(encoding="utf-8")
    assert not (repo / ".claude" / "agents" / "cairn-scout.md").exists()
    assert "cairn" not in json.dumps(json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8")).get("hooks", {}))


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


def test_session_start_shows_the_person_every_layer(cairn):
    cairn.remember("Refunds always go through the payment gateway", kind="decision")
    data = json.loads(hooks.session_start(cairn))
    panel = data["systemMessage"]  # the visible part; additionalContext is what the model gets
    assert panel.startswith("▲ cairn")
    assert "Map " in panel and "files" in panel
    assert "Memory " in panel and "[decision] Refunds always go through the payment gateway" in panel
    assert "Refunds" in data["hookSpecificOutput"]["additionalContext"]


def test_session_start_keeps_the_brief_when_the_panel_fails(cairn, monkeypatch):
    monkeypatch.setattr(hooks, "session_panel", lambda c: 1 / 0)
    data = json.loads(hooks.session_start(cairn))
    assert "systemMessage" not in data and data["hookSpecificOutput"]["additionalContext"]


# ---- ambient context (the enforced read path) --------------------------------------------------------------
PROMPT = "fix the double charge in PaymentService"


def _ambient_payload(prompt: str) -> str:
    return json.dumps({"prompt": prompt, "session_id": "s1", "cwd": "/repo"})


def test_ambient_injects_a_nugget_for_code_prompts(cairn):
    cairn.remember("PaymentService retries can double charge; always pass an idempotency key", kind="gotcha")
    cairn.project.set_cfg("context.ambient", True)
    out = hooks.ambient(cairn, _ambient_payload(PROMPT))
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    nugget = out["hookSpecificOutput"]["additionalContext"]
    assert "PaymentService" in nugget and "[memory:" in nugget  # a resolved target and a memory id
    assert len(nugget) <= hooks.AMBIENT_CAP


def test_ambient_skips_chat_and_respects_the_kill_switch(cairn):
    cairn.remember("PaymentService retries can double charge", kind="gotcha")
    cairn.project.set_cfg("context.ambient", True)
    for payload in ("what's the weather like today?", "hello there!", "ok done", ""):  # chat, short, empty
        assert hooks.ambient(cairn, _ambient_payload(payload)) is None, payload
    for payload in (json.dumps(["not", "an", "object"]), json.dumps({"session_id": "s1"})):  # no prompt in it
        assert hooks.ambient(cairn, payload) is None, payload
    assert hooks.ambient(cairn, _ambient_payload(PROMPT)) is not None
    cairn.project.set_cfg("context.ambient", False)  # the kill-switch wins even for a code prompt
    assert hooks.ambient(cairn, _ambient_payload(PROMPT)) is None


def test_ambient_truncates_to_the_cap(cairn, monkeypatch):
    cairn.project.set_cfg("context.ambient", True)
    for i in range(6):
        cairn.remember(f"Gotcha number {i}: " + "payments charge retries ledger idempotency " * 20, kind="gotcha")
    monkeypatch.setattr(hooks, "AMBIENT_CAP", 300)
    nugget = hooks.ambient(cairn, _ambient_payload("refactor the PaymentService charge flow now"))
    nugget = nugget["hookSpecificOutput"]["additionalContext"]
    assert len(nugget) == 300 and nugget.endswith(hooks._AMBIENT_MORE)


def test_ambient_never_raises(cairn, monkeypatch):
    cairn.project.set_cfg("context.ambient", True)

    def boom(*_a, **_k):
        raise RuntimeError("the brain is busy")
    monkeypatch.setattr(cairn, "infer_targets", boom)
    monkeypatch.setattr(cairn.memory, "recall", boom)
    assert hooks.ambient(cairn, _ambient_payload(PROMPT)) is None


def test_hook_ambient_cli(cairn):
    cairn.remember("PaymentService retries can double charge; always pass an idempotency key", kind="gotcha")
    cairn.project.set_cfg("context.ambient", True)
    runner = CliRunner()
    r = runner.invoke(app, ["hook", "ambient"], input=_ambient_payload(PROMPT))
    assert r.exit_code == 0, r.output
    nugget = json.loads(r.output)["hookSpecificOutput"]["additionalContext"]
    assert "PaymentService" in nugget
    chat = runner.invoke(app, ["hook", "ambient"], input=_ambient_payload("hello there!"))
    assert chat.exit_code == 0 and chat.output == ""  # empty stdout injects nothing


def test_hook_ambient_without_a_project_prints_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no git root and no .cairn/ anywhere above
    r = CliRunner().invoke(app, ["hook", "ambient"], input=_ambient_payload(PROMPT))
    assert r.exit_code == 0 and r.output == ""


def test_claude_wiring_includes_the_ambient_prompt_hook(repo):
    proj = Project.discover(repo)
    proj.ensure_dir()
    settings_path = repo / ".claude" / "settings.json"

    def ambient_entries():
        hooks_cfg = json.loads(settings_path.read_text(encoding="utf-8"))["hooks"]
        return [h for g in hooks_cfg.get("UserPromptSubmit", []) for h in g["hooks"]
                if "cairn hook ambient" in h.get("command", "")]

    agents.install(proj, ["claude"])
    entries = ambient_entries()
    assert len(entries) == 1 and entries[0]["type"] == "command" and entries[0]["timeout"] == 10
    agents.install(proj, ["claude"])  # idempotent: a re-init must not duplicate the prompt hook
    assert len(ambient_entries()) == 1
    agents.install(proj, ["claude"], capture=False)  # the ambient hook rides on its own, not on capture
    assert len(ambient_entries()) == 1
    agents.uninstall(proj)
    assert "cairn hook ambient" not in json.dumps(json.loads(settings_path.read_text(encoding="utf-8")).get("hooks", {}))


def test_doctor_reports_the_ambient_switch(cairn, monkeypatch):
    monkeypatch.setenv("COLUMNS", "260")
    row = next(ln for ln in CliRunner().invoke(app, ["doctor"]).output.splitlines() if "Ambient context" in ln)
    assert "off (config)" in row and "set [context] ambient = true" in row
    cairn.project.set_cfg("context.ambient", True)
    row = next(ln for ln in CliRunner().invoke(app, ["doctor"]).output.splitlines() if "Ambient context" in ln)
    assert "on, prompt hook not wired" in row and "cairn agents install" in row
    agents.install(cairn.project, ["claude"])
    row = next(ln for ln in CliRunner().invoke(app, ["doctor"]).output.splitlines() if "Ambient context" in ln)
    assert "●" in row and "on (Claude Code prompt hook)" in row


def _client(cairn, tmp_path):
    from fastapi.testclient import TestClient

    from cairn.platform import Platform, ServerConfig
    from cairn.server import create_app
    config = ServerConfig(mode="local")
    app = create_app(platform=Platform(home=tmp_path / "home", config=config), config=config,
                     register=[cairn.project.root])
    client = TestClient(app, base_url="http://127.0.0.1")
    pid = client.get("/api/session").json()["projects"][0]["id"]
    return client, f"/api/p/{pid}"


def test_http_api(cairn, tmp_path):
    c, p = _client(cairn, tmp_path)
    assert c.get("/api/health").json()["ok"]
    session = c.get("/api/session").json()
    assert session["mode"] == "local" and "project.write" in session["projects"][0]["permissions"]
    assert c.get(f"{p}/overview").json()["layers"]["map"]["nodes"] > 0
    assert c.get(f"{p}/impact", params={"target": "PaymentService"}).json()["risk"]
    assert c.get(f"{p}/specs").json()["features"]
    assert c.post(f"{p}/memories", json={"text": "Ledger rows are append-only", "kind": "convention"}).json()["id"]
    assert c.get(f"{p}/search", params={"q": "ledger"}).json()
    assert c.get("/api/p/p_missing/overview").status_code == 404
    assert c.get("/api/session", headers={"host": "evil.example"}).status_code == 400  # DNS-rebinding defence
    assert "<title>" in c.get("/").text


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
    assert server is not None and len(mcp_server.TOOLS) == 10  # the compact core set


def test_mcp_status_tool_reports_project_health(cairn, monkeypatch):
    import asyncio

    from cairn import mcp_server
    mcp_server._cairn.cache_clear()
    monkeypatch.setattr(mcp_server, "_cairn", lambda: cairn)
    out = mcp_server.cairn_status()
    assert cairn.project.name in out and "last sync" in out
    assert "model" in out  # availability is always reported (none in tests)
    assert "001" in out and "2/3" in out  # the active spec and its task progress
    assert "drift" in out
    assert "cairn_status" in {t.__name__ for t in mcp_server.CORE}
    listed = asyncio.run(mcp_server.build(mode="core").list_tools())
    assert len(listed) == 13  # tools/list grew 12 -> 13 with the status tool


def test_mcp_tool_descriptions_lead_with_the_trigger():
    # Agents route by description: every tool must say WHEN to call it, first words, and stay cheap to read.
    from cairn import mcp_server
    for tool in mcp_server.CORE:
        d = tool.__doc__ or ""
        assert d.startswith("Call "), f"{tool.__name__}: description must open with the WHEN-trigger ('Call ...')"
        assert len(d) <= 400, f"{tool.__name__}: description is {len(d)} chars, cap is 400"


def test_statusline_shows_spec_number(cairn, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    line = hooks.statusline(cairn, "{}")
    assert " 001 2/3" in line and "001-" not in line


def test_installed_agents_reads_configs(repo):
    (repo / ".claude").mkdir()
    proj = Project.discover(repo)
    proj.ensure_dir()
    assert agents.installed(proj) == []
    agents.install(proj, ["claude"])
    assert agents.installed(proj) == ["claude"]
    agents.uninstall(proj)
    assert agents.installed(proj) == []


def test_doctor_shows_model_hint_and_wired_agents(cairn, repo, monkeypatch):
    monkeypatch.setenv("COLUMNS", "260")
    agents.install(cairn.project, ["claude"])
    out = CliRunner().invoke(app, ["doctor"]).output
    assert "sign in to Claude Code" in out  # tests run with no model: the fix names every way to get one
    wired = next(ln for ln in out.splitlines() if "Agents" in ln).split("also detected")[0]
    assert "Claude Code" in wired and "Codex" not in wired and "Gemini" not in wired


def test_http_impact_and_why_reject_degenerate_targets(cairn, tmp_path):
    c, p = _client(cairn, tmp_path)
    assert c.get(f"{p}/impact", params={"target": "."}).status_code == 400
    assert c.get(f"{p}/why", params={"target": "/"}).status_code == 400
    assert c.get(f"{p}/impact", params={"target": "PaymentService"}).status_code == 200


def test_http_views_for_the_page(cairn, tmp_path):
    c, p = _client(cairn, tmp_path)
    im = c.get(f"{p}/impact", params={"target": "shop/payments.py"}).json()
    assert im["traversal"] and all({"file", "depth", "via_file"} <= set(t) for t in im["traversal"])
    assert im["tokens"]["used"] <= im["tokens"]["budget"] == 1500  # what cairn_impact sends
    assert im["source"]["tokens"] > 0
    arch = c.get(f"{p}/graph/architecture").json()
    assert arch["level"] == "file" and any(n["id"] == "shop/api.py" for n in arch["nodes"])
    assert c.get(f"{p}/agents").json()["source"]["path"] == ".cairn/sessions.db"
    assert c.get(f"{p}/savings").json()["recent"] == []  # looking at a pack on the page is not serving one


def test_http_spec_docs_tasks_and_settings(cairn, tmp_path):
    c, p = _client(cairn, tmp_path)
    assert "Refunds" in c.get(f"{p}/specs/001-refunds/doc/spec.md").json()["markdown"]
    assert c.get(f"{p}/specs/001-refunds/doc/..%2F..%2Fshop%2Fapi.py").status_code == 404  # no escaping the spec
    t = c.patch(f"{p}/specs/001-refunds/tasks/T003", json={"done": True}).json()
    assert t["id"] == "T003" and t["done"] is True
    assert "- [x] T003" in (cairn.project.root / "specs/001-refunds/tasks.md").read_text(encoding="utf-8")
    assert c.patch(f"{p}/settings", json={"sessions.capture": False}).json()["sessions"]["capture"] is False
    assert c.patch(f"{p}/settings", json={"server.mode": "team"}).status_code == 400  # not a project setting


def test_session_briefing_counts_as_cost(cairn):
    hooks.session_start(cairn)
    [q] = cairn.brain.queries()
    assert (q["surface"], q["kind"], q["source_tokens"]) == ("hook", "brief", None) and q["sent_tokens"] > 0


def test_mcp_tool_calls_count_as_agent_context(cairn, monkeypatch):
    from cairn import mcp_server
    from cairn.core import Cairn
    mcp_server._cairn.cache_clear()
    monkeypatch.setattr(Cairn, "here", classmethod(lambda cls, start=None: cairn))
    mcp_server.cairn_impact("PaymentService")
    mcp_server._cairn.cache_clear()
    assert [(q["surface"], q["kind"]) for q in cairn.brain.queries()] == [("mcp", "impact")]


def test_cli_json_is_counted_and_stays_lean(cairn):
    runner = CliRunner()
    out = runner.invoke(app, ["impact", "PaymentService", "--json"]).output
    assert not {"traversal", "target_nodes", "evidence_files"} & set(json.loads(out))  # page-only data
    runner.invoke(app, ["why", "PaymentService", "--json"])
    why_q, impact_q = cairn.brain.queries()
    assert (why_q["kind"], impact_q["kind"], impact_q["surface"]) == ("why", "impact", "cli")
    assert impact_q["sent_tokens"] == max(1, len(out.rstrip("\n")) // 4)  # what the reader actually got


def _mcp_list(client, path, headers=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    r = client.post(path, json=body, headers={"accept": "application/json, text/event-stream", **(headers or {})})
    return r, ([t["name"] for t in r.json()["result"]["tools"]] if r.status_code == 200 else None)


def test_mcp_registry_core_and_all():
    import asyncio

    from cairn import mcp_server
    core = asyncio.run(mcp_server.build(mode="core").list_tools())
    full = asyncio.run(mcp_server.build(mode="all").list_tools())
    assert {"cairn_context", "cairn_impact", "cairn_remember", "cairn_facts"} <= {t.name for t in core}
    assert len(full) > len(core) + 30  # every graph and timeline operation
    assert {"cairn_graph_shortest_path", "cairn_timeline_search_facts"} <= {t.name for t in full}


def test_mcp_over_http_respects_roles(cairn, tmp_path):
    from fastapi.testclient import TestClient

    from cairn.platform import Platform, ServerConfig
    from cairn.server import create_app
    config = ServerConfig(mode="team", allowed_hosts=("testserver", "127.0.0.1"))
    platform = Platform(home=tmp_path / "home", config=config)
    owner = platform.create_user("owner@example.com", "Owner", "correct horse battery")
    viewer = platform.create_user("viewer@example.com", "Viewer", "correct horse battery")
    team = platform.create_team("Shop", owner_id=owner.id)
    platform.add_member(team.id, viewer.id, "viewer")
    proj = platform.register_local_project(cairn.project.root, team_id=team.id)
    other = platform.create_team("Elsewhere", owner_id=viewer.id)
    _, owner_token = platform.issue_token(owner.id, team.id, "agent")
    _, viewer_token = platform.issue_token(viewer.id, team.id, "agent")
    _, stranger_token = platform.issue_token(viewer.id, other.id, "agent")
    with TestClient(create_app(platform=platform, config=config), base_url="http://127.0.0.1") as c:
        path = f"/mcp/{proj.id}/"
        assert _mcp_list(c, path)[0].status_code == 401                                     # no token
        r, owner_tools = _mcp_list(c, path, {"authorization": f"Bearer {owner_token}"})
        assert r.status_code == 200 and "cairn_remember" in owner_tools
        r, viewer_tools = _mcp_list(c, path, {"authorization": f"Bearer {viewer_token}"})
        assert r.status_code == 200 and "cairn_impact" in viewer_tools and "cairn_remember" not in viewer_tools
        assert _mcp_list(c, path, {"authorization": f"Bearer {stranger_token}"})[0].status_code == 404  # other team


def test_agents_connect_never_writes_the_token(repo):
    proj = Project.discover(repo)
    proj.ensure_dir()
    agents.connect(proj, "https://cairn.example.com/", "p_123")
    cfg = json.loads((repo / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["cairn"]
    assert cfg["url"] == "https://cairn.example.com/mcp/p_123/" and cfg["type"] == "http"
    assert cfg["headers"]["Authorization"] == "Bearer ${CAIRN_TOKEN}"  # expanded by the agent at runtime


def test_uninstall_removes_skills_and_hooks_on_a_custom_hooks_path(repo):
    import subprocess
    (repo / ".claude").mkdir()
    subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", ".githooks"], check=True)
    proj = Project.discover(repo)
    proj.ensure_dir()
    agents.install(proj, ["claude"])
    hooks.install_git_hooks(proj)
    assert (repo / ".githooks" / "post-commit").exists()
    assert any(p.name.startswith("cairn-") for p in (repo / ".claude" / "skills").iterdir())
    agents.uninstall(proj)
    hooks.remove_git_hooks(proj)
    assert not (repo / ".githooks" / "post-commit").exists()
    assert not [p for p in (repo / ".claude" / "skills").glob("cairn-*")]


def test_team_server_accepts_session_events_from_agents(cairn, tmp_path):
    from fastapi.testclient import TestClient

    from cairn.platform import Platform, ServerConfig
    from cairn.server import create_app
    config = ServerConfig(mode="team", allowed_hosts=("127.0.0.1",))
    platform = Platform(home=tmp_path / "home", config=config)
    owner = platform.create_user("owner@example.com", "Owner", "correct horse battery")
    viewer = platform.create_user("viewer@example.com", "Viewer", "correct horse battery")
    team = platform.create_team("Shop", owner_id=owner.id)
    platform.add_member(team.id, viewer.id, "viewer")
    proj = platform.register_local_project(cairn.project.root, team_id=team.id)
    _, agent_token = platform.issue_token(owner.id, team.id, "laptop agent")  # default scopes include capture
    _, viewer_token = platform.issue_token(viewer.id, team.id, "viewer")
    event = {"event": "session-init", "platform": "claude-code",
             "payload": {"session_id": "remote-1", "prompt": "Add refunds", "cwd": str(cairn.project.root)}}
    with TestClient(create_app(platform=platform, config=config), base_url="http://127.0.0.1") as c:
        path = f"/api/p/{proj.id}/sessions/events"
        assert c.post(path, json=[event], headers={"authorization": f"Bearer {viewer_token}"}).status_code == 403
        r = c.post(path, json=[event], headers={"authorization": f"Bearer {agent_token}"})
        assert r.status_code == 200, r.text
        assert c.post(path, json={"events": "nope"},
                      headers={"authorization": f"Bearer {agent_token}"}).status_code == 400
        member = platform.create_user("member@example.com", "Member", "correct horse battery")
        platform.add_member(team.id, member.id, "member")
        _, member_token = platform.issue_token(member.id, team.id, "member laptop")
        assert c.post(path, json=[event], headers={"authorization": f"Bearer {member_token}"}).status_code == 200
        listed = c.get(f"/api/p/{proj.id}/sessions/list", headers={"authorization": f"Bearer {viewer_token}"})
        names = {s["pushed_by_name"] for s in listed.json()["items"] if s.get("pushed_by")}
        assert names == {"Owner", "Member"}
    from cairn.engines.recall import api as recall_api
    pushed = {s["id"]: s["pushed_by"] for s in recall_api.sessions(cairn.project.root)["items"]
              if s["id"].endswith(":remote-1")}
    assert pushed == {f"{owner.id}:remote-1": owner.id, f"{member.id}:remote-1": member.id}


def test_agents_connect_records_the_team_server(repo):
    proj = Project.discover(repo)
    proj.ensure_dir()
    agents.connect(proj, "https://cairn.example.com/", "p_123")
    proj.reload()
    assert proj.cfg("team.server") == "https://cairn.example.com" and proj.cfg("team.project") == "p_123"


def test_push_reads_the_token_from_the_connected_variable(repo, monkeypatch):
    from cairn.engines.recall import remote
    proj = Project.discover(repo)
    proj.ensure_dir()
    agents.connect(proj, "https://cairn.example.com", "p_1", env_var="TEAM_CAIRN_KEY")
    monkeypatch.delenv("CAIRN_TOKEN", raising=False)
    monkeypatch.setenv("TEAM_CAIRN_KEY", "cairn_abc_123")
    assert remote.status(repo)["token"] is True


def _team_app(cairn, tmp_path):
    from fastapi.testclient import TestClient

    from cairn.platform import Platform, ServerConfig
    from cairn.server import create_app
    config = ServerConfig(mode="team", allowed_hosts=("127.0.0.1",))
    platform = Platform(home=tmp_path / "home", config=config)
    owner = platform.create_user("owner@example.com", "Owner", "correct horse battery")
    team = platform.create_team("Shop", owner_id=owner.id)
    proj = platform.register_local_project(cairn.project.root, team_id=team.id)
    _, token = platform.issue_token(owner.id, team.id, "agent", scopes=["all"])
    client = TestClient(create_app(platform=platform, config=config), base_url="http://127.0.0.1")
    return client, f"/api/p/{proj.id}", {"authorization": f"Bearer {token}"}, platform, proj


def test_team_server_never_takes_network_destinations_from_the_repo(cairn, tmp_path):
    cairn.project.set_cfg("models.base_url", "https://attacker.example/v1")  # e.g. pushed in .cairn/config.toml
    cairn.project.set_cfg("temporal.url", "bolt://attacker.example:7687")
    client, p, auth, _platform, proj = _team_app(cairn, tmp_path)
    with client as c:
        served = c.app.state.hub.cairn(proj.id)
        assert served.router.base_url is None and served.project.cfg("temporal.url") == ""
        assert served.project.id == proj.id  # shared stores are keyed by the platform id, not the folder name
        r = c.patch(f"{p}/settings", json={"models.base_url": "https://attacker.example/v1"}, headers=auth)
        assert r.status_code == 403
        assert c.patch(f"{p}/settings", json={"context.budget": 2000}, headers=auth).status_code == 200


def test_graph_tools_cannot_reach_another_projects_folder(cairn, tmp_path):
    other = tmp_path / "vault"
    other.mkdir()
    (other / ".cairn" / "graph").mkdir(parents=True)
    (other / ".cairn" / "graph" / "graph.json").write_text(json.dumps({"nodes": [
        {"id": "vault_rotate_master_key", "label": "rotate_master_key()", "file_type": "code",
         "source_file": "vault.py", "community": 0}], "links": []}), encoding="utf-8")
    client, p, auth, _platform, _proj = _team_app(cairn, tmp_path)
    with client as c:
        r = c.post(f"{p}/graph/tools/query_graph", headers=auth,
                   json={"question": "rotate_master_key", "project_path": str(other)})
        assert r.status_code in (200, 400) and "vault" not in r.text


def test_memory_routes_stay_in_their_project(cairn, tmp_path):
    cairn.remember("Refunds always go through the gateway", kind="decision")
    client, p, auth, _platform, proj = _team_app(cairn, tmp_path)
    with client as c:
        r = c.get(f"{p}/memory/memories", params={"project_id": "someone-elses-repo"}, headers=auth)
        if r.status_code == 200:  # the engine may be unavailable in minimal installs (503)
            assert all(m.get("project_id") in (None, proj.id, cairn.project.id) for m in r.json()["results"])


# ---- regressions from the integration review ------------------------------------------------------------
def _local_hub(cairn, tmp_path):
    from cairn.platform import Platform, ServerConfig
    from cairn.server import Hub
    platform = Platform(home=tmp_path / "hub-home", config=ServerConfig(mode="local"))
    platform.ensure_local_owner()
    rec = platform.register_local_project(cairn.project.root)
    return Hub(platform), platform, rec


def test_read_only_agents_get_no_corpus_or_pr_tools_over_the_network():
    import asyncio

    from cairn import mcp_server
    names = {t.name for t in asyncio.run(mcp_server.build(mode="all", read_only=True,
                                                          resolver=lambda: None).list_tools())}
    assert not names & {"cairn_build_corpus", "cairn_prime_corpus", "cairn_query_corpus", "cairn_remember"}
    assert not names & mcp_server.NETWORK_HIDDEN


def test_settings_change_keeps_one_live_cairn(cairn, tmp_path):
    hub, _platform, rec = _local_hub(cairn, tmp_path)
    first = hub.cairn(rec.id)
    first.project.set_cfg("context.budget", 1234)
    hub.reconfigure(rec.id)
    assert hub.cairn(rec.id) is first and first.budget_default == 1234
    assert hub.cairn(rec.id, surface="mcp").memory is first.memory


def test_deleting_a_project_stops_its_threads_and_closes_it(cairn, tmp_path):
    hub, platform, rec = _local_hub(cairn, tmp_path)
    hub.watch_sessions(rec.id)
    stop = hub._watch[rec.id]
    platform.delete_project(rec.id, purge=False)
    assert stop.is_set() and rec.id not in hub._watch and rec.id not in hub._cairns


def test_watching_a_project_whose_files_are_missing_can_be_retried(cairn, tmp_path):
    import pytest
    from fastapi import HTTPException
    hub, platform, _rec = _local_hub(cairn, tmp_path)
    ghost = tmp_path / "moved-away"
    ghost.mkdir()
    rec = platform.register_local_project(ghost)
    ghost.rmdir()
    with pytest.raises(HTTPException):
        hub.watch_sessions(rec.id)
    assert rec.id not in hub._watch


def test_a_subfolder_project_keeps_its_own_stores(cairn, tmp_path):
    hub, platform, _rec = _local_hub(cairn, tmp_path)
    sub = cairn.project.root / "shop"
    rec = platform.register_local_project(sub)
    assert hub.cairn(rec.id).project.dir == sub / ".cairn"


def test_a_sync_requested_while_one_runs_runs_after_it(cairn, tmp_path, monkeypatch):
    import threading
    import time as _t

    from cairn import server as server_mod
    hub, _platform, rec = _local_hub(cairn, tmp_path)
    calls, gate = [], threading.Event()

    def fake_run(c, deep=None, progress=None):
        calls.append(_t.time())
        gate.wait(5)
        return {"seconds": 0}
    monkeypatch.setattr(server_mod.sync, "run", fake_run)
    t = threading.Thread(target=hub.run_sync, args=(rec.id,))
    t.start()
    while not calls:
        _t.sleep(0.01)
    assert hub.run_sync(rec.id) == {"queued": True}  # not dropped
    gate.set()
    t.join(5)
    assert len(calls) == 2 and not hub.syncing


def test_bad_input_is_a_400_not_a_500(cairn, tmp_path):
    c, p = _client(cairn, tmp_path)
    assert c.get(f"{p}/facts", params={"at": "not-a-date"}).status_code in (400, 503)
    assert c.post(f"{p}/ask", json={"question": "x", "budget": "lots"}).status_code == 422
    assert c.patch(f"{p}/settings", json={"deep.budget_tokens": None}).status_code == 400
    assert c.patch(f"{p}/settings", json={"context.budget": "2000"}).status_code == 400


def test_uninstall_keeps_the_users_own_hooks(repo):
    (repo / ".claude").mkdir()
    proj = Project.discover(repo)
    proj.ensure_dir()
    agents.install(proj, ["claude"])
    settings_path = repo / ".claude" / "settings.json"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    data["hooks"].setdefault("PostToolUse", []).append(
        {"matcher": "Edit", "hooks": [{"type": "command", "command": "/Users/me/code/cairn-tools/format.sh"}]})
    data["hooks"]["SessionStart"][0]["hooks"].append({"type": "command", "command": "say done"})
    settings_path.write_text(json.dumps(data), encoding="utf-8")
    agents.install(proj, ["claude"])  # a re-init must not drop them either
    agents.uninstall(proj)
    left = json.dumps(json.loads(settings_path.read_text(encoding="utf-8")).get("hooks", {}))
    assert "cairn-tools/format.sh" in left and "say done" in left and "cairn.capture" not in left


def test_git_hooks_follow_a_home_relative_hooks_path(repo, tmp_path):
    import os
    import subprocess
    subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath", "~/.myhooks"], check=True)
    proj = Project.discover(repo)
    hooks.install_git_hooks(proj)
    assert (Path(os.environ["HOME"]) / ".myhooks" / "post-commit").exists()
    assert not (repo / "~").exists()


def test_cairn_down_never_stops_another_process(tmp_path, monkeypatch):
    import subprocess
    import sys

    from cairn import daemon
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (tmp_path / "server.json").write_text(json.dumps({"pid": other.pid, "port": 1}), encoding="utf-8")
        assert daemon.stop() is False and other.poll() is None
    finally:
        other.kill()


def test_pushed_session_events_are_size_capped(cairn, tmp_path, monkeypatch):
    from cairn import server as server_mod
    monkeypatch.setattr(server_mod, "MAX_EVENTS_BODY", 1024)
    c, p = _client(cairn, tmp_path)
    assert c.post(f"{p}/sessions/events", content=b"[" + b" " * 2048 + b"]",
                  headers={"content-type": "application/json"}).status_code == 413
    assert c.post(f"{p}/sessions/events", content=b"not json",
                  headers={"content-type": "application/json"}).status_code == 400
    assert c.post(f"{p}/sessions/events", json=[]).status_code == 200


def test_a_connected_repository_leaves_model_work_to_the_server(repo, monkeypatch):
    from cairn.engines.recall import hooks as recall_hooks
    from cairn.engines.recall.settings import load
    proj = Project.discover(repo)
    proj.ensure_dir()
    assert load(repo)["worker_model"] is True
    agents.connect(proj, "https://cairn.example.com", "p_1")
    assert load(repo)["worker_model"] is False
    spawned = []
    monkeypatch.delenv("CAIRN_RECALL_NO_SPAWN", raising=False)
    monkeypatch.setattr(recall_hooks, "worker_running", lambda root: False)
    monkeypatch.setattr(recall_hooks, "_spawn", lambda root, module, args, log: spawned.append(args) or True)
    recall_hooks.spawn_worker(repo, model=False)
    assert spawned[-1][-1] == "--no-model"


def test_the_repository_map_links_repositories_the_caller_can_see(cairn, tmp_path):
    from fastapi.testclient import TestClient

    from cairn import sync
    from cairn.core import Cairn
    from cairn.platform import Platform, ServerConfig
    from cairn.server import create_app
    web = tmp_path / "web-repo"
    (web / "web").mkdir(parents=True)
    (web / "web" / "__init__.py").write_text("", encoding="utf-8")
    (web / "web" / "views.py").write_text("from shop.api import checkout\n\n\ndef buy(o):\n    return checkout(o, 1)\n", encoding="utf-8")
    other = Cairn.here(web)
    other.project.ensure_dir()
    sync.run(other)
    config = ServerConfig(mode="team", allowed_hosts=("127.0.0.1",))
    platform = Platform(home=tmp_path / "home", config=config)
    owner = platform.create_user("owner@example.com", "Owner", "correct horse battery")
    stranger = platform.create_user("stranger@example.com", "Stranger", "correct horse battery")
    shop_team = platform.create_team("Shop", owner_id=owner.id)
    elsewhere = platform.create_team("Elsewhere", owner_id=stranger.id)
    shop = platform.register_local_project(cairn.project.root, team_id=shop_team.id)
    site = platform.register_local_project(web, team_id=shop_team.id)
    _, owner_token = platform.issue_token(owner.id, shop_team.id, "owner")
    _, stranger_token = platform.issue_token(stranger.id, elsewhere.id, "stranger")
    with TestClient(create_app(platform=platform, config=config), base_url="http://127.0.0.1") as c:
        data = c.get("/api/repos/map", headers={"authorization": f"Bearer {owner_token}"}).json()
        assert {n["id"] for n in data["nodes"]} == {shop.id, site.id}
        assert any(link["source"] == site.id and link["target"] == shop.id for link in data["links"])
        seen = c.get("/api/repos/map", headers={"authorization": f"Bearer {stranger_token}"}).json()
        assert seen["nodes"] == [] and seen["links"] == []
        assert c.get("/api/repos/map").status_code == 401


# ---- fixes from the independent UI test ----------------------------------------------------------------------
def test_a_folder_opens_everything_under_it(cairn, tmp_path):
    c, p = _client(cairn, tmp_path)
    nodes = c.get(f"{p}/graph/data", params={"scope": "file:shop"}).json()["nodes"]
    files = {n.get("file") or n.get("source_file") for n in nodes}
    assert nodes and {f for f in files if f} <= {f for f in files if f and f.startswith("shop/")}


def test_an_unknown_name_is_not_answered_with_something_else(cairn):
    t = cairn.resolve("checkout-missing-thing.xyz")
    assert t.nodes == [] and t.files == []
    assert cairn.resolve("checkout").nodes  # a real name still resolves


def test_settings_reject_values_that_make_no_sense(cairn, tmp_path):
    c, p = _client(cairn, tmp_path)
    for body in ({"models.provider": "nonsense"}, {"context.budget": -100}, {"context.budget": 99_999_999},
                 {"models.fast": "  "}, {"models.base_url": "ftp://x"}, {"deep.enabled": "sometimes"}):
        r = c.patch(f"{p}/settings", json=body)
        assert r.status_code == 400, (body, r.text)
    ok = c.patch(f"{p}/settings", json={"context.budget": 2400, "models.provider": "auto"})
    assert ok.status_code == 200 and ok.json()["context"]["budget"] == 2400
    got = c.get(f"{p}/settings").json()
    assert got["locked"] == [] and "models.provider" in got["rules"]  # a local project can change everything


def test_a_failed_sync_step_is_reported_as_failed(cairn, monkeypatch):
    from cairn import sync
    from cairn.engines import mapper
    events = []
    monkeypatch.setattr(mapper, "build", lambda root: (False, "tree-sitter crashed"))
    sync.run(cairn, progress=lambda name, state, msg: events.append((name, state)))
    assert ("map", "fail") in events
    assert "map" in cairn.overview()["sync_failed"]
    monkeypatch.undo()
    sync.run(cairn)
    assert cairn.overview()["sync_failed"] == {}


def test_files_agents_touch_are_only_this_repositorys(cairn):
    from cairn.core import in_repo
    assert in_repo("shop/api.py") and not in_repo("/tmp/scratch/x.py") and not in_repo("~/.claude/settings.json")
    assert not in_repo("../other/x.py") and not in_repo("C:/Users/x.py")
    with cairn.brain.tx() as db:
        for dst in ("file:shop/api.py", "file:/Users/me/.claude/settings.json"):
            db.execute("INSERT INTO links(src, dst, rel, source) VALUES ('obs:1', ?, 'reads', 'test')", (dst,))
    assert [f["path"] for f in cairn.agent_activity()["files"]] == ["shop/api.py"]


def test_changes_through_the_page_are_in_the_team_audit_log(cairn, tmp_path):
    from fastapi.testclient import TestClient

    from cairn.platform import Platform, ServerConfig
    from cairn.server import create_app
    config = ServerConfig(mode="team", allowed_hosts=("127.0.0.1",))
    platform = Platform(home=tmp_path / "home", config=config)
    owner = platform.create_user("owner@example.com", "Owner", "correct horse battery")
    team = platform.create_team("Shop", owner_id=owner.id)
    proj = platform.register_local_project(cairn.project.root, team_id=team.id)
    _, token = platform.issue_token(owner.id, team.id, "laptop", scopes=["*"])
    h = {"authorization": f"Bearer {token}"}
    with TestClient(create_app(platform=platform, config=config), base_url="http://127.0.0.1") as c:
        p = f"/api/p/{proj.id}"
        mid = c.post(f"{p}/memories", json={"text": "Refunds are capped at the captured amount"}, headers=h).json()["id"]
        c.delete(f"{p}/memories/{mid}", headers=h)
        c.patch(f"{p}/specs/001-refunds/tasks/T003", json={"done": True}, headers=h)
        c.patch(f"{p}/settings", json={"context.budget": 2000}, headers=h)
        c.post(f"{p}/sync", json={}, headers=h)
    actions = {e.action for e in platform.audit_entries(team_id=team.id, limit=50)}
    assert {"memory.add", "memory.forget", "task.done", "project.settings", "project.sync"} <= actions


def test_the_first_session_check_does_not_error_when_signed_out(cairn, tmp_path):
    from fastapi.testclient import TestClient

    from cairn.platform import Platform, ServerConfig
    from cairn.server import create_app
    config = ServerConfig(mode="team", allowed_hosts=("127.0.0.1",))
    platform = Platform(home=tmp_path / "home", config=config)
    with TestClient(create_app(platform=platform, config=config), base_url="http://127.0.0.1") as c:
        assert c.get("/api/session", params={"optional": 1}).json() == {"signed_in": False, "mode": "team"}
        assert c.get("/api/session").status_code == 401
    local, _ = _client(cairn, tmp_path / "local")
    assert local.get("/api/session", params={"optional": 1}).json()["signed_in"] is True


def test_sync_writes_the_wiki(cairn):
    wiki = cairn.project.map_dir / "wiki"
    assert wiki.is_dir() and (wiki / "index.md").exists()


def test_the_wiki_lands_in_the_project_even_from_a_foreign_cwd(cairn, tmp_path, monkeypatch):
    # A server process runs with cwd = $CAIRN_HOME, not the project root — the
    # wiki export must not silently write relative to whatever the cwd happens
    # to be (#... it used to land under the process's home instead of the
    # project's .cairn/graph/wiki).
    import shutil

    from cairn import sync

    wiki = cairn.project.map_dir / "wiki"
    shutil.rmtree(wiki)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    sync.run(cairn)
    assert wiki.is_dir() and (wiki / "index.md").exists()
    assert not (elsewhere / ".cairn").exists()

    shutil.rmtree(wiki)
    c, p = _client(cairn, tmp_path)
    res = c.post(f"{p}/graph/wiki")
    assert res.status_code == 200 and res.json()["ok"], res.text
    assert wiki.is_dir() and (wiki / "index.md").exists()
    assert not (elsewhere / ".cairn").exists()


def test_someone_who_must_change_their_password_is_told_so_not_bounced(cairn, tmp_path):
    from fastapi.testclient import TestClient

    from cairn.platform import Platform, ServerConfig
    from cairn.server import create_app
    config = ServerConfig(mode="team", allowed_hosts=("127.0.0.1",))
    platform = Platform(home=tmp_path / "home", config=config)
    owner = platform.create_user("owner@example.com", "Owner", "correct horse battery", must_change_password=True)
    team = platform.create_team("Shop", owner_id=owner.id)
    platform.register_local_project(cairn.project.root, team_id=team.id)
    with TestClient(create_app(platform=platform, config=config), base_url="http://127.0.0.1") as c:
        assert c.post("/api/auth/login", json={"email": "owner@example.com",
                                               "password": "correct horse battery"}).status_code == 200
        me = c.get("/api/session", params={"optional": 1})
        assert me.status_code == 200 and me.json()["signed_in"] and me.json()["projects"] == []
        assert me.json()["user"]["must_change_password"] is True


def test_a_foreground_server_records_where_it_listens_without_taking_over(tmp_path):
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.request

    def free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def up(port: int) -> bool:
        try:
            return urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.5).status == 200
        except OSError:
            return False

    env = {**os.environ, "CAIRN_HOME": str(tmp_path / "home")}
    state = tmp_path / "home" / "server.json"
    first, second = free_port(), free_port()
    a = subprocess.Popen([sys.executable, "-m", "cairn", "serve", "--port", str(first)], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    b = None
    try:
        deadline = time.time() + 30
        while not up(first) and time.time() < deadline:
            time.sleep(0.2)
        assert json.loads(state.read_text(encoding="utf-8"))["port"] == first
        b = subprocess.Popen([sys.executable, "-m", "cairn", "serve", "--port", str(second)], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while not up(second) and time.time() < deadline:
            time.sleep(0.2)
        assert json.loads(state.read_text(encoding="utf-8"))["port"] == first  # the live server keeps its record
    finally:
        for proc in (b, a):
            if proc:
                proc.terminate()
                proc.wait(10)
    if os.name != "nt":  # terminate() is a hard kill on Windows, so there is no way-out cleanup to observe
        assert not state.exists()  # the server that wrote it removed it on the way out


def test_a_stopped_server_exits_even_with_a_page_still_open(cairn, tmp_path):
    import os
    import socket
    import subprocess
    import sys
    import time
    import urllib.request

    from cairn.platform import Platform, ServerConfig
    home = tmp_path / "home"
    env = {**os.environ, "CAIRN_HOME": str(home)}
    platform = Platform(home=home, config=ServerConfig(mode="local"))
    platform.ensure_local_owner()
    pid = platform.register_local_project(cairn.project.root).id
    platform.close()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([sys.executable, "-m", "cairn", "serve", "--port", str(port)], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                page = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/p/{pid}/stream", timeout=5)
                break
            except OSError:
                time.sleep(0.2)
        assert page.read(6) == b"retry:"  # a live-update stream is open, as a browser tab keeps it
        started = time.time()
        server.terminate()
        server.wait(15)
        assert time.time() - started < 10
    finally:
        if server.poll() is None:
            server.kill()

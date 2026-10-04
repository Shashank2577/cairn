"""Memory at session start and session capture for every agent: install, uninstall, the shared context."""
from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from cairn import agents
from cairn.engines.recall import hooks as recall_hooks
from cairn.engines.recall import integrations as ig
from cairn.engines.recall.store import Store

OTHERS = ["gemini", "codex", "cursor", "opencode", "copilot", "windsurf", "antigravity", "vscode"]
COPILOT_FILE = ".github/instructions/cairn-context.instructions.md"


@pytest.fixture()
def wired(cairn, monkeypatch):
    """The fixture repository with a decision to remember and no agent CLI reachable (no `codex mcp add`)."""
    monkeypatch.setattr(agents.shutil, "which", lambda name: None)
    monkeypatch.setenv("CAIRN_RECALL_NO_SPAWN", "1")
    cairn.remember("Refund retries use exponential backoff capped at 5 attempts; marker ZEBRA-7731", "decision")
    return cairn


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _context(root, platform, **payload):
    """What an agent's session-start hook hands it."""
    _, out = recall_hooks.run("context", {"session_id": "s1", "cwd": str(root), **payload}, platform)
    if platform == "cursor":
        return out["additional_context"]
    return out["hookSpecificOutput"]["additionalContext"]


def _excluded(root):
    return (root / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()


def test_every_agent_is_wired_and_unwired_without_touching_the_users_entries(wired):
    root = wired.project.root
    # the user's own entries in every file Cairn merges into
    (root / ".gemini").mkdir()
    (root / ".gemini" / "settings.json").write_text(json.dumps(
        {"theme": "dark", "hooks": {"BeforeTool": [{"matcher": "write_file", "hooks": [{"type": "command",
                                                                                        "command": "./lint.sh"}]}]}}), encoding="utf-8")
    (root / ".codex").mkdir()
    (root / ".codex" / "hooks.json").write_text(json.dumps(
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "./notify.sh"}]}]}}), encoding="utf-8")
    (root / ".cursor").mkdir()
    (root / ".cursor" / "hooks.json").write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "./x.sh"}]}}), encoding="utf-8")
    (root / ".github" / "hooks").mkdir(parents=True)
    (root / ".github" / "hooks" / "team.json").write_text('{"version": 1, "hooks": {}}', encoding="utf-8")
    (root / "AGENTS.md").write_text("# House rules\n\nUse tabs.\n", encoding="utf-8")
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "exclude").write_text("# mine\n*.log\n", encoding="utf-8")

    changed = agents.install(wired.project, ["claude", *OTHERS])
    assert {"gemini", "codex", "cursor", "opencode", "copilot", "antigravity", "windsurf"} <= set(changed)
    assert not [p for files in changed.values() for p in files if p.startswith("/")]  # nothing outside the repo

    gemini = _json(root / ".gemini" / "settings.json")
    assert gemini["theme"] == "dark" and gemini["hooks"]["BeforeTool"][0]["hooks"][0]["command"] == "./lint.sh"
    assert gemini["hooks"]["SessionStart"][0]["hooks"][0]["command"].endswith("--platform gemini context")
    assert gemini["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] == ig.GEMINI_CONTEXT_TIMEOUT_MS
    assert {"BeforeAgent", "AfterTool", "AfterAgent", "SessionEnd"} <= set(gemini["hooks"])
    codex = _json(root / ".codex" / "hooks.json")["hooks"]
    assert codex["Stop"][0]["hooks"][0]["command"] == "./notify.sh"
    assert codex["Stop"][1]["hooks"][0]["command"].endswith("--platform codex summarize")
    assert codex["SessionStart"][0]["matcher"] == "startup|resume|clear|compact"
    assert codex["SessionStart"][0]["hooks"][0]["command"].endswith("--platform codex context")
    cursor = _json(root / ".cursor" / "hooks.json")["hooks"]
    assert cursor["stop"][0] == {"command": "./x.sh"}
    assert cursor["sessionStart"][0]["command"].endswith("--platform cursor context")
    plugin = (root / ".opencode" / "plugins" / "cairn.js").read_text(encoding="utf-8")
    assert json.dumps(sys.executable) in plugin and "experimental.chat.system.transform" in plugin
    assert "const CAPTURE = true;" in plugin
    copilot = _json(root / ".github" / "hooks" / "cairn.json")
    assert set(copilot["hooks"]) == {"userPromptSubmitted", "postToolUse", "agentStop", "sessionEnd"}
    assert copilot["hooks"]["agentStop"][0]["bash"].endswith("--platform copilot summarize")

    # instruction-file agents: an untracked file each, never the committed AGENTS.md
    instructions = (root / COPILOT_FILE).read_text(encoding="utf-8")
    assert instructions.startswith('---\napplyTo: "**"\n---\n') and "ZEBRA-7731" in instructions
    assert "<cairn-context>" not in instructions  # Copilot drops unknown tags with their content
    rules = (root / ".agents" / "rules" / "cairn-context.md").read_text(encoding="utf-8")
    assert rules.startswith("---\ntrigger: always_on\n---\n") and "ZEBRA-7731" in rules
    assert len((root / ".windsurf" / "rules" / "cairn-context.md").read_text(encoding="utf-8")) <= 12_000
    for rel in (COPILOT_FILE, ".agents/rules/cairn-context.md", ".windsurf/rules/cairn-context.md"):
        assert "/" + rel in _excluded(root)
    assert _excluded(root)[:2] == ["# mine", "*.log"]
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, capture_output=True,
                            text=True, check=True, encoding="utf-8", errors="replace").stdout
    assert "cairn-context" not in status
    agents_md = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert agents_md.startswith("# House rules") and "ZEBRA" not in agents_md and agents.CONTEXT_OPEN not in agents_md

    status = agents.memory_status(wired.project)
    for agent in ("claude", "gemini", "codex", "cursor"):
        assert status[agent]["injection"] == "SessionStart hook" and status[agent]["capture"], agent
    assert status["codex"]["approved"] is False  # nothing approved in ~/.codex yet
    assert status["opencode"] == {"injection": "system prompt (plugin)", "capture": True}
    assert status["copilot"] == {"injection": f"{COPILOT_FILE} (untracked)", "capture": True}
    assert status["vscode"] == {"injection": f"{COPILOT_FILE} (untracked)", "capture": False}
    assert status["antigravity"]["injection"] == ".agents/rules/cairn-context.md (untracked)"
    assert {"opencode", "copilot", "antigravity", "windsurf"} <= set(agents.installed(wired.project))

    before = {p: p.read_text(encoding="utf-8") for p in (root / ".gemini" / "settings.json", root / ".codex" / "hooks.json",
                                         root / ".cursor" / "hooks.json", root / "AGENTS.md", root / COPILOT_FILE,
                                         root / ".git" / "info" / "exclude")}
    assert set(agents.install(wired.project, ["claude", *OTHERS])) <= {"claude"}  # idempotent (skills are recopied)
    assert all(p.read_text(encoding="utf-8") == t for p, t in before.items())

    agents.uninstall(wired.project)
    assert _json(root / ".gemini" / "settings.json") == {"theme": "dark", "hooks": {"BeforeTool": [
        {"matcher": "write_file", "hooks": [{"type": "command", "command": "./lint.sh"}]}]}}
    assert _json(root / ".codex" / "hooks.json") == {"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "./notify.sh"}]}]}}
    assert _json(root / ".cursor" / "hooks.json") == {"version": 1, "hooks": {"stop": [{"command": "./x.sh"}]}}
    for rel in (".opencode/plugins/cairn.js", ".github/hooks/cairn.json", COPILOT_FILE, ".agents/rules/cairn-context.md",
                ".windsurf/rules/cairn-context.md"):
        assert not (root / rel).exists(), rel
    assert (root / ".github" / "hooks" / "team.json").exists()
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "# House rules\n\nUse tabs.\n"
    assert _excluded(root) == ["# mine", "*.log"]
    assert not any(v["injection"] or v["capture"] for v in agents.memory_status(wired.project).values())


def test_no_capture_still_wires_memory(wired):
    root = wired.project.root
    agents.install(wired.project, ["gemini", "codex", "cursor", "opencode", "copilot"], capture=False)
    assert set(_json(root / ".gemini" / "settings.json")["hooks"]) == {"SessionStart"}
    assert set(_json(root / ".codex" / "hooks.json")["hooks"]) == {"SessionStart"}
    assert set(_json(root / ".cursor" / "hooks.json")["hooks"]) == {"sessionStart"}
    assert "const CAPTURE = false;" in (root / ".opencode" / "plugins" / "cairn.js").read_text(encoding="utf-8")
    assert not (root / ".github" / "hooks" / "cairn.json").exists() and (root / COPILOT_FILE).exists()
    assert not any(m["capture"] for m in agents.memory_status(wired.project).values())


def test_every_agent_gets_what_claude_gets(wired):
    root = wired.project.root
    agents.install(wired.project, ["claude", "gemini", "codex", "cursor", "opencode", "copilot"])
    recall_hooks.run("session-init", {"session_id": "s0", "cwd": str(root), "prompt": "Add refund retries"},
                     "claude-code")
    claude_brief = json.loads(subprocess.run(
        [sys.executable, "-m", "cairn", "hook", "session-start"], cwd=root, capture_output=True, text=True,
        check=True, encoding="utf-8", errors="replace").stdout)["hookSpecificOutput"]["additionalContext"]
    claude_timeline = _context(root, "claude-code")
    assert "ZEBRA-7731" in claude_brief
    expected = f"{claude_brief}\n\n{claude_timeline}"
    assert _context(root, "gemini") == expected
    assert _context(root, "codex") == expected
    assert _context(root, "cursor", conversation_id="c1", workspace_roots=[str(root)]) == expected
    assert _context(root, "opencode") == expected  # what the plugin adds to the system prompt
    assert expected == agents.agent_context(wired.project)
    block = (root / COPILOT_FILE).read_text(encoding="utf-8").split(agents.CONTEXT_OPEN + "\n", 1)[1].split("\n" + agents.CONTEXT_CLOSE)[0]
    assert block.startswith(agents.CONTEXT_HEADING + "\n\n" + claude_brief.split("\n", 1)[0])
    assert "ZEBRA-7731" in block and "<cairn" not in block  # plain Markdown: no tags Copilot would drop
    assert "recent context, " not in block  # the render time is left out of the file


def test_context_files_follow_the_memory(wired):
    from cairn import sync
    root = wired.project.root
    agents.install(wired.project, ["copilot"])
    first = (root / COPILOT_FILE).read_text(encoding="utf-8")
    assert not agents.refresh_context(wired.project)  # nothing new: the file is left alone
    wired.remember("Ledger writes go through the outbox table", "convention")
    sync.run(wired)  # a sync refreshes it
    assert "outbox table" in (root / COPILOT_FILE).read_text(encoding="utf-8") and (root / COPILOT_FILE).read_text(encoding="utf-8") != first
    agents.uninstall(wired.project)
    assert not agents.refresh_context(wired.project) and not (root / COPILOT_FILE).exists()  # never recreated


def test_a_users_file_of_the_same_name_is_left_alone(wired):
    root = wired.project.root
    (root / COPILOT_FILE).parent.mkdir(parents=True)
    (root / COPILOT_FILE).write_text("my own instructions\n", encoding="utf-8")
    agents.install(wired.project, ["copilot"])
    agents.uninstall(wired.project)
    assert (root / COPILOT_FILE).read_text(encoding="utf-8") == "my own instructions\n"


def test_install_moves_the_memory_out_of_agents_md(wired):
    root = wired.project.root
    (root / "AGENTS.md").write_text(f"# Rules\n\n{agents.CONTEXT_OPEN}\nold memory\n{agents.CONTEXT_CLOSE}\n", encoding="utf-8")
    agents.install(wired.project, ["codex"])
    assert "old memory" not in (root / "AGENTS.md").read_text(encoding="utf-8")


def test_a_stored_summary_refreshes_the_context_files(wired, monkeypatch):
    from cairn.engines.recall.worker import Worker
    root = wired.project.root
    calls = []
    monkeypatch.setattr(agents, "refresh_context", lambda project, cairn=None: calls.append(project.root))
    worker = Worker(root, router=None)
    with Store.open(root) as st:
        worker.after_store(st, {"observation_ids": []})
        assert calls == []
        worker.after_store(st, {"observation_ids": [], "summary_id": 1})
    assert calls == [root]


def test_codex_hook_approval_is_read_from_codex_config(wired):
    root = wired.project.root
    agents.install(wired.project, ["codex"])
    hooks_file = (root / ".codex" / "hooks.json").resolve()
    toml_file = str(hooks_file).replace("\\", "\\\\")  # a basic TOML string escapes backslashes
    config = ig.codex_dir() / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    entries = [f'[hooks.state."{toml_file}:{event}:0:0"]\ntrusted_hash = "sha256:x"\n'
               for event in ("session_start", "user_prompt_submit", "post_tool_use")]
    config.write_text("\n".join(entries), encoding="utf-8")
    assert agents.codex_hooks_approved(wired.project) is False  # Stop not approved yet
    config.write_text("\n".join([*entries, f'[hooks.state."{toml_file}:stop:0:0"]\ntrusted_hash = "sha256:y"\n']), encoding="utf-8")
    assert agents.codex_hooks_approved(wired.project) is True
    assert config.read_text(encoding="utf-8").count("trusted_hash") == 4  # read, never written


def test_the_memory_is_never_captured_back(wired):
    root = wired.project.root
    agents.install(wired.project, ["copilot"])
    text = (root / COPILOT_FILE).read_text(encoding="utf-8")
    recall_hooks.run("observation", {"session_id": "r1", "cwd": str(root), "tool_name": "Read",
                                     "tool_input": {"file_path": COPILOT_FILE}, "tool_response": {"content": text}},
                     "claude-code")
    with Store.open(root) as st:
        stored = st.db.execute("SELECT tool_response FROM pending_messages").fetchone()[0]
    assert "ZEBRA-7731" not in stored and "applyTo" in stored


def test_cursor_running_claude_hooks_is_not_recorded_twice(wired):
    root = wired.project.root
    res, _ = recall_hooks.run("session-init", {"session_id": "c1", "cwd": str(root), "prompt": "hi there",
                                                "cursor_version": "3.2"}, "claude-code")
    assert not res.get("_recorded")
    res, out = recall_hooks.run("session-init", {"conversation_id": "c1", "workspace_roots": [str(root)],
                                                  "prompt": "hi there", "cursor_version": "3.2"}, "cursor")
    assert res["_recorded"] == 1 and out == {}


def test_copilot_payloads_are_captured(wired):
    root = wired.project.root
    base = {"sessionId": "cp1", "cwd": str(root), "timestamp": 1}
    assert recall_hooks.run("session-init", {**base, "prompt": "Explain the refund flow"}, "copilot")[0]["_recorded"]
    res, out = recall_hooks.run("observation", {**base, "toolName": "view", "toolArgs": '{"path": "shop/api.py"}',
                                                "toolResult": {"resultType": "success",
                                                               "textResultForLlm": "def checkout"}}, "copilot")
    assert res["_recorded"] == 1 and out == {}
    with Store.open(root) as st:
        row = st.db.execute("SELECT tool_name, tool_input, tool_response FROM pending_messages").fetchone()
    assert row["tool_name"] == "view" and "shop/api.py" in row["tool_input"] and "def checkout" in row["tool_response"]


def test_hooks_are_quiet_and_fast_outside_a_cairn_repository(tmp_path):
    for platform, event in (("gemini", "context"), ("codex", "context"), ("cursor", "context"),
                            ("opencode", "context"), ("copilot", "observation")):
        t = time.time()
        out = subprocess.run([sys.executable, "-m", "cairn.capture", "--platform", platform, event],
                             input=json.dumps({"session_id": "x", "sessionId": "x", "conversation_id": "x",
                                               "cwd": str(tmp_path), "workspace_roots": [str(tmp_path)],
                                               "prompt": "hello", "toolName": "view"}),
                             cwd=tmp_path, capture_output=True, text=True, check=True, encoding="utf-8", errors="replace")
        assert time.time() - t < 3, platform
        printed = json.loads(out.stdout or "{}")  # nothing, or an empty answer in the agent's own shape
        assert not printed.get("hookSpecificOutput", {}).get("additionalContext"), platform
        assert not printed.get("additional_context") and not printed.get("systemMessage"), platform
        assert not (tmp_path / ".cairn").exists()

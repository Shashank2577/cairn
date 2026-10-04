"""Recall agent integrations: hook installers, MCP writers, the OpenCode plugin, bundled skills and scripts."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairn import shellcmd
from cairn.engines.recall import integrations as ig

PY = "/opt/py env/bin/python3"  # a path with a space: quoting must survive it


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Path.home() on Windows
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "cairn-home"))
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CAIRN_RECALL_MODES_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


@pytest.fixture(autouse=True)
def _posix_shell_shapes(monkeypatch):
    """These tests pin the POSIX spelling of hook commands (`"<python>" -m ...`); the Windows spellings
    (PowerShell call operator, forward slashes, 8.3 names) are pinned in tests/test_windows.py."""
    monkeypatch.setattr(shellcmd, "is_windows", lambda: False)


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    return r


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _commands(obj) -> list[str]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "command" and isinstance(v, str):
                out.append(v)
            else:
                out += _commands(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _commands(v)
    return out


def _seed_store(root: Path, title: str = "Added JWT authentication") -> str:
    from cairn.engines.recall.projects import project_context
    from cairn.engines.recall.store import Store
    (root / ".cairn").mkdir(exist_ok=True)
    project = project_context(str(root)).primary
    with Store.open(root) as s:
        sid = s.create_sdk_session("sess-1", project, "add auth", platform_source="claude-code", cwd=str(root))
        mid = s.ensure_memory_session_id(sid)
        s.store_observation(mid, project, {"title": title, "narrative": "JWT tokens now guard the API.",
                                           "type": "feature", "facts": ["tokens expire after 1h"]})
    return project


# ---- commands and generic merging --------------------------------------------------------------------------
def test_hook_command_shapes():
    cmd = ig.hook_command("claude-code", "context", python=PY)
    assert cmd == f'"{PY}" -m cairn.capture --platform claude-code context'
    bare = ig.hook_command("antigravity", "summarize", python="C:\\py\\python.exe", style="bare")
    assert bare == "C:/py/python.exe -m cairn.capture --platform antigravity summarize" and '"' not in bare
    assert ig.hook_command("windsurf", "observation", python="/p").startswith('"/p"')
    with pytest.raises(ValueError):
        ig.hook_command("claude-code", "nope")
    assert ig.hook_command("codex", "file-edit").startswith(shellcmd.posix_quote(sys.executable))


def test_claude_code_hooks_exact_entries():
    hooks = ig.claude_code_hooks(PY)
    assert list(hooks) == ["SessionStart", "UserPromptSubmit", "PostToolUse", "PreToolUse", "Stop", "SessionEnd"]
    ss = hooks["SessionStart"][0]
    assert ss["matcher"] == "startup|resume|clear|compact"
    assert ss["hooks"][0] == {"type": "command", "command": f'"{PY}" -m cairn.capture --platform claude-code context',
                              "timeout": 60}
    assert "matcher" not in hooks["UserPromptSubmit"][0]
    assert hooks["UserPromptSubmit"][0]["hooks"][0]["command"].endswith("--platform claude-code session-init")
    assert hooks["PostToolUse"][0]["matcher"] == "*"
    assert hooks["PostToolUse"][0]["hooks"][0]["timeout"] == 120 and hooks["PostToolUse"][0]["hooks"][0]["async"]
    assert hooks["PreToolUse"][0]["matcher"] == "Read"
    assert hooks["PreToolUse"][0]["hooks"][0]["command"].endswith("file-context")
    assert hooks["Stop"][0]["hooks"][0]["command"].endswith("summarize")
    assert hooks["Stop"][0]["hooks"][0]["timeout"] == 120
    end = hooks["SessionEnd"][0]["hooks"][0]
    assert end["command"].endswith("session-end") and "timeout" not in end
    for c in _commands(hooks):
        assert "-m cairn.capture --platform claude-code" in c


def test_merge_and_remove_hook_groups_preserve_user_entries():
    user = {"hooks": [{"type": "command", "command": "echo mine"}]}
    existing = {"Stop": [user], "Notification": [{"hooks": [{"type": "command", "command": "notify"}]}]}
    merged = ig.merge_hook_groups(existing, ig.claude_code_hooks(PY))
    assert ig.merge_hook_groups(merged, ig.claude_code_hooks(PY)) == merged  # idempotent
    assert merged["Stop"][0] == user and len(merged["Stop"]) == 2
    assert merged["Notification"] == existing["Notification"]
    # a newer python replaces Cairn's old entries instead of adding a second set
    again = ig.merge_hook_groups(merged, ig.claude_code_hooks("/other/python"))
    assert sum("cairn.capture" in c for c in _commands(again["Stop"])) == 1
    cleaned, removed = ig.remove_hook_groups(merged)
    assert removed == 6
    assert cleaned == existing


# ---- markdown context --------------------------------------------------------------------------------------
def test_bmp_safe_and_context_blocks(tmp_path):
    assert ig.to_bmp_safe("\U0001F534 fix \U0001F600 x\ud800y") == "\u25CF fix \u2022 xy"
    assert ig.to_bmp_safe("") == ""
    f = tmp_path / "AGENTS.md"
    f.write_text("# Mine\n\nkeep this\n", encoding="utf-8")
    ig.inject_context_into_markdown_file(f, "one \U0001F7E3")
    ig.inject_context_into_markdown_file(f, "two")
    text = f.read_text(encoding="utf-8")
    assert text.count("<cairn-context>") == 1 and "two" in text and "keep this" in text
    assert ig.remove_context_block(f)
    assert f.read_text(encoding="utf-8") == "# Mine\n\nkeep this\n"
    new = tmp_path / "sub" / "NEW.md"
    ig.inject_context_into_markdown_file(new, "ctx", "# Header")
    assert new.read_text(encoding="utf-8").startswith("# Header\n\n<cairn-context>\nctx\n</cairn-context>")
    assert ig.remove_context_block(new, header_line="# Header") and not new.exists()


# ---- Cursor ------------------------------------------------------------------------------------------------
def test_cursor_project_round_trip(repo, home):
    cfg = repo / ".cursor" / "hooks.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "./mine.sh"}]}}), encoding="utf-8")
    res = ig.install_cursor(repo, python=PY)
    assert res["ok"], res
    data = _load(cfg)
    assert data["version"] == 1
    assert data["hooks"]["stop"][0] == {"command": "./mine.sh"}
    assert [h["command"] for h in data["hooks"]["beforeSubmitPrompt"]] == [
        f'"{PY}" -m cairn.capture --platform cursor session-init']
    assert data["hooks"]["sessionStart"][0]["command"].endswith("--platform cursor context")  # memory at start
    assert data["hooks"]["postToolUse"][0]["command"].endswith("--platform cursor observation")
    assert data["hooks"]["stop"][1]["command"].endswith("--platform cursor summarize")
    assert data["hooks"]["sessionEnd"][0]["command"].endswith("--platform cursor session-end")
    assert not (repo / ".cursor" / "rules").exists() and repo.name not in ig.read_cursor_registry()
    first = cfg.read_text(encoding="utf-8")
    assert ig.install_cursor(repo, python=PY)["ok"] and cfg.read_text(encoding="utf-8") == first  # idempotent
    status = ig.cursor_status(repo)
    proj = next(r for r in status["locations"] if r["name"] == "project")
    assert proj["installed"] and "beforeSubmitPrompt" in proj["events"]

    res = ig.uninstall_cursor(repo)
    assert res["ok"]
    assert _load(cfg) == {"version": 1, "hooks": {"stop": [{"command": "./mine.sh"}]}}
    assert not ig.cursor_status(repo)["installed"]


def test_cursor_user_target_and_cleanup(home):
    assert ig.install_cursor(target="user", python=PY)["ok"]
    cfg = home / ".cursor" / "hooks.json"
    assert cfg.exists() and not (home / ".cursor" / "rules").exists()
    assert ig.uninstall_cursor(target="user")["ok"]
    assert not cfg.exists()  # only Cairn's entries were there
    assert not ig.install_cursor(target="bogus")["ok"]


def test_cursor_refuses_corrupt_hooks(repo):
    cfg = repo / ".cursor" / "hooks.json"
    cfg.parent.mkdir()
    cfg.write_text("{not json", encoding="utf-8")
    res = ig.install_cursor(repo, python=PY)
    assert not res["ok"] and "refusing" in res["error"]
    assert cfg.read_text(encoding="utf-8") == "{not json"
    assert ig.uninstall_cursor(repo)["ok"] and cfg.read_text(encoding="utf-8") == "{not json"


def test_cursor_install_retires_the_generated_context_rule(repo):
    rules = repo / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "cairn-context.mdc").write_text(ig.CURSOR_PLACEHOLDER, encoding="utf-8")  # written by an earlier version
    res = ig.install_cursor(repo, python=PY)
    assert str(rules / "cairn-context.mdc") in res["removed"] and not (rules / "cairn-context.mdc").exists()
    (rules / "cairn-context.mdc").write_text("---\nalwaysApply: true\n---\nmy own rule\n", encoding="utf-8")  # same name, the user's
    ig.install_cursor(repo, python=PY)
    ig.uninstall_cursor(repo)
    assert (rules / "cairn-context.mdc").read_text(encoding="utf-8").endswith("my own rule\n")


def test_cursor_mcp_config(repo):
    mcp = repo / ".cursor" / "mcp.json"
    mcp.parent.mkdir()
    mcp.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    assert ig.configure_cursor_mcp(repo, command="cairn", args=["mcp"])["ok"]
    assert _load(mcp)["mcpServers"] == {"other": {"command": "x"}, "cairn": {"command": "cairn", "args": ["mcp"]}}
    assert ig.configure_cursor_mcp(repo)["written"] == []  # idempotent
    assert ig.remove_cursor_mcp(repo)["ok"]
    assert _load(mcp) == {"mcpServers": {"other": {"command": "x"}}}
    mcp.write_text("oops", encoding="utf-8")
    assert not ig.configure_cursor_mcp(repo)["ok"] and mcp.read_text(encoding="utf-8") == "oops"


# ---- Windsurf ----------------------------------------------------------------------------------------------
def test_windsurf_round_trip(repo, home):
    path = home / ".codeium" / "windsurf" / "hooks.json"
    path.parent.mkdir(parents=True)
    path.write_text("\ufeff" + json.dumps({"hooks": {"post_run_command": [{"command": "mine", "show_output": True}]}}), encoding="utf-8")
    assert ig.install_windsurf(repo, python=PY)["ok"]
    hooks = _load(path)["hooks"] if not path.read_text(encoding="utf-8").startswith("\ufeff") else None
    assert hooks is not None
    assert set(hooks) == set(ig.WINDSURF_EVENTS)
    assert hooks["post_run_command"][0] == {"command": "mine", "show_output": True}
    entry = hooks["pre_user_prompt"][0]
    assert entry == {"command": f'"{PY}" -m cairn.capture --platform windsurf session-init', "show_output": False}
    assert hooks["post_write_code"][0]["command"].endswith("file-edit")
    assert all(h["command"].endswith("observation") for ev in ("post_mcp_tool_use", "post_cascade_response")
               for h in hooks[ev])
    first = path.read_text(encoding="utf-8")
    ig.install_windsurf(repo, python=PY)
    assert path.read_text(encoding="utf-8") == first
    assert (repo / ".windsurf" / "rules" / "cairn-context.md").exists()
    st = ig.windsurf_status(repo)
    assert st["installed"] and len(st["events"]) == 5 and st["context"]
    assert str(repo) in ig.read_windsurf_registry()

    assert ig.uninstall_windsurf(repo)["ok"]
    assert _load(path) == {"hooks": {"post_run_command": [{"command": "mine", "show_output": True}]}}
    assert not (repo / ".windsurf" / "rules" / "cairn-context.md").exists()
    assert str(repo) not in ig.read_windsurf_registry()


def test_windsurf_corrupt_and_truncation(repo, home):
    path = ig.windsurf_hooks_path()
    path.parent.mkdir(parents=True)
    path.write_text("[broken", encoding="utf-8")
    assert not ig.install_windsurf(repo)["ok"] and path.read_text(encoding="utf-8") == "[broken"
    res = ig.uninstall_windsurf(repo)
    assert res["ok"] and path.read_text(encoding="utf-8") == "[broken" and res["notes"]
    out = ig.write_windsurf_context_file(repo, "x" * 10_000)
    text = out.read_text(encoding="utf-8")
    assert len(text) <= ig.WINDSURF_CONTEXT_CHAR_LIMIT and "Truncated" in text


def test_windsurf_only_ours_deletes_file(repo, home):
    ig.install_windsurf(repo, python=PY, working_directory="/w")
    path = ig.windsurf_hooks_path()
    assert _load(path)["hooks"]["pre_user_prompt"][0]["working_directory"] == "/w"
    ig.uninstall_windsurf(repo)
    assert not path.exists()


# ---- Codex -------------------------------------------------------------------------------------------------
class FakeCodex:
    def __init__(self, version="codex-cli 0.130.0", fail_add_once=False):
        self.calls: list[list[str]] = []
        self.version = version
        self.fail_add_once = fail_add_once

    def __call__(self, args):
        self.calls.append(list(args))
        if args == ["--version"]:
            return SimpleNamespace(returncode=0, stdout=self.version, stderr="")
        if args[:3] == ["plugin", "marketplace", "add"] and self.fail_add_once:
            self.fail_add_once = False
            return SimpleNamespace(returncode=1, stdout="",
                                   stderr="marketplace 'cairn-local' is already added from a different source")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")


def test_codex_install_uninstall(home, tmp_path):
    codex = home / ".codex"
    codex.mkdir()
    (codex / "config.toml").write_text('model = "o4"\n\n[mcp_servers.cairn]\ncommand = "cairn"\nargs = ["mcp"]\n\n'
                                       '[mcp_servers.other]\ncommand = "x"\n', encoding="utf-8")
    (codex / "AGENTS.md").write_text("# Mine\n\n<cairn-context>\nold\n</cairn-context>\n", encoding="utf-8")
    watch = tmp_path / "cairn-home" / "transcript-watch.json"
    watch.parent.mkdir(parents=True)
    legacy = {"mode": "agents", "updateOn": ["session_start", "session_end"]}
    watch.write_text(json.dumps({"watches": [{"name": "codex", "context": legacy},
                                             {"name": "other", "context": {"mode": "agents"}}]}), encoding="utf-8")
    fake = FakeCodex()
    res = ig.install_codex(python=PY, runner=fake, mcp_command="cairn", mcp_args=["mcp"])
    assert res["ok"], res
    root = ig.codex_marketplace_root()
    assert ig.missing_marketplace_files(root) == []
    assert _load(root / ".agents" / "plugins" / "marketplace.json")["name"] == "cairn-local"
    manifest = _load(root / "plugin" / ".codex-plugin" / "plugin.json")
    assert manifest["hooks"] == "./hooks/codex-hooks.json" and manifest["name"] == "cairn"
    assert _load(root / "plugin" / ".mcp.json")["mcpServers"]["cairn"] == {"type": "stdio", "command": "cairn",
                                                                          "args": ["mcp"]}
    hooks = _load(root / "plugin" / "hooks" / "codex-hooks.json")["hooks"]
    assert list(hooks) == ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]
    assert hooks["PreToolUse"][0]["matcher"] == r"^Bash$|^mcp__.+__(read|view|cat)(_file|_files)?$"
    assert hooks["PreToolUse"][0]["hooks"][0]["timeout"] == 30
    assert all("-m cairn.capture --platform codex" in c for c in _commands(hooks))
    assert (root / "plugin" / "skills" / "cairn-recall-search" / "SKILL.md").exists()
    assert ["plugin", "marketplace", "add", str(root)] in fake.calls
    assert fake.calls[-1] == ["plugin", "add", "cairn@cairn-local"]
    cfg = (codex / "config.toml").read_text(encoding="utf-8")
    assert "[features]\nhooks = true" in cfg and '[plugins."cairn@cairn-local"]\nenabled = true' in cfg
    assert "[mcp_servers.cairn]" not in cfg and "[mcp_servers.other]" in cfg and 'model = "o4"' in cfg
    assert (codex / "AGENTS.md").read_text(encoding="utf-8") == "# Mine\n"
    watches = _load(watch)["watches"]
    assert "context" not in watches[0] and "context" in watches[1]
    st = ig.codex_status()
    assert st["installed"] and st["plugin_enabled"] and st["hooks_feature"]
    # idempotent
    assert ig.install_codex(python=PY, runner=FakeCodex())["ok"]
    assert (codex / "config.toml").read_text(encoding="utf-8") == cfg

    fake2 = FakeCodex()
    res = ig.uninstall_codex(runner=fake2)
    assert res["ok"], res
    assert ["plugin", "marketplace", "remove", "cairn-local"] in fake2.calls
    assert '[plugins."cairn@cairn-local"]\nenabled = false' in (codex / "config.toml").read_text(encoding="utf-8")
    assert not root.exists() and not ig.codex_status()["installed"]


def test_codex_errors_and_marketplace_replacement(home, monkeypatch):
    assert not ig.install_codex(runner=FakeCodex(version="codex-cli 0.100.0"))["ok"]
    monkeypatch.setattr(ig, "default_codex_runner", lambda: None)
    res = ig.install_codex()
    assert not res["ok"] and "not found" in res["error"]
    fake = FakeCodex(fail_add_once=True)
    assert ig.install_codex(runner=fake)["ok"]
    assert ["plugin", "marketplace", "remove", "cairn-local"] in fake.calls
    assert ig.uninstall_codex()["ok"]  # no codex: marketplace removal skipped, config still disabled


def test_toml_helpers():
    assert ig.set_toml_feature_enabled("", "hooks", True) == "[features]\nhooks = true\n"
    t = "[features]\nother = 1\nhooks = false\n[x]\ny = 2"
    assert ig.set_toml_feature_enabled(t, "hooks", True) == "[features]\nother = 1\nhooks = true\n[x]\ny = 2"
    t2 = '[mcp_servers.cairn]\ncommand = "/x/cairn"\n[mcp_servers.cairn.tools.a]\nk = 1\n[keep]\nv = 1\n'
    assert ig.remove_codex_mcp_server_block(t2) == "[keep]\nv = 1\n"
    foreign = '[mcp_servers.cairn]\ncommand = "someone-else"\n'
    assert ig.remove_codex_mcp_server_block(foreign, owner_marker="cairn-owned") == foreign
    assert ig.resolve_codex_command("linux", which=lambda _c: None) is None
    assert ig.resolve_codex_command("darwin", which=lambda _c: None, bundle_ok=lambda c: "Codex.app" in c) \
        == "/Applications/Codex.app/Contents/Resources/codex"


# ---- OpenCode ----------------------------------------------------------------------------------------------
def test_opencode_round_trip(home, repo):
    cfg_dir = home / ".config" / "opencode"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "opencode.json").write_text(json.dumps({"$schema": "x", "plugin": "other-plugin", "theme": "dark"}), encoding="utf-8")
    (cfg_dir / "AGENTS.md").write_text("# My rules\n", encoding="utf-8")
    res = ig.install_opencode(python=PY)
    assert res["ok"], res
    plugin = cfg_dir / "plugins" / "cairn.js"
    js = plugin.read_text(encoding="utf-8")
    assert f"const PYTHON = {json.dumps(PY)};" in js and 'const PLATFORM = "opencode";' in js
    assert '["-m", "cairn.capture", "--platform", PLATFORM, event]' in js
    for hook in ("tool.execute.after", "chat.message", "experimental.session.compacting", "session.idle",
                 "session.deleted", "cairn_search"):
        assert hook in js
    config = _load(cfg_dir / "opencode.json")
    assert config["plugin"] == ["other-plugin", "./plugins/cairn.js"] and config["theme"] == "dark"
    agents = (cfg_dir / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith("# My rules") and "<cairn-context>" in agents
    ig.install_opencode(python=PY)
    assert _load(cfg_dir / "opencode.json")["plugin"].count("./plugins/cairn.js") == 1
    assert (cfg_dir / "AGENTS.md").read_text(encoding="utf-8").count("<cairn-context>") == 1
    assert ig.opencode_status()["installed"]

    assert ig.uninstall_opencode()["ok"]
    assert not plugin.exists()
    assert _load(cfg_dir / "opencode.json") == {"$schema": "x", "plugin": ["other-plugin"], "theme": "dark"}
    assert (cfg_dir / "AGENTS.md").read_text(encoding="utf-8") == "# My rules\n"
    assert not ig.opencode_status()["installed"]


def test_opencode_fresh_and_corrupt(home, repo, monkeypatch):
    custom = home / "oc"
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(custom))
    project = _seed_store(repo)
    assert ig.install_opencode(python=PY, root=repo)["ok"]
    assert _load(custom / "opencode.json") == {"$schema": "https://opencode.ai/config.json",
                                               "plugin": ["./plugins/cairn.js"]}
    assert project in (custom / "AGENTS.md").read_text(encoding="utf-8")
    assert ig.uninstall_opencode()["ok"]
    assert not (custom / "AGENTS.md").exists()  # only the header + block were ours
    assert _load(custom / "opencode.json") == {"$schema": "https://opencode.ai/config.json"}
    (custom / "opencode.json").write_text("{bad", encoding="utf-8")
    assert not ig.install_opencode()["ok"] and (custom / "opencode.json").read_text(encoding="utf-8") == "{bad"
    foreign = custom / "plugins" / "cairn.js"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("// someone else's plugin\n", encoding="utf-8")
    (custom / "opencode.json").write_text("{}", encoding="utf-8")
    assert not ig.install_opencode()["ok"]
    ig.uninstall_opencode()
    assert foreign.exists()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_opencode_plugin_is_valid_javascript(tmp_path):
    f = tmp_path / "cairn.mjs"
    f.write_text(ig.opencode_plugin_source(PY), encoding="utf-8")
    res = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, check=False, encoding="utf-8", errors="replace")
    assert res.returncode == 0, res.stderr


# ---- Antigravity and Gemini CLI ----------------------------------------------------------------------------
def test_antigravity_round_trip(home):
    gem = home / ".gemini"
    (gem / "config").mkdir(parents=True)
    (gem / "config" / "mcp_config.json").write_text("", encoding="utf-8")  # agy's empty placeholder
    user_group = {"matcher": "*", "hooks": [{"name": "mine", "type": "command", "command": "echo", "timeout": 5}]}
    (gem / "config" / "hooks.json").write_text(json.dumps({"Stop": [user_group]}), encoding="utf-8")
    (gem / "GEMINI.md").write_text("Gemini Added Memories\n- likes tea", encoding="utf-8")
    res = ig.install_antigravity(python="/opt/py/bin/python", mcp_command="cairn", mcp_args=["mcp"])
    assert res["ok"], res
    hooks = _load(gem / "config" / "hooks.json")
    assert set(hooks) == set(ig.ANTIGRAVITY_EVENTS)
    assert hooks["Stop"][0] == user_group
    ours = hooks["PreInvocation"][0]["hooks"][0]
    assert ours == {"name": "cairn", "type": "command", "timeout": 10000,
                    "command": "/opt/py/bin/python -m cairn.capture --platform antigravity context"}
    assert hooks["Stop"][1]["hooks"][0]["command"].endswith("summarize")
    for p in ig.antigravity_mcp_paths():
        assert _load(p)["mcpServers"]["cairn"] == {"command": "cairn", "args": ["mcp"]}
    assert (gem / "GEMINI.md").read_text(encoding="utf-8").startswith("Gemini Added Memories\n- likes tea\n\n<cairn-context>")
    assert ig.has_context_block(ig.antigravity_rules_path())
    first = (gem / "config" / "hooks.json").read_text(encoding="utf-8")
    ig.install_antigravity(python="/opt/py/bin/python")
    assert (gem / "config" / "hooks.json").read_text(encoding="utf-8") == first
    st = ig.antigravity_status()
    assert st["installed"] and len(st["events"]) == 5 and all(st["mcp"].values())

    assert ig.uninstall_antigravity()["ok"]
    assert _load(gem / "config" / "hooks.json") == {"Stop": [user_group]}
    assert (gem / "GEMINI.md").read_text(encoding="utf-8") == "Gemini Added Memories\n- likes tea\n"
    assert not ig.antigravity_rules_path().exists()
    for p in ig.antigravity_mcp_paths():
        assert "cairn" not in json.dumps(_load(p))


def test_antigravity_spaces_warning_and_corrupt(home):
    res = ig.install_antigravity(python=PY)
    assert res["ok"] and any("spaces" in n for n in res["notes"])
    path = ig.antigravity_hooks_path()
    path.write_text("{corrupt", encoding="utf-8")
    assert not ig.install_antigravity(python=PY)["ok"] and path.read_text(encoding="utf-8") == "{corrupt"
    res = ig.uninstall_antigravity()
    assert res["ok"] and path.read_text(encoding="utf-8") == "{corrupt"


def test_gemini_cli_round_trip_and_shared_context(home):
    settings = home / ".gemini" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"theme": "Dracula", "mcpServers": {"x": {"command": "y"}},
                                    "hooks": {"AfterTool": [{"matcher": "write_file",
                                                             "hooks": [{"name": "fmt", "type": "command",
                                                                        "command": "prettier"}]}]}}), encoding="utf-8")
    assert ig.install_gemini_cli(python=PY)["ok"]
    data = _load(settings)
    assert data["theme"] == "Dracula" and data["mcpServers"] == {"x": {"command": "y"}}
    assert set(data["hooks"]) == set(ig.GEMINI_CLI_EVENTS)
    assert data["hooks"]["AfterTool"][0]["hooks"][0]["name"] == "fmt"
    ours = data["hooks"]["BeforeAgent"][0]["hooks"][0]
    assert ours["name"] == "cairn" and ours["command"] == f'"{PY}" -m cairn.capture --platform gemini session-init'
    assert data["hooks"]["AfterAgent"][0]["hooks"][0]["command"].endswith("summarize")
    assert data["hooks"]["SessionEnd"][0]["hooks"][0]["command"].endswith("session-end")
    first = settings.read_text(encoding="utf-8")
    ig.install_gemini_cli(python=PY)
    assert settings.read_text(encoding="utf-8") == first
    assert ig.gemini_cli_status()["installed"]

    ig.install_antigravity(python="/p/python")
    assert ig.uninstall_gemini_cli()["ok"]
    assert ig.has_context_block(home / ".gemini" / "GEMINI.md")  # antigravity still uses it
    data = _load(settings)
    assert set(data["hooks"]) == {"AfterTool"} and data["theme"] == "Dracula"
    ig.uninstall_antigravity()
    assert not (home / ".gemini" / "GEMINI.md").exists()


def test_gemini_cli_corrupt_settings(home):
    settings = ig.gemini_settings_path()
    settings.parent.mkdir(parents=True)
    settings.write_text("{nope", encoding="utf-8")
    assert not ig.install_gemini_cli()["ok"] and settings.read_text(encoding="utf-8") == "{nope"
    assert not ig.uninstall_gemini_cli()["ok"] and settings.read_text(encoding="utf-8") == "{nope"


# ---- MCP-only IDEs -----------------------------------------------------------------------------------------
def test_mcp_ides_json_round_trips(home, repo):
    for ide, path, key in (("copilot-cli", home / ".github/copilot/mcp.json", "servers"),
                           ("roo-code", repo / ".roo/mcp.json", "mcpServers")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({key: {"keep": {"command": "k"}}, "other": 1}), encoding="utf-8")
        res = ig.install_mcp_ide(ide, root=repo, command="cairn", args=["mcp"])
        assert res["ok"], res
        assert _load(path)[key]["cairn"] == {"command": "cairn", "args": ["mcp"]}
        assert ig.mcp_ide_status(ide, root=repo)["installed"]
        assert ig.uninstall_mcp_ide(ide, root=repo)["ok"]
        assert _load(path) == {key: {"keep": {"command": "k"}}, "other": 1}
    assert not (repo / ".github" / "copilot-instructions.md").exists()
    assert not (repo / ".roo" / "rules" / "cairn-context.md").exists()


def test_mcp_ide_warp_and_context(home, repo):
    (repo / "WARP.md").write_text("# Warp rules\n", encoding="utf-8")
    res = ig.install_mcp_ide("warp", root=repo)
    assert res["ok"] and not (home / ".warp" / "mcp.json").exists() and "Warp Drive" in res["notes"][0]
    assert "<cairn-context>" in (repo / "WARP.md").read_text(encoding="utf-8")
    (home / ".warp").mkdir()
    ig.install_mcp_ide("warp", root=repo)
    assert ig.mcp_ide_status("warp", root=repo)["installed"]
    ig.uninstall_mcp_ide("warp", root=repo)
    assert (repo / "WARP.md").read_text(encoding="utf-8") == "# Warp rules\n"
    assert not ig.install_mcp_ide("nope")["ok"]


def test_goose_yaml(home):
    cfg = home / ".config" / "goose" / "config.yaml"
    assert ig.install_mcp_ide("goose", command="/usr/bin/cairn", args=["mcp"])["ok"]
    assert cfg.read_text(encoding="utf-8") == "mcpServers:\n  cairn:\n    command: /usr/bin/cairn\n    args:\n      - mcp\n"
    assert ig.uninstall_mcp_ide("goose")["ok"] and not cfg.exists()
    original = "GOOSE_MODEL: x\nmcpServers:\n  other:\n    command: o\n\nextensions: {}\n"
    cfg.write_text(original, encoding="utf-8")
    ig.install_mcp_ide("goose", command="cairn", args=["mcp"])
    text = cfg.read_text(encoding="utf-8")
    assert "  cairn:\n    command: cairn\n    args:\n      - mcp\n" in text and "  other:\n    command: o" in text
    ig.install_mcp_ide("goose", command="cairn2", args=[])
    text = cfg.read_text(encoding="utf-8")
    assert text.count("  cairn:") == 1 and "command: cairn2\n    args: []" in text
    assert ig.mcp_ide_status("goose")["installed"]
    ig.uninstall_mcp_ide("goose")
    assert cfg.read_text(encoding="utf-8") == original
    cfg.write_text("GOOSE_MODEL: x\n", encoding="utf-8")
    ig.install_mcp_ide("goose")
    assert cfg.read_text(encoding="utf-8").startswith("GOOSE_MODEL: x\n\nmcpServers:\n  cairn:")
    ig.uninstall_mcp_ide("goose")
    assert cfg.read_text(encoding="utf-8") == "GOOSE_MODEL: x\n"


# ---- metadata, dispatch, search ----------------------------------------------------------------------------
def test_hook_events_metadata():
    assert set(ig.HOOK_EVENTS) == {"claude-code", "codex", "cursor", "windsurf", "antigravity", "gemini-cli",
                                   "opencode", "copilot"}
    for agent, mapping in ig.HOOK_EVENTS.items():
        assert mapping, agent
        for ours in mapping.values():
            assert all(e in ig.CAPTURE_EVENTS for e in ours)
    assert ig.HOOK_EVENTS["cursor"]["beforeSubmitPrompt"] == ["session-init"]
    assert ig.HOOK_EVENTS["copilot"]["agentStop"] == ["summarize"]
    assert ig.HOOK_EVENTS["claude-code"]["PreToolUse(Read)"] == ["file-context"]
    assert ig.HOOK_EVENTS["antigravity"]["PreInvocation"] == ["context"]


def test_dispatch_install_status_uninstall(home, repo):
    for agent in ("cursor", "windsurf", "opencode", "antigravity", "gemini-cli", "copilot-cli", "roo-code", "goose"):
        assert ig.install(agent, root=repo, python="/p/python")["ok"], agent
        assert ig.status(agent, root=repo)["installed"], agent
        assert ig.uninstall(agent, root=repo)["ok"], agent
        assert not ig.status(agent, root=repo)["installed"], agent
    assert not ig.install("nope")["ok"]
    assert ig.install("codex", runner=FakeCodex())["ok"]
    assert ig.status("codex")["installed"]


def test_search_text_and_cli(repo, capsys):
    assert ig.search_text(repo, "jwt") == 'No results found for "jwt".'
    _seed_store(repo)
    out = ig.search_text(repo, "JWT")
    assert "Added JWT authentication" in out and "| ID |" in out
    assert ig.search_text(repo, "zzzqqq").startswith("No results")
    assert ig.main(["search", "--cwd", str(repo), "--", "JWT"]) == 0
    assert "Added JWT authentication" in capsys.readouterr().out
    assert ig.main(["status", "gemini-cli"]) == 0


# ---- skills ------------------------------------------------------------------------------------------------
EXPECTED_SKILLS = {"cairn-recall-search", "cairn-smart-explore", "cairn-knowledge-agent", "cairn-learn-codebase", "cairn-how-it-works",
                   "cairn-timeline-report", "cairn-standup", "cairn-weekly-digests", "cairn-make-plan", "cairn-do", "cairn-mode-creator", "cairn-pathfinder",
                   "cairn-what-the"}
# assembled at runtime so this file stays clean under the same check; the last four are the upstream's
# notifier, worker port range, worker binary and settings prefix, none of which exist here
_WORDS = ["claude" + "-mem", "claude" + "_mem", r"\bc" + r"mem\b", "thedot" + "mack", "graph" + "ify", "graph" + "iti",
          r"\bz" + r"ep\b", "mem" + "0", "spec" + "-kit", "spec" + "kit", "tele" + "gram", "377" + "00",
          "worker" + "-service", "CLAUDE" + "_MEM"]
FORBIDDEN = re.compile("|".join(_WORDS), re.IGNORECASE)


def test_skills_listing_and_content():
    skills = {s["name"]: s for s in ig.list_skills()}
    assert set(skills) == EXPECTED_SKILLS
    for name, s in skills.items():
        assert s["description"], name
        assert Path(s["path"]).name == name
    assert "onboarding-explainer.md" in skills["cairn-how-it-works"]["files"]
    assert {"SKILL.md", "agent-brief.md", "standup.py"} <= set(skills["cairn-standup"]["files"])
    assert {"references/mode-authoring.md", "scripts/install_mode.py"} <= set(skills["cairn-mode-creator"]["files"])
    for f in ig.skills_dir().rglob("*"):
        if f.is_file() and f.suffix in (".md", ".py", ".json"):
            hits = FORBIDDEN.findall(f.read_text(encoding="utf-8"))
            assert not hits, (f, hits)
    search = (ig.skills_dir() / "cairn-recall-search" / "SKILL.md").read_text(encoding="utf-8")
    assert "recall_search(" in search and "recall_timeline(" in search and "get_observations(" in search
    assert not re.search(r"(?<![_\w])search\(query", search) and not re.search(r"(?<![_\w])timeline\(anchor", search)


def test_install_skills_round_trip(repo):
    dest = repo / ".claude" / "skills"
    mine = dest / "cairn-do"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: do\n---\nmine\n", encoding="utf-8")
    installed = ig.install_skills(dest)
    assert set(installed) == EXPECTED_SKILLS - {"cairn-do"}
    assert (mine / "SKILL.md").read_text(encoding="utf-8").endswith("mine\n")
    assert (dest / "cairn-recall-search" / "SKILL.md").exists()
    assert set(ig.install_skills(dest, ["cairn-standup"])) == {"cairn-standup"}
    removed = ig.uninstall_skills(dest)
    assert set(removed) == EXPECTED_SKILLS - {"cairn-do"}
    assert [p.name for p in dest.iterdir()] == ["cairn-do"]


# ---- skill scripts -----------------------------------------------------------------------------------------
def _standup(tmp_path, *args, agent=None, cwd=None):
    env = {**os.environ, "CAIRN_STANDUP_FILE": str(tmp_path / "room" / "STANDUP.md")}
    if agent:
        env["CAIRN_STANDUP_AGENT"] = agent
    return subprocess.run([sys.executable, str(ig.skills_dir() / "cairn-standup" / "standup.py"), *args], env=env,
                          capture_output=True, text=True, cwd=cwd or tmp_path, check=False, encoding="utf-8", errors="replace")


def test_standup_chat_flow(tmp_path):
    goal = "Collapse everything into one worktree"
    r = _standup(tmp_path, "open", "--goal", goal, "--prompt", "Talk it out", agent="facilitator")
    assert r.returncode == 0, r.stderr
    assert _standup(tmp_path, "open", "--goal", "g", "--prompt", "p").returncode == 1
    assert _standup(tmp_path, "join", agent="feat-a").returncode == 0
    assert _standup(tmp_path, "post", "--message", "I have the code", "--agree", "Build on feat-a",
                    agent="feat-a").returncode == 0
    assert _standup(tmp_path, "agree", "--deliverable", "build on  FEAT-A", agent="facilitator").returncode == 0
    status = _standup(tmp_path, "status").stdout
    assert f"goal    : {goal}" in status and "participants (2):" in status
    assert "consensus: REACHED" in status
    read = _standup(tmp_path, "read", "--since", "feat-a").stdout
    assert read.startswith("### facilitator") and "AGREE: build on  FEAT-A" in read
    assert _standup(tmp_path, "read", "--tail", "1").stdout.count("### ") == 1
    r = _standup(tmp_path, "summation", "--text", "Build on feat-a.", agent="facilitator")
    assert r.returncode == 0
    text = (tmp_path / "room" / "STANDUP.md").read_text(encoding="utf-8")
    assert "status: agreed" in text and text.rstrip().endswith("Build on feat-a.") and "## SUMMATION" in text
    w = _standup(tmp_path, "watch", "--timeout", "0.2", "--interval", "0.1", agent="feat-a")
    assert w.returncode == 2 and "TIMEOUT" in w.stdout
    assert _standup(tmp_path, "open", "--force", "--goal", "g2", "--prompt", "p2").returncode == 0
    assert len(list((tmp_path / "room").glob("STANDUP-*.md"))) == 1
    assert _standup(tmp_path).returncode == 1
    assert _standup(tmp_path, "bogus").returncode == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_standup_worktrees(tmp_path):
    repo = tmp_path / "wt"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["-c", "user.email=a@b", "-c", "user.name=n", "commit", "-q",
                                               "--allow-empty", "-m", "init"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    (repo / "new.txt").write_text("x", encoding="utf-8")
    rows = json.loads(_standup(tmp_path, "worktrees", "--since", "1h", "--json", cwd=repo).stdout)
    assert rows[0]["branch"] == "main" and rows[0]["current"] and rows[0]["lastActivityMs"] > 0
    out = _standup(tmp_path, "worktrees", "--since", "bogus", cwd=repo)
    assert "unrecognized window" in out.stderr and "main" in out.stdout


def _mode_draft() -> dict:
    ref = (ig.skills_dir() / "cairn-mode-creator" / "references" / "mode-authoring.md").read_text(encoding="utf-8")
    draft = json.loads(re.search(r"```json\n([\s\S]*?)\n```", ref).group(1))
    types = ["design-decision", "constraint", "client-direction", "coordination-issue", "site-discovery", "approval"]
    draft["observation_types"] = [{"id": t, "label": t, "description": t, "emoji": "\u25B2", "work_emoji": "\u25B3"}
                                  for t in types]
    return draft


def _install_mode(*args, cwd):
    return subprocess.run([sys.executable, str(ig.skills_dir() / "cairn-mode-creator" / "scripts" / "install_mode.py"),
                           *args], capture_output=True, text=True, cwd=cwd, env=dict(os.environ), check=False, encoding="utf-8", errors="replace")


def test_install_mode_script(tmp_path, repo):
    (repo / ".cairn").mkdir()
    (repo / ".cairn" / "config.toml").write_text('[server]\nport = 4747\n\n[recall]\nmode = "code"\n', encoding="utf-8")
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps(_mode_draft()), encoding="utf-8")
    dry = _install_mode("--mode", str(draft), "--mode-id", "code--architecture-practice", "--dry-run", cwd=repo)
    assert dry.returncode == 0, dry.stderr
    assert json.loads(dry.stdout)["dryRun"] is True
    modes_dir = tmp_path / "cairn-home" / "modes"
    assert not modes_dir.exists()
    res = _install_mode("--mode", str(draft), "--mode-id", "code--architecture-practice", "--root", str(repo),
                        cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["ok"] and out["modeName"] == "Architecture Practice" and out["activated"]
    assert (modes_dir / "code--architecture-practice.json").exists()
    cfg = (repo / ".cairn" / "config.toml").read_text(encoding="utf-8")
    assert 'mode = "code--architecture-practice"' in cfg and "[server]\nport = 4747" in cfg
    assert out["backups"]["config"] and Path(out["backups"]["config"]).exists()
    from cairn.engines.recall.modes import clear_cache, load_mode
    clear_cache()
    assert load_mode("code--architecture-practice").name == "Architecture Practice"
    again = json.loads(_install_mode("--mode", str(draft), "--mode-id", "code--architecture-practice", "--root",
                                     str(repo), "--no-activate", cwd=tmp_path).stdout)
    assert again["backups"]["mode"] and again["activated"] is False
    clear_cache()


def test_install_mode_validation(tmp_path, repo):
    bad = _mode_draft()
    bad["prompts"]["type_guidance"] = "type must be design-decision"
    draft = tmp_path / "bad.json"
    draft.write_text(json.dumps(bad), encoding="utf-8")
    r = _install_mode("--mode", str(draft), "--mode-id", "code--arch", "--dry-run", cwd=repo)
    assert r.returncode == 1 and "does not mention type: constraint" in r.stderr
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(_mode_draft()), encoding="utf-8")
    r = _install_mode("--mode", str(ok), "--mode-id", "code--chill", "--dry-run", cwd=repo)
    assert r.returncode == 1 and "collides with bundled mode" in r.stderr
    r = _install_mode("--mode", str(ok), "--mode-id", "Bad_ID", "--dry-run", cwd=repo)
    assert r.returncode == 1 and "kebab-case" in r.stderr
    r = _install_mode("--mode", str(ok), "--mode-id", "nosuchparent--x", "--dry-run", cwd=repo)
    assert r.returncode == 1 and "parent mode not found" in r.stderr
    assert _install_mode("--mode", cwd=repo).returncode == 1

"""PreCompact: a compact resume snapshot saved before the host compacts a session and handed back after it."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from cairn import agents, mcp_server
from cairn.engines.recall import hooks, integrations as ig, resume, worker
from cairn.engines.recall.store import Store


@pytest.fixture()
def repo(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "shop"
    (root / ".cairn").mkdir(parents=True)
    (root / "shop").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for k, v in {"CAIRN_HOME": str(tmp_path / "home"), "CAIRN_EMBEDDER": "hash", "CAIRN_NO_CLI_MODELS": "1",
                 "CAIRN_RECALL_NO_SPAWN": "1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CAIRN_INTERNAL", raising=False)
    return root


def send(repo, event, sid="s1", **p):
    return hooks.run(event, {"session_id": sid, "cwd": str(repo), **p})


def work(repo, sid="s1", files=("shop/a.py", "shop/b.py")):
    send(repo, "session-init", sid, prompt="Add refund retries to the gateway")
    for i, f in enumerate(files):
        send(repo, "observation", sid, tool_name="Edit", tool_input={"file_path": str(repo / f)},
             tool_response={"ok": 1}, tool_use_id=f"e{i}")
    send(repo, "observation", sid, tool_name="TodoWrite", tool_use_id="td", tool_input={"todos": [
        {"content": "Write the retry loop", "status": "completed", "activeForm": "x"},
        {"content": "Add the regression test", "status": "in_progress", "activeForm": "x"},
        {"content": "Update the docs", "status": "pending", "activeForm": "x"}]}, tool_response={"ok": 1})


def snapshot(repo, sid="s1"):
    with Store.open(repo) as st:
        return resume.build_snapshot(st, sid, str(repo))


def test_snapshot_lists_request_files_open_tasks_and_names_real_tools(repo):
    work(repo)
    text = snapshot(repo)
    assert "Last request: Add refund retries to the gateway" in text
    assert "Files changed (2): shop/a.py, shop/b.py" in text
    assert "Open tasks (2): Add the regression test; Update the docs" in text and "Write the retry loop" not in text
    assert 'cairn_session_search(query="a.py")' in text


def test_snapshot_points_at_tools_the_mcp_server_really_serves(repo):
    work(repo)
    send(repo, "summarize", answer=None, last_assistant_message="ok")
    with Store.open(repo) as st:
        mem = st.ensure_memory_session_id(st.find_session_db_id("s1", None))
        st.store_observations(mem, "shop", [{"type": "decision", "title": "Retries use backoff", "facts": [],
                                             "narrative": "n", "concepts": [], "files_read": [], "files_modified": []}], None, 1)
    text = snapshot(repo)
    served = set(re.findall(r"cairn_session_\w+", Path(mcp_server.__file__).read_text(encoding="utf-8"))) \
        | set(re.findall(r"cairn_session_\w+", (Path(hooks.__file__).parent / "mcp.py").read_text(encoding="utf-8")))
    named = set(re.findall(r"cairn_\w+", text))
    assert named >= {"cairn_session_search", "cairn_session_observations", "cairn_session_timeline"}
    assert named <= served, named - served
    assert "Retries use backoff" in text


def test_snapshot_is_under_two_kilobytes_however_much_happened(repo):
    send(repo, "session-init", prompt="x" * 5000)
    for i in range(80):
        send(repo, "observation", tool_name="Write", tool_input={"file_path": str(repo / f"shop/very_long_module_name_{i}.py")},
             tool_response={"ok": 1}, tool_use_id=f"w{i}")
    send(repo, "observation", tool_name="TodoWrite", tool_use_id="td", tool_response={"ok": 1}, tool_input={"todos": [
        {"content": "task " + "y" * 300 + str(i), "status": "pending", "activeForm": "x"} for i in range(30)]})
    with Store.open(repo) as st:
        mem = st.ensure_memory_session_id(st.find_session_db_id("s1", None))
        st.store_observations(mem, "shop", [{"type": "decision", "title": "z" * 400 + str(i), "facts": [], "narrative": str(i),
                                             "concepts": [], "files_read": [], "files_modified": []} for i in range(9)], None, 1)
    text = snapshot(repo)
    assert 0 < len(text.encode("utf-8")) <= resume.SNAPSHOT_MAX_BYTES
    assert text.startswith(resume.HEADER)


def test_nothing_recorded_means_no_snapshot(repo):
    assert snapshot(repo, "nope") == ""
    send(repo, "observation", tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="x")  # no prompt, no edit
    assert snapshot(repo) == ""


def test_secrets_never_reach_the_snapshot(repo):
    send(repo, "session-init", prompt="Deploy with API_TOKEN=abc123xyz789 now")
    assert "abc123xyz789" not in snapshot(repo)


def test_pre_compact_saves_and_the_compact_session_start_hands_it_over(repo):
    work(repo)
    res, out = send(repo, "pre-compact", trigger="auto")
    assert out == {} and not res.get("_push")  # nothing is returned to the host, nothing is pushed anywhere
    with Store.open(repo) as st:
        assert "Add refund retries" in st.get_kv("resume.s1")
    send(repo, "observation", tool_name="Edit", tool_input={"file_path": str(repo / "shop/late.py")}, tool_response={}, tool_use_id="late")
    _, out = send(repo, "context", source="compact")
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert ctx.startswith(resume.HEADER) and "late.py" not in ctx  # frozen when the host compacted
    with Store.open(repo) as st:
        assert st.get_kv("resume.s1") is None  # handed over once


def test_session_start_after_compaction_builds_one_when_none_was_saved(repo):
    work(repo)
    _, out = send(repo, "context", source="compact")
    assert "Files changed (2)" in out["hookSpecificOutput"]["additionalContext"]


def test_other_session_starts_are_untouched(repo):
    work(repo)
    send(repo, "pre-compact")
    for source in ("startup", "resume", "clear", None):
        payload = {"source": source} if source else {}
        _, out = send(repo, "context", **payload)
        assert resume.HEADER not in out["hookSpecificOutput"]["additionalContext"]


def test_a_compact_start_with_nothing_to_resume_falls_back_to_the_usual_context(repo):
    _, out = send(repo, "context", sid="fresh", source="compact")
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert resume.HEADER not in out["hookSpecificOutput"]["additionalContext"]


def test_a_failing_pre_compact_never_fails_the_host(repo, monkeypatch):
    work(repo)
    monkeypatch.setattr(resume, "build_snapshot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    res, out = send(repo, "pre-compact")
    assert out == {}


def test_private_prompts_and_untracked_projects_save_nothing(repo, monkeypatch):
    send(repo, "session-init", prompt="<private>secret plan</private>")
    send(repo, "pre-compact")
    with Store.open(repo) as st:
        assert st.get_kv("resume.s1") is None


def test_session_end_drops_an_unused_snapshot(repo):
    work(repo)
    send(repo, "pre-compact")
    send(repo, "session-end")
    with Store.open(repo) as st:
        assert st.get_kv("resume.s1") is None


def test_the_snapshot_includes_rejections_and_corrections_found_without_a_model(repo):
    work(repo)
    send(repo, "observation", tool_name="Bash", tool_input={"command": "git push --force"},
         tool_response={"error": "The user doesn't want to proceed with this tool use. The tool use was rejected."},
         tool_use_id="d1")
    send(repo, "summarize", last_assistant_message="ok")

    class NoModel:
        available = False
    worker.run_once(repo, router=NoModel())
    assert "Rejected by the user: Bash `git push --force`" in snapshot(repo)


# ---- wiring -----------------------------------------------------------------------------------------------------------
def test_init_wires_pre_compact_for_claude_only_and_stays_idempotent(cairn, monkeypatch):
    monkeypatch.setattr(agents.shutil, "which", lambda name: None)
    root = cairn.project.root
    agents.install(cairn.project, ["claude", "gemini", "codex", "cursor"])
    settings = root / ".claude" / "settings.json"
    first = settings.read_text(encoding="utf-8")
    hooks_cfg = json.loads(first)["hooks"]
    [entry] = [h for g in hooks_cfg["PreCompact"] for h in g["hooks"]]
    assert entry["command"].endswith("--platform claude-code pre-compact")
    assert any("PostToolUseFailure" in k for k in hooks_cfg)
    agents.install(cairn.project, ["claude", "gemini", "codex", "cursor"])
    assert settings.read_text(encoding="utf-8") == first  # a second init changes nothing
    assert len(json.loads(first)["hooks"]["PreCompact"]) == 1
    for other in (".gemini/settings.json", ".codex/hooks.json", ".cursor/hooks.json"):
        assert "pre-compact" not in (root / other).read_text(encoding="utf-8")
        assert "PreCompact" not in (root / other).read_text(encoding="utf-8")


def test_uninstall_removes_pre_compact_but_keeps_the_users_own_hook(cairn, monkeypatch):
    monkeypatch.setattr(agents.shutil, "which", lambda name: None)
    root = cairn.project.root
    settings = root / ".claude" / "settings.json"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(json.dumps({"hooks": {"PreCompact": [{"hooks": [{"type": "command", "command": "./mine.sh"}]}]}}),
                        encoding="utf-8")
    agents.install(cairn.project, ["claude"])
    assert len(json.loads(settings.read_text(encoding="utf-8"))["hooks"]["PreCompact"]) == 2
    agents.uninstall(cairn.project)
    left = json.loads(settings.read_text(encoding="utf-8")).get("hooks", {})
    assert left.get("PreCompact") == [{"hooks": [{"type": "command", "command": "./mine.sh"}]}]
    assert "PostToolUseFailure" not in left and "SessionStart" not in left


def test_the_hook_event_map_lists_pre_compact_for_claude_alone():
    assert ig.HOOK_EVENTS["claude-code"]["PreCompact"] == ["pre-compact"]
    for name, events in ig.HOOK_EVENTS.items():
        if name != "claude-code":
            assert "PreCompact" not in events and "pre-compact" not in sum(events.values(), [])

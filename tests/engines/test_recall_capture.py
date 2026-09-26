"""Recall capture: hook payloads -> the durable queue, adapters, privacy, settings, schema migrations."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cairn.engines.recall import adapters, hooks, ingest, schema, tags
from cairn.engines.recall import settings as rsettings
from cairn.engines.recall.store import Store


@pytest.fixture()
def repo(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "shop"
    (root / ".cairn").mkdir(parents=True)
    (root / "shop").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CAIRN_EMBEDDER", "hash")
    monkeypatch.setenv("CAIRN_NO_CLI_MODELS", "1")
    monkeypatch.setenv("CAIRN_RECALL_NO_SPAWN", "1")
    monkeypatch.delenv("CAIRN_INTERNAL", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return root


def run(repo: Path, event: str, platform: str = "claude-code", **payload):
    return hooks.run(event, {"session_id": "s1", "cwd": str(repo), **payload}, platform)


def rows(repo: Path, sql: str, args=()):
    with Store.open(repo) as st:
        return [dict(r) for r in st.db.execute(sql, args)]


def test_prompt_tool_and_stop_are_queued(repo):
    res, out = run(repo, "session-init", prompt="Fix the double charge on retry")
    assert res["_recorded"] == 1 and out == {}
    run(repo, "observation", tool_name="Read", tool_input={"file_path": str(repo / "shop/api.py")},
        tool_response={"content": "x"}, tool_use_id="t1")
    run(repo, "observation", tool_name="Bash", tool_input={"command": "pytest -q API_TOKEN=abc123"},
        tool_response={"stdout": "ok"}, tool_use_id="t2")
    res, _ = run(repo, "summarize", last_assistant_message="Fixed it.")
    assert res["_spawn"] and res["_recorded"] == 1
    sessions = rows(repo, "SELECT * FROM sdk_sessions")
    assert len(sessions) == 1 and sessions[0]["project"] == "shop" and sessions[0]["platform_source"] == "claude"
    prompts = rows(repo, "SELECT prompt_number, prompt_text FROM user_prompts")
    assert prompts == [{"prompt_number": 1, "prompt_text": "Fix the double charge on retry"}]
    queue = rows(repo, "SELECT message_type, tool_name, tool_input, prompt_number, status FROM pending_messages ORDER BY id")
    assert [q["message_type"] for q in queue] == ["observation", "observation", "summarize"]
    assert all(q["prompt_number"] == 1 and q["status"] == "pending" for q in queue)
    assert "abc123" not in queue[1]["tool_input"] and "API_TOKEN=***" in queue[1]["tool_input"]  # secrets masked
    assert {r["tool_use_id"] for r in rows(repo, "SELECT tool_use_id FROM tool_uses")} == {"t1", "t2"}


def test_duplicate_tool_calls_and_prompts_are_suppressed(repo):
    run(repo, "session-init", prompt="Explain the gateway")
    res, _ = run(repo, "session-init", prompt="Explain the gateway")  # a hook retry within the window
    assert res["_recorded"] == 0
    for _ in range(2):
        run(repo, "observation", tool_name="Read", tool_input={"file_path": "a.py"}, tool_use_id="same")
    assert len(rows(repo, "SELECT id FROM pending_messages")) == 1
    assert len(rows(repo, "SELECT id FROM user_prompts")) == 1


def test_private_content_never_reaches_the_store(repo):
    run(repo, "session-init", prompt="Deploy with <private>hunter2</private> the new key")
    assert rows(repo, "SELECT prompt_text FROM user_prompts")[0]["prompt_text"] == "Deploy with  the new key"
    run(repo, "observation", tool_name="Bash", tool_input={"command": "echo <private>secret</private> ok"})
    assert "secret" not in rows(repo, "SELECT tool_input FROM pending_messages")[0]["tool_input"]
    # a wholly private prompt withholds everything recorded for that turn
    res, _ = run(repo, "session-init", prompt="<private>my bank details</private>")
    assert res["_recorded"] == 0
    assert rows(repo, "SELECT prompt_text FROM user_prompts WHERE prompt_number=2")[0]["prompt_text"] == ""
    before = len(rows(repo, "SELECT id FROM pending_messages"))
    run(repo, "observation", tool_name="Read", tool_input={"file_path": "bank.txt"})
    run(repo, "summarize", last_assistant_message="Done")
    assert len(rows(repo, "SELECT id FROM pending_messages")) == before
    # ... and a session that opens with one keeps no trace of it either
    hooks.run("session-init", {"session_id": "s2", "cwd": str(repo), "prompt": "<private>pin 1234</private>"})
    assert rows(repo, "SELECT user_prompt FROM sdk_sessions WHERE content_session_id='s2'")[0]["user_prompt"] == ""


def test_tag_stripping_and_protocol_payloads():
    stripped, counts = tags.strip_tags("a <private>x</private> b <system-reminder>y</system-reminder> "
                                       "<cairn-context>z</cairn-context>")
    assert stripped == "a  b" and counts["private"] == 1 and counts["system-reminder"] == 1
    assert tags.is_internal_protocol_payload("<task-notification>done</task-notification>")
    assert not tags.is_internal_protocol_payload("please <task-notification>x</task-notification>")
    assert tags.normalize_stored_prompt_text("x" * 5000).endswith("…")
    assert tags.redact_value({"cmd": ["run --api-key abc"], "n": 1}) == {"cmd": ["run --api-key ***"], "n": 1}


def test_skip_tools_excluded_projects_internal_and_untracked(repo, tmp_path, monkeypatch):
    run(repo, "session-init", prompt="hi there")
    run(repo, "observation", tool_name="TodoWrite", tool_input={"todos": []})  # default skip list
    assert rows(repo, "SELECT id FROM pending_messages") == []
    assert hooks.record("prompt", {"session_id": "x", "cwd": str(tmp_path), "prompt": "no repo"}) == 0
    monkeypatch.setenv("CAIRN_INTERNAL", "1")
    assert hooks.record("prompt", {"session_id": "s1", "cwd": str(repo), "prompt": "internal call"}) == 0
    monkeypatch.delenv("CAIRN_INTERNAL")
    rsettings.save(repo, {"excluded_projects": str(repo)})
    assert hooks.record("tool", {"session_id": "s1", "cwd": str(repo), "tool_name": "Bash",
                                 "tool_input": {"command": "ls"}}) == 0
    rsettings.save(repo, {"excluded_projects": "", "capture": False})
    assert hooks.record("prompt", {"session_id": "s1", "cwd": str(repo), "prompt": "capture is off"}) == 0


def test_internal_protocol_prompt_and_subagent_stop_are_ignored(repo):
    res, _ = run(repo, "session-init", prompt="<task-notification>agent finished</task-notification>")
    assert not res.get("_recorded")
    res, _ = run(repo, "summarize", last_assistant_message="x", agent_id="sub-1")
    assert not res.get("_recorded")
    res, _ = run(repo, "summarize", last_assistant_message="x", stop_hook_active=True)
    assert not res.get("_recorded")


def test_summary_text_comes_from_the_transcript(repo, tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(x) for x in [
        {"type": "user", "message": {"content": "do it"}},
        {"type": "assistant", "message": {"model": "claude-opus-5-5", "content": [
            {"type": "text", "text": "All done <system-reminder>noise</system-reminder>"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "x", "name": "Read"}]}},
        "not json"]) + "\nnot json either\n")
    run(repo, "session-init", prompt="do it")
    run(repo, "summarize", transcript_path=str(transcript))
    [msg] = rows(repo, "SELECT last_assistant_message FROM pending_messages")
    assert msg["last_assistant_message"] == "All done"
    assert rows(repo, "SELECT observed_model FROM sdk_sessions")[0]["observed_model"] == "claude-opus-5-5"


def test_session_end_marks_completed_and_a_new_prompt_reopens(repo):
    run(repo, "session-init", prompt="first prompt")
    res, _ = run(repo, "session-end")
    assert res["_spawn"]
    assert rows(repo, "SELECT status FROM sdk_sessions")[0]["status"] == "completed"
    run(repo, "session-init", prompt="resumed prompt")
    assert rows(repo, "SELECT status FROM sdk_sessions")[0]["status"] == "active"


def test_platform_adapters_normalize_payloads(repo, tmp_path):
    codex = adapters.normalize("codex", {"session_id": "c1", "cwd": str(repo), "hook_event_name": "PreToolUse",
                                         "tool_name": "Bash", "tool_input": {"command": "cat shop/a.py | head -n 5 x"}})
    assert codex["tool_name"] == "Bash" and codex["platform"] == "codex"
    (repo / "shop" / "a.py").write_text("print(1)\n")
    codex = adapters.normalize("codex", {"session_id": "c1", "cwd": str(repo), "hook_event_name": "PreToolUse",
                                         "tool_name": "Bash", "tool_input": {"command": "cat shop/a.py"}})
    assert codex["tool_input"]["filePaths"] == ["shop/a.py"]
    with pytest.raises(adapters.AdapterRejectedInput):
        adapters.normalize("codex", {"cwd": str(repo)})
    cursor = adapters.normalize("cursor", {"conversation_id": "k1", "workspace_roots": [str(repo)], "command": "ls",
                                           "output": "a"})
    assert cursor["tool_name"] == "Bash" and cursor["tool_input"] == {"command": "ls"}
    wind = adapters.normalize("windsurf", {"trajectory_id": "w1", "agent_action_name": "post_write_code",
                                           "tool_info": {"cwd": str(repo), "file_path": "x.py", "edits": []}})
    assert wind["tool_name"] == "Write" and wind["file_path"] == "x.py"
    agy = adapters.normalize("antigravity", {"conversationId": "g1", "workspacePaths": [str(repo)],
                                             "toolCall": {"name": "view_file", "args": {"path": "a"}}, "error": ""})
    assert agy["tool_response"] == {"status": "completed"}
    assert adapters.format_output("antigravity", {"hookSpecificOutput": {"additionalContext": "\x1b[31mhi"}}) == \
        {"injectSteps": [{"ephemeralMessage": "hi"}]}
    assert adapters.format_output("cursor", {"continue": True, "suppressOutput": True}) == {}
    assert adapters.format_output("cursor", {"continue": False}) == {"continue": False}
    assert adapters.format_output("cursor", {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                                    "additionalContext": "memory"}}) == \
        {"additional_context": "memory"}
    gem = adapters.normalize("gemini", {"session_id": "m", "cwd": str(repo), "prompt_response": "final answer"})
    assert gem["last_assistant_message"] == "final answer"
    # the same queue receives every agent's events
    hooks.run("observation", {"conversation_id": "k1", "workspace_roots": [str(repo)], "command": "ls",
                              "output": "a"}, "cursor")
    assert rows(repo, "SELECT platform_source FROM sdk_sessions WHERE content_session_id='k1'")[0][
        "platform_source"] == "cursor"


def test_capture_entry_point_output_and_resilience(repo):
    def cli(event, payload, *extra):
        return subprocess.run([sys.executable, "-m", "cairn.capture", *extra, event], input=payload,
                              capture_output=True, text=True, cwd=repo, check=False)
    bad = cli("tool", "not json")
    assert bad.returncode == 0 and bad.stdout == "" and bad.stderr == ""
    ctx = cli("context", json.dumps({"session_id": "s1", "cwd": str(repo)}))
    out = json.loads(ctx.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "no session memory yet" in out["hookSpecificOutput"]["additionalContext"]
    broken = cli("context", "{broken", "--platform", "codex")
    assert json.loads(broken.stdout)["hookSpecificOutput"]["additionalContext"] == ""
    ok = cli("session-init", json.dumps({"session_id": "s9", "cwd": str(repo), "prompt": "from the CLI"}),
             "--platform", "claude-code")
    assert ok.returncode == 0
    assert rows(repo, "SELECT prompt_text FROM user_prompts") == [{"prompt_text": "from the CLI"}]


def test_file_context_hook_injects_prior_observations_once(repo):
    big = repo / "shop" / "payments.py"
    big.write_text("# payments\n" + "x = 1\n" * 400)
    import os
    import time
    os.utime(big, (time.time() - 3600, time.time() - 3600))
    with Store.open(repo) as st:
        sid = st.create_sdk_session("old", "shop", "earlier")
        mid = st.ensure_memory_session_id(sid)
        st.store_observations(mid, "shop", [{"type": "bugfix", "title": "Retry guard added", "narrative": "n",
                                             "facts": [], "concepts": ["gotcha"], "files_read": [],
                                             "files_modified": ["shop/payments.py"]}])
    res, out = run(repo, "file-context", tool_name="Read", tool_input={"file_path": str(big)})
    ctx = out["hookSpecificOutput"]
    assert ctx["hookEventName"] == "PreToolUse" and ctx["permissionDecision"] == "allow"
    assert "Retry guard added" in ctx["additionalContext"] and "get_observations" in ctx["additionalContext"]
    _, again = run(repo, "file-context", tool_name="Read", tool_input={"file_path": str(big)})
    assert again == {}  # already surfaced this session
    small = repo / "shop" / "tiny.py"
    small.write_text("x=1\n")
    _, none = run(repo, "file-context", session_id="s2", tool_name="Read", tool_input={"file_path": str(small)})
    assert none == {}


def test_settings_round_trip_preserves_the_rest_of_the_file(repo):
    cfg = repo / ".cairn" / "config.toml"
    cfg.write_text('[server]\nport = 4800   # keep me\n\n[recall]\nmode = "code"  # the mode\n')
    rsettings.save(repo, {"mode": "code--ja", "context_observations": 12, "folder_md_exclude": '["vendor"]'})
    text = cfg.read_text()
    assert "port = 4800   # keep me" in text and 'mode = "code--ja"' in text
    s = rsettings.load(repo)
    assert s["mode"] == "code--ja" and s["context_observations"] == 12 and s["folder_md_exclude"] == '["vendor"]'
    cfg.write_text(text + 'skip_tools = "A,B"\n')
    assert rsettings.load(repo)["skip_tools"] == "A,B"
    with pytest.raises(KeyError):
        rsettings.save(repo, {"nope": 1})


def test_schema_migrations_are_recorded_and_idempotent(tmp_path):
    db_path = tmp_path / "s.db"
    db = schema.connect(db_path)
    assert schema.schema_versions(db) == [1, 2, 3, 4, 5, 6]
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
    assert {"sdk_sessions", "observations", "session_summaries", "user_prompts", "pending_messages", "tool_uses",
            "observations_fts", "session_summaries_fts", "user_prompts_fts", "observations_ai", "vector_docs",
            "observer_conversations", "file_context_injections", "kv", "remote_outbox", "remote_received"} <= names
    db.close()
    db = schema.connect(db_path)  # re-opening applies nothing new
    assert schema.schema_versions(db) == [1, 2, 3, 4, 5, 6]
    ro = schema.connect(db_path, readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO kv(key, value) VALUES('a', 'b')")
    with pytest.raises(FileNotFoundError):
        schema.connect(tmp_path / "missing.db", readonly=True)


def test_legacy_capture_log_is_imported(repo):
    """Stores written by Cairn's earlier minimal capture hook keep their history."""
    path = schema.store_path(repo)
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, session TEXT NOT NULL,
          kind TEXT NOT NULL, tool TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '');
        INSERT INTO events(ts, session, kind, tool, detail, path) VALUES
          (1700000000, 'old', 'prompt', '', 'Fix the ledger', ''),
          (1700000001, 'old', 'read', 'Read', '', 'shop/ledger.py'),
          (1700000002, 'old', 'edit', 'Edit', '', 'shop/ledger.py'),
          (1700000003, 'old', 'command', 'Bash', 'pytest -q', ''),
          (1700000010, 'old', 'prompt', '', 'Just a question', '');
    """)
    db.commit()
    db.close()
    with Store.open(repo, project_name="shop") as st:
        assert schema.schema_versions(st.db) == [1, 2, 3, 4, 5, 6]
        prompts = [r[0] for r in st.db.execute("SELECT prompt_text FROM user_prompts ORDER BY prompt_number")]
        assert prompts == ["Fix the ledger", "Just a question"]
        obs = [dict(r) for r in st.db.execute("SELECT title, type, files_modified, project FROM observations ORDER BY id")]
        assert obs[0]["title"] == "Fix the ledger" and json.loads(obs[0]["files_modified"]) == ["shop/ledger.py"]
        assert obs[0]["project"] == "shop" and len(obs) == 2
        assert st.db.execute("SELECT 1 FROM sqlite_master WHERE name='legacy_capture_events'").fetchone()


def test_ingest_functions_directly(repo):
    with Store.open(repo) as st:
        res = ingest.session_init(st, content_session_id="d1", project="shop", prompt="  ", platform_source="codex")
        assert res["promptNumber"] == 1 and not res["skipped"]
        assert st.get_user_prompt("d1", 1, res["sessionDbId"]) == ingest.MEDIA_PROMPT
        q = ingest.ingest_observation(st, rsettings.load(repo), content_session_id="d1", tool_name="Read",
                                      tool_input={"file_path": "/x/session-memory/notes.md"}, platform_source="codex")
        assert q["reason"] == "session_memory_meta"
        q = ingest.ingest_observation(st, rsettings.load(repo), content_session_id="d1", tool_name="Edit",
                                      tool_input={"file_path": "a.py"}, platform_source="codex", prompt_number=1)
        assert q["status"] == "queued"
        assert ingest.session_end(st, content_session_id="missing")["status"] == "unknown_session"

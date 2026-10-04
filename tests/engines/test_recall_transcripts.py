"""Transcript ingestion: schema-driven JSONL watching (offsets, globs, match rules, field specs, actions)
and Claude Code transcript replay (idempotent recovery of prompts, tool calls and turn summaries)."""
from __future__ import annotations

import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from cairn.engines.recall import ingest
from cairn.engines.recall import transcripts as tr
from cairn.engines.recall.settings import load as load_settings
from cairn.engines.recall.store import Store

CLAUDE_SID = "11111111-2222-3333-4444-555555555555"
CODEX_SID = "0199a0b1-aaaa-4bbb-8ccc-dddddddddddd"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CAIRN_RECALL_NO_SPAWN", "1")
    monkeypatch.delenv("CAIRN_INTERNAL", raising=False)


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / ".cairn").mkdir(parents=True)
    return root


@pytest.fixture
def outside(tmp_path) -> Path:
    other = tmp_path / "elsewhere"
    other.mkdir()
    return other


def write_jsonl(path: Path, entries: list[dict], mode: str = "w") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode) as fh:
        fh.writelines(json.dumps(e) + "\n" for e in entries)
    return path


def rows(root: Path, sql: str, args: tuple = ()) -> list[dict]:
    store = Store.open(root)
    try:
        return [dict(r) for r in store.db.execute(sql, args)]
    finally:
        store.close()


# ---- Claude Code replay -----------------------------------------------------------------------------------
def claude_lines(cwd: Path, sid: str = CLAUDE_SID) -> list[dict]:
    base = {"sessionId": sid, "cwd": str(cwd)}
    return [
        {"type": "summary", "summary": "Login form", "leafUuid": "x"},
        {**base, "type": "user", "uuid": "u1", "message": {"role": "user", "content": "Add a login form"}},
        {**base, "type": "assistant", "message": {"model": "claude-test", "content": [
            {"type": "text", "text": "Let me look at the page."},
            {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": f"{cwd}/app.py"}}]}},
        {**base, "type": "user", "toolUseResult": {"type": "text", "file": {"filePath": "app.py"}},
         "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "print(1)"}]}},
        {**base, "type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_2", "name": "Edit",
             "input": {"file_path": f"{cwd}/app.py", "old_string": "1", "new_string": "2"}}]}},
        {**base, "type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "ok"}]}},
        {**base, "type": "assistant", "message": {"content": [
            {"type": "text", "text": "Added the form.<system-reminder>noise</system-reminder>"}]}},
        {**base, "type": "user", "isMeta": True,
         "message": {"content": "<local-command-caveat>x</local-command-caveat>"}},
        {**base, "type": "user", "isSidechain": True, "message": {"content": "a subagent prompt"}},
        {**base, "type": "user", "message": {"content": [{"type": "text", "text": "[Request interrupted by user]"}]}},
        {**base, "type": "user", "message": {"content": [{"type": "text", "text": "Now add tests"}]}},
        {**base, "type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu_3", "name": "Bash", "input": {"command": "pytest -q"}}]}},
        {**base, "type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_3", "content": [{"type": "text", "text": "3 passed"}]}]}},
        {**base, "type": "assistant", "message": {"content": [{"type": "text", "text": "Tests added."}]}},
    ]


def test_replay_records_prompts_observations_and_summaries(repo, tmp_path):
    path = write_jsonl(tmp_path / "t" / f"{CLAUDE_SID}.jsonl", claude_lines(repo))
    res = tr.ingest_claude_transcript(path)
    assert (res["prompts"], res["observations"], res["summaries"]) == (2, 3, 2)
    assert res["sessions"] == [CLAUDE_SID]

    prompts = rows(repo, "SELECT prompt_number, prompt_text FROM user_prompts ORDER BY prompt_number")
    assert [(p["prompt_number"], p["prompt_text"]) for p in prompts] == [(1, "Add a login form"), (2, "Now add tests")]
    obs = rows(repo, "SELECT tool_name, tool_use_id, prompt_number, tool_response FROM pending_messages"
                     " WHERE message_type='observation' ORDER BY id")
    assert [(o["tool_name"], o["tool_use_id"], o["prompt_number"]) for o in obs] == [
        ("Read", "toolu_1", 1), ("Edit", "toolu_2", 1), ("Bash", "toolu_3", 2)]
    assert json.loads(obs[0]["tool_response"]) == {"type": "text", "file": {"filePath": "app.py"}}  # toolUseResult
    sums = rows(repo, "SELECT prompt_number, last_assistant_message FROM pending_messages"
                      " WHERE message_type='summarize' ORDER BY id")
    assert [(s["prompt_number"], s["last_assistant_message"]) for s in sums] == [(1, "Added the form."),
                                                                                 (2, "Tests added.")]
    session = rows(repo, "SELECT platform_source, observed_model FROM sdk_sessions")[0]
    assert session == {"platform_source": "claude", "observed_model": "claude-test"}
    assert rows(repo, "SELECT COUNT(*) AS n FROM tool_uses")[0]["n"] == 3


def test_replay_twice_adds_nothing(repo, tmp_path):
    path = write_jsonl(tmp_path / "t" / f"{CLAUDE_SID}.jsonl", claude_lines(repo))
    tr.ingest_claude_transcript(path)
    before = rows(repo, "SELECT (SELECT COUNT(*) FROM user_prompts) p, (SELECT COUNT(*) FROM pending_messages) q,"
                        " (SELECT COUNT(*) FROM tool_uses) t")[0]
    again = tr.ingest_claude_transcript(path)
    after = rows(repo, "SELECT (SELECT COUNT(*) FROM user_prompts) p, (SELECT COUNT(*) FROM pending_messages) q,"
                       " (SELECT COUNT(*) FROM tool_uses) t")[0]
    assert before == after == {"p": 2, "q": 5, "t": 3}
    assert (again["prompts"], again["observations"], again["summaries"]) == (0, 0, 0)
    assert (again["prompts_existing"], again["observations_existing"], again["summaries_existing"]) == (2, 3, 2)


def test_replay_is_idempotent_after_the_queue_drained(repo, tmp_path):
    path = write_jsonl(tmp_path / "t" / f"{CLAUDE_SID}.jsonl", claude_lines(repo))
    tr.ingest_claude_transcript(path)
    store = Store.open(repo)
    store.db.execute("DELETE FROM pending_messages")   # the worker processed everything
    store.close()
    again = tr.ingest_claude_transcript(path)
    assert (again["prompts"], again["observations"], again["summaries"]) == (0, 0, 0)
    assert rows(repo, "SELECT COUNT(*) AS n FROM pending_messages")[0]["n"] == 0


def test_replay_skips_what_the_live_hooks_recorded(repo, tmp_path):
    store = Store.open(repo)
    ingest.session_init(store, content_session_id=CLAUDE_SID, project="repo", prompt="Add a login form",
                        platform_source="claude-code", cwd=str(repo))
    store.close()
    path = write_jsonl(tmp_path / "t" / f"{CLAUDE_SID}.jsonl", claude_lines(repo))
    res = tr.ingest_claude_transcript(path)
    assert (res["prompts"], res["prompts_existing"]) == (1, 1)
    assert [p["prompt_text"] for p in rows(repo, "SELECT prompt_text FROM user_prompts ORDER BY prompt_number")] == [
        "Add a login form", "Now add tests"]


def test_replay_skips_lines_outside_a_cairn_repo(repo, outside, tmp_path):
    other_sid = "99999999-2222-3333-4444-555555555555"
    lines = claude_lines(repo) + claude_lines(outside, sid=other_sid)
    res = tr.ingest_claude_transcript(write_jsonl(tmp_path / "t" / "mixed.jsonl", lines))
    assert res["prompts"] == 2 and res["skipped_no_repo"] >= 2
    assert {r["content_session_id"] for r in rows(repo, "SELECT content_session_id FROM sdk_sessions")} == {CLAUDE_SID}
    assert not (outside / ".cairn").exists()


def test_replay_project_uses_dashed_cwd_under_claude_config_dir(repo, tmp_path):
    project_dir = tmp_path / "claude" / "projects" / tr.cwd_to_dashed(str(repo))
    assert "/" not in project_dir.name and "." not in project_dir.name
    write_jsonl(project_dir / f"{CLAUDE_SID}.jsonl", claude_lines(repo))
    res = tr.ingest_claude_project(repo)
    assert res["files"] == 1 and res["prompts"] == 2 and res["observations"] == 3
    assert tr.ingest_claude_project(repo)["prompts"] == 0


def test_cli_replay_accepts_a_project_folder(repo, tmp_path, capsys):
    write_jsonl(tmp_path / "claude" / "projects" / tr.cwd_to_dashed(str(repo)) / f"{CLAUDE_SID}.jsonl",
                claude_lines(repo))
    assert tr.cli_replay(repo) == 0
    assert "2 new prompt(s)" in capsys.readouterr().out
    assert tr.cli_replay(tmp_path / "missing") == 1


def test_claude_prompt_text_normalizes_slash_commands_and_drops_non_prompts():
    cmd = "<command-message>review is running</command-message>\n<command-name>/review</command-name>\n" \
          "<command-args>src/app.py</command-args>"
    assert tr.claude_prompt_text(cmd) == "/review src/app.py"
    assert tr.claude_prompt_text("<local-command-stdout>done</local-command-stdout>") is None
    assert tr.claude_prompt_text("[Request interrupted by user for tool use]") is None
    assert tr.claude_prompt_text("fix it") == "fix it"
    assert tr.stored_prompt_form("<private>secret</private>") == ""
    assert tr.stored_prompt_form("  ") == ingest.MEDIA_PROMPT


# ---- schema-driven watcher --------------------------------------------------------------------------------
def codex_turn(cwd: Path, prompt: str, call: str, answer: str, meta: bool = False) -> list[dict]:
    out = [{"type": "session_meta", "payload": {"id": CODEX_SID, "cwd": str(cwd)}}] if meta else []
    return out + [
        {"type": "turn_context", "payload": {"cwd": str(cwd)}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": prompt}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "shell",
                                              "arguments": json.dumps({"command": ["ls"]}), "call_id": call}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": call,
                                              "output": json.dumps({"output": "app.py"})}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": answer}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ]


def codex_config(tmp_path: Path, **watch_extra) -> Path:
    cfg = {"version": 1, "schemas": {"codex": tr.CODEX_SCHEMA},
           "watches": [{"name": "codex", "path": str(tmp_path / "codex" / "**" / "*.jsonl"), "schema": "codex",
                        **watch_extra}],
           "stateFile": str(tmp_path / "state.json")}
    path = tmp_path / "watch.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def codex_file(tmp_path: Path) -> Path:
    return tmp_path / "codex" / "2026" / "09" / f"rollout-2026-09-25T10-00-00-{CODEX_SID}.jsonl"


def test_codex_events_land_in_the_store(repo, tmp_path):
    write_jsonl(codex_file(tmp_path), codex_turn(repo, "fix the bug", "call_1", "Fixed.", meta=True))
    res = tr.run_once(codex_config(tmp_path), settings=load_settings(repo))
    assert res["status"] == "ok" and res["lines"] == 7
    assert (res["prompts"], res["observations"], res["summaries"], res["session_ends"]) == (1, 1, 1, 1)
    session = rows(repo, "SELECT content_session_id, platform_source, status FROM sdk_sessions")[0]
    assert session == {"content_session_id": CODEX_SID, "platform_source": "codex", "status": "completed"}
    obs = rows(repo, "SELECT tool_name, tool_use_id, tool_input FROM pending_messages WHERE message_type='observation'")
    assert obs[0]["tool_name"] == "shell" and obs[0]["tool_use_id"] == "call_1"
    assert json.loads(obs[0]["tool_input"]) == {"command": ["ls"]}
    assert rows(repo, "SELECT last_assistant_message FROM pending_messages WHERE message_type='summarize'")[0][
        "last_assistant_message"] == "Fixed."


def test_state_offsets_make_run_once_incremental(repo, tmp_path):
    cfg, settings, path = codex_config(tmp_path), load_settings(repo), codex_file(tmp_path)
    first = codex_turn(repo, "first prompt", "call_1", "One.", meta=True)
    write_jsonl(path, first[:4])                        # ends on a function_call with no output yet
    assert tr.run_once(cfg, settings=settings)["prompts"] == 1
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["offsets"][str(path)] == path.stat().st_size
    assert any(s["pendingTools"] for s in state["sessions"].values())   # survives between runs

    write_jsonl(path, first[4:] + codex_turn(repo, "second prompt", "call_2", "Two."), mode="a")
    second = tr.run_once(cfg, settings=settings)
    assert second["lines"] == len(first) - 4 + 6
    assert (second.get("prompts"), second["observations"], second["summaries"]) == (1, 2, 2)
    assert [p["prompt_text"] for p in rows(repo, "SELECT prompt_text FROM user_prompts ORDER BY id")] == [
        "first prompt", "second prompt"]
    names = rows(repo, "SELECT tool_name FROM pending_messages WHERE tool_use_id='call_1'")
    assert names == [{"tool_name": "shell"}]            # the pending tool_use was paired across runs

    third = tr.run_once(cfg, settings=settings)
    assert third["lines"] == 0 and "prompts" not in third


def test_partial_lines_wait_and_truncation_restarts(repo, tmp_path):
    cfg, settings, path = codex_config(tmp_path), load_settings(repo), codex_file(tmp_path)
    lines = codex_turn(repo, "hello there", "call_1", "Hi.", meta=True)
    text = "".join(json.dumps(e) + "\n" for e in lines)
    path.parent.mkdir(parents=True)
    path.write_text(text[:-10], encoding="utf-8")                         # the last line is still being written
    assert tr.run_once(cfg, settings=settings)["lines"] == len(lines) - 1
    path.write_text(text, encoding="utf-8")
    assert tr.run_once(cfg, settings=settings)["lines"] == 1
    path.write_text(json.dumps(lines[1]) + "\n", encoding="utf-8")        # truncated/rewritten: read from the start
    assert tr.run_once(cfg, settings=settings)["lines"] == 1
    rotated = path.with_name("rotated.tmp")
    rotated.write_text(text + text, encoding="utf-8")                     # a new, larger file moved into place (rotation)
    rotated.replace(path)
    assert tr.run_once(cfg, settings=settings)["lines"] == 2 * len(lines)


def test_start_at_end_skips_existing_history(repo, tmp_path):
    cfg, settings, path = codex_config(tmp_path, startAtEnd=True), load_settings(repo), codex_file(tmp_path)
    write_jsonl(path, codex_turn(repo, "old prompt", "call_0", "Old.", meta=True))
    assert tr.run_once(cfg, settings=settings)["lines"] == 0
    write_jsonl(path, codex_turn(repo, "new prompt", "call_1", "New."), mode="a")
    assert tr.run_once(cfg, settings=settings)["prompts"] == 1
    assert [p["prompt_text"] for p in rows(repo, "SELECT prompt_text FROM user_prompts")] == ["new prompt"]


def test_events_whose_cwd_has_no_cairn_repo_are_skipped(repo, outside, tmp_path):
    write_jsonl(codex_file(tmp_path), codex_turn(outside, "elsewhere", "call_1", "Nope.", meta=True))
    res = tr.run_once(codex_config(tmp_path), settings=load_settings(repo))
    assert res["skipped_no_repo"] >= 3 and "prompts" not in res
    assert not (outside / ".cairn").exists()
    assert rows(repo, "SELECT COUNT(*) AS n FROM sdk_sessions")[0]["n"] == 0


def test_run_once_status_when_disabled_or_unconfigured(repo, tmp_path):
    settings = load_settings(repo)
    assert tr.run_once(tmp_path / "nope.json", settings=settings)["status"] == "no_config"
    assert tr.run_once(codex_config(tmp_path), settings={**settings, "transcripts_enabled": False})["status"] == \
        "disabled"
    (tmp_path / "bad.json").write_text(json.dumps({"version": 1}), encoding="utf-8")
    assert tr.run_once(tmp_path / "bad.json", settings=settings)["status"] == "invalid_config"


def test_watch_runs_on_a_thread_until_stopped(repo, tmp_path):
    cfg = codex_config(tmp_path)
    stop = threading.Event()
    result: dict = {}
    t = threading.Thread(target=lambda: result.update(tr.watch(cfg, stop, 0.05, settings=load_settings(repo))))
    t.start()
    try:
        write_jsonl(codex_file(tmp_path), codex_turn(repo, "threaded prompt", "call_1", "Done.", meta=True))
        deadline = time.time() + 10
        while time.time() < deadline:
            if rows(repo, "SELECT COUNT(*) AS n FROM pending_messages WHERE message_type='summarize'")[0]["n"]:
                break
            time.sleep(0.05)
    finally:
        stop.set()
        t.join(10)
    assert not t.is_alive()
    assert result["prompts"] == 1 and result["summaries"] == 1


def test_apply_patch_becomes_file_edits(repo, tmp_path):
    patch = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** Add File: src/b.py\n+z\n*** End Patch"
    lines = [{"type": "turn_context", "payload": {"cwd": str(repo)}},
             {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch",
                                                   "input": patch, "call_id": "call_p"}},
             {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "call_p",
                                                   "output": "Success"}}]
    write_jsonl(codex_file(tmp_path), lines)
    res = tr.run_once(codex_config(tmp_path), settings=load_settings(repo))
    assert res["file_edits"] == 2 and res["observations"] == 1
    inputs = [json.loads(r["tool_input"]) for r in rows(
        repo, "SELECT tool_input FROM pending_messages WHERE tool_name='write_file' ORDER BY id")]
    assert [i["filePath"] for i in inputs] == ["src/a.py", "src/b.py"]
    assert rows(repo, "SELECT tool_name FROM pending_messages WHERE tool_use_id='call_p'") == [
        {"tool_name": "apply_patch"}]


def test_agents_context_is_written_through_folders(repo, tmp_path, monkeypatch):
    calls: list[tuple] = []
    fake = types.ModuleType("cairn.engines.recall.folders")
    fake.write_agents_md = lambda path, context: calls.append((str(path), context))
    monkeypatch.setitem(sys.modules, "cairn.engines.recall.folders", fake)
    write_jsonl(codex_file(tmp_path), codex_turn(repo, "context please", "call_1", "Ok.", meta=True))
    tr.run_once(codex_config(tmp_path, context={"mode": "agents", "updateOn": ["session_start"]}),
                settings=load_settings(repo))
    assert len(calls) == 2                              # session start + session end
    assert calls[0][0] == f"{repo}/AGENTS.md" and calls[0][1].strip()

    calls.clear()
    proc = tr.TranscriptEventProcessor()
    session = tr.SessionState("s", "codex", cwd=str(repo))
    outside_path = {"name": "x", "context": {"mode": "agents", "path": str(tmp_path / "evil.md")}}
    assert proc.update_context(session, outside_path) is False and not calls


# ---- config, codex filtering, helpers -----------------------------------------------------------------------
def test_sample_config_ships_the_codex_schema(tmp_path):
    path = tr.write_sample_config()
    assert path == tmp_path / "home" / "transcript-watch.json"
    cfg = tr.load_transcript_watch_config(path)
    assert cfg["schemas"]["codex"]["name"] == "codex" and cfg["watches"][0]["path"] == "~/.codex/sessions/**/*.jsonl"
    assert cfg["stateFile"] == str(tmp_path / "home" / "transcript-watch-state.json")
    assert tr.validate_config(cfg) == []
    with pytest.raises(tr.ConfigNotFoundError):
        tr.load_transcript_watch_config(tmp_path / "absent.json")


def test_config_path_follows_settings(tmp_path):
    home = tmp_path / "home"
    assert tr.resolve_config_path(None, {"transcripts_config_path": "$CAIRN_HOME/x.json"}) == home / "x.json"
    assert tr.resolve_config_path(None, {}) == tmp_path / "home" / "transcript-watch.json"
    assert tr.expand_home_path("~alice/t") == "~alice/t"


def test_cli_init_validate(tmp_path, capsys):
    cfg = tmp_path / "cfg" / "w.json"
    assert tr.cli_validate(cfg) == 0                    # missing: the sample is created, then validated
    assert "Created sample config" in capsys.readouterr().out
    cfg.write_text(json.dumps({"version": 1, "watches": [{"name": "w", "path": "/x", "schema": "nope"}]}), encoding="utf-8")
    assert tr.cli_validate(cfg) == 1
    assert tr.cli_init(cfg) == 0 and tr.cli_validate(cfg) == 0
    assert tr.run_transcript_command("bogus", []) == 1


def test_native_codex_watch_filtering(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    native = {"name": "codex", "path": "~/.codex/sessions/**/*.jsonl", "schema": "codex",
              "context": {"mode": "agents"}}
    custom = {"name": "codex", "path": "/elsewhere/**/*.jsonl", "schema": "codex"}
    assert tr.is_native_hook_backed_codex_watch(native) and not tr.is_native_hook_backed_codex_watch(custom)
    assert tr.should_suppress_native_codex_agents_context(native)
    assert not tr.should_suppress_native_codex_agents_context({**native, "name": "mine"})
    cfg = {"version": 1, "watches": [native, custom]}
    kept, removed = tr.filter_native_hook_backed_codex_watches(cfg, False)
    assert removed == 1 and kept["watches"] == [custom]
    assert tr.filter_native_hook_backed_codex_watches(cfg, True) == (cfg, 0)


def test_helpers():
    assert tr.parse_apply_patch_files("--- a/x.py\n+++ b/x.py\n+++ /dev/null\n*** Move to: y.py") == ["x.py", "y.py"]
    assert tr.resolve_watch_agent_id({"path": "/r/agent-transcripts/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/x.jsonl"}) \
        == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert tr.resolve_watch_agent_id({"agentId": " grok-1 ", "path": "/x"}) == "grok-1"
    assert tr.resolve_watch_agent_id({"agentId": "*", "path": "/x"}) is None
    assert tr.extract_session_id_from_path(f"/a/rollout-{CODEX_SID}.jsonl") == CODEX_SID
    assert tr.expand_braces("a/{b,c{d,e}}/*.jsonl") == ["a/b/*.jsonl", "a/cd/*.jsonl", "a/ce/*.jsonl"]


def test_resolve_watch_files_globs_folders_and_braces(tmp_path):
    for rel in ("s/1/a.jsonl", "s/2/deep/b.jsonl", "s/.hidden/c.jsonl", "s/x.txt", "t/d.jsonl"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("", encoding="utf-8")
    names = lambda files: sorted(Path(f).name for f in files)
    assert names(tr.resolve_watch_files(str(tmp_path / "s" / "**" / "*.jsonl"))) == ["a.jsonl", "b.jsonl", "c.jsonl"]
    assert names(tr.resolve_watch_files(str(tmp_path / "s"))) == ["a.jsonl", "b.jsonl", "c.jsonl"]
    assert names(tr.resolve_watch_files(str(tmp_path / "{s,t}" / "**" / "*.jsonl"))) == [
        "a.jsonl", "b.jsonl", "c.jsonl", "d.jsonl"]
    assert tr.resolve_watch_files(str(tmp_path / "t" / "d.jsonl")) == [str(tmp_path / "t" / "d.jsonl")]
    assert tr.resolve_watch_files(str(tmp_path / "missing")) == []


# ---- match rules and field specs ----------------------------------------------------------------------------
ENTRY = {"type": "event", "payload": {"type": "user_message", "message": "hello world", "n": 3, "empty": "",
                                      "flag": True, "items": [{"id": "first"}, {"id": "second"}], "nothing": None}}
SCHEMA = {"name": "s", "eventTypePath": "payload.type"}


@pytest.mark.parametrize("rule,expected", [
    (None, True),
    ({"equals": "user_message"}, True),                       # default path: schema.eventTypePath
    ({"path": "type", "equals": "event"}, True),
    ({"path": "payload.n", "equals": 3}, True),
    ({"path": "payload.n", "equals": "3"}, False),            # strict equality
    ({"path": "payload.flag", "equals": 1}, False),
    ({"path": "payload.nothing", "equals": None}, True),
    ({"path": "payload.absent", "equals": None}, False),
    ({"not_equals": "agent_message"}, True),
    ({"not_equals": "user_message"}, False),
    ({"in": ["a", "user_message"]}, True),
    ({"in": ["a", "b"]}, False),
    ({"not_in": ["user_message"]}, False),
    ({"not_in": ["x"]}, True),
    ({"path": "payload.message", "contains": "world"}, True),
    ({"path": "payload.n", "contains": "3"}, False),          # contains needs a string value
    ({"path": "payload.message", "not_contains": "world"}, False),
    ({"path": "payload.n", "not_contains": "3"}, True),
    ({"path": "payload.message", "exists": True}, True),
    ({"path": "payload.empty", "exists": True}, False),       # "" counts as absent
    ({"path": "payload.nothing", "exists": False}, True),
    ({"path": "payload.message", "regex": "^hel+o\\s"}, True),
    ({"path": "payload.flag", "regex": "^true$"}, True),      # regex tests the JavaScript string form
    ({"path": "payload.message", "regex": "(unclosed"}, False),
    ({"path": "payload.message", "exists": True, "not_contains": "world"}, False),   # every operator must pass
    ({"path": "payload.items[1].id", "equals": "second"}, True),
])
def test_match_rules(rule, expected):
    assert tr.matches_rule(ENTRY, rule, SCHEMA) is expected


def test_field_specs():
    watch = {"name": "w", "workspace": "/ws", "project": "proj", "extra": "x"}
    session = tr.SessionState("sid-1", "codex", cwd="/sess")
    ctx = {"watch": watch, "schema": {"name": "s", "version": "2"}, "session": session}
    resolve = lambda spec: tr.resolve_field_spec(spec, ENTRY, ctx)
    assert resolve("payload.message") == "hello world"
    assert resolve("$.payload.items[0].id") == "first"
    assert resolve("payload.missing") is tr.MISSING
    assert resolve("$watch.extra") == "x" and resolve("$schema.version") == "2"
    assert resolve("$session.sessionId") == "sid-1" and resolve("$session.cwd") == "/sess"
    assert resolve("$cwd") == "/ws" and resolve("$project") == "proj"
    assert tr.resolve_field_spec("$cwd", {"cwd": "/from-entry"}, {"watch": {}, "schema": {}}) == "/from-entry"
    assert resolve({"coalesce": ["payload.empty", "payload.nothing", "payload.message"]}) == "hello world"
    assert resolve({"path": "payload.empty", "default": "d"}) == "d"
    assert resolve({"path": "payload.missing", "value": "v", "default": "d"}) == "v"
    assert resolve({"value": None}) is None
    assert resolve({"coalesce": ["payload.missing"]}) is tr.MISSING
    fields = tr.resolve_fields({"msg": "payload.message", "n": {"path": "payload.n"}}, ENTRY, ctx)
    assert fields == {"msg": "hello world", "n": 3}


def test_session_id_resolution_prefers_fields_then_schema_then_path():
    proc = tr.TranscriptEventProcessor()
    watch, event = {"name": "w"}, {"name": "e", "action": "user_message"}
    assert proc._resolve_session_id({"sid": 42}, watch, {"sessionIdPath": "sid"}, event, "from-path") == "42"
    assert proc._resolve_session_id({}, watch, {"sessionIdPath": "sid"}, event, "from-path") == "from-path"
    with_field = {**event, "fields": {"sessionId": "payload.id"}}
    assert proc._resolve_session_id({"payload": {"id": "x"}}, watch, {}, with_field, "p") == "x"
    assert proc._resolve_session_id({}, watch, {}, event, None) is None

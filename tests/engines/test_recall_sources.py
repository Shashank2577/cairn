"""Non-JSONL session readers (Gemini CLI, Cline, OpenCode) wired into the transcript watcher."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cairn.engines.recall import sources
from cairn.engines.recall import transcripts as tr
from cairn.engines.recall.store import Store


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "userhome"))  # Path.home() on Windows
    monkeypatch.setenv("CAIRN_RECALL_NO_SPAWN", "1")
    for var in ("GEMINI_CLI_HOME", "OPENCODE_DB", "XDG_DATA_HOME", "APPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / ".cairn").mkdir(parents=True)
    return root


def rows(root: Path, sql: str) -> list[dict]:
    store = Store.open(root)
    try:
        return [dict(r) for r in store.db.execute(sql)]
    finally:
        store.close()


def watcher(tmp_path, *watches) -> tr.TranscriptWatcher:
    config = {"version": 1, "watches": list(watches)}
    return tr.TranscriptWatcher(config, tmp_path / "state.json")


def prompts(root: Path) -> list[str]:
    return [r["prompt_text"] for r in rows(root, "SELECT prompt_text FROM user_prompts ORDER BY id")]


# ---- Gemini ------------------------------------------------------------------------------------------------
def gemini_session(tmp_path, repo, messages, home=None) -> Path:
    project = (home or tmp_path / "userhome" / ".gemini" / "tmp") / "proj"
    (project / "chats").mkdir(parents=True, exist_ok=True)
    (project / ".project_root").write_text(str(repo) + "\n")
    path = project / "chats" / "session-1.json"
    path.write_text(json.dumps({"sessionId": "g-1", "startTime": "t", "lastUpdated": "t", "messages": messages}))
    return path


G_MSGS = [
    {"type": "user", "timestamp": "1", "content": [{"text": "add a login form"}]},
    {"type": "gemini", "timestamp": "2", "content": "Looking.",
     "thoughts": [{"subject": "Plan", "description": "read"}],
     "toolCalls": [{"id": "c1", "name": "read_file", "args": {"path": "a.py"},
                    "result": [{"functionResponse": {"response": {"output": "print(1)"}}}]}]},
    {"type": "gemini", "timestamp": "3", "content": "Done."},
]
GEMINI = {"name": "gemini", "source": "gemini"}


def test_gemini_ingests_once_and_appends(tmp_path, repo):
    path = gemini_session(tmp_path, repo, G_MSGS)
    w = watcher(tmp_path, GEMINI)
    w.poll()
    w.stop()
    assert prompts(repo) == ["add a login form"]
    counts = w.processor.counts
    assert counts["observations"] == 2   # reasoning + the read_file call
    assert counts["session_ends"] == 1
    # restart: nothing is ingested again
    w2 = watcher(tmp_path, GEMINI)
    w2.poll()
    w2.stop()
    assert w2.processor.counts["prompts"] == 0 and w2.processor.counts["observations"] == 0
    # a new message appears
    data = json.loads(path.read_text())
    data["messages"].append({"type": "user", "content": "now tests"})
    path.write_text(json.dumps(data))
    w3 = watcher(tmp_path, GEMINI)
    w3.poll()
    w3.stop()
    assert w3.processor.counts["prompts"] == 1
    assert prompts(repo)[-1] == "now tests"


def test_gemini_honours_home_env_and_skips_outside_repo(tmp_path, repo, monkeypatch):
    home = tmp_path / "gh"
    monkeypatch.setenv("GEMINI_CLI_HOME", str(home))
    gemini_session(tmp_path, repo, G_MSGS, home=home / ".gemini" / "tmp")
    assert any(str(home) in r for r in sources.gemini_roots())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    other = home / ".gemini" / "tmp" / "proj2"
    (other / "chats").mkdir(parents=True)
    (other / ".project_root").write_text(str(elsewhere))
    (other / "chats" / "s.json").write_text(json.dumps({"sessionId": "g-2", "messages": G_MSGS[:1]}))
    w = watcher(tmp_path, GEMINI)
    w.poll()
    w.stop()
    assert prompts(repo) == ["add a login form"]
    assert w.processor.counts["skipped_no_repo"] >= 1


def test_gemini_tool_result_arrives_later(tmp_path, repo):
    first = [G_MSGS[0], {"type": "gemini", "content": "", "toolCalls": [{"id": "c1", "name": "shell", "args": {}}]}]
    path = gemini_session(tmp_path, repo, first)
    w = watcher(tmp_path, GEMINI)
    w.poll()
    assert w.processor.counts["observations"] == 0
    first[1]["toolCalls"][0]["result"] = "ok"
    path.write_text(json.dumps({"sessionId": "g-1", "messages": first}))
    w.poll()
    w.stop()
    assert w.processor.counts["observations"] == 1


def test_gemini_start_at_end_and_corrupt_files(tmp_path, repo):
    gemini_session(tmp_path, repo, G_MSGS)
    bad = tmp_path / "userhome" / ".gemini" / "tmp" / "proj" / "chats" / "bad.json"
    bad.write_text("{not json")
    w = watcher(tmp_path, {**GEMINI, "startAtEnd": True})
    w.poll()
    w.stop()
    assert prompts(repo) == []


def test_gemini_clips_tool_output(tmp_path, repo):
    msgs = [G_MSGS[0], {"type": "gemini", "content": "x", "toolCalls": [
        {"id": "c1", "name": "shell", "args": {}, "result": "z" * 9000}]}]
    path = gemini_session(tmp_path, repo, msgs)
    _, _, parsed, _ = sources.parse_gemini(str(path))
    entries = sources._sync_messages({}, "s", "/x", parsed, True)
    tool = next(e for e in entries if e["kind"] == "tool")
    assert len(tool["toolResponse"]) < 4100


# ---- Cline -------------------------------------------------------------------------------------------------
CLINE = {"name": "cline", "source": "cline"}


def cline_task(tmp_path, repo, messages, wrap=False) -> Path:
    d = tmp_path / "userhome" / ".cline" / "data" / "sessions" / "task1"
    d.mkdir(parents=True)
    (d / "task1.json").write_text(json.dumps({"cwd": str(repo), "started_at": 1, "model": "m"}))
    path = d / "task1.messages.json"
    path.write_text(json.dumps({"messages": messages} if wrap else messages))
    return path


C_MSGS = [
    {"role": "user", "ts": 1, "content": [{"type": "text", "text": "<user_input mode=\"act\">fix bug</user_input>"}]},
    {"role": "assistant", "ts": 2, "content": [
        {"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "ok"},
        {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}}]},
    {"role": "user", "ts": 3, "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "data"}]},
    {"role": "assistant", "ts": 4, "content": "all done"},
]


@pytest.mark.parametrize("wrap", [False, True])
def test_cline_session(tmp_path, repo, wrap):
    cline_task(tmp_path, repo, C_MSGS, wrap)
    w = watcher(tmp_path, CLINE)
    w.poll()
    w.stop()
    assert prompts(repo) == ["fix bug"]
    assert w.processor.counts["observations"] == 2   # thinking + paired tool call
    w2 = watcher(tmp_path, CLINE)
    w2.poll()
    w2.stop()
    assert w2.processor.counts["observations"] == 0 and w2.processor.counts["prompts"] == 0


def test_cline_cwd_from_environment_details(tmp_path, repo):
    d = tmp_path / "tasks" / "abc"
    d.mkdir(parents=True)
    f = d / "api_conversation_history.json"
    env = f"<environment_details>\n# Current Working Directory ({repo}) Files\n</environment_details>"
    f.write_text(json.dumps([{"role": "user", "content": [
        {"type": "text", "text": "<task>do it</task>"}, {"type": "text", "text": env}]}]))
    parsed = sources.parse_cline(str(f))
    assert parsed and parsed[1] == str(repo) and parsed[0] == "abc"
    assert sources._sync_messages({}, parsed[0], parsed[1], parsed[2], parsed[3])[0]["text"] == "do it"


# ---- OpenCode ----------------------------------------------------------------------------------------------
OPENCODE = {"name": "opencode", "source": "opencode"}


def make_db(path: Path, repo: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE session(id TEXT PRIMARY KEY, title TEXT, directory TEXT, time_created INT, time_updated INT);
        CREATE TABLE message(id TEXT PRIMARY KEY, session_id TEXT, time_created INT, data TEXT);
        CREATE TABLE part(id TEXT PRIMARY KEY, message_id TEXT, time_created INT, data TEXT);""")
    conn.execute("INSERT INTO session VALUES('s1','t',?,1,1)", (str(repo),))
    return conn


def add(conn, mid, role, parts, t, extra=None):
    conn.execute("INSERT OR REPLACE INTO message VALUES(?,?,?,?)",
                 (mid, "s1", t, json.dumps({"role": role, **(extra or {})})))
    for i, (pid, data) in enumerate(parts):
        conn.execute("INSERT OR REPLACE INTO part VALUES(?,?,?,?)", (pid, mid, t + i, json.dumps(data)))
    conn.commit()


def test_opencode_incremental_and_idempotent(tmp_path, repo, monkeypatch):
    db = tmp_path / "oc.db"
    monkeypatch.setenv("OPENCODE_DB", str(db))
    conn = make_db(db, repo)
    add(conn, "m1", "user", [("p1", {"type": "text", "text": "build it"}),
                             ("p2", {"type": "text", "text": "synthetic!", "synthetic": True})], 1000)
    running = {"type": "tool", "tool": "bash", "callID": "c1", "state": {"status": "running", "input": {"cmd": "ls"}}}
    add(conn, "m2", "assistant", [
        ("p3", {"type": "reasoning", "text": "r" * 9000}), ("p4", running),
        ("p5", {"type": "text", "text": "partial", "time": {"start": 1}})], 2000,
        {"time": {"completed": 3}, "finish": "stop"})
    w = watcher(tmp_path, OPENCODE)
    w.poll()
    c = w.processor.counts
    assert prompts(repo) == ["build it"]
    assert c["observations"] == 1 and c["session_ends"] == 1     # reasoning only; the tool is still pending
    done = {"type": "tool", "tool": "bash", "callID": "c1",
            "state": {"status": "completed", "input": {"cmd": "ls"}, "output": "file.txt"}}
    add(conn, "m2", "assistant", [
        ("p3", {"type": "reasoning", "text": "r" * 9000}), ("p4", done),
        ("p5", {"type": "text", "text": "final", "time": {"start": 1, "end": 2}})], 2000,
        {"time": {"completed": 3}, "finish": "stop"})
    w.poll()
    w.stop()
    assert c["observations"] == 2
    assert c["session_ends"] == 1
    w2 = watcher(tmp_path, OPENCODE)
    w2.poll()
    w2.stop()
    assert sum(w2.processor.counts[k] for k in ("prompts", "observations", "session_ends")) == 0


def test_opencode_start_at_end_and_missing_or_corrupt(tmp_path, repo, monkeypatch):
    db = tmp_path / "oc.db"
    monkeypatch.setenv("OPENCODE_DB", str(db))
    assert watcher(tmp_path, OPENCODE).poll()["lines"] == 0   # no database yet
    conn = make_db(db, repo)
    add(conn, "m1", "user", [("p1", {"type": "text", "text": "old"})], 1000)
    w = watcher(tmp_path, {**OPENCODE, "startAtEnd": True})
    w.poll()
    assert prompts(repo) == []   # the first read of the database is recorded, not ingested
    add(conn, "m3", "user", [("p9", {"type": "text", "text": "new"})], 5000)
    w.poll()
    w.stop()
    assert prompts(repo) == ["new"]
    db.write_bytes(b"not a database")
    assert sources.poll_opencode({"name": "x", "path": str(db)}, {}, False) == []


def test_validate_config_accepts_source_watches():
    cfg = {"version": 1, "watches": [{"name": "g", "source": "gemini"}, {"name": "z", "source": "nope"}]}
    problems = tr.validate_config(cfg)
    assert len(problems) == 1 and "nope" in problems[0]

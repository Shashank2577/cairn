"""Session capture from other machines: the server's ingest, the client's outbox and push, the hook spawn."""
from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from cairn.engines.recall import api, cli, hooks, remote, schema
from cairn.engines.recall.store import Store

T0 = 1_750_000_000_000
ALICE = "/home/alice/shop"


def _repo(path: Path) -> Path:
    (path / ".cairn").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def rows(root: Path, sql: str, args=()):
    with Store.open(root) as st:
        return [dict(r) for r in st.db.execute(sql, args)]


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CAIRN_RECALL_NO_SPAWN", "1")
    monkeypatch.setenv("CAIRN_TOKEN", "tok")
    for var in ("CAIRN_INTERNAL", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def server_root(tmp_path) -> Path:
    return _repo(tmp_path / "server" / "team-shop")


@pytest.fixture()
def client(tmp_path) -> Path:
    return _repo(tmp_path / "alice" / "shop")


class Stub:
    """A team server endpoint that records into ``root`` through ``api.ingest_remote``."""

    def __init__(self, root: Path):
        self.root, self.token, self.url = root, "tok", ""
        self.requests: list[dict] = []
        self.fail_after_ingest: list[int] = []  # statuses answered after recording (a lost acknowledgement)
        self.max_events: int | None = None


@pytest.fixture()
def stub(server_root):
    state = Stub(server_root)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append({"path": self.path, "events": [e["id"] for e in body["events"]]})
            if self.headers.get("Authorization") != f"Bearer {state.token}":
                return self._send(401, {"error": "unauthorized"})
            if state.max_events and len(body["events"]) > state.max_events:
                return self._send(413, {"error": "too large"})
            try:
                res = api.ingest_remote(state.root, body)
            except api.IngestError as exc:
                return self._send(exc.status, {"error": str(exc)})
            if state.fail_after_ingest:
                return self._send(state.fail_after_ingest.pop(0), {"error": "flaky"})
            return self._send(200, res)

        def _send(self, code: int, obj: dict):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield state
    srv.shutdown()
    srv.server_close()


def connect(client: Path, url: str, project: str = "p1") -> None:
    (client / ".cairn" / "config.toml").write_text(f'[team]\nserver = "{url}"\nproject = "{project}"\n')


def capture(client: Path, event: str, sid: str = "s1", **payload):
    return hooks.run(event, {"session_id": sid, "cwd": str(client), **payload}, "claude-code")


def outbox(client: Path) -> list[dict]:
    return [{**r, "payload": json.loads(r["payload"])} for r in rows(client, "SELECT * FROM remote_outbox ORDER BY id")]


# ---- server: ingest_remote -------------------------------------------------------------------------------------
def _session_events() -> list:
    return [
        {"id": "c:1", "event": "session-init", "platform": "claude-code", "ts": T0,
         "payload": {"session_id": "s1", "cwd": ALICE, "prompt": "Fix the double charge", "branch": "main"}},
        {"id": "c:2", "event": "observation", "ts": T0 + 1000,
         "payload": {"session_id": "s1", "cwd": ALICE, "tool_name": "Bash", "tool_use_id": "t1",
                     "tool_input": {"command": "deploy API_TOKEN=abc123"}, "tool_response": {"stdout": "ok"}}},
        {"id": "c:3", "event": "observation", "ts": T0 + 1500,
         "payload": {"session_id": "s1", "cwd": ALICE, "tool_name": "TodoWrite", "tool_input": {}}},
        {"id": "c:4", "event": "summarize", "ts": T0 + 2000,
         "payload": {"session_id": "s1", "cwd": ALICE, "last_assistant_message": "Fixed.",
                     "transcript_path": "/etc/hosts"}},
        {"id": "c:5", "event": "session-end", "ts": T0 + 3000, "payload": {"session_id": "s1"}},
    ]


def test_ingest_remote_records_events_like_local_hooks(server_root):
    bad = ["junk",
           {"event": "context", "payload": {"session_id": "s1"}},
           {"event": "observation", "payload": {"cwd": ALICE, "tool_name": "Read"}},
           {"event": "observation", "platform": "Bad Platform!", "payload": {"session_id": "s1"}},
           {"event": "observation", "payload": {"session_id": "s1", "cwd": ALICE, "tool_name": "Read",
                                                "tool_input": {"blob": "x" * (remote.MAX_EVENT_BYTES + 1)}}},
           {"event": "session-init", "payload": {"session_id": {"not": "a string"}}}]
    res = api.ingest_remote(server_root, _session_events() + bad)
    assert res["received"] == 11
    assert (res["accepted"], res["skipped"], res["duplicates"]) == (4, 1, 0)  # TodoWrite is a skipped tool
    assert [r["index"] for r in res["rejected"]] == [5, 6, 7, 8, 9, 10]
    reasons = " | ".join(r["reason"] for r in res["rejected"])
    assert "unsupported event 'context'" in reasons and "session_id is required" in reasons
    assert "invalid platform" in reasons and "limit" in reasons

    [sess] = rows(server_root, "SELECT * FROM sdk_sessions")
    assert sess["project"] == "team-shop"  # the server's project, not the client's folder name
    assert (sess["cwd"], sess["branch"], sess["status"], sess["started_at_epoch"]) == (ALICE, "main", "completed", T0)
    assert (sess["content_session_id"], sess["pushed_by"]) == ("s1", None)  # no caller: ids used as sent
    assert rows(server_root, "SELECT prompt_number, prompt_text, created_at_epoch FROM user_prompts") == [
        {"prompt_number": 1, "prompt_text": "Fix the double charge", "created_at_epoch": T0}]
    queue = rows(server_root, "SELECT message_type, tool_input, last_assistant_message, created_at_epoch,"
                              " prompt_number FROM pending_messages ORDER BY id")
    assert [(q["message_type"], q["created_at_epoch"], q["prompt_number"]) for q in queue] == [
        ("observation", T0 + 1000, 1), ("summarize", T0 + 2000, 1)]
    assert "abc123" not in queue[0]["tool_input"] and "API_TOKEN=***" in queue[0]["tool_input"]
    assert queue[1]["last_assistant_message"] == "Fixed."

    again = api.ingest_remote(server_root, {"events": _session_events()})  # a batch sent twice
    assert (again["accepted"], again["duplicates"], again["rejected"]) == (0, 5, [])
    assert len(rows(server_root, "SELECT id FROM pending_messages")) == 2
    assert len(rows(server_root, "SELECT id FROM user_prompts")) == 1


def test_each_caller_gets_its_own_sessions(server_root):
    events = _session_events()
    first = api.ingest_remote(server_root, events[:4], caller="u1")  # u1's session, still open
    assert (first["accepted"], first["skipped"]) == (3, 1)
    # u2 reuses u1's session id and event ids: it can neither close u1's session nor shadow its events
    end = api.ingest_remote(server_root, [{**events[4], "id": "probe"}], caller="u2")
    assert (end["accepted"], end["skipped"], end["duplicates"]) == (0, 1, 0)
    second = api.ingest_remote(server_root, events, caller="u2")
    assert (second["accepted"], second["duplicates"]) == (4, 0)
    sessions = rows(server_root, "SELECT content_session_id, pushed_by, status FROM sdk_sessions ORDER BY id")
    assert sessions == [{"content_session_id": "u1:s1", "pushed_by": "u1", "status": "active"},
                        {"content_session_id": "u2:s1", "pushed_by": "u2", "status": "completed"}]
    assert len(rows(server_root, "SELECT id FROM user_prompts")) == 2
    assert len(rows(server_root, "SELECT id FROM pending_messages")) == 4
    repeat = api.ingest_remote(server_root, events[:4], caller="u1")  # the same caller again: duplicates
    assert (repeat["accepted"], repeat["duplicates"]) == (0, 4)
    assert len(rows(server_root, "SELECT id FROM user_prompts")) == 2
    assert api.session(server_root, "u1:s1")["session"]["pushed_by"] == "u1"
    assert {s["id"]: s["pushed_by"] for s in api.sessions(server_root)["items"]} == {"u1:s1": "u1", "u2:s1": "u2"}


def test_ingest_remote_rejects_unusable_requests(server_root):
    with pytest.raises(api.IngestError) as too_many:
        api.ingest_remote(server_root, [{}] * (remote.MAX_BATCH + 1))
    assert too_many.value.status == 413
    for body in ("nope", {"events": "nope"}, None):
        with pytest.raises(api.IngestError) as bad:
            api.ingest_remote(server_root, body)
        assert bad.value.status == 400


def test_ingest_remote_keeps_privacy_and_never_reads_the_server_disk(server_root, tmp_path):
    # a path that happens to exist on the server, with a git checkout on another branch
    elsewhere = tmp_path / "home" / "alice" / "shop"
    (elsewhere / ".git").mkdir(parents=True)
    (elsewhere / ".git" / "HEAD").write_text("ref: refs/heads/secret-branch\n")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {"content": "from the server disk"}}) + "\n")
    cwd = str(elsewhere)
    res = api.ingest_remote(server_root, [
        {"event": "session-init", "payload": {"session_id": "s9", "cwd": cwd, "prompt": remote.PRIVATE_PROMPT}},
        {"event": "observation", "payload": {"session_id": "s9", "cwd": cwd, "tool_name": "Read",
                                             "tool_input": {"file_path": "bank.txt"}}},
        {"event": "session-init", "payload": {"session_id": "s9", "cwd": cwd,
                                              "prompt": "Rotate <private>pin 1234</private> the keys"}},
        {"event": "summarize", "payload": {"session_id": "s9", "cwd": cwd, "transcript_path": str(transcript)}},
    ])
    assert (res["accepted"], res["skipped"]) == (2, 2)  # the private turn's work and the transcript-only stop
    [sess] = rows(server_root, "SELECT user_prompt, branch FROM sdk_sessions")
    assert sess == {"user_prompt": "Rotate  the keys", "branch": None}  # titled by the first non-private prompt
    assert [r["prompt_text"] for r in rows(server_root, "SELECT prompt_text FROM user_prompts ORDER BY id")] == [
        "", "Rotate  the keys"]
    assert rows(server_root, "SELECT id FROM pending_messages") == []


# ---- client: outbox --------------------------------------------------------------------------------------------
def test_hooks_fill_the_outbox_only_when_connected(client):
    capture(client, "session-init", prompt="Ship it")
    assert outbox(client) == []  # no [team] section: nothing leaves the machine
    connect(client, "https://cairn.example.com")
    capture(client, "session-init", prompt="Deploy with <private>hunter2</private> the key, password=abc123")
    capture(client, "observation", tool_name="Bash", tool_use_id="t1",
            tool_input={"command": "echo <private>secret</private> API_TOKEN=xyz"}, tool_response={"out": "y" * 50_000})
    capture(client, "observation", tool_name="Bash", tool_use_id="t1", tool_input={"command": "again"})  # duplicate
    capture(client, "observation", tool_name="TodoWrite", tool_input={})  # a skipped tool
    capture(client, "summarize", last_assistant_message="Deployed.")
    capture(client, "session-init", prompt="<private>my bank details</private>")
    capture(client, "session-end")
    box = outbox(client)
    assert [b["event"] for b in box] == ["session-init", "observation", "summarize", "session-init", "session-end"]
    assert all(b["platform"] == "claude-code" and b["payload"]["session_id"] == "s1" for b in box)
    text = json.dumps([b["payload"] for b in box])
    for secret in ("hunter2", "abc123", "secret", "xyz", "bank details"):
        assert secret not in text
    assert box[0]["payload"]["prompt"] == "Deploy with  the key, password=***"
    assert box[0]["payload"]["branch"]  # read from the client's checkout
    assert len(box[1]["payload"]["tool_response"]["out"]) < 25_000  # long output trimmed
    assert box[2]["payload"]["last_assistant_message"] == "Deployed."
    assert box[3]["payload"]["prompt"] == remote.PRIVATE_PROMPT
    assert "transcript_path" not in box[2]["payload"]


def test_summary_text_is_taken_from_the_local_transcript(client, tmp_path):
    connect(client, "https://cairn.example.com")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {"model": "m-1", "content": [
        {"type": "text", "text": "All green."}]}}) + "\n")
    capture(client, "session-init", prompt="Run the tests")
    capture(client, "summarize", transcript_path=str(transcript))
    payload = outbox(client)[-1]["payload"]
    assert payload["last_assistant_message"] == "All green." and payload["observed_model"] == "m-1"


# ---- client: push ----------------------------------------------------------------------------------------------
def _seven_events(client: Path) -> None:
    capture(client, "session-init", prompt="Fix the refund rounding")
    for i in range(4):
        capture(client, "observation", tool_name="Read", tool_use_id=f"t{i}", tool_input={"file_path": f"shop/f{i}.py"})
    capture(client, "summarize", last_assistant_message="Rounded half-even.")
    capture(client, "session-end")


def test_push_delivers_in_batches_and_moves_the_cursor(client, stub):
    connect(client, stub.url)
    _seven_events(client)
    res = remote.push(client, batch=3, backoff=0)
    assert res["ok"] and (res["pushed"], res["accepted"], res["requests"], res["pending"]) == (7, 7, 3, 0)
    assert [len(r["events"]) for r in stub.requests] == [3, 3, 1]
    assert all(r["path"] == "/api/p/p1/sessions/events" for r in stub.requests)
    assert outbox(client) == []
    srv = stub.root
    assert [r["prompt_text"] for r in rows(srv, "SELECT prompt_text FROM user_prompts")] == ["Fix the refund rounding"]
    assert [r["message_type"] for r in rows(srv, "SELECT message_type FROM pending_messages ORDER BY id")] == \
        ["observation"] * 4 + ["summarize"]
    assert rows(srv, "SELECT status FROM sdk_sessions")[0]["status"] == "completed"
    # the client's own store is untouched by pushing
    assert len(rows(client, "SELECT id FROM pending_messages")) == 5
    again = remote.push(client, backoff=0)
    assert again["ok"] and again["pushed"] == 0 and len(stub.requests) == 3
    assert remote.status(client)["last_push"]["ok"] is True


def test_a_lost_acknowledgement_is_retried_without_duplicates(client, stub):
    connect(client, stub.url)
    _seven_events(client)
    stub.fail_after_ingest = [502]  # recorded, but the reply never made it back
    res = remote.push(client, backoff=0)
    assert res["ok"] and res["requests"] == 1 and (res["accepted"], res["duplicates"]) == (0, 7)
    assert len(stub.requests) == 2 and stub.requests[0]["events"] == stub.requests[1]["events"]
    srv = stub.root
    assert len(rows(srv, "SELECT id FROM user_prompts")) == 1
    assert len(rows(srv, "SELECT id FROM pending_messages")) == 5


def test_push_failures_keep_the_events_and_back_off(client, stub, monkeypatch):
    connect(client, stub.url)
    _seven_events(client)
    stub.token = "another"
    res = remote.push(client, backoff=0)
    assert not res["ok"] and res["http_status"] == 401 and res["error"].startswith("HTTP 401")
    assert len(stub.requests) == 1  # an auth failure is not retried
    assert len(outbox(client)) == 7 and res["pending"] == 7
    st = remote.status(client)
    assert st["failures"] == 1 and st["retry_after"] and st["last_push"]["ok"] is False
    assert not remote.push_due(client)  # the hooks wait out the backoff ...
    stub.token = "tok"
    assert remote.push(client, backoff=0)["pushed"] == 7  # ... an explicit push does not
    assert remote.status(client)["failures"] == 0 and remote.push_due(client) is False

    capture(client, "session-init", prompt="One more")
    monkeypatch.delenv("CAIRN_TOKEN")
    res = remote.push(client, backoff=0)
    assert not res["ok"] and "CAIRN_TOKEN" in res["error"] and res["pending"] == 1
    connect(client, "http://127.0.0.1:9")  # nothing listens there
    res = remote.push(client, token="tok", attempts=2, backoff=0, timeout=2)
    assert not res["ok"] and "cannot reach" in res["error"] and res["pending"] == 1


def test_push_halves_a_batch_the_server_finds_too_large(client, stub):
    connect(client, stub.url)
    _seven_events(client)
    stub.max_events = 2
    res = remote.push(client, batch=5, backoff=0)
    assert res["ok"] and res["pushed"] == 7 and res["pending"] == 0
    assert max(len(r["events"]) for r in stub.requests if r["events"]) == 5  # the first, refused, request
    assert len(rows(stub.root, "SELECT id FROM pending_messages")) == 5


def test_push_needs_a_team_server(client):
    res = remote.push(client)
    assert not res["ok"] and "[team]" in res["error"]
    (client / ".cairn" / "config.toml").write_text('[team]\nserver = "ftp://example.com"\nproject = "p1"\n')
    assert remote.team_config(client) is None


def test_cli_push_and_status(client, stub, capsys):
    connect(client, stub.url)
    _seven_events(client)
    assert cli.main(["--root", str(client), "push"]) == 0
    assert "pushed 7 events (7 recorded" in capsys.readouterr().out
    assert cli.main(["--root", str(client), "push", "--status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["configured"] and status["pending"] == 0 and status["url"].endswith("/api/p/p1/sessions/events")
    stub.token = "another"
    capture(client, "session-init", prompt="Later")
    assert cli.main(["--root", str(client), "push"]) == 1
    assert "push failed: HTTP 401" in capsys.readouterr().err


# ---- the Stop/SessionEnd hooks start a push -------------------------------------------------------------------------
def test_stop_hook_spawns_a_push_only_when_connected(client, monkeypatch):
    spawned: list[list[str]] = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            spawned.append(argv)

    monkeypatch.setattr(hooks.subprocess, "Popen", FakePopen)
    monkeypatch.delenv("CAIRN_RECALL_NO_SPAWN")
    (client / ".cairn" / "config.toml").write_text("[recall]\nworker_spawn = false\n")

    def stop(sid: str = "s1") -> None:
        hooks.main(["summarize"], stdin_text=json.dumps({"session_id": sid, "cwd": str(client),
                                                         "last_assistant_message": "Done."}))

    capture(client, "session-init", prompt="Tidy the imports")
    stop()
    assert spawned == []  # not connected
    (client / ".cairn" / "config.toml").write_text(
        '[recall]\nworker_spawn = false\n\n[team]\nserver = "https://cairn.example.com"\nproject = "p1"\n')
    capture(client, "session-init", prompt="Tidy the tests")
    stop()
    assert len(spawned) == 1 and spawned[0][1:] == ["-m", "cairn.engines.recall.remote", "--root", str(client)]
    hooks.main(["session-end"], stdin_text=json.dumps({"session_id": "s1", "cwd": str(client)}))
    assert len(spawned) == 2
    monkeypatch.setenv("CAIRN_RECALL_NO_SPAWN", "1")
    stop()
    assert len(spawned) == 2
    monkeypatch.delenv("CAIRN_RECALL_NO_SPAWN")
    with Store.open(client) as st:  # backing off after a failed push
        st.set_kv(remote.RETRY_KEY, str(schema.now_ms() + 60_000))
    stop()
    assert len(spawned) == 2

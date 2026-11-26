"""Deleting captured sessions: one at a time, by age (retention) and "delete my data"; docs generation stays local."""
from __future__ import annotations

import json

import pytest

from cairn.engines.recall import api, viewer, worker
from cairn.engines.recall.store import Store
from cairn.engines.recall.tags import redact

DAY = 86_400_000


def _events(sid: str, ts: int) -> list[dict]:
    return [
        {"id": f"{sid}:1", "event": "session-init", "ts": ts,
         "payload": {"session_id": sid, "cwd": "/w", "prompt": f"work on {sid}"}},
        {"id": f"{sid}:2", "event": "observation", "ts": ts + 1000,
         "payload": {"session_id": sid, "cwd": "/w", "tool_name": "Bash", "tool_use_id": f"{sid}-t1",
                     "tool_input": {"command": "pytest"}, "tool_response": {"stdout": "ok"}}},
        {"id": f"{sid}:3", "event": "summarize", "ts": ts + 2000,
         "payload": {"session_id": sid, "cwd": "/w", "last_assistant_message": "done"}},
    ]


def count(root, table: str) -> int:
    with Store.open(root) as st:
        return st.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def ids(root) -> list[str]:
    with Store.open(root) as st:
        return [r[0] for r in st.db.execute("SELECT content_session_id FROM sdk_sessions ORDER BY id")]


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CAIRN_RECALL_NO_SPAWN", "1")
    repo = tmp_path / "repo"
    (repo / ".cairn").mkdir(parents=True)
    return repo


def test_deleting_a_session_removes_everything_recorded_for_it_and_nothing_else(root):
    from cairn.engines.recall import schema
    now = schema.now_ms()
    api.ingest_remote(root, _events("keep", now) + _events("gone", now))
    worker.run_once(root, router=worker._NoModel(), settings={"retention_days": 0})  # observations + summaries
    with Store.open(root) as st:
        st.db.execute("INSERT INTO remote_outbox(event, platform, payload, created_at_epoch) VALUES(?,?,?,?)",
                      ("observation", "claude-code", json.dumps({"session_id": "gone"}), now))
        st.db.execute("INSERT INTO remote_outbox(event, platform, payload, created_at_epoch) VALUES(?,?,?,?)",
                      ("observation", "claude-code", json.dumps({"session_id": "keep"}), now))
    before = {t: count(root, t) for t in ("observations", "session_summaries", "user_prompts", "tool_uses")}
    assert all(n >= 2 for n in before.values()), before

    res = viewer.delete_session(root, "gone")
    assert res["success"] and res["sessions"] == 1 and res["prompts"] == 1
    assert ids(root) == ["keep"]
    for table, n in before.items():
        assert count(root, table) == n // 2, table                       # exactly the other session's rows remain
    assert count(root, "remote_outbox") == 1                             # its unsent events never leave either
    assert viewer.delete_session(root, "gone") == {"success": False, "sessions": 0}


def test_retention_deletes_idle_sessions_but_never_hand_saved_memories(root):
    from cairn.engines.recall import schema
    now = schema.now_ms()
    api.ingest_remote(root, _events("old", now - 40 * DAY) + _events("recent", now - 2 * DAY))
    with Store.open(root) as st:
        mid = st.get_or_create_manual_session("repo")
        st.store_observation(mid, "repo", {"type": "decision", "title": "Use idempotency keys"})
        st.db.execute("UPDATE sdk_sessions SET started_at_epoch=? WHERE memory_session_id=?", (now - 400 * DAY, mid))

    assert viewer.purge(root, 30, dry_run=True) == {"dry_run": True, "sessions": 1}
    assert len(ids(root)) == 3                                           # a dry run deletes nothing
    assert viewer.apply_retention(root, {"retention_days": 0}) is None   # 0 keeps everything
    assert viewer.apply_retention(root, {"retention_days": 30})["sessions"] == 1
    remaining = ids(root)
    assert "old" not in remaining and "recent" in remaining
    assert any(s.startswith("manual-content-") for s in remaining)       # the hand-saved memory survives
    with pytest.raises(ValueError):
        viewer.purge(root, 0)


def test_delete_my_data_removes_only_the_callers_sessions(root):
    from cairn.engines.recall import schema
    now = schema.now_ms()
    api.ingest_remote(root, _events("s1", now), caller="alice")
    api.ingest_remote(root, _events("s1", now), caller="bob")
    api.ingest_remote(root, _events("local", now))                       # captured on the server itself
    assert viewer.delete_pushed_by(root, "alice")["sessions"] == 1
    assert ids(root) == ["bob:s1", "local"]
    assert viewer.delete_pushed_by(root, None)["sessions"] == 1          # local mode: this machine's captures
    assert ids(root) == ["bob:s1"]


def test_cairn_team_tokens_are_masked():
    token = "cairn_ab12cd34_" + "Zq9" * 13 + "x"
    assert len(token) == len("cairn_") + 8 + 1 + 40
    for text in (f"export CAIRN_TOKEN={token}", f"curl -H 'Authorization: Bearer {token}'", f"the token is {token}."):
        assert token not in redact(text), text
    assert redact("cairn_status is fine") == "cairn_status is fine"     # ordinary identifiers are left alone


def _team(cairn, tmp_path):
    from fastapi.testclient import TestClient

    from cairn.platform import Platform, ServerConfig
    from cairn.server import create_app
    config = ServerConfig(mode="team", allowed_hosts=("testserver", "127.0.0.1"))
    platform = Platform(home=tmp_path / "home", config=config)
    owner = platform.create_user("owner@example.com", "Owner", "correct horse battery")
    alice = platform.create_user("alice@example.com", "Alice", "correct horse battery")
    bob = platform.create_user("bob@example.com", "Bob", "correct horse battery")
    team = platform.create_team("Shop", owner_id=owner.id)
    platform.add_member(team.id, alice.id, "member")
    platform.add_member(team.id, bob.id, "member")
    proj = platform.register_local_project(cairn.project.root, team_id=team.id)
    tokens = {name: platform.issue_token(u.id, team.id, name, scopes=[scope])[1]
              for name, u, scope in (("owner", owner, "all"), ("alice", alice, "agent"), ("bob", bob, "agent"))}
    users = {"owner": owner.id, "alice": alice.id, "bob": bob.id}
    client = TestClient(create_app(platform=platform, config=config), base_url="http://127.0.0.1")
    client.platform = platform
    return client, proj.id, tokens, users


def test_team_server_session_delete_rules_and_audit(cairn, tmp_path):
    from cairn.engines.recall import schema
    client, pid, tokens, users = _team(cairn, tmp_path)
    root = cairn.project.root
    now = schema.now_ms()
    api.ingest_remote(root, _events("a1", now) + _events("a2", now), caller=users["alice"])
    api.ingest_remote(root, _events("b1", now), caller=users["bob"])
    auth = {k: {"authorization": f"Bearer {v}"} for k, v in tokens.items()}
    with client as c:
        bob_session = f"{users['bob']}:b1"
        r = c.delete(f"/api/p/{pid}/sessions/{bob_session}", headers=auth["alice"])
        assert r.status_code == 403                                      # a member cannot delete another's session
        r = c.delete(f"/api/p/{pid}/sessions/{users['alice']}:a1", headers=auth["alice"])
        assert r.status_code == 200 and r.json()["sessions"] == 1         # but can delete their own
        assert c.delete(f"/api/p/{pid}/sessions/nope", headers=auth["alice"]).status_code == 404
        r = c.delete(f"/api/p/{pid}/sessions/{bob_session}", headers=auth["owner"])
        assert r.status_code == 200                                      # an admin can
        api.ingest_remote(root, _events("b2", now), caller=users["bob"])
        r = c.delete(f"/api/p/{pid}/sessions/mine", headers=auth["bob"])
        assert r.status_code == 200 and r.json()["sessions"] == 1
        assert ids(root) == [f"{users['alice']}:a2"]                    # only alice's other session is left
    entries = client.platform.audit_entries(project_id=pid)
    actions = [e["action"] if isinstance(e, dict) else getattr(e, "action", "") for e in entries]
    assert actions.count("sessions.delete") == 2 and actions.count("sessions.delete_mine") == 1


def test_docs_generation_is_refused_on_a_team_server(cairn, tmp_path):
    client, pid, tokens, _ = _team(cairn, tmp_path)
    auth = {"authorization": f"Bearer {tokens['owner']}"}
    with client as c:
        status = c.get(f"/api/p/{pid}/docs/generate", headers=auth)
        assert status.status_code == 200 and status.json()["available"] is False
        r = c.post(f"/api/p/{pid}/docs/generate", json={"model": "haiku"}, headers=auth)
        assert r.status_code == 403 and "local mode" in r.json()["detail"]

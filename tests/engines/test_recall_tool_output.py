"""Tool-output measurement: how many bytes each tool put into the agent's context. Measurement only."""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from cairn.engines.recall import hooks, ingest, schema
from cairn.engines.recall.store import Store, tool_output_totals


@pytest.fixture()
def repo(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "shop"
    (root / ".cairn").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for k, v in {"CAIRN_HOME": str(tmp_path / "home"), "CAIRN_EMBEDDER": "hash", "CAIRN_NO_CLI_MODELS": "1",
                 "CAIRN_RECALL_NO_SPAWN": "1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CAIRN_INTERNAL", raising=False)
    return root


def send(repo, tool, resp, tid, **extra):
    return hooks.run("observation", {"session_id": "s1", "cwd": str(repo), "tool_name": tool, "tool_input": {"command": "x"},
                                     "tool_response": resp, "tool_use_id": tid, **extra})


def totals(repo):
    with Store.open(repo) as st:
        return st.tool_output_totals()


def test_each_tool_result_is_measured_in_bytes_before_any_trimming(repo):
    big = "é" * 200_000                       # 2 bytes each; far past what the tool-use index keeps
    send(repo, "Read", {"content": big}, "r1")
    send(repo, "Bash", "plain text result", "b1")
    send(repo, "Bash", {"stdout": "ok"}, "b2")
    t = totals(repo)
    assert t["calls"] == 3
    read = next(x for x in t["tools"] if x["tool"] == "Read")
    assert read["calls"] == 1 and read["bytes"] > 400_000                  # the result as the agent received it
    assert t["tools"][0]["tool"] == "Read"                                  # largest first
    bash = next(x for x in t["tools"] if x["tool"] == "Bash")
    assert bash["calls"] == 2 and bash["bytes"] == len(b"plain text result") + len(b'{"stdout": "ok"}')
    assert t["bytes"] == read["bytes"] + bash["bytes"]


def test_a_retried_hook_counts_once(repo):
    for _ in range(3):
        send(repo, "Grep", {"stdout": "hits"}, "same")
    assert totals(repo)["calls"] == 1


def test_calls_without_an_id_all_count(repo):
    for _ in range(2):
        hooks.run("observation", {"session_id": "s1", "cwd": str(repo), "tool_name": "Glob", "tool_response": {"files": "a"}})
    assert totals(repo)["calls"] >= 1


def test_cairns_own_answers_and_skipped_tools_are_not_counted(repo):
    send(repo, "mcp__cairn__cairn_context", {"result": "pack" * 100}, "c1")
    send(repo, "AskUserQuestion", {"answer": "y" * 500}, "q1")
    assert totals(repo) == {"calls": 0, "bytes": 0, "tools": []}


def test_private_prompts_and_missing_responses_are_handled(repo):
    hooks.run("session-init", {"session_id": "s1", "cwd": str(repo), "prompt": "<private>x</private>"})
    send(repo, "Read", {"content": "secret"}, "p1")
    assert totals(repo)["calls"] == 0
    hooks.run("session-init", {"session_id": "s1", "cwd": str(repo), "prompt": "do something real"})
    send(repo, "Bash", None, "n1")
    assert totals(repo)["tools"][0] == {"tool": "Bash", "calls": 1, "bytes": 0}


def test_nothing_is_blocked_or_changed(repo):
    res, out = send(repo, "Bash", {"stdout": "z" * 100_000}, "big")
    assert out == {} and res["_recorded"] == 1                              # queued exactly as before
    assert "permissionDecision" not in str(res) and "updatedInput" not in str(res)


def test_response_size_of_odd_values():
    assert ingest.response_size(None) == 0 and ingest.response_size("abc") == 3
    assert ingest.response_size({"a": "é"}) == len('{"a": "é"}'.encode()) and ingest.response_size(object()) > 0


def test_a_store_from_before_the_measurement_opens_and_reports_zero(tmp_path):
    path = tmp_path / "old.db"
    db = schema.connect(path)
    db.execute("DROP TABLE tool_output_sizes")
    db.execute("DELETE FROM schema_versions WHERE version = 7")
    assert tool_output_totals(db) == {"calls": 0, "bytes": 0, "tools": []}   # no table yet: zeros, no error
    db.close()
    db = schema.connect(path)                                                # opening again applies the migration
    assert schema.schema_versions(db)[-1] == 7 and tool_output_totals(db)["calls"] == 0
    db.close()
    ro = schema.connect(path, readonly=True)
    assert tool_output_totals(ro)["calls"] == 0
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("DELETE FROM tool_output_sizes")


def test_overview_reports_bytes_beside_the_savings(cairn):
    from cairn.engines.recall import hooks as rh
    root = cairn.project.root
    assert cairn.tool_output() == {"calls": 0, "bytes": 0, "tools": []}
    rh.run("session-init", {"session_id": "s9", "cwd": str(root), "prompt": "Look at the payments"})
    rh.run("observation", {"session_id": "s9", "cwd": str(root), "tool_name": "Read", "tool_input": {"file_path": "a"},
                           "tool_response": {"content": "x" * 5000}, "tool_use_id": "t9"})
    out = cairn.tool_output()
    assert out["calls"] == 1 and out["bytes"] > 5000 and out["tools"][0]["tool"] == "Read"


def test_the_task_list_is_indexed_for_resume_but_not_observed(repo):
    send(repo, "TodoWrite", {"ok": 1}, "td1")
    with Store.open(repo) as st:
        assert st.db.execute("SELECT COUNT(*) FROM tool_uses WHERE tool_name='TodoWrite'").fetchone()[0] == 1
        assert st.db.execute("SELECT COUNT(*) FROM pending_messages WHERE tool_name='TodoWrite'").fetchone()[0] == 0

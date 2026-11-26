"""Richer deterministic events: rejected approaches, error-then-fix pairs, retry loops and user corrections.

Detection is by fixed rules on explicit signals only, so most of this file is false-positive fixtures: ordinary
output that merely contains the word "error", a shell's own "Permission denied", polite prompts that start with
"no" or "don't", and repeated reads."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cairn.engines.recall import fallback, hooks, sqlsearch, worker
from cairn.engines.recall.store import Store


def call(tool, inp=None, resp=None, tid=None):
    return {"message_type": "observation", "tool_name": tool, "tool_input": json.dumps(inp or {}),
            "tool_response": json.dumps(resp) if resp is not None else None, "cwd": "/r", "tool_use_id": tid}


def bash(cmd, resp=None, tid=None):
    return call("Bash", {"command": cmd}, resp if resp is not None else {"stdout": "ok", "stderr": ""}, tid)


FAIL = {"error": "Exit code 1\nFAILED tests/test_a.py::test_x - assert 1 == 2"}
DENY = {"error": "The user doesn't want to proceed with this tool use. The tool use was rejected."}


# ---- failure and denial signals --------------------------------------------------------------------------
@pytest.mark.parametrize("resp", [
    {"is_error": True, "content": "boom"}, {"isError": True}, {"error": "command not found: foo"},
    {"stdout": "", "exit_code": 2}, {"stdout": "x", "returncode": 1}, {"metadata": {"exit_code": 127}, "output": "no"},
    "Exit code 1\nFAILED test", "Error: file not found", "<tool_use_error>File has not been read yet</tool_use_error>",
])
def test_explicit_failure_signals_are_errors(resp):
    assert fallback.tool_error(resp) is not None


@pytest.mark.parametrize("resp", [
    {"stdout": "grep: 3 matches for 'error' in logs/app.log\nerror: disk quota\nERROR handled", "stderr": ""},
    {"stdout": "tests/test_error_paths.py ... passed", "exit_code": 0},
    {"stdout": "ok", "stderr": "DeprecationWarning: x is deprecated"},          # a warning is not a failure
    {"content": "def handle_error(e): raise Error('x')"},                       # source text mentioning Error
    {"error": None}, {"error": ""}, {"is_error": False},
    "All 3 tests passed. Exit code 0", "Wrote 12 lines; error handling added", "", None,
    {"stdout": "Exit code: 1 is what the script prints on bad input"},          # inside ordinary output
])
def test_ordinary_output_is_not_an_error(resp):
    assert fallback.tool_error(resp) is None


@pytest.mark.parametrize("resp", [DENY, {"permissionDecision": "deny"}, {"hookSpecificOutput": {"permissionDecision": "deny"}},
                                  "The user doesn't want to proceed with this tool use.",
                                  {"error": "Permission to use Bash has been denied."}])
def test_user_refusals_are_denials(resp):
    assert fallback.tool_denied(resp)


@pytest.mark.parametrize("resp", [{"stderr": "rm: build: Permission denied", "exit_code": 1},   # the OS, not the person
                                  "bash: ./run.sh: Permission denied", {"stdout": "access denied by firewall rule 7"},
                                  {"error": "401 unauthorized"}, None, {"stdout": "ok"}])
def test_shell_permission_errors_are_not_denials(resp):
    assert not fallback.tool_denied(resp)


# ---- rejected approaches -----------------------------------------------------------------------------------
def test_a_denied_call_is_a_rejected_approach_and_not_also_an_error():
    evs = fallback.turn_events([bash("rm -rf build", DENY, "t1")], "/r")
    assert [e["event"] for e in evs] == ["rejected"]
    assert "rm -rf build" in evs[0]["title"] and "do not repeat" in evs[0]["narrative"]


def test_rejected_edit_names_the_file():
    evs = fallback.turn_events([call("Edit", {"file_path": "/r/src/a.py"}, DENY, "t1")], "/r")
    assert evs[0]["files_read"] == ["src/a.py"]


# ---- error then fix ------------------------------------------------------------------------------------------
def test_error_then_fix_after_an_edit():
    evs = fallback.turn_events([
        bash("pytest tests/test_a.py", FAIL, "t1"),
        call("Edit", {"file_path": "/r/src/a.py"}, {"filePath": "/r/src/a.py"}, "t2"),
        bash("pytest tests/test_a.py", None, "t3")], "/r")
    [ev] = evs
    assert ev["event"] == "error-fix" and ev["files_modified"] == ["src/a.py"]
    assert "FAILED tests/test_a.py::test_x" in ev["narrative"] and "src/a.py" in ev["narrative"]


def test_error_then_fix_with_a_changed_command():
    evs = fallback.turn_events([bash("pytest tests/test_a.py", FAIL, "t1"), bash("pytest tests/test_a.py -x -q", None, "t2")], "/r")
    assert [e["event"] for e in evs] == ["error-fix"] and "changed command" in evs[0]["narrative"]


def test_a_plain_retry_that_passes_teaches_nothing():
    assert fallback.turn_events([bash("npm test", FAIL, "t1"), bash("npm test", None, "t2")], "/r") == []


def test_an_error_nobody_fixed_is_not_a_pair():
    assert fallback.turn_events([bash("pytest", FAIL, "t1"), call("Edit", {"file_path": "/r/a.py"}, {"ok": 1}, "t2")], "/r") == []


def test_a_success_of_another_command_does_not_close_the_error():
    cs = [bash("pytest tests/test_a.py", FAIL, "t1"), call("Edit", {"file_path": "/r/a.py"}, {"ok": 1}, "t2"),
          bash("ls -la", None, "t3")]
    assert fallback.turn_events(cs, "/r") == []


def test_secrets_in_the_failing_command_are_masked():
    [ev] = fallback.turn_events([bash("API_TOKEN=abc123xyz deploy.sh", FAIL, "t1"),
                                 bash("API_TOKEN=abc123xyz deploy.sh --retry", None, "t2")], "/r")
    assert "abc123xyz" not in json.dumps(ev)


# ---- retry loops ------------------------------------------------------------------------------------------------
def test_three_attempts_with_two_failures_is_a_retry_loop():
    evs = fallback.turn_events([bash("make build", FAIL, "t1"), bash("make build", FAIL, "t2"),
                                bash("make build -j4", None, "t3")], "/r")
    [ev] = [e for e in evs if e["event"] == "retry-loop"]  # the changed command that finally worked is a fix, too
    assert "error-fix" in {e["event"] for e in evs} and "3 times" in ev["narrative"] and "failed 2 times" in ev["narrative"]


def test_the_same_command_run_three_times_successfully_is_not_a_loop():
    assert fallback.turn_events([bash("git status", None, f"t{i}") for i in range(4)], "/r") == []


def test_one_failure_in_three_is_not_a_loop():
    assert fallback.turn_events([bash("make", FAIL, "t1"), bash("make", None, "t2"), bash("make", None, "t3")], "/r") == []


def test_three_reads_of_one_file_or_one_search_are_not_a_loop():
    reads = [call("Read", {"file_path": "/r/a.py"}, {"error": "File too large"}, f"t{i}") for i in range(3)]
    assert [e for e in fallback.turn_events(reads, "/r") if e["event"] == "retry-loop"] == []
    greps = [call("Grep", {"pattern": "x"}, {"is_error": True}, f"g{i}") for i in range(3)]
    assert [e for e in fallback.turn_events(greps, "/r") if e["event"] == "retry-loop"] == []


def test_three_different_files_with_the_same_tool_are_not_a_loop():
    cs = [call("Edit", {"file_path": f"/r/f{i}.py"}, {"error": "String not found"}, f"t{i}") for i in range(3)]
    assert fallback.turn_events(cs, "/r") == []


def test_a_failing_edit_attempted_three_times_is_a_loop():
    cs = [call("Edit", {"file_path": "/r/a.py"}, {"error": "String to replace not found"}, f"t{i}") for i in range(3)]
    assert [e["event"] for e in fallback.turn_events(cs, "/r")] == ["retry-loop"]


def test_event_keys_are_stable_when_a_loop_grows():
    two = [bash("make", FAIL, "t1"), bash("make", FAIL, "t2"), bash("make", FAIL, "t3")]
    more = two + [bash("make", FAIL, "t4")]
    assert fallback.turn_events(two, "/r")[0]["key"] == fallback.turn_events(more, "/r")[0]["key"]


def test_cairns_own_tools_are_never_events():
    assert fallback.turn_events([call("mcp__cairn__cairn_context", {}, FAIL, f"t{i}") for i in range(4)], "/r") == []


# ---- user corrections --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("prompt", [
    "No, that's the wrong file. Use shop/gateway.py", "Wrong. The retry belongs in the gateway", "Stop. You broke the build",
    "Don't touch the migration files", "that's not what I asked for", "Revert that please", "undo the last change",
    "Why did you rename the function?", "You shouldn't have deleted the fixture",
    "please look again: that's wrong, the limit is 5", "Not that one, the other service",
])
def test_corrections(prompt):
    ev = fallback.correction_event(prompt, 3, ["shop/payments.py"])
    assert ev and ev["event"] == "correction" and "shop/payments.py" in ev["narrative"]


@pytest.mark.parametrize("prompt", [
    "No problem, thanks!", "no worries, take your time", "No need to run the whole suite", "Don't forget to add a test",
    "don't worry about lint for now", "Never mind, I found it", "Stop by the docs and summarise them",
    "Now add a retry to the gateway", "Great, now do the same for refunds", "Can you explain why the lock is needed?",
    "Add a note that we do not support Windows paths here", "Nothing else to change", "Normalize the config keys",
    "x" * 700,                                                       # a long message is a request
])
def test_polite_and_ordinary_prompts_are_not_corrections(prompt):
    assert fallback.correction_event(prompt, 3) is None


def test_the_first_prompt_cannot_be_a_correction():
    assert fallback.correction_event("No, use the gateway", 1) is None
    assert fallback.correction_event("No, use the gateway", None) is None


def test_correction_masks_secrets():
    ev = fallback.correction_event("No, the key is API_TOKEN=abc123xyz not that one", 2)
    assert ev and "abc123xyz" not in json.dumps(ev)


# ---- stored and searched -------------------------------------------------------------------------------------------
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


class NoModel:
    available = False


def send(repo, event, **p):
    return hooks.run(event, {"session_id": "s1", "cwd": str(repo), **p})


def rows(repo, sql, args=()):
    with Store.open(repo) as st:
        return [dict(r) for r in st.db.execute(sql, args)]


def play(repo):
    send(repo, "session-init", prompt="Make the checkout tests pass")
    send(repo, "observation", tool_name="Bash", tool_input={"command": "pytest tests/test_checkout.py"},
         tool_response={"error": "Exit code 1\nFAILED tests/test_checkout.py::test_total"}, tool_use_id="a1")
    send(repo, "observation", tool_name="Edit", tool_input={"file_path": str(repo / "shop/cart.py")},
         tool_response={"filePath": "x"}, tool_use_id="a2")
    send(repo, "observation", tool_name="Bash", tool_input={"command": "pytest tests/test_checkout.py"},
         tool_response={"stdout": "1 passed"}, tool_use_id="a3")
    send(repo, "observation", tool_name="Bash", tool_input={"command": "git push --force origin main"},
         tool_response=DENY, tool_use_id="a4")
    send(repo, "summarize", last_assistant_message="Tests pass.")
    send(repo, "session-init", prompt="No, that's wrong: never force push to main")
    send(repo, "observation", tool_name="Read", tool_input={"file_path": str(repo / "shop/cart.py")},
         tool_response={"content": "x"}, tool_use_id="b1")
    send(repo, "summarize", last_assistant_message="Understood.")


def events(repo):
    return {json.loads(r["metadata"])["event"]: r for r in rows(repo, "SELECT * FROM observations")
            if '"event"' in (r["metadata"] or "")}


def test_the_worker_stores_each_event_once_without_a_model(repo):
    play(repo)
    worker.run_once(repo, router=NoModel())
    worker.run_once(repo, router=NoModel())
    found = events(repo)
    assert set(found) == {"rejected", "error-fix", "correction"}
    assert found["rejected"]["type"] == "decision" and found["error-fix"]["type"] == "bugfix"
    assert found["error-fix"]["prompt_number"] == 1 and found["correction"]["prompt_number"] == 2
    assert json.loads(found["error-fix"]["files_modified"]) == ["shop/cart.py"]
    # the turn's own observation is unchanged: one per prompt, still the one later events merge into
    plain = [r for r in rows(repo, "SELECT * FROM observations WHERE prompt_number=1") if '"event"' not in r["metadata"]]
    assert len(plain) == 1 and json.loads(plain[0]["metadata"])["derived"] is True
    assert len(rows(repo, "SELECT id FROM observations")) == 5


def test_events_are_found_by_searching_for_what_happened(repo):
    play(repo)
    worker.run_once(repo, router=NoModel())
    with Store.open(repo) as st:
        titles = lambda q: [o["title"] for o in sqlsearch.search_observations(st.db, q, limit=10, project="shop", loose=True)]  # noqa: E731
        assert any("Rejected by the user" in t for t in titles("rejected approach force push"))
        assert any("Fixed after an error" in t for t in titles("error then fix pytest"))
        assert any("User correction" in t for t in titles("user correction never force push"))


def test_a_loop_split_across_two_worker_runs_is_still_found_and_stored_once(repo):
    send(repo, "session-init", prompt="Build it")
    for i in range(2):
        send(repo, "observation", tool_name="Bash", tool_input={"command": "make all"},
             tool_response={"error": "Exit code 2"}, tool_use_id=f"m{i}")
    worker.run_once(repo, router=NoModel())
    assert "retry-loop" not in events(repo)
    send(repo, "observation", tool_name="Bash", tool_input={"command": "make all"}, tool_response={"error": "Exit code 2"},
         tool_use_id="m2")
    worker.run_once(repo, router=NoModel())
    assert "retry-loop" in events(repo)
    send(repo, "observation", tool_name="Bash", tool_input={"command": "make all"}, tool_response={"error": "Exit code 2"},
         tool_use_id="m3")
    worker.run_once(repo, router=NoModel())
    loops = [r for r in rows(repo, "SELECT * FROM observations") if '"retry-loop"' in (r["metadata"] or "")]
    assert len(loops) == 1 and "4 times" in loops[0]["narrative"]


def test_a_quiet_session_stores_no_events(repo):
    send(repo, "session-init", prompt="Explain the gateway")
    send(repo, "observation", tool_name="Grep", tool_input={"pattern": "error"},
         tool_response={"stdout": "gateway.py: raise PaymentError  # error path"}, tool_use_id="q1")
    send(repo, "observation", tool_name="Read", tool_input={"file_path": str(repo / "shop/gateway.py")},
         tool_response={"content": "error = None"}, tool_use_id="q2")
    send(repo, "summarize", last_assistant_message="It charges.")
    send(repo, "session-init", prompt="Great, now no need to change anything; don't forget the docs")
    send(repo, "summarize", last_assistant_message="Done.")
    worker.run_once(repo, router=NoModel())
    assert events(repo) == {}


def test_claudes_tool_failure_event_arrives_as_an_error_response(repo):
    send(repo, "session-init", prompt="Run it")
    _, _ = hooks.run("observation", {"session_id": "s1", "cwd": str(repo), "tool_name": "Bash", "tool_use_id": "f1",
                                     "tool_input": {"command": "make"}, "error": "Exit code 2\nboom", "hook_event_name":
                                     "PostToolUseFailure"})
    [queued] = rows(repo, "SELECT tool_response FROM pending_messages WHERE tool_name='Bash'")
    assert fallback.tool_error(json.loads(queued["tool_response"])) == "boom"
    _, _ = hooks.run("observation", {"session_id": "s1", "cwd": str(repo), "tool_name": "Bash", "tool_use_id": "f2",
                                     "tool_input": {"command": "sleep 99"}, "error": "interrupted", "is_interrupt": True})
    last = rows(repo, "SELECT tool_response FROM pending_messages WHERE tool_use_id='f2'")[0]
    assert fallback.tool_error(json.loads(last["tool_response"])) is None  # pressing Esc is not a failure


class ScriptedModel:
    """A model that answers every observation with a fixed note (what a signed-in model does)."""
    available = True
    provider = "scripted"

    def complete(self, task, prompt, **kw):
        if task == "recall_summarize":
            return "<summary><request>r</request><investigated>i</investigated><learned>l</learned>" \
                   "<completed>c</completed><next_steps>n</next_steps></summary>"
        return "<observation><type>change</type><title>Model note</title><narrative>The model wrote this.</narrative>" \
               "<facts><fact>f</fact></facts><concepts><concept>what-changed</concept></concepts></observation>"

    def model(self, tier):
        return f"model-{tier}"

    def tier_for(self, task, input_tokens=0):
        return "fast"


def test_events_are_found_when_a_model_writes_the_observations_too(repo):
    play(repo)
    stats = worker.run_once(repo, router=ScriptedModel())
    assert stats["model"]
    found = events(repo)
    assert set(found) == {"rejected", "error-fix", "correction"}
    assert any(r["title"] == "Model note" for r in rows(repo, "SELECT title FROM observations"))
    worker.run_once(repo, router=ScriptedModel())
    assert len(events(repo)) == 3 and len([r for r in rows(repo, "SELECT id FROM observations WHERE metadata LIKE '%\"event\"%'")]) == 3

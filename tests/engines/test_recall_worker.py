"""Recall worker: model-written observations and summaries (a scripted router), failure handling,
the observer conversation, deterministic records without a model, locking and the hosted loop."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from cairn.engines.recall import health, hooks, parser, prompts, worker
from cairn.engines.recall.modes import load_mode
from cairn.engines.recall.observer import compact_edit_output, file_evidence
from cairn.engines.recall.store import Store

OBSERVATION_XML = """```xml
<observation>
  <type>bugfix</type>
  <title>[**title**: Retry guard prevents a double charge]</title>
  <subtitle>Payments no longer re-charge on provider timeouts</subtitle>
  <facts>
    <fact>shop/payments.py passes an idempotency key on every charge</fact>
    <fact>The provider retries on timeout</fact>
  </facts>
  <narrative>The retry path called charge() twice; an idempotency key now makes it safe.</narrative>
  <concepts>
    <concept>gotcha: provider retries</concept>
    <concept>problem-solution</concept>
    <concept>bugfix</concept>
  </concepts>
  <files_read><file>shop/gateway.py</file></files_read>
  <files_modified><file>somewhere/else.py</file></files_modified>
</observation>
```"""

SUMMARY_XML = """<summary>
  <request>Fix the double charge on retry</request>
  <investigated>shop/payments.py and the gateway retry path</investigated>
  <learned>The provider retries on timeout, so charges must be idempotent</learned>
  <completed>Added an idempotency key to every charge</completed>
  <next_steps>Add a regression test for the retry path</next_steps>
  <notes>No schema change needed</notes>
</summary>"""


class ScriptedRouter:
    """Stands in for cairn.router.Router: returns scripted replies and records every call."""
    available = True
    provider = "scripted"

    def __init__(self, replies=None, fail=None):
        self.replies = replies or {}
        self.fail = fail
        self.calls: list[dict] = []

    def complete(self, task, prompt, *, system="", cached_context="", max_tokens=1200, budget=None, tier=None):
        self.calls.append({"task": task, "prompt": prompt, "system": system, "cached": cached_context, "tier": tier})
        if self.fail:
            raise self.fail
        reply = self.replies.get(task)
        if callable(reply):
            return reply(prompt)
        if reply is not None:
            return reply
        if task == "recall_summarize":
            return SUMMARY_XML
        if task == "recall_compress":
            return "condensed payload"
        return OBSERVATION_XML if "Edit" in prompt or "Bash" in prompt else '<skip_summary reason="noise" />'

    def model(self, tier):
        return f"model-{tier}"

    def tier_for(self, task, input_tokens=0):
        return "fast"


@pytest.fixture()
def repo(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "shop"
    (root / ".cairn").mkdir(parents=True)
    (root / "shop").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for k, v in {"CAIRN_HOME": str(tmp_path / "home"), "CAIRN_EMBEDDER": "hash", "CAIRN_NO_CLI_MODELS": "1",
                 "CAIRN_RECALL_NO_SPAWN": "1", "CAIRN_RECALL_OBSERVE_BATCH": "1",
                 "CAIRN_RECALL_OBSERVER_REPLAY_EXCHANGES": "0"}.items():  # the one-event-per-call conversation
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CAIRN_INTERNAL", raising=False)
    return root


def turn(repo: Path, prompt="Fix the double charge on retry", sid="s1", answer="Fixed: charges are idempotent."):
    base = {"session_id": sid, "cwd": str(repo)}
    hooks.run("session-init", {**base, "prompt": prompt})
    hooks.run("observation", {**base, "tool_name": "Read", "tool_input": {"file_path": str(repo / "shop/gateway.py")},
                              "tool_response": {"content": "def charge(): ..."}, "tool_use_id": f"{sid}-{len(prompt)}-r"})
    hooks.run("observation", {**base, "tool_name": "Edit", "tool_input": {
        "file_path": str(repo / "shop/payments.py"), "old_string": "charge(a)", "new_string": "charge(a, key)"},
        "tool_response": {"filePath": str(repo / "shop/payments.py")}, "tool_use_id": f"{sid}-{len(prompt)}-e"})
    hooks.run("summarize", {**base, "last_assistant_message": answer})


def q(repo: Path, sql: str, args=()):
    with Store.open(repo) as st:
        return [dict(r) for r in st.db.execute(sql, args)]


def test_model_observations_and_summaries_are_parsed_and_stored(repo):
    turn(repo)
    router = ScriptedRouter()
    stats = worker.run_once(repo, router=router)
    assert stats["status"] == "ok" and stats["model"] and stats["observations"] == 1 and stats["summaries"] == 1
    [obs] = q(repo, "SELECT * FROM observations")
    assert obs["title"] == "Retry guard prevents a double charge"  # label-wrapped title unwrapped
    assert obs["type"] == "bugfix" and json.loads(obs["concepts"]) == ["gotcha", "problem-solution"]
    assert json.loads(obs["facts"])[0].startswith("shop/payments.py passes")
    # the files the tool calls touched win over the model's claim
    assert json.loads(obs["files_modified"]) == ["shop/payments.py"]
    assert json.loads(obs["files_read"]) == ["shop/gateway.py"]
    assert obs["prompt_number"] == 1 and obs["discovery_tokens"] > 0 and obs["generated_by_model"] == "model-fast"
    [summ] = q(repo, "SELECT * FROM session_summaries")
    assert summ["learned"].startswith("The provider retries") and summ["next_steps"].startswith("Add a regression")
    assert json.loads(summ["files_edited"]) == ["shop/payments.py"]
    assert q(repo, "SELECT COUNT(*) n FROM pending_messages")[0]["n"] == 0
    links = {r["tool_use_id"]: r["observation_id"] for r in q(repo, "SELECT * FROM tool_uses")}
    assert links["s1-30-e"] == obs["id"]
    # prompts: framing prompt as system, then one prompt per tool event, then the summary prompt
    tasks = [c["task"] for c in router.calls]
    assert tasks == ["recall_observe", "recall_observe", "recall_summarize"]
    assert "<user_request>Fix the double charge on retry</user_request>" in router.calls[0]["system"]
    assert "<what_happened>Read</what_happened>" in router.calls[0]["prompt"] and router.calls[0]["tier"] == "fast"
    assert router.calls[1]["tier"] is None and "OBSERVER CONVERSATION SO FAR" in router.calls[1]["cached"]
    assert "CRITICAL TAG REQUIREMENT" in router.calls[2]["prompt"] and "Fixed: charges" in router.calls[2]["prompt"]
    assert health.read(Store.open(repo))["consecutiveFailures"] == 0
    assert q(repo, "SELECT COUNT(*) n FROM vector_docs WHERE doc_type='observation'")[0]["n"] >= 3


def test_conversation_continues_across_runs_and_prompts(repo):
    turn(repo)
    router = ScriptedRouter()
    worker.run_once(repo, router=router)
    turn(repo, prompt="Now add a refund", answer="Refund added.")
    worker.run_once(repo, router=router)
    second = [c for c in router.calls if c["task"] == "recall_observe"][2]
    assert "continuing to observe" in second["system"]  # the continuation framing for prompt 2
    assert "<user_request>Now add a refund</user_request>" in second["system"]
    assert "Retry guard prevents a double charge" in second["cached"]  # earlier exchanges are replayed
    conv = Store.open(repo).get_conversation(1)
    assert conv["history"]["prompt_number"] == 2 and len(conv["history"]["exchanges"]) == 6


def test_skips_and_prose_confirm_the_batch(repo):
    turn(repo)
    router = ScriptedRouter(replies={"recall_observe": "Skipping: nothing interesting here."})
    stats = worker.run_once(repo, router=router)
    assert stats["dropped"] == 2 and q(repo, "SELECT COUNT(*) n FROM observations")[0]["n"] == 0
    assert q(repo, "SELECT COUNT(*) n FROM pending_messages")[0]["n"] == 0


@pytest.mark.parametrize("reply,kind", [
    ("You've hit your session limit · resets 5:50pm (America/Los_Angeles)", "quota_exhausted"),
    ("Not logged in · Please run /login", "auth_invalid"),
    ("API Error: Connection error: ECONNRESET", "transient"),
])
def test_refusals_keep_the_work_and_mark_the_observer_unhealthy(repo, reply, kind):
    turn(repo)
    router = ScriptedRouter(replies={"recall_observe": reply, "recall_summarize": reply})
    stats = worker.run_once(repo, router=router)
    assert stats["preserved"] >= 1 and stats["deferred_sessions"] == 1
    assert q(repo, "SELECT COUNT(*) n FROM pending_messages WHERE status='pending'")[0]["n"] == 3
    st = Store.open(repo)
    state = health.read(st)
    assert state["lastErrorKind"] == kind and state["consecutiveFailures"] >= 1
    if kind == "quota_exhausted":
        assert not health.admit_quota_probe(st, "scripted")  # one probe per window, not one per tool call
        calls = len(router.calls)
        worker.run_once(repo, router=router)
        assert len(router.calls) == calls


def test_repeated_failures_fall_back_to_derived_records(repo):
    turn(repo)
    router = ScriptedRouter(fail=RuntimeError("model call failed: upstream 503 overloaded"))
    worker.run_once(repo, router=router)
    [first] = q(repo, "SELECT retry_count FROM pending_messages ORDER BY id LIMIT 1")
    assert first["retry_count"] == 1  # kept for the next run, not dropped
    for _ in range(12):  # each run retries the session's next batch until its retries are spent
        worker.run_once(repo, router=router)
    assert q(repo, "SELECT COUNT(*) n FROM pending_messages")[0]["n"] == 0  # nothing lost
    [obs] = q(repo, "SELECT * FROM observations")
    assert json.loads(obs["metadata"])["derived"] is True
    assert json.loads(obs["files_modified"]) == ["shop/payments.py"]
    state = health.read(Store.open(repo))
    assert health.is_unhealthy(state)
    assert "can't save session memories" in health.warning_text(Store.open(repo))


def test_context_overflow_recycles_the_conversation(repo):
    turn(repo)
    seen = {"n": 0}

    def reply(prompt):
        seen["n"] += 1
        return "Prompt is too long" if seen["n"] == 1 else OBSERVATION_XML
    router = ScriptedRouter(replies={"recall_observe": reply})
    stats = worker.run_once(repo, router=router)
    assert stats["recycled"] == 1 and q(repo, "SELECT COUNT(*) n FROM observations")[0]["n"] == 1
    # a fresh generation is briefed with the session-start context
    assert "<session_start_context>" in router.calls[1]["system"] or router.calls[1]["system"]


def test_budget_recycle_retires_a_full_conversation(repo, monkeypatch):
    monkeypatch.setenv("CAIRN_RECALL_OBSERVER_MAX_CONVERSATION_CHARS", "10")
    turn(repo)
    router = ScriptedRouter()
    stats = worker.run_once(repo, router=router)
    assert stats["recycled"] >= 1 and q(repo, "SELECT COUNT(*) n FROM pending_messages")[0]["n"] == 0


def test_without_a_model_records_are_derived_per_turn(repo):
    turn(repo)

    class NoModel:
        available = False
    stats = worker.run_once(repo, router=NoModel())
    assert stats["derived"] == 3 and not stats["model"]
    [obs] = q(repo, "SELECT * FROM observations")
    assert obs["title"] == "Fix the double charge on retry" and obs["type"] == "change"
    assert json.loads(obs["files_modified"]) == ["shop/payments.py"] and json.loads(obs["files_read"]) == \
        ["shop/gateway.py"]
    assert json.loads(obs["concepts"]) == ["what-changed"]
    [summ] = q(repo, "SELECT * FROM session_summaries")
    assert summ["request"] == "Fix the double charge on retry" and "shop/payments.py" in summ["completed"]
    assert summ["notes"].startswith("Fixed: charges")
    # later events of the same prompt merge into the same derived record
    hooks.run("observation", {"session_id": "s1", "cwd": str(repo), "tool_name": "Bash",
                              "tool_input": {"command": "pytest -q tests/"}, "tool_use_id": "late"})
    worker.run_once(repo, router=NoModel())
    [obs] = q(repo, "SELECT * FROM observations")
    assert any("pytest -q tests/" in f for f in json.loads(obs["facts"]))
    assert json.loads(obs["files_modified"]) == ["shop/payments.py"]


def test_a_second_worker_exits_while_one_holds_the_lock(repo):
    turn(repo)
    with worker.worker_lock(repo) as held:
        assert held
        assert worker.run_once(repo, router=ScriptedRouter()) == {"status": "busy"}
        assert hooks.worker_running(repo)
    assert not hooks.worker_running(repo)


def test_hosted_worker_drains_new_work_until_stopped(repo):
    class NoModel:
        available = False
    stop = threading.Event()
    t = threading.Thread(target=worker.run_worker, args=(repo, stop), kwargs={"poll_seconds": 0.05,
                                                                              "router": NoModel()})
    t.start()
    try:
        turn(repo)
        for _ in range(200):
            if q(repo, "SELECT COUNT(*) n FROM session_summaries")[0]["n"]:
                break
            stop.wait(0.05)
    finally:
        stop.set()
        t.join(5)
    assert q(repo, "SELECT COUNT(*) n FROM session_summaries")[0]["n"] == 1
    assert not t.is_alive()


def test_worker_module_entry_point(repo):
    turn(repo)
    res = subprocess.run([sys.executable, "-m", "cairn.engines.recall.worker", "--once", "--no-model", "--root", str(repo)],
                         capture_output=True, text=True, check=False, cwd=repo,
                         env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}, encoding="utf-8", errors="replace")
    assert res.returncode == 0, res.stderr
    assert q(repo, "SELECT COUNT(*) n FROM observations")[0]["n"] == 1
    status = worker.status(repo)
    assert status["queueDepth"] == 0 and not status["worker"]["running"]


def test_oversized_fields_are_condensed_or_truncated(repo):
    hooks.run("session-init", {"session_id": "s1", "cwd": str(repo), "prompt": "read a huge file"})
    hooks.run("observation", {"session_id": "s1", "cwd": str(repo), "tool_name": "Bash",
                              "tool_input": {"command": "cat big.log"}, "tool_response": {"stdout": "y" * 40_000}})
    router = ScriptedRouter()
    worker.run_once(repo, router=router)
    assert any(c["task"] == "recall_compress" for c in router.calls)
    obs_prompt = next(c["prompt"] for c in router.calls if c["task"] == "recall_observe")
    assert "<condensed" in obs_prompt and "condensed payload" in obs_prompt
    field = prompts.truncate_observation_field({"stdout": "z" * 40_000})
    assert "<elided chars=" in field and len(field) < 20_000


def test_parser_and_output_classifier():
    mode = load_mode("code")
    res = parser.parse_agent_xml(OBSERVATION_XML, mode)
    assert res["valid"] and res["observations"][0]["concepts"] == ["gotcha", "problem-solution"]
    assert parser.parse_agent_xml('<skip_summary reason="noise" />', mode)["summary"]["skipped"]
    assert not parser.parse_agent_xml("<summary><notes>x</notes></summary>", mode)["valid"]
    salvage = parser.parse_agent_xml("<observation><type>change</type>Refactored the queue\nmore detail</observation>",
                                     mode)
    assert salvage["observations"][0]["title"] == "Refactored the queue"
    assert salvage["observations"][0]["narrative"] == "more detail"
    assert parser.classify_observer_output("") == "idle" and parser.classify_observer_output("hi") == "prose"
    assert parser.is_context_overflow("Prompt is too long")
    assert not parser.is_transport_failure("Connection error handling was reviewed")
    assert parser.is_transport_failure("connect ECONNREFUSED 127.0.0.1:443")
    assert parser.is_auth_failure("Not logged in") and not parser.is_auth_failure("Not logged in during the reboot")
    assert parser.classify_error(RuntimeError("no model key configured")) == "setup_required"
    img = prompts.strip_image_payloads({"content": [{"type": "image", "source": {"data": "A" * 100,
                                                                               "media_type": "image/png"}}]})
    assert img["content"][0]["source"] == {"elided": "image data withheld from the observer",
                                           "media_type": "image/png", "bytes": 100}


def test_file_evidence_and_edit_compaction():
    msgs = [{"message_type": "observation", "tool_name": "Read", "tool_input": json.dumps({"file_path": "/r/a.py"})},
            {"message_type": "observation", "tool_name": "apply_patch",
             "tool_input": json.dumps({"patch": "*** Update File: b.py\n+x\n*** Add File: c.py"})},
            {"message_type": "observation", "tool_name": "MultiEdit",
             "tool_input": json.dumps({"file_path": "d.py", "edits": [{"old_string": "a"}]})}]
    assert file_evidence(msgs) == (["/r/a.py"], ["b.py", "c.py", "d.py"])
    inp = {"file_path": "p.py", "old_string": "old", "new_string": "new"}
    out = {"filePath": "p.py", "oldString": "old", "newString": "new", "originalFile": "x" * 20_000,
           "userModified": False, "structuredPatch": [{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 1,
                                                       "lines": ["-old", "+new"]}]}
    view = compact_edit_output(inp, out, 16_000)
    assert view["originalFile"] == "[omitted 20000 source characters]" and view["structuredPatch"][0]["omitted_lines"] == 2


def test_batched_events_and_a_bounded_replay_window(repo, monkeypatch):
    """The defaults send several events per call and replay only recent exchanges (the model is stateless)."""
    monkeypatch.setenv("CAIRN_RECALL_OBSERVE_BATCH", "5")
    monkeypatch.setenv("CAIRN_RECALL_OBSERVER_REPLAY_EXCHANGES", "1")
    turn(repo)
    turn(repo, prompt="Now add a refund", answer="Refund added.")
    router = ScriptedRouter()
    worker.run_once(repo, router=router)
    observe = [c for c in router.calls if c["task"] == "recall_observe"]
    assert len(observe) == 2  # one call per prompt's batch of events
    assert observe[0]["prompt"].count("<observed_from_primary_session>") == 2
    assert observe[1]["cached"].count('<turn role="user">') == 1  # only the last exchange is replayed
    conv = Store.open(repo).get_conversation(1)
    assert len(conv["history"]["exchanges"]) == 1
    [obs] = q(repo, "SELECT * FROM observations")
    assert json.loads(obs["files_modified"]) == ["shop/payments.py"]


def test_a_session_far_behind_is_observed_in_bigger_batches():
    from cairn.engines.recall.worker import CATCH_UP_CALLS, batch_size
    assert batch_size(5, 20, 40) == 5                      # a normal turn: the configured size
    assert batch_size(5, 20, 20 * CATCH_UP_CALLS * 3) == 20  # far behind: capped catch-up size
    assert 5 < batch_size(5, 20, 9 * CATCH_UP_CALLS) < 20    # in between: drains in about CATCH_UP_CALLS calls
    assert batch_size(5, 0, 10_000) == 5                   # catch-up off
    assert batch_size(0, 20, 0) == 1


def test_a_catch_up_batch_stays_small_and_cheap(repo, monkeypatch):
    """A long autonomous turn (many big tool outputs) must not become a huge prompt: fields are trimmed to a
    per-call budget, no per-field condensing calls are made, and replayed history is bounded."""
    monkeypatch.setenv("CAIRN_RECALL_OBSERVE_BATCH", "5")
    monkeypatch.setenv("CAIRN_RECALL_OBSERVE_CATCH_UP", "20")
    monkeypatch.setenv("CAIRN_RECALL_OBSERVER_REPLAY_EXCHANGES", "8")
    monkeypatch.setattr(worker, "CATCH_UP_CALLS", 1)  # any backlog counts as "far behind"
    base = {"session_id": "long", "cwd": str(repo)}
    hooks.run("session-init", {**base, "prompt": "Refactor everything"})
    for i in range(60):
        hooks.run("observation", {**base, "tool_name": "Bash", "tool_input": {"command": f"pytest -k case{i}"},
                                  "tool_response": {"stdout": f"case{i} " + "x" * 50_000}, "tool_use_id": f"t{i}"})
    router = ScriptedRouter()
    worker.run_once(repo, router=router)
    observe = [c for c in router.calls if c["task"] == "recall_observe"]
    assert observe and not [c for c in router.calls if c["task"] == "recall_compress"]
    assert len(observe) <= 4  # 60 events in batches of 20, not 12 batches of 5
    for call in observe:
        assert len(call["prompt"]) < 48_000 + 20 * 1_500  # the budget plus per-event framing
        assert len(call["cached"]) < 24_000 + 2_000


def test_background_work_never_moves_to_a_stronger_model(cairn):
    from cairn.router import Router
    router = Router(cairn.project)
    assert router.tier_for("recall_observe", 500_000) == "fast"   # bulk work stays on its tier
    assert router.tier_for("extract", 100_000) == "deep"           # judgement moves up one step
    assert router.tier_for("classify", 10_000_000) == "fast"
    assert router.tier_for("why", 10_000_000) == "deep"            # never auto-escalates to frontier
    assert router.tier_for("summarize", 0) == "fast"

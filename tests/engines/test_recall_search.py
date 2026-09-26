"""Recall retrieval: keyword + semantic search, timelines, agent tools and their visibility, injected
context, UI data (feed, sessions, stats, stream, import/export) and the read-model bridge."""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from cairn.engines.recall import context, mcp, sqlsearch, viewer
from cairn.engines.recall import settings as rsettings
from cairn.engines.recall.search import RecallSearch
from cairn.engines.recall.store import Store
from cairn.engines.recall.vectorsync import VectorSync

DAY = 86_400_000


@pytest.fixture()
def repo(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "shop"
    (root / ".cairn").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for k, v in {"CAIRN_HOME": str(tmp_path / "home"), "CAIRN_EMBEDDER": "hash", "CAIRN_NO_CLI_MODELS": "1",
                 "CAIRN_RECALL_NO_SPAWN": "1"}.items():
        monkeypatch.setenv(k, v)
    now = int(time.time() * 1000)
    with Store.open(root) as st:
        sid = st.create_sdk_session("s1", "shop", "Fix the double charge", platform_source="claude")
        st.save_user_prompt("s1", 1, "Fix the double charge on retry", sid)
        mid = st.ensure_memory_session_id(sid)
        st.store_observations(mid, "shop", [
            {"type": "bugfix", "title": "Idempotency key prevents double charge", "subtitle": "retry path",
             "facts": ["charge() takes an idempotency key"], "narrative": "Payments retried on timeout and charged"
             " twice; the gateway now dedupes by idempotency key.", "concepts": ["gotcha", "problem-solution"],
             "files_read": ["shop/gateway.py"], "files_modified": ["shop/payments.py"]}], None, 1, 1200, now - 3 * 60_000)
        st.store_observations(mid, "shop", [
            {"type": "discovery", "title": "Ledger writes are append-only", "subtitle": "ledger",
             "facts": ["ledger rows are never updated"], "narrative": "The ledger module only inserts rows.",
             "concepts": ["how-it-works"], "files_read": ["shop/ledger.py"], "files_modified": []}], None, 1, 800,
            now - 2 * 60_000)
        st.store_summary(mid, "shop", {"request": "Fix the double charge", "investigated": "gateway retries",
                                       "learned": "provider retries on timeout", "completed": "idempotency key added",
                                       "next_steps": "regression test", "notes": None,
                                       "files_read": ["shop/gateway.py"], "files_edited": ["shop/payments.py"]},
                         1, 300, now - 60_000)
        sid2 = st.create_sdk_session("c1", "shop", "Codex work", platform_source="codex")
        mid2 = st.ensure_memory_session_id(sid2)
        st.store_observations(mid2, "shop", [
            {"type": "feature", "title": "Refund endpoint added", "facts": ["POST /refunds"], "narrative":
             "Codex added a refund endpoint.", "concepts": ["what-changed"], "files_read": [],
             "files_modified": ["shop/api.py"]}], None, 1, 500, now - 120 * DAY)
    return root


def text(res: dict) -> str:
    return res["content"][0]["text"]


def test_keyword_search_filters(repo):
    with Store.open(repo) as st:
        db = st.db
        assert [o["title"] for o in sqlsearch.search_observations(db, "double charge")] == \
            ["Idempotency key prevents double charge"]
        assert sqlsearch.search_observations(db, "charge twice gateway") == []  # a phrase query
        assert sqlsearch.search_observations(db, "charge twice gateway", loose=True)
        assert [o["title"] for o in sqlsearch.search_observations(db, None, type="discovery")] == \
            ["Ledger writes are append-only"]
        assert [o["title"] for o in sqlsearch.search_observations(db, None, concepts=["gotcha"])] == \
            ["Idempotency key prevents double charge"]
        assert [o["title"] for o in sqlsearch.search_observations(db, None, files=["api.py"])] == ["Refund endpoint added"]
        recent = sqlsearch.search_observations(db, None, date_range={"start": int(time.time() * 1000) - DAY})
        assert len(recent) == 2
        assert [o["title"] for o in sqlsearch.search_observations(db, None, platform_source="codex")] == \
            ["Refund endpoint added"]
        assert sqlsearch.search_sessions(db, "timeout")[0]["learned"] == "provider retries on timeout"
        assert sqlsearch.search_user_prompts(db, "double")[0]["prompt_number"] == 1
        with pytest.raises(sqlsearch.SearchInputError):
            sqlsearch.search_observations(db, None)
        by_file = sqlsearch.find_by_file(db, "shop/payments.py")
        assert by_file["observations"] and by_file["sessions"]
        assert len(sqlsearch.find_by_file(db, "shop", is_folder=True)["observations"]) == 3


def test_semantic_search_and_the_index(repo):
    with Store.open(repo) as st:
        vs = VectorSync(repo, st)
        res = vs.backfill()
        assert res["observations"] >= 5 and res["summaries"] == 5 and res["prompts"] == 1
        hits = vs.query("idempotency key double charge", 5, doc_types=["observation"])
        assert hits["ids"][0] == 1 and len(set(hits["ids"])) == len(hits["ids"])  # one hit per entity
        assert vs.backfill()["observations"] == 0  # incremental
    with RecallSearch(repo) as s:
        res = s.search({"query": "idempotency key double charge", "format": "json"})
        assert res["strategy"] == "vectors" and res["observations"][0]["id"] == 1
        # the 90-day window hides older work unless a date range is given
        assert all(o["id"] != 3 for o in res["observations"])
        res = s.search({"query": "refund endpoint", "format": "json", "dateStart": "2000-01-01"})
        assert any(o["id"] == 3 for o in res["observations"])
        index = text(s.search({"query": "double charge", "limit": 5}))
        assert "| #1 |" in index and "Found" in index and "**shop/payments.py**" in index
        assert s.search({"query": "anything", "type": "bugfix", "format": "json"})["observations"][0]["type"] == \
            "bugfix"  # a non-category type filters observations


def test_keyword_fallback_when_vectors_are_off(repo, monkeypatch):
    rsettings.save(repo, {"vectors": False})
    with RecallSearch(repo) as s:
        res = s.search({"query": "double charge", "format": "json"})
        assert res["strategy"] == "fts" and res["observations"][0]["id"] == 1
        assert text(s.search({"query": "nothing matches zzz"})) == 'No results found matching "nothing matches zzz"'
        assert "Ledger" in text(s.search({"obs_type": "discovery"}))


def test_timeline_modes(repo):
    with RecallSearch(repo) as s:
        t = text(s.timeline({"anchor": 2, "depth_before": 3, "depth_after": 3}))
        assert "# Timeline around anchor: 2" in t and "<- **ANCHOR**" in t and "Idempotency key" in t
        around_session = text(s.timeline({"anchor": "S1", "depth_before": 5, "depth_after": 5}))
        assert "**\U0001F3AF #S1** Fix the double charge" in around_session and "<- **ANCHOR**" in around_session
        assert "Timeline for query" in text(s.timeline({"query": "ledger append"}))
        assert s.timeline({"anchor": 999})["isError"]
        assert s.timeline({})["isError"] and s.timeline({"anchor": 1, "query": "x"})["isError"]
        assert "not-a-date" in text(s.timeline({"anchor": "not-a-date"}))
        recent = text(s.recent_context({"limit": 3}))
        assert "Recent Session Context" in recent and "idempotency key added" in recent
        assert "Found" in text(s.search_by_file({"filePath": "shop/payments.py"}))
        assert s.semantic_context("how do we stop charging customers twice on retry", limit=2)["count"] >= 1


def test_agent_tools_and_visibility(repo):
    names = {t["name"] for t in mcp.advertised_tools("local")}
    assert {"recall_search", "recall_timeline", "get_observations", "get_tool_uses", "session_start_context",
            "smart_search", "smart_outline", "smart_unfold", "build_corpus", "query_corpus",
            "important_workflow"} <= names
    assert not names & set(mcp.SERVER_ONLY_TOOL_NAMES)
    assert {"observation_add", "observation_search"} <= {t["name"] for t in mcp.advertised_tools("server")}
    assert "Found" in text(mcp.call_tool(repo, "search", {"query": "double charge"}))  # upstream alias
    rows = json.loads(text(mcp.call_tool(repo, "get_observations", {"ids": [2, 1], "orderBy": "date_asc"})))
    assert [r["id"] for r in rows] == [1, 2]
    assert json.loads(text(mcp.call_tool(repo, "get_tool_uses", {"ids": ["nope"]}))) == []
    ctx = text(mcp.call_tool(repo, "session_start_context", {"project": "shop"}))
    assert "# [shop] recent context" in ctx
    assert mcp.call_tool(repo, "session_start_context", {})["isError"]
    assert mcp.call_tool(repo, "observation_add", {"content": "x"})["isError"]  # local runtime
    added = json.loads(text(mcp.call_tool(repo, "observation_add", {"content": "Deploys need a feature flag"},
                                          runtime="server")))
    assert added["success"]
    job = json.loads(text(mcp.call_tool(repo, "observation_record_event", {
        "eventType": "PostToolUse", "contentSessionId": "api1",
        "payload": {"tool_name": "Bash", "tool_input": {"command": "make"}}}, runtime="server")))
    status = json.loads(text(mcp.call_tool(repo, "observation_generation_status", {"jobId": job["jobId"]},
                                           runtime="server")))
    assert status["status"] == "pending"
    assert "WORKFLOW" not in text(mcp.call_tool(repo, "important_workflow")) and \
        "recall_search" in text(mcp.call_tool(repo, "important_workflow"))
    assert mcp.call_tool(repo, "no_such_tool")["isError"]
    outline = mcp.call_tool(repo, "smart_search", {"query": "zzz"})
    assert not outline["isError"]


def test_session_start_context_render(repo):
    out, stats = context.generate_context_with_stats(repo, cwd=str(repo))
    assert out.startswith("# [shop] recent context")
    assert "Legend:" in out and "Stats: 3 obs" in out and "% savings" in out
    assert "1 " in out and "Idempotency key prevents double charge" in out
    assert "**Learned**: provider retries on timeout" in out  # the latest summary is newer than the observations
    assert stats["observation_count"] == 3 and stats["has_session_summary"]
    colored = context.generate_context(repo, cwd=str(repo), for_human=True)
    assert "\x1b[" in colored and "Context Economics" in colored
    full = context.generate_context(repo, cwd=str(repo))
    tight = context.generate_context(repo, cwd=str(repo), limit=len(full) - 50)
    assert len(tight) <= len(full) - 50 and tight.startswith("# [shop] recent context")
    assert "**Learned**" not in tight  # whole items are dropped (the last summary block first)
    codex_only = context.generate_context(repo, cwd=str(repo), platform_source="codex")
    assert "Refund endpoint" in codex_only and "Idempotency" not in codex_only
    rsettings.save(repo, {"context_observation_types": "discovery"})
    narrowed = context.generate_context(repo, cwd=str(repo))
    assert "Ledger writes" in narrowed and "Idempotency" not in narrowed


def test_context_budget_drops_whole_items():
    cfg = context.ContextConfig(50, 5, 10, True, True, False, True, ["bugfix"], ["gotcha"], "narrative", True, False)
    obs = [{"id": i} for i in range(20)]
    seen = []

    def render(items, c):
        seen.append((len(items), c.full_observation_count, c.show_last_summary, c.session_count))
        return "x" * (100 * len(items) + (500 if c.full_observation_count else 0) + (300 if c.show_last_summary else 0))
    res = context.fit_to_budget(obs, cfg, render, limit=900)
    assert not res["overBudget"] and len(res["text"]) <= 900
    assert seen[1][1] == 0 and seen[2][2] is False  # narratives go first, then the last summary


def test_welcome_hint_and_health_warning(repo, tmp_path):
    empty = tmp_path / "fresh"
    (empty / ".cairn").mkdir(parents=True)
    hint = context.inject_context(empty, ["fresh"])
    assert "no session memory yet" in hint and "http://localhost:" in hint
    from cairn.engines.recall import health
    with Store.open(repo) as st:
        for _ in range(3):
            health.record_failure(st, "claude-code", "model call failed: boom")
    out = context.inject_context(repo, ["shop"])
    assert out.rstrip().endswith("quoting the error above.)") and "boom" in out


def test_viewer_data(repo):
    page = viewer.observations(repo, 0, 2)
    assert len(page["items"]) == 2 and page["hasMore"] and page["items"][0]["id"] == 2
    assert viewer.summaries(repo)["items"][0]["learned"] == "provider retries on timeout"
    assert viewer.prompts(repo)["items"][0]["prompt_text"] == "Fix the double charge on retry"
    feed = viewer.feed(repo, 0, 10)
    assert {i["itemType"] for i in feed["items"]} == {"observation", "summary", "prompt"}
    assert viewer.feed(repo, 0, 10, platform_source="codex")["items"][0]["title"] == "Refund endpoint added"
    sessions = viewer.sessions(repo)["items"]
    assert {s["content_session_id"] for s in sessions} == {"s1", "c1"}
    detail = viewer.session_detail(repo, sessions[-1]["id"])
    assert detail["session"]["content_session_id"] in ("s1", "c1") and "observations" in detail
    assert viewer.projects(repo)["projects"] == ["shop"]
    assert viewer.projects(repo)["sources"] == ["claude", "codex"]
    st = viewer.stats(repo)
    assert st["database"]["observations"] == 3 and st["database"]["summaries"] == 1
    assert viewer.processing_status(repo)["queueDepth"] == 0
    s = viewer.get_settings(repo)
    assert s["settings"]["mode"] == "code" and any(m["id"] == "code--ja" for m in s["modes"])
    assert viewer.update_settings(repo, {"context_session_count": 3})["settings"]["context_session_count"] == 3
    saved = viewer.save_memory(repo, "Always run migrations before deploys", title="Deploy order")
    assert saved["success"] and viewer.observation(repo, saved["id"])["subtitle"] == "Manual memory"
    assert viewer.observations_by_file(repo, ["shop/payments.py"])["count"] == 1
    assert "Recent" not in viewer.context_preview(repo)


def test_export_import_round_trip_and_delete(repo, tmp_path):
    data = viewer.export_memories(repo)
    assert data["totalObservations"] == 3 and data["totalSessions"] == 2 and data["totalPrompts"] == 1
    assert viewer.export_memories(repo, "idempotency")["totalObservations"] >= 1
    other = tmp_path / "other"
    (other / ".cairn").mkdir(parents=True)
    res = viewer.import_memories(other, json.loads(json.dumps(data, default=str)))
    assert res["stats"]["observationsImported"] == 3 and res["stats"]["sessionsImported"] == 2
    assert res["stats"]["promptsImported"] == 1
    again = viewer.import_memories(other, json.loads(json.dumps(data, default=str)))
    assert again["stats"]["observationsSkipped"] == 3 and again["stats"]["sessionsSkipped"] == 2
    assert viewer.delete(repo, "observation", 2)["success"]
    assert viewer.observation(repo, 2) is None
    with Store.open(repo) as st:
        assert st.get_kv("readmodel.resync") == "1"


def test_stream_reports_new_rows_and_queue_changes(repo):
    stop = threading.Event()
    events: list[dict] = []

    def consume():
        for ev in viewer.stream(repo, stop, poll_seconds=0.05):
            events.append(ev)
    t = threading.Thread(target=consume)
    t.start()
    try:
        deadline = time.time() + 3
        while not any(e["type"] == "initial_load" for e in events) and time.time() < deadline:
            time.sleep(0.02)
        from cairn.engines.recall import hooks
        hooks.run("session-init", {"session_id": "s1", "cwd": str(repo), "prompt": "Stream this prompt"})
        with Store.open(repo) as st:
            mid = st.ensure_memory_session_id(1)
            st.store_observations(mid, "shop", [{"type": "change", "title": "Live row", "narrative": "n",
                                                 "concepts": ["what-changed"]}])
        while time.time() < deadline and not {"new_prompt", "new_observation"} <= {e["type"] for e in events}:
            time.sleep(0.02)
    finally:
        stop.set()
        t.join(3)
    kinds = [e["type"] for e in events]
    assert kinds[:2] == ["connected", "initial_load"] and "processing_status" in kinds
    assert any(e["type"] == "new_observation" and e["observation"]["title"] == "Live row" for e in events)
    assert any(e["type"] == "new_prompt" and e["prompt"]["prompt_text"] == "Stream this prompt" for e in events)
    assert viewer.sse({"type": "x"}) == 'data: {"type": "x"}\n\n'


def test_read_model_bridge(tmp_path, monkeypatch, repo):
    """journal mirrors observations/sessions/summaries into the Brain incrementally."""
    from cairn.engines import journal
    from cairn.project import Project
    from cairn.store import Brain
    project = Project(root=repo)
    project.reload()
    brain = Brain(tmp_path / "brain.db")
    res = journal.ingest(project, brain)
    assert res["observations"] == 3 and res["summaries"] == 1
    obs = {e["name"]: e for e in brain.entities("obs")}
    first = obs["Idempotency key prevents double charge"]
    assert first["meta"]["type"] == "bugfix" and first["meta"]["modified"] == ["shop/payments.py"]
    assert {(l["dst"], l["rel"]) for l in brain.links_from(first["id"])} >= {
        ("session:s1", "part_of"), ("file:shop/payments.py", "modifies"), ("file:shop/gateway.py", "reads")}
    [sess] = [e for e in brain.entities("session") if e["id"] == "session:s1"]
    assert sess["meta"]["latest_summary"]["learned"] == "provider retries on timeout"
    kinds = {e["id"].split(":")[0] for e in brain.events(kinds=["session"])}
    assert kinds == {"obs", "summary"}
    assert journal.for_files(brain, ["shop/payments.py"])[0]["title"] == "Idempotency key prevents double charge"
    assert journal.ingest(project, brain)["observations"] == 0  # incremental
    viewer.delete(repo, "observation", 1)
    journal.ingest(project, brain)  # a deletion resyncs the mirror
    assert "Idempotency key prevents double charge" not in {e["name"] for e in brain.entities("obs")}
    assert brain.search("ledger append")[0]["kind"] == "obs"


def test_ui_api_shapes(repo):
    from cairn.engines.recall import api
    page = api.feed(repo, limit=2)
    assert [i["kind"] for i in page["items"]] == ["prompt", "summary"] and page["next_cursor"]
    rest = api.feed(repo, cursor=page["next_cursor"], limit=10)
    assert [i["kind"] for i in rest["items"]] == ["observation"] * 3 and rest["next_cursor"] is None
    obs = rest["items"][0]
    assert set(obs) >= {"kind", "id", "ts", "session_id", "type", "title", "subtitle", "narrative", "facts", "concepts",
                        "files_read", "files_modified", "prompt_number", "tokens"}
    assert obs["session_id"] == "s1" and obs["tokens"]["discovery"] == 800 and isinstance(obs["facts"], list)
    assert [i["kind"] for i in api.feed(repo, type="summary,prompt")["items"]] == ["prompt", "summary"]
    assert [i["title"] for i in api.feed(repo, type="bugfix")["items"]] == ["Idempotency key prevents double charge"]
    assert [i["title"] for i in api.feed(repo, concept="how-it-works")["items"]] == ["Ledger writes are append-only"]
    assert {i["kind"] for i in api.feed(repo, file="payments.py")["items"]} == {"observation", "summary"}
    assert [i["kind"] for i in api.feed(repo, q="double")["items"]] == ["prompt", "summary", "observation"]
    st = api.stats(repo)
    assert (st["sessions"], st["observations"], st["summaries"], st["prompts"]) == (2, 3, 1, 1)
    assert st["by_type"]["bugfix"] == 1 and st["by_concept"]["gotcha"] == 1
    assert st["tokens"]["discovery"] == 2500 and st["tokens"]["saved"] == 2500 - st["tokens"]["read"]
    assert {"path": "shop/payments.py", "reads": 0, "modifies": 1} in st["top_files"][:2]
    detail = api.session(repo, "s1")
    assert detail["session"]["title"] == "Fix the double charge on retry" and detail["session"]["agent"] == "claude"
    assert [o["id"] for o in detail["observations"]] == [1, 2] and detail["summaries"][0]["learned"]
    assert api.session(repo, "missing") is None
    found = api.search(repo, "idempotency double charge", limit=5)["results"]
    assert found[0]["kind"] == "observation" and found[0]["id"] == 1 and 0 <= found[0]["score"] <= 1
    assert all(r["kind"] == "summary" for r in api.search(repo, "timeout", type="summary")["results"])
    assert api.context(repo)["markdown"].startswith("# [shop] recent context")
    assert api.sessions(repo)["items"][0]["id"] in ("s1", "c1")
    events = api.ui_events(repo, {"type": "stored", "observation_ids": [1], "summary_id": 1})
    assert [e["type"] for e in events] == ["observation", "summary"] and events[0]["observation"]["id"] == 1

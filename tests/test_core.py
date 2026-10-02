"""Store, history, specs, drift, memory, linker and the context assembler."""
from __future__ import annotations

import json

import pytest

from cairn import drift
from cairn.engines import history, specs
from cairn.store import Brain, fts_query


def test_store_search_and_memory(tmp_path):
    b = Brain(tmp_path / "b.db")
    b.put_entity("symbol:x", "symbol", "processPayment", "a.py")
    assert b.search("process payment")[0]["id"] == "symbol:x"
    m1 = b.add_memory("Use optimistic locking", kind="convention")
    m2 = b.add_memory("Use pessimistic locking for ledgers", kind="decision", supersedes=m1)
    active = [m["id"] for m in b.memories()]
    assert m2 in active and m1 not in active
    assert b.forget(m2) and not b.memories()
    assert fts_query("") == ""


def test_risk_tags():
    assert history.risk_tags("fix typo") == []
    assert "revert" in history.risk_tags('Revert "x"')
    assert "race_condition" in history.risk_tags("Fix race condition")


def test_sync_builds_all_layers(cairn):
    ov = cairn.overview()
    assert ov["layers"]["map"]["nodes"] > 0
    assert ov["layers"]["timeline"]["commits"] == 5
    assert ov["layers"]["specs"]["features"] == 1
    assert cairn.brain.cochanged("shop/payments.py")


def test_impact_has_dependents_warnings_and_owners(cairn):
    p = cairn.impact("PaymentService", budget=1500)
    secs = p.sections()
    assert any("checkout" in i["text"] for i in secs.get("Dependents", []) + secs.get("Tests likely affected", []))
    assert "Historical warnings" in secs and any("Revert" in i["text"] for i in secs["Historical warnings"])
    assert p.data["risk"] in ("LOW", "MEDIUM", "HIGH")
    gw = cairn.impact("shop/gateway.py").sections()
    assert any("T001" in i["text"] for i in gw.get("Intent", []))


def test_budget_is_respected(cairn):
    for budget in (120, 300, 900):
        text = cairn.impact("PaymentService", budget=budget).render()
        used = int(text.rsplit("budget used: ", 1)[1].split("/")[0])
        assert used <= budget


def test_why_includes_rationale_and_origin(cairn):
    secs = cairn.why("PaymentService.process").sections()
    assert any("idempotency" in i["text"] for i in secs.get("Rationale", []))
    assert secs.get("Origin")


def test_memory_links_to_code(cairn):
    res = cairn.remember("PaymentService.process must always pass an idempotency key", kind="gotcha")
    assert res["status"] == "stored"
    assert cairn.remember("PaymentService.process must always pass an idempotency key")["status"] == "exists"
    mem = cairn.impact("PaymentService").sections().get("Memory", [])
    assert any("idempotency" in m["text"] for m in mem)


def test_specs_parser_and_drift(cairn, repo):
    f = specs.features(repo)[0]
    assert f["progress"] == {"done": 2, "total": 3}
    assert f["tasks"][0]["files"] == [("shop/gateway.py", True)]
    found = drift.check(cairn)
    kinds = {d["kind"] for d in found}
    assert "missing-file" in kinds          # T002 done but shop/ledger.py absent
    assert "uncovered" in kinds             # FR-002 never referenced


def test_specs_ingest_records_one_progress_event_per_feature(cairn):
    ent = cairn.brain.entity("spec:001-refunds")
    assert ent and ent["meta"]["progress"] == {"done": 2, "total": 3}
    rows = cairn.brain.q("SELECT id, meta FROM events WHERE id='spec:001-refunds:progress'")
    assert len(rows) == 1
    assert json.loads(rows[0]["meta"]) == {"done": 2, "total": 3}


def test_progress_event_is_rewritten_not_duplicated(cairn, repo):
    # a disagreeing fact left by an older sync format is cleaned up on ingest
    cairn.brain.add_events([{"id": "specstate:001-refunds:0", "ts": 0.0, "kind": "spec",
                             "title": "Refunds — 0/3 tasks", "refs": ["spec:001-refunds"],
                             "meta": {"done": 0, "total": 3}, "source": "specs"}])
    specs.set_task_done(repo, "001-refunds", "T003", True)
    specs.ingest(cairn.project, cairn.brain)
    assert cairn.brain.entity("spec:001-refunds")["meta"]["progress"] == {"done": 3, "total": 3}
    rows = cairn.brain.q("SELECT id, meta FROM events WHERE id LIKE 'spec:001-refunds:progress%'")
    assert len(rows) == 1                      # same event id, no duplicate rows
    assert json.loads(rows[0]["meta"]) == {"done": 3, "total": 3}
    assert cairn.brain.q("SELECT id FROM events WHERE id LIKE 'specstate:%'") == []


def test_drift_reports_a_stale_progress_fact(cairn, repo):
    cairn.brain.add_events([{"id": "spec:001-refunds:progress", "ts": 0.0, "kind": "spec",
                             "title": "Refunds — 0/3 tasks", "refs": ["spec:001-refunds"],
                             "meta": {"done": 0, "total": 3}, "source": "specs"}])
    stale = [d for d in drift.check(cairn) if d["kind"] == "stale-fact"]
    assert len(stale) == 1
    assert stale[0]["severity"] == "low"
    assert "0/3" in stale[0]["title"] and "2/3" in stale[0]["title"]
    specs.ingest(cairn.project, cairn.brain)   # the read model catches up
    assert not [d for d in drift.check(cairn) if d["kind"] == "stale-fact"]


def test_context_infers_targets(cairn):
    p = cairn.context("change how PaymentService handles retries", budget=800)
    assert p.data["targets"]
    assert "PaymentService" in p.render()


def test_brief_is_small(cairn):
    b = cairn.brief()
    assert len(b) // 4 <= 560 and "Cairn brief" in b


def _hook(kind, root, **payload):
    from cairn import capture
    return capture.record(kind, {"session_id": "s1", "cwd": str(root), **payload})


@pytest.fixture()
def no_model(monkeypatch):
    """Session capture without a model: the sync derives records from the queued tool events."""
    monkeypatch.setenv("CAIRN_NO_CLI_MODELS", "1")
    monkeypatch.setenv("CAIRN_EMBEDDER", "hash")


def test_capture_records_prompts_files_and_commands(cairn, repo, no_model):
    assert _hook("prompt", repo, prompt="Fix the double charge on retry") == 1
    _hook("tool", repo, tool_name="Read", tool_input={"file_path": str(repo / "shop/api.py")})
    _hook("tool", repo, tool_name="Edit", tool_input={"file_path": str(repo / "shop/payments.py")})
    _hook("tool", repo, tool_name="Bash", tool_input={"command": "pytest -q  tests/  API_TOKEN=abc123"})
    _hook("tool", repo, tool_name="Read", tool_input={"file_path": "/etc/hosts"})  # outside the repo: not recorded
    _hook("stop", repo)
    from cairn.engines import journal
    assert journal.ingest(cairn.project, cairn.brain)["observations"] == 1
    assert journal.ingest(cairn.project, cairn.brain)["observations"] == 0  # cursor
    [turn] = cairn.brain.entities("obs")
    assert turn["name"] == "Fix the double charge on retry"
    assert turn["meta"]["read"] == ["shop/api.py"] and turn["meta"]["modified"] == ["shop/payments.py"]
    assert any("pytest -q tests/" in f and "abc123" not in f for f in turn["meta"]["facts"])  # secrets redacted
    secs = cairn.impact("shop/payments.py").sections()
    assert any("double charge" in i["text"] for i in secs.get("Agent sessions", []))


def test_capture_groups_turns_and_finishes_them_on_the_next_sync(cairn, repo, no_model):
    from cairn.engines import journal
    _hook("prompt", repo, prompt="Explain the gateway")
    _hook("tool", repo, tool_name="Read", tool_input={"file_path": str(repo / "shop/gateway.py")})
    journal.ingest(cairn.project, cairn.brain)
    _hook("tool", repo, tool_name="Write", tool_input={"file_path": str(repo / "shop/ledger.py")})  # same turn, later
    _hook("prompt", repo, prompt="Now add a refund")
    _hook("tool", repo, tool_name="Bash", tool_input={"command": "pytest -q"})
    journal.ingest(cairn.project, cairn.brain)
    turns = {e["name"]: e["meta"] for e in cairn.brain.entities("obs")}
    assert turns["Explain the gateway"]["modified"] == ["shop/ledger.py"]  # the later event joined its turn
    assert turns["Explain the gateway"]["read"] == ["shop/gateway.py"]
    assert "Now add a refund" in turns
    [session] = cairn.brain.entities("session")
    assert session["meta"]["turns"] == 2


def test_capture_is_silent_outside_cairn_repos_and_when_disabled(tmp_path, repo):
    from cairn import capture
    assert capture.record("prompt", {"session_id": "s", "cwd": str(tmp_path), "prompt": "hi"}) == 0
    (repo / ".cairn").mkdir(exist_ok=True)
    (repo / ".cairn" / "config.toml").write_text("[sessions]\ncapture = false\n")
    assert capture.record("prompt", {"session_id": "s", "cwd": str(repo), "prompt": "hi"}) == 0


def test_capture_hook_is_light_and_never_fails(repo):
    """Runs on every tool call: standard library only, and bad input must not break the agent."""
    import subprocess
    import sys
    probe = "import sys, cairn.capture; print(sorted(m for m in ('typer', 'rich', 'fastapi', 'cairn.core') if m in sys.modules))"
    assert subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout.strip() == "[]"
    res = subprocess.run([sys.executable, "-m", "cairn.capture", "tool"], input="not json", capture_output=True,
                         text=True, cwd=repo, check=False)
    assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_drift_ids_are_stable_across_processes():
    """Finding ids must not depend on PYTHONHASHSEED, or every sync re-records every finding."""
    import os
    import subprocess
    import sys
    code = "from cairn.drift import _finding; print(_finding('missing-file', 'high', '001-x', 'T1 is done', [])['id'])"
    ids = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                          env={**os.environ, "PYTHONHASHSEED": seed}).stdout.strip() for seed in ("1", "2", "3")}
    assert len(ids) == 1


def test_drift_record_is_idempotent_and_keeps_first_seen(cairn):
    found = drift.check(cairn)
    drift.record(cairn, found)
    first = {e["id"]: e["ts"] for e in cairn.brain.events(kinds=["drift"])}
    assert first
    drift.record(cairn, drift.check(cairn))
    again = {e["id"]: e["ts"] for e in cairn.brain.events(kinds=["drift"])}
    assert again == first


def test_sync_links_summary_names_what_it_counts():
    from cairn import sync
    assert sync._summary("links", {"links": 3}) == "3 memory links"


def test_session_paths_keep_dotfolders(tmp_path):
    from cairn.capture import _rel
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    assert _rel(str(root / ".claude" / "settings.json"), root, str(root)) == ".claude/settings.json"
    assert _rel("./src/app.py", root, str(root)) == "src/app.py"
    assert _rel(str(tmp_path / "elsewhere.py"), root, str(root)) == ""  # outside the repo


def test_pack_accounting_matches_render_and_logs_once(cairn):
    p = cairn.impact("PaymentService", budget=300)
    acc = p.accounting()
    assert f"budget used: {acc['used']}/300" in p.render()
    assert all(v["kept"] <= v["total"] for v in acc["sections"].values())
    p.render()  # rendering again must not count the pack twice
    [q] = cairn.brain.queries()
    assert (q["surface"], q["kind"], q["target"]) == ("cli", "impact", "PaymentService")
    assert q["sent_tokens"] == acc["used"] and q["source_tokens"] > 0 and q["source_files"] >= 1


def test_only_packs_handed_out_are_logged(cairn):
    cairn.impact("PaymentService")  # built, never rendered: nobody received it
    cairn.context("change how PaymentService handles retries").render()
    assert [q["kind"] for q in cairn.brain.queries()] == ["context"]


def test_source_cost_counts_existing_files_once(cairn, repo):
    tokens, n = cairn.source_cost(["shop/payments.py", "shop/payments.py", "missing.py"])
    assert n == 1 and tokens == max(1, (repo / "shop/payments.py").stat().st_size // 4)


def test_file_graph_layers_and_cycles():
    from cairn.engines.mapper import Edge, MapIndex
    idx = MapIndex()
    for nid, f in {"a": "app.py", "b": "svc.py", "c": "db.py", "d": "x.py", "e": "y.py"}.items():
        idx.nodes[nid] = {"id": nid, "label": nid, "file_type": "code", "source_file": f}
        idx.by_file[f].append(nid)
    for s, t in (("a", "b"), ("b", "c"), ("d", "e"), ("e", "d"), ("d", "c")):
        idx.out[s].append(Edge(t, "calls", "EXTRACTED", None))
        idx.inc[t].append(Edge(s, "calls", "EXTRACTED", None))
    g = idx.file_graph()
    layer = {n["id"]: n["layer"] for n in g["nodes"]}
    assert (layer["db.py"], layer["svc.py"], layer["app.py"]) == (0, 1, 2)
    assert layer["x.py"] == layer["y.py"] == 1  # an import cycle shares one layer, above what it uses
    cycle = {n["id"]: n["cycle"] for n in g["nodes"]}
    assert cycle["x.py"] == ["x.py", "y.py"] and cycle["app.py"] == []
    assert {"source": "app.py", "target": "svc.py", "weight": 1} in g["links"]


def test_wrapped_requirements_are_read_whole(tmp_path):
    fdir = tmp_path / "specs" / "001-x"
    fdir.mkdir(parents=True)
    (fdir / "spec.md").write_text("# Feature Specification: X\n\n- **FR-001**: The system MUST parse specs into\n"
                                  "  features and tasks.\n- **FR-002**: One line.\n\n  Unrelated indented text.\n")
    (fdir / "tasks.md").write_text("# Tasks\n")
    reqs = specs.features(tmp_path)[0]["requirements"]
    assert [r["text"] for r in reqs] == ["The system MUST parse specs into features and tasks.", "One line."]


def test_map_rebuild_skips_when_nothing_changed(cairn, repo):
    from cairn.engines import mapper
    assert mapper.build(repo)[1] == "unchanged"                 # the fixture already synced once
    (repo / "shop" / "api.py").write_text((repo / "shop" / "api.py").read_text() + "\n\ndef refund(order_id): ...\n")
    ok, summary = mapper.build(repo)
    assert ok and summary.startswith("1 changed file re-mapped")
    assert any(cairn.map.label(n) == "refund()" for n in cairn.map.by_file["shop/api.py"])
    assert mapper.build(repo)[1] == "unchanged"


def test_a_shrink_guard_failure_reads_like_english_on_the_web_page(cairn, repo, monkeypatch):
    from cairn.engines import mapper
    from cairn.engines.graph import api as graph_api

    cli_message = (
        "[cairn graph] WARNING: new graph has 3 nodes but existing graph.json has 10. "
        "Refusing to overwrite — you may be missing chunk files from a previous session. "
        "Pass --force to override."
    )
    monkeypatch.setattr(graph_api, "build",
                         lambda *a, **k: {"ok": False, "summary": cli_message, "log": [cli_message]})
    ok, summary = mapper.build(repo, force=True)
    assert not ok
    assert "[cairn graph]" not in summary
    assert "Pass --force to override" not in summary  # meaningless to someone on the web page
    assert f"cairn graph update {repo} --force" in summary  # the actual command to run instead


def test_changing_ignore_rules_rebuilds_the_map(cairn, repo):
    from cairn.engines import mapper
    assert mapper.build(repo)[1] == "unchanged"
    with open(repo / ".cairn" / "graphignore", "a") as fh:
        fh.write("shop/api.py\n")
    ok, summary = mapper.build(repo)
    assert ok and summary != "unchanged"
    assert "shop/api.py" not in {cairn.map.file_of(n) for n in cairn.map.nodes}


def test_map_includes_yaml_and_yml_files(cairn, repo):
    from cairn.engines import mapper
    (repo / "ci.yaml").write_text("name: CI\njobs:\n  build:\n    runs-on: ubuntu-latest\n")
    (repo / "policies").mkdir()
    (repo / "policies" / "retention.yml").write_text("keep_days: 90\n")
    assert mapper.build(repo)[0]
    files = set(cairn.map.by_file)
    assert "ci.yaml" in files and "policies/retention.yml" in files
    langs = cairn.map.languages()
    assert langs[".yaml"] >= 1 and langs[".yml"] >= 1  # the per-language overview counts them
    # searchable at minimum by file name, even without deep symbols
    assert cairn.map.resolve("ci.yaml")


def test_map_excludes_cairn_generated_files_but_keeps_user_files(cairn, repo):
    from cairn.engines import mapper
    block = "<!-- cairn:begin -->\n" + ("Cairn memory instructions. " * 40) + "\n<!-- cairn:end -->\n"
    (repo / "AGENTS.md").write_text("Two words.\n\n" + block)          # cairn's block is the content
    (repo / "CLAUDE.md").write_text(
        "<!-- generated by cairn; safe to edit, removed by `cairn uninstall` -->\n" + block)
    (repo / "GEMINI.md").write_text("# My project\n\n" + ("My own conventions. " * 60) + "\n" + block)
    (repo / ".mcp.json").write_text('{"mcpServers": {"cairn": {"command": "python", "args": ["-m", "cairn"]}}}')
    (repo / "README.md").write_text("# Shop\n\nUser-authored documentation.\n")
    ok, summary = mapper.build(repo)
    assert ok
    files = set(cairn.map.by_file)
    assert "README.md" in files
    assert "GEMINI.md" in files          # user-authored: the cairn block is a minority of the file
    assert "AGENTS.md" not in files      # majority cairn block: cairn's output, not the system
    assert "CLAUDE.md" not in files      # carries the generated-by-cairn header
    assert ".mcp.json" not in files      # exists only to register cairn's own MCP server


def test_map_of_an_empty_repo_is_empty(tmp_path, monkeypatch):
    """cairn init on a fresh, zero-commit repo must not map cairn's own artifacts."""
    import os
    import subprocess

    from cairn.engines import mapper
    root = tmp_path / "fresh-repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, env=os.environ, check=True)
    block = "<!-- cairn:begin -->\nCairn instructions.\n<!-- cairn:end -->\n"
    (root / "AGENTS.md").write_text(block)
    (root / "CLAUDE.md").write_text("<!-- generated by cairn -->\n" + block)
    (root / "GEMINI.md").write_text(block)
    (root / ".mcp.json").write_text('{"mcpServers": {"cairn": {}}}')
    (root / ".cairn").mkdir()
    (root / ".cairn" / "config.toml").write_text("")
    ok, summary = mapper.build(root)
    assert ok and summary == "0 files"
    assert not mapper.index(mapper.map_dir(root) / "graph.json").nodes


def test_map_never_includes_cairn_dir(cairn, repo):
    from cairn.engines import mapper
    (repo / ".cairn" / "notes").mkdir(parents=True, exist_ok=True)
    (repo / ".cairn" / "notes" / "scratch.md").write_text("# Scratch\n\ncairn would map this if it could.\n")
    (repo / ".cairn" / "extra.json").write_text('{"why": "not"}')
    assert mapper.build(repo)[0]
    assert not [f for f in cairn.map.by_file if f.startswith(".cairn/")]


def test_set_cfg_never_writes_an_invalid_config(tmp_path):
    import tomllib

    import pytest

    from cairn.project import Project
    proj = Project(root=tmp_path)
    proj.ensure_dir()
    text = proj.config_path.read_text().replace("[models]", "[ models ]   # which models")
    proj.config_path.write_text(text)
    proj.set_cfg("models.provider", "openai")
    proj.set_cfg("models.base_url", "http://h/v1\nx\x07")
    tomllib.loads(proj.config_path.read_text())
    assert proj.cfg("models.provider") == "openai" and proj.cfg("models.base_url") == "http://h/v1\nx\x07"
    with pytest.raises(ValueError):
        proj.set_cfg("deep.budget_tokens", None)


def test_session_start_carries_remembered_facts_and_always_its_instruction(cairn):
    for i in range(12):
        cairn.remember(f"Convention {i}: every money amount is an integer number of cents, never a float value",
                       kind="convention")
    cairn.remember("Refund retries use exponential backoff capped at 5 attempts", kind="fact")
    brief = cairn.brief()
    assert "Refund retries use exponential backoff" in brief
    assert brief.rstrip().endswith("Record durable learnings with cairn_remember.")
    assert len(cairn.brief(max_tokens=60).splitlines()) >= 2  # a tiny budget still ends with the instruction


def test_remember_links_symbols_and_refreshes_agent_context_files(cairn, monkeypatch):
    from cairn import agents
    refreshed = []
    monkeypatch.setattr(agents, "refresh_context", lambda project, c=None: refreshed.append(project.root) or True)
    res = cairn.remember("checkout() in shop/api.py must pass an idempotency key", kind="gotcha", scope="team")
    assert refreshed == [cairn.project.root]
    assert cairn.brain.memory(res["id"])["scope"] == "team"
    monkeypatch.setattr(agents, "refresh_context", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    assert cairn.remember("Refunds are capped at the captured amount")["status"] == "stored"  # never fails a remember

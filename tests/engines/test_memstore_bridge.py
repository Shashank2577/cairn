"""Cairn's memory layer over the engine: remember / recall / about / supersede / forget with and
without a model, and seeding memories from what the repository already contains."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_memstore_support import FakeRouter  # noqa: E402

from cairn.engines import vectors  # noqa: E402
from cairn.engines.memory import MemoryStore, collect_seeds, commit_seeds, same_fact, seed_from_repo  # noqa: E402
from cairn.project import Project  # noqa: E402
from cairn.store import Brain  # noqa: E402

ENV = {**os.environ, "GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
       "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.com"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBEDDER", "hash")
    monkeypatch.setenv("CAIRN_NO_CLI_MODELS", "1")
    monkeypatch.setenv("CAIRN_NLP_AUTO_DOWNLOAD", "0")
    vectors.reset_embedder()
    yield
    vectors.reset_embedder()


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=ENV,
                          check=True, encoding="utf-8", errors="replace").stdout


def commit(root: Path, files: dict[str, str | None], msg: str) -> str:
    for rel, text in files.items():
        p = root / rel
        if text is None:
            p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", msg)
    return git(root, "rev-parse", "HEAD").strip()


def store_for(repo: Path, router=None) -> MemoryStore:
    project = Project.discover(repo)
    project.ensure_dir()
    return MemoryStore(project, Brain(project.db_path), router if router is not None else FakeRouter(available=False))


def active(store: MemoryStore) -> list[dict]:
    return store.brain.memories()


# ---- remember / recall / about ------------------------------------------------------------------------
def test_remember_without_a_model_uses_the_engine_for_semantic_recall(repo):
    s = store_for(repo)
    res = s.remember("PaymentService.process must always pass an idempotency key", kind="gotcha")
    assert res["status"] == "stored"
    row = s.brain.memory(res["id"])
    assert row["engine_ref"] and row["kind"] == "gotcha"
    assert s.remember("PaymentService.process must always pass an idempotency key")["status"] == "exists"
    assert s.remember("  PaymentService.process must   always pass an idempotency key ")["status"] == "exists"
    assert [m["id"] for m in s.recall("idempotency")] == [res["id"]]           # full-text
    assert s.semantic.search("payments idempotence")[0]["id"] == row["engine_ref"]  # local embeddings, no model
    assert [m["id"] for m in s.recall("payments idempotence")] == [res["id"]]
    assert s.semantic.ready and (repo / ".cairn" / "memstore" / "history.db").exists()
    with pytest.raises(ValueError):
        s.remember("   ")
    with pytest.raises(ValueError):
        s.remember("x", supersedes="nope")


def test_supersede_evolves_the_engine_memory_and_keeps_history(repo):
    s = store_for(repo)
    old = s.remember("Use optimistic locking for orders", kind="convention")
    new = s.remember("Use pessimistic locking for ledger rows", kind="decision", supersedes=old["id"])
    assert new["status"] == f"superseded {old['id']}"
    assert s.brain.memory(old["id"])["superseded_by"] == new["id"]
    assert s.brain.memory(new["id"])["engine_ref"] == s.brain.memory(old["id"])["engine_ref"]
    hist = s.history(new["id"])
    assert [c["id"] for c in hist["chain"]] == [new["id"], old["id"]]
    assert [(c["event"], c["new_memory"]) for c in hist["changes"]] == [
        ("ADD", "Use optimistic locking for orders"), ("UPDATE", "Use pessimistic locking for ledger rows")]
    assert [m["id"] for m in s.recall("locking")] == [new["id"]]


def test_with_a_model_new_memories_reconcile_against_old_ones(repo):
    router = FakeRouter()
    s = store_for(repo, router)
    a = s.remember("Deploys run from main", kind="decision")
    same = s.remember("deploys run from MAIN")          # model says NONE
    assert same == {"id": a["id"], "status": "exists"}
    upd = s.remember("Deploys run from the release branch", kind="decision")  # model says UPDATE
    assert upd["status"] == "updated" and s.brain.memory(a["id"])["superseded_by"] == upd["id"]
    assert s.brain.memory(upd["id"])["engine_ref"] == s.brain.memory(a["id"])["engine_ref"]
    gone = s.remember("no longer deploys run from the release branch")  # model says DELETE
    assert gone["retired"] == [upd["id"]] and s.brain.memory(upd["id"])["superseded_by"] == gone["id"]
    assert s.brain.memory(gone["id"])["engine_ref"]  # still searchable
    assert [m["text"] for m in active(s)] == ["no longer deploys run from the release branch"]
    assert set(router.tasks()) == {"memory"}
    assert all("Personal Information Organizer" not in c["system"] for c in router.calls)  # no re-extraction


def test_a_statement_the_model_splits_becomes_several_memories(repo):
    s = store_for(repo, FakeRouter())
    res = s.remember("Builds use pnpm + Tests use vitest", kind="convention")
    assert res["status"] == "stored" and len(res["also"]) == 1
    texts = {m["text"]: m for m in active(s)}
    assert set(texts) == {"Builds use pnpm", "Tests use vitest"}
    assert all(m["engine_ref"] and m["kind"] == "convention" for m in texts.values())


def test_forget_removes_the_memory_from_the_engine(repo):
    s = store_for(repo)
    res = s.remember("The staging database is reset nightly")
    ref = s.brain.memory(res["id"])["engine_ref"]
    assert s.forget(res["id"])
    assert s.semantic._engine().get(ref) is None
    assert s.recall("staging database") == []


def test_engine_can_be_switched_off(repo):
    (repo / ".cairn").mkdir(exist_ok=True)
    (repo / ".cairn" / "config.toml").write_text('[memory]\nengine = "off"\n', encoding="utf-8")
    s = store_for(repo)
    res = s.remember("Feature flags live in LaunchConfig")
    assert s.brain.memory(res["id"])["engine_ref"] is None and not s.semantic.ready
    assert [m["id"] for m in s.recall("feature flags")] == [res["id"]]


def test_about_finds_memories_linked_to_files_and_labels(repo):
    s = store_for(repo)
    res = s.remember("Never call charge() without an idempotency key", kind="gotcha")
    s.brain.link([(f"memory:{res['id']}", "file:shop/gateway.py", "mentions", "INFERRED", 0.8, "memory-links")])
    s.remember("Use ruff for linting", kind="convention")
    assert [m["id"] for m in s.about(["charge"], ["shop/gateway.py"])] == [res["id"]]


def test_graph_memory_reads_the_real_temporal_graph_without_a_model(repo):
    pytest.importorskip("kuzu")
    (repo / ".cairn").mkdir(exist_ok=True)
    (repo / ".cairn" / "config.toml").write_text("[memory]\ngraph = true\n", encoding="utf-8")
    router = FakeRouter(available=False)
    project = Project.discover(repo)
    router.project, router.brain = project, Brain(project.db_path)
    s = MemoryStore(project, router.brain, router)
    assert s.remember("We deploy from the release branch")["status"] == "stored"  # no model: no episode, no error
    engine = s.semantic.engine
    assert engine.search("deploy", filters={"project_id": project.id}, threshold=0.0)["relations"] == []
    assert type(engine.graph._service).__name__ == "TemporalService" and engine.graph._missing is None


def test_user_and_session_scopes_reach_the_engine(repo):
    s = store_for(repo)
    res = s.remember("Ada reviews every migration", kind="preference", scope="user", scope_id="ada")
    row = s.brain.memory(res["id"])
    assert row["scope"] == "user"
    item = s.semantic._engine().get(row["engine_ref"])
    assert item["user_id"] == "ada" and item["project_id"] == s.project.id and item["metadata"]["kind"] == "preference"


# ---- seeding -----------------------------------------------------------------------------------
ADR = """# ADR-0001: Use SQLite for the read model

**Status:** Accepted · **Date:** 2026-01-02

## Context
Surfaces need joined answers fast.

## Decision
Store the read model in **SQLite** because it must work offline. Surfaces never query engine stores.

## Consequences
Sync keeps it fresh.
"""
ADR_PROPOSED = "# ADR-0002: Move to Postgres\n\nStatus: Proposed\n\n## Decision\nUse Postgres.\n"
SPEC = """# Feature Specification: Refunds

## Clarifications

### Session 2026-01-03

- Q: Can refunds exceed the original charge? → A: No. A refund is capped at the captured amount,
  including partial refunds.
- Q: Who can issue refunds? → A: Support agents and admins.

## Requirements
- **FR-001**: The system MUST refund through the gateway.
"""
CONTRIBUTING = """# Contributing

Run the tests first.

## Rules of thumb
- Never call the payment gateway without an idempotency key.
- Every migration must be reversible and tested.
- Keep public API changes behind a feature flag.

## Setup
- Run `make setup` to install dependencies and git hooks for you.
"""
FIX_BODY = ("fix(api): reject negative refund amounts\n\nA negative amount was sent to the gateway as a charge, "
            "so the customer was billed twice instead of refunded.\n\nSigned-off-by: Ada <ada@example.com>")


def _seed_repo(repo: Path) -> dict:
    commit(repo, {"docs/adr/0001-use-sqlite.md": ADR, "docs/adr/0002-postgres.md": ADR_PROPOSED,
                  "docs/adr/README.md": "# Decisions\n\n- a list of decisions lives here\n",
                  "specs/001-refunds/spec.md": SPEC, "CONTRIBUTING.md": CONTRIBUTING}, "docs: decisions and rules")
    fix = commit(repo, {"shop/api.py": "def checkout(order_id, amount):\n    assert amount >= 0\n"}, FIX_BODY)
    return {"fix": fix}


def by_source(s: MemoryStore) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for m in active(s):
        out.setdefault(m["source"], []).append(m)
    return out


def test_seed_from_repo_is_deterministic_idempotent_and_cited(repo):
    shas = _seed_repo(repo)
    s = store_for(repo)
    stats = seed_from_repo(s.project, s.brain, s.router, store=s)
    assert stats["added"] == 8 and stats["retired"] == 0 and stats["reconciled"] == 0
    src = by_source(s)
    [adr] = src["docs/adr/0001-use-sqlite.md"]
    assert adr["kind"] == "decision" and adr["text"].startswith("Use SQLite for the read model: Store the read model")
    assert "Move to Postgres" not in str(src) and "a list of decisions" not in str(src)
    clar = sorted(m["text"] for m in src["specs/001-refunds/spec.md"])
    assert clar == ["Can refunds exceed the original charge? No. A refund is capped at the captured amount, "
                    "including partial refunds.", "Who can issue refunds? Support agents and admins."]
    rules = sorted(m["text"] for m in src["CONTRIBUTING.md"])
    assert rules == ["Every migration must be reversible and tested.",
                     "Keep public API changes behind a feature flag.",
                     "Never call the payment gateway without an idempotency key."]
    assert all(m["kind"] == "convention" for m in src["CONTRIBUTING.md"])
    gotchas = {m["source"]: m for m in active(s) if m["kind"] == "gotcha"}
    fix = gotchas[f"commit:{shas['fix'][:10]}"]
    assert fix["text"] == ("Reject negative refund amounts: A negative amount was sent to the gateway as a charge, "
                           "so the customer was billed twice instead of refunded.")
    revert = next(m for m in gotchas.values() if "was reverted" in m["text"])
    assert revert["text"].startswith('"Fix double charge on retry" was reverted (commit ')
    # dated by their source, cited, and linked to the files they are about
    commit_ts = float(git(repo, "log", "-1", "--format=%at", shas["fix"]).strip())
    assert fix["created_at"] == commit_ts
    cites = {(k["src"], k["dst"], k["rel"]) for k in s.brain.links_from(f"memory:{adr['id']}")}
    assert (f"memory:{adr['id']}", "file:docs/adr/0001-use-sqlite.md", "cites") in cites
    about = {k["dst"] for k in s.brain.links_from(f"memory:{fix['id']}", rels=("about",))}
    assert about == {"file:shop/api.py"}
    assert {k["dst"] for k in s.brain.links_from(f"memory:{src['specs/001-refunds/spec.md'][0]['id']}")} >= {
        "spec:001-refunds", "file:specs/001-refunds/spec.md"}
    assert {m["id"] for m in s.about([], ["shop/api.py"])} == {fix["id"], revert["id"]}  # both touched it
    # the engine holds them too, with their citation
    engine_item = s.semantic._engine().get(adr["engine_ref"])
    assert engine_item["metadata"]["source"] == "docs/adr/0001-use-sqlite.md"

    again = seed_from_repo(s.project, s.brain, s.router, store=s)
    assert again["added"] == 0 and again["updated"] == 0 and again["retired"] == 0 and again["unchanged"] == 6
    assert len(active(s)) == 8
    # commits are read incrementally, but the citations of earlier commit gotchas are kept
    assert {k["dst"] for k in s.brain.links_from(f"memory:{fix['id']}", rels=("about",))} == {"file:shop/api.py"}
    assert again["links"] == stats["links"]


def test_seeds_follow_their_sources(repo):
    shas = _seed_repo(repo)
    s = store_for(repo)
    seed_from_repo(s.project, s.brain, s.router, store=s)
    before = by_source(s)
    adr_old = before["docs/adr/0001-use-sqlite.md"][0]
    migration_old = next(m for m in before["CONTRIBUTING.md"] if "migration" in m["text"])
    flag_old = next(m for m in before["CONTRIBUTING.md"] if "feature flag" in m["text"])
    fix_old = next(m for m in active(s) if m["source"] == f"commit:{shas['fix'][:10]}")

    commit(repo, {"docs/adr/0001-use-sqlite.md": ADR.replace("because it must work offline",
                                                             "because it must work offline and in CI"),
                  "CONTRIBUTING.md": CONTRIBUTING.replace("reversible and tested.", "reversible and tested in CI.")
                  .replace("- Keep public API changes behind a feature flag.\n", "")}, "docs: tighten rules")
    git(repo, "revert", "--no-edit", shas["fix"])
    stats = seed_from_repo(s.project, s.brain, s.router, store=s)
    assert stats["updated"] == 3 and stats["retired"] == 1 and stats["added"] == 0

    adr_new = by_source(s)["docs/adr/0001-use-sqlite.md"][0]
    assert "offline and in CI" in adr_new["text"] and s.brain.memory(adr_old["id"])["superseded_by"] == adr_new["id"]
    migration_new = s.brain.memory(s.brain.memory(migration_old["id"])["superseded_by"])
    assert migration_new["text"] == "Every migration must be reversible and tested in CI."
    assert s.brain.memory(flag_old["id"])["forgotten"] == 1
    revert = s.brain.memory(s.brain.memory(fix_old["id"])["superseded_by"])
    assert revert["text"].startswith('"fix(api): reject negative refund amounts" was reverted')
    assert [c["event"] for c in s.history(adr_new["id"])["changes"]] == ["ADD", "UPDATE"]

    # a seeded memory someone forgot on purpose stays forgotten
    s.forget(adr_new["id"])
    assert seed_from_repo(s.project, s.brain, s.router, store=s)["added"] == 0
    assert s.brain.memory(adr_new["id"])["forgotten"] == 1


def test_seeding_never_retires_hand_written_memories(repo):
    _seed_repo(repo)
    s = store_for(repo)
    mine = s.remember("Never call the payment gateway without an idempotency key.", kind="gotcha")
    stats = seed_from_repo(s.project, s.brain, s.router, store=s)
    assert stats["merged"] == 1 and stats["added"] == 7
    commit(repo, {"CONTRIBUTING.md": CONTRIBUTING.replace(
        "- Never call the payment gateway without an idempotency key.\n", "")}, "docs: drop rule")
    seed_from_repo(s.project, s.brain, s.router, store=s)
    assert s.brain.memory(mine["id"])["forgotten"] == 0 and s.brain.memory(mine["id"])["superseded_by"] is None


def test_seeding_with_a_model_merges_restatements(repo):
    _seed_repo(repo)
    router = FakeRouter()
    s = store_for(repo, router)
    mine = s.remember("never call the payment gateway without an idempotency key.", kind="gotcha")
    stats = seed_from_repo(s.project, s.brain, s.router, store=s)
    assert stats["merged"] == 1 and stats["reconciled"] == 8
    assert "memory" in router.tasks()
    texts = [m["text"] for m in active(s)]
    assert texts.count("never call the payment gateway without an idempotency key.") == 1
    assert "Never call the payment gateway without an idempotency key." not in texts
    assert s.brain.memory(mine["id"])["superseded_by"] is None


def test_seed_merged_into_a_hand_written_memory_is_never_retired(repo):
    _seed_repo(repo)
    s = store_for(repo, FakeRouter())
    mine = s.remember("Every migration must be reversible.", kind="convention")
    stats = seed_from_repo(s.project, s.brain, s.router, store=s)
    merged = s.brain.memory(s.brain.memory(mine["id"])["superseded_by"])  # the model merged the seed into it
    assert merged["text"] == "Every migration must be reversible and tested." and stats["merged"] == 1
    commit(repo, {"CONTRIBUTING.md": CONTRIBUTING.replace("- Every migration must be reversible and tested.\n", "")},
           "docs: drop rule")
    seed_from_repo(s.project, s.brain, s.router, store=s)
    assert s.brain.memory(merged["id"])["forgotten"] == 0 and s.brain.memory(merged["id"])["superseded_by"] is None


def test_seed_model_budget_is_bounded(repo):
    _seed_repo(repo)
    (repo / ".cairn").mkdir(exist_ok=True)
    (repo / ".cairn" / "config.toml").write_text("[memory]\nseed_model_limit = 2\n", encoding="utf-8")
    s = store_for(repo, FakeRouter())
    stats = seed_from_repo(s.project, s.brain, s.router, store=s)
    assert stats["reconciled"] == 2 and stats["added"] == 8


def test_two_stores_on_one_repository_share_memory(repo):
    server, cli = store_for(repo), store_for(repo)  # e.g. the UI server and `cairn remember`
    a = server.remember("Use ruff for linting", kind="convention")
    b = cli.remember("Use pytest for the tests", kind="convention")
    for s in (server, cli):
        assert {m["id"] for m in s.recall("ruff pytest linting tests")} == {a["id"], b["id"]}
        engine = s.semantic.engine
        assert engine.get(s.brain.memory(a["id"])["engine_ref"]) and engine.get(s.brain.memory(b["id"])["engine_ref"])
    assert cli.remember("Use ruff for linting")["status"] == "exists"


def test_concurrent_seed_runs_do_not_duplicate(repo):
    import threading

    _seed_repo(repo)
    stores = [store_for(repo), store_for(repo)]
    results: list[dict] = []
    threads = [threading.Thread(target=lambda s=s: results.append(seed_from_repo(s.project, s.brain, s.router, store=s)))
               for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r["added"] for r in results) == [0, 8]
    assert len(active(stores[0])) == 8
    engine = stores[1].semantic.engine
    assert len(engine.get_all(filters={"project_id": stores[1].project.id}, top_k=100)["results"]) == 8


def test_check_engine_finds_and_repairs_damaged_records(repo):
    s = store_for(repo)
    a = s.remember("Architecture decisions get an ADR in docs/adr", kind="convention")
    b = s.remember("Environment variables start with CAIRN_ and state lives under .cairn", kind="convention")
    c = s.remember("Releases are cut from the main branch every Tuesday", kind="decision")
    d = s.remember("The staging database is reset nightly", kind="gotcha")
    ref = {k: s.brain.memory(v["id"])["engine_ref"] for k, v in {"a": a, "b": b, "c": c, "d": d}.items()}
    engine = s.semantic.engine
    # the damage seen in the wild: a record shared by two memories rewritten with the other one's text,
    # a record that disappeared, and a memory that never got one
    with s.brain.tx() as db:
        db.execute("UPDATE memories SET engine_ref=? WHERE id=?", (ref["b"], a["id"]))
        db.execute("UPDATE memories SET engine_ref=NULL WHERE id=?", (d["id"],))
    engine.delete(ref["c"])
    # a record legitimately shared by two memories that state the same fact is fine
    twin = s.brain.add_memory("environment variables start with CAIRN_ and state lives under .cairn",
                              kind="convention", engine_ref=ref["b"])

    report = s.check_engine(fix=False)
    assert report["checked"] == 5 and report["ok"] == 2 and report["fixed"] == 0
    assert {(p["memory"], p["problem"]) for p in report["problems"]} == {
        (a["id"], "mismatched"), (c["id"], "missing"), (d["id"], "missing")}
    assert engine.get(ref["b"])["memory"].startswith("Environment variables")  # nothing changed yet

    fixed = s.check_engine(fix=True)
    assert fixed["fixed"] == 3 and fixed["remaining"] == 0
    for mid in (a["id"], b["id"], c["id"], d["id"], twin):
        row = s.brain.memory(mid)
        assert same_fact(row["text"], engine.get(row["engine_ref"])["memory"])
    assert s.brain.memory(a["id"])["engine_ref"] == ref["a"]  # its original record still said exactly this
    assert s.brain.memory(twin)["engine_ref"] == s.brain.memory(b["id"])["engine_ref"] == ref["b"]
    assert s.check_engine()["problems"] == []
    assert s.recall("staging database reset")[0]["id"] == d["id"]  # searchable again


def test_check_engine_removes_records_nobody_uses_any_more(repo):
    s = store_for(repo)
    a = s.remember("Architecture decisions get an ADR in docs/adr", kind="convention")
    engine = s.semantic.engine
    old = s.brain.memory(a["id"])["engine_ref"]
    engine.update(old, text="Environment variables start with CAIRN_")  # rewritten into someone else's fact
    fixed = s.check_engine(fix=True)
    new = s.brain.memory(a["id"])["engine_ref"]
    assert new != old and engine.get(new)["memory"] == "Architecture decisions get an ADR in docs/adr"
    assert fixed["removed_records"] == [old] and engine.get(old) is None


def test_seeding_repairs_the_engine_first(repo):
    _seed_repo(repo)
    s = store_for(repo)
    mine = s.remember("The staging database is reset nightly", kind="gotcha")
    s.semantic.engine.delete(s.brain.memory(mine["id"])["engine_ref"])
    stats = seed_from_repo(s.project, s.brain, s.router, store=s)
    assert stats["engine_check"] == {"checked": 1, "problems": 1, "fixed": 1, "removed_records": 0, "remaining": 0}
    row = s.brain.memory(mine["id"])
    assert s.semantic.engine.get(row["engine_ref"])["memory"] == "The staging database is reset nightly"


def test_check_engine_without_the_engine(repo):
    (repo / ".cairn").mkdir(exist_ok=True)
    (repo / ".cairn" / "config.toml").write_text('[memory]\nengine = "off"\n', encoding="utf-8")
    s = store_for(repo)
    s.remember("Feature flags live in LaunchConfig")
    report = s.check_engine(fix=True)
    assert report["checked"] == 0 and report["fixed"] == 0 and "off" in report["note"]


def test_commit_seed_rules():
    def c(subject, body="", sha="a" * 40, files=("x.py",)):
        return {"sha": sha, "ts": 1.0, "author": "Ada", "subject": subject, "body": body, "files": list(files)}

    seeds = commit_seeds([
        c("fix typo in README", "Fixes the spelling of the product name in two places."),
        c("Fix crash", "short"),
        c("hotfix: cache stampede on deploy",
          "Every pod warmed the cache at once after deploys, which overloaded the database for minutes.",
          sha="b" * 40),
        c('Revert "Add retries"', "This reverts commit " + "c" * 40 + ".\n\nRetries doubled the load during incidents.",
          sha="d" * 40),
        c("Add a feature", "Explains the feature at length for the reviewers of this change."),
    ])
    assert [s.slot for s in seeds] == ["gotcha:commit:" + "b" * 40, "gotcha:commit:" + "d" * 40]
    assert seeds[0].text.startswith("Cache stampede on deploy: Every pod warmed")
    assert seeds[1].text == '"Add retries" was reverted (commit dddddddddd): Retries doubled the load during incidents.'
    assert seeds[1].replaces_slot == "gotcha:commit:" + "c" * 40


def test_collect_seeds_reads_history_incrementally(repo):
    _seed_repo(repo)
    s = store_for(repo)
    from cairn.engines.memory import SeedLedger

    ledger = SeedLedger(s.project.dir / "memstore" / "seeds.db")
    first, rewritten = collect_seeds(s.project, ledger)
    assert not rewritten and sum(x.slot.startswith("gotcha:commit:") for x in first) == 2
    second, _ = collect_seeds(s.project, ledger)
    assert not any(x.slot.startswith("gotcha:commit:") for x in second)
    ledger.close()


class ListsEverythingAsUnchanged(FakeRouter):
    """A reconciler that reports every memory it compared as NONE and records nothing for the new fact:
    the shape that once bound a new memory to an unrelated one."""

    def _reconcile(self, prompt):
        from test_memstore_support import _blocks
        blocks = _blocks(prompt)
        old = __import__("ast").literal_eval(blocks[0].strip()) if len(blocks) >= 2 else []
        return json.dumps({"memory": [{"id": m["id"], "text": m["text"], "event": "NONE"} for m in old]})


def test_an_unrelated_unchanged_memory_is_never_mistaken_for_the_new_one(repo):
    s = store_for(repo, ListsEverythingAsUnchanged())
    first = s.remember("A feature ships with its CLI command, its tests and its docs", kind="convention")
    first_ref = s.brain.memory(first["id"])["engine_ref"]
    new = s.remember("Zqx7 widget flanges are safe to forget", kind="gotcha")
    assert new["status"] == "stored" and new["id"] != first["id"]      # not swallowed as "exists"
    new_ref = s.brain.memory(new["id"])["engine_ref"]
    assert new_ref and new_ref != first_ref                              # its own engine record
    edited = s.remember("Zqx7 widget flanges are safe to delete", kind="gotcha", supersedes=new["id"])
    s.forget(edited["id"])
    engine = s.semantic._engine()
    assert engine.get(first_ref)["memory"] == "A feature ships with its CLI command, its tests and its docs"
    # an engine record whose read-model row is gone is not borrowed by an unrelated memory either
    s.brain.forget(first["id"])
    other = s.remember("Nightly builds publish to the staging bucket", kind="fact")
    assert s.brain.memory(other["id"])["engine_ref"] != first_ref
    # a real duplicate is still recognised
    assert s.remember("nightly builds publish to the STAGING bucket")["id"] == other["id"]


def test_a_seed_recorded_against_an_unrelated_memory_is_written_again(repo):
    import sqlite3
    _seed_repo(repo)
    s = store_for(repo)
    seed_from_repo(s.project, s.brain, s.router, store=s)
    unrelated = s.remember("The office plants are watered on Fridays")
    db = sqlite3.connect(s.project.dir / "memstore" / "seeds.db")
    slot = db.execute("SELECT slot FROM seeds WHERE slot LIKE 'convention:CONTRIBUTING.md#%' LIMIT 1").fetchone()[0]
    old = db.execute("SELECT memory_id FROM seeds WHERE slot=?", (slot,)).fetchone()[0]
    rule = s.brain.memory(old)["text"]
    s.forget(old)  # what the earlier mix-up left behind: the rule's own memory gone, its slot pointing elsewhere
    db.execute("UPDATE seeds SET memory_id=?, owned=0 WHERE slot=?", (unrelated["id"], slot))
    db.commit()
    db.close()
    seed_from_repo(s.project, s.brain, s.router, store=s)
    assert rule in {m["text"] for m in active(s)}
    assert s.brain.memory(unrelated["id"])["text"] == "The office plants are watered on Fridays"


class RewritesAnUnrelatedMemory(FakeRouter):
    """A reconciler that answers every new fact with UPDATE (text unchanged) or DELETE of an unrelated memory."""
    event = "UPDATE"

    def _reconcile(self, prompt):
        import ast

        from test_memstore_support import _blocks
        blocks = _blocks(prompt)
        old = ast.literal_eval(blocks[0].strip()) if len(blocks) >= 2 else []
        if not old:
            return json.dumps({"memory": []})
        m = old[0]
        fact = ast.literal_eval(blocks[-1].strip())[0]
        text = fact if self.event == "REPLACE" else m["text"]  # REPLACE: the unrelated memory becomes the new fact
        return json.dumps({"memory": [{"id": m["id"], "text": text, "event": self.event.replace("REPLACE", "UPDATE"),
                                       "old_memory": m["text"]}]})


@pytest.mark.parametrize("event", ["UPDATE", "REPLACE", "DELETE"])
def test_a_reconciler_cannot_merge_into_or_retire_an_unrelated_memory(repo, event):
    router = RewritesAnUnrelatedMemory()
    router.event = event
    s = store_for(repo, router)
    adr = s.remember("Architecture decisions get an ADR in docs/adr", kind="convention")
    env = s.remember("Environment variables start with CAIRN_ and state lives under .cairn", kind="convention")
    assert env["status"] == "stored" and env["id"] != adr["id"] and not env.get("retired")
    texts = {m["text"] for m in active(s)}
    assert texts == {"Architecture decisions get an ADR in docs/adr",
                     "Environment variables start with CAIRN_ and state lives under .cairn"}
    engine = s.semantic._engine()
    assert engine.get(s.brain.memory(adr["id"])["engine_ref"])["memory"] == "Architecture decisions get an ADR in docs/adr"

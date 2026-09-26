"""The self-reconciling memory engine: reconciliation, history, hybrid search with filters, scopes,
persistence, procedural memory, async API, chat proxy, HTTP handlers, CLI handlers and the graph hook.
All model calls go to a scripted router; embeddings use the deterministic offline embedder."""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_memstore_support import FakeRouter  # noqa: E402

from cairn.engines import vectors  # noqa: E402
from cairn.engines.memstore import AsyncMemory, Memory, MemoryConfig  # noqa: E402
from cairn.engines.memstore import api, commands  # noqa: E402
from cairn.engines.memstore.exceptions import LLMError  # noqa: E402
from cairn.engines.memstore.exceptions import ValidationError as MemoryValidationError  # noqa: E402
from cairn.engines.memstore.llms.router import RouterLLM, render_messages  # noqa: E402
from cairn.engines.memstore.proxy import ChatProxy  # noqa: E402
from cairn.engines.memstore.vector_stores.filtering import matches  # noqa: E402

P = "shop"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBEDDER", "hash")
    monkeypatch.setenv("CAIRN_NLP_AUTO_DOWNLOAD", "0")
    vectors.reset_embedder()
    yield
    vectors.reset_embedder()


def make(tmp_path, router=None, **extra) -> Memory:
    cfg = {"vector_store": {"provider": "faiss", "config": {"path": str(tmp_path / "vec")}},
           "history_db_path": str(tmp_path / "history.db"), **extra}
    return Memory.from_config(cfg, router=router if router is not None else FakeRouter())


def texts(res) -> list[str]:
    return [r["memory"] for r in res["results"]]


# ---- add: reconciliation ----------------------------------------------------------------------------
def test_reconcile_add_update_none_delete_with_history(tmp_path):
    router = FakeRouter()
    m = make(tmp_path, router)
    added = m.add("Deploys run from main", project_id=P)["results"]
    assert [r["event"] for r in added] == ["ADD"]
    mid = added[0]["id"]

    upd = m.add("Deploys run from the release branch", project_id=P)["results"]
    assert upd == [{"id": mid, "memory": "Deploys run from the release branch", "event": "UPDATE",
                    "previous_memory": "Deploys run from main"}]
    assert m.get(mid)["memory"] == "Deploys run from the release branch"

    assert m.add("deploys run from the release branch", project_id=P)["results"] == []  # NONE: nothing changes
    assert len(m.get_all(filters={"project_id": P})["results"]) == 1

    gone = m.add("no longer deploys run from the release branch", project_id=P)["results"]
    assert gone[0]["event"] == "DELETE" and gone[0]["id"] == mid
    assert m.get(mid) is None

    events = [(h["event"], h["old_memory"], h["new_memory"]) for h in m.history(mid)]
    assert events == [("ADD", None, "Deploys run from main"),
                      ("UPDATE", "Deploys run from main", "Deploys run from the release branch"),
                      ("DELETE", "Deploys run from the release branch", None)]
    assert m.history(mid)[-1]["is_deleted"] is True
    # extraction and reconciliation both went through the router, as the "memory" task
    assert set(router.tasks()) == {"memory"}


class ScriptedReconciler(FakeRouter):
    """Answers reconciliation with ``decide(old_memories, facts) -> [actions]`` (ids as shown to the model)."""

    def __init__(self, decide):
        super().__init__()
        self.decide = decide

    def _reconcile(self, prompt):
        import ast

        from test_memstore_support import _blocks
        blocks = _blocks(prompt)
        old = ast.literal_eval(blocks[0].strip()) if len(blocks) >= 2 else []
        return json.dumps({"memory": self.decide(old, ast.literal_eval(blocks[-1].strip()))})


def seeded(tmp_path, decide):
    """An engine holding one unrelated memory, answering reconciliation with ``decide``."""
    m = make(tmp_path, ScriptedReconciler(decide))
    unrelated = m.add_facts("Architecture decisions get an ADR in docs/adr", project_id=P, reconcile=False)
    return m, unrelated["results"][0]["id"]


NEW_FACT = "Environment variables start with CAIRN_ and state lives under .cairn"
ADR_TEXT = "Architecture decisions get an ADR in docs/adr"


@pytest.mark.parametrize("case", ["rewrite", "rewrite-unchanged", "delete", "none-without-add", "empty",
                                  "hallucinated-add"])
def test_reconciliation_never_touches_an_unrelated_memory(tmp_path, case):
    decide = {
        # (a) the unrelated memory "updated" into the new fact / "updated" with nothing new
        "rewrite": lambda old, facts: [{"id": old[0]["id"], "text": facts[0], "event": "UPDATE",
                                        "old_memory": old[0]["text"]}],
        "rewrite-unchanged": lambda old, facts: [{"id": old[0]["id"], "text": old[0]["text"], "event": "UPDATE"}],
        # (b) the unrelated memory deleted
        "delete": lambda old, facts: [{"id": old[0]["id"], "text": old[0]["text"], "event": "DELETE"}],
        # (c) everything NONE and no ADD for the fact
        "none-without-add": lambda old, facts: [{"id": o["id"], "text": o["text"], "event": "NONE"} for o in old],
        "empty": lambda old, facts: [],
        "hallucinated-add": lambda old, facts: [{"id": "7", "text": "The office plants are watered on Fridays",
                                                 "event": "ADD"}],
    }[case]
    m, adr_id = seeded(tmp_path, decide)
    res = m.add_facts(NEW_FACT, project_id=P)["results"]
    assert m.get(adr_id)["memory"] == ADR_TEXT  # untouched, still present
    assert [h["event"] for h in m.history(adr_id)] == ["ADD"]
    assert set(texts(m.get_all(filters={"project_id": P}))) == {ADR_TEXT, NEW_FACT}
    # the events say what the engine did: the new fact was added as its own memory, nothing else
    assert [(r["event"], r["memory"]) for r in res] == [("ADD", NEW_FACT)]
    assert res[0]["id"] != adr_id


def test_add_with_extraction_is_guarded_the_same_way(tmp_path):
    m, adr_id = seeded(tmp_path, lambda old, facts: [{"id": old[0]["id"], "text": facts[0], "event": "UPDATE"}])
    res = m.add(NEW_FACT, project_id=P)["results"]
    assert [(r["event"], r["memory"]) for r in res] == [("ADD", NEW_FACT)]
    assert m.get(adr_id)["memory"] == ADR_TEXT


def test_decisions_about_the_same_subject_are_still_applied(tmp_path):
    old_text = "Deploys run from the main branch"
    m = make(tmp_path, ScriptedReconciler(lambda old, facts: []))
    mid = m.add_facts(old_text, project_id=P, reconcile=False)["results"][0]["id"]

    m.llm.router.decide = lambda old, facts: [{"id": old[0]["id"], "text": facts[0], "event": "UPDATE"}]
    upd = m.add_facts("Deploys run from the release branch", project_id=P)["results"]
    assert upd == [{"id": mid, "memory": "Deploys run from the release branch", "event": "UPDATE",
                    "previous_memory": old_text}]

    m.llm.router.decide = lambda old, facts: [{"id": old[0]["id"], "text": old[0]["text"], "event": "NONE"}]
    same = m.add_facts("deploys run from the release branch", project_id=P)["results"]
    assert same == [{"id": mid, "memory": "Deploys run from the release branch", "event": "NONE"}]

    m.llm.router.decide = lambda old, facts: [{"id": old[0]["id"], "text": old[0]["text"], "event": "DELETE"},
                                              {"id": "9", "text": facts[0], "event": "ADD"}]
    out = m.add_facts("Deploys no longer run from the release branch; they are manual", project_id=P)["results"]
    assert [r["event"] for r in out] == ["DELETE", "ADD"] and m.get(mid) is None
    assert texts(m.get_all(filters={"project_id": P})) == [
        "Deploys no longer run from the release branch; they are manual"]


def test_a_model_that_omits_an_exact_duplicate_does_not_duplicate_it(tmp_path):
    m, adr_id = seeded(tmp_path, lambda old, facts: [])
    res = m.add_facts(ADR_TEXT.lower(), project_id=P)["results"]
    assert res == [{"id": adr_id, "memory": ADR_TEXT, "event": "NONE"}]
    assert len(m.get_all(filters={"project_id": P})["results"]) == 1


def test_extraction_with_nothing_to_remember_skips_reconciliation(tmp_path):
    router = FakeRouter()
    m = make(tmp_path, router)
    assert m.add("nothing to remember here", project_id=P) == {"results": []}
    assert len(router.calls) == 1  # extraction only
    assert m.db.get_last_messages(f"project_id={P}")[0]["content"] == "nothing to remember here"


def test_custom_prompts_are_used(tmp_path):
    router = FakeRouter()
    m = make(tmp_path, router, custom_fact_extraction_prompt='Extract build facts as {"facts": []} json',
             custom_update_memory_prompt="CUSTOM DECISION RULES. You are a smart memory manager.")
    m.add("We use pnpm", project_id=P)
    assert router.calls[0]["system"].startswith("Extract build facts")
    assert router.calls[1]["prompt"].startswith("CUSTOM DECISION RULES")


def test_custom_instructions_and_per_call_prompt(tmp_path):
    router = FakeRouter()
    m = make(tmp_path, router, custom_instructions="Only keep tooling facts.")
    m.add("We use pnpm", project_id=P)
    assert "Only keep tooling facts." in router.calls[0]["system"]
    m.add("We use ruff", project_id=P, prompt='Per-call rules. Return {"facts": []} json')
    assert router.calls[2]["system"].startswith("Per-call rules")


def test_add_without_model_raises_and_add_facts_falls_back(tmp_path):
    m = make(tmp_path, FakeRouter(available=False))
    with pytest.raises(LLMError):
        m.add("We use pnpm", project_id=P)
    first = m.add_facts(["We use pnpm"], project_id=P)["results"]
    again = m.add_facts("We use pnpm", project_id=P)["results"]
    assert first[0]["event"] == "ADD" and again == [{"id": first[0]["id"], "memory": "We use pnpm", "event": "NONE"}]


def test_add_facts_reconciles_and_reports_noop(tmp_path):
    router = FakeRouter()
    m = make(tmp_path, router)
    a = m.add_facts(["Tests run with pytest"], project_id=P, metadata={"kind": "convention"})["results"]
    b = m.add_facts(["tests run with pytest"], project_id=P)["results"]
    assert a[0]["event"] == "ADD" and b[0]["event"] == "NONE" and b[0]["id"] == a[0]["id"]
    assert m.get(a[0]["id"])["metadata"] == {"kind": "convention"}
    assert all("Personal Information" not in c["system"] for c in router.calls)  # no extraction pass


def test_infer_false_stores_each_message_with_role_and_actor(tmp_path):
    m = make(tmp_path, FakeRouter(available=False))
    res = m.add([{"role": "system", "content": "be terse"},
                 {"role": "user", "content": "The API gateway times out after 30s", "name": "ada"},
                 {"role": "assistant", "content": "Noted: gateway timeout is 30 seconds"}],
                project_id=P, infer=False)["results"]
    assert [(r["role"], r["actor_id"]) for r in res] == [("user", "ada"), ("assistant", None)]
    item = m.get(res[0]["id"])
    assert item["role"] == "user" and item["actor_id"] == "ada" and item["project_id"] == P


def test_additive_mode_dedupes_by_hash_and_links(tmp_path):
    m = make(tmp_path, FakeRouter(), add_mode="additive")
    first = m.add("The ledger service owns refunds", project_id=P)["results"]
    assert first[0]["event"] == "ADD"
    assert m.add("The ledger service owns refunds", project_id=P)["results"] == []  # same hash
    second = m.add("Refunds are idempotent", project_id=P)["results"]
    payload = m.vector_store.get(second[0]["id"]).payload
    assert payload["linked_memory_ids"] == [first[0]["id"]] and payload["attributed_to"] == "user"


def test_validation_errors(tmp_path):
    m = make(tmp_path)
    with pytest.raises(MemoryValidationError):
        m.add("x")
    with pytest.raises(MemoryValidationError):
        m.add("x", project_id=P, memory_type="episodic")
    with pytest.raises(MemoryValidationError):
        m.add(42, project_id=P)
    with pytest.raises(ValueError):
        m.add("x", project_id="has space")
    with pytest.raises(ValueError):
        m.search("x", filters={"kind": "decision"})
    with pytest.raises(ValueError):
        m.search("   ", filters={"project_id": P})
    with pytest.raises(ValueError):
        m.search("x", filters={"project_id": P}, threshold=2)
    with pytest.raises(ValueError):
        m.search("x", project_id=P)
    with pytest.raises(ValueError):
        m.delete_all()
    with pytest.raises(ValueError):
        m.update("missing-id", text="y")


def test_identity_keys_cannot_be_set_through_metadata(tmp_path):
    m = make(tmp_path, FakeRouter(available=False))
    mid = m.add_facts("Use UTC everywhere", project_id=P, metadata={"user_id": "mallory", "kind": "convention"})
    item = m.get(mid["results"][0]["id"])
    assert "user_id" not in item and item["metadata"] == {"kind": "convention"}
    m.update(item["id"], metadata={"project_id": "other", "kind": "decision"})
    item = m.get(item["id"])
    assert item["project_id"] == P and item["metadata"]["kind"] == "decision"


# ---- search -------------------------------------------------------------------------------------
@pytest.fixture()
def catalogue(tmp_path):
    m = make(tmp_path, FakeRouter(available=False))
    rows = [("Payments retry on timeout so charges need an idempotency key", {"kind": "gotcha", "prio": 3}),
            ("Use optimistic locking for order updates", {"kind": "convention", "prio": 2}),
            ("The read model is SQLite so it works offline", {"kind": "decision", "prio": 1}),
            ("UI copy uses sentence case and plain verbs", {"kind": "convention", "prio": 1, "area": "Frontend UI"})]
    for text, meta in rows:
        m.add(text, project_id=P, metadata=meta, infer=False)
    m.add("Payments in the other repo use Stripe", project_id="elsewhere", infer=False)
    return m


def test_search_ranks_hybrid_and_explains(catalogue):
    res = catalogue.search("idempotency key for payment charges", filters={"project_id": P}, explain=True)
    top = res["results"][0]
    assert "idempotency key" in top["memory"]
    details = top["score_details"]
    assert details["semantic_score"] > 0 and details["bm25_score"] > 0 and details["max_possible_score"] >= 2.0
    assert all(r["project_id"] == P for r in res["results"])  # scope isolation
    assert top["score"] >= res["results"][-1]["score"]


def test_search_threshold_and_top_k(catalogue):
    loose = catalogue.search("sqlite offline read model", filters={"project_id": P}, threshold=0.0, top_k=10)
    strict = catalogue.search("sqlite offline read model", filters={"project_id": P}, threshold=0.3)
    assert 1 <= len(strict["results"]) < len(loose["results"])
    assert "SQLite" in strict["results"][0]["memory"]
    assert len(catalogue.search("sqlite", filters={"project_id": P}, top_k=1, threshold=0.0)["results"]) == 1


@pytest.mark.parametrize("flt, expected", [
    ({"kind": "convention"}, {"optimistic", "sentence"}),
    ({"kind": {"ne": "convention"}}, {"idempotency", "sqlite"}),
    ({"kind": {"in": ["gotcha", "decision"]}}, {"idempotency", "sqlite"}),
    ({"kind": {"nin": ["gotcha", "decision"]}}, {"optimistic", "sentence"}),
    ({"prio": {"gte": 2}}, {"idempotency", "optimistic"}),
    ({"prio": {"lt": 2}}, {"sqlite", "sentence"}),
    ({"area": {"icontains": "frontend"}}, {"sentence"}),
    ({"area": {"contains": "Frontend"}}, {"sentence"}),
    ({"area": "*"}, {"sentence"}),
    ({"AND": [{"kind": "convention"}, {"prio": {"gt": 1}}]}, {"optimistic"}),
    ({"OR": [{"kind": "gotcha"}, {"prio": {"eq": 1}, "kind": "decision"}]}, {"idempotency", "sqlite"}),
    ({"NOT": [{"kind": "convention"}]}, {"idempotency", "sqlite"}),
])
def test_search_and_list_metadata_filters(catalogue, flt, expected):
    key = {"idempotency": "idempotency", "optimistic": "optimistic", "sqlite": "SQLite", "sentence": "sentence case"}
    got = catalogue.search("payments locking sqlite copy", filters={"project_id": P, **flt}, threshold=0.0)
    found = {k for k, needle in key.items() for t in texts(got) if needle in t}
    assert found == expected
    listed = catalogue.get_all(filters={"project_id": P, **flt})
    assert {k for k, needle in key.items() for t in texts(listed) if needle in t} == expected


def test_unsupported_filter_operator(catalogue):
    with pytest.raises(ValueError):
        catalogue.search("x", filters={"project_id": P, "kind": {"regex": "c.*"}})


def test_rerank_with_model(tmp_path):
    router = FakeRouter()
    m = make(tmp_path, router, reranker={"provider": "llm_reranker", "config": {"top_k": 2}})  # search's top_k wins
    for t in ("gateway retries need idempotency keys", "the gateway uses TLS", "cats are nice"):
        m.add(t, project_id=P, infer=False)
    res = m.search("gateway idempotency keys", filters={"project_id": P}, rerank=True, threshold=0.0)
    assert res["results"][0]["memory"] == "gateway retries need idempotency keys"
    assert "rerank_score" in res["results"][0] and len(res["results"]) == 3
    assert res["results"][0]["rerank_score"] > res["results"][-1]["rerank_score"]
    assert "memory_rerank" in router.tasks()


def test_entity_links_boost_search(tmp_path):
    m = make(tmp_path, FakeRouter(available=False))
    m.add("PaymentService.process must pass an idempotency key", project_id=P, infer=False)
    m.add("Something unrelated about the build", project_id=P, infer=False)
    ents = [r.payload for r in m.entity_store.list(filters={"project_id": P})[0]]
    assert any(e["data"] == "PaymentService.process" for e in ents)
    res = m.search("PaymentService.process", filters={"project_id": P}, explain=True, threshold=0.0)
    assert res["results"][0]["score_details"]["entity_boost"] > 0
    # deleting the memory unlinks (and drops) its entities
    m.delete(res["results"][0]["id"])
    assert not [e for e in m.entity_store.list(filters={"project_id": P})[0] if e.payload["data"] == "PaymentService.process"]


# ---- lifecycle ----------------------------------------------------------------------------------
def test_update_delete_expiration_and_timestamp(tmp_path):
    m = make(tmp_path, FakeRouter(available=False))
    rid = m.add("Old flag", project_id=P, infer=False, timestamp=1_700_000_000, expiration_date="2000-01-01")
    mid = rid["results"][0]["id"]
    item = m.get(mid)
    assert item["created_at"].startswith("2023-11-14") and item["expiration_date"] == "2000-01-01"
    assert m.get_all(filters={"project_id": P})["results"] == []  # expired is hidden
    assert len(m.get_all(filters={"project_id": P}, show_expired=True)["results"]) == 1
    assert m.search("flag", filters={"project_id": P}, threshold=0.0)["results"] == []
    m.update(mid, expiration_date=None)
    assert len(m.get_all(filters={"project_id": P})["results"]) == 1
    m.update(mid, text="New flag", metadata={"kind": "fact"})
    item = m.get(mid)
    assert item["memory"] == "New flag" and item["created_at"].startswith("2023-11-14") and item["updated_at"] > item["created_at"]
    with pytest.raises(ValueError):
        m.update(mid)
    assert m.delete(mid) == {"message": "Memory deleted successfully!"}
    with pytest.raises(ValueError):
        m.delete(mid)


def test_scopes_are_isolated_and_delete_all_is_scoped(tmp_path):
    m = make(tmp_path, FakeRouter(available=False))
    m.add("Project rule", project_id=P, infer=False)
    m.add("Ada prefers small PRs", project_id=P, user_id="ada", infer=False)
    m.add("Session note", project_id=P, run_id="s1", infer=False)
    m.add("Team rule", team_id="core", infer=False)
    assert len(m.get_all(filters={"project_id": P})["results"]) == 3
    assert texts(m.get_all(filters={"project_id": P, "user_id": "ada"})) == ["Ada prefers small PRs"]
    assert texts(m.get_all(filters={"team_id": "core"})) == ["Team rule"]
    m.delete_all(run_id="s1")
    assert len(m.get_all(filters={"project_id": P})["results"]) == 2
    m.delete_all(project_id=P)
    assert m.get_all(filters={"project_id": P})["results"] == [] and len(m.get_all(filters={"team_id": "core"})["results"]) == 1


def test_persists_across_instances_and_reset(tmp_path):
    m = make(tmp_path, FakeRouter())
    mid = m.add("Deploys run from main", project_id=P)["results"][0]["id"]
    m.close()
    again = make(tmp_path, FakeRouter(available=False))
    assert again.get(mid)["memory"] == "Deploys run from main"
    assert again.history(mid)[0]["event"] == "ADD"
    assert again.search("deploys main", filters={"project_id": P})["results"][0]["id"] == mid
    again.reset()
    assert again.get(mid) is None and again.history(mid) == []
    assert make(tmp_path, FakeRouter(available=False)).get(mid) is None


def test_two_engines_on_one_directory_keep_each_others_memories(tmp_path):
    a = make(tmp_path, FakeRouter(available=False))
    b = make(tmp_path, FakeRouter(available=False))  # e.g. the server and a CLI call
    ida = a.add("From A", project_id=P, infer=False)["results"][0]["id"]
    b.add("From B", project_id=P, infer=False)
    a.add("From A again", project_id=P, infer=False)
    for m in (a, b, make(tmp_path, FakeRouter(available=False))):
        assert set(texts(m.get_all(filters={"project_id": P}))) == {"From A", "From B", "From A again"}
        assert m.search("From B", filters={"project_id": P}, threshold=0.0)["results"][0]["memory"] == "From B"
    b.delete(ida)
    assert a.get(ida) is None
    a.add("From A, third", project_id=P, infer=False)  # a's next write must not bring the deleted one back
    assert set(texts(make(tmp_path, FakeRouter(available=False)).get_all(filters={"project_id": P}))) == {
        "From B", "From A again", "From A, third"}
    assert [h["event"] for h in a.history(ida)] == ["ADD", "DELETE"]


def test_a_batch_in_one_engine_merges_with_another_writer(tmp_path):
    a = make(tmp_path, FakeRouter(available=False))
    b = make(tmp_path, FakeRouter(available=False))
    with a.batch():
        a.add("Batched note about PaymentService", project_id=P, infer=False)
        b.add("Written meanwhile about LedgerService", project_id=P, infer=False)
        a.add("Batched note about OrderService", project_id=P, infer=False)
    fresh = make(tmp_path, FakeRouter(available=False))
    assert set(texts(fresh.get_all(filters={"project_id": P}))) == {
        "Batched note about PaymentService", "Batched note about OrderService", "Written meanwhile about LedgerService"}
    ents = {e.payload["data"] for e in fresh.entity_store.list(top_k=None)[0]}
    assert {"PaymentService", "OrderService", "LedgerService"} <= ents  # both writers' entity links survive


def test_threads_writing_through_two_engines(tmp_path):
    import threading

    a = make(tmp_path, FakeRouter(available=False))
    b = make(tmp_path, FakeRouter(available=False))

    def work(m, tag):
        for i in range(12):
            m.add(f"{tag} memory number {i}", project_id=P, infer=False)

    threads = [threading.Thread(target=work, args=(m, tag)) for m, tag in ((a, "alpha"), (b, "beta"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = texts(make(tmp_path, FakeRouter(available=False)).get_all(filters={"project_id": P}, top_k=100))
    assert len(got) == 24 and len(set(got)) == 24


def test_a_writer_in_another_process_is_seen_and_not_erased(tmp_path):
    import subprocess

    parent = make(tmp_path, FakeRouter(available=False))
    parent.add("Parent before", project_id=P, infer=False)
    script = (
        "import sys; from cairn.engines.memstore import Memory\n"
        "m = Memory.from_config({'vector_store': {'provider': 'faiss', 'config': {'path': sys.argv[1]}},"
        " 'history_db_path': sys.argv[2]})\n"
        "m.add('Child memory', project_id='shop', infer=False)\n")
    subprocess.run([sys.executable, "-c", script, str(tmp_path / "vec"), str(tmp_path / "history.db")],
                   check=True, timeout=120)
    listed = parent.get_all(filters={"project_id": P})
    assert set(texts(listed)) == {"Parent before", "Child memory"}
    child_id = next(r["id"] for r in listed["results"] if r["memory"] == "Child memory")
    assert parent.history(child_id)[0]["event"] == "ADD"  # the shared change log too
    parent.add("Parent after", project_id=P, infer=False)
    assert set(texts(make(tmp_path, FakeRouter(available=False)).get_all(filters={"project_id": P}))) == {
        "Parent before", "Child memory", "Parent after"}


def test_vectors_from_another_embedding_space_are_reembedded(tmp_path):
    m = make(tmp_path, FakeRouter(available=False))
    mid = m.add("Use optimistic locking", project_id=P, infer=False)["results"][0]["id"]
    index_file = tmp_path / "vec" / "memories" / "index.npz"

    def meta() -> dict:
        with np.load(index_file) as z:
            return json.loads(str(z["meta"]))

    with np.load(index_file) as z:
        arrays = {k: z[k] for k in z.files}
    arrays["meta"] = np.array(json.dumps({**meta(), "embedder": "some-other-model"}))
    np.savez(index_file.with_suffix(""), **arrays)  # as if written with another embedding model
    again = make(tmp_path, FakeRouter(available=False))
    assert not again.vector_store.stale
    assert meta()["embedder"] == vectors.embedder_id()
    assert again.search("optimistic locking", filters={"project_id": P})["results"][0]["id"] == mid


def test_procedural_memory(tmp_path):
    router = FakeRouter()
    m = make(tmp_path, router)
    res = m.add([{"role": "user", "content": "open the dashboard"}, {"role": "assistant", "content": "opened /dash"}],
                agent_id="builder", project_id=P, memory_type="procedural_memory")
    item = m.get(res["results"][0]["id"])
    assert item["memory"].startswith("## Summary") and item["metadata"]["memory_type"] == "procedural_memory"
    assert router.tasks() == ["memory_procedural"]


def test_chat_answers_from_memories(tmp_path):
    router = FakeRouter()
    m = make(tmp_path, router)
    m.add("The gateway timeout is 30 seconds", project_id=P, infer=False)
    answer = m.chat("what is the gateway timeout", filters={"project_id": P}, threshold=0.0)
    assert answer.startswith("ANSWER") and "gateway timeout is 30 seconds" in answer


def test_async_memory(tmp_path):
    cfg = MemoryConfig(**{"vector_store": {"provider": "faiss", "config": {"path": str(tmp_path / "vec")}},
                          "history_db_path": str(tmp_path / "h.db")})

    async def run():
        async with AsyncMemory(cfg, router=FakeRouter()) as am:
            added = await am.add("Deploys run from main", project_id=P)
            mid = added["results"][0]["id"]
            await am.update(mid, text="Deploys run from release")
            found = await am.search("deploys", filters={"project_id": P}, threshold=0.0)
            hist = await am.history(mid)
            listed = await am.get_all(filters={"project_id": P})
            await am.delete_all(project_id=P)
            return mid, found, hist, listed, await am.get(mid)

    mid, found, hist, listed, gone = asyncio.run(run())
    assert found["results"][0]["id"] == mid and [h["event"] for h in hist] == ["ADD", "UPDATE"]
    assert texts(listed) == ["Deploys run from release"] and gone is None


def test_team_server_backend_qdrant_local_mode(tmp_path, monkeypatch):
    pytest.importorskip("qdrant_client")
    from cairn.engines.memstore.vector_stores import qdrant as qdrant_store

    monkeypatch.setattr(qdrant_store.Qdrant, "_get_bm25_encoder", lambda self: None)  # offline: no sparse model
    m = Memory.from_config({"vector_store": {"provider": "qdrant", "config": {"path": str(tmp_path / "q"),
                                                                               "on_disk": True}},
                            "history_db_path": str(tmp_path / "h.db")}, router=FakeRouter())
    assert m.vector_store.embedding_model_dims == 384  # sized for the local embedder automatically
    mid = m.add("Deploys run from main", project_id=P, metadata={"kind": "decision"})["results"][0]["id"]
    m.add("Deploys run from the release branch", project_id=P, metadata={"kind": "decision"})
    assert m.get(mid)["memory"] == "Deploys run from the release branch"
    m.add("Use ruff", project_id=P, metadata={"kind": "convention"}, infer=False)
    got = m.search("deploys release", filters={"project_id": P, "kind": {"ne": "convention"}}, threshold=0.0)
    assert [r["id"] for r in got["results"]] == [mid]
    assert [h["event"] for h in m.history(mid)] == ["ADD", "UPDATE"]
    m.delete_all(project_id=P)
    assert m.get_all(filters={"project_id": P})["results"] == []
    m.entity_store.client.close()
    m.vector_store.client.close()


# ---- model adapter -------------------------------------------------------------------------------
def test_router_llm_json_retry_tools_and_messages():
    router = FakeRouter(bad_json_first=1)
    llm = RouterLLM({"router": router, "task": "memory"})
    out = llm.generate_response([{"role": "system", "content": 'Return {"facts": []}'},
                                 {"role": "user", "content": "Input:\nuser: We use pnpm"}],
                                response_format={"type": "json_object"})
    assert json.loads(out) == {"facts": ["We use pnpm"]} and len(router.calls) == 2
    tools = llm.generate_response([{"role": "user", "content": "find x"}],
                                  tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}])
    assert tools["tool_calls"] == [{"name": "lookup", "arguments": {"q": "find x"}}]
    system, prompt = render_messages([{"role": "system", "content": "S"}, {"role": "user", "content": "hi"},
                                      {"role": "assistant", "content": [{"type": "text", "text": "yo"}]},
                                      {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "u"}}]}])
    assert system == "S" and prompt == "user: hi\n\nassistant: yo\n\nuser: [image: u]"
    assert not RouterLLM({"router": FakeRouter(available=False)}).available
    with pytest.raises(LLMError):
        RouterLLM({"router": FakeRouter(available=False)}).generate_response([{"role": "user", "content": "x"}])
    tiered = RouterLLM({"router": router, "model": "fast"})
    tiered.generate_response([{"role": "user", "content": "x"}])
    assert router.calls[-1]["tier"] == "fast"


def test_filter_language():
    p = {"kind": "decision", "tags": ["db", "perf"], "n": 5, "title": "Use SQLite"}
    assert matches(p, {"tags": "db"}) and matches(p, {"tags": {"in": ["perf", "x"]}})
    assert matches(p, {"n": {"gt": 4, "lte": 5}}) and not matches(p, {"n": {"gt": 5}})
    assert matches(p, {"missing": {"ne": "x"}}) and not matches(p, {"missing": "x"})
    assert matches(p, {"$or": [{"kind": "x"}, {"title": {"icontains": "sqlite"}}]})
    assert not matches(p, {"$not": [{"kind": "decision"}]})
    assert matches(p, {"kind": ["decision", "fact"]}) and matches(p, {"title": "*"}) and not matches(p, {"none": "*"})


# ---- surfaces: proxy, HTTP handlers, CLI handlers ----------------------------------------------------
def test_chat_proxy_is_openai_compatible_and_remembers(tmp_path):
    router = FakeRouter()
    m = make(tmp_path, router)
    m.add("The gateway timeout is 30 seconds", project_id=P, infer=False)
    proxy = ChatProxy(memory=m)
    resp = proxy.chat.completions.create(model="fast", messages=[{"role": "user", "content": "gateway timeout?"}],
                                         project_id=P, temperature=0.2)
    proxy.chat.completions.wait(10)
    assert resp["object"] == "chat.completion" and resp["choices"][0]["message"]["role"] == "assistant"
    content = resp["choices"][0]["message"]["content"]
    assert "gateway timeout is 30 seconds" in content and "User Question: gateway timeout?" in content
    chat_call = [c for c in router.calls if c["task"] == "memory_chat"][0]
    assert chat_call["tier"] == "fast" and "expert at answering questions" in chat_call["system"]
    assert "gateway timeout?" in texts(m.get_all(filters={"project_id": P}))  # the question was remembered
    with pytest.raises(ValueError):
        proxy.chat.completions.create(messages=[{"role": "user", "content": "x"}])
    status = api.chat_completions(proxy, {"messages": [{"role": "user", "content": "hi"}], "project_id": P})
    assert status["choices"]
    proxy.chat.completions.wait(10)


def test_http_handlers(tmp_path):
    m = make(tmp_path, FakeRouter())
    added = api.add_memories(m, {"messages": [{"role": "user", "content": "We use pnpm"}], "project_id": P,
                                 "metadata": {"kind": "convention"}})
    mid = added["results"][0]["id"]
    with pytest.raises(api.ApiError) as e:
        api.add_memories(m, {"messages": [{"role": "user", "content": "x"}]})
    assert e.value.status == 400
    with pytest.raises(api.ApiError) as e:
        api.add_memories(m, {"messages": "nope"})
    assert e.value.status == 422
    assert api.get_memory(m, mid)["memory"] == "We use pnpm"
    with pytest.raises(api.ApiError) as e:
        api.get_memory(m, "nope")
    assert e.value.status == 404
    assert texts(api.list_memories(m, project_id=P)) == ["We use pnpm"]
    with pytest.raises(api.ApiError) as e:
        api.list_memories(m)
    assert e.value.status == 403
    assert api.list_memories(m, admin=True)["results"][0]["project_id"] == P
    assert api.search_memories(m, {"query": "pnpm", "project_id": P, "threshold": 0.0})["results"][0]["id"] == mid
    api.update_memory(m, mid, {"text": "We use pnpm 9"})
    assert [h["event"] for h in api.memory_history(m, mid)] == ["ADD", "UPDATE"]
    with pytest.raises(api.ApiError) as e:
        api.update_memory(m, "missing", {"text": "x"})
    assert e.value.status == 404
    ents = api.list_entities(m)
    assert {"id": P, "type": "project"}.items() <= ents[0].items() and ents[0]["total_memories"] == 1
    cfg = api.get_configuration(m)
    assert cfg["vector_store"]["provider"] == "faiss" and cfg["llm"]["config"]["router"].startswith("<")
    gen = api.generate_instructions(m, {"use_case": "a build tool"})
    assert gen == {"custom_instructions": "remember build tooling choices", "test_message": "We use pnpm."}
    assert api.delete_memory(m, mid)["message"]
    with pytest.raises(api.ApiError):
        api.delete_all_memories(m)
    api.add_memories(m, {"messages": [{"role": "user", "content": "A"}], "project_id": P, "infer": False})
    api.delete_entity(m, "project", P)
    assert api.list_memories(m, project_id=P)["results"] == []
    assert api.reset_memories(m) == {"message": "All memories reset"}


def test_http_handlers_need_a_model_for_inference(tmp_path):
    m = make(tmp_path, FakeRouter(available=False))
    with pytest.raises(api.ApiError) as e:
        api.add_memories(m, {"messages": [{"role": "user", "content": "x"}], "project_id": P})
    assert e.value.status == 503


def test_cli_handlers(tmp_path):
    m = make(tmp_path, FakeRouter(available=False))
    commands.add(m, "We use pnpm, never npm", infer=False, project_id=P, metadata='{"kind": "convention"}')
    commands.add(m, messages='[{"role": "user", "content": "Tests run with pytest"}]', infer=False, project_id=P)
    with pytest.raises(commands.CommandError):
        commands.add(m, "x")
    with pytest.raises(commands.CommandError):
        commands.add(m, "x", metadata="{bad", project_id=P)
    kw = commands.search(m, "pnpm", keyword=True, project_id=P)
    assert kw["results"][0]["memory"].startswith("We use pnpm")
    hybrid = commands.search(m, "pytest", project_id=P, filters='{"kind": {"ne": "convention"}}', threshold=0.0)
    assert texts(hybrid) == ["Tests run with pytest"]
    page = commands.list_memories(m, page=2, page_size=1, project_id=P)
    assert len(page["results"]) == 1 and page["page"] == 2
    data = tmp_path / "import.json"
    data.write_text(json.dumps([{"memory": "Use ruff", "metadata": {"kind": "convention"}}, {"text": ""},
                                {"content": "Owned by team core", "team_id": "core"}]))
    assert commands.import_file(m, str(data), project_id=P) == {"added": 2, "failed": 1, "errors": []}
    listed = commands.list_memories(m, project_id=P)["results"]
    assert len(listed) == 4
    dry = commands.delete(m, all_=True, dry_run=True, project_id=P)
    assert len(dry["would_delete"]) == 4
    one = listed[0]["id"]
    commands.update(m, one, "Use ruff for linting")
    assert [h["event"] for h in commands.history(m, one)] == ["ADD", "UPDATE"]
    with pytest.raises(commands.CommandError):
        commands.update(m, one)
    commands.delete(m, one)
    with pytest.raises(commands.CommandError):
        commands.delete(m)
    commands.delete(m, all_=True, project_id=P)
    assert commands.list_memories(m, project_id=P)["results"] == []
    assert commands.entities(m) == []


# ---- graph hook -----------------------------------------------------------------------------------
class FakeTemporalService:
    groups: dict = {}

    def __init__(self, project, router, brain=None, **kw):
        self.available = True

    async def add_episode(self, name, body, **kw):
        FakeTemporalService.groups.setdefault(kw["group_id"], []).append(body)
        fact = {"uuid": f"f{len(FakeTemporalService.groups[kw['group_id']])}", "name": "USES", "fact": body,
                "source_node_uuid": "n1", "target_node_uuid": "n2"}
        return {"nodes": [{"uuid": "n1", "name": "shop"}, {"uuid": "n2", "name": "pnpm"}], "facts": [fact],
                "invalidated": []}

    async def search_facts(self, query, group_ids, max_facts=10):
        return [{"uuid": "f1", "name": "USES", "fact": b, "source_node_uuid": "n1", "target_node_uuid": "n2"}
                for g in group_ids for b in FakeTemporalService.groups.get(g, [])][:max_facts]

    async def list_facts(self, group_id, limit=100):
        return await self.search_facts("", [group_id], limit)

    async def delete_group(self, group_id):
        FakeTemporalService.groups.pop(group_id, None)


def test_graph_memory_goes_through_the_temporal_graph(tmp_path, monkeypatch):
    import asyncio as _asyncio

    fake = types.ModuleType("cairn.engines.temporal")
    fake.TemporalService = FakeTemporalService
    fake.run_sync = _asyncio.run
    monkeypatch.setitem(sys.modules, "cairn.engines.temporal", fake)
    FakeTemporalService.groups = {}
    router = FakeRouter()
    router.project = object()
    m = make(tmp_path, router, graph_store={"provider": "temporal", "enabled": True})
    res = m.add("We use pnpm", project_id=P)
    assert res["relations"]["added_entities"][0] == {"source": "shop", "relationship": "USES", "destination": "pnpm",
                                                      "fact": "We use pnpm", "uuid": "f1"}
    [gid] = FakeTemporalService.groups
    assert gid.startswith("mem_project-shop_")
    found = m.search("pnpm", filters={"project_id": P}, threshold=0.0)
    assert found["relations"][0]["fact"] == "We use pnpm"
    assert m.get_all(filters={"project_id": P})["relations"]
    m.delete_all(project_id=P)
    assert FakeTemporalService.groups == {}


def test_graph_memory_degrades_without_the_temporal_graph(tmp_path):
    m = make(tmp_path, FakeRouter(), graph_store={"provider": "temporal", "enabled": True})  # router has no project
    res = m.add("We use pnpm", project_id=P)
    assert res["results"][0]["event"] == "ADD" and res["relations"] == {"deleted_entities": [], "added_entities": []}
    assert m.search("pnpm", filters={"project_id": P})["relations"] == []


# ---- naming ---------------------------------------------------------------------------------------
def test_no_upstream_names_in_memory_sources():
    root = Path(__file__).resolve().parents[2]
    words = ["graph" + "ify", "spec-" + "kit", "spec" + "kit", "spec " + "kit", "specify" + "_cli", "specify" + "-cli",
             "specify " + "cli", ".spec" + "ify", "claude" + "-mem", "claude" + "_mem", "the" + "dotmack",
             "graph" + "iti", "me" + "m0"]
    import re as _re
    pattern = _re.compile("|".join(_re.escape(w) for w in words) + r"|\bcm" + r"em\b|\bze" + r"p\b", _re.I)
    files = list((root / "src/cairn/engines/memstore").rglob("*.py")) + [
        root / "src/cairn/engines/memory.py", root / "src/cairn/engines/vectors.py",
        *Path(__file__).parent.glob("test_memstore*.py"), Path(__file__).parent / "test_vectors.py"]
    hits = [f"{f.relative_to(root)}:{i}" for f in files for i, line in enumerate(f.read_text().splitlines(), 1)
            if pattern.search(line)]
    assert hits == []

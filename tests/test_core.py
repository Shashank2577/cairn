"""Store, history, specs, drift, memory, linker and the context assembler."""
from __future__ import annotations

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


def test_context_infers_targets(cairn):
    p = cairn.context("change how PaymentService handles retries", budget=800)
    assert p.data["targets"]
    assert "PaymentService" in p.render()


def test_brief_is_small(cairn):
    b = cairn.brief()
    assert len(b) // 4 <= 560 and "Cairn brief" in b

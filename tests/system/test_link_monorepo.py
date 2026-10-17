"""One repository, several deploy units, scored against ground truth written before extraction (T002,
T021, FR-006, FR-013, SC-001, SC-002).

The committed ``monorepo`` fixture is the edge case the plan names and the only fixture that is both
multi-unit and offline: the voting app covers it too, but only over the network. Its three units are
built from sibling directories by one compose file, in three languages, so every rule that has to map a
file to a container runs here.
"""
from __future__ import annotations

from pathlib import Path

from cairn.system.link import link
from cairn.system.views import view

from .scoring import load_truth, score, summary
from .util import FIXTURES, aliases, copy_fixture, store

RECALL, PRECISION, LABELLED = 0.90, 0.95, 0.10


def scored(root: Path, name: str) -> tuple[dict, dict]:
    pm = link(root)
    v = view(root, "container", model=pm)
    s = score(v, load_truth(FIXTURES / name / "GROUND_TRUTH.yaml"), aliases(pm))
    print("\n" + summary(name, s))
    return v, s


def test_monorepo_containers_view_matches_ground_truth(tmp_path):
    """FR-006, FR-013, SC-001: three deploy units from one repository, each in its own language,
    linked by the same rules as across repositories, with evidence on every claim."""
    root = copy_fixture(FIXTURES / "monorepo", tmp_path / "monorepo")
    store(root)
    v, s = scored(root, "monorepo")
    assert s["element_recall"] >= RECALL and s["relationship_recall"] >= RECALL, s
    assert s["element_precision"] >= PRECISION and s["relationship_precision"] >= PRECISION, s
    assert s["labelled_share"] <= LABELLED, s
    for x in v["elements"] + v["relationships"]:
        assert x["evidence"] and all(isinstance(e, str) and e for e in x["evidence"]), x


def test_each_deploy_unit_is_its_own_container_with_its_own_routes(tmp_path):
    """FR-006: containers are deploy units, not repositories; each keeps the routes it declares."""
    root = copy_fixture(FIXTURES / "monorepo", tmp_path / "monorepo")
    store(root)
    m = link(root)
    by_parent: dict[str, set[str]] = {}
    for e in m.elements.values():
        if e.kind == "route":
            by_parent.setdefault(e.parent, set()).add(e.name)
    names = {e.id: e.name for e in m.elements.values()}
    assert {names[p]: sorted(r) for p, r in by_parent.items()} == {
        "gateway": ["GET /health", "POST /checkout/invoices"],
        "billing": ["GET /invoices/{}", "POST /invoices"],
    }


def test_the_gateway_call_reaches_billing_by_route_match_inside_one_repository(tmp_path):
    """FR-013: the same route-match rule links a call and a route inside one repository."""
    root = copy_fixture(FIXTURES / "monorepo", tmp_path / "monorepo")
    store(root)
    m = link(root)
    names = {e.id: e.name for e in m.elements.values()}
    rels = {(names[r.from_id], names[r.to_id]): r for r in m.relationships.values()
            if r.rule == "http-route-match"}
    assert ("gateway", "billing") in rels
    assert rels[("gateway", "billing")].what == "Creates invoices"
    assert rels[("gateway", "billing")].provenance == "extracted"


def test_the_compose_file_does_not_leak_its_configuration_values(tmp_path):
    """FR-011, FR-029b: names are recorded; the Stripe key's value never is."""
    root = copy_fixture(FIXTURES / "monorepo", tmp_path / "monorepo")
    store(root)
    m = link(root)
    text = repr([e.meta for e in m.elements.values()] + [r.meta for r in m.relationships.values()])
    assert "sk_live_example" not in text
    assert "DATABASE_URL" in text and "BILLING_URL" in text
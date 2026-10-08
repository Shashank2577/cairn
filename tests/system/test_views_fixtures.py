"""Golden tests: computed Containers views of the fixture products scored against ground truth written
before extraction (SC-001, SC-002, FR-001 to FR-015, US1 acceptance 1-3; T002, T023)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cairn.system.diagram.check import check
from cairn.system.diagram.schema import loads as load_description
from cairn.system.link import link
from cairn.system.views import view

from .fixtures.fetch import voting_app
from .scoring import load_truth, score, summary
from .util import FIXTURES, aliases, copy_fixture, product, store

RECALL, PRECISION, LABELLED = 0.90, 0.95, 0.10


def _scored(root: Path, truth: Path, name: str) -> tuple[dict, dict]:
    pm = link(root)
    v = view(root, "container", model=pm)
    s = score(v, load_truth(truth), aliases(pm))
    print("\n" + summary(name, s))
    return v, s


def _assert_sc001(s: dict) -> None:
    assert s["element_recall"] >= RECALL, s
    assert s["relationship_recall"] >= RECALL, s
    assert s["element_precision"] >= PRECISION, s
    assert s["relationship_precision"] >= PRECISION, s
    assert s["labelled_share"] <= LABELLED, s


def _assert_evidence(v: dict) -> None:
    """SC-002: every element and relationship opens at least one evidence reference."""
    for x in v["elements"] + v["relationships"]:
        assert x["evidence"] and all(isinstance(e, str) and e for e in x["evidence"]), x


def test_shop_containers_view_matches_ground_truth(tmp_path):
    """SC-001 on the four-repository shop: every container, store, channel, outside service, library and
    relationship of the written design, nothing else, each with evidence."""
    root = product(tmp_path, "shop", "shop-api", ["shop-web", "shop-api", "shop-worker", "shop-contracts"])
    v, s = _scored(root, FIXTURES / "shop" / "GROUND_TRUTH.yaml", "shop")
    _assert_sc001(s)
    assert s["element_recall"] == 1.0 and s["relationship_recall"] == 1.0
    _assert_evidence(v)
    kinds = {e["name"]: e.get("kind") for e in v["elements"]}
    assert kinds["shop-web"] == "web-app" and kinds["shop-api"] == "service" and kinds["shop-worker"] == "worker"
    by_pair = {(r["from"].split(":")[-1], r["to"].split(":")[-1]): r for r in v["relationships"]}
    web_api = by_pair[("shop-web", "shop-api")]
    assert web_api["what"] == "Creates and reads orders" and "POST /api/orders" in web_api["how"]
    assert by_pair[("shop-api", "redis")]["what"] == "Publishes order.created"
    assert by_pair[("shop-worker", "redis")].get("rank_reverse") is True
    assert by_pair[("shop-api", "db")]["what"] == "Reads and writes orders"
    assert by_pair[("shop-api", "shop-contracts")]["style"] == "build"


def test_orders_spring_and_go_view_matches_ground_truth(tmp_path):
    """SC-001 on the Java/Spring + Go fixture (written for this build, ground truth first): RestTemplate to
    net/http routes, database/sql with pgx, kafka-go producer, Spring Kafka listener."""
    root = product(tmp_path, "orders", "checkout-spring", ["checkout-spring", "inventory-go"])
    v, s = _scored(root, FIXTURES / "orders" / "GROUND_TRUTH.yaml", "orders")
    _assert_sc001(s)
    assert s["element_recall"] == 1.0 and s["relationship_recall"] == 1.0
    _assert_evidence(v)
    names = {e["name"] for e in v["elements"]}
    assert "legacy-api" not in names and "Memcached" not in names  # docs/ example compose ignored


def test_voting_app_monorepo_view(tmp_path):
    """SC-001 on a real open-source product (dockersamples/example-voting-app at a pinned commit; ground
    truth committed before extraction). One repository, several deploy units (monorepo edge case): vote,
    result, worker and seed are separate containers linked by the same rules as across repositories."""
    src = voting_app()
    root = copy_fixture(src, tmp_path / "example-voting-app", git=False)
    store(root)
    v, s = _scored(root, FIXTURES / "voting-app" / "GROUND_TRUTH.yaml", "voting-app")
    _assert_sc001(s)
    _assert_evidence(v)
    containers = sorted(e["name"] for e in v["elements"] if e["type"] == "container")
    assert containers == ["result", "seed", "vote", "worker"]
    kinds = {e["name"]: e.get("kind") for e in v["elements"]}
    assert kinds["worker"] == "worker" and kinds["seed"] == "job"
    assert kinds["vote"] == "web-app" and kinds["result"] == "web-app"
    problems = check(load_description(json.dumps(v)), roots={"example-voting-app": root})
    assert problems == [], [str(p) for p in problems]  # SC-006, evidence resolved in the real checkout


def test_ground_truth_files_are_not_derived_from_output():
    """SC-001: T002: each ground truth states it was written before extraction ran."""
    for name in ("shop", "orders", "voting-app"):
        text = (FIXTURES / name / "GROUND_TRUTH.yaml").read_text(encoding="utf-8")
        assert "before" in text and "extraction" in text


@pytest.mark.parametrize("missing", ["directory", "brain"])
def test_named_repository_not_set_up_is_drawn_as_not_mapped(tmp_path, missing):
    """US1-AS3, FR-015: US1 acceptance 3 and the 'missing on disk' edge case: a box marked not mapped yet / not found, with
    the command that maps it, and no guessed relationships."""
    root = product(tmp_path, "shop", "shop-api", ["shop-api", "shop-web"],
                   system_yaml="system: Shop\nrepos:\n  - path: shop-web\n  - path: shop-worker\n")
    if missing == "brain":
        copy_fixture(FIXTURES / "shop" / "shop-worker", tmp_path / "shop-worker")
    v = view(root, "container")
    box = next(e for e in v["elements"] if e["name"] == "shop-worker")
    assert box["provenance"] == "inferred"
    assert ("not found" if missing == "directory" else "not mapped yet") in box["desc"]
    if missing == "brain":
        assert "cairn init" in box["desc"]
    assert not [r for r in v["relationships"] if box["id"] in (r["from"], r["to"])]
    assert "Not mapped" in v["description"]

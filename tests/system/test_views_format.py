"""View queries (T022): the diagram description format of docs/diagram-standard/README.md section 9, one
level per view, budgets with collapsing, scoping, the context view, and the standard check (SC-006)."""
from __future__ import annotations

import json

import pytest
import yaml
from typer.testing import CliRunner

from cairn.cli import app
from cairn.system.diagram.check import check
from cairn.system.diagram.schema import loads as load_description
from cairn.system.link import link
from cairn.system.views import budget, view

from .util import FIXTURES, product, repo, store

KEYS = {"diagram", "title", "scope", "description", "elements", "boundaries", "relationships"}


@pytest.fixture()
def shop(tmp_path):
    return product(tmp_path, "shop", "shop-api", ["shop-web", "shop-api", "shop-worker", "shop-contracts"])


def test_containers_view_has_the_standard_shape(shop):
    """US1-AS1, US1-AS2, FR-001, FR-002, FR-003, FR-004: Section 9: diagram, title, scope, description, elements, boundaries, relationships; evidence as
    reference strings; provenance on every claim; rank_reverse on consumer edges; one level only."""
    v = view(shop, "container")
    assert KEYS <= set(v) and v["diagram"] == "container" and v["title"] == "Containers of Shop"
    assert "0 model calls" in v["scope"] and "commit " in v["scope"]
    for e in v["elements"]:
        assert {"id", "name", "type", "desc", "provenance", "evidence"} <= set(e)
        assert e["type"] not in ("code", "component")
        assert all(isinstance(x, str) for x in e["evidence"])
    for r in v["relationships"]:
        assert {"from", "to", "style", "provenance", "evidence", "rule"} <= set(r) and r.get("how")
    consumers = [r for r in v["relationships"] if r["rule"] in ("pubsub-topic", "queue-key")
                 and r.get("what", "").startswith(("Subscribes", "Consumes"))]
    assert consumers and all(r["rank_reverse"] for r in consumers)
    b = v["boundaries"][0]
    assert b["type"] == "software system" and "actor:customer" not in b["contains"]
    assert not any("sendgrid" in c for c in b["contains"])


def _roots(tmp_path, names):
    return {n: tmp_path / n for n in names}


@pytest.mark.parametrize("level", ["container", "context"])
def test_computed_views_pass_the_standard_check(shop, tmp_path, level):
    """SC-006 for computed views: the description loads with the shared schema and the checker reports no
    violation, evidence references resolved against the repositories' working trees."""
    v = view(shop, level)
    desc = load_description(json.dumps(v))
    problems = check(desc, roots=_roots(tmp_path, ["shop-web", "shop-api", "shop-worker", "shop-contracts"]))
    assert problems == [], [str(p) for p in problems]


def test_orders_view_passes_the_standard_check(tmp_path):
    """SC-006, FR-003."""
    root = product(tmp_path, "orders", "checkout-spring", ["checkout-spring", "inventory-go"])
    v = view(root, "container")
    problems = check(load_description(json.dumps(v)), roots=_roots(tmp_path, ["checkout-spring", "inventory-go"]))
    assert problems == [], [str(p) for p in problems]


def test_context_view(shop):
    """FR-001: The product as one box with its people and outside systems; no technology on context arrows."""
    v = view(shop, "context")
    types = {e["type"] for e in v["elements"]}
    assert types == {"person", "system", "external-system"} and v["title"].startswith("System context")
    assert all("how" not in r for r in v["relationships"])
    pairs = {(r["from"], r["to"]) for r in v["relationships"]}
    assert ("actor:customer", "system:shop") in pairs and ("system:shop", "shop-worker:external:sendgrid") in pairs


def test_budget_collapses_and_says_so(tmp_path):
    """FR-001: Very large products: beyond the token file's node budget, the least connected elements collapse
    into one '+N more' element, relationships are redirected, and the scope line says so."""
    units = {f"svc{i:02d}": {"Dockerfile": "FROM x\n", "a.py": f"import redis\nredis.Redis(host='bus').publish('t{i}', 1)\n"}
             for i in range(16)}
    files = {f"{u}/{k}": v for u, fs in units.items() for k, v in fs.items()}
    files["docker-compose.yml"] = "services:\n" + "".join(f"  {u}:\n    build: ./{u}\n" for u in units) + \
        "  bus:\n    image: redis\n"
    root = repo(tmp_path, "big", files)
    store(root)
    v = view(root, "container")
    assert len(v["elements"]) <= budget()
    group = next(e for e in v["elements"] if e["id"] == "collapsed:more")
    assert group["name"] == f"+{group['count']} more" and group["count"] == 17 - (budget() - 1)
    assert "collapsed" in v["scope"]
    ids = {e["id"] for e in v["elements"]}
    assert all(r["from"] in ids and r["to"] in ids for r in v["relationships"])
    assert len(v["relationships"]) <= budget("relationships", 16)


def test_scope_to_one_container_and_its_neighbours(shop):
    """FR-001."""
    v = view(shop, "container", scope="shop-worker")
    names = {e["name"] for e in v["elements"]}
    assert names == {"shop-worker", "Redis", "SendGrid", "shop-contracts"}
    assert "scoped to shop-worker" in v["scope"]
    with pytest.raises(ValueError):
        view(shop, "container", scope="nope")


def test_unknown_level_is_an_error(shop):
    """FR-001."""
    with pytest.raises(ValueError):
        view(shop, "components")


def test_single_repository_product_without_system_yaml(tmp_path):
    """FR-001, FR-012: Edge case: without system.yaml the product is the repository; Context and Containers still work."""
    root = repo(tmp_path, "solo", {"Dockerfile": "FROM x\n", "a.py": "import stripe\nstripe.Charge.create(amount=1)\n"})
    v = view(root, "container")
    assert v["title"] == "Containers of solo" and "1 repository" in v["scope"]
    assert {e["name"] for e in v["elements"]} == {"solo", "Stripe"}
    c = view(root, "context")
    assert {e["type"] for e in c["elements"]} == {"system", "external-system"}


def test_cli_view_json_and_out(shop, monkeypatch, tmp_path):
    """US1-AS1, FR-001: `cairn system --view containers --json` and `--out FILE.yaml` (T022); the old listing still works."""
    monkeypatch.chdir(shop)
    runner = CliRunner()
    res = runner.invoke(app, ["system", "--view", "containers", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["title"] == "Containers of Shop"
    out = tmp_path / "shop.yaml"
    res = runner.invoke(app, ["system", "--view", "context", "--out", str(out)])
    assert res.exit_code == 0 and yaml.safe_load(out.read_text(encoding="utf-8"))["diagram"] == "context"
    res = runner.invoke(app, ["system", "--view", "containers"])
    assert res.exit_code == 0 and "shop-web → shop-api" in res.output and "[package shop-contracts]" in res.output
    assert runner.invoke(app, ["system", "--view", "flows"]).exit_code == 2
    assert runner.invoke(app, ["system", "--view", "containers", "--scope", "nope"]).exit_code == 2
    res = runner.invoke(app, ["system"])
    assert res.exit_code == 0 and "Shop" in res.output


def test_view_reads_the_stored_model_without_writing(shop):
    """FR-015."""
    db = shop / ".cairn" / "brain.db"
    before = db.stat().st_mtime_ns
    link(shop)
    view(shop, "container")
    assert db.stat().st_mtime_ns == before


def test_fixture_views_are_stable_between_runs(tmp_path):
    """FR-005: Deterministic: the same repositories give the same description."""
    a = product(tmp_path / "a", "shop", "shop-api", ["shop-web", "shop-api", "shop-worker", "shop-contracts"])
    b = product(tmp_path / "b", "shop", "shop-api", ["shop-web", "shop-api", "shop-worker", "shop-contracts"])
    va, vb = view(a, "container"), view(b, "container")
    strip = lambda v: json.dumps({k: v[k] for k in ("elements", "relationships", "boundaries")}, sort_keys=True)  # noqa: E731
    assert strip(va).replace("commit", "") == strip(vb).replace("commit", "")
    assert (FIXTURES / "shop" / "GROUND_TRUTH.yaml").is_file()

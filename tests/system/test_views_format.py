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
from cairn.system.views import LEVEL_NAMES, LEVELS, budget, view

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
    """FR-001, FR-024: the five levels are the only ones; anything else is a message, not a crash."""
    with pytest.raises(ValueError, match="unknown level"):
        view(shop, "landscape")
    with pytest.raises(ValueError, match="unknown level"):
        view(shop, "engine")


def test_every_level_is_one_of_the_five_the_map_offers():
    """FR-024, T022: Context, Containers, Components, Flows and Code, and nothing else."""
    assert LEVEL_NAMES == ("context", "containers", "components", "flows", "code")
    assert set(LEVELS.values()) == {"context", "container", "component", "flow", "code"}
    assert {LEVELS[n] for n in LEVEL_NAMES} == set(LEVELS.values())
    assert LEVELS["container"] == LEVELS["containers"] and LEVELS["component"] == LEVELS["components"]
    assert LEVELS["flow"] == LEVELS["flows"]


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
    assert runner.invoke(app, ["system", "--view", "landscape"]).exit_code == 2
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


def test_the_code_view_lists_one_box_per_place_in_the_code(shop):
    """FR-024, T022: Code is where a reader drills in; every box is a claim the model made, with its evidence,
    and two deploy units that expose the same route do not produce the same box twice."""
    v = view(shop, "code", scope="shop-api")
    assert v["diagram"] == "code" and v["title"] == "Code of shop-api" and "scoped to shop-api" in v["scope"]
    assert len(v["elements"]) <= budget()
    names = [e["name"] for e in v["elements"]]
    assert "POST /api/orders" in names and len(names) == len(set(names))
    for e in v["elements"]:
        assert e["type"] == "code" and e["evidence"] and e["provenance"]
    problems = check(load_description(json.dumps(v)),
                     roots={"shop-api": shop.parent / "shop-api", "shop-web": shop.parent / "shop-web"})
    assert problems == [], [str(p) for p in problems]


def test_the_code_view_without_a_scope_covers_the_product(shop):
    """FR-024: with no scope the Code view is every code-level claim the product's model holds."""
    v = view(shop, "code")
    assert v["title"].startswith("Code of ") and len(v["elements"]) >= 2
    assert {e["kind"] for e in v["elements"]} <= {"route", "http-call", "deploy-ref"}
    with pytest.raises(ValueError):
        view(shop, "code", scope="nope")


def test_the_five_levels_each_return_one_description(shop):
    """FR-024, T022: every level answers with a diagram description a renderer and the checker take."""
    for level in LEVEL_NAMES:
        if level == "components":
            continue                       # needs the code map; its own tests cover it
        v = view(shop, level)
        assert v["diagram"] == level or (level, v["diagram"]) in (("containers", "container"),
                                                                    ("flows", "flow"))
        assert {"title", "scope", "description", "elements"} <= set(v)
        assert "0 model calls" in v["scope"]


def test_budget_reads_the_token_file_and_survives_a_broken_one(tmp_path, monkeypatch):
    """T022: the views and the checker read one budget block, so they cannot disagree."""
    import cairn.system.views as views_mod
    assert budget("nodes_soft") == 12 and budget("relationships") == 16
    assert budget("label_what_chars") == 32 and budget("sequence_messages") == 12
    assert budget("accents_min") == 0
    monkeypatch.setattr(views_mod, "TOKENS", tmp_path / "missing.json")
    assert budget("nodes_soft", 7) == 7 and budget("nodes_soft") == views_mod.DEFAULTS["nodes_soft"]


def test_the_cli_draws_every_level(shop, monkeypatch, tmp_path):
    """T022: `cairn system --view context|containers|flows|code` all answer; an unknown level exits 2."""
    monkeypatch.chdir(shop)
    runner = CliRunner()
    for level in ("context", "containers", "flows", "code"):
        res = runner.invoke(app, ["system", "--view", level])
        assert res.exit_code == 0, (level, res.output)
    res = runner.invoke(app, ["system", "--view", "flows", "--json"])
    assert res.exit_code == 0 and json.loads(res.output)["diagram"] == "flow"
    out = tmp_path / "flow.yaml"
    res = runner.invoke(app, ["system", "--view", "flows", "--scope", "POST /api/orders", "--out", str(out)])
    assert res.exit_code == 0 and yaml.safe_load(out.read_text(encoding="utf-8"))["diagram"] == "flow"
    assert runner.invoke(app, ["system", "--view", "landscape"]).exit_code == 2
    assert runner.invoke(app, ["system", "--view", "flows", "--scope", "nope"]).exit_code == 2


def test_every_budget_key_of_the_token_file_is_honoured(shop):
    """T022: nodes_soft and relationships drive collapsing, boundaries and accents cap what is drawn, the label
    budgets clip, and the sequence budgets bound the flow views. A view never carries its own numbers."""
    from cairn.system.views import fit, fit_accents, fit_boundaries
    assert budget("boundaries") == 3 and budget("accents") == 2
    assert fit_boundaries([{"id": str(i)} for i in range(9)]) == [{"id": str(i)} for i in range(3)]
    focused = [{"id": str(i), "name": str(i), "focus": True} for i in range(5)]
    assert sum(1 for e in fit_accents(focused) if e["focus"]) <= budget("accents")
    assert not any(e.get("focus") for e in fit_accents([{"id": "a", "name": "a"}]))
    many = [{"id": str(i), "name": f"n{i}", "type": "container", "desc": "d", "provenance": "extracted",
             "evidence": ["x"], "tech": "t"} for i in range(40)]
    els, rels, collapsed, dropped = fit(many, [])
    assert len(els) <= budget() and collapsed == 40 - (budget() - 1)
    assert dropped == 0 and rels == []


def test_the_focus_accent_is_within_budget(shop, tmp_path):
    """T022, FR-027: the components view marks the biggest part, and never more accents than the budget."""
    from cairn.engines import mapper
    for n in ("shop-web", "shop-api", "shop-worker", "shop-contracts"):
        mapper.build(shop.parent / n, force=True)
    v = view(shop, "components")
    assert sum(1 for e in v["elements"] if e.get("focus")) <= budget("accents")
    assert any(e.get("focus") for e in v["elements"])
    assert check(load_description(json.dumps(v)),
                 roots={n: shop.parent / n for n in
                        ("shop-web", "shop-api", "shop-worker", "shop-contracts")}) == []


def test_the_new_levels_lay_out_and_render(shop):
    """T022, FR-024: Components, Flows and Code are drawn by the same renderer as the rest, so the renderer's
    layout self-test (overlapping labels, labels over boxes) reports nothing for them either."""
    from cairn.engines import mapper
    from cairn.system.diagram import layout, svg
    for n in ("shop-web", "shop-api", "shop-worker", "shop-contracts"):
        mapper.build(shop.parent / n, force=True)
    for level, scope in (("components", "shop-api"), ("flows", "POST /api/orders"), ("code", "shop-api")):
        geometry = layout.layout(load_description(json.dumps(view(shop, level, scope=scope))))
        assert geometry["kind"] == ("component", "flow", "code")[("components", "flows", "code").index(level)]
        assert layout.self_test(geometry) == [], level
        assert "<svg" in svg.render(load_description(json.dumps(view(shop, level, scope=scope))))

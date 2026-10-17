"""Components inside one container (FR-016, T033): communities of the code map restricted to that
container's code, named from their most specific meaningful module path, linked by aggregated dependencies,
within the token file's budgets, and passing the diagram standard's checker."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cairn.engines import mapper
from cairn.system.components import arrows, find_container, groups, module_path
from cairn.system.diagram.check import check
from cairn.system.diagram.schema import loads as load_description
from cairn.system.link import link
from cairn.system.views import budget, view

from .util import FIXTURES, product, repo, store

SHOP = ["shop-web", "shop-api", "shop-worker", "shop-contracts"]


def mapped(root: Path, *names: str) -> dict:
    """The code map of every repository of a product, which components are derived from."""
    return {n: root.parent / n for n in names}


def with_map(root: Path, *names: str) -> None:
    for n in names:
        assert mapper.build(root.parent / n, force=True)[0]


@pytest.fixture()
def shop(tmp_path):
    root = product(tmp_path, "shop", "shop-api", SHOP)
    with_map(root, *SHOP)
    return root


def test_components_view_has_the_standard_shape(shop):
    """US1-AS2, FR-016, FR-024: one level (components of one container), the standard's keys, evidence and
    provenance on every claim, a "how" on every arrow, and the runtime boundary that says where they live."""
    v = view(shop, "components")
    assert v["diagram"] == "component" and v["title"] == "Components of shop-api"
    assert "0 model calls" in v["scope"] and "components of shop-api" in v["scope"]
    assert {e["type"] for e in v["elements"]} == {"component"}
    for e in v["elements"]:
        assert {"id", "name", "type", "tech", "desc", "provenance", "evidence"} <= set(e)
        assert e["evidence"] and all(isinstance(x, str) for x in e["evidence"])
    for r in v["relationships"]:
        assert {"from", "to", "what", "how", "style", "provenance", "evidence"} <= set(r)
    b = v["boundaries"]
    assert len(b) == 1 and b[0]["name"] == "shop-api"
    assert set(b[0]["contains"]) == {e["id"] for e in v["elements"]}


def test_components_pass_the_standard_check(shop):
    """SC-006 for a computed view: the description loads with the shared schema and the checker reports no
    violation, evidence references resolved against the repositories' working trees."""
    v = view(shop, "components")
    problems = check(load_description(json.dumps(v)), roots=mapped(shop, *SHOP))
    assert problems == [], [str(p) for p in problems]


def test_orders_components_pass_the_standard_check(tmp_path):
    """SC-006 on the Java/Spring + Go fixture: components are per repository, not per product."""
    root = product(tmp_path, "orders", "checkout-spring", ["checkout-spring", "inventory-go"])
    with_map(root, "checkout-spring", "inventory-go")
    roots = mapped(root, "checkout-spring", "inventory-go")
    for scope in ("checkout-spring", "inventory-go"):
        v = view(root, "components", scope=scope)
        problems = check(load_description(json.dumps(v)), roots=roots)
        assert problems == [], [str(p) for p in problems]


def test_a_component_is_named_from_its_most_specific_module_path(shop):
    """FR-016: the name comes from the module path, skipping the generic folders every language has."""
    pm = link(shop)
    api = find_container(pm, "shop-api")
    names = [g["name"] for g in groups(api, shop)]
    assert "shop_api.orders" in names
    assert not any(n.startswith(("src.", "lib.", "internal.", "utils.", "common.", "core.")) for n in names)
    assert module_path("src/cairn/system/views.py") == "cairn.system.views"
    assert module_path("src/app/services/billing.py", "src") == "app.services.billing"
    assert module_path("src/main/java/com/acme/Order.java").endswith("com.acme.Order")
    assert module_path("internal/store/store.go") == "store"
    assert module_path("src/main/java/com/acme/Order.java") == "com.acme.Order"


def test_a_component_restricted_to_its_container_code(shop):
    """FR-016: the code of another deploy unit never joins a component, in a monorepo or across repositories."""
    pm = link(shop)
    api = find_container(pm, "shop-api")
    files = {g["element"]["evidence"][0].split("/", 1)[1].split(":")[0] for g in groups(api, shop)}
    assert files and all(f.startswith("shop_api/") or "/" not in f for f in files)
    assert not any(f.startswith("shop_web") for f in files)


def test_a_component_with_no_meaningful_path_is_named_after_its_busiest_symbol(tmp_path):
    """FR-016: no meaningful module path left after the generic folders are skipped: the busiest symbol's
    name, marked inferred, so nobody reads the guess as a fact."""
    files = {"Dockerfile": "FROM node:22\n",
             "src/index.ts": "export function alpha() {\n  return beta();\n}\n\n"
                             "export function beta() {\n  return 1;\n}\n",
             "package.json": '{"name": "flat", "scripts": {"start": "node dist/index.js"}}\n'}
    root = repo(tmp_path, "flat", files)
    store(root)
    with_map(root, "flat")
    pm = link(root)
    found = groups(find_container(pm, None), root)
    named = [g for g in found if g["name_inferred"]]
    assert named, [g["name"] for g in found]
    assert all(g["element"]["provenance"] == "inferred" for g in named)
    assert all(g["element"].get("name_inferred") is True for g in named)
    assert all("named after" in g["element"]["desc"] for g in named)


def test_dependencies_are_aggregated_and_labelled_with_their_how(shop):
    """FR-016: one arrow per pair of components, labelled with the mechanism, carrying the call sites."""
    pm = link(shop)
    api = find_container(pm, "shop-api")
    by_id = {g["id"]: g for g in groups(api, shop)}
    arrows_ = [r for g in by_id.values() for r in arrows(g)]
    assert arrows_
    for r in arrows_:
        assert r["how"] in ("calls", "imports", "calls · imports", "uses", "references")
        assert r["evidence"] and all("/" in ref for ref in r["evidence"])
        assert r["from"] in by_id and r["to"] in by_id
    assert len({(r["from"], r["to"]) for r in arrows_}) == len(arrows_)      # one arrow per direction


def test_components_stay_within_every_budget(shop, tmp_path):
    """FR-016 with the tokens: at most ``nodes_soft`` components and ``relationships`` arrows, labels inside
    ``label_what_chars`` and ``label_how_chars``, at most ``boundaries`` boundaries."""
    units = {f"svc{i:02d}": {"Dockerfile": "FROM x\n",
                             "app.py": "".join(f"import mod{j}\n" for j in range(4)) +
                                       "".join(f"\ndef f{j}():\n    return mod{j}.x\n" for j in range(4)),
                             **{f"mod{j}.py": f"x = {j}\n" for j in range(4)}} for i in range(9)}
    body = {f"{u}/{k}": v for u, fs in units.items() for k, v in fs.items()}
    body["docker-compose.yml"] = "services:\n" + "".join(f"  {u}:\n    build: ./{u}\n" for u in units)
    root = repo(tmp_path, "many", body)
    store(root)
    with_map(root, "many")
    v = view(root, "components", scope="svc00")
    assert len(v["elements"]) <= budget("nodes_soft")
    assert len(v["relationships"]) <= budget("relationships")
    assert len(v["boundaries"]) <= budget("boundaries")
    for r in v["relationships"]:
        assert len(r.get("what", "")) <= budget("label_what_chars")
        assert len(r.get("how", "")) <= budget("label_how_chars")
    if any(e["id"] == "collapsed:more" for e in v["elements"]):
        assert "collapsed" in v["scope"]


def test_components_are_deterministic(shop):
    """FR-005: the same repositories give the same components, ids and arrows, every run."""
    api = find_container(link(shop), "shop-api")
    first = json.dumps(groups(api, shop), sort_keys=True, default=str)
    second = json.dumps(groups(api, shop), sort_keys=True, default=str)
    assert first == second


def test_components_read_only_and_writes_nothing(shop):
    """FR-015: a view never writes to a repository's store."""
    db = shop.parent / "shop-api" / ".cairn" / "brain.db"
    before = db.stat().st_mtime_ns
    view(shop, "components")
    view(shop, "components", scope="shop-worker")
    assert db.stat().st_mtime_ns == before


def test_an_unmapped_container_says_what_to_run(tmp_path):
    """FR-016: components come from the code map, so a repository without one says so instead of inventing."""
    root = repo(tmp_path, "nomap", {"Dockerfile": "FROM x\n", "a.py": "import stripe\nstripe.Charge.create(1)\n"})
    store(root)
    with pytest.raises(ValueError, match="cairn map"):
        view(root, "components")


def test_an_unknown_scope_is_an_error(shop):
    with pytest.raises(ValueError, match="no container"):
        view(shop, "components", scope="nope")


def test_no_container_is_an_error(tmp_path):
    root = repo(tmp_path, "libonly", {"pyproject.toml": '[project]\nname = "libonly"\n',
                                      "libonly/__init__.py": "X = 1\n"})
    store(root)
    with pytest.raises(ValueError, match="no containers"):
        view(root, "components")


def test_the_shop_fixture_ships_a_ground_truth_for_components(tmp_path):
    """The fixture set is the same one the other slices score against."""
    assert (FIXTURES / "shop" / "shop-api" / "shop_api").is_dir()

def test_components_make_no_model_or_network_call(shop, monkeypatch):
    """FR-005, SC-003: components are read from the local map; no socket and no model is ever reached."""
    import socket

    from cairn import router as router_mod

    def boom(*_a, **_k):
        raise AssertionError("a model or network call was made")

    for name in ("complete", "stream", "_anthropic", "_openai_request"):
        if hasattr(router_mod.Router, name):
            monkeypatch.setattr(router_mod.Router, name, boom)
    for target in ("connect", "connect_ex", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket.socket, target, boom, raising=False)
    v = view(shop, "components")
    assert v["elements"] and v["source_note"].endswith("0 model calls")

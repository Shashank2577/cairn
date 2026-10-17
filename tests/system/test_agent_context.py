"""What an agent is told about its place in the product (spec 007: FR-028, SC-007; T036 to T039).

The session brief carries one line naming this repository's container and its neighbours, and
``cairn_context`` names the containers in other repositories that use the routes, handlers and contracts
a file implements. Both read the computed system model only: no model calls, no network, and nothing at
all in a repository that has no model.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cairn.core import BRIEF_SYSTEM_TOKENS, MAX_CONTEXT_CONSUMERS, Cairn
from cairn.router import estimate_tokens

from .util import FIXTURES, product

REPOS = ["shop-web", "shop-api", "shop-worker", "shop-contracts"]


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("CAIRN_EMBEDDER", "hash")
    monkeypatch.setenv("CAIRN_NO_CLI_MODELS", "1")
    for k in ("ANTHROPIC_API_KEY", "CAIRN_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(k, "")


def _product(tmp_path: Path, viewer: str = "shop-api") -> Cairn:
    root = product(tmp_path, "shop", viewer, REPOS)
    c = Cairn.here(root)
    c.project.ensure_dir()
    return c


def _drop_model(c: Cairn) -> None:
    """The state of a repository that never ran the system step."""
    for table in ("sm_relationship", "sm_evidence", "sm_element"):
        c.brain.q(f"DELETE FROM {table}")
    c._system_cache = None


LONG = ("Every money amount in this product is an integer number of cents, never a float, and every "
        "repository enforces the same rule at its own boundary.")


def _crowded(c: Cairn) -> None:
    """A brief with no room left: principles, then enough conventions and facts that later lines drop."""
    c.brain.set_kv("specs.constitution", json.dumps({"principles": [f"Principle {i}: {LONG}" for i in range(6)]}))
    for i in range(14):
        c.remember(f"Convention {i}: {LONG}", kind="convention")
    for i in range(6):
        c.remember(f"Fact {i}: production runs four replicas of every container behind one ingress",
                   kind="fact")


# --------------------------------------------------------------------------- T036: the brief line
def test_brief_names_this_container_and_its_neighbours(tmp_path):
    """FR-028 scenario 1: the brief of a repository in a grouped product carries the container line."""
    c = _product(tmp_path)
    line = c.system_line()
    assert line.startswith("System: Shop · this repository is shop-api (service)")
    assert "inbound: shop-web" in line and "outbound:" in line
    assert "Redis" in line and "PostgreSQL" in line


def test_brief_line_lands_before_the_team_knowledge_and_within_its_token_cap(tmp_path):
    """SC-007: the line costs at most 40 tokens and sits before the team knowledge, so it survives."""
    c = _product(tmp_path)
    _crowded(c)
    lines = c.brief().splitlines()
    line = next(i for i, x in enumerate(lines) if x.startswith("System: "))
    assert lines.index("Team knowledge:") > line         # placed before it, so a full brief keeps it
    assert estimate_tokens(lines[line]) <= BRIEF_SYSTEM_TOKENS


def test_the_line_survives_a_brief_that_is_already_full(tmp_path):
    """SC-007: a full brief keeps the line, stays inside the budget, and gives up team knowledge for it."""
    c = _product(tmp_path)
    _crowded(c)
    full = c.brief()
    assert estimate_tokens(full) <= 550 and "System: Shop" in full
    assert full.count("- [fact]") == 0                    # this brief is at its cap, not merely full
    with_model = full.count("- [convention]")
    _drop_model(c)
    without = c.brief()
    assert "System:" not in without
    assert estimate_tokens(without) <= 550
    assert without.count("- [convention]") >= with_model   # the line costs room, it never adds any
    assert without.splitlines()[-1] == full.splitlines()[-1]


def test_a_budget_too_small_for_the_line_still_answers(tmp_path):
    """A tiny budget drops the line rather than failing, and the instruction always survives."""
    c = _product(tmp_path)
    _crowded(c)
    tight = c.brief(max_tokens=60)
    assert "System:" not in tight and estimate_tokens(tight) <= 60
    assert tight.splitlines()[-1].startswith("Before editing")


def test_a_container_with_no_neighbours_is_not_announced(tmp_path):
    """A name on its own is not a place in a product; without neighbours the line stays silent."""
    from .util import copy_fixture, store
    root = copy_fixture(FIXTURES / "shop" / "shop-contracts", tmp_path / "shop-contracts")
    store(root)
    c = Cairn.here(root)
    c.project.ensure_dir()
    assert c.system_model() is not None and c.system_line() == ""
    assert "System:" not in c.brief()


def test_the_line_is_capped_when_a_product_has_many_neighbours(tmp_path):
    """SC-007 holds whatever the model says: neighbours are dropped, the cap is never exceeded."""
    c = _product(tmp_path)
    _add_callers(c, ["shop-web", "orders-checkout", "orders-billing", "orders-analytics", "orders-search",
                     "orders-exports", "orders-webhooks"])
    line = c.system_line()
    assert estimate_tokens(line) <= BRIEF_SYSTEM_TOKENS and "shop-api" in line
    named = line.split("inbound: ", 1)[1].split(" · ")[0]
    assert len(named.split(", ")) < 7                    # surplus neighbours dropped, never clipped mid-name
    assert all(n.strip() for n in named.split(", "))


def _add_callers(c: Cairn, callers: list[str]) -> None:
    """Extra sibling containers that call this repository's container, added to the linked model."""
    from cairn.system.model import Element, Evidence, Relationship
    pm = c.system_model()
    for n in callers:
        pm.elements[f"{n}:container:{n}"] = Element(
            id=f"{n}:container:{n}", type="container", name=n, repo=n, kind="service",
            evidence=[Evidence(repo=n, kind="dockerfile", file="Dockerfile", line=1)])
        pm.relationships[f"rel-{n}"] = Relationship(
            from_id=f"{n}:container:{n}", to_id="shop-api:container:shop-api", rule="http-route-match",
            what="Reads orders", repo=n, evidence=[Evidence(repo=n, kind="call", file="src/api.js", line=3)])
    c._system_cache = (c._system_cache[0], pm)


# -------------------------------------------------------------------------- T037: consuming containers
def test_context_names_the_containers_that_call_this_repository(tmp_path):
    """FR-028 scenario 2: the file behind a route another repository calls names that caller."""
    c = _product(tmp_path)
    out = c.context("change how orders are created", targets=["shop_api/orders.py"]).render()
    assert "### System consumers" in out
    assert "shop-web — Creates and reads orders" in out
    assert "shop-web/src/api.js:3" in out          # the evidence, in the calling repository
    assert "shop-worker — Subscribes to order.created" in out


def test_context_names_the_repositories_that_implement_this_contract(tmp_path):
    """A shared contract is served by the library repository and used by every container importing it."""
    c = _product(tmp_path, viewer="shop-contracts")
    out = c.context("extend the order contract", targets=["shop_contracts/orders.py"]).render()
    assert "shop-api — package shop-contracts (shop-api/shop_api/orders.py:2)" in out
    assert "shop-worker — package shop-contracts (shop-worker/shop_worker/main.py:3)" in out


def test_context_stays_silent_for_a_file_no_container_uses(tmp_path):
    """A file the product model says nothing about adds no section and no tokens."""
    c = _product(tmp_path, viewer="shop-web")
    out = c.context("tidy the storefront", targets=["server.js"]).render()
    assert "System consumers" not in out


def test_context_stays_silent_without_a_system_model(tmp_path):
    """No model, no consumers: the pack is exactly what it was before."""
    c = _product(tmp_path)
    for table in ("sm_relationship", "sm_evidence", "sm_element"):
        c.brain.q(f"DELETE FROM {table}")
    c._system_cache = None
    out = c.context("change how orders are created", targets=["shop_api/orders.py"]).render()
    assert "System consumers" not in out and "Context:" in out


def test_the_consumer_list_stays_within_the_pack_budget(tmp_path):
    """The pack's own budget still decides: many consumers are listed, but never over budget."""
    c = _product(tmp_path)
    _add_callers(c, [f"orders-{i}" for i in range(20)])
    pack = c.context("change how orders are created", targets=["shop_api/orders.py"], budget=800)
    out = pack.render()
    used, _, rest = out.rsplit("budget used: ", 1)[1].partition("/")
    consumers = c._system_consumers(["shop_api/orders.py"])
    assert "### System consumers" in out and len(consumers) == MAX_CONTEXT_CONSUMERS
    assert int(used) <= 800 and int(rest.split()[0]) == 800


# --------------------------------------------------------------------------- T038: the MCP tool
def test_cairn_system_is_in_the_full_toolset_only(tmp_path):
    """The tool costs schema tokens on every turn, so it is opt-in with `[mcp] tools = "all"`."""
    from cairn import mcp_server
    core = {t.__name__ for t in mcp_server.all_tools("core")}
    full = {t.__name__ for t in mcp_server.all_tools("all")}
    assert "cairn_system" in full and "cairn_system" not in core
    assert "cairn_system" not in {t.__name__ for t in mcp_server.CORE}
    assert "cairn_system" not in mcp_server.WRITES        # it reads the model, it never writes


def test_cairn_system_answers_with_the_computed_view_and_its_evidence(tmp_path, monkeypatch):
    c = _product(tmp_path)
    from cairn import mcp_server
    mcp_server._cairn.cache_clear()
    monkeypatch.setattr(mcp_server, "_cairn", lambda: c)
    out = mcp_server.cairn_system()
    assert "Containers of Shop" in out and "0 model calls" in out
    assert "shop-web (web-app)" in out and "shop-web/src/api.js:3" in out
    assert "shop-web → shop-api — Creates and reads orders" in out


def test_cairn_system_answers_for_the_other_levels_and_a_bad_level(tmp_path, monkeypatch):
    c = _product(tmp_path)
    from cairn import mcp_server
    mcp_server._cairn.cache_clear()
    monkeypatch.setattr(mcp_server, "_cairn", lambda: c)
    assert "System context of Shop" in mcp_server.cairn_system("context")
    assert "Code of Shop" in mcp_server.cairn_system("code")
    assert "unknown level" in mcp_server.cairn_system("nonsense")
    assert "nothing in this product is called" in mcp_server.cairn_system("containers", "nope")


def test_cairn_system_makes_no_model_call(tmp_path, monkeypatch):
    """FR-005, SC-003: the tool only reads what the sync already computed."""
    c = _product(tmp_path)
    calls = []
    from cairn import mcp_server
    from cairn.system import build as build_mod
    mcp_server._cairn.cache_clear()
    monkeypatch.setattr(mcp_server, "_cairn", lambda: c)
    monkeypatch.setattr(c.router, "complete", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(c.router, "stream", lambda *a, **k: pytest.fail("called the model"))
    monkeypatch.setattr(build_mod, "build_repo", lambda *a, **k: pytest.fail("rebuilt the model at read time"))
    assert mcp_server.cairn_system()
    assert calls == []


def test_cairn_system_is_served_read_only_over_http(tmp_path, monkeypatch):
    """The team server's read-only set keeps it: it answers about the model, it changes nothing."""
    c = _product(tmp_path)
    from cairn import mcp_server
    mcp_server._cairn.cache_clear()
    monkeypatch.setattr(mcp_server, "_cairn", lambda: c)
    assert mcp_server.cairn_system.__name__ not in mcp_server.WRITES
    assert mcp_server.cairn_system.__name__ not in mcp_server.NETWORK_HIDDEN
    doc = mcp_server.cairn_system.__doc__ or ""
    assert doc.startswith("Call ") and "0 model calls" in doc
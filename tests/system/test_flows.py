"""Flows from entry points (FR-017, T034): an endpoint, a message handler or a command the system model
recorded; the steps it reaches, each with evidence; order taken from one function body where the code gives
it and marked inferred everywhere else; split within the token file's sequence budgets."""
from __future__ import annotations

import json

import pytest

from cairn.system.diagram.check import check
from cairn.system.diagram.schema import loads as load_description
from cairn.system.flows import entry_points, flows, steps_for
from cairn.system.link import link
from cairn.system.views import budget, view

from .fixtures.fetch import voting_app
from .util import copy_fixture, product, repo, store

SHOP = ["shop-web", "shop-api", "shop-worker", "shop-contracts"]
SINGLE = {
    "Dockerfile": "FROM python:3.11\n",
    "pyproject.toml": '[project]\nname = "solo"\n\n[project.scripts]\nsolo = "app.cli:main"\n',
    "app/main.py": (
        "import psycopg\n"
        "import redis\n"
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "\n"
        "@app.get('/things')\n"
        "def list_things():\n"
        "    with psycopg.connect('postgres://x') as c:\n"
        "        rows = c.execute('SELECT * FROM things').fetchall()\n"
        "    r = redis.Redis()\n"
        "    r.publish('things', 1)\n"
        "    return rows\n"
    ),
    "app/cli.py": "def main():\n    print('go')\n",
}


@pytest.fixture()
def shop(tmp_path):
    return product(tmp_path, "shop", "shop-api", SHOP)


@pytest.fixture()
def solo(tmp_path):
    root = repo(tmp_path, "solo", SINGLE)
    store(root)
    return root


def roots(root, *names):
    return {n: root.parent / n for n in names}


def test_entry_points_come_from_the_model_not_from_inference(shop):
    """FR-017: the entry points are the routes, message handlers and commands the system model recorded."""
    kinds = {e.kind for e in entry_points(link(shop))}
    assert kinds == {"endpoint", "message", "command"}
    names = {e.name for e in entry_points(link(shop))}
    assert "POST /api/orders" in names and "Subscribes to order.created" in names
    by_kind = {e.kind: e for e in entry_points(link(shop))}
    assert any(e.how.startswith("FastAPI") for e in entry_points(link(shop)) if e.kind == "endpoint")
    assert by_kind["message"].how.startswith("Redis")
    assert all(e.evidence and e.site for e in entry_points(link(shop)))
    for e in entry_points(link(shop)):
        assert e.evidence and e.container


def test_a_flow_has_the_standard_shape(shop):
    """FR-017, US1-AS2: one flow per description, sequence style, participants and messages within the
    token file's budgets, every step with evidence and a "what" or a "how"."""
    v = view(shop, "flows")
    assert v["diagram"] == "flow" and v["title"].startswith("Flow of ")
    assert "0 model calls" in v["scope"]
    f = v["flow"]
    assert len(f["participants"]) <= budget("sequence_participants")
    assert len(f["messages"]) <= budget("sequence_messages")
    assert set(f["participants"]) == {e["id"] for e in v["elements"]}
    for m in f["messages"]:
        assert {"from", "to", "style", "provenance", "evidence"} <= set(m)
        assert m["from"] in f["participants"] and m["to"] in f["participants"]
        assert m.get("what") or m.get("how")
        assert m["evidence"] and all(isinstance(x, str) and x for x in m["evidence"])
    assert v["boundaries"] == []


def test_flows_pass_the_standard_check(shop):
    """SC-006: every computed flow loads with the shared schema and breaks no rule of the standard."""
    rs = roots(shop, *SHOP)
    for f in flows(shop, link(shop)):
        problems = check(load_description(json.dumps(f)), roots=rs)
        assert problems == [], [f["title"], [str(p) for p in problems]]


def test_a_flow_follows_the_steps_the_model_records(shop):
    """FR-017: the flow is the model's own chain: storefront calls the API, the API writes the store and
    publishes, the worker subscribes and sends the mail."""
    f = view(shop, "flows", scope="POST /api/orders")
    pairs = {(m["from"].split(":")[-1], m["to"].split(":")[-1]) for m in f["flow"]["messages"]}
    assert ("shop-web", "shop-api") in pairs
    assert ("shop-api", "db") in pairs
    assert ("shop-api", "redis") in pairs
    assert ("redis", "shop-worker") in pairs
    assert ("shop-worker", "sendgrid") in pairs
    assert [m["style"] for m in f["flow"]["messages"] if m["to"].endswith("redis")] == ["async"]


def test_a_flow_spanning_more_than_one_container_is_inferred(shop):
    """FR-017: order across containers is not derivable from one function body, so every step says so."""
    f = view(shop, "flows", scope="POST /api/orders")
    assert all(m["provenance"] == "inferred" for m in f["flow"]["messages"])
    assert "inferred" in f["scope"]
    assert "no model calls" in f["description"]


def test_steps_in_one_function_body_keep_their_real_order(solo):
    """FR-017: within one body the order is the order of the call sites, so it is not marked inferred."""
    f = view(solo, "flows", scope="solo [project.scripts]")
    steps = f["flow"]["messages"]
    assert [m["to"].split(":")[-1] for m in steps] == ["postgresql", "redis"]
    assert all(m["provenance"] == "extracted" for m in steps)
    assert "ordered by position" in f["scope"]
    lines = [int(m["evidence"][0].split(":")[1]) for m in steps]
    assert lines == sorted(lines) and len(set(lines)) == len(lines)


def test_a_delivery_through_a_channel_is_never_extracted(shop):
    """FR-017: a channel carries the message to another process; nothing in the code orders that."""
    f = view(shop, "flows", scope="Subscribes to order.created")
    delivery = [m for m in f["flow"]["messages"] if m["from"].endswith("redis")]
    assert delivery and delivery[0]["style"] == "async"
    assert all(m["provenance"] == "inferred" for m in f["flow"]["messages"])


def test_a_flow_splits_within_the_sequence_budgets(tmp_path):
    """FR-017, T034: a flow wider or longer than the token file allows is split, and the split is said."""
    n = 14
    files = {"Dockerfile": "FROM python:3.11\n",
             "pyproject.toml": '[project]\nname = "hub"\n\n[project.scripts]\nhub = "hub.cli:main"\n',
             "hub/cli.py": "import requests\n\ndef main():\n" + "".join(
                 f"    requests.post('http://svc{i}:9000/jobs/{{}}')\n" for i in range(1, n + 1))}
    for i in range(1, n + 1):
        files[f"svc{i}/Dockerfile"] = "FROM python:3.11\n"
        files[f"svc{i}/app.py"] = ("import psycopg\nfrom fastapi import FastAPI\napp = FastAPI()\n"
                                   "\n@app.get('/jobs/{}')\ndef job():\n"
                                   "    with psycopg.connect('postgres://x') as c:\n"
                                   "        c.execute('SELECT 1')\n    return {}\n")
    files["docker-compose.yml"] = "services:\n  hub:\n    build: .\n" + "".join(
        f"  svc{i}:\n    build: ./svc{i}\n" for i in range(1, n + 1))
    root = repo(tmp_path, "hub", files)
    store(root)
    pm = link(root)
    parts = [f for f in flows(root, pm) if "hub" in f["title"] and "GET" not in f["title"]]
    assert len(parts) > 1
    assert [p for p in parts if "(1 of " in p["title"]]
    for p in parts:
        assert len(p["flow"]["messages"]) <= budget("sequence_messages")
        assert len(p["flow"]["participants"]) <= budget("sequence_participants")
        assert check(load_description(json.dumps(p)), roots={"hub": root}) == []
    assert any("following parts" in p["scope"] for p in parts)


def test_the_scope_picks_the_entry_point(shop):
    """FR-017, T022: ``--scope`` names the entry point; a name that is not one is an error, not a guess."""
    assert view(shop, "flows", scope="POST /api/orders")["title"].endswith("POST /api/orders")
    with pytest.raises(ValueError, match="no entry point"):
        view(shop, "flows", scope="DELETE /nothing")


def test_a_product_without_entry_points_says_so(tmp_path):
    """FR-017: no entry point recorded, no flow — the view says so instead of inventing one."""
    root = repo(tmp_path, "quiet", {"pyproject.toml": '[project]\nname = "quiet"\n',
                                    "quiet/__init__.py": "X = 1\n"})
    store(root)
    with pytest.raises(ValueError, match="no entry point"):
        view(root, "flows")


def test_flows_are_deterministic(shop):
    """FR-005: the same repositories give the same flows, in the same order, every run."""
    strip = lambda fs: [  # noqa: E731
        (f["title"], [(m["from"], m["to"], m["what"], m["how"]) for m in f["flow"]["messages"]])
        for f in fs]
    assert strip(flows(shop, link(shop))) == strip(flows(shop, link(shop)))


def test_flows_read_only_and_carry_no_writes(shop):
    """FR-015, FR-005: reading the model never writes, and no step invents evidence."""
    db = shop / ".cairn" / "brain.db"
    before = db.stat().st_mtime_ns
    pm = link(shop)
    steps = steps_for(pm, next(e for e in entry_points(pm) if e.kind == "endpoint"))
    assert steps and all(s.evidence for s in steps)
    assert db.stat().st_mtime_ns == before


def test_flows_of_a_monorepo_stay_inside_their_container(tmp_path):
    """FR-017: one repository with several deploy units; each unit's flow starts from its own entry point."""
    root = copy_fixture(voting_app(), tmp_path / "example-voting-app", git=False)
    store(root)
    pm = link(root)
    found = flows(root, pm)
    assert found
    for f in found:
        names = {e["id"] for e in f["elements"]}
        assert set(f["flow"]["participants"]) <= names
        assert len(f["flow"]["participants"]) <= budget("sequence_participants")

def test_flows_make_no_model_or_network_call(shop, monkeypatch):
    """FR-005, SC-003: a flow is read from the stored model; no socket and no model is ever reached."""
    import socket

    from cairn import router as router_mod

    def boom(*_a, **_k):
        raise AssertionError("a model or network call was made")

    for name in ("complete", "stream", "_anthropic", "_openai_request"):
        if hasattr(router_mod.Router, name):
            monkeypatch.setattr(router_mod.Router, name, boom)
    for target in ("connect", "connect_ex", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket.socket, target, boom, raising=False)
    built = flows(shop, link(shop))
    assert built and all(f["source_note"].endswith("0 model calls") for f in built)

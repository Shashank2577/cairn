"""Linking containers inside one repository and across repositories by the same rules (FR-013 to FR-015,
edge cases: ambiguous routes, unresolved targets, shared stores and services; T021)."""
from __future__ import annotations

import pytest

from cairn.system.link import UNRESOLVED_ID, link
from cairn.system.signals.routes import norm_path

from .util import repo, store

EXPRESS = '{"name": "%s", "dependencies": {"express": "4"}}'
SERVE = ("const express = require('express');\nconst app = express();\n%s\napp.listen(80);\n")


def svc(routes: str = "", calls: str = "", name: str = "x") -> dict:
    return {"package.json": EXPRESS % name, "Dockerfile": "FROM node\n",
            "server.js": SERVE % routes, "client.js": calls}


def monorepo(tmp_path, units: dict[str, dict], env: dict[str, dict] | None = None):
    files: dict = {}
    compose = "services:\n"
    for name, fs in units.items():
        compose += f"  {name}:\n    build: ./{name}\n"
        for k, v in (env or {}).get(name, {}).items():
            compose += f"    environment:\n      {k}: {v}\n"
        files.update({f"{name}/{k}": v for k, v in fs.items()})
    files["docker-compose.yml"] = compose
    root = repo(tmp_path, "mono", files)
    store(root)
    return link(root)


def http_rels(m):
    names = {e.id: e.name for e in m.elements.values()}
    return {(names[r.from_id], names[r.to_id], r.provenance, r.rule) for r in m.relationships.values()
            if r.rule.startswith("http")}


def test_route_match_inside_a_monorepo(tmp_path):
    """FR-013: method + normalised path template match an inbound route of another container."""
    m = monorepo(tmp_path, {
        "web": svc(calls="fetch(`${process.env.ORDERS}/orders/${id}`);\n"),
        "orders": svc(routes="app.get('/orders/:id', h);")})
    assert http_rels(m) == {("web", "orders", "extracted", "http-route-match")}
    rel = next(r for r in m.relationships.values() if r.rule == "http-route-match")
    assert rel.what == "Reads orders" and rel.how == "HTTP · GET /orders/{}"
    assert any("client.js" in e.ref() for e in rel.evidence) and any("server.js" in e.ref() for e in rel.evidence)


def test_method_mismatch_does_not_match(tmp_path):
    """FR-013."""
    m = monorepo(tmp_path, {
        "web": svc(calls="fetch('/orders', { method: 'DELETE' });\n"),
        "orders": svc(routes="app.get('/orders', h);")})
    assert http_rels(m) == {("web", "Unresolved HTTP target", "inferred", "http-unresolved")}


@pytest.mark.parametrize("a,b", [
    ("/users/{user_id}/carts", "/users/:id/carts"), ("/users/<int:uid>/carts", "/users/${id}/carts"),
    ("/users/[id]/carts/", "/users/{id}/carts?x=1"), ("^users/(?P<pk>\\d+)/carts/$", "/users/{}/carts"),
    ("/files/*path", "/files/{path...}"), ("users//x/", "/users/x")])
def test_path_template_normalisation(a, b):
    """FR-013: every framework's path syntax normalises to one template."""
    assert norm_path(a) == norm_path(b)


def test_ambiguous_route_lists_every_candidate(tmp_path):
    """FR-014: two services serve the same route; one relationship per candidate, marked ambiguous."""
    m = monorepo(tmp_path, {
        "web": svc(calls="fetch('/status');\n"),
        "a": svc(routes="app.get('/status', h);"), "b": svc(routes="app.get('/status', h);")})
    rels = [r for r in m.relationships.values() if r.rule == "http-route-match"]
    assert {r.provenance for r in rels} == {"ambiguous"} and len(rels) == 2
    for r in rels:
        assert sorted(r.meta["candidates"]) == ["mono:container:a", "mono:container:b"]


def test_configuration_pins_the_target_and_resolves_ambiguity(tmp_path):
    """FR-013, FR-014, FR-011: Prototype lesson 5: the base URL's configuration names the target, so two candidates resolve."""
    m = monorepo(tmp_path, {
        "web": svc(calls="fetch(`${process.env.STATUS_URL}/status`);\n"),
        "a": svc(routes="app.get('/status', h);"), "b": svc(routes="app.get('/status', h);")},
        env={"web": {"STATUS_URL": "http://b:8080"}})
    assert http_rels(m) == {("web", "b", "extracted", "http-route-match")}


def test_configuration_target_without_a_known_route(tmp_path):
    """FR-013, FR-011."""
    m = monorepo(tmp_path, {
        "web": svc(calls="fetch(`${process.env.PAY}/charge`, { method: 'POST' });\n"),
        "pay": {"Dockerfile": "FROM x\n", "main.py": "print(1)\n"}},
        env={"web": {"PAY": "http://pay:9000"}})
    assert http_rels(m) == {("web", "pay", "extracted", "http-config")}


def test_unresolved_dynamic_url(tmp_path):
    """FR-008, FR-004: Edge case: a URL that is neither literal nor configured goes to "Unresolved HTTP target", inferred."""
    m = monorepo(tmp_path, {"web": svc(calls="fetch(url);\n"), "api": svc(routes="app.get('/x', h);")})
    assert http_rels(m) == {("web", "Unresolved HTTP target", "inferred", "http-unresolved")}
    assert m.elements[UNRESOLVED_ID].provenance == "inferred"


def test_call_to_own_route_is_not_a_relationship(tmp_path):
    """FR-013."""
    m = monorepo(tmp_path, {"web": svc(routes="app.get('/me', h);", calls="fetch('http://web/me');\n"),
                            "api": svc(routes="app.get('/x', h);")})
    assert http_rels(m) == set()


def _product(tmp_path, repos: dict[str, dict], viewer: str, extra: str = "") -> object:
    for name, files in repos.items():
        store(repo(tmp_path, name, files))
    paths = "".join(f"  - path: ../{n}\n" for n in repos)
    (tmp_path / viewer / ".cairn" / "system.yaml").write_text(f"system: P\nrepos:\n{paths}{extra}", encoding="utf-8")
    return link(tmp_path / viewer)


def test_publisher_and_subscriber_across_repositories_share_the_channel(tmp_path):
    """FR-013: publisher and subscriber link to one channel element; the consumer edge ranks after it."""
    pub = {"Dockerfile": "FROM x\n", "docker-compose.yml": "services:\n  pub:\n    build: .\n  bus:\n    image: redis:7\n",
           "p.py": "import redis\nr = redis.Redis(host='bus')\nr.publish('order.created', b'')\n"}
    sub = {"Dockerfile": "FROM x\n", "s.py": "import os, redis\nr = redis.Redis.from_url(os.environ['BUS_URL'])\n"
                                             "s = r.pubsub()\ns.subscribe('order.created')\n"}
    m = _product(tmp_path, {"pub": pub, "sub": sub}, "pub")
    chans = [e for e in m.elements.values() if e.type == "channel"]
    assert len(chans) == 1
    rels = {r.from_id.split(":")[-1]: r for r in m.relationships.values() if r.to_id == chans[0].id}
    assert rels["pub"].what == "Publishes order.created" and rels["sub"].what == "Subscribes to order.created"
    assert rels["sub"].meta["rank_reverse"] is True and rels["pub"].style == rels["sub"].style == "async"


def test_list_queue_push_and_pop(tmp_path):
    """FR-009, FR-013."""
    m = monorepo(tmp_path, {
        "producer": {"Dockerfile": "FROM x\n", "a.py": "from redis import Redis\nRedis(host='q').rpush('jobs', 1)\n"},
        "consumer": {"Dockerfile": "FROM x\n", "b.py": "from redis import Redis\nRedis(host='q').blpop('jobs')\n"}})
    whats = {r.what for r in m.relationships.values() if r.rule == "queue-key"}
    assert whats == {"Queues jobs", "Consumes jobs"}


def test_shared_store_is_one_element(tmp_path):
    """FR-013 'shared data store' and the edge case 'same outside service from several repositories'."""
    a = {"Dockerfile": "FROM x\n", "docker-compose.yml": "services:\n  a:\n    build: .\n  db:\n    image: postgres\n",
         "a.py": "import psycopg, sendgrid\npsycopg.connect('postgresql://db/app')\nsendgrid.SendGridAPIClient(k)\n"}
    b = {"Dockerfile": "FROM x\n", "b.py": "import os, psycopg, sendgrid\npsycopg.connect(os.environ['DATABASE_URL'])\n"
                                         "sendgrid.SendGridAPIClient(k)\n"}
    m = _product(tmp_path, {"a": a, "b": b}, "a")
    stores = [e for e in m.elements.values() if e.type == "data-store"]
    sendgrid = [e for e in m.elements.values() if e.name == "SendGrid"]
    assert len(stores) == 1 and len(sendgrid) == 1
    into = {r.from_id.split(":")[-1] for r in m.relationships.values() if r.to_id == stores[0].id}
    assert into == {"a", "b"}
    assert {r.from_id.split(":")[-1] for r in m.relationships.values() if r.to_id == sendgrid[0].id} == {"a", "b"}


def test_store_with_several_candidate_instances_is_ambiguous(tmp_path):
    """FR-014."""
    a = {"Dockerfile": "FROM x\n", "docker-compose.yml": "services:\n  a:\n    build: .\n  orders-db:\n    image: postgres\n"
                                                       "  audit-db:\n    image: postgres\n"}
    b = {"Dockerfile": "FROM x\n", "b.py": "import psycopg\npsycopg.connect(dsn)\n"}
    m = _product(tmp_path, {"a": a, "b": b}, "a")
    rel = next(r for r in m.relationships.values() if r.from_id.endswith(":b") and r.rule == "store-use")
    assert rel.provenance == "ambiguous" and len(rel.meta["candidates"]) == 2


def test_package_dependency_on_a_sibling_library_is_build_time(tmp_path):
    """FR-013: build-time dependency on a sibling's package, with the import site as evidence."""
    lib = {"pyproject.toml": '[project]\nname = "acme-types"\n', "acme_types/__init__.py": "X = 1\n"}
    app_ = {"Dockerfile": "FROM x\n", "pyproject.toml": '[project]\nname = "app"\ndependencies = ["acme-types", "requests"]\n',
            "app/__init__.py": "", "app/main.py": "import os\nfrom acme_types import X\nprint(os.environ['A'])\n"}
    m = _product(tmp_path, {"app": app_, "lib": lib}, "app")
    rels = [r for r in m.relationships.values() if r.rule == "package-dep"]
    assert len(rels) == 1 and rels[0].style == "build" and rels[0].how == "package acme-types"
    assert rels[0].what is None  # no rule-derived "what": the label is "how" alone (FR-003)
    assert rels[0].evidence[0].ref() == "app/app/main.py:2"


def test_siblings_are_read_without_being_written(tmp_path):
    """FR-015: linking never writes a sibling's files or its store."""
    m_files = {"Dockerfile": "FROM x\n", "a.py": "x = 1\n"}
    store(repo(tmp_path, "sib", m_files))
    db = tmp_path / "sib" / ".cairn" / "brain.db"
    before = (db.stat().st_mtime_ns, sorted(p.name for p in db.parent.iterdir()))
    store(repo(tmp_path, "me", m_files))
    (tmp_path / "me" / ".cairn" / "system.yaml").write_text("repos:\n  - path: ../sib\n", encoding="utf-8")
    m = link(tmp_path / "me")
    assert {e.repo for e in m.elements.values()} >= {"me", "sib"}
    assert (db.stat().st_mtime_ns, sorted(p.name for p in db.parent.iterdir())) == before


def test_every_claim_records_the_commit_it_was_built_at(tmp_path):
    """FR-029c, FR-004: elements, relationships and their evidence carry the commit they were computed at."""
    from .util import git_init
    root = repo(tmp_path, "c", {}, git=False)
    files = {"Dockerfile": "FROM x\n", "a.py": "import psycopg\npsycopg.connect('postgresql://db/x')\n"}
    for k, v in files.items():
        (root / k).write_text(v, encoding="utf-8")
    head = git_init(root)
    m = store(root)
    assert m.elements and m.relationships
    for x in list(m.elements.values()) + list(m.relationships.values()):
        assert x.built_at_commit == head
        assert all(e.commit == head for e in x.evidence)


def test_http_call_to_a_store_links_to_the_store(tmp_path):
    """FR-013, FR-011: a URL whose host is a store's compose service links to that store, not "unresolved"."""
    root = repo(tmp_path, "s", {"Dockerfile": "FROM x\n", "docker-compose.yml": (
        "services:\n  s:\n    build: .\n    environment:\n      SEARCH_URL: http://search:9200\n"
        "  search:\n    image: elasticsearch:8\n"),
        "a.py": "import os, requests\nrequests.post(os.environ['SEARCH_URL'] + '/orders/_search')\n"})
    store(root)
    m = link(root)
    names = {e.id: e.name for e in m.elements.values()}
    rels = {(names[r.from_id], names[r.to_id], r.rule) for r in m.relationships.values()}
    assert ("s", "Elasticsearch", "http-config") in rels
    assert UNRESOLVED_ID not in m.elements

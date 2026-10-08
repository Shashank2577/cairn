"""Optional system.yaml declarations (FR-012, FR-029a; T020): actors, per-repository kind, role and
description, declared relationships. Declared facts are labelled declared and take precedence; old files
stay valid; invalid entries are skipped with a reason, never fatal."""
from __future__ import annotations

from cairn.engines import systems
from cairn.system.link import link
from cairn.system.views import view

from .util import repo, store

SVC = {"Dockerfile": "FROM x\n", "package.json": '{"name": "n", "dependencies": {"express": "4"}}',
       "server.js": "const express = require('express');\nconst app = express();\napp.get('/x', h);\n"}


def setup(tmp_path, yaml_text: str):
    for n in ("web", "api"):
        store(repo(tmp_path, n, SVC))
    (tmp_path / "web" / ".cairn" / "system.yaml").write_text(yaml_text, encoding="utf-8")
    return tmp_path / "web"


def test_valid_declarations_are_applied_and_labelled_declared(tmp_path):
    """FR-012, FR-029a, FR-004."""
    root = setup(tmp_path, (
        "system: Shop\nrepos:\n  - path: ../web\n    kind: web-app\n    description: Storefront\n"
        "  - path: ../api\n    kind: worker\n"
        "actors:\n  - {name: Customer, desc: Buys things, uses: web, what: Places orders, how: HTTPS}\n"
        "relationships:\n  - {from: web, to: api, what: Sends carts, how: HTTP, style: sync}\n"
        "  - {from: api, to: Customer, what: Emails receipts, style: async}\n"))
    v = view(root, "container")
    by_name = {e["name"]: e for e in v["elements"]}
    assert by_name["web"]["kind"] == "web-app" and by_name["web"]["kind_provenance"] == "declared"
    assert by_name["web"]["desc"] == "Storefront" and by_name["api"]["kind"] == "worker"
    assert by_name["Customer"]["type"] == "person" and by_name["Customer"]["provenance"] == "declared"
    rels = {(r["from"], r["to"]): r for r in v["relationships"]}
    cust = rels[("actor:customer", "web:container:web")]
    assert cust["provenance"] == "declared" and cust["what"] == "Places orders"
    assert cust["evidence"][0].startswith("web/.cairn/system.yaml:")
    assert rels[("web:container:web", "api:container:api")]["provenance"] == "declared"
    assert rels[("api:container:api", "actor:customer")]["style"] == "async"


def test_old_files_stay_valid(tmp_path):
    """FR-012."""
    root = setup(tmp_path, "system: Shop\nrepos:\n  - path: ../api\n")
    assert systems.discover(root).name == "Shop"
    d = systems.declarations(root)
    assert d.actors == () and d.relationships == () and d.skipped == ()
    assert {e["name"] for e in view(root, "container")["elements"]} == {"web", "api"}


def test_invalid_entries_are_skipped_with_a_reason(tmp_path):
    """FR-012."""
    root = setup(tmp_path, (
        "system: Shop\nrepos:\n  - path: ../api\n    kind: mainframe\n    role: database\n    description: [1, 2]\n"
        "actors:\n  - {desc: nameless}\n  - just-a-string\n"
        "relationships:\n  - {from: web}\n  - {from: web, to: api, style: telepathy}\n"
        "  - {from: api, to: api, what: loops}\n  - {from: web, to: Nowhere, what: x}\n"))
    d = systems.declarations(root)
    reasons = " | ".join(f"{s['entry']}: {s['reason']}" for s in d.skipped)
    for needle in ("kind must be one of", "role must be one of", "description must be text", "an actor needs a name",
                   "a relationship needs from and to", "style must be one of", "self reference"):
        assert needle in reasons, needle
    m = link(root)
    skipped = m.product["declared_skipped"]
    assert any("unknown element 'Nowhere'" in s["reason"] for s in skipped)
    assert not [r for r in m.relationships.values() if r.rule == "declared"]


def test_actor_using_a_missing_repository_and_unknown_names(tmp_path):
    """FR-012, US1-AS3."""
    root = setup(tmp_path, (
        "system: Shop\nrepos:\n  - path: ../api\n  - path: ../billing\n"
        "actors:\n  - {name: Finance, uses: billing, what: Reconciles}\n  - {name: Ghost, uses: nothing-here}\n"))
    m = link(root)
    billing = next(e for e in m.elements.values() if e.name == "billing")
    assert billing.meta["not_mapped"] == "not found"
    assert any(r.to_id == billing.id and r.provenance == "declared" for r in m.relationships.values())
    assert any("nothing-here" in s["reason"] for s in m.product["declared_skipped"])


def test_declared_label_takes_precedence_on_an_extracted_relationship(tmp_path):
    """FR-012, FR-003."""
    for n, files in {"web": {**SVC, "client.js": "fetch(`${process.env.API}/x`);\n"}, "api": SVC}.items():
        store(repo(tmp_path, n, files))
    (tmp_path / "web" / ".cairn" / "system.yaml").write_text(
        "repos:\n  - path: ../api\nrelationships:\n  - {from: web, to: api, what: Fetches the x feed}\n",
        encoding="utf-8")
    m = link(tmp_path / "web")
    rels = [r for r in m.relationships.values() if r.from_id == "web:container:web" and r.to_id == "api:container:api"]
    assert len(rels) == 1 and rels[0].what == "Fetches the x feed" and rels[0].provenance == "extracted"
    assert rels[0].meta.get("what_declared") is True

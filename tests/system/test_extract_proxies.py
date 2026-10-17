"""Reverse-proxy configuration as an outbound edge (T052, FR-034's ``config`` kind): nginx ``location``
blocks whose ``proxy_pass`` names another service, the upstream host supplied by a template variable the
Dockerfile's ENV defaults to the compose service name."""
from __future__ import annotations

from cairn.system.catalog import load
from cairn.system.link import link
from cairn.system.signals.files import scan
from cairn.system.signals.proxy import extract as proxy_extract

from .util import repo, store

CONF = """server {
    listen 8080;
    location / {
        root /usr/share/nginx/html;
    }
    #location /api/old/ {
    #    proxy_pass http://gone:8080/;
    #}
    location /api/cart/ {
        proxy_pass http://${CART_HOST}:8080/;
    }
    location /api/orders/ {
        proxy_pass http://orders:8080/;
    }
    location ~ ^/api/legacy/(.*)$ {
        proxy_pass http://orders:8080/$1;
    }
}
"""


def test_proxy_pass_parsed_from_an_nginx_template(tmp_path):
    root = repo(tmp_path, "proxy", {"web/default.conf.template": CONF})
    passes = proxy_extract(scan(root), load(root))
    by_path = {p.path: p for p in passes}
    assert set(by_path) == {"/api/cart/", "/api/orders/", "/api/legacy/(.*)$"}   # commented block skipped
    cart = by_path["/api/cart/"]
    assert (cart.host, cart.config, cart.port) == (None, "CART_HOST", "8080")
    orders = by_path["/api/orders/"]
    assert (orders.host, orders.config) == ("orders", None)
    for p in passes:
        assert p.proxy == "nginx" and p.file == "web/default.conf.template" and p.line


def test_proxy_edges_resolve_through_dockerfile_env(tmp_path):
    """T052: the proxying container relates to each proxied service, the variable host resolved from the
    Dockerfile ENV default, the literal host directly; evidence is the conf line."""
    files = {
        "web/Dockerfile": "FROM nginx:1.21\nEXPOSE 8080\nENV CART_HOST=cart ORDERS_HOST=orders\n",
        "web/default.conf.template": CONF,
        "cart/Dockerfile": "FROM node\n",
        "cart/server.js": "require('express')().get('/x', h);",
        "cart/package.json": '{"name": "cart", "dependencies": {"express": "4"}}',
        "orders/Dockerfile": "FROM node\n",
        "orders/server.js": "require('express')().get('/x', h);",
        "orders/package.json": '{"name": "orders", "dependencies": {"express": "4"}}',
        "docker-compose.yml": ("services:\n  web:\n    build: ./web\n  cart:\n    build: ./cart\n"
                               "  orders:\n    build: ./orders\n"),
    }
    root = repo(tmp_path, "prod", files)
    store(root)
    m = link(root)
    rels = [r for r in m.relationships.values() if r.rule.startswith("http")]
    assert {("web", "cart")} <= {(m.elements[r.from_id].name, m.elements[r.to_id].name) for r in rels}
    assert {("web", "orders")} <= {(m.elements[r.from_id].name, m.elements[r.to_id].name) for r in rels}
    ev = next(r for r in rels if m.elements[r.to_id].name == "cart").evidence
    assert any(e.kind == "proxy" and e.file.endswith("default.conf.template") for e in ev)

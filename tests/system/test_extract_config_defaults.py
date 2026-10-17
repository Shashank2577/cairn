"""Configuration fallbacks close the gaps a literal-only reader cannot (T053 to T056):

- a PHP service (curl client, PDO driver) is extracted with its edges (T053);
- an environment read whose literal fallback names a compose service links to it, in Python
  (``.format``-built URLs), JavaScript (``||`` fallbacks, the ``request`` client) and Java
  (``getenv(k, def)`` helpers, ternary defaults) (T054);
- a compose service built from a directory whose Dockerfile only configures a store image is that
  store, named by the service (T055);
- a configured domain address that leaves the product becomes an inferred outside system named after
  the configuration key (T056);
- a value that merely looks like a word (``APP_ENV=dev``) is not an address.
"""
from __future__ import annotations

from cairn.system.link import link

from .util import build, containers, pairs, repo, store

PHP_SERVICE = {
    "Dockerfile": "FROM php:7.4-apache\n",
    "html/index.php": "<?php\n$dsn = getenv('PDO_URL') ?: 'mysql:host=mysql;dbname=x';\n"
                      "$url = getenv('CATALOGUE_URL') ?: 'http://catalogue:8080';\n",
    "html/src/Database.php": "<?php\nclass Database {\n"
                             "    public function getConnection() {\n"
                             "        return new PDO($this->dsn, 'u', 'p');\n    }\n}\n",
    "html/src/Service/CatalogueService.php": "<?php\nclass CatalogueService {\n"
                                             "    public function checkSKU($sku) {\n"
                                             "        $url = sprintf('%s/product/%s', $this->catalogueUrl, $sku);\n"
                                             "        $curl = curl_init($url);\n"
                                             "        return curl_exec($curl);\n    }\n}\n",
}
COMPOSE_STORES = """services:
  ratings:
    build: ./ratings
  catalogue:
    build: ./catalogue
    ports: ["8080"]
  mysql:
    build: ./mysql
"""


def _http(m):
    return {p for p in pairs(m) if any(r.rule.startswith("http") for r in m.relationships.values()
                                       if (m.elements[r.from_id].name, m.elements[r.to_id].name) == p)}


def test_php_service_stores_and_env_default_edges(tmp_path):
    """T053 + T054: the PDO use resolves to the build-context MySQL store through the DSN fallback; the
    catalogue edge comes from the env read's URL fallback."""
    files = {
        "ratings/Dockerfile": PHP_SERVICE["Dockerfile"],
        "ratings/html/index.php": PHP_SERVICE["html/index.php"],
        "ratings/html/src/Database.php": PHP_SERVICE["html/src/Database.php"],
        "ratings/html/src/Service/CatalogueService.php": PHP_SERVICE["html/src/Service/CatalogueService.php"],
        "mysql/Dockerfile": "FROM mysql:5.7\nENV MYSQL_ALLOW_EMPTY_PASSWORD=yes\n",
        "catalogue/Dockerfile": "FROM node\n",
        "catalogue/server.js": "require('express')().get('/product/:sku', h);",
        "catalogue/package.json": '{"name": "catalogue", "dependencies": {"express": "4"}}',
        "docker-compose.yml": COMPOSE_STORES,
    }
    root = repo(tmp_path, "shop", files)
    store(root)
    m = link(root)
    assert set(containers(m)) == {"ratings", "catalogue"}          # mysql is a store, not a container
    mysql = [e for e in m.elements.values() if e.type == "data-store" and e.name == "MySQL"]
    assert mysql and "mysql" in (mysql[0].meta.get("aliases") or [])   # T055: named by the service
    rels = pairs(m)
    assert ("ratings", "MySQL") in rels
    assert ("ratings", "catalogue") in rels


def test_store_image_with_a_command_stays_a_container(tmp_path):
    """T055, the other side: a Dockerfile on a store image that runs its own command is an application."""
    files = {
        "cache/Dockerfile": "FROM redis:6\nCMD [\"redis-server\", \"--appendonly\", \"yes\"]\n",
        "docker-compose.yml": "services:\n  cache:\n    build: ./cache\n",
    }
    m = build(repo(tmp_path, "t", files))
    assert set(containers(m)) == {"cache"} and not [e for e in m.elements.values() if e.type == "data-store"]


def test_env_default_edges_per_language(tmp_path):
    """T054: Python ``.format`` URLs, JavaScript ``||`` fallbacks through the ``request`` client, and a
    Java ``getenv(k, def)`` helper all resolve to the compose service the fallback names."""
    files = {
        "api/Dockerfile": "FROM node\n",
        "api/package.json": '{"name": "api", "dependencies": {"express": "4", "request": "2"}}',
        "api/server.js": ("const express = require('express'); const request = require('request');\n"
                          "var host = process.env.PAY_HOST || 'pay';\n"
                          "request('http://' + host + ':8080/charge', () => {});\n"
                          "express().get('/checkout', h);\n"),
        "pay/Dockerfile": "FROM python:3.12\n",
        "pay/pay.py": ("import os, requests\n"
                       "CART = os.getenv('CART_HOST', 'cart')\n"
                       "requests.post('http://{cart}:8080/cart/{id}'.format(cart=CART, id=1))\n"),
        "cart/Dockerfile": "FROM python:3.12\n",
        "cart/cart.py": "from flask import Flask\napp = Flask(__name__)\napp.route('/cart/<id>')(h)\n",
        "ship/Dockerfile": "FROM openjdk:17\n",
        "ship/Svc.java": ("class Svc {\n"
                          "    private String getenv(String k, String def) { return def; }\n"
                          "    private String CART_URL = \"http://\" + getenv(\"CART_ENDPOINT\", \"cart\") + \"/shipping/\";\n"
                          "    void go() { new org.springframework.web.client.RestTemplate().getForObject(CART_URL, String.class); }\n"
                          "}\n"),
        "docker-compose.yml": "services:\n  api:\n    build: ./api\n  pay:\n    build: ./pay\n"
                              "  cart:\n    build: ./cart\n  ship:\n    build: ./ship\n",
    }
    root = repo(tmp_path, "langs", files)
    store(root)
    m = link(root)
    rels = _http(m)
    assert ("api", "pay") in rels                     # JS: request() + || fallback
    assert ("pay", "cart") in rels                    # Python: .format-built URL
    assert ("ship", "cart") in rels                   # Java: getenv helper default


def test_configured_domain_becomes_an_inferred_outside_system(tmp_path):
    """T056: the outside system is inferred and named after the configuration key, never the address; a
    word-like fallback (APP_ENV=dev) creates nothing."""
    files = {
        "pay/Dockerfile": "FROM python:3.12\n",
        "pay/pay.py": ("import os, requests\n"
                       "GATEWAY = os.getenv('PAYMENT_GATEWAY', 'https://payments.example.com/')\n"
                       "requests.get(GATEWAY)\n"
                       "env = os.getenv('APP_ENV', 'dev')\n"),
        "docker-compose.yml": "services:\n  pay:\n    build: ./pay\n",
    }
    root = repo(tmp_path, "gw", files)
    store(root)
    m = link(root)
    ext = [e for e in m.elements.values() if e.type == "external-system"]
    assert [e.name for e in ext] == ["Payment Gateway"] and ext[0].provenance == "inferred"
    rel = [r for r in m.relationships.values() if r.to_id == ext[0].id]
    assert rel and rel[0].provenance == "inferred" and rel[0].rule == "http-config"
    assert ("pay", "dev") not in pairs(m)

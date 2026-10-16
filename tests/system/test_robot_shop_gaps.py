"""Gaps the robot-shop documentation experiment exposed in the system view (spec 010), reproduced on
instana/robot-shop at the pinned commit, where the ground truth is known:

* the protocol on a relationship comes from client call sites, never from a lack of them (cart -> catalogue,
  shipping -> cart and load -> web are plain HTTP, not gRPC);
* a relationship cites the call site when the code has one, not the configuration line that names the host;
* a container's technology comes from its real base image or entry command, not from a wrapper script
  (web is nginx, load is Python, fluentd is Fluentd);
* a relationship lists every distinct call it summarises (payment -> user: GET /check/{} and POST /order/{}).
"""
from __future__ import annotations

import pytest

from cairn.system.link import link
from cairn.system.views import view

from .fixtures.fetch import pinned_repo
from .util import copy_fixture

PIN = "55292e2199f2fb00a165b1f7d3045fe7f8922038"


@pytest.fixture(scope="module")
def v(tmp_path_factory):
    src = pinned_repo("https://github.com/instana/robot-shop", PIN, "robot-shop")
    root = copy_fixture(src, tmp_path_factory.mktemp("rs") / "robot-shop", git=False)
    return view(root, "container", model=link(root))


def rel(v, a, b):
    return next(r for r in v["relationships"] if r["from"].endswith(f":{a}") and r["to"].endswith(f":{b}"))


def el(v, name):
    return next(e for e in v["elements"] if e["name"] == name)


@pytest.mark.parametrize("a,b", [("cart", "catalogue"), ("shipping", "cart"), ("load", "web")])
def test_http_wiring_is_never_labelled_grpc(v, a, b):
    assert "grpc" not in rel(v, a, b)["how"].lower()


@pytest.mark.parametrize("a,b", [("cart", "catalogue"), ("shipping", "cart")])
def test_an_http_url_literal_beside_the_address_makes_it_http(v, a, b):
    assert rel(v, a, b)["how"].startswith("HTTP"), rel(v, a, b)["how"]    # 'http://' + host / "http://%s/..."


def test_config_wiring_cites_the_call_site_not_the_config_line(v):
    cart = rel(v, "cart", "catalogue")["evidence"][0]
    assert cart.endswith("cart/server.js:362"), cart            # request('http://' + catalogueHost + ...)
    ship = rel(v, "shipping", "cart")["evidence"][0]
    assert ship.endswith("shipping/Controller.java:136"), ship  # new CartHelper(CART_URL), not the :24 binding


@pytest.mark.parametrize("name,tech,kind_not", [("web", "nginx", "job"), ("load", "Python", None),
                                                ("fluentd", "Fluentd", None)])
def test_tech_comes_from_the_image_or_entry_command(v, name, tech, kind_not):
    e = el(v, name)
    assert tech in e["tech"] and "Shell" not in e["tech"], e
    if kind_not:
        assert e.get("kind") != kind_not, e


def test_summary_lists_every_distinct_call(v):
    r = rel(v, "payment", "user")
    assert "/check" in r["how"] and "POST /order" in r["how"], r["how"]
    assert "ANY" not in r["how"], r["how"]                      # the config default adds no path
    assert r["evidence"][0].endswith("payment/payment.py:64"), r["evidence"]


def test_a_citation_carries_the_enclosing_functions_lines(v, tmp_path_factory):
    from cairn.spans import annotate
    src = pinned_repo("https://github.com/instana/robot-shop", PIN, "robot-shop")
    ref = rel(v, "cart", "catalogue")["evidence"][0]                       # robot-shop/cart/server.js:362
    assert annotate(src, ref) == f"{ref} (lines 360-374)"                  # function getProduct(sku) { ... }
    assert annotate(src, "robot-shop/cart/missing.js:3") == "robot-shop/cart/missing.js:3"

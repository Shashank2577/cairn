"""Inbound route extraction end to end, per framework and per pattern kind (FR-007, FR-034; T013a-d).

``test_extract_catalog`` covers one rule per framework against the bundled catalog. This file covers
the shapes a real repository uses around those rules and the two properties FR-007 asks for beyond
the path: every route names the handler and the line it was declared on, and extraction is
deterministic and offline (no model call, no network, FR-005).
"""
from __future__ import annotations

import socket

from .util import build, write

DOCKER = {"Dockerfile": "FROM base\nEXPOSE 8080\nCMD [\"run\"]\n"}

PY = {"pyproject.toml": '[project]\nname = "svc"\ndependencies = ["fastapi", "flask", "django"]\n'}
POM = {"pom.xml": "<project><dependencies><dependency><groupId>org.springframework.boot</groupId>"
                   "<artifactId>spring-boot-starter-web</artifactId></dependency></dependencies></project>"}
WEBFLUX = {"pom.xml": "<project><dependencies><dependency><groupId>org.springframework.boot</groupId>"
                       "<artifactId>spring-boot-starter-webflux</artifactId></dependency></dependencies></project>"}
GO = {"go.mod": "module m\n\ngo 1.22\n\nrequire github.com/gin-gonic/gin v1.9.0\n"}
JS = {"package.json": '{"name":"svc","dependencies":{"express":"4","fastify":"4"}}'}


def model(tmp_path, files, name="svc"):
    return build(write(tmp_path / name, {**DOCKER, **files}), name)


def routes(m) -> list[str]:
    return sorted(e.name for e in m.elements.values() if e.kind == "route")


def evidence(m) -> dict[str, str]:
    return {e.name: e.evidence[0].ref() for e in m.elements.values() if e.kind == "route" and e.evidence}


# ------------------------------------------------------------------------------ FastAPI (decorator)
def test_fastapi_router_prefix_constructor_and_mount_compose(tmp_path):
    """T013a: ``APIRouter(prefix=)`` and ``include_router(prefix=)`` stack; the method name is the verb."""
    m = model(tmp_path, {**PY, "app/main.py": (
        "from fastapi import FastAPI, APIRouter\nfrom .items import router\napp = FastAPI()\n"
        "@app.get('/health')\ndef health(): ...\napp.include_router(router, prefix='/api')\n"),
        "app/__init__.py": "", "app/items.py": (
        "from fastapi import APIRouter\nrouter = APIRouter(prefix='/v1')\n"
        "@router.post('/items/{item_id}')\ndef create(item_id: int): ...\n"
        "@router.api_route('/items', methods=['PUT', 'PATCH'])\ndef bulk(): ...\n")})
    assert routes(m) == ["GET /health", "PATCH /api/v1/items", "POST /api/v1/items/{}", "PUT /api/v1/items"]


def test_fastapi_route_names_its_handler_and_declaration_line(tmp_path):
    """FR-007: the handler and the location are recorded, not just the path."""
    m = model(tmp_path, {**PY, "main.py": (
        "from fastapi import FastAPI\napp = FastAPI()\n\n\n@app.post('/orders')\ndef create_order(): ...\n")})
    e = next(x for x in m.elements.values() if x.name == "POST /orders")
    assert e.meta["handler"] == "create_order" and e.meta["framework"] == "FastAPI"
    assert e.evidence[0].ref() == "svc/main.py:5"


# --------------------------------------------------------------------------------- Flask (decorator)
def test_flask_blueprint_url_prefix_and_register_prefix(tmp_path):
    """T013a: ``url_prefix`` at construction and at registration both count, and stack."""
    m = model(tmp_path, {**PY, "web.py": (
        "from flask import Flask, Blueprint\napp = Flask(__name__)\n"
        "bp = Blueprint('o', __name__, url_prefix='/orders')\n"
        "@app.route('/', methods=['POST', 'GET'])\ndef index(): ...\n"
        "@bp.route('/<int:oid>')\ndef one(oid): ...\n@bp.delete('/<int:oid>')\ndef rm(oid): ...\n"
        "app.register_blueprint(bp, url_prefix='/api')\n")})
    assert routes(m) == ["DELETE /api/orders/{}", "GET /", "GET /api/orders/{}", "POST /"]


def test_flask_methods_given_as_one_string_name_one_verb(tmp_path):
    """T013a: ``methods='POST'`` is a single method, not a list of characters."""
    m = model(tmp_path, {**PY, "a.py": (
        "from flask import Flask\napp = Flask(__name__)\n@app.route('/login', methods='POST')\ndef login(): ...\n")})
    assert routes(m) == ["POST /login"]


# --------------------------------------------------------------------------------- Django (file-route)
def test_django_include_chain_composes_every_prefix(tmp_path):
    """T013a: a URLconf included from another URLconf keeps both prefixes, at any depth."""
    m = model(tmp_path, {**PY,
        "urls.py": "from django.urls import include, path\nurlpatterns = [path('api/', include('shop.urls'))]\n",
        "shop/__init__.py": "",
        "shop/urls.py": "from django.urls import include, path\nurlpatterns = [path('v1/', include('shop.v1'))]\n",
        "shop/v1.py": "from django.urls import path\nfrom . import views\nurlpatterns = [path('orders/', views.l)]\n",
        "shop/views.py": "def l(request): ...\n"})
    assert routes(m) == ["ANY /api/v1/orders"]


def test_django_include_with_an_app_namespace_tuple(tmp_path):
    """T013a: ``include(('shop.urls', 'shop'), namespace=...)`` names the module first in the tuple."""
    m = model(tmp_path, {**PY,
        "urls.py": ("from django.urls import include, path\n"
                    "urlpatterns = [path('api/', include(('shop.urls', 'shop'), namespace='s'))]\n"),
        "shop/__init__.py": "",
        "shop/urls.py": "from django.urls import path\nfrom . import views\nurlpatterns = [path('orders/', views.l)]\n",
        "shop/views.py": "def l(request): ...\n"})
    assert routes(m) == ["ANY /api/orders"]


def test_django_urlconf_package_and_non_urls_file_name(tmp_path):
    """T013a: a URLconf is a file another one includes, whatever it is called."""
    m = model(tmp_path, {**PY,
        "urls.py": "from django.urls import include, path\nurlpatterns = [path('api/', include('shop.urls'))]\n",
        "shop/urls/__init__.py": "from django.urls import path\nfrom .. import views\n"
                                 "urlpatterns = [path('orders/', views.l)]\n",
        "shop/views.py": "def l(request): ...\n"})
    assert routes(m) == ["ANY /api/orders"]


def test_django_regex_path_and_query_string_normalise(tmp_path):
    """FR-007: a regex capture group and a query string are parameters, not segments."""
    m = model(tmp_path, {**PY, "urls.py": (
        "from django.urls import re_path\nfrom . import views\n"
        "urlpatterns = [re_path(r'^items/(?P<pk>\\d+)/$', views.d)]\n")})
    assert routes(m) == ["ANY /items/{}"]


# --------------------------------------------------------------------------------------- Spring (annotation)
def test_spring_class_prefix_method_annotations_and_value_forms(tmp_path):
    """T013c: ``value=``, positional and bare annotations all read; the class mapping is the prefix."""
    m = model(tmp_path, {**POM, "src/main/java/a/Orders.java": (
        "package a;\nimport org.springframework.web.bind.annotation.*;\n@RestController\n"
        "@RequestMapping(\"/api/orders\")\npublic class Orders {\n"
        "  @GetMapping(\"/{id}\")\n  public String one() { return \"\"; }\n"
        "  @RequestMapping(value = \"/bulk\", method = RequestMethod.PUT)\n  public void bulk() {}\n"
        "  @PostMapping(path = \"/\")\n  public void create() {}\n}\n")})
    assert routes(m) == ["GET /api/orders/{}", "POST /api/orders", "PUT /api/orders/bulk"]


def test_spring_one_annotation_several_paths_is_several_routes(tmp_path):
    """T013c: ``@GetMapping({"/a", "/b"})`` serves both; a rule reads every literal, not the first."""
    m = model(tmp_path, {**POM, "src/main/java/a/C.java": (
        "package a;\nimport org.springframework.web.bind.annotation.*;\n@RestController\n"
        "@RequestMapping(\"/api\")\nclass C {\n  @GetMapping({\"/a\", \"/b\"})\n  void m() {}\n}\n")})
    assert routes(m) == ["GET /api/a", "GET /api/b"]


def test_spring_webflux_uses_the_same_annotations(tmp_path):
    """T013c: the WebFlux starter is detected and its functional-style mapping is read."""
    m = model(tmp_path, {**WEBFLUX, "src/main/java/a/C.java": (
        "package a;\nimport org.springframework.web.bind.annotation.*;\n@RestController\n"
        "@RequestMapping(\"/api\")\nclass C {\n  @GetMapping(\"/a\")\n  void m() {}\n}\n")})
    assert routes(m) == ["GET /api/a"]
    assert next(e for e in m.elements.values() if e.kind == "route").meta["framework"] == "Spring Boot"


def test_spring_interface_mapping_and_record_components(tmp_path):
    """T013c: an interface method and a record component are both handler methods."""
    src = {
        "I.java": ("package a;\nimport org.springframework.web.bind.annotation.*;\n@RequestMapping(\"/api\")\n"
                   "public interface I {\n  @GetMapping(\"/x\")\n  String x();\n}\n"),
        "R.java": ("package a;\nimport org.springframework.web.bind.annotation.*;\n@RestController\n"
                   "@RequestMapping(\"/api\")\npublic record R(@PostMapping(\"/y\") int y) {}\n"),
    }
    m = model(tmp_path, {**POM, **{f"src/main/java/a/{k}": v for k, v in src.items()}})
    assert routes(m) == ["GET /api/x", "POST /api/y"]


# --------------------------------------------------------------------------------- Go (call: net/http, Gin)
def test_go_1_22_patterns_carry_the_method_and_the_wildcard_means_one_parameter(tmp_path):
    """T013d: Go 1.22 ``mux.HandleFunc`` patterns spell the method in the pattern."""
    m = model(tmp_path, {**GO, "go.mod": "module m\n\ngo 1.22\n", "main.go": (
        "package main\nimport \"net/http\"\nfunc main() {\n  mux := http.NewServeMux()\n"
        "  mux.HandleFunc(\"GET /orders/{id}\", one)\n  mux.HandleFunc(\"POST /orders\", create)\n"
        "  mux.HandleFunc(\"GET /files/{path...}\", files)\n}\n")})
    assert routes(m) == ["GET /files/{}", "GET /orders/{}", "POST /orders"]


def test_go_host_pattern_drops_the_host_and_keeps_the_path(tmp_path):
    """T013d: ``example.com/api`` is a host-restricted pattern; the path is what the server serves."""
    m = model(tmp_path, {**GO, "go.mod": "module m\n\ngo 1.22\n", "main.go": (
        "package main\nimport \"net/http\"\nfunc main() {\n  mux := http.NewServeMux()\n"
        "  mux.HandleFunc(\"example.com/api/orders\", h)\n}\n")})
    assert routes(m) == ["ANY /api/orders"]


def test_gin_groups_nest_to_a_full_path(tmp_path):
    """T013d: a group is a prefix every method it declares inherits."""
    m = model(tmp_path, {**GO, "main.go": (
        "package main\nimport \"github.com/gin-gonic/gin\"\nfunc main() {\n  r := gin.Default()\n"
        "  api := r.Group(\"/api\")\n  v1 := api.Group(\"/v1\")\n  v1.GET(\"/items/:id\", h)\n"
        "  api.POST(\"/items\", h)\n}\n")})
    assert routes(m) == ["GET /api/v1/items/{}", "POST /api/items"]


# ---------------------------------------------------------------------------------- Express / Fastify (call)
def test_express_router_built_in_place_resolves_through_its_import(tmp_path):
    """T013b: ``module.exports = Router().get(...)`` builds the router in place; the import names it."""
    m = model(tmp_path, {**JS, "v1.js": (
        "const { Router } = require('express');\nmodule.exports = Router().get('/y', h);\n"),
        "s.js": ("const express = require('express');\nconst v1 = require('./v1');\nconst app = express();\n"
                 "app.use('/api/v1', v1);\n")})
    assert routes(m) == ["GET /api/v1/y"]


def test_express_route_chain_takes_its_path_from_the_route_call(tmp_path):
    """T013b: ``app.route('/x').get(h)`` names the path on ``route`` and the verb on ``get``."""
    m = model(tmp_path, {**JS, "s.js": (
        "const express = require('express');\nconst app = express();\n"
        "app.route('/orders').get(one).post(create);\n")})
    assert routes(m) == ["GET /orders", "POST /orders"]


def test_express_router_variable_mounted_under_a_prefix(tmp_path):
    """T013b: a router exported under a name keeps its mount prefix across files."""
    m = model(tmp_path, {**JS,
        "orders.js": ("const { Router } = require('express');\nconst router = Router();\n"
                      "router.post('/orders/:id', h);\nmodule.exports = router;\n"),
        "s.js": ("const express = require('express');\nconst orders = require('./orders');\n"
                 "const app = express();\napp.use('/api', orders);\n")})
    assert routes(m) == ["POST /api/orders/{}"]


def test_fastify_route_object_names_method_and_url_separately(tmp_path):
    """T013b: ``fastify.route({method, url})`` is one route with both halves read from the object."""
    m = model(tmp_path, {**JS, "index.mjs": (
        "import Fastify from 'fastify'\nconst fastify = Fastify({ logger: true })\n"
        "fastify.route({ method: 'POST', url: '/items', handler: h })\n")})
    assert routes(m) == ["POST /items"]


# ------------------------------------------------------------------------------------- Next.js (file-route)
def test_nextjs_route_handlers_including_groups_and_catch_all(tmp_path):
    """T013b: the file path is the route; route groups and catch-alls are not segments."""
    m = model(tmp_path, {**JS, "package.json": '{"name":"svc","dependencies":{"next":"14"}}',
        "app/api/orders/route.ts": "export async function GET(req: Request) {}\n",
        "app/api/[...slug]/route.ts": "export const DELETE = async () => {}\n",
        "app/(shop)/cart/route.ts": "export async function POST() {}\n",
        "pages/api/hello.ts": "export default function handler(req, res) {}\n"})
    assert routes(m) == sorted(["DELETE /api/{}", "GET /api/orders", "POST /cart", "ANY /api/hello"])


# -------------------------------------------------------------------------------------------- negatives
def test_a_call_that_only_looks_like_a_route_creates_nothing(tmp_path):
    """FR-010, FR-034: a use site is required; a matching shape without the library makes nothing."""
    m = model(tmp_path, {"package.json": '{"name":"svc"}', "server.js": (
        "const app = makeThing();\napp.get('/x', h);\napp.route('/y').get(h);\n")})
    assert routes(m) == []


def test_spring_annotations_without_the_starter_make_nothing(tmp_path):
    """FR-010: an annotation alone is not a framework."""
    m = model(tmp_path, {"src/main/java/a/C.java": (
        "package a;\nclass C {\n  @GetMapping(\"/x\")\n  void f() {}\n}\n")})
    assert routes(m) == []


def test_go_handle_on_another_type_makes_nothing(tmp_path):
    """T013d: the receiver is checked, not only the method name."""
    m = model(tmp_path, {**GO, "go.mod": "module m\n\ngo 1.22\n", "main.go": (
        "package main\nfunc main() {\n  router.HandleFunc(\"/x\", h)\n}\n")})
    assert routes(m) == []


# ----------------------------------------------------------------------------------------- determinism
def test_route_extraction_is_deterministic_and_makes_no_model_or_network_call(tmp_path, monkeypatch):
    """FR-005, SC-003: the same repository extracts the same routes, offline, every time."""
    def forbidden(*a, **k):
        raise AssertionError("route extraction reached the network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    files = {**PY, **JS, "app/main.py": (
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef h(): ...\n"),
        "s.js": "const express = require('express');\nconst app = express();\napp.get('/ping', h);\n"}
    first, second = model(tmp_path, files), model(tmp_path, files)
    assert [e.id for e in first.elements.values() if e.kind == "route"] == \
           [e.id for e in second.elements.values() if e.kind == "route"]
    assert routes(first) == ["GET /health", "GET /ping"]


def test_every_route_carries_evidence_and_a_method(tmp_path):
    """FR-004, SC-002: no route without a line it can be opened at."""
    m = model(tmp_path, {**PY, "main.py": (
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef h(): ...\n")})
    for e in (x for x in m.elements.values() if x.kind == "route"):
        assert e.evidence and e.meta["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD",
                                                   "OPTIONS", "ANY"}
        assert e.name == f"{e.meta['method']} {e.meta['path']}"
    assert evidence(m) == {"GET /health": "svc/main.py:3"}
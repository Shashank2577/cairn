"""gRPC as an outbound edge (T058): client construction rules (Go ``grpc.NewClient``/``Dial``, Python
``grpc.insecure_channel``, Java ``ManagedChannelBuilder.forAddress``, C# ``GrpcChannel.ForAddress``) and
configured-address wiring — an address-marked environment name a deploy description wires to a sibling
names the edge even when both the call and the read hide behind first-party helpers (Online Boutique's
``mustMapEnv``/``mustConnGRPC``). Also T059: a Kubernetes workload with only an image whose name matches
a path segment of a code unit adopts that unit's code and its name.
"""
from __future__ import annotations

from cairn.system.link import link

from .util import containers, pairs, repo, store

GO_SVC = """package main

import (
    "os"
    "google.golang.org/grpc"
)

func main() {
    conn, err := grpc.NewClient(os.Getenv("ORDERS_SERVICE_ADDR"))
    if err != nil {
        panic(err)
    }
    _ = conn
}
"""

WIRE_MANIFEST = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend
spec:
  template:
    spec:
      containers:
        - name: frontend
          image: gcr.io/example/frontend:1
          env:
            - name: ORDERS_SERVICE_ADDR
              value: "orders:3550"
            - name: OTEL_EXPORTER_OTLP_ENDPOINT
              value: "http://collector:4317"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: orders
spec:
  template:
    spec:
      containers:
        - name: orders
          image: gcr.io/example/orders:1
"""


def _http(m):
    return {p for p in pairs(m) if any(r.rule.startswith("http") for r in m.relationships.values()
                                       if (m.elements[r.from_id].name, m.elements[r.to_id].name) == p)}


def test_grpc_client_rules_link_by_call_and_by_wiring(tmp_path):
    """T058: a Go ``grpc.NewClient`` whose address comes straight from the environment, and a workload
    whose manifest wires ORDERS_SERVICE_ADDR to the orders service (no readable read in code), both
    produce the edge, labelled gRPC; an endpoint naming an unknown collector makes no claim."""
    files = {
        "frontend/Dockerfile": "FROM golang:1.22\n",
        "frontend/go.mod": "module example.com/frontend\nrequire google.golang.org/grpc v1.60.0\n",
        "frontend/main.go": GO_SVC,
        "orders/Dockerfile": "FROM golang:1.22\n",
        "orders/go.mod": "module example.com/orders\n",
        "orders/main.go": "package main\nfunc main() {}\n",
        "k8s.yaml": WIRE_MANIFEST,
    }
    root = repo(tmp_path, "mesh", files)
    store(root)
    m = link(root)
    assert set(containers(m)) == {"frontend", "orders"}
    rels = [r for r in m.relationships.values() if r.rule.startswith("http")]
    assert {("frontend", "orders")} == {(m.elements[r.from_id].name, m.elements[r.to_id].name) for r in rels}
    assert all(r.how.startswith("gRPC") for r in rels)
    assert not [e for e in m.elements.values() if e.meta.get("unresolved")]    # collector: no claim


def test_image_only_workload_adopts_a_code_unit_on_a_path_segment(tmp_path):
    """T059: a Kubernetes workload with only an image, whose name matches a directory segment of a code
    unit (cartservice and src/cartservice/src/Dockerfile), takes that unit's code and its name — no
    'src' container, extracted provenance, and the unit's store uses attribute to it."""
    files = {
        "src/cartservice/src/Dockerfile": "FROM mcr.microsoft.com/dotnet/sdk:8.0\n",
        "src/cartservice/src/Startup.cs": (
            "using Microsoft.Extensions.Caching.StackExchangeRedis;\n"
            "public class Startup {\n"
            "    public void Configure(IServices services) {\n"
            "        services.AddStackExchangeRedisCache(o => o.Configuration = cfg);\n"
            "    }\n"
            "}\n"),
        "k8s.yaml": ("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: cartservice\nspec:\n"
                     "  template:\n    spec:\n      containers:\n        - name: cartservice\n"
                     "          image: gcr.io/example/cartservice:1\n          env:\n"
                     "            - name: REDIS_ADDR\n              value: \"redis-cart:6379\"\n"),
        "redis/k8s.yaml": ("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: redis-cart\nspec:\n"
                           "  template:\n    spec:\n      containers:\n        - name: redis\n"
                           "          image: redis:alpine\n"),
    }
    root = repo(tmp_path, "cart", files)
    store(root)
    m = link(root)
    cs = [e for e in m.elements.values() if e.type == "container"]
    assert {e.name for e in cs} == {"cartservice"} and cs[0].provenance == "extracted"
    assert ("cartservice", "Redis") in pairs(m)
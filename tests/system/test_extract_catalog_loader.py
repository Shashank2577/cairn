"""The catalog is data (FR-010, FR-034; T018): bundled rules, a per-project extension without code changes,
malformed entries skipped with a reason, and the lookups the extractors rely on."""
from __future__ import annotations

import pytest

from cairn.system import catalog
from cairn.system.signals.match import host_of

from .util import build, write


def test_bundled_catalog_loads_cleanly_and_covers_the_first_build():
    """FR-034, FR-010."""
    cat = catalog.load()
    assert cat.skipped == []
    names = {f["framework"] for f in cat.frameworks}
    assert {"fastapi", "flask", "django", "express", "fastify", "nextjs", "spring-web", "net-http", "gin"} <= names
    assert {"psycopg", "pg", "go-sql", "npgsql"} <= {s["client"] for s in cat.stores}
    assert {"redis-py", "kafkajs", "spring-kafka", "kafka-go", "pika", "boto3-sqs"} <= {m["client"] for m in cat.messaging}


def test_project_extension_adds_a_framework_and_a_client_without_code(tmp_path):
    """FR-034: adding a framework or client is a rule in .cairn/catalog/*.yaml plus a fixture."""
    root = write(tmp_path / "svc", {
        ".cairn/catalog/acme.yaml": (
            "frameworks:\n  - framework: acmeweb\n    language: python\n    detect: [acmeweb]\n    routes:\n"
            "      - {kind: decorator, match: ['acmeweb.App().handle'], path: [arg0], default_methods: [POST]}\n"
            "http:\n  - client: acmehttp\n    language: python\n    match: ['acmehttp.send']\n    url: [arg0]\n"
            "    default_methods: [PUT]\n"
            "images:\n  acme/queue: rabbitmq\n"),
        "Dockerfile": "FROM x\n",
        "docker-compose.yml": "services:\n  svc:\n    build: .\n  mq:\n    image: registry.acme.io/acme/queue:2\n",
        "app.py": "import acmeweb, acmehttp\napp = acmeweb.App()\n@app.handle('/hooks')\ndef h():\n"
                  "    acmehttp.send('/api/x')\n"})
    m = build(root, "svc")
    assert {e.name for e in m.elements.values() if e.kind == "route"} == {"POST /hooks"}
    assert {e.name for e in m.elements.values() if e.kind == "http-call"} == {"PUT /api/x"}
    assert any(e.name == "RabbitMQ" and e.type == "channel" for e in m.elements.values())


def test_malformed_project_rules_are_skipped_with_a_reason(tmp_path):
    """FR-034, FR-010."""
    root = write(tmp_path / "svc", {".cairn/catalog/bad.yaml": (
        "frameworks:\n  - framework: x\n    language: cobol\n    routes: [{kind: decorator}]\n"
        "  - {framework: y, language: python, routes: [{kind: telepathy}]}\n"
        "messaging:\n  - {client: z, language: python, tech: redis, ops: [{op: shout, match: [a]}]}\n"
        "stores:\n  - {client: w, language: python, tech: postgresql}\n"
        "http: not-a-list\n"),
        ".cairn/catalog/broken.yaml": "frameworks: [unclosed\n"})
    cat = catalog.load(root)
    reasons = " | ".join(s["reason"] for s in cat.skipped)
    for needle in ("language must be one of", "route kind must be one of", "each op needs op", "a use site",
                   "expected a list", "not valid YAML"):
        assert needle in reasons, needle
    assert len(cat.frameworks) == len(catalog.load().frameworks)


@pytest.mark.parametrize("image,tech", [
    ("postgres:16-alpine", "postgresql"), ("docker.io/library/redis:7", "redis"), ("bitnami/kafka:3.7", "kafka"),
    ("ghcr.io/acme/postgres-tools:1", None), ("mcr.microsoft.com/mssql/server:2022-latest", "sqlserver"),
    ("redis@sha256:abc", "redis"), ("acme/orders", None)])
def test_image_lookup(image, tech):
    """FR-010."""
    assert catalog.load().image_tech(image) == tech


@pytest.mark.parametrize("name,kind", [
    ("SENDGRID_API_KEY", "a SendGrid credential"), ("AWS_SECRET_ACCESS_KEY", "a AWS credential"),
    ("spring.datasource.password", "a database credential"), ("githubToken", "a GitHub credential"),
    ("DATABASE_URL", None), ("API_BASE_URL", None), ("KEYSPACE_NAME", None), ("PAYMENT_SERVICE_HOST", None)])
def test_sensitive_names_become_their_kind(name, kind):
    """FR-029b, FR-011."""
    assert catalog.load().sensitive_kind(name) == kind


@pytest.mark.parametrize("value,want", [
    ("postgresql://u:p@db:5432/x", ("postgresql", "db")), ("jdbc:mysql://mysql.svc:3306/app", ("mysql", "mysql.svc")),
    ("Server=db;Username=u;Password=p;", (None, "db")), ("redis:6379", (None, "redis")), ("kafka:9092,k2:9092", (None, "kafka")),
    ("not a url at all", (None, None)), ("", (None, None))])
def test_connection_value_hosts(value, want):
    """FR-011, FR-029b."""
    assert host_of(value) == want

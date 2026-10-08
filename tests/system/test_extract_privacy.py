"""Privacy (FR-011, FR-029b, data-model rules): configuration and deploy VALUES never reach the model, the
store, evidence or any view; sensitive names appear only as their kind; a sibling resolved through a URL
is stored as its identifier, never as the URL or host string."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from cairn.cli import app
from cairn.system.link import link
from cairn.system.views import view

from .util import code, repo, store

SECRETS = ["AKIAIOSFODNN7EXAMPLE", "sk_live_51HxQyFakeKey000", "hunter2-db-pass", "s3cr3t-redis",
           "jwt-signing-shh", "pg-in-code-pass", "ghp_fakeGitHubToken0123456789"]
SENSITIVE_NAMES = ["SENDGRID_API_KEY", "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD", "JWT_SECRET", "STRIPE_SECRET_KEY",
                   "GITHUB_TOKEN", "spring.datasource.password"]
HOSTS = ["billing.prod.acme.internal", "orders-svc.internal.example", "http://orders-svc", "db.cluster.local"]

FILES = {
    "docker-compose.yml": (
        "services:\n  api:\n    build: .\n    environment:\n"
        "      DATABASE_URL: postgresql://app:hunter2-db-pass@db.cluster.local:5432/app\n"
        "      AWS_ACCESS_KEY_ID: AKIAIOSFODNN7EXAMPLE\n      AWS_SECRET_ACCESS_KEY: wJalrXUtnFEMI\n"
        "      SENDGRID_API_KEY: SG.fake\n      REDIS_URL: redis://:s3cr3t-redis@cache:6379/0\n"
        "  web:\n    build: ../web\n    environment:\n      ORDERS_URL: http://orders-svc.internal.example:8080/api\n"
        "      STRIPE_SECRET_KEY: sk_live_51HxQyFakeKey000\n"
        "  cache:\n    image: redis:7\n"),
    ".env.example": "JWT_SECRET=jwt-signing-shh\nGITHUB_TOKEN=ghp_fakeGitHubToken0123456789\nLOG_LEVEL=info\n",
    "Dockerfile": "FROM python:3.12\nENV DB_PASSWORD=hunter2-db-pass\nCMD [\"api\", \"--token\", \"ghp_x\"]\n",
    "k8s.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: api}\nspec:\n  template:\n    spec:\n"
                "      containers:\n      - name: api\n        image: acme/api\n        env:\n"
                "        - {name: DB_PASSWORD, value: hunter2-db-pass}\n",
    "src/main/resources/application.properties": "spring.datasource.password=hunter2-db-pass\n",
    "app.py": ("import os, requests, psycopg, redis\n"
               "conn = psycopg.connect('postgresql://u:pg-in-code-pass@db.cluster.local/app')\n"
               "key = os.environ['SENDGRID_API_KEY']\nsecret = os.getenv('JWT_SECRET')\n"
               "r = redis.Redis.from_url(os.environ['REDIS_URL'])\nr.publish('events', b'x')\n"
               "requests.post('https://billing.prod.acme.internal/api/charge', json={})\n"),
}


def _everything(tmp_path):
    root = repo(tmp_path, "api", FILES)
    store(root)
    texts = [repr(link(root)), json.dumps(view(root, "container")), json.dumps(view(root, "context"))]
    res = CliRunner().invoke(app, ["system", "--view", "containers", "--json"], env={"CAIRN_ROOT": str(root)},
                             catch_exceptions=False)
    assert res.exit_code == 0 and '"diagram": "container"' in res.output, res.output
    texts.append(res.output)
    texts.append((root / ".cairn" / "brain.db").read_bytes().decode("latin-1"))
    return root, "\n".join(texts)


def test_no_secret_value_reaches_model_store_evidence_or_view(tmp_path, monkeypatch):
    """FR-011, FR-029b: Fake secrets in compose, .env, Dockerfile, Kubernetes, Spring properties and code literals: none of
    them appears anywhere Cairn writes or shows."""
    monkeypatch.chdir(tmp_path)
    _, everything = _everything(tmp_path)
    for s in SECRETS:
        assert s not in everything, s


def test_sensitive_names_only_as_their_kind(tmp_path, monkeypatch):
    """FR-029b, FR-011."""
    monkeypatch.chdir(tmp_path)
    root, everything = _everything(tmp_path)
    for n in SENSITIVE_NAMES:
        assert n not in everything, n
    m = link(root)
    api = next(e for e in m.elements.values() if e.type == "container" and e.name == "api")
    cfg = api.meta["config"]
    kinds = {c.get("kind") for c in cfg}
    names = {c.get("name") for c in cfg}
    assert {"a SendGrid credential", "a JWT credential", "a database credential", "a AWS credential"} & kinds
    assert {"DATABASE_URL", "REDIS_URL"} <= names  # FR-011: non-sensitive names are recorded


def test_hosts_and_urls_of_other_services_are_not_stored(tmp_path, monkeypatch):
    """FR-029b: a URL resolving to a sibling stores the sibling's id; other hosts are kept as one-way keys."""
    monkeypatch.chdir(tmp_path)
    root, everything = _everything(tmp_path)
    for h in HOSTS:
        assert h not in everything, h
    m = link(root)
    call = next(e for e in code(m, "http-call"))
    assert call.meta["host_key"] and len(call.meta["host_key"]) == 16


def test_sibling_url_resolves_to_identifier(tmp_path):
    """FR-029b: A compose URL whose host names a service becomes that element's id in the deploy reference."""
    root = repo(tmp_path, "api", {"docker-compose.yml": (
        "services:\n  api:\n    build: .\n  web:\n    build: ../web\n    environment:\n"
        "      ORDERS_URL: http://api.svc.cluster.local:8080/orders\n")})
    m = store(root)
    ref = code(m, "deploy-ref")[0]
    assert ref.meta["config_targets"] == {"ORDERS_URL": "api:container:api"}
    assert "svc.cluster.local" not in repr(m)

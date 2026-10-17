"""Containers per deploy unit, from deploy and run descriptions and entry points (FR-006, FR-029a,
edge cases: monorepo, no deploy files, library repository; T011, T012, T019)."""
from __future__ import annotations

from .util import build, code, containers, write


def m_of(tmp_path, files, name="repo"):
    return build(write(tmp_path / name, files), name)


def test_compose_services_built_here_are_containers_images_are_stores(tmp_path):
    """FR-006: one container per compose service with build:, stores and channels from catalog images."""
    m = m_of(tmp_path, {
        "docker-compose.yml": "services:\n  api:\n    build: ./api\n    ports: ['8000:8000']\n"
                              "  web:\n    build:\n      context: ./web\n  db:\n    image: postgres:16-alpine\n"
                              "  queue:\n    image: docker.io/library/rabbitmq:3-management\n",
        "api/app.py": "print('api')\n", "web/index.js": "console.log(1)\n"})
    cs = containers(m)
    assert set(cs) == {"api", "web"}
    assert cs["api"].meta["root"] == "api" and "8000" in cs["api"].meta["ports"]
    types = {e.name: e.type for e in m.elements.values() if e.level == "container"}
    assert types["PostgreSQL"] == "data-store" and types["RabbitMQ"] == "channel"
    assert all(e.evidence for e in m.elements.values())


def test_root_build_is_named_after_the_repository_with_the_service_as_alias(tmp_path):
    """FR-006, FR-001."""
    m = m_of(tmp_path, {"docker-compose.yml": "services:\n  api:\n    build: .\n", "Dockerfile": "FROM x\nEXPOSE 9000\n"},
             name="orders-service")
    cs = containers(m)
    assert list(cs) == ["orders-service"]
    assert cs["orders-service"].meta["aliases"] == ["api"]
    assert "9000" in cs["orders-service"].meta["ports"]


def test_dockerfile_alone_and_dockerfile_stages(tmp_path):
    """FR-006: T011: EXPOSE and CMD of the final stage; the start command is cut to the executable and one arg."""
    m = m_of(tmp_path, {"svc/Dockerfile": "FROM golang AS build\nEXPOSE 1\nFROM alpine\nEXPOSE 8080\n"
                                         'CMD ["/app", "--password=hunter2", "serve"]\n'})
    c = containers(m)["svc"]
    assert c.meta["ports"] == ["8080"]
    refs = " ".join(e.ref() for e in c.evidence)
    assert "CMD /app" in refs or "svc/Dockerfile:" in refs
    assert "hunter2" not in repr(m)


def test_kubernetes_workloads_services_and_jobs(tmp_path):
    """FR-006, FR-011: T011: workloads become containers (Job/CronJob are jobs); a Service name is an alias of the workloads
    it selects; catalog images are stores; templated (Helm) manifests are skipped, not fatal."""
    m = m_of(tmp_path, {
        "k8s/app.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: orders}\nspec:\n  replicas: 3\n"
                        "  template:\n    metadata: {labels: {app: orders}}\n    spec:\n      containers:\n"
                        "      - name: orders\n        image: acme/orders:1.2\n        env:\n"
                        "        - {name: DB_PASSWORD, value: not-a-real-pass}\n---\n"
                        "apiVersion: v1\nkind: Service\nmetadata: {name: orders-svc}\nspec:\n  selector: {app: orders}\n"
                        "---\napiVersion: batch/v1\nkind: CronJob\nmetadata: {name: nightly}\nspec:\n  jobTemplate:\n"
                        "    spec:\n      template:\n        spec:\n          containers:\n          - {name: n, image: acme/n}\n"
                        "---\napiVersion: apps/v1\nkind: StatefulSet\nmetadata: {name: cache}\nspec:\n  template:\n"
                        "    spec:\n      containers:\n      - {name: r, image: 'redis:7'}\n",
        "charts/x/templates/d.yaml": "apiVersion: v1\nkind: Deployment\nmetadata:\n  name: {{ .Values.name }}\n"})
    cs = containers(m)
    assert set(cs) == {"orders", "nightly"}
    assert cs["orders"].meta["count"] == 3 and "orders-svc" in cs["orders"].meta["aliases"]
    assert cs["orders"].provenance == "inferred"  # an image-only workload: no code of its own here
    assert cs["nightly"].kind == "job"
    assert any(e.name == "Redis" for e in m.elements.values())
    assert "not-a-real-pass" not in repr(m)


def test_procfile_processes(tmp_path):
    """FR-006."""
    m = m_of(tmp_path, {"Procfile": "web: gunicorn app:app\nworker: celery -A app worker\n",
                        "app.py": "x = 1\n"}, name="heroku-app")
    cs = containers(m)
    assert set(cs) == {"heroku-app", "worker"}
    assert cs["worker"].meta.get("shares_code_with") == cs["heroku-app"].id


def test_entry_points_without_deploy_files(tmp_path):
    """FR-006, FR-001: T012: [project.scripts], package.json bin/start, Go package main, Spring Boot, .NET, __main__.py."""
    cases = {
        "py": {"pyproject.toml": '[project]\nname = "tool"\n[project.scripts]\ntool = "tool:main"\n',
               "tool/__init__.py": ""},
        "js": {"package.json": '{"name": "cli", "bin": {"cli": "bin/cli.js"}}'},
        "go": {"go.mod": "module x\n", "main.go": "package main\nfunc main() {}\n"},
        "java": {"pom.xml": "<project><artifactId>app</artifactId><build><plugins><plugin><artifactId>"
                            "spring-boot-maven-plugin</artifactId></plugin></plugins></build></project>"},
        "net": {"App.csproj": '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
                              "</PropertyGroup></Project>", "Program.cs": "class P { static void Main() {} }\n"},
        "pymain": {"pkg/__main__.py": "print(1)\n", "pkg/__init__.py": ""},
    }
    for name, files in cases.items():
        m = m_of(tmp_path, files, name=name)
        cs = containers(m)
        assert len(cs) == 1, (name, cs)
        c = next(iter(cs.values()))
        assert c.provenance == "extracted" and c.evidence, name
    py = containers(m_of(tmp_path, cases["py"], name="py2"))
    assert next(iter(py.values())).kind == "cli"


def test_go_monorepo_with_several_main_packages(tmp_path):
    """FR-006: Monorepo edge case: each `package main` directory is its own container."""
    m = m_of(tmp_path, {"go.mod": "module x\n", "cmd/api/main.go": "package main\nfunc main() {}\n",
                        "cmd/worker/main.go": "package main\nfunc main() {}\n", "internal/x.go": "package x\n"})
    assert set(containers(m)) == {"api", "worker"}


def test_library_repository_is_a_library_not_a_container(tmp_path):
    """FR-002, FR-006: Edge case: a package with no entry point or deploy file is a library (build-time)."""
    m = m_of(tmp_path, {"pyproject.toml": '[project]\nname = "acme-contracts"\n',
                        "acme_contracts/__init__.py": "X = 1\n"})
    libs = [e for e in m.elements.values() if e.type == "library"]
    assert len(libs) == 1 and not containers(m)
    assert {"acme-contracts"} <= set(libs[0].meta["packages"])


def test_repository_with_nothing_is_one_container_marked_entry_points_not_found(tmp_path):
    """FR-006, FR-004: Edge case: no deploy files and no entry points: one container, typed by language, not dropped."""
    m = m_of(tmp_path, {"lib/a.py": "def f():\n    return 1\n", "lib/b.py": "X = 2\n"}, name="scripts")
    cs = containers(m)
    assert list(cs) == ["scripts"]
    c = cs["scripts"]
    assert c.provenance == "inferred" and c.desc == "entry points not found"
    assert c.tech == "Python" and c.kind is None and c.evidence


def test_empty_repository(tmp_path):
    """FR-006."""
    m = m_of(tmp_path, {}, name="empty")
    cs = containers(m)
    assert list(cs) == ["empty"] and cs["empty"].meta.get("entry_points_not_found")


def test_deploy_files_under_test_and_example_trees_are_ignored(tmp_path):
    """FR-006: Test rigs (tests/, docker-compose.test.yml, examples/, docs/) describe no part of the system."""
    m = m_of(tmp_path, {
        "Dockerfile": "FROM x\n",
        "tests/Dockerfile": "FROM y\n", "docker-compose.test.yml": "services:\n  sut:\n    build: ./tests\n",
        "examples/demo/docker-compose.yml": "services:\n  demo:\n    build: .\n  db:\n    image: mongo\n",
        "docs/Dockerfile": "FROM z\n"}, name="svc")
    assert set(containers(m)) == {"svc"}
    assert not [e for e in m.elements.values() if e.type == "data-store"]


def test_compose_service_built_from_a_sibling_directory_is_a_deploy_reference(tmp_path):
    """FR-011, FR-029b, FR-013: A compose file that builds ../other records where it points and which configuration names resolve
    to which element (never the values)."""
    m = m_of(tmp_path, {"docker-compose.yml": (
        "services:\n  api:\n    build: .\n  web:\n    build: ../web\n    environment:\n"
        "      API_URL: http://api:8000/v1\n      STRIPE_SECRET_KEY: sk_live_abc123\n")}, name="api-repo")
    refs = code(m, "deploy-ref")
    assert len(refs) == 1
    meta = refs[0].meta
    assert meta["build"] == "../web" and meta["config_targets"] == {"API_URL": "api-repo:container:api-repo"}
    assert {"kind": "a Stripe credential"} in meta["config"]
    text = repr(m)
    assert "sk_live_abc123" not in text and "STRIPE_SECRET_KEY" not in text and "http://api:8000" not in text


def test_kinds_are_marked_inferred(tmp_path):
    """FR-029a: web-app versus service cannot be read directly; the kind is marked inferred."""
    m = m_of(tmp_path, {"Dockerfile": "FROM node\n",
                        "package.json": '{"name": "site", "dependencies": {"express": "4", "react": "18"}}',
                        "server.js": "const express = require('express');\nconst app = express();\napp.get('/', h);\n"})
    c = next(iter(containers(m).values()))
    assert c.kind == "web-app" and c.meta["kind_inferred"] is True

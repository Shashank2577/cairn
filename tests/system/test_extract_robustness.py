"""Malformed and hostile inputs never break extraction, and are bounded by limits.py (FR-005, edge cases;
T019): broken, empty, huge, binary and non-UTF-8 files; missing or malformed manifests and deploy files;
unknown frameworks; a symbolic-link loop; an unreadable file; pathological nesting."""
from __future__ import annotations

import os
import sys

import pytest

from cairn.system.limits import MAX_FILE_BYTES

from .util import build, containers, write

ROUTE = "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/ok')\ndef ok():\n    pass\n"
PY = {"pyproject.toml": '[project]\nname = "svc"\ndependencies = ["fastapi"]\n', "Dockerfile": "FROM x\n"}


def routes(m):
    return {e.name for e in m.elements.values() if e.kind == "route"}


def test_broken_empty_huge_binary_and_non_utf8_files(tmp_path):
    """FR-005, FR-007."""
    root = write(tmp_path / "svc", {**PY, "ok.py": ROUTE,
                                    "broken.py": "def (:\n  @app.get('/x'\n",
                                    "broken.js": "const = require(;\napp.get('/y', ",
                                    "empty.py": "",
                                    "huge.py": ROUTE.replace("/ok", "/huge") + "#" * (MAX_FILE_BYTES + 10),
                                    "binary.py": b"\x00\x01\x02" + ROUTE.encode() + b"\x00",
                                    "latin.py": ROUTE.replace("/ok", "/latin").encode("utf-8") + b"\n# caf\xe9\n"})
    m = build(root, "svc")
    assert routes(m) == {"GET /ok", "GET /latin"}
    assert m.stats["skipped: larger than the file limit"] == 1 and m.stats["skipped: binary"] == 1


def test_malformed_manifests_and_deploy_files_are_skipped(tmp_path):
    """FR-006."""
    root = write(tmp_path / "svc", {
        "pyproject.toml": "[project\nname = ",
        "package.json": "{not json",
        "pom.xml": '<?xml version="1.0"?><!DOCTYPE p [<!ENTITY x "y">]><project><artifactId>&x;</artifactId></project>',
        "go.mod": "\x00garbage",
        "docker-compose.yml": "services: [unclosed\n  - :",
        "k8s/bomb.yaml": "apiVersion: v1\nkind: Deployment\na: &a [x, x, x, x, x, x, x, x, x, x]\n"
                         + "".join(f"b{i}: &b{i} [*{'a' if i == 0 else 'b' + str(i - 1)}, *{'a' if i == 0 else 'b' + str(i - 1)}]\n"
                                   for i in range(120)),
        "Procfile": ":::\n",
        "app.py": "x = 1\n"})
    m = build(root, "svc")
    assert list(containers(m)) == ["svc"]  # nothing usable: one container, entry points not found
    assert containers(m)["svc"].meta.get("entry_points_not_found")


def test_unknown_framework_and_unknown_language_create_nothing(tmp_path):
    """FR-034, FR-007."""
    root = write(tmp_path / "svc", {"Dockerfile": "FROM x\n", "app.rb": "get '/x' do\n  'hi'\nend\n",
                                    "server.ex": "get \"/x\", Ctrl, :index\n",
                                    "weird.py": "from mystery import Router\nr = Router()\n@r.get('/x')\ndef f():\n    pass\n"})
    m = build(root, "svc")
    assert routes(m) == set() and list(containers(m)) == ["svc"]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlink_loop_is_not_followed(tmp_path):
    """FR-005."""
    root = write(tmp_path / "svc", {**PY, "ok.py": ROUTE})
    os.symlink(root, root / "loop")
    os.symlink(root / "ok.py", root / "alias.py")
    m = build(root, "svc")
    assert routes(m) == {"GET /ok"}


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="POSIX permissions; root can read anything")
def test_unreadable_file_is_skipped(tmp_path):
    """FR-005."""
    root = write(tmp_path / "svc", {**PY, "ok.py": ROUTE, "secret.py": ROUTE.replace("/ok", "/hidden")})
    os.chmod(root / "secret.py", 0)
    try:
        m = build(root, "svc")
    finally:
        os.chmod(root / "secret.py", 0o644)
    assert routes(m) == {"GET /ok"}
    assert m.stats["skipped: unreadable"] >= 1


def test_pathological_nesting_does_not_crash(tmp_path):
    """FR-005."""
    deep = "x = " + "(" * 3000 + "1" + ")" * 3000 + "\n"
    root = write(tmp_path / "svc", {**PY, "ok.py": ROUTE, "deep.py": "import os\n" + deep,
                                    "deep.yml": "a: " + "[" * 500 + "]" * 500 + "\n"})
    m = build(root, "svc")
    assert routes(m) == {"GET /ok"}


def test_minified_bundles_are_not_read_as_code(tmp_path):
    """FR-008."""
    bundle = "var a=1;" * 20000 + "fetch('/api/never')"
    root = write(tmp_path / "svc", {"Dockerfile": "FROM x\n", "static/app.min.js": bundle, "static/bundle.js": bundle})
    m = build(root, "svc")
    assert not [e for e in m.elements.values() if e.kind == "http-call"]

"""Windows paths and line endings in evidence references (T046; FR-004).

An evidence reference is ``repo/path:line``, stored in one column of one row and printed in one cell
of a diagram, so a path must arrive in POSIX form whatever produced it: ``\\`` separators, a drive
letter, a UNC share, a CRLF line ending or a stray NUL all have to become a plain relative path that
never escapes the repository. Covered here: ``model.posix`` and ``Evidence.ref`` directly, and one
end-to-end build of a repository whose files are written with CRLF endings.
"""
from __future__ import annotations

import pytest

from cairn.system.build import build_repo
from cairn.system.model import Evidence, clip, posix


@pytest.mark.parametrize("raw,want", [
    ("app/main.py", "app/main.py"),
    ("app\\main.py", "app/main.py"),
    ("app\\sub\\main.py", "app/sub/main.py"),
    ("app/sub\\main.py", "app/sub/main.py"),
    ("./app/main.py", "app/main.py"),
    ("C:\\Users\\ada\\shop\\main.py", "Users/ada/shop/main.py"),
    ("c:/Users/ada/shop/main.py", "Users/ada/shop/main.py"),
    ("\\\\server\\share\\main.py", "main.py"),
    ("\\\\?\\C:\\shop\\main.py", "shop/main.py"),
    ("C:", None),
])
def test_windows_paths_become_posix_relative_paths(raw, want):
    """T046: separators, drive letters and UNC shares normalise to one repository-relative spelling."""
    assert posix(raw) == want


@pytest.mark.parametrize("raw", [
    "..", "../..", "..\\..", "app/../../etc/passwd", "app\\..\\..\\etc\\passwd", "C:\\..\\..\\Windows\\System32",
    "/etc/passwd", "\\\\server\\share\\..\\..\\secret"])
def test_traversal_and_absolute_paths_never_escape_the_repository(raw):
    """T046: no '..' survives and nothing stays absolute, whatever the separators."""
    out = posix(raw)
    assert out is None or (".." not in out.split("/") and not out.startswith("/") and ":" not in out)


@pytest.mark.parametrize("raw,want", [
    ("app/main.py\r\n", "app/main.py"),
    ("app\\main.py\r\n", "app/main.py"),
    ("app/main.py\n", "app/main.py"),
    ("app/main.py\x00", "app/main.py"),
    ("  app/main.py  ", "app/main.py"),
])
def test_line_endings_and_control_characters_never_reach_a_path(raw, want):
    """T046: a path is one line of one row; CRLF and NUL are removed rather than stored."""
    assert posix(raw) == want


@pytest.mark.parametrize("raw,want", [
    ("services.db image: postgres:16\r\ndepends_on: db\r\n", "services.db image: postgres:16 depends_on: db"),
    ("services.db\r\n\timage: redis:7", "services.db image: redis:7"),
])
def test_a_crlf_entry_collapses_to_one_line(raw, want):
    """T046: a deploy entry read from a CRLF file is one line, never two."""
    assert clip(raw) == want


def test_evidence_ref_reads_the_same_on_every_platform():
    """T046, FR-004: ``repo/path:line`` with a Windows path stored, an entry when there is no line."""
    ev = Evidence(repo="shop-api", kind="route", file="src\\cairn\\system\\routes.py", line=42)
    assert ev.file == "src/cairn/system/routes.py"
    assert ev.ref() == "shop-api/src/cairn/system/routes.py:42"
    entry = Evidence(repo="shop-api", kind="manifest", file="docker-compose.yml", entry="services.db build .")
    assert entry.ref() == "shop-api/docker-compose.yml: services.db build ."
    assert Evidence(repo="r", kind="declared", entry="repos: ../api").ref() == "r: repos: ../api"
    assert Evidence(repo="r", kind="route", file="a.py", line=0).ref() == "r/a.py"


def test_evidence_ref_keeps_a_windows_path_on_one_line(tmp_path):
    """T046 end to end: a repository written with CRLF endings still yields one-line references."""
    root = tmp_path / "svc"
    files = {
        "pyproject.toml": '[project]\r\nname = "svc"\r\ndependencies = ["fastapi"]\r\n',
        "Dockerfile": "FROM python\r\n",
        "app/main.py": ("from fastapi import FastAPI\r\n\r\napp = FastAPI()\r\n\r\n\r\n"
                        "@app.post('/orders')\r\ndef create():\r\n    pass\r\n"),
    }
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body.encode("utf-8"))
    m = build_repo(root, "svc")
    refs = [e.ref() for e in m.elements.values() for e in e.evidence]
    assert any(r.endswith("app/main.py:6") for r in refs), refs
    assert all("\r" not in r and "\n" not in r for r in refs), refs
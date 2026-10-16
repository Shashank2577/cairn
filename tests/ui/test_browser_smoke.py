"""The real-browser check runs with the test suite: every page, every dropdown, every engine view in Chrome (spec 012).

A server whose files were deleted answered /api/health for days while its UI returned 500; no non-browser test
saw it. These tests start real servers: one healthy (the check must pass) and one whose code is deleted after it
starts (the check must fail and the verdict must say why). They skip when Node 22+ or Chrome is missing.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from cairn import daemon
from cairn.project import Project

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "tests" / "ui" / "smoke.mjs"
CHROMES = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome",
           "/usr/bin/chromium", "/usr/bin/chromium-browser")


def _node_ok() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    out = subprocess.run([node, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    try:
        return int(out.lstrip("v").split(".")[0]) >= 22
    except ValueError:
        return False


def _chrome_ok() -> bool:
    return bool(os.environ.get("CHROME_PATH")) or any(Path(c).exists() for c in CHROMES)


pytestmark = pytest.mark.skipif(not (_node_ok() and _chrome_ok()), reason="needs Node 22+ and Chrome or Chromium")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _up(url: str, timeout: float = 40) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1) as r:
                if json.loads(r.read()).get("ok"):
                    return True
        except (OSError, ValueError):
            time.sleep(0.3)
    return False


def _smoke(url: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", str(SMOKE), "--base", url, "--widths", "1440", "--timeout=60"], capture_output=True, text=True,
                          timeout=900, check=False)


@pytest.fixture
def server(repo, tmp_path, monkeypatch):
    """Start `cairn serve` for the throwaway repository in its own Cairn home; yield (url, home, pythonpath)."""
    home = tmp_path / "cairn-home"
    home.mkdir()
    monkeypatch.setenv("CAIRN_HOME", str(home))
    daemon.register(Project.discover(repo))
    procs: list[subprocess.Popen] = []

    def start(pythonpath: str | None = None) -> str:
        port = _free_port()
        env = {**os.environ, "CAIRN_HOME": str(home)}
        if pythonpath:
            env["PYTHONPATH"] = pythonpath
        procs.append(subprocess.Popen([sys.executable, "-m", "cairn", "serve", "--port", str(port)], cwd=str(home),
                                      env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        url = f"http://127.0.0.1:{port}"
        assert _up(url), "the server did not start"
        return url

    yield start, home
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


def test_a_healthy_server_passes_every_page_dropdown_and_engine_view(server):
    start, _ = server
    out = _smoke(start())
    assert out.returncode == 0, out.stdout + out.stderr
    assert "page visits passed" in out.stdout


def test_a_server_whose_code_was_deleted_fails_the_check_and_is_named_stale(server, tmp_path):
    start, home = server
    copy = tmp_path / "code"
    shutil.copytree(ROOT / "src" / "cairn", copy / "cairn", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    url = start(str(copy))
    assert _smoke(url).returncode == 0  # healthy while its files exist
    shutil.rmtree(copy)  # the incident: the server keeps answering /api/health, its code and UI are gone
    out = _smoke(url)
    assert out.returncode != 0, out.stdout
    record = json.loads((home / "server.json").read_text(encoding="utf-8"))
    verdict = daemon.verdict(record)
    assert verdict and verdict["stale"] == "code-missing" and "cairn up" in verdict["fix"]

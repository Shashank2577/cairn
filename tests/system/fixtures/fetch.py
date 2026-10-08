"""Fetch a pinned public repository for fixture tests (the only network access in the system-model tests).

The repository is cloned once into a cache directory (``CAIRN_TEST_CACHE``, default
``<repo>/.pytest-fixture-cache/``), checked out at the pinned commit and verified; it is never vendored
into this repository. Offline, or when git is missing, the calling test is skipped, not failed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

VOTING_APP_URL = "https://github.com/dockersamples/example-voting-app"
VOTING_APP_COMMIT = "63e9150ca17af4ed05880d4245e486481f73fcb4"


def cache_dir() -> Path:
    return Path(os.environ.get("CAIRN_TEST_CACHE") or ROOT / ".pytest-fixture-cache")


def _git(*args: str, cwd: Path | None = None, timeout: float = 120) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, check=True).stdout.strip()


def _head(path: Path) -> str | None:
    try:
        return _git("rev-parse", "HEAD", cwd=path)
    except (subprocess.SubprocessError, OSError):
        return None


def pinned_repo(url: str, commit: str, name: str) -> Path:
    """A checkout of ``url`` at ``commit``; skips the test when it cannot be obtained."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    dest = cache_dir() / name
    if _head(dest) == commit:
        return dest
    tmp = dest.with_name(name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        _git("init", "-q", str(tmp))
        _git("fetch", "-q", "--depth", "1", url, commit, cwd=tmp, timeout=300)
        _git("-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD", cwd=tmp)
    except (subprocess.SubprocessError, OSError) as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        pytest.skip(f"could not fetch {url} at {commit[:12]} (offline?): {str(exc)[:120]}")
    if _head(tmp) != commit:
        shutil.rmtree(tmp, ignore_errors=True)
        pytest.fail(f"{url} checkout is not at the pinned commit {commit}")
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return dest


def voting_app() -> Path:
    return pinned_repo(VOTING_APP_URL, VOTING_APP_COMMIT, "example-voting-app")

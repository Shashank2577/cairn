"""Everything the workflow ships works with the network switched off."""
from __future__ import annotations

import socket
import urllib.request

import pytest
from workflow_helpers import git_repo, init, spec


class NetworkUsed(AssertionError):
    pass


@pytest.fixture()
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise NetworkUsed("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


def test_full_setup_and_discovery_offline(tmp_path, monkeypatch, no_network):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))  # Path.home() on Windows
    root = git_repo(tmp_path / "repo")
    init(root, "claude", "--preset", "lean", "--extension", "git")
    steps = [
        ("extension", "search"), ("extension", "add", "bug"), ("extension", "info", "assess"),
        ("extension", "catalog", "list"),
        ("preset", "search"), ("preset", "add", "constitution-sync"), ("preset", "info", "lean"),
        ("workflow", "search"), ("workflow", "add", "bugfix"), ("workflow", "info", "assess"),
        ("workflow", "step", "search"),
        ("bundle", "search"), ("bundle", "install", "assess"), ("bundle", "info", "bugfix"),
        ("integration", "search"), ("integration", "info", "gemini"), ("integration", "install", "gemini", "--force"),
        ("version",), ("check",),
    ]
    for args in steps:
        code, out = spec(root, *args)
        assert code == 0, f"cairn spec {' '.join(args)} -> {code}\n{out}"
        assert "network access attempted" not in out

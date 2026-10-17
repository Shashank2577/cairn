"""Deep mode always says why it is on or off, and finds the Claude Code CLI where it is installed (spec 012)."""
from __future__ import annotations

import os
import stat

import pytest

from cairn import router as router_mod
from cairn.project import Project
from cairn.router import Router


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "CAIRN_API_KEY", "OPENAI_API_KEY", "CAIRN_NO_CLI_MODELS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(router_mod.shutil, "which", lambda name, *a, **k: None)  # nothing on PATH


def _router(tmp_path, toml: str = "") -> Router:
    (tmp_path / ".cairn").mkdir(exist_ok=True)
    (tmp_path / ".cairn" / "config.toml").write_text(toml, encoding="utf-8")
    project = Project(tmp_path)
    project.reload()
    return Router(project)


def _fake_claude(directory):
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / ("claude.cmd" if os.name == "nt" else "claude")
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


def test_cli_found_in_a_standard_location_when_not_on_path(tmp_path, monkeypatch):
    exe = _fake_claude(tmp_path / "bin")
    monkeypatch.setattr(router_mod, "CLAUDE_DIRS", (tmp_path / "bin",))
    state = _router(tmp_path).deep_state()
    assert state["enabled"] is True
    assert state["backend"] == "claude-code"
    assert state["claude_path"] == str(exe)
    assert str(exe) in state["reason"]


def test_off_names_the_missing_prerequisite_and_the_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(router_mod, "CLAUDE_DIRS", (tmp_path / "nowhere",))
    state = _router(tmp_path, '[models]\nprovider = "claude-code"\n').deep_state()
    assert state["enabled"] is False
    assert state["backend"] == "claude-code"
    assert "Claude Code" in state["reason"] and "not found" in state["reason"]
    assert "claude" in state["fix"] and "ANTHROPIC_API_KEY" in state["fix"]


def test_off_by_configuration_names_the_key(tmp_path, monkeypatch):
    monkeypatch.setattr(router_mod, "CLAUDE_DIRS", (tmp_path / "nowhere",))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    state = _router(tmp_path, "[deep]\nenabled = false\n").deep_state()
    assert state["enabled"] is False
    assert "deep.enabled" in state["reason"] and "config" in state["reason"]


def test_on_with_an_api_key_names_the_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(router_mod, "CLAUDE_DIRS", (tmp_path / "nowhere",))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    state = _router(tmp_path).deep_state()
    assert state["enabled"] is True and state["backend"] == "anthropic"
    assert "ANTHROPIC_API_KEY" in state["reason"]


def test_nothing_available_says_how_to_turn_it_on(tmp_path, monkeypatch):
    monkeypatch.setattr(router_mod, "CLAUDE_DIRS", (tmp_path / "nowhere",))
    state = _router(tmp_path).deep_state()
    assert state["enabled"] is False and state["backend"] is None
    assert state["reason"] and state["fix"]


def test_deep_enabled_agrees_with_deep_state(tmp_path, monkeypatch):
    exe = _fake_claude(tmp_path / "bin")
    monkeypatch.setattr(router_mod, "CLAUDE_DIRS", (tmp_path / "bin",))
    r = _router(tmp_path)
    assert r.deep_enabled() is r.deep_state()["enabled"] is True
    assert r.available is True and exe.exists()

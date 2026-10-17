"""Every limit that is called a "budget" has a plain name, a value, usage where measurable and a consequence (spec 012)."""
from __future__ import annotations

import time

from cairn.budgets import budgets
from cairn.project import Project
from cairn.store import Brain


def _project(tmp_path, toml: str = "") -> Project:
    (tmp_path / ".cairn").mkdir(exist_ok=True)
    (tmp_path / ".cairn" / "config.toml").write_text(toml, encoding="utf-8")
    project = Project(tmp_path)
    project.reload()
    return project


def test_every_budget_is_explained_in_plain_words(tmp_path):
    rows = budgets(_project(tmp_path))
    assert [r["key"] for r in rows] == ["context.budget", "deep.budget_tokens", "deep.wall_seconds",
                                        "memory.wall_seconds"]
    for r in rows:
        assert r["name"] and r["meaning"] and r["when_exceeded"] and r["unit"] in ("tokens", "seconds")
        assert "budget" not in r["name"].lower()  # the word is the confusion; the name says what is limited


def test_values_come_from_the_project_config(tmp_path):
    rows = {r["key"]: r for r in budgets(_project(tmp_path, "[context]\nbudget = 900\n[deep]\nbudget_tokens = 5000\n"))}
    assert rows["context.budget"]["value"] == 900
    assert rows["deep.budget_tokens"]["value"] == 5000
    assert rows["deep.wall_seconds"]["value"] == 300  # the default when unset


def test_deep_allowance_shows_what_the_last_day_spent(tmp_path):
    project = _project(tmp_path)
    brain = Brain(tmp_path / "b.db")
    brain.log_call("why", "deep", "claude-opus-5-5", {"input": 1000, "output": 500, "cache_read": 0, "cache_write": 0}, True)
    rows = {r["key"]: r for r in budgets(project, brain)}
    assert rows["deep.budget_tokens"]["used"] == 1500
    assert rows["context.budget"]["used"] is None  # nothing served yet
    brain.log_query("mcp", "context", "x", 700, 5000)
    brain.log_query("mcp", "context", "y", 500, 4000)
    assert {r["key"]: r for r in budgets(project, brain)}["context.budget"]["used"] == 600  # typical pack


def test_usage_is_unknown_without_a_store(tmp_path):
    rows = budgets(_project(tmp_path))
    assert all(r["used"] is None for r in rows)
    assert time.time() > 0


def test_the_overview_api_carries_the_deep_reason_and_the_budgets(cairn, tmp_path):
    from fastapi.testclient import TestClient

    from cairn.platform import Platform, ServerConfig
    from cairn.server import create_app
    config = ServerConfig(mode="local")
    app = create_app(platform=Platform(home=tmp_path / "home", config=config), config=config,
                     register=[cairn.project.root])
    client = TestClient(app, base_url="http://127.0.0.1")
    pid = client.get("/api/session").json()["projects"][0]["id"]
    ov = client.get(f"/api/p/{pid}/overview").json()
    deep = ov["models"]["deep_state"]
    assert deep["reason"] and ov["models"]["deep"] == deep["enabled"]
    assert [b["key"] for b in ov["budgets"]][0] == "context.budget"
    assert all(b["when_exceeded"] for b in ov["budgets"])

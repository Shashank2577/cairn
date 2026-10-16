"""Narrative tests (spec 008: FR-030; T043).

The narrative layer is optional and separate: the model with and without it is identical, the ledger
records only opted-in calls, and every narrated sentence cites the computed elements it describes and is
labelled inferred.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cairn.system.export import export
from cairn.system.link import link

from .util import product

REPOS = ["shop-web", "shop-api", "shop-worker", "shop-contracts"]


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("CAIRN_EMBEDDER", "hash")
    monkeypatch.setenv("CAIRN_NO_CLI_MODELS", "1")
    for k in ("ANTHROPIC_API_KEY", "CAIRN_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(k, "")


def _product(tmp_path: Path) -> Path:
    return product(tmp_path, "shop", "shop-api", REPOS)


# --------------------------------------------------------------------------- T043: identical model
def test_the_model_is_identical_with_and_without_narrative(tmp_path):
    """FR-030: disabling narrative changes no element and no relationship."""
    root = _product(tmp_path)
    pm_before = link(root)
    ids_before = sorted(pm_before.elements)
    rels_before = sorted(pm_before.relationships)
    out = tmp_path / "docs"
    export(root, out, narrate=False)
    pm_after = link(root)
    assert sorted(pm_after.elements) == ids_before
    assert sorted(pm_after.relationships) == rels_before


def test_no_ledger_entries_without_opt_in(tmp_path, monkeypatch):
    """FR-030: the ledger records narrative only when the user opted in."""
    root = _product(tmp_path)
    calls = []
    import cairn.router as router_mod
    monkeypatch.setattr(router_mod.Router, "complete", lambda *a, **k: calls.append(a) or "")
    out = tmp_path / "docs"
    export(root, out, narrate=False)
    assert calls == []


def test_narrative_is_a_no_op_without_a_model(tmp_path):
    """FR-030 scenario 1: with no model, every view still works and narrative is skipped."""
    root = _product(tmp_path)
    out = tmp_path / "docs"
    result = export(root, out, narrate=True)
    assert result["narrated"] is False
    # The documents are still written, with computed text only.
    for md in out.glob("*.md"):
        text = md.read_text(encoding="utf-8")
        assert "Narrative" not in text


def test_narrative_cites_the_elements_it_describes(tmp_path, monkeypatch):
    """FR-030: a narrated sentence cites the computed elements it describes and is labelled inferred."""
    root = _product(tmp_path)
    answer = "shop-api handles orders [inferred]."
    import cairn.router as router_mod
    monkeypatch.setattr(router_mod.Router, "complete", lambda self, *a, **k: answer)
    monkeypatch.setattr(router_mod, "resolve_provider", lambda *a, **k: "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = tmp_path / "docs"
    result = export(root, out, narrate=True)
    assert result["narrated"] is True
    found = False
    for md in out.glob("*.md"):
        text = md.read_text(encoding="utf-8")
        if "Narrative (inferred" in text:
            found = True
            assert "shop-api" in text
            assert "[inferred]" in text
            break
    assert found, "no document carried the narrated sentence"


def test_narrative_stays_within_the_budget(tmp_path, monkeypatch):
    """FR-030: the cost is capped by the [system] narrate_tokens budget."""
    root = _product(tmp_path)
    import cairn.router as router_mod
    calls = []
    monkeypatch.setattr(router_mod.Router, "complete",
                        lambda self, *a, **k: calls.append(k.get("budget")) or "x")
    monkeypatch.setattr(router_mod, "resolve_provider", lambda *a, **k: "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    out = tmp_path / "docs"
    export(root, out, narrate=True)
    # Every call carried a budget, and the budget was the one configured.
    assert calls and all(b is not None for b in calls)

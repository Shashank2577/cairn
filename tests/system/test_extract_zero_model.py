"""Zero model calls and no network (FR-005, SC-003): building, syncing, linking and viewing the system model
never reaches a model or the network, and the cost ledger does not move. Also: the sync step is wired
after the map step, and its failure never breaks sync."""
from __future__ import annotations

import socket

import pytest
from typer.testing import CliRunner

from cairn import router as router_mod
from cairn.cli import app
from cairn.system.build import build_repo
from cairn.system.link import link
from cairn.system.views import view

from .scoring import load_truth, score
from .util import FIXTURES, aliases, copy_fixture, product


class Forbidden(AssertionError):
    pass


def _boom(*a, **k):
    raise Forbidden("a model or network call was made")


@pytest.fixture()
def sealed(monkeypatch):
    """Every model entry point and every socket connection raises."""
    for name in ("complete", "stream", "_anthropic", "_anthropic_stream", "_claude_code", "_claude_code_stream",
                 "_openai_request"):
        if hasattr(router_mod.Router, name):
            monkeypatch.setattr(router_mod.Router, name, _boom)
    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setattr(socket.socket, "connect_ex", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    return monkeypatch


def test_build_link_and_view_make_no_model_or_network_call(tmp_path, sealed):
    """FR-005, SC-003."""
    root = product(tmp_path, "shop", "shop-api", ["shop-web", "shop-api", "shop-worker", "shop-contracts"])
    build_repo(root, "shop-api")
    m = link(root)
    v = view(root, "container", model=m)
    view(root, "context", model=m)
    assert v["elements"] and v["relationships"]


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    for k in ("ANTHROPIC_API_KEY", "CAIRN_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("CAIRN_NO_CLI_MODELS", "1")
    monkeypatch.setenv("CAIRN_EMBEDDER", "hash")


def test_sync_runs_the_system_step_with_an_unchanged_ledger(tmp_path, monkeypatch, sealed):
    """SC-003: the ledger row count is the same before and after a sync that builds the model."""
    _env(monkeypatch, tmp_path)
    from cairn import sync
    from cairn.core import Cairn
    root = copy_fixture(FIXTURES / "shop" / "shop-api", tmp_path / "shop-api")
    c = Cairn.here(root)
    c.project.ensure_dir()
    before = c.brain.q("SELECT COUNT(*) n FROM ledger")[0]["n"]
    res = sync.run(c, deep=False)
    assert "error" not in res["system"], res["system"]
    assert res["system"]["containers"] == 1 and res["system"]["relationships"] == 2
    assert c.brain.q("SELECT COUNT(*) n FROM ledger")[0]["n"] == before
    rows = c.brain.q("SELECT id FROM sm_element WHERE type='container'")
    assert [r["id"] for r in rows] == ["shop-api:container:shop-api"]
    order = list(res)
    assert order.index("map") < order.index("system") < order.index("drift")
    c.close()


def test_a_failing_system_step_never_breaks_sync(tmp_path, monkeypatch):
    """FR-005."""
    _env(monkeypatch, tmp_path)
    from cairn import sync
    from cairn.core import Cairn
    from cairn.system import build as build_mod

    def broken(*a, **k):
        raise RuntimeError("extractor exploded")
    monkeypatch.setattr(build_mod, "build_repo", broken)
    root = copy_fixture(FIXTURES / "shop" / "shop-worker", tmp_path / "shop-worker")
    c = Cairn.here(root)
    c.project.ensure_dir()
    res = sync.run(c, deep=False)
    assert "extractor exploded" in res["system"]["error"]
    assert "error" not in res["map"] and "error" not in res["drift"]
    c.close()


def test_init_then_sync_then_view_end_to_end(tmp_path, monkeypatch):
    """US1-AS1, SC-001, SC-003, FR-005: US1 independent test: four repositories set up with `cairn init` and no model configured; the stored
    model drives `cairn system --view containers --json`, which matches the shop's ground truth."""
    _env(monkeypatch, tmp_path)
    runner = CliRunner()
    names = ["shop-web", "shop-api", "shop-worker", "shop-contracts"]
    for n in names:
        copy_fixture(FIXTURES / "shop" / n, tmp_path / n)
        monkeypatch.chdir(tmp_path / n)
        res = runner.invoke(app, ["init", "--no-deep", "--no-ui", "--no-hooks", "--no-capture", "--no-specs",
                                  "--agents", "claude"])
        assert res.exit_code == 0, res.output
        assert "System model" in res.output
    text = (FIXTURES / "shop" / "system.yaml").read_text(encoding="utf-8").replace("path: ", "path: ../")
    (tmp_path / "shop-api" / ".cairn" / "system.yaml").write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path / "shop-api")
    res = runner.invoke(app, ["system", "--view", "containers", "--json"])
    assert res.exit_code == 0, res.output
    import json
    v = json.loads(res.output)
    s = score(v, load_truth(FIXTURES / "shop" / "GROUND_TRUTH.yaml"), aliases(link(tmp_path / "shop-api")))
    assert s["element_recall"] == 1.0 and s["relationship_recall"] == 1.0
    assert s["element_precision"] == 1.0 and s["relationship_precision"] == 1.0

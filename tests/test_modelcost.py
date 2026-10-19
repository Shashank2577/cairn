"""Model spend as the UI shows it: tokens measured, dollars estimated from a list price, before/after the spec-002 defaults."""
from __future__ import annotations

import sqlite3

import pytest

from cairn import modelcost
from cairn.project import Project
from cairn.store import Brain


def _project(tmp_path, toml: str = "") -> Project:
    (tmp_path / ".cairn").mkdir(exist_ok=True)
    (tmp_path / ".cairn" / "config.toml").write_text(toml, encoding="utf-8")
    p = Project(tmp_path)
    p.reload()
    return p


def _row(**kw):
    base = dict(input=0, output=0, cache_read=0, cache_write=0, cache_write_1h=0, thinking=0)
    return {**base, **kw}


def test_list_price_covers_dated_ids_and_unknown_models_have_none():
    assert modelcost.price("claude-haiku-5-5") == (0.10, 0.50)
    assert modelcost.price("claude-haiku-4-5-20251001") == (1.0, 5.0)
    assert modelcost.price("some-local-model") is None


def test_a_configured_price_wins_over_the_list(tmp_path):
    p = _project(tmp_path, '[models.prices]\n"claude-haiku-5-5" = [0.2, 0.9]\n')
    assert modelcost.price("claude-haiku-5-5", p) == (0.2, 0.9)


def test_dollars_use_the_cache_multipliers_and_leave_thinking_inside_output():
    # 1M plain input + 1M output at $1/$5 = $6; cache read 1M at 0.1x = $0.10; 1M 5-minute write 1.25x; 1M 1-hour write 2x
    usd = modelcost.dollars(_row(input=1_000_000, output=1_000_000, thinking=500_000), (1.0, 5.0))
    assert usd == pytest.approx(6.0)
    assert modelcost.dollars(_row(cache_read=1_000_000), (1.0, 5.0)) == pytest.approx(0.10)
    assert modelcost.dollars(_row(cache_write=1_000_000), (1.0, 5.0), unknown_write="5m") == pytest.approx(1.25)
    assert modelcost.dollars(_row(cache_write=1_000_000, cache_write_1h=1_000_000), (1.0, 5.0)) == pytest.approx(2.0)
    assert modelcost.dollars(_row(cache_write=1_000_000), (1.0, 5.0), unknown_write="1h") == pytest.approx(2.0)


def _seed(tmp_path):
    """Old behaviour (cache writes) then the new defaults, plus the session capture they observed."""
    project = _project(tmp_path)
    brain = Brain(tmp_path / "b.db")
    old = {"input": 10, "output": 2000, "cache_write": 14000, "cache_read": 100}
    new = {"input": 12000, "output": 900, "thinking": 300}
    for _ in range(4):
        brain.log_call("recall_observe", "fast", "claude-haiku-4-5-20251001", old, True)
    for _ in range(2):
        brain.log_call("recall_observe", "fast", "claude-haiku-5-5", new, True)
    brain.log_call("recall_summarize", "fast", "claude-haiku-5-5", new, True)
    brain.db.execute("UPDATE ledger SET ts = ts - 1000 WHERE model LIKE '%4-5%'")
    brain.db.commit()
    db = sqlite3.connect(tmp_path / ".cairn" / "sessions.db")
    db.executescript("""CREATE TABLE tool_uses(id INTEGER PRIMARY KEY, created_at_epoch INTEGER NOT NULL);
      CREATE TABLE observations(id INTEGER PRIMARY KEY, created_at_epoch INTEGER NOT NULL);""")
    import time
    now = int(time.time())
    db.executemany("INSERT INTO tool_uses(created_at_epoch) VALUES(?)", [(now - 1000,)] * 20 + [(now,)] * 10)
    db.executemany("INSERT INTO observations(created_at_epoch) VALUES(?)", [(now - 1000,)] * 8 + [(now,)] * 5)
    db.commit(); db.close()
    return project, brain


def test_summary_splits_old_and_new_observer_behaviour_with_per_event_figures(tmp_path):
    project, brain = _seed(tmp_path)
    s = modelcost.summary(brain, project, provider="claude-code")
    before, after = s["observer"]["before"], s["observer"]["after"]
    assert (before["calls"], after["calls"]) == (4, 2)
    assert before["cache_write"] == 56000 and after["cache_write"] == 0
    assert after["thinking"] == 600
    assert before["events"] == 20 and after["events"] == 10 and before["observations"] == 8 and after["observations"] == 5
    assert before["tokens_per_event"] == pytest.approx((4 * (10 + 2000 + 14000 + 100)) / 20)
    assert after["usd_per_event"] < before["usd_per_event"]
    assert s["labels"] == {"tokens": "measured", "dollars": "estimated"}


def test_the_study_baseline_is_shown_when_this_store_has_no_old_rows(tmp_path):
    project = _project(tmp_path)
    brain = Brain(tmp_path / "b.db")
    brain.log_call("recall_observe", "fast", "claude-haiku-5-5", {"input": 10, "output": 5}, True)
    s = modelcost.summary(brain, project)
    assert s["observer"]["before"] is None
    study = s["study"]
    assert study["before"]["tokens_per_event"] > study["after_haiku_5_5"]["usd_per_event"]
    assert study["source"].startswith("specs/002-observation-token-cost/findings.md")


def test_tiers_carry_the_active_model_price_and_totals(tmp_path):
    project, brain = _seed(tmp_path)
    s = modelcost.summary(brain, project)
    haiku = next(t for t in s["tiers"] if t["model"] == "claude-haiku-5-5")
    assert haiku["calls"] == 3 and haiku["price"] == [0.10, 0.50] and haiku["usd"] > 0
    unknown = Brain(tmp_path / "c.db")
    unknown.log_call("x", "fast", "local-model", {"input": 5, "output": 5}, True)
    row = modelcost.summary(unknown, project)["tiers"][0]
    assert row["price"] is None and row["usd"] is None  # no price, no invented dollars


def test_the_report_the_page_reads_has_cost_pins_and_each_tier_default(cairn):
    cairn.project.set_cfg("models.fast", "claude-haiku-4-5-20251001")
    cairn.brain.log_call("recall_observe", "fast", "claude-haiku-4-5-20251001", {"input": 5, "output": 5}, True)
    r = cairn.model_report()
    assert r["pins"][0]["tier"] == "fast" and r["pins"][0]["default"] == "claude-haiku-5-5"
    fast = next(t for t in r["table"] if t["tier"] == "fast")
    assert fast["model"] == "claude-haiku-4-5-20251001" and fast["default"] == "claude-haiku-5-5" and fast["price"] == [1.0, 5.0]
    assert r["cost"]["labels"]["dollars"] == "estimated" and r["totals"] and r["ledger"][0]["thinking_tokens"] == 0
    assert "cache_write_1h" in r["ledger"][0]


def test_cli_ledger_labels_measured_and_estimated(cairn):
    from typer.testing import CliRunner

    from cairn.cli import app
    cairn.brain.log_call("recall_observe", "fast", "claude-haiku-5-5", {"input": 1000, "output": 100, "thinking": 40}, True)
    out = CliRunner().invoke(app, ["models", "--ledger"]).output
    assert "measured" in out and "estimated" in out and "thinking" in out and "$" in out

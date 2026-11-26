"""Ledger rows keep the provider's thinking and cache-TTL counts (specs/002-observation-token-cost T016); older stores gain
the columns without losing rows."""
from __future__ import annotations

import sqlite3

from cairn.router import Router
from cairn.store import Brain

CLI = {"is_error": False, "result": "ok", "usage": {
    "input_tokens": 12, "output_tokens": 900, "cache_read_input_tokens": 5, "cache_creation_input_tokens": 700,
    "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 600},
    "output_tokens_details": {"thinking_tokens": 400}}}


def test_cli_usage_carries_thinking_and_the_one_hour_write_split():
    u = Router._claude_code_result(CLI)
    assert u["thinking"] == 400 and u["cache_write_1h"] == 600 and u["cache_write"] == 700


def test_cli_usage_without_the_detail_is_zero_not_an_error():
    u = Router._claude_code_result({"usage": {"input_tokens": 1, "output_tokens": 2}})
    assert u["thinking"] == 0 and u["cache_write_1h"] == 0


def test_the_ledger_stores_and_sums_the_detail(tmp_path):
    b = Brain(tmp_path / "b.db")
    usage = Router._claude_code_result(CLI)
    b.log_call("recall_observe", "fast", "claude-haiku-5-5", usage, True)
    b.log_call("recall_observe", "fast", "claude-haiku-5-5", usage, True)
    (row,) = b.ledger()
    assert (row["calls"], row["thinking"], row["cache_write_1h"], row["cache_write"]) == (2, 800, 1200, 1400)


def test_a_store_from_before_the_columns_is_upgraded_in_place(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, task TEXT NOT NULL,
      tier TEXT NOT NULL, model TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
      cache_read INTEGER NOT NULL DEFAULT 0, cache_write INTEGER NOT NULL DEFAULT 0, ok INTEGER NOT NULL);
      INSERT INTO ledger(ts,task,tier,model,input_tokens,output_tokens,cache_write,ok) VALUES(1,'recall_observe','fast','m',10,20,30,1);""")
    db.commit(); db.close()
    b = Brain(path)
    (row,) = b.ledger()
    assert (row["calls"], row["cache_write"], row["thinking"], row["cache_write_1h"]) == (1, 30, 0, 0)
    Brain(path)  # reopening is a no-op

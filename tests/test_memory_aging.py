"""Memory aging: last-used tracking, down-ranking of long-unused memories, pinned kinds, reversibility, migration."""
from __future__ import annotations

import sqlite3
import time

import pytest

from cairn.engines.memory import STALE_FACTOR
from cairn.store import AGING_SINCE, Brain

DAY = 86400


@pytest.fixture()
def mem(cairn):
    return cairn.memory


def add(mem, text, kind="fact", age_days=0.0):
    mid = mem.remember(text, kind)["id"]
    if age_days:
        with mem.brain.tx() as db:
            db.execute("UPDATE memories SET created_at=? WHERE id=?", (time.time() - age_days * DAY, mid))
    return mid


def age_started_long_ago(mem, days=400):
    mem.brain.set_kv(AGING_SINCE, str(time.time() - days * DAY))


def test_a_new_store_starts_aging_now_so_old_imported_memories_are_not_stale_on_day_one(cairn):
    since = float(cairn.brain.get_kv(AGING_SINCE))
    assert abs(since - time.time()) < 3600
    old = add(cairn.memory, "Legacy gotcha imported from an old ADR", "gotcha", age_days=900)  # dated by its source
    [m] = [x for x in cairn.memory.annotate(cairn.brain.memories()) if x["id"] == old]
    assert m["stale"] is False


def test_unused_memories_go_stale_after_the_window_and_pinned_kinds_never_do(mem):
    ids = {k: add(mem, f"A {k} about zebra cache invalidation rules", k, age_days=200) for k in
           ("convention", "decision", "gotcha", "preference", "fact")}
    age_started_long_ago(mem)
    stale = {m["kind"]: m["stale"] for m in mem.annotate(mem.brain.memories())}
    assert stale == {"convention": False, "decision": False, "gotcha": True, "preference": True, "fact": True}
    assert set(ids) == set(stale)


def test_the_window_and_pinned_kinds_are_configurable(mem):
    add(mem, "A gotcha about zebra locks", "gotcha", age_days=200)
    add(mem, "A fact about zebra locks", "fact", age_days=200)
    age_started_long_ago(mem)
    mem.project.set_cfg("memory.pinned_kinds", ["gotcha"])
    mem.project.set_cfg("memory.stale_days", 500)
    assert not any(m["stale"] for m in mem.annotate(mem.brain.memories()))
    mem.project.set_cfg("memory.stale_days", 30)
    assert {m["kind"]: m["stale"] for m in mem.annotate(mem.brain.memories())} == {"gotcha": False, "fact": True}


def test_recall_records_use_and_a_used_memory_is_fresh_again(mem):
    mid = add(mem, "Refund webhook retries need an idempotency key", "gotcha", age_days=200)
    age_started_long_ago(mem)
    assert mem.annotate([mem.brain.memory(mid)])[0]["stale"]
    assert mem.recall("refund webhook idempotency", limit=3, touch=False)
    assert mem.brain.memory_use() == {}                       # browsing does not count
    assert mem.recall("refund webhook idempotency", limit=3)[0]["id"] == mid
    use = mem.brain.memory_use()[mid]
    assert use["uses"] == 1 and abs(use["last_used"] - time.time()) < 60
    assert not mem.annotate([mem.brain.memory(mid)])[0]["stale"]
    mem.recall("refund webhook idempotency", limit=3)
    assert mem.brain.memory_use()[mid]["uses"] == 2


def test_stale_memories_rank_lower_but_are_still_returned(mem):
    old = add(mem, "Checkout totals round half up in the ledger", "fact", age_days=300)
    new = add(mem, "Checkout totals are computed in cents in the ledger", "fact")
    age_started_long_ago(mem)
    mem.brain.touch_memories([new], now=time.time())
    order = [m["id"] for m in mem.recall("checkout totals ledger", limit=5, touch=False)]
    assert order.index(new) < order.index(old) and old in order              # demoted, never dropped
    mem.project.set_cfg("memory.aging", False)
    assert not any(m["stale"] for m in mem.annotate(mem.brain.memories()))     # off switch
    assert STALE_FACTOR < 1


def test_nothing_is_deleted_by_aging(mem):
    ids = [add(mem, f"Zebra fact number {i} about the payment gateway", "fact", age_days=500) for i in range(4)]
    age_started_long_ago(mem)
    mem.recall("zebra payment gateway", limit=2)
    assert {m["id"] for m in mem.brain.memories()} >= set(ids)
    assert mem.brain.one("SELECT COUNT(*) n FROM memories WHERE forgotten=1")["n"] == 0


def test_clearing_usage_history_restarts_the_clock_and_changes_nothing_else(mem):
    mid = add(mem, "Quokka gateway timeouts are 30 seconds", "gotcha", age_days=200)
    age_started_long_ago(mem)
    mem.brain.touch_memories([mid])
    before = mem.brain.memory(mid)
    assert mem.brain.clear_memory_use() == 1
    assert mem.brain.memory_use() == {} and mem.brain.memory(mid) == before
    assert not mem.annotate([before])[0]["stale"]                              # fresh from the restart


def test_a_lock_held_by_a_sync_drops_the_touch_instead_of_waiting(mem, tmp_path):
    mid = add(mem, "Gateway timeouts are 30 seconds", "gotcha")
    other = sqlite3.connect(str(mem.brain.path), timeout=1)
    other.execute("BEGIN IMMEDIATE")                                          # a sync is writing
    started = time.time()
    assert mem.brain.touch_memories([mid]) == 0
    assert time.time() - started < 5
    other.rollback()
    other.close()
    assert mem.brain.touch_memories([mid]) == 1                               # and the timeout is back to normal


def test_brief_and_context_prefer_fresh_memories(cairn):
    mem = cairn.memory
    stale = add(mem, "Stale gotcha: the old queue was named q1", "gotcha", age_days=300)
    fresh = add(mem, "Fresh gotcha: the queue is named q2", "gotcha")
    age_started_long_ago(mem)
    mem.brain.touch_memories([fresh])
    lines = [ln for ln in cairn.brief(max_tokens=2000).splitlines() if "queue" in ln]
    assert [ln for ln in lines if "q2" in ln] and [ln for ln in lines if "q1" in ln]      # the stale one is still there
    assert lines.index(next(ln for ln in lines if "q2" in ln)) < lines.index(next(ln for ln in lines if "q1" in ln))
    ordered = [m["id"] for m in mem.fresh_first(mem.brain.memories())]
    assert ordered.index(fresh) < ordered.index(stale)
    packed = {i.cite: i.score for i in cairn._memories(["queue"], [])}
    assert packed[f"memory:{fresh}"] > packed[f"memory:{stale}"] or f"memory:{stale}" not in packed


def test_the_memories_api_reports_stale_and_last_used(cairn):
    mem = cairn.memory
    mid = add(mem, "Unused fact about the nightly export", "fact", age_days=200)
    age_started_long_ago(mem)
    rows = {m["id"]: m for m in mem.annotate(mem.brain.memories())}
    assert rows[mid]["stale"] is True and rows[mid]["last_used"] is None and rows[mid]["uses"] == 0


# ---- migration safety ----------------------------------------------------------------------------------------------
def test_an_existing_brain_db_migrates_in_place_without_losing_memories(cairn):
    cairn.memory.remember("Keep me through the migration", "decision")
    path = cairn.brain.path
    cairn.brain.close()
    db = sqlite3.connect(str(path))
    db.executescript("DROP TABLE memory_use; DELETE FROM kv WHERE key IN ('schema_version', 'memory.aging_since');")
    db.execute("INSERT INTO kv(key,value) VALUES('schema_version','2')")
    db.commit()
    db.close()
    brain = Brain(path)                                                       # an older store, as a prior release left it
    try:
        assert brain.get_kv("schema_version") == "3" and brain.memory_use() == {}
        assert float(brain.get_kv(AGING_SINCE)) > time.time() - 60            # aging starts at the upgrade
        assert "Keep me through the migration" in [m["text"] for m in brain.memories()]
    finally:
        brain.close()
    again = Brain(path)                                                       # idempotent
    again.touch_memories([again.memories()[0]["id"]])
    assert len(again.memory_use()) == 1
    again.close()


def test_an_older_build_ignores_the_usage_table(cairn):
    mem = cairn.memory
    mid = add(mem, "Shared between old and new builds", "fact")
    mem.brain.touch_memories([mid])
    row = mem.brain.one("SELECT * FROM memories WHERE id=?", (mid,))
    assert set(row.keys()) == {"id", "text", "kind", "scope", "source", "provenance", "confidence", "created_at",
                               "superseded_by", "forgotten", "engine_ref"}   # the memories table has the same shape


def test_what_the_prompt_hook_and_the_mcp_tools_return_counts_as_used(cairn, monkeypatch):
    import json

    from cairn import hooks, mcp_server
    mem = cairn.memory
    mid = add(mem, "Refund retries use exponential backoff capped at five attempts", "gotcha")
    out = hooks.ambient(cairn, json.dumps({"prompt": "Update the refund retries in shop/payments.py to use backoff"}))
    assert out and mid in mem.brain.memory_use()
    mem.brain.clear_memory_use()
    monkeypatch.setattr(mcp_server, "_project", lambda: cairn)
    assert "backoff" in mcp_server.cairn_recall("refund retries backoff")
    assert mid in mem.brain.memory_use()

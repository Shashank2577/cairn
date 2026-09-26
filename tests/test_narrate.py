"""Streamed narration: the model layer yields text as it arrives, and the page receives it line by line."""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import time

import pytest

from cairn.router import Router

STREAM = [
    {"type": "system", "subtype": "init"},
    {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                       "delta": {"type": "thinking_delta", "thinking": "hmm"}}},
    {"type": "stream_event", "event": {"type": "content_block_delta", "index": 1,
                                       "delta": {"type": "text_delta", "text": "Refunds "}}},
    {"type": "stream_event", "event": {"type": "content_block_delta", "index": 1,
                                       "delta": {"type": "text_delta", "text": "call payments [file:shop/api.py]"}}},
    {"type": "result", "is_error": False, "result": "Refunds call payments [file:shop/api.py]",
     "usage": {"input_tokens": 120, "output_tokens": 9}},
]


def _fake_cli(tmp_path, monkeypatch, body: str):
    """A `claude` executable on PATH that runs ``body`` (Python) instead of the real CLI."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "claude"
    exe.write_text(f"#!{sys.executable}\nimport json, sys, time\nsys.stdin.read()\n{body}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("CAIRN_NO_CLI_MODELS", raising=False)
    for key in ("ANTHROPIC_API_KEY", "CAIRN_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def _router(cairn) -> Router:
    router = Router(cairn.project, cairn.brain)
    assert router.provider == "claude-code"
    return router


def test_claude_code_streams_text_and_records_usage(cairn, tmp_path, monkeypatch):
    lines = "\n".join(json.dumps(e) for e in STREAM)
    _fake_cli(tmp_path, monkeypatch, f"print({lines!r}, flush=True)")
    router = _router(cairn)
    assert list(router.stream("ask", "Why refunds?")) == ["Refunds ", "call payments [file:shop/api.py]"]
    row = cairn.brain.db.execute("SELECT * FROM ledger ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row["task"] == "ask" and row["ok"] == 1 and row["input_tokens"] == 120


def test_a_failed_turn_raises_with_the_reason(cairn, tmp_path, monkeypatch):
    err = json.dumps({"type": "result", "is_error": True, "result": "You've hit your session limit"})
    _fake_cli(tmp_path, monkeypatch, f"print({err!r}, flush=True)")
    with pytest.raises(RuntimeError, match="session limit"):
        list(_router(cairn).stream("ask", "q"))
    _fake_cli_dir = tmp_path / "bin" / "claude"
    _fake_cli_dir.write_text(f"#!{sys.executable}\nimport sys\nsys.stderr.write('not signed in')\nsys.exit(1)\n")
    with pytest.raises(RuntimeError, match="not signed in"):
        list(_router(cairn).stream("ask", "q"))


def test_closing_the_stream_stops_the_model_call(cairn, tmp_path, monkeypatch):
    first = json.dumps(STREAM[2])
    marker = tmp_path / "still-running"
    _fake_cli(tmp_path, monkeypatch, f"print({first!r}, flush=True)\ntime.sleep(30)\nopen({str(marker)!r}, 'w')")
    chunks = _router(cairn).stream("ask", "q")
    started = time.time()
    assert next(chunks) == "Refunds "
    chunks.close()  # the page went away
    assert time.time() - started < 10 and not marker.exists()


def test_the_page_receives_evidence_then_text_then_done(cairn, tmp_path, monkeypatch):
    from test_surfaces import _client
    lines = "\n".join(json.dumps(e) for e in STREAM)
    _fake_cli(tmp_path, monkeypatch, f"print({lines!r}, flush=True)")
    c, p = _client(cairn, tmp_path)
    with c.stream("POST", f"{p}/narrate", json={"kind": "ask", "text": "How are refunds processed?"}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert [e["type"] for e in events] == ["start", "text", "text", "done"]
    assert events[0]["pack"] and events[0]["model"]
    assert "".join(e["text"] for e in events if e["type"] == "text") == "Refunds call payments [file:shop/api.py]"
    with c.stream("POST", f"{p}/narrate", json={"kind": "impact", "text": "shop/api.py"}) as r:
        kinds = [json.loads(line)["type"] for line in r.iter_lines() if line]
    assert kinds[0] == "start" and kinds[-1] == "done"


def test_a_model_error_reaches_the_page_as_an_event(cairn, tmp_path, monkeypatch):
    from test_surfaces import _client
    err = json.dumps({"type": "result", "is_error": True, "result": "You've hit your session limit"})
    _fake_cli(tmp_path, monkeypatch, f"print({err!r}, flush=True)")
    c, p = _client(cairn, tmp_path)
    with c.stream("POST", f"{p}/narrate", json={"kind": "why", "text": "shop/api.py"}) as r:
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert events[-1]["type"] == "error" and "session limit" in events[-1]["message"]


def test_narration_needs_a_model_and_valid_input(cairn, tmp_path):
    from test_surfaces import _client
    c, p = _client(cairn, tmp_path)  # tests run without any model
    assert c.post(f"{p}/narrate", json={"kind": "ask", "text": "q"}).status_code == 409
    assert c.post(f"{p}/narrate", json={"kind": "delete", "text": "q"}).status_code == 422
    assert c.post(f"{p}/narrate", json={"kind": "ask", "text": ""}).status_code == 422


def test_stop_from_the_page_ends_the_call_and_is_still_recorded(cairn, tmp_path, monkeypatch):
    import threading
    first = json.dumps(STREAM[2])
    marker = tmp_path / "still-running"
    _fake_cli(tmp_path, monkeypatch, f"print({first!r}, flush=True)\ntime.sleep(30)\nopen({str(marker)!r}, 'w')")
    cancel = threading.Event()
    chunks = _router(cairn).stream("why", "q" * 4000, cancel=cancel)
    assert next(chunks) == "Refunds "
    threading.Timer(0.3, cancel.set).start()  # Stop pressed while the model is still writing
    started = time.time()
    assert list(chunks) == []                   # the stream ends by itself, it isn't left hanging
    assert time.time() - started < 5 and not marker.exists()
    row = cairn.brain.db.execute("SELECT * FROM ledger ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row["task"] == "why" and row["ok"] == 1 and row["input_tokens"] >= 1000 and row["output_tokens"] >= 1


def test_the_first_event_names_every_citation(cairn, tmp_path, monkeypatch):
    cairn.remember("Payments retry with an idempotency key", kind="decision")
    lines = "\n".join(json.dumps(e) for e in STREAM)
    _fake_cli(tmp_path, monkeypatch, f"print({lines!r}, flush=True)")
    start = next(iter(cairn.narrate_stream(cairn.impact("shop/payments.py", 2, 1500), "impact")))
    labels = start["labels"]
    assert labels and all(v for v in labels.values())
    symbols = {k: v for k, v in labels.items() if k.startswith("symbol:")}
    assert all(v == cairn.map.label(k[7:]) for k, v in symbols.items())
    memories = {k: v for k, v in labels.items() if k.startswith("memory:")}
    assert memories, "expected the remembered decision to be cited"
    assert not any(re.match(r"^\w+\]", v) for v in labels.values())


def test_a_busy_ledger_never_loses_the_answer(cairn, tmp_path, monkeypatch):
    import sqlite3
    ok = json.dumps({"type": "result", "is_error": False, "result": "fine", "usage": {"input_tokens": 3}})
    _fake_cli(tmp_path, monkeypatch, f"print({ok!r})")
    router = _router(cairn)

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(cairn.brain, "log_call", locked)
    assert router.complete("ask", "q") == "fine"

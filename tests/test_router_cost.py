"""The Claude Code CLI path: no cache writes, no thinking for bulk tasks, and the cheap default fast tier
(specs/002-observation-token-cost)."""
from __future__ import annotations

import json
import subprocess

from cairn.router import NO_THINKING_TASKS, Router


def _router(cairn, monkeypatch, **env):
    for k in ("ANTHROPIC_API_KEY", "CAIRN_API_KEY", "OPENAI_API_KEY", "DISABLE_PROMPT_CACHING", "MAX_THINKING_TOKENS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CAIRN_NO_CLI_MODELS", raising=False)
    monkeypatch.setattr("cairn.router.shutil.which", lambda name: "/usr/bin/claude")
    r = Router(cairn.project)
    r.provider = "claude-code"
    return r


def _capture(monkeypatch):
    seen = {}

    def run(args, **kw):
        seen["env"] = kw["env"]
        out = json.dumps({"result": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}})
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")
    monkeypatch.setattr("cairn.router.subprocess.run", run)
    return seen


def test_cli_calls_do_not_write_cache_entries_by_default(cairn, monkeypatch):
    seen = _capture(monkeypatch)
    _router(cairn, monkeypatch).complete("ask", "q")
    assert seen["env"]["DISABLE_PROMPT_CACHING"] == "1"
    assert seen["env"]["CAIRN_INTERNAL"] == "1"


def test_a_user_who_set_caching_keeps_it(cairn, monkeypatch):
    seen = _capture(monkeypatch)
    _router(cairn, monkeypatch, DISABLE_PROMPT_CACHING="0").complete("ask", "q")
    assert seen["env"]["DISABLE_PROMPT_CACHING"] == "0"


def test_the_cli_cache_can_be_switched_back_on_in_config(cairn, monkeypatch):
    seen = _capture(monkeypatch)
    r = _router(cairn, monkeypatch)
    monkeypatch.setattr(r.project, "cfg", lambda key, default=None: True if key == "models.cli_cache" else default)
    r.complete("ask", "q")
    assert "DISABLE_PROMPT_CACHING" not in seen["env"]


def test_thinking_is_off_for_bulk_tasks_only(cairn, monkeypatch):
    seen = _capture(monkeypatch)
    r = _router(cairn, monkeypatch)
    r.complete("recall_observe", "q")
    assert seen["env"]["MAX_THINKING_TOKENS"] == "0"
    r.complete("why", "q")  # judgement keeps the CLI's default
    assert "MAX_THINKING_TOKENS" not in seen["env"]
    assert {"recall_observe", "recall_summarize", "recall_compress"} <= NO_THINKING_TASKS


def test_a_users_thinking_budget_wins(cairn, monkeypatch):
    seen = _capture(monkeypatch)
    _router(cairn, monkeypatch, MAX_THINKING_TOKENS="2048").complete("recall_observe", "q")
    assert seen["env"]["MAX_THINKING_TOKENS"] == "2048"


def test_the_fast_tier_defaults_to_haiku_5_5(cairn, monkeypatch):
    assert _router(cairn, monkeypatch).model("fast") == "claude-haiku-5-5"

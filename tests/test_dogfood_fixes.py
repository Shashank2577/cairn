"""Regression tests for the dogfood findings fixed on 002-dogfood-fixes.

Each group maps to a failure observed in the 2026-09-27 real-world dogfood run
(cairn-demo/FINDINGS.md): statusline crashes, bare-path timeline lookups, degenerate
targets, question words becoming context targets, sync-lock contention, doctor
contradicting the config, and the init flag that lets users skip the deep tier.
"""
import json
import sys
import threading
import time

import pytest
from typer.testing import CliRunner

from cairn import agents as agents_mod
from cairn import hooks, sync
from cairn.cli import _statusline_cwd, _timeline_ref, app
from cairn.core import _QUESTION_STOP

runner = CliRunner()


# ---- statusline: Claude Code pipes JSON here and it is not always an object ------------------------

@pytest.mark.parametrize("payload", ["null", "[1, 2]", '"current_dir"', "not json at all", ""])
def test_statusline_cwd_tolerates_non_object_json(payload):
    assert _statusline_cwd(payload) is None


def test_statusline_cwd_reads_a_valid_payload():
    assert _statusline_cwd(json.dumps({"workspace": {"current_dir": "/tmp/x"}})) == "/tmp/x"
    assert _statusline_cwd(json.dumps({"workspace": "not-a-dict"})) is None


def test_hooks_statusline_never_crashes_on_weird_input(cairn):
    for payload in ("null", "[1]", '{"workspace": 3}', "garbage{"):
        assert "cairn" in hooks.statusline(cairn, payload)


# ---- timeline --target: bare paths and symbols must resolve like impact ----------------------------

def test_timeline_ref_resolves_a_bare_path(cairn):
    assert _timeline_ref(cairn, "shop/payments.py") == "file:shop/payments.py"


def test_timeline_ref_passes_prefixed_ids_through(cairn):
    assert _timeline_ref(cairn, "file:shop/payments.py") == "file:shop/payments.py"
    assert _timeline_ref(cairn, "spec:001-refunds") == "spec:001-refunds"


def test_timeline_ref_leaves_unknown_targets_alone(cairn):
    assert _timeline_ref(cairn, "no/such/file.py") == "no/such/file.py"
    assert _timeline_ref(cairn, ".") == "."


def test_timeline_command_shows_history_for_a_bare_path(cairn):
    res = runner.invoke(app, ["timeline", "--target", "shop/payments.py", "--days", "365"])
    assert res.exit_code == 0
    assert "payments" in res.output


# ---- degenerate targets: refuse them instead of garbage-matching -----------------------------------

@pytest.mark.parametrize("target", [".", "", "..", "/", "-"])
def test_resolve_rejects_degenerate_targets(cairn, target):
    with pytest.raises(ValueError):
        cairn.resolve(target)


def test_impact_dot_exits_with_a_friendly_error(cairn):
    res = runner.invoke(app, ["impact", "."])
    assert res.exit_code == 2
    assert "cairn search" in res.output


def test_why_dot_exits_with_a_friendly_error(cairn):
    res = runner.invoke(app, ["why", "."])
    assert res.exit_code == 2


# ---- sync lock: --wait waits for the hook's background sync instead of skipping --------------------

@pytest.mark.skipif(sys.platform == "win32", reason="no flock on Windows; the lock is best-effort there")
def test_locked_skips_immediately_when_busy(repo):
    lock = repo / ".cairn" / "sync.lock"
    with sync.locked(lock) as first:
        assert first
        started = time.monotonic()
        with sync.locked(lock) as busy:
            assert busy is False
            assert time.monotonic() - started < 0.2  # no wait configured: skip at once


@pytest.mark.skipif(sys.platform == "win32", reason="no flock on Windows; the lock is best-effort there")
def test_locked_waits_for_the_holder_to_release(repo):
    lock = repo / ".cairn" / "sync.lock"

    def hold():
        with sync.locked(lock):
            time.sleep(1.0)

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    time.sleep(0.3)  # let the thread take the lock
    started = time.monotonic()
    with sync.locked(lock, wait=5) as ok:
        assert ok is True
        assert time.monotonic() - started >= 0.5  # it genuinely waited for the holder
    t.join(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="no flock on Windows; the lock is best-effort there")
def test_locked_times_out_when_the_holder_keeps_holding(repo):
    lock = repo / ".cairn" / "sync.lock"
    with sync.locked(lock):
        started = time.monotonic()
        with sync.locked(lock, wait=0.5) as ok:
            assert ok is False
            assert time.monotonic() - started >= 0.4


def test_locked_contract_holds_on_this_os(repo):
    """First lock True, contended False — unskipped on every OS: flock on POSIX, the new msvcrt path on
    Windows (the fixture repo above keeps the wait-timing coverage, which is flock-shaped)."""
    lock = repo / ".cairn" / "sync.lock"
    with sync.locked(lock) as first:
        assert first is True
        with sync.locked(lock) as busy:
            assert busy is False


# ---- deterministic ask: question words never become context targets --------------------------------

def test_infer_targets_ignores_question_words(cairn):
    targets = cairn.infer_targets("what owns the checkout flow and where is it tested")
    assert not ({t.lower() for t in targets} & _QUESTION_STOP)
    assert not ({t.lower() for t in targets} & {"owns", "tested"})


# ---- doctor: must reflect the capture config, not contradict it ------------------------------------

def test_doctor_reports_capture_off_when_config_disables_it(cairn):
    (cairn.project.dir / "config.toml").write_text("[sessions]\ncapture = false\n", encoding="utf-8")
    res = runner.invoke(app, ["doctor"])
    assert res.exit_code == 0
    assert "off (config)" in res.output


def test_doctor_does_not_claim_off_when_capture_is_enabled(cairn):
    res = runner.invoke(app, ["doctor"])
    assert res.exit_code == 0
    # scoped to the capture row: ambient context has its own "off (config)" state (it defaults to off)
    capture = next(ln for ln in res.output.splitlines() if "Session capture" in ln)
    assert "off (config)" not in capture


# ---- init --no-deep: the first sync can skip the deep tier -----------------------------------------

def test_init_no_deep_forces_deep_off(monkeypatch, repo):
    import cairn.sync as sync_mod
    captured = {}

    def fake_run(c, *, deep=None, progress=None, **kw):
        captured["deep"] = deep
        return {}

    monkeypatch.setattr(sync_mod, "run", fake_run)
    res = runner.invoke(app, ["init", "--no-deep", "--no-ui", "--no-hooks"])
    assert res.exit_code == 0, res.output
    assert captured["deep"] is False


# ---- the search fallback in infer_targets must not surface content-free heading anchors ------------

def test_infer_targets_skips_content_free_heading_anchors(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text("# demo\n\n## what\n\nA section named after a question word.\n", encoding="utf-8")
    (tmp_path / "payments_core.py").write_text("def dispatch():\n    ...\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    from cairn import sync as sync_mod
    from cairn.core import Cairn
    c = Cairn.here(tmp_path)
    sync_mod.run(c, deep=False)
    targets = c.infer_targets("what owns dispatch")
    assert "what" not in {t.lower() for t in targets}


# ---- sync shows long steps while they work (the deep tier used to be invisible for minutes) --------

def test_sync_prints_start_states(cairn):
    res = runner.invoke(app, ["sync", "--no-deep"])
    assert res.exit_code == 0
    assert "◌" in res.output  # a start line per step, not only the done summary


# ---- active spec: process-dialect ids must print whole (strip only a real "spec:" prefix) -----------

def test_status_active_spec_prints_process_dialect_ids_whole(repo):
    import shutil

    from cairn.core import Cairn
    shutil.rmtree(repo / "specs")  # the fixture seeds a spec-kit feature; the dialect needs a clear field
    (repo / "requirements").mkdir()
    (repo / "requirements" / "index.md").write_text(
        "| ID | Requirement |\n|---|---|\n| REQ-001 | Everything is traceable. |\n", encoding="utf-8")
    c = Cairn.here(repo)
    sync.run(c)
    res = runner.invoke(app, ["status"])
    assert res.exit_code == 0, res.output
    # the whole dialect id on the active-spec line, never a fixed-slice mangling ("ess:req-001")
    assert "Active spec process:req-001" in res.output


# ---- global suggest hook: the harness offers setup in repos that have no cairn ----------------------

def test_global_session_hint_offers_init_in_uninited_git_repo(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    payload = json.dumps({"cwd": str(tmp_path)})
    out = hooks.global_session_hint(payload)
    assert out and "cairn init --no-deep" in out["hookSpecificOutput"]["additionalContext"]


def test_global_session_hint_silent_when_inited_non_git_or_home(tmp_path, monkeypatch):
    import subprocess
    payload = json.dumps({"cwd": str(tmp_path)})
    assert hooks.global_session_hint(payload) is None            # not a git repo
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".cairn").mkdir()                                # any cairn state counts as initialized
    assert hooks.global_session_hint(payload) is None
    monkeypatch.setenv("HOME", str(tmp_path))                    # home itself is never suggested
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() on Windows
    assert hooks.global_session_hint(json.dumps({"cwd": str(tmp_path)})) is None


def test_global_install_and_remove_wire_user_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() on Windows
    other = {"hooks": [{"type": "command", "command": "echo user-owns-this"}]}
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": [other]}}), encoding="utf-8")
    assert agents_mod.global_install() is True
    wired = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    groups = wired["hooks"]["SessionStart"]
    ours = [h for g in groups for h in g["hooks"] if "global-session" in h["command"]]
    theirs = [h for g in groups for h in g["hooks"] if "user-owns-this" in h["command"]]
    assert ours and theirs                                       # ours added, the user's kept
    assert agents_mod.global_install() is True                   # idempotent (no duplicate)
    groups2 = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"]
    assert sum("global-session" in h["command"] for g in groups2 for h in g["hooks"]) == 1
    assert agents_mod.global_remove() is True
    groups3 = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"]
    assert not any("global-session" in h["command"] for g in groups3 for h in g["hooks"])
    assert any("user-owns-this" in h["command"] for g in groups3 for h in g["hooks"])


def test_global_session_hook_cli_end_to_end(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkey_cwd = str(tmp_path)
    payload = json.dumps({"cwd": monkey_cwd})
    runner = CliRunner()
    res = runner.invoke(app, ["hook", "global-session"], input=payload)
    assert res.exit_code == 0
    assert "cairn init --no-deep" in res.output


def test_global_install_preserves_the_hand_edited_dict_form(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.home() on Windows
    (tmp_path / ".claude").mkdir()
    user_group = {"hooks": [{"type": "command", "command": "echo user-owns-this"}]}
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"SessionStart": user_group}}), encoding="utf-8")
    assert agents_mod.global_install() is True
    groups = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"]
    cmds = [h["command"] for g in groups for h in g["hooks"]]
    assert any("global-session" in c for c in cmds) and any("user-owns-this" in c for c in cmds)


def test_recap_shows_savings_and_memories(cairn):
    from cairn import hooks
    cairn.brain.log_query("cli", "impact", "shop/payments.py", 500, 5000, 2)
    out = hooks.recap(cairn)
    assert "memories" in out and "saved" in out and "1 queries" in out




def test_recap_shows_receipts_not_just_counts(cairn):
    from cairn import hooks
    cairn.brain.log_query("cli", "impact", "shop/payments.py", 500, 5000, 2)
    mem = cairn.brain.q("SELECT text FROM memories LIMIT 1")[0]["text"]
    out = hooks.recap(cairn)
    assert "memories it learned" in out and mem[:40] in out      # the actual lesson text
    assert "impact shop/payments.py" in out and "saved 4,500" in out  # the actual query receipt


# ---- the ask fix: audit trail, past questions, cited docs, compounding answers -----------------------

def test_ask_logs_the_question_for_every_surface(cairn):
    cairn.ask("how does upload work", llm=False)
    rows = cairn.brain.q("SELECT target FROM queries WHERE kind='ask'")
    assert any(r["target"] == "how does upload work" for r in rows)
    from cairn.mcp_server import cairn_context  # the chatbot surface logs too
    cairn_context("how does transcription work")
    rows = cairn.brain.q("SELECT target FROM queries WHERE kind='context'")
    assert any("transcription" in r["target"] for r in rows)


def test_context_shows_past_related_questions(cairn):
    cairn.brain.log_query("cli", "ask", "how does upload work", 100, None)
    pack = cairn.context("how does upload work")
    assert "Past questions" in pack.render() and "how does upload work" in pack.render()


def test_cited_docs_pulls_the_document_that_answers_the_question(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "upload-flow.md").write_text(
        "# upload flow\n" + ("filler line\n" * 400) +
        "MIDAS-77: the web proxy posts the finished MP4 to /upload with x-sa-api-key, "
        "storage lands in the recordings bucket. MIDAS-77 end.\n", encoding="utf-8")
    from cairn.core import cited_docs
    out = cited_docs(tmp_path, "the flow is documented in docs/upload-flow.md", "where does upload happen")
    assert "MIDAS-77" in out and "Cited repository documents" in out   # the keyword window, not the head
    assert cited_docs(tmp_path, "nothing cited here", "q") == ""


def test_answer_compounds_into_memory(cairn):
    cairn._remember_qa("how does upload work", "streams via disk-uploader, proxy needs x-sa-api-key")
    cairn._remember_qa("how does upload work", "duplicate must not store")
    mems = cairn.brain.q("SELECT text FROM memories WHERE text LIKE 'Q: how does upload work%'")
    assert len(mems) == 1 and "disk-uploader" in mems[0]["text"]


def test_ask_system_addresses_a_human_and_never_punts():
    from cairn.core import ASK_SYSTEM
    assert "HUMAN" in ASK_SYSTEM and "Never tell the human to call tools" in ASK_SYSTEM
    assert "CLOSE those gaps" in ASK_SYSTEM

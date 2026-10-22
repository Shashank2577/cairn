"""`cairn handoff`: a resume briefing built from a captured session and project memory."""
from __future__ import annotations

import pytest

from cairn import handoff
from cairn.engines.recall import ingest
from cairn.engines.recall.store import Store


@pytest.fixture()
def captured(cairn, repo):
    cairn.brain.add_memory("Always pass an idempotency key to the gateway", kind="convention")
    with Store.open(repo) as st:
        ingest.session_init(st, content_session_id="sess-1", project="shop-repo", prompt="Harden checkout retries",
                            platform_source="codex", cwd=str(repo))
        row = st.db.execute("SELECT id FROM sdk_sessions WHERE content_session_id='sess-1'").fetchone()
        mid = st.ensure_memory_session_id(row[0])
        st.store_observation(mid, "shop-repo", {"type": "change", "title": "Guarded retry in payments",
                                                "narrative": "Added a retry guard.",
                                                "files_modified": ["shop/payments.py"]}, 1)
        st.store_summary(mid, "shop-repo", {"request": "Harden checkout retries", "completed": "Retry guard added",
                                            "next_steps": "Add a regression test for double charge",
                                            "files_edited": ["shop/payments.py"]}, 1)
    return "sess-1"


def test_handoff_carries_the_goal_stop_point_files_and_conventions(cairn, repo, captured):
    text, meta = handoff.build_handoff(cairn, repo, None)
    assert meta["session"] == captured and meta["files"] == ["shop/payments.py"]
    assert "Harden checkout retries" in text
    assert "Add a regression test for double charge" in text
    assert "### `shop/payments.py`" in text and "shop/api.py" in text   # blast radius: its dependent
    assert "idempotency key" in text and "## Resume" in text


def test_launch_line_escapes_quotes_and_names_the_agent(repo):
    path = repo / ".cairn" / "handoffs" / "it's.md"
    line = handoff.launch_line("gemini", path)
    assert line.startswith("gemini --prompt-interactive '") and "--include-directories" in line
    assert "'\\''" in line                                             # the apostrophe in the path is escaped
    assert handoff.launch_line("codex", path).startswith("codex '")


def test_errors_are_plain(cairn, repo, captured):
    with pytest.raises(handoff.HandoffError):
        handoff.build_handoff(cairn, repo, "no-such-session")
    with pytest.raises(handoff.HandoffError):
        handoff.build_handoff(cairn, repo, None, to="emacs")


def test_write_handoff_lands_under_cairn_handoffs(cairn, repo, captured):
    text, meta = handoff.build_handoff(cairn, repo, None, "claude")
    path = handoff.write_handoff(repo, text, meta["session"])
    assert path.parent == repo / ".cairn" / "handoffs" and path.read_text(encoding="utf-8") == text


def test_cli_prints_and_writes(cairn, repo, captured):
    from typer.testing import CliRunner

    from cairn.cli import app
    runner = CliRunner()
    shown = runner.invoke(app, ["handoff"])
    assert shown.exit_code == 0 and "# Handoff: Harden checkout retries" in shown.output
    saved = runner.invoke(app, ["handoff", "--to", "codex", "--json"])
    assert saved.exit_code == 0 and '"launch": "codex ' in saved.output
    assert list((repo / ".cairn" / "handoffs").glob("*.md"))
    assert runner.invoke(app, ["handoff", "nope"]).exit_code == 2

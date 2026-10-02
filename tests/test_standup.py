"""Tests for `cairn standup`: the event-derived digest (no model calls, no git subprocesses).

Events are seeded straight into the read model with `add_events` so timestamps, bodies and
trailers are fully deterministic no matter when the suite runs.
"""
import json
import time
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from cairn.cli import app
from cairn.core import day
from cairn.standup import digest, parse_trailers, render

runner = CliRunner()


def _noon(days_back: int) -> float:
    """Noon of a calendar day, so a seeded event lands on that day regardless of the clock."""
    local = datetime.now(tz=UTC).astimezone()
    return (local - timedelta(days=days_back)).replace(hour=12, minute=0, second=0, microsecond=0).timestamp()


def _commit(eid: str, ts: float, title: str, body: str = "", sha: str = "a1b2c3d4e5f6") -> dict:
    return {"id": f"commit:{eid}", "ts": ts, "kind": "commit", "title": title, "body": body,
            "actor": "Ada", "refs": [], "meta": {"sha": sha}}


# ---- grouping ---------------------------------------------------------------------------------------

def test_digest_groups_events_into_days_newest_first(cairn):
    cairn.brain.add_events([
        _commit("t1", _noon(0), "Refactor the gateway", body="Requirement: REQ-003"),
        _commit("t2", _noon(1), "Add refund ledger", body="Requirement: REQ-010"),
    ])
    days = digest(cairn.brain, days=2)
    labels = [d["day"] for d in days]
    assert labels == sorted(labels, reverse=True)  # newest day first
    assert days[0]["day"] == day(_noon(0))
    assert "Refactor the gateway" in [c["title"] for c in days[0]["commits"]]
    yesterday = next(d for d in days if d["day"] == day(_noon(1)))
    assert [c["title"] for c in yesterday["commits"]] == ["Add refund ledger"]


# ---- trailers ---------------------------------------------------------------------------------------

def test_parse_trailers_reads_multi_line_comma_separated_bodies():
    body = "Tighten the retry guard.\n\nWork-Item: acme/widgets#12\nRequirement: REQ-001, REQ-002\n"
    assert parse_trailers(body) == {"requirements": ["REQ-001", "REQ-002"], "work_items": ["acme/widgets#12"]}


def test_parse_trailers_tolerates_missing_and_degenerate_bodies():
    assert parse_trailers("") == {"requirements": [], "work_items": []}
    assert parse_trailers("a plain body\nwith no trailers") == {"requirements": [], "work_items": []}
    assert parse_trailers("Requirement:\nWork-Item:\n") == {"requirements": [], "work_items": []}


def test_digest_carries_trailers_per_commit_and_unions_requirements(cairn):
    cairn.brain.add_events([
        _commit("r2", _noon(0) + 60, "Second", body="Requirement: REQ-002"),
        _commit("r1", _noon(0), "First", body="Work-Item: acme/widgets#1\nRequirement: REQ-001, REQ-002"),
        _commit("r0", _noon(0) - 60, "Bare", body="no trailers at all"),
    ])
    today = digest(cairn.brain, days=1)[0]
    by_title = {c["title"]: c for c in today["commits"]}
    assert by_title["First"]["requirements"] == ["REQ-001", "REQ-002"]
    assert by_title["First"]["work_items"] == ["acme/widgets#1"]
    assert by_title["Bare"]["requirements"] == [] and by_title["Bare"]["work_items"] == []
    assert today["requirements"] == ["REQ-002", "REQ-001"]  # the day's union, deduplicated (newest commit first)
    assert today["work_items"] == ["acme/widgets#1"]


# ---- the days boundary ------------------------------------------------------------------------------

def test_event_three_days_old_needs_days_3(cairn):
    cairn.brain.add_events([_commit("old1", time.time() - 60 * 3600, "Ancient refactor",
                                    body="Requirement: REQ-777")])  # 2.5 days old
    assert not any(c["title"] == "Ancient refactor" for d in digest(cairn.brain, days=1) for c in d["commits"])
    wide = digest(cairn.brain, days=3)
    assert any(c["title"] == "Ancient refactor" and c["requirements"] == ["REQ-777"]
               for d in wide for c in d["commits"])


# ---- memories, facts, sessions, drift ---------------------------------------------------------------

def test_digest_counts_memories_facts_sessions_and_drift(cairn):
    cairn.brain.set_kv("drift.last", json.dumps([{"severity": "high"}, {"severity": "low"}]))
    cairn.brain.add_events([
        {"id": "memory:m1", "ts": _noon(0), "kind": "memory", "title": "Seed events deterministically",
         "meta": {"kind": "convention"}, "source": "memory"},
        {"id": "fact:f1", "ts": _noon(0), "kind": "fact", "title": "Gateway retries twice before failing"},
        {"id": "obs:o1", "ts": _noon(0), "kind": "session", "title": "Refund flow walkthrough", "actor": "agent"},
        {"id": "summary:s1", "ts": _noon(0), "kind": "session", "title": "Refund work summary",
         "meta": {"summary": True}},
    ])
    today = next(d for d in digest(cairn.brain, days=1) if d["day"] == day(_noon(0)))
    mine = next(m for m in today["memories"] if m["text"] == "Seed events deterministically")
    assert mine["kind"] == "convention"  # sync's memory step also seeds its own memories; mine is among them
    assert "Gateway retries twice before failing" in today["facts"]
    assert today["sessions"] == 2  # observations and summaries are both session events
    assert today["drift"] == 2


# ---- rendering --------------------------------------------------------------------------------------

def test_render_empty_digest_prints_one_honest_line(repo, capsys):
    from cairn.core import Cairn
    render(digest(Cairn.here(repo).brain, days=1))  # no sync: an empty read model
    out = capsys.readouterr().out
    assert "no events" in out
    assert "✓" not in out  # nothing fabricated
    render([])
    assert "no events" in capsys.readouterr().out


# ---- the CLI ----------------------------------------------------------------------------------------

def test_standup_cli_shows_known_commits(cairn):
    res = runner.invoke(app, ["standup", "--days", "3"])
    assert res.exit_code == 0, res.output
    assert "Add payments" in res.output  # a commit the conftest fixture created
    assert "Ada" in res.output


def test_standup_cli_json_is_machine_readable(cairn):
    cairn.brain.add_events([_commit("j1", _noon(0), "Json commit",
                                    body="Work-Item: acme/widgets#9\nRequirement: REQ-005")])
    res = runner.invoke(app, ["standup", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert isinstance(data, list) and data
    today = next(d for d in data if d["day"] == day(_noon(0)))
    assert any(c["title"] == "Json commit" and c["work_items"] == ["acme/widgets#9"] for c in today["commits"])
    assert "REQ-005" in today["requirements"]
    assert isinstance(today["drift"], int) and isinstance(today["sessions"], int)

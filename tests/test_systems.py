"""Tests for the system layer (system.yaml siblings) and the review-context pack.

Sibling brains are created with the real store schema and raw sqlite3 inserts; the review
fixture is a throwaway git repo with a story branch, commit trailers and a synced cairn brain.
No network, no model calls.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cairn.cli import app
from cairn.engines import review_context as rc
from cairn.engines import systems
from cairn.store import SCHEMA

runner = CliRunner()

ENV = {**os.environ, "GIT_AUTHOR_NAME": "Ada", "GIT_AUTHOR_EMAIL": "ada@example.com",
       "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.com",
       "ANTHROPIC_API_KEY": "", "CAIRN_API_KEY": "", "OPENAI_API_KEY": ""}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          env=ENV, check=True, encoding="utf-8", errors="replace").stdout


def commit(root: Path, files: dict[str, str], msg: str) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", msg)


def sibling_brain(root: Path, rows: list[tuple], memories: list[tuple] = ()) -> Path:
    """A minimal sibling repo whose only cairn artifact is a brain.db with the real schema."""
    (root / ".cairn").mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / ".cairn" / "brain.db")
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO entities(id, kind, name, path, meta, source, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(i, k, n, p, "{}", "map", time.time()) for i, k, n, p in rows])
        con.executemany("INSERT INTO memories(id, text, kind, created_at) VALUES (?, ?, ?, ?)",
                        [(m[0], m[1], m[2], time.time()) for m in memories])
        con.commit()
    finally:
        con.close()
    return root


API = "from shop.payments import PaymentService\n\n\ndef checkout(oid):\n    return PaymentService().process(oid)\n"
PAY = "class PaymentService:\n    def process(self, oid):\n        return oid\n"
SPEC = "# Payment hooks\n\n- REQ-002: hooks MUST fire before settlement.\n"
WORK_ITEM = ("symbol:p1", "symbol", "PaymentsService", "src/services/payments.py")


@pytest.fixture()
def syst(tmp_path, monkeypatch):
    """A synced shop repo on branch story/FDY-42-payment-hooks with trailers, plus a sibling."""
    root = tmp_path / "shop-repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, {"shop/__init__.py": "", "shop/payments.py": PAY, "shop/api.py": API,
                  "tests/test_api.py": "from shop.api import checkout\n\n\ndef test_checkout():\n    assert checkout('o')\n",
                  "specs/002-hooks/spec.md": SPEC},
           "Add payments and specs")
    import cairn.sync as sync
    from cairn.core import Cairn
    c = Cairn.here(root)
    c.project.ensure_dir()
    sync.run(c)
    c.close()
    git(root, "checkout", "-qb", "story/FDY-42-payment-hooks")
    commit(root, {"shop/payments.py": PAY + "\n# hook guard\n"},
           "Wire payment hooks\n\nWork-Item: org/x#99\nRequirement: REQ-002")
    sibling_brain(tmp_path / "sibling-api", [WORK_ITEM],
                  memories=[("m1", "Payments share the idempotency key", "fact")])
    (root / "system.yaml").write_text("system: shop-system\nrepos:\n  - path: ../sibling-api\n", encoding="utf-8")
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(root)
    return root


# ---- discovery ---------------------------------------------------------------------------------------

def test_discover_missing_or_invalid_yaml_returns_none(tmp_path):
    empty = tmp_path / "r1"
    empty.mkdir()
    assert systems.discover(empty) is None
    broken = tmp_path / "r2"
    broken.mkdir()
    (broken / "system.yaml").write_text("system: [unclosed\nrepos: :::", encoding="utf-8")
    assert systems.discover(broken) is None


def test_discover_reads_root_and_cairn_fallback(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    sibling_brain(tmp_path / "sib", [WORK_ITEM])
    (root / "system.yaml").write_text("system: edgeplus\nrepos:\n  - path: ../sib\n", encoding="utf-8")
    sysdef = systems.discover(root)
    assert sysdef is not None and sysdef.name == "edgeplus"
    assert [s.name for s in sysdef.siblings] == ["sib"]
    assert sysdef.siblings[0].db == (tmp_path / "sib" / ".cairn" / "brain.db")

    (root / "system.yaml").unlink()
    (root / ".cairn").mkdir()
    (root / ".cairn" / "system.yaml").write_text("repos:\n  - path: ../sib\n", encoding="utf-8")
    fallback = systems.discover(root)
    assert fallback is not None and fallback.declared_in.endswith(".cairn/system.yaml")
    assert fallback.name == root.name  # a declaration without `system:` falls back to the repo name


def test_discover_skips_invalid_entries_and_self(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    sibling_brain(tmp_path / "good", [WORK_ITEM])
    (tmp_path / "hollow").mkdir()  # exists but no brain
    (root / "system.yaml").write_text(
        "system: mix\nrepos:\n"
        "  - path: ../good\n"
        "  - path: ../missing-repo\n"
        "  - path: ../hollow\n"
        "  - path: .\n"                 # self is never a sibling
        "  - {}\n"                      # no path at all
        "  - ../good\n", encoding="utf-8")                # bare string tolerated
    sysdef = systems.discover(root)
    assert sysdef is not None
    assert [s.name for s in sysdef.siblings] == ["good", "good"]
    reasons = " | ".join(s["reason"] for s in sysdef.skipped)
    assert "directory not found" in reasons and "no .cairn/brain.db" in reasons


# ---- read-only sibling access ------------------------------------------------------------------------

def test_system_query_reads_siblings_read_only(syst, tmp_path):
    sysdef = systems.discover(syst)
    rows = sysdef.query("SELECT id, kind, name, path FROM entities WHERE name LIKE ?", ("Payments%",))
    assert list(rows) == ["sibling-api"]
    assert rows["sibling-api"][0]["name"] == "PaymentsService"
    mems = sysdef.query("SELECT text, kind FROM memories")
    assert mems["sibling-api"][0]["text"].startswith("Payments share")
    # the read left zero traces in the sibling repo (no -wal/-shm churn, no side effects)
    assert sorted(p.name for p in (tmp_path / "sibling-api" / ".cairn").iterdir()) == ["brain.db"]


def test_system_query_tolerates_broken_sibling(syst, tmp_path):
    (tmp_path / "broken" / ".cairn").mkdir(parents=True)
    (tmp_path / "broken" / ".cairn" / "brain.db").write_bytes(b"not a database")
    (syst / "system.yaml").write_text("system: shop-system\nrepos:\n  - path: ../sibling-api\n"
                                      "  - path: ../broken\n", encoding="utf-8")
    sysdef = systems.discover(syst)
    rows = sysdef.query("SELECT COUNT(*) n FROM entities")
    assert rows["sibling-api"] and rows["broken"] == []  # a broken sibling never blocks the rest


# ---- cross-repo hits ---------------------------------------------------------------------------------

def test_cross_repo_hits_attributes_sibling_repos(syst, tmp_path):
    sibling_brain(tmp_path / "sibling-web", [WORK_ITEM[:3] + ("ui/payments.ts",)])
    (syst / "system.yaml").write_text("system: shop-system\nrepos:\n  - path: ../sibling-api\n"
                                      "  - path: ../sibling-web\n", encoding="utf-8")
    hits = systems.cross_repo_hits(syst, ["shop/payments.py"])
    repos = {h["repo"] for h in hits}
    assert repos == {"sibling-api", "sibling-web"}
    for h in hits:
        assert h["label"] == "PaymentsService" and h["kind"] == "symbol"
        assert set(h) >= {"repo", "id", "kind", "label", "path", "score"}
    # deterministic order: score desc, then repo
    assert [h["repo"] for h in hits] == sorted(repos)


def test_cross_repo_hits_unrelated_token_is_empty(syst):
    assert systems.cross_repo_hits(syst, ["completely/unrelated_thing"]) == []


def test_cross_repo_hits_plural_form_matches_and_respects_limit(syst, tmp_path):
    rows = [(f"symbol:p{i}", "symbol", f"PaymentHooks{i}", f"src/hook{i}.py") for i in range(5)]
    sibling_brain(tmp_path / "sibling-many", rows)
    (syst / "system.yaml").write_text("system: shop-system\nrepos:\n  - path: ../sibling-many\n", encoding="utf-8")
    # `payments` matches `PaymentHooks` via the loose singular form; per-sibling limit holds
    hits = systems.cross_repo_hits(syst, ["shop/payments.py"], limit=3)
    assert 0 < len(hits) <= 3 and all(h["repo"] == "sibling-many" for h in hits)


# ---- system context ----------------------------------------------------------------------------------

def test_system_context_reports_members_and_stats(syst):
    ctx = systems.system_context(syst)
    assert ctx["system"] == "shop-system"
    assert len(ctx["members"]) == 1
    m = ctx["members"][0]
    assert m["repo"] == "sibling-api" and m["symbols"] == 1 and m["memories"] == 1
    assert m["path"] == str(syst.parent / "sibling-api")


def test_system_context_without_yaml_is_none(syst):
    (syst / "system.yaml").unlink()
    assert systems.system_context(syst) is None


# ---- the review pack ---------------------------------------------------------------------------------

def test_review_pack_ticket_section_from_branch_trailers_and_specs(syst):
    pack = rc.review_pack(syst, files=["shop/payments.py"])
    assert "story/FDY-42-payment-hooks" in pack["text"]
    assert "FDY-42" in pack["text"] and "Work-Item: org/x#99" in pack["text"]
    assert "REQ-002" in pack["text"] and "specs/002-hooks/spec.md" in pack["text"]
    assert pack["ticket"]["ids"] == ["FDY-42", "org/x#99", "REQ-002"]
    assert pack["ticket"]["base"] == "main"
    assert pack["ticket"]["commits"][0]["subject"] == "Wire payment hooks"


def test_review_pack_ticket_filter_matches_and_misses(syst):
    hit = rc.review_pack(syst, files=["shop/payments.py"], ticket="FDY-42")
    assert hit["ticket"]["filter_matched"] is True
    miss = rc.review_pack(syst, files=["shop/payments.py"], ticket="NOPE-9")
    assert miss["ticket"]["filter_matched"] is False
    assert "ticket NOPE-9: not found" in miss["text"]


def test_review_pack_local_impact_renders_dependents(syst):
    pack = rc.review_pack(syst, files=["shop/payments.py"])
    assert "### Local impact" in pack["text"]
    assert pack["local_impact"]["available"] is True
    assert pack["local_impact"]["files"][0]["file"] == "shop/payments.py"
    assert pack["local_impact"]["files"][0]["items"], "api.py imports PaymentService — a dependent must show"
    assert "shop/api.py" in pack["text"] or "Dependents" in pack["text"]


def test_review_pack_cross_repo_section_needs_system_yaml(syst):
    pack = rc.review_pack(syst, files=["shop/payments.py"])
    assert "⚠ sibling: sibling-api — PaymentsService (src/services/payments.py)" in pack["text"]
    assert pack["system"] == "shop-system"
    (syst / "system.yaml").unlink()
    none = rc.review_pack(syst, files=["shop/payments.py"])
    assert "no system.yaml" in none["text"] and none["system"] is None
    assert none["cross_repo"] == []


def test_review_pack_budget_truncates_gracefully(syst):
    pack = rc.review_pack(syst, files=["shop/payments.py"], budget=60)
    assert pack["used"] <= 60
    assert len(pack["text"]) <= 60 * 4 + 8  # the bytes/4 promise, newline slack included
    assert "truncated" in pack["text"]


def test_review_pack_without_cairn_brain_still_renders_ticket(tmp_path):
    root = tmp_path / "fresh-repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, {"a.py": "x = 1\n"}, "First")
    git(root, "checkout", "-qb", "bug/FDY-200-x")
    commit(root, {"a.py": "x = 2\n"}, "Fix it\n\nWork-Item: org/y#7")
    pack = rc.review_pack(root, files=["a.py"])
    assert "FDY-200" in pack["text"] and "org/y#7" in pack["text"]
    assert "no local brain yet" in pack["text"]
    assert pack["local_impact"] == {"available": False}
    assert not (root / ".cairn").exists(), "a review pack must never create state in the reviewed repo"


# ---- the CLI -----------------------------------------------------------------------------------------

def test_cli_system_lists_members(syst):
    res = runner.invoke(app, ["system"])
    assert res.exit_code == 0, res.output
    assert "shop-system" in res.output and "sibling-api" in res.output


def test_cli_system_without_yaml_prints_a_hint(syst):
    (syst / "system.yaml").unlink()
    res = runner.invoke(app, ["system"])
    assert res.exit_code == 0, res.output
    assert "no system.yaml" in res.output


def test_cli_review_context_renders_pack(syst):
    res = runner.invoke(app, ["review-context", "--files", "shop/payments.py", "--ticket", "FDY-42"])
    assert res.exit_code == 0, res.output
    assert "Ticket" in res.output and "FDY-42" in res.output and "org/x#99" in res.output


def test_cli_review_context_json_is_machine_readable(syst):
    res = runner.invoke(app, ["review-context", "--files", "shop/payments.py", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["system"] == "shop-system"
    assert "FDY-42" in data["ticket"]["ids"]
    assert "text" in data and "Local impact" in data["text"]


def test_memory_seed_wall_clock_stops_between_seeds(cairn):
    """The memory step wedged in the field (model call hang); the cap bounds it like the deep tier's."""
    from cairn.engines.memory import seed_from_repo
    # the cairn fixture already seeded once; force a re-run where every slot is a candidate
    cairn.project.dir.joinpath("memstore/seeds.db").unlink(missing_ok=True)
    out = seed_from_repo(cairn.project, cairn.brain, None, store=cairn.memory, wall_seconds=0)
    assert out.get("candidates", 0) > 0
    assert "next sync" in out.get("note", "")

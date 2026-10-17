"""Findings that know the project (T022): history, conventions, spec task and sessions; SC-002."""
from __future__ import annotations

import pytest

from cairn.review import pipeline, store
from tests.conftest import commit


def test_past_fix_is_cited_by_commit(seeded_cairn):
    out = pipeline.run(seeded_cairn)
    hist = [f for f in out.findings if f["kind"] == "history"]
    assert hist and hist[0]["citations"][0].startswith("commit:") and hist[0]["provenance"] == "INFERRED"
    assert seeded_cairn.brain.entity(hist[0]["citations"][0]) is not None  # the citation resolves


def test_a_past_revert_is_cited_as_a_revert(seeded_cairn):
    from tests.conftest import git
    root = seeded_cairn.project.root
    git(root, "checkout", "-q", "main")
    commit(root, {"shop/ledger.py": "def record(o, c):\n    return None\n"}, "Fix ledger rounding")
    commit(root, {"shop/ledger.py": "def record(o, c):\n    return (o, c)\n"}, "Revert \"Fix ledger rounding\"")
    git(root, "checkout", "-q", "feature")
    git(root, "merge", "-q", "--no-edit", "main")
    commit(root, {"shop/ledger.py": "def record(o, c):\n    return (o, c, 1)\n"}, "touch ledger")
    from cairn import sync
    sync.run(seeded_cairn)
    out = pipeline.run(seeded_cairn)
    hist = [f for f in out.findings if f["kind"] == "history" and f["path"] == "shop/ledger.py"]
    assert hist and "revert" in hist[0]["title"] and hist[0]["severity"] == "medium"


def test_the_branchs_own_fix_is_not_cited_as_history(seeded_cairn):
    commit(seeded_cairn.project.root, {"shop/ledger.py": "def record(o, c):\n    return 2\n"}, "Fix ledger crash")
    from cairn import sync
    sync.run(seeded_cairn)
    out = pipeline.run(seeded_cairn)
    assert not [f for f in out.findings if f["kind"] == "history" and f["path"] == "shop/ledger.py"]


def test_convention_is_cited_by_memory_id(seeded_cairn):
    out = pipeline.run(seeded_cairn)
    conv = [f for f in out.findings if f["kind"] == "convention"]
    assert conv and conv[0]["citations"][0].startswith("memory:")
    mid = conv[0]["citations"][0].split(":", 1)[1]
    assert seeded_cairn.brain.memory(mid)["text"].startswith("All money")


def test_forgotten_convention_is_not_cited(seeded_cairn):
    for m in seeded_cairn.brain.memories():
        seeded_cairn.brain.forget(m["id"])
    out = pipeline.run(seeded_cairn)
    assert not [f for f in out.findings if f["kind"] == "convention"]


def test_review_keeps_owning_task_and_sessions_as_context(seeded_cairn):
    root = seeded_cairn.project.root
    commit(root, {"specs/001-pay/spec.md": "# Feature Specification: Pay\n\n**Status**: Draft\n\n"
                                            "- **FR-001**: The system MUST charge.\n",
                  "specs/001-pay/tasks.md": "# Tasks: Pay\n\n- [ ] T001 Charge in `shop/payments.py` for FR-001\n"},
           "spec pay")
    from cairn import sync
    sync.run(seeded_cairn)
    commit(root, {"shop/payments.py": "def charge(o, c):\n    return 1\n"}, "touch payments")
    out = pipeline.run(seeded_cairn)
    ctx = out.review["context"]
    assert any(t["id"] == "task:001-pay/T001" for t in ctx["tasks"])
    assert "sessions" in ctx


@pytest.mark.parametrize("bad", [
    {"provenance": "GUESS"}, {"citations": []}, {"citations": ["  "]}, {"severity": "critical"}, {"kind": "vibes"}, {"title": " "}])
def test_a_finding_without_provenance_or_citation_is_rejected(tmp_path, bad):
    from cairn.store import Brain
    brain = Brain(tmp_path / "b.db")
    rev = store.start_review(brain, project="p", branch="b", base_sha="1", head_sha="2")
    good = {"kind": "history", "severity": "low", "path": "a.py", "title": "t", "provenance": "INFERRED",
            "citations": ["commit:abc"]}
    assert len(store.add_findings(brain, rev["id"], [good])) == 1
    with pytest.raises(store.FindingRejected):
        store.add_findings(brain, rev["id"], [good, {**good, **bad}])
    assert len(store.findings_of(brain, rev["id"])) == 1  # nothing was half-written


def test_triage_state_survives_a_rerun_of_the_same_finding(tmp_path):
    from cairn.store import Brain
    brain = Brain(tmp_path / "b.db")
    rev = store.start_review(brain, project="p", branch="b", base_sha="1", head_sha="2")
    f = {"kind": "history", "severity": "low", "path": "a.py", "title": "t", "provenance": "INFERRED", "citations": ["commit:x"]}
    fid = store.add_findings(brain, rev["id"], [f])[0]["id"]
    brain.db.execute("UPDATE review_findings SET state='dismissed' WHERE id=?", (fid,))
    brain.db.commit()
    again = store.add_findings(brain, rev["id"], [f])
    assert again[0]["id"] == fid and again[0]["state"] == "dismissed"

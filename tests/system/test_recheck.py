"""Staleness re-check (FR-018, FR-019, SC-004; T029): a claim whose evidence moved and is no longer derived
is marked stale with the reason and the time, restored evidence clears the mark, and nothing is ever
deleted. Also the negative the prototype taught: an import without a use site creates no claim to go stale.
"""
from __future__ import annotations

import time
from contextlib import contextmanager

from cairn import drift
from cairn.store import Brain
from cairn.system.build import sync_step
from cairn.system.model import load, save

from .util import write

DOCKER = {"Dockerfile": "FROM base\nEXPOSE 8080\nCMD [\"run\"]\n",
          "pyproject.toml": '[project]\nname = "svc"\ndependencies = ["redis"]\n'}
PUB = "import redis\nr = redis.Redis()\nr.publish('order.created', b'x')\n"
BARE = "import redis\nVALUE = 1\n"


class _Project:
    def __init__(self, root, name):
        self.root, self.name = root, name


class _Cairn:
    def __init__(self, root, name, brain):
        self.project, self.brain = _Project(root, name), brain


@contextmanager
def brain(root, name="svc"):
    b = Brain(root / ".cairn" / "brain.db")
    try:
        yield b
    finally:
        b.close()


def sync(root, name="svc") -> dict:
    """What sync's system step does, so the test exercises the path a developer takes."""
    with brain(root, name) as b:
        return sync_step(_Cairn(root, name, b))


def model(root, name="svc"):
    with brain(root, name) as b:
        return load(b, name)


def rel(m, *, contains: str):
    return next(r for r in m.relationships.values() if contains in (r.what or ""))


def setup(tmp_path, files=None):
    return write(tmp_path / "svc", {**DOCKER, **(files or {"app.py": PUB})})


def test_removing_the_evidence_marks_the_relationship_stale(tmp_path):
    """SC-004, FR-018: the only evidence for a relationship goes, the next sync marks it stale with the
    reason, the file it named, and the time; the claim survives in the store."""
    root = setup(tmp_path)
    sync(root)
    assert not rel(model(root), contains="Publishes order.created").stale_reason
    with brain(root) as b:
        assert b.get_kv("sm.recheck:svc")

    (root / "app.py").write_text("import redis\nr = redis.Redis()\n", encoding="utf-8")
    out = sync(root)
    stale = rel(model(root), contains="Publishes order.created")
    assert stale.provenance == "stale" and out["stale"] >= 1
    assert "app.py" in stale.stale_reason
    assert stale.stale_since and stale.stale_since > time.time() - 3600


def test_removing_the_evidence_file_names_it_in_the_reason(tmp_path):
    """FR-018: a claim whose file was deleted says so, rather than "no longer derived"."""
    root = setup(tmp_path)
    sync(root)
    (root / "app.py").unlink()
    sync(root)
    reason = rel(model(root), contains="Publishes order.created").stale_reason
    assert "app.py" in reason and "gone" in reason


def test_restoring_the_evidence_clears_the_mark(tmp_path):
    """FR-018: restoring the publish makes the claim current again, with no stale mark left."""
    root = setup(tmp_path)
    sync(root)
    (root / "app.py").write_text("import redis\nr = redis.Redis()\n", encoding="utf-8")
    sync(root)
    assert rel(model(root), contains="Publishes order.created").provenance == "stale"

    (root / "app.py").write_text(PUB, encoding="utf-8")
    out = sync(root)
    fresh = rel(model(root), contains="Publishes order.created")
    assert fresh.provenance == "extracted" and not fresh.stale_reason and not fresh.stale_since
    assert out["recheck"]["cleared"] >= 1


def test_stale_claims_survive_repeated_syncs(tmp_path):
    """FR-018: never deleted, and the first sighting fixes the timestamp."""
    root = setup(tmp_path)
    sync(root)
    (root / "app.py").write_text("import redis\n", encoding="utf-8")
    sync(root)
    first = rel(model(root), contains="Publishes order.created")
    assert first.provenance == "stale"
    time.sleep(0.01)
    sync(root)
    again = rel(model(root), contains="Publishes order.created")
    assert again.stale_reason == first.stale_reason and again.stale_since == first.stale_since


def test_only_claims_in_changed_files_are_rechecked(tmp_path):
    """FR-019: a claim whose evidence file did not move is reused, not re-derived."""
    root = setup(tmp_path)
    sync(root)
    (root / "other.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    out = sync(root)["recheck"]
    assert out.get("rechecked", 0) == 0 and out["reused"] >= 1

    (root / "app.py").write_text(BARE, encoding="utf-8")
    out = sync(root)["recheck"]
    assert out["rechecked"] >= 1 and out["stale"] >= 1


def test_import_without_a_use_creates_no_claim(tmp_path):
    """The prototype's lesson, and the reason nothing goes stale here: a bare import derives nothing."""
    root = setup(tmp_path, {"app.py": BARE})
    sync(root)
    m = model(root)
    assert not [r for r in m.relationships.values() if "Publishes" in (r.what or "")]
    assert not [e for e in m.elements.values() if e.type in ("channel", "data-store")]
    assert not [e for e in m.elements.values() if e.stale_reason]
    out = sync(root)
    with brain(root) as b:
        assert out["stale"] == 0 and not drift.architecture(b)


def test_declared_claims_are_never_marked_stale(tmp_path):
    """A declaration is intent, not a derivation the code can refute."""
    root = setup(tmp_path)
    sync(root)
    with brain(root) as b:
        m = load(b, "svc")
        declared = next(r for r in m.relationships.values() if r.what)
        declared.provenance, declared.evidence = "declared", []
        rel_id = declared.id
        save(b, m, "svc")
    (root / "app.py").write_text(BARE, encoding="utf-8")
    sync(root)
    again = next(r for r in model(root).relationships.values() if r.id == rel_id)
    assert again.provenance == "declared" and not again.stale_reason


def test_a_build_that_ran_out_of_room_marks_nothing(tmp_path):
    """A truncated build is not evidence that every claim is false."""
    from cairn.system.build import build_repo
    from cairn.system.recheck import recheck
    root = setup(tmp_path)
    sync(root)
    stale_id = rel(model(root), contains="Publishes order.created").id
    fresh = build_repo(root, "svc", "abc")
    fresh.relationships.pop(stale_id, None)
    fresh.stats = {**getattr(fresh, "stats", {}), "elements truncated": 1}
    with brain(root) as b:
        merged, stats = recheck(b, root, "svc", fresh)
    assert stats.get("stale", 0) == 0 and stale_id in merged.relationships


def test_stale_claims_appear_as_drift_findings_and_keep_their_id(tmp_path):
    """FR-018, T028: stale claims are drift, with the missing evidence named; re-running keeps the id and
    the time it was first seen."""
    root = setup(tmp_path)
    sync(root)
    (root / "app.py").write_text("import redis\n", encoding="utf-8")
    sync(root)
    with brain(root) as b:
        found = drift.architecture(b)
        assert found and all(d["kind"] == "stale-architecture" for d in found)
        rels = [d for d in found if d["cite"].startswith("claim:svc:rel:")]
        assert rels and all(d["severity"] == "high" and "→" in d["title"] for d in rels)
        assert any("app.py" in e for d in found for e in d["evidence"])
        assert drift.architecture(b) == found

        drift.record(_Cairn(root, "svc", b), found)
        first = {e["id"]: e["ts"] for e in b.events(kinds=["drift"])}
        drift.record(_Cairn(root, "svc", b), drift.architecture(b))
        assert {e["id"]: e["ts"] for e in b.events(kinds=["drift"])} == first


def test_a_stale_element_is_medium_severity(tmp_path):
    """T028: a box that vanished is real drift, but not a wrong interaction."""
    root = setup(tmp_path)
    sync(root)
    with brain(root) as b:
        b.db.execute("UPDATE sm_element SET stale_reason='gone', stale_since=1.0 WHERE type='container'")
        b.db.commit()
        found = drift.architecture(b)
    assert {d["severity"] for d in found} == {"medium"}
    assert all(d["title"].endswith("(container) is stale: gone") for d in found)
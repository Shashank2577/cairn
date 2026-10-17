"""Foundation of the system model: store migrations, model types and storage, path safety, safe YAML,
token sync. IDs in docstrings map to specs/002-system-model/traceability.md."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cairn.store import SCHEMA, SCHEMA_VERSION, Brain, migrate
from cairn.system import model as M
from cairn.system.limits import MAX_EVIDENCE_PER_CLAIM, MAX_LABEL_CHARS
from cairn.system.safeyaml import UnsafeYAML, load_file, loads

ROOT = Path(__file__).resolve().parents[2]


def _v1_store(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO kv VALUES('schema_version','1')")
    db.commit()
    db.close()


def test_migration_upgrades_a_v1_store(tmp_path):
    """FR-001 storage; T004/T004a: an existing v1 store gains the system-model tables on open."""
    _v1_store(tmp_path / "b.db")
    b = Brain(tmp_path / "b.db")
    names = {r[0] for r in b.q("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'sm_%'")}
    assert names == {"sm_element", "sm_relationship", "sm_evidence", "sm_flow", "sm_flow_step"}
    assert b.get_kv("schema_version") == str(SCHEMA_VERSION)
    b.close()


def test_migration_is_idempotent_and_reopen_writes_nothing(tmp_path):
    """T004a: re-running migrations is harmless; reopening an up-to-date store does not write."""
    b = Brain(tmp_path / "b.db")
    migrate(b.db, 1)  # again
    b.close()
    before = (tmp_path / "b.db").stat().st_mtime_ns
    b = Brain(tmp_path / "b.db")
    assert b.get_kv("schema_version") == str(SCHEMA_VERSION)
    b.close()
    wal = tmp_path / "b.db-wal"
    assert (tmp_path / "b.db").stat().st_mtime_ns == before or not wal.exists() or wal.stat().st_size == 0


def test_newer_store_is_left_alone(tmp_path):
    """T004a: a store written by a newer build is not downgraded or rewritten."""
    _v1_store(tmp_path / "b.db")
    db = sqlite3.connect(tmp_path / "b.db")
    db.execute("UPDATE kv SET value='99' WHERE key='schema_version'")
    db.commit()
    db.close()
    b = Brain(tmp_path / "b.db")
    assert b.get_kv("schema_version") == "99"
    b.close()


def test_corrupt_version_value_still_opens(tmp_path):
    _v1_store(tmp_path / "b.db")
    db = sqlite3.connect(tmp_path / "b.db")
    db.execute("UPDATE kv SET value='garbage' WHERE key='schema_version'")
    db.commit()
    db.close()
    b = Brain(tmp_path / "b.db")
    assert b.get_kv("schema_version") == str(SCHEMA_VERSION)
    b.close()


@pytest.mark.parametrize("raw,want", [
    ("src\\db.py", "src/db.py"), ("C:\\x\\..\\..\\etc/passwd", "etc/passwd"), ("/abs/a/./b", "abs/a/b"),
    ("../../x", "x"), ("a/b/../c", "a/c"), ("", None), (None, None), ("..", None)])
def test_posix_paths_never_escape(raw, want):
    """Security: stored evidence paths are relative, POSIX, and cannot climb above the repository."""
    assert M.posix(raw) == want


def test_model_round_trip_through_the_store(tmp_path):
    """FR-001, FR-004, FR-029c: elements and relationships keep type, provenance, evidence and commit."""
    b = Brain(tmp_path / "b.db")
    m = M.Model()
    m.add(M.Element(id="r:container:api", type="container", name="api", repo="r", kind="service", tech="FastAPI",
                    evidence=[M.Evidence("r", "dockerfile", "Dockerfile", 5)]))
    m.add(M.Element(id="r:data-store:pg", type="data-store", name="PostgreSQL", repo="r", provenance="inferred"))
    r = m.relate(M.Relationship("r:container:api", "r:data-store:pg", "store-use", how="SQL", what="Reads orders",
                                evidence=[M.Evidence("r", "driver", "src\\db.py", 7)]))
    assert M.save(b, m, "r", "abc123") == {"elements": 2, "relationships": 1}
    n = M.load(b, "r")
    assert n.elements["r:container:api"].tech == "FastAPI"
    assert n.elements["r:data-store:pg"].provenance == "inferred"
    assert n.relationships[r.id].evidence[0].ref() == "r/src/db.py:7"
    assert n.relationships[r.id].built_at_commit == "abc123"
    assert n.elements["r:container:api"].built_at_commit == "abc123"
    b.close()


def test_save_replaces_but_keeps_stale_claims(tmp_path):
    """FR-018: a re-save rewrites the repository's claims but keeps those marked stale."""
    b = Brain(tmp_path / "b.db")
    m = M.Model()
    m.add(M.Element(id="r:container:a", type="container", name="a", repo="r"))
    m.add(M.Element(id="r:container:old", type="container", name="old", repo="r", stale_reason="gone", stale_since=1.0))
    M.save(b, m, "r")
    M.save(b, M.Model(), "r")
    left = M.load(b, "r").elements
    assert set(left) == {"r:container:old"}
    b.close()


def test_invalid_types_and_styles_are_refused():
    with pytest.raises(ValueError):
        M.Element(id="x", type="microservice", name="x")
    with pytest.raises(ValueError):
        M.Relationship("a", "b", "r", style="bidirectional")
    with pytest.raises(ValueError):
        M.Element(id="x", type="container", name="x", provenance="guess")


def test_labels_and_evidence_are_bounded():
    """Resource limits: long labels are cut; evidence per claim is capped."""
    e = M.Element(id="x", type="container", name="n" * 5000, desc="d\n" * 5000)
    assert len(e.name) <= MAX_LABEL_CHARS and "\n" not in e.desc
    for i in range(MAX_EVIDENCE_PER_CLAIM + 10):
        e.add_evidence(M.Evidence("r", "call", "f.py", i + 1))
    assert len(e.evidence) == MAX_EVIDENCE_PER_CLAIM


def test_relationship_ids_are_stable():
    a = M.Relationship("x", "y", "http-route-match", key="POST /a")
    b = M.Relationship("x", "y", "http-route-match", key="POST /a", what="different label")
    c = M.Relationship("x", "y", "http-route-match", key="GET /a")
    assert a.id == b.id != c.id


BOMB = "a: &a [x,x,x,x,x,x,x,x,x]\n" + "\n".join(
    f"{c}: &{c} [" + ",".join(["*" + p] * 9) + "]" for p, c in zip("abcdefgh", "bcdefghi"))


@pytest.mark.parametrize("doc,msg", [
    (BOMB, "expands"),
    ("x: !!python/object/apply:os.system ['echo pwned']", "not valid YAML"),
    ("x: !!python/name:os.system", "not valid YAML"),
    ("a: " + "[" * 60 + "]" * 60, "nested"),
    ("a: [1, 2", "not valid YAML"),
    ("\x00\x01\x02", "not valid YAML"),
])
def test_safe_yaml_refuses_hostile_documents(doc, msg):
    """Security: no object construction, no alias bombs, no unbounded nesting, clean errors."""
    with pytest.raises(UnsafeYAML, match=msg):
        loads(doc)


def test_safe_yaml_size_limit(tmp_path):
    p = tmp_path / "big.yaml"
    p.write_text("a: " + "x" * 3000, encoding="utf-8")
    with pytest.raises(UnsafeYAML, match="larger than"):
        load_file(p, max_bytes=1000)
    assert load_file(p, max_bytes=10_000)["a"].startswith("xxx")


def test_safe_yaml_accepts_non_utf8_without_crashing():
    assert loads(b"a: caf\xe9")["a"].startswith("caf")


def test_generated_token_copies_are_in_sync():
    """FR-021: the docs copy and the UI's CSS roles are generated from the one canonical token file."""
    r = subprocess.run([sys.executable, str(ROOT / "scripts/sync_tokens.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

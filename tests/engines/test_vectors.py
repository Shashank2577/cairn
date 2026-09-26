"""Shared embeddings + vector index: hashing fallback, faiss and numpy backends, persistence."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from cairn.engines import vectors


@pytest.fixture(autouse=True)
def hashing(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBEDDER", "hash")
    vectors.reset_embedder()
    yield
    vectors.reset_embedder()


def test_hash_embed_is_deterministic_normalised_and_lexical():
    a, b, c = vectors.embed(["payment retries need idempotency keys",
                             "Payments retry with an idempotency key",
                             "the UI uses a dark colour theme"])
    assert len(a) == vectors.DIM == 384
    assert abs(np.linalg.norm(a) - 1.0) < 1e-5
    assert vectors.embed(["payment retries need idempotency keys"])[0] == a
    assert vectors.cosine(a, b) > vectors.cosine(a, c)
    assert vectors.embedder_id() == vectors.HASH_ID
    assert vectors.embed([]) == []
    assert len(vectors.embed([""])[0]) == 384  # empty text still yields a unit vector


def test_embed_is_thread_safe():
    out: list = []
    texts = [f"memory number {i}" for i in range(40)]

    def work():
        out.append(vectors.embed(texts))

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(o == out[0] for o in out)


@pytest.mark.parametrize("backend", ["faiss", "numpy"])
def test_index_add_search_delete_persist(tmp_path, monkeypatch, backend):
    if backend == "numpy":
        monkeypatch.setenv("CAIRN_VECTOR_BACKEND", "numpy")
    else:
        pytest.importorskip("faiss")
    texts = {"a": "use optimistic locking for orders", "b": "ledger writes use pessimistic locking",
             "c": "frontend is built with react", "d": "orders lock optimistically"}
    idx = vectors.VectorIndex(tmp_path / "ix", embedder=vectors.embedder_id())
    assert idx.backend == backend
    idx.add(list(texts), vectors.embed(list(texts.values())))
    assert len(idx) == 4 and "a" in idx
    q = vectors.embed(["optimistic locking orders"])[0]
    hits = idx.search(q, k=2)
    assert hits[0][0] in ("a", "d") and {h[0] for h in hits} <= {"a", "d", "b"}
    assert hits[0][1] >= hits[1][1]
    # restricted search
    only = idx.search(q, k=3, allowed=["b", "c", "missing"])
    assert {h[0] for h in only} == {"b", "c"}
    assert idx.search(q, k=3, allowed=["missing"]) == []
    # replace + delete
    idx.add(["c"], vectors.embed(["optimistic locking orders"]))
    assert idx.search(q, k=1)[0][0] == "c"
    assert idx.delete(["c", "zzz"]) == 1 and "c" not in idx and len(idx) == 3
    idx.save()
    again = vectors.VectorIndex(tmp_path / "ix", embedder=vectors.embedder_id())
    assert again.ids() == idx.ids() and not again.stale
    assert again.search(q, k=1)[0][0] in ("a", "d")
    assert np.allclose(again.get("b"), idx.get("b"))
    # a different embedding space marks the index stale
    assert vectors.VectorIndex(tmp_path / "ix", embedder="other-model").stale


def test_index_batch_with_repeated_ids_and_dim_check(tmp_path):
    idx = vectors.VectorIndex(tmp_path / "ix", dim=4)
    idx.add(["x", "x", "y"], [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]])
    assert len(idx) == 2
    assert idx.search([0, 1, 0, 0], k=1) == [("x", pytest.approx(1.0))]
    with pytest.raises(ValueError):
        idx.add(["z"], [[1, 2, 3]])
    idx.clear()
    assert len(idx) == 0 and idx.search([1, 0, 0, 0], 3) == []


def test_indexes_written_without_faiss_load_with_faiss(tmp_path, monkeypatch):
    pytest.importorskip("faiss")
    monkeypatch.setenv("CAIRN_VECTOR_BACKEND", "numpy")
    idx = vectors.VectorIndex(tmp_path / "ix", dim=4)
    idx.add(["p", "q"], [[1, 0, 0, 0], [0, 1, 0, 0]])
    idx.save()
    monkeypatch.delenv("CAIRN_VECTOR_BACKEND")
    fa = vectors.VectorIndex(tmp_path / "ix", dim=4)
    assert fa.backend == "faiss" and fa.search([0, 1, 0, 0], 1)[0][0] == "q"


# ---- several writers --------------------------------------------------------------------------------
def _ids(path, dim=4) -> list[str]:
    return sorted(vectors.VectorIndex(path, dim=dim).ids())


def test_two_writers_on_one_index_merge_instead_of_overwriting(tmp_path):
    a = vectors.VectorIndex(tmp_path / "ix", dim=4)
    b = vectors.VectorIndex(tmp_path / "ix", dim=4)  # opened before either saved
    a.add(["a"], [[1, 0, 0, 0]])
    a.save()
    b.add(["b"], [[0, 1, 0, 0]])
    b.save()
    assert _ids(tmp_path / "ix") == ["a", "b"]
    assert sorted(a.ids()) == ["a", "b"] and a.search([0, 1, 0, 0], 1)[0][0] == "b"  # reads see b's save
    a.delete(["b"])
    a.save()
    b.add(["c"], [[0, 0, 1, 0]])
    b.save()  # b's stale copy must not bring "b" back
    assert _ids(tmp_path / "ix") == ["a", "c"]
    # unsaved changes survive a reload triggered by someone else's save
    a.add(["d"], [[0, 0, 0, 1]])
    b.add(["e"], [[1, 1, 0, 0]])
    b.save()
    assert "d" in a and "e" in a
    a.save()
    assert _ids(tmp_path / "ix") == ["a", "c", "d", "e"]


def test_a_writer_in_another_process_is_seen_and_kept(tmp_path):
    parent = vectors.VectorIndex(tmp_path / "ix", dim=4)
    parent.add(["p1"], [[1, 0, 0, 0]])
    parent.save()
    script = ("import sys; from cairn.engines import vectors; "
              "ix = vectors.VectorIndex(sys.argv[1], dim=4); ix.add(['child'], [[0, 1, 0, 0]]); ix.save()")
    subprocess.run([sys.executable, "-c", script, str(tmp_path / "ix")], check=True, timeout=120)
    assert "child" in parent
    parent.add(["p2"], [[0, 0, 1, 0]])
    parent.save()
    assert _ids(tmp_path / "ix") == ["child", "p1", "p2"]


def test_legacy_two_file_layout_is_read_and_migrated(tmp_path):
    d = tmp_path / "ix"
    d.mkdir()
    np.save(d / "vectors.npy", np.eye(4, dtype=np.float32)[:2])
    (d / "meta.json").write_text(json.dumps({"dim": 4, "embedder": None, "ids": ["x", "y"]}))
    idx = vectors.VectorIndex(d, dim=4)
    assert idx.ids() == ["x", "y"]
    idx.add(["z"], [[0, 0, 1, 0]])
    idx.save()
    assert not (d / "vectors.npy").exists() and (d / "index.npz").exists() and _ids(d) == ["x", "y", "z"]


def test_file_lock_is_reentrant_and_excludes_other_processes(tmp_path):
    lock = vectors.file_lock(tmp_path / "x.lock")
    assert vectors.file_lock(tmp_path / "x.lock") is lock
    with lock, lock:  # re-entrant in one thread
        pass
    ready = tmp_path / "ready"
    script = ("import sys, time, pathlib; from cairn.engines import vectors\n"
              "with vectors.file_lock(sys.argv[1]):\n"
              "    pathlib.Path(sys.argv[2]).write_text('1'); time.sleep(3)")
    child = subprocess.Popen([sys.executable, "-c", script, str(tmp_path / "x.lock"), str(ready)])
    try:
        deadline = time.monotonic() + 60
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        other = vectors.FileLock(tmp_path / "x.lock", timeout=0.3)  # a separate holder, like another process
        with pytest.raises(TimeoutError):
            other.__enter__()
    finally:
        child.wait(timeout=60)
    with vectors.FileLock(tmp_path / "x.lock", timeout=5):
        pass

"""Local embeddings and a persistent vector index, shared by every engine.

``embed(texts)`` turns text into 384-d unit vectors with a local ONNX model (no key, no network
after the first download). When the model cannot be loaded — offline first run, missing package,
``CAIRN_EMBEDDER=hash`` — a deterministic feature-hashing embedder takes over so every feature
keeps working (lexical rather than semantic similarity).

``VectorIndex(path, dim)`` stores vectors under string ids with cosine search, deletes and
persistence. It uses faiss when installed and plain numpy otherwise; both read and write the same
on-disk file (``index.npz`` inside ``path``), so an index moves freely between machines with and
without faiss. Several processes may write the same index: saves are serialised by a file lock and
merge with what others saved (see ``VectorIndex``). ``file_lock`` / ``file_state`` /
``atomic_write_bytes`` are the building blocks, reusable by other stores.

Environment
  CAIRN_EMBEDDER       auto (default) | fastembed | hash
  CAIRN_MODEL_CACHE    where the embedding model is cached (default: $CAIRN_HOME/models or ~/.cairn/models)
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import warnings
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from .. import filelock

log = logging.getLogger("cairn.vectors")

MODEL = "BAAI/bge-small-en-v1.5"
DIM = 384
HASH_ID = "hash-v1"
# The model retrieves best when short queries carry this instruction (documents are embedded as-is).
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
# Cosine below which a hit is rarely relevant, per embedding space (the model's scores are compressed).
_RELEVANCE_FLOOR = {"fastembed": 0.58, "hash": 0.15}

_lock = threading.RLock()
_model = None
_backend: str | None = None  # "fastembed:<model>" or HASH_ID once resolved
_load_error: str | None = None
_cache: "OrderedDict[tuple[str, str], np.ndarray]" = OrderedDict()
_CACHE_MAX = 4096


def cairn_home() -> Path:
    return Path(os.environ.get("CAIRN_HOME") or Path.home() / ".cairn")


def model_cache_dir() -> Path:
    env = os.environ.get("CAIRN_MODEL_CACHE")
    return Path(env) if env else cairn_home() / "models"


def _mode() -> str:
    mode = os.environ.get("CAIRN_EMBEDDER", "auto").strip().lower()
    return mode if mode in ("auto", "fastembed", "hash") else "auto"


def _load() -> str:
    """Resolve the embedding backend once per process (thread-safe)."""
    global _model, _backend, _load_error
    mode = _mode()
    if mode == "hash":
        return HASH_ID
    with _lock:
        if _backend is not None:
            return _backend
        try:
            from fastembed import TextEmbedding

            cache = model_cache_dir()
            cache.mkdir(parents=True, exist_ok=True)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                _model = TextEmbedding(MODEL, cache_dir=str(cache))
            _backend = f"fastembed:{MODEL}"
        except Exception as exc:  # any failure degrades to the hashing embedder
            if mode == "fastembed":
                raise
            _load_error = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("local embedding model unavailable, using hashing embedder: %s", _load_error)
            _model, _backend = None, HASH_ID
        return _backend


def embedder_id() -> str:
    """Identity of the active embedding space. Vectors from different ids must not be mixed."""
    return _load()


def load_error() -> str | None:
    return _load_error


def reset_embedder() -> None:
    """Forget the resolved backend (tests, or after the model has been downloaded)."""
    global _model, _backend, _load_error
    with _lock:
        _model, _backend, _load_error = None, None, None
        _cache.clear()


# ---- hashing embedder ----------------------------------------------------------------------------
_TOKEN = re.compile(r"[a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SUFFIXES = ("ingly", "edly", "ing", "ers", "ies", "ied", "ed", "es", "er", "ly", "s")


def _stem(word: str) -> str:
    for suf in _SUFFIXES:
        if len(word) > len(suf) + 2 and word.endswith(suf):
            return word[: -len(suf)]
    return word


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    h = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    n = int.from_bytes(h, "little")
    return n % dim, (1.0 if (n >> 63) & 1 else -1.0)


def hash_embed(text: str, dim: int = DIM) -> np.ndarray:
    """Deterministic signed feature hashing over stems, stem bigrams and character trigrams."""
    vec = np.zeros(dim, dtype=np.float32)
    words = [_stem(w) for w in _TOKEN.findall(_CAMEL.sub(" ", text or "").replace("_", " ").lower())]
    feats: list[tuple[str, float]] = [(f"w:{w}", 1.0) for w in words]
    feats += [(f"b:{a} {b}", 0.5) for a, b in zip(words, words[1:])]
    for w in words:
        padded = f"#{w}#"
        feats += [(f"c:{padded[i:i + 3]}", 0.25) for i in range(max(1, len(padded) - 2))]
    for feat, weight in feats:
        i, sign = _bucket(feat, dim)
        vec[i] += sign * weight
    norm = float(np.linalg.norm(vec))
    if norm == 0.0:
        i, _ = _bucket("<empty>", dim)
        vec[i] = 1.0
        return vec
    return vec / norm


# ---- public embedding API ------------------------------------------------------------------------
def embed_array(texts: Sequence[str]) -> np.ndarray:
    """Embed texts into an (n, 384) float32 matrix of unit vectors."""
    texts = [t if isinstance(t, str) else str(t) for t in texts]
    if not texts:
        return np.zeros((0, DIM), dtype=np.float32)
    backend = _load()
    out: list[np.ndarray | None] = [None] * len(texts)
    todo: list[int] = []
    with _lock:
        for i, t in enumerate(texts):
            hit = _cache.get((backend, t))
            if hit is not None:
                _cache.move_to_end((backend, t))
                out[i] = hit
            else:
                todo.append(i)
    if todo:
        missing = [texts[i] for i in todo]
        if backend == HASH_ID:
            fresh = [hash_embed(t) for t in missing]
        else:
            with _lock:  # onnxruntime sessions are shared; serialise to keep memory flat
                fresh = [np.asarray(v, dtype=np.float32) for v in _model.embed(missing)]
            fresh = [v / (float(np.linalg.norm(v)) or 1.0) for v in fresh]
        with _lock:
            for i, v in zip(todo, fresh):
                out[i] = v
                _cache[(backend, texts[i])] = v
            while len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)
    return np.vstack(out).astype(np.float32, copy=False)


def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts into 384-d unit vectors (cosine similarity == dot product)."""
    return embed_array(texts).tolist()


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def embed_query(texts: list[str]) -> list[list[float]]:
    """Embed search queries (adds the model's retrieval instruction; identical to ``embed`` offline)."""
    if _load() == HASH_ID:
        return embed(texts)
    return embed([QUERY_INSTRUCTION + t for t in texts])


def relevance_floor() -> float:
    """A sensible minimum cosine for "this hit is about the query" in the active embedding space."""
    return _RELEVANCE_FLOOR["hash" if _load() == HASH_ID else "fastembed"]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    return float(va @ vb / (na * nb)) if na and nb else 0.0


# ---- cross-process locking -----------------------------------------------------------------------
class FileLock:
    """Re-entrant lock for one path: a thread lock within this process plus an advisory ``flock`` on
    the path across processes (``fcntl`` on POSIX, ``msvcrt`` on Windows).

    Obtain it with ``file_lock(path)`` — every object guarding the same path in a process shares one
    instance, so nested use (a store and the index inside it) never deadlocks.
    """

    def __init__(self, path: Path, timeout: float = 120.0):
        self.path = path
        self.timeout = timeout
        self._rlock = threading.RLock()
        self._depth = 0
        self._fh = None

    def __enter__(self) -> "FileLock":
        self._rlock.acquire()
        try:
            if self._depth == 0:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fh = open(self.path, "a+", encoding="utf-8")
                try:
                    _flock(fh, self.timeout, self.path)
                except BaseException:
                    fh.close()
                    raise
                self._fh = fh
            self._depth += 1
        except BaseException:
            self._rlock.release()
            raise
        return self

    def __exit__(self, *exc) -> None:
        try:
            self._depth -= 1
            if self._depth == 0 and self._fh is not None:
                try:
                    _funlock(self._fh)
                finally:
                    self._fh.close()
                    self._fh = None
        finally:
            self._rlock.release()


def _flock(fh, timeout: float, path: Path) -> None:
    if not filelock.lock(fh, timeout):
        raise TimeoutError(f"timed out waiting for {path}")


def _funlock(fh) -> None:
    filelock.unlock(fh)


_file_locks: dict[str, FileLock] = {}
_file_locks_guard = threading.Lock()


def file_lock(path: str | Path) -> FileLock:
    """The process-wide re-entrant ``FileLock`` for ``path`` (created on first use)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.join(os.path.realpath(path.parent), path.name)
    with _file_locks_guard:
        lock = _file_locks.get(key)
        if lock is None:
            lock = _file_locks[key] = FileLock(Path(key))
        return lock


def file_state(path: Path) -> tuple | None:
    """Identity of a file's current version (changes on every atomic replace); None when missing."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ---- vector index --------------------------------------------------------------------------------
def _faiss():
    if os.environ.get("CAIRN_VECTOR_BACKEND", "").lower() == "numpy":
        return None
    try:
        import faiss  # noqa: PLC0415 (optional accelerator)

        return faiss
    except Exception:
        return None


class VectorIndex:
    """Cosine-similarity index over string ids, persisted in a directory.

    Safe for several writers on the same directory (processes or objects): changes are kept as a
    journal until ``save()``, which — holding ``<path>/.lock`` — reloads whatever other writers saved
    meanwhile, replays this writer's changes on top and replaces ``index.npz`` atomically (vectors, ids
    and metadata in one file, so readers never see them out of step). Reads pick up other writers'
    saves automatically.

    ``embedder`` (optional) records which embedding space the vectors belong to; when an existing
    index was written by a different embedder, ``stale`` is True and the owner should re-embed.
    """

    FILE = "index.npz"
    LEGACY = ("vectors.npy", "meta.json")

    def __init__(self, path: str | Path, dim: int = DIM, *, embedder: str | None = None):
        self.path = Path(path)
        self.dim = int(dim)
        self.embedder = embedder
        self.stale = False
        self._flock = file_lock(self.path / ".lock")
        self._lock = self._flock._rlock          # in-process guard shared with the file lock
        self._faiss = _faiss()
        self._ids: list[str] = []            # row order of the matrix
        self._row: dict[str, int] = {}       # id -> row
        self._matrix = np.zeros((0, self.dim), dtype=np.float32)
        self._index = None                   # faiss index mirroring _matrix, row numbers as ids
        self._ops: list[tuple] = []          # unsaved changes, replayed over newer saves by others
        self._state: tuple | None = None     # version of index.npz last loaded or written
        self.generation = 0
        self._disk_embedder: str | None = None
        with self._flock:
            self._load()

    @property
    def backend(self) -> str:
        return "faiss" if self._faiss is not None else "numpy"

    @property
    def file(self) -> Path:
        return self.path / self.FILE

    # ---- persistence -----------------------------------------------------------------------------
    def _read_disk(self) -> tuple[list[str], np.ndarray, dict] | None:
        """(ids, matrix, meta) as saved, or None when nothing (readable) is on disk."""
        if self.file.exists():
            try:
                with np.load(self.file, allow_pickle=False) as z:
                    meta = json.loads(str(z["meta"]))
                    return [str(i) for i in z["ids"].tolist()], z["vectors"].astype(np.float32), meta
            except (OSError, ValueError, KeyError) as exc:
                log.warning("vector index at %s unreadable, starting empty: %s", self.path, exc)
                return None
        vec_p, meta_p = (self.path / n for n in self.LEGACY)
        if vec_p.exists() and meta_p.exists():  # the earlier two-file layout
            try:
                meta = json.loads(meta_p.read_text(encoding="utf-8"))
                return list(meta.get("ids", [])), np.load(vec_p).astype(np.float32), meta
            except (OSError, ValueError) as exc:
                log.warning("vector index at %s unreadable, starting empty: %s", self.path, exc)
        return None

    def _load(self) -> None:
        """Replace the in-memory state with what is on disk (caller holds the file lock)."""
        self._state = file_state(self.file)
        self.stale = False
        ids, matrix, meta = [], np.zeros((0, self.dim), dtype=np.float32), {}
        disk = self._read_disk()
        if disk is not None:
            d_ids, d_matrix, meta = disk
            if int(meta.get("dim", self.dim)) != self.dim or d_matrix.shape != (len(d_ids), self.dim):
                log.warning("vector index at %s has a different shape, starting empty", self.path)
                self.stale = True
            else:
                ids, matrix = d_ids, d_matrix
                if self.embedder and meta.get("embedder") and meta["embedder"] != self.embedder:
                    self.stale = True
        self.generation = int(meta.get("generation", 0))
        self._disk_embedder = meta.get("embedder")
        self._ids, self._matrix = ids, matrix
        self._row = {k: i for i, k in enumerate(ids)}
        self._rebuild()

    def _reload_and_replay(self) -> None:
        ops, self._ops = self._ops, []
        self._load()
        for op in ops:
            self._apply(op)
        self._ops = ops

    def changed_on_disk(self) -> bool:
        return file_state(self.file) != self._state

    def refresh(self) -> bool:
        """Pick up saves made by other writers (keeping this writer's unsaved changes). Cheap when
        nothing changed: one ``stat``."""
        if not self.changed_on_disk():
            return False
        with self._flock:
            if not self.changed_on_disk():
                return False
            self._reload_and_replay()
            return True

    def save(self) -> None:
        with self._flock:
            if self.changed_on_disk():
                self._reload_and_replay()
            self.path.mkdir(parents=True, exist_ok=True)
            self.generation += 1
            # vectors of another embedding space stay marked as such until they are re-embedded
            embedder = self.embedder if not self.stale else (self._disk_embedder or "unknown")
            meta = {"dim": self.dim, "embedder": embedder, "count": len(self._ids), "generation": self.generation}
            buf = io.BytesIO()
            np.savez(buf, vectors=self._matrix, ids=np.array(self._ids, dtype=str),
                     meta=np.array(json.dumps(meta)))
            atomic_write_bytes(self.file, buf.getvalue())
            for name in self.LEGACY:  # migrated to the one-file layout
                try:
                    (self.path / name).unlink()
                except FileNotFoundError:
                    pass
            self._ops = []
            self._state = file_state(self.file)
            self._disk_embedder = embedder

    def _rebuild(self) -> None:
        if self._faiss is None:
            self._index = None
            return
        index = self._faiss.IndexIDMap2(self._faiss.IndexFlatIP(self.dim))
        if len(self._ids):
            index.add_with_ids(self._matrix, np.arange(len(self._ids), dtype=np.int64))
        self._index = index

    # ---- mutation --------------------------------------------------------------------------------
    def _prep(self, vectors: Iterable[Sequence[float]]) -> np.ndarray:
        arr = np.asarray(list(vectors), dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        if arr.size and arr.shape[1] != self.dim:
            raise ValueError(f"expected {self.dim}-d vectors, got {arr.shape[1]}-d")
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms

    def _apply(self, op: tuple) -> int:
        kind = op[0]
        if kind == "add":
            self._add_rows(op[1])
            return len(op[1])
        if kind == "delete":
            return self._delete_rows(op[1])
        self._ids, self._row = [], {}
        self._matrix = np.zeros((0, self.dim), dtype=np.float32)
        self._rebuild()
        self.stale = False  # nothing from another embedding space is left
        return 0

    def _add_rows(self, latest: dict[str, np.ndarray]) -> None:
        new_ids: list[str] = []
        for k, v in latest.items():
            if k in self._row:  # replace in place
                r = self._row[k]
                self._matrix[r] = v
                if self._index is not None:
                    self._index.remove_ids(np.array([r], dtype=np.int64))
                    self._index.add_with_ids(v.reshape(1, -1), np.array([r], dtype=np.int64))
            else:
                new_ids.append(k)
        if new_ids:
            start = len(self._ids)
            rows = np.arange(start, start + len(new_ids), dtype=np.int64)
            fresh = np.vstack([latest[k] for k in new_ids])
            self._ids.extend(new_ids)
            self._row.update({k: start + i for i, k in enumerate(new_ids)})
            self._matrix = np.vstack([self._matrix, fresh]) if len(self._matrix) else fresh.copy()
            if self._index is not None:
                self._index.add_with_ids(fresh, rows)

    def _delete_rows(self, ids: set[str]) -> int:
        gone = ids & self._row.keys()
        if not gone:
            return 0
        keep = [i for i, k in enumerate(self._ids) if k not in gone]
        self._ids = [self._ids[i] for i in keep]
        self._matrix = self._matrix[keep] if keep else np.zeros((0, self.dim), dtype=np.float32)
        self._row = {k: i for i, k in enumerate(self._ids)}
        self._rebuild()
        return len(gone)

    def _record(self, op: tuple) -> int:
        with self._lock:
            n = self._apply(op)
            self._ops.append(op)
            return n

    def add(self, ids: Sequence[str], vectors: Iterable[Sequence[float]]) -> None:
        """Insert or replace vectors under the given ids (persisted by ``save()``)."""
        ids = [str(i) for i in ids]
        arr = self._prep(vectors)
        if len(ids) != len(arr):
            raise ValueError(f"{len(ids)} ids for {len(arr)} vectors")
        if not ids:
            return
        self._record(("add", dict(zip(ids, arr))))  # a repeated id in one batch: last vector wins

    def delete(self, ids: Iterable[str]) -> int:
        """Remove ids; returns how many were present."""
        return self._record(("delete", {str(i) for i in ids}))

    def clear(self) -> None:
        self._record(("clear",))

    # ---- reads -----------------------------------------------------------------------------------
    def search(self, vector: Sequence[float], k: int = 10, *,
               allowed: Iterable[str] | None = None) -> list[tuple[str, float]]:
        """Top-k (id, cosine) pairs, best first. ``allowed`` restricts the candidate ids."""
        q = self._prep([vector])
        self.refresh()
        with self._lock:
            if not self._ids or k <= 0:
                return []
            rows = None
            if allowed is not None:
                rows = np.array(sorted({self._row[a] for a in map(str, allowed) if a in self._row}), dtype=np.int64)
                if not len(rows):
                    return []
            k = min(k, len(self._ids) if rows is None else len(rows))
            if self._index is not None:
                params = None
                if rows is not None:
                    params = self._faiss.SearchParameters(sel=self._faiss.IDSelectorBatch(rows))
                scores, found = self._index.search(q, k, params=params)
                pairs = [(int(r), float(s)) for r, s in zip(found[0], scores[0]) if r >= 0]
            else:
                cand = self._matrix if rows is None else self._matrix[rows]
                sims = cand @ q[0]
                top = np.argsort(-sims)[:k]
                pairs = [(int(rows[t]) if rows is not None else int(t), float(sims[t])) for t in top]
            return [(self._ids[r], s) for r, s in pairs]

    def get(self, id: str) -> list[float] | None:
        self.refresh()
        with self._lock:
            r = self._row.get(str(id))
            return None if r is None else self._matrix[r].tolist()

    def ids(self) -> list[str]:
        self.refresh()
        with self._lock:
            return list(self._ids)

    def __len__(self) -> int:
        self.refresh()
        return len(self._ids)

    def __contains__(self, id: object) -> bool:
        self.refresh()
        return str(id) in self._row

    @property
    def dirty(self) -> bool:
        """Unsaved changes are pending."""
        return bool(self._ops)

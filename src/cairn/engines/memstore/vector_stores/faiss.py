"""Local vector store: Cairn's shared faiss-backed ``VectorIndex`` plus a JSON payload store.

Adds what a plain faiss index lacks for memory: exact metadata filtering (the full operator
language, applied before the similarity search rather than after it), BM25 keyword search for
hybrid scoring, and crash-safe persistence under ``<path>/<collection>/``.

Several writers may share a collection directory — a long-running server, ``cairn remember`` in a
terminal, a git-hook sync, or two engine objects in one process. Every change is applied in memory
and journalled; saving takes ``<collection>/.lock`` (shared with the vector index inside), reloads
whatever other writers saved since this object last looked, replays the journal on top and replaces
the files atomically. Reads re-check the files (one ``stat`` each) and reload when another writer
saved. Nothing a different writer stored is ever overwritten by a stale copy.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import uuid
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from pydantic import BaseModel

from cairn.engines.vectors import VectorIndex, atomic_write_bytes, file_lock, file_state
from cairn.engines.memstore.vector_stores.base import VectorStoreBase
from cairn.engines.memstore.vector_stores.filtering import matches

logger = logging.getLogger(__name__)

_TOKEN = re.compile(r"[\w]+", re.UNICODE)
BM25_K1 = 1.2
BM25_B = 0.75
DOCSTORE = "docstore.json"


class OutputData(BaseModel):
    id: Optional[str]  # memory id
    score: Optional[float]  # similarity (higher is better)
    payload: Optional[Dict]  # metadata


def _tokens(text: str) -> List[str]:
    return _TOKEN.findall((text or "").lower())


class FAISS(VectorStoreBase):
    def __init__(
        self,
        collection_name: str,
        path: Optional[str] = None,
        distance_strategy: str = "cosine",
        normalize_L2: bool = True,
        embedding_model_dims: int = 384,
        embedder_id: Optional[str] = None,
    ):
        """
        Initialize the local vector store.

        Args:
            collection_name (str): Name of the collection.
            path (str, optional): Directory holding the collections. Defaults to $CAIRN_HOME/memstore/faiss.
            distance_strategy (str, optional): 'cosine' (default), 'inner_product' or 'euclidean'. Vectors are
                stored unit-length, so all three rank identically; the reported score differs.
            normalize_L2 (bool, optional): Kept for configuration compatibility; vectors are always normalised.
            embedding_model_dims (int, optional): Vector dimension. Defaults to 384.
            embedder_id (str, optional): Embedding space identity; a different space on disk marks ``stale``.
        """
        self.collection_name = collection_name
        if path is None:
            from cairn.engines.memstore.configs.base import engine_dir

            path = os.path.join(engine_dir(), "faiss")
        self.path = path
        self.distance_strategy = distance_strategy
        self.normalize_L2 = normalize_L2
        self.embedding_model_dims = embedding_model_dims
        self.embedder_id = embedder_id
        self._defer = 0
        self._pending = False
        self.index: Optional[VectorIndex] = None
        self.docstore: Dict[str, Dict] = {}
        self._doc_ops: List[tuple] = []          # unsaved payload changes (replayed over others' saves)
        self._doc_state: Optional[tuple] = None  # version of docstore.json last loaded or written
        self.create_col(collection_name)

    # ---- persistence -----------------------------------------------------------------------------
    @property
    def _dir(self) -> Path:
        return Path(self.path) / self.collection_name

    @property
    def _docstore_path(self) -> Path:
        return self._dir / DOCSTORE

    def _bind_lock(self) -> None:
        # one lock per collection directory, shared with the VectorIndex stored in it
        self._flock = file_lock(self._dir / ".lock")
        self._lock = self._flock._rlock

    def _load_docstore(self) -> None:
        p = self._docstore_path
        self._doc_state = file_state(p)
        if self._doc_state is None:
            self.docstore = {}
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            self.docstore = {str(k): dict(v) for k, v in (data.get("docstore") or {}).items()}
        except (OSError, ValueError, AttributeError) as e:
            logger.warning(f"Failed to load payloads for {self.collection_name}: {e}")
            self.docstore = {}

    def _apply_doc(self, op: tuple) -> None:
        if op[0] == "put":
            self.docstore[op[1]] = dict(op[2])
        elif op[0] == "del":
            self.docstore.pop(op[1], None)
        else:
            self.docstore = {}

    def _doc(self, op: tuple) -> None:
        self._apply_doc(op)
        self._doc_ops.append(op)

    def _changed_on_disk(self) -> bool:
        return self.index is not None and (
            file_state(self._docstore_path) != self._doc_state or self.index.changed_on_disk())

    def _reload_locked(self) -> None:
        """Merge what other writers saved into this object (caller holds the file lock)."""
        if file_state(self._docstore_path) != self._doc_state:
            self._load_docstore()
            for op in self._doc_ops:
                self._apply_doc(op)
        self.index.refresh()

    def _refresh(self) -> None:
        """Pick up other writers' saves before a read (one stat per file when nothing changed)."""
        if self._changed_on_disk():
            with self._flock:
                self._reload_locked()

    @contextmanager
    def deferred(self):
        """Write to disk once when the block ends instead of after every change (bulk loads)."""
        with self._lock:
            self._defer += 1
        try:
            yield self
        finally:
            with self._lock:
                self._defer -= 1
                if self._defer == 0 and self._pending:
                    self._save()

    def _save(self) -> None:
        with self._lock:
            if self.index is None:
                return
            if self._defer:
                self._pending = True
                return
            with self._flock:
                self._reload_locked()
                orphans = set(self.index.ids()) - set(self.docstore)
                if orphans:  # left by a crash between the two writes: unreachable without payloads
                    self.index.delete(orphans)
                self._dir.mkdir(parents=True, exist_ok=True)
                self.index.save()
                atomic_write_bytes(self._docstore_path,
                                   json.dumps({"docstore": self.docstore}, ensure_ascii=False).encode("utf-8"))
                self._doc_ops = []
                self._doc_state = file_state(self._docstore_path)
                self._pending = False

    @property
    def stale(self) -> bool:
        """True when the stored vectors belong to a different embedding space than ``embedder_id``."""
        return bool(self.index is not None and self.index.stale)

    def reindex(self, embed_batch) -> int:
        """Re-embed every stored record from its payload text (``data``) with ``embed_batch(texts)``,
        e.g. after the embedding model changed. Returns how many records were re-embedded."""
        if self.index is None:
            return 0
        with self._flock:
            self._reload_locked()
            ids = [vid for vid, p in self.docstore.items() if isinstance(p.get("data"), str)]
            self.index.clear()
            for start in range(0, len(ids), 256):
                chunk = ids[start:start + 256]
                vecs = embed_batch([self.docstore[vid]["data"] for vid in chunk])
                self.index.add(chunk, np.asarray(vecs, dtype=np.float32))
            self.index.stale = False
            self._save()
            return len(ids)

    # ---- scores ----------------------------------------------------------------------------------
    def _score(self, cosine: float) -> float:
        strategy = self.distance_strategy.lower()
        if strategy == "euclidean":
            distance = math.sqrt(max(0.0, 2.0 - 2.0 * cosine))  # unit vectors
            return 1.0 / (1.0 + distance)
        return cosine

    def _allowed(self, filters: Optional[Dict]) -> Optional[List[str]]:
        if not filters:
            return None
        return [vid for vid, payload in self.docstore.items() if matches(payload, filters)]

    # ---- collection ------------------------------------------------------------------------------
    def create_col(self, name: str, distance: str = None):
        """
        Create (or open) a collection.

        Args:
            name (str): Name of the collection.
            distance (str, optional): Distance metric override. Defaults to None.

        Returns:
            self: The store.
        """
        if distance:
            self.distance_strategy = distance
        self.collection_name = name
        self._bind_lock()
        with self._flock:
            self.index = VectorIndex(self._dir, dim=self.embedding_model_dims, embedder=self.embedder_id)
            self._load_docstore()
            self._doc_ops = []
        return self

    def insert(
        self,
        vectors: List[list],
        payloads: Optional[List[Dict]] = None,
        ids: Optional[List[str]] = None,
    ):
        """
        Insert vectors into a collection.

        Args:
            vectors (List[list]): List of vectors to insert.
            payloads (Optional[List[Dict]], optional): List of payloads corresponding to vectors. Defaults to None.
            ids (Optional[List[str]], optional): List of IDs corresponding to vectors. Defaults to None.
        """
        if self.index is None:
            raise ValueError("Collection not initialized. Call create_col first.")
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in range(len(vectors))]
        if payloads is None:
            payloads = [{} for _ in range(len(vectors))]
        if len(vectors) != len(ids) or len(vectors) != len(payloads):
            raise ValueError("Vectors, payloads, and IDs must have the same length")
        with self._lock:
            self.index.add([str(i) for i in ids], np.asarray(vectors, dtype=np.float32))
            for vector_id, payload in zip(ids, payloads):
                self._doc(("put", str(vector_id), dict(payload or {})))
            self._save()
        logger.debug(f"Inserted {len(vectors)} vectors into collection {self.collection_name}")

    def search(
        self, query: str, vectors: List[list], top_k: int = 5, filters: Optional[Dict] = None
    ) -> List[OutputData]:
        """
        Search for similar vectors.

        Args:
            query (str): Query (not used, kept for API compatibility).
            vectors (List[list]): Query vector.
            top_k (int, optional): Number of results to return. Defaults to 5.
            filters (Optional[Dict], optional): Filters to apply to the search. Defaults to None.

        Returns:
            List[OutputData]: Search results, best first.
        """
        if self.index is None:
            raise ValueError("Collection not initialized. Call create_col first.")
        vec = np.asarray(vectors, dtype=np.float32)
        if vec.ndim > 1:
            vec = vec[0]
        self._refresh()
        with self._lock:
            allowed = self._allowed(filters)
            if allowed is not None and not allowed:
                return []
            hits = self.index.search(vec, top_k, allowed=allowed)
            return [OutputData(id=vid, score=self._score(score), payload=dict(self.docstore.get(vid, {})))
                    for vid, score in hits if vid in self.docstore]

    def keyword_search(self, query: str, top_k: int = 5, filters: dict = None):
        """Okapi BM25 over the stored (lemmatised) memory text, restricted by ``filters``."""
        terms = _tokens(query)
        if not terms:
            return []
        self._refresh()
        with self._lock:
            docs = [(vid, p) for vid, p in self.docstore.items() if not filters or matches(p, filters)]
            if not docs:
                return []
            tokenised = [(vid, p, _tokens(p.get("text_lemmatized") or p.get("data") or "")) for vid, p in docs]
            n = len(tokenised)
            avgdl = sum(len(t) for _, _, t in tokenised) / n or 1.0
            df = Counter()
            for _, _, toks in tokenised:
                df.update(set(toks))
            scored = []
            for vid, payload, toks in tokenised:
                if not toks:
                    continue
                tf = Counter(toks)
                score = 0.0
                for term in set(terms):
                    f = tf.get(term, 0)
                    if not f:
                        continue
                    idf = math.log((n - df[term] + 0.5) / (df[term] + 0.5) + 1.0)
                    score += idf * f * (BM25_K1 + 1) / (f + BM25_K1 * (1 - BM25_B + BM25_B * len(toks) / avgdl))
                if score > 0:
                    scored.append(OutputData(id=vid, score=score, payload=dict(payload)))
            scored.sort(key=lambda o: o.score, reverse=True)
            return scored[:top_k]

    def delete(self, vector_id: str):
        """
        Delete a vector by ID.

        Args:
            vector_id (str): ID of the vector to delete.
        """
        if self.index is None:
            raise ValueError("Collection not initialized. Call create_col first.")
        self._refresh()
        with self._lock:
            vid = str(vector_id)
            if vid not in self.docstore:
                logger.warning(f"Vector {vector_id} not found in collection {self.collection_name}")
                return
            self.index.delete([vid])
            self._doc(("del", vid))
            self._save()

    def update(
        self,
        vector_id: str,
        vector: Optional[List[float]] = None,
        payload: Optional[Dict] = None,
    ):
        """
        Update a vector and its payload.

        Args:
            vector_id (str): ID of the vector to update.
            vector (Optional[List[float]], optional): Updated vector. Defaults to None.
            payload (Optional[Dict], optional): Updated payload. Defaults to None.
        """
        if self.index is None:
            raise ValueError("Collection not initialized. Call create_col first.")
        self._refresh()
        with self._lock:
            vid = str(vector_id)
            if vid not in self.docstore:
                raise ValueError(f"Vector {vector_id} not found")
            if payload is not None:
                self._doc(("put", vid, dict(payload)))
            if vector is not None:
                self.index.add([vid], np.asarray([vector], dtype=np.float32))
            self._save()

    def get(self, vector_id: str) -> Optional[OutputData]:
        """
        Retrieve a vector by ID.

        Args:
            vector_id (str): ID of the vector to retrieve.

        Returns:
            OutputData: Retrieved vector, or None.
        """
        if self.index is None:
            raise ValueError("Collection not initialized. Call create_col first.")
        self._refresh()
        with self._lock:
            payload = self.docstore.get(str(vector_id))
            if payload is None:
                return None
            return OutputData(id=str(vector_id), score=None, payload=dict(payload))

    def list_cols(self) -> List[str]:
        """
        List all collections.

        Returns:
            List[str]: List of collection names.
        """
        root = Path(self.path)
        if not root.exists():
            return [self.collection_name] if self.index is not None else []
        return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / DOCSTORE).exists()) or (
            [self.collection_name] if self.index is not None else [])

    def delete_col(self):
        """
        Delete a collection.
        """
        with self._flock:
            for name in (VectorIndex.FILE, *VectorIndex.LEGACY, DOCSTORE):
                try:
                    (self._dir / name).unlink()
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.warning(f"Failed to delete {name} of {self.collection_name}: {e}")
            self.index = None
            self.docstore = {}
            self._doc_ops = []
            self._doc_state = None

    def col_info(self) -> Dict:
        """
        Get information about a collection.

        Returns:
            Dict: Collection information.
        """
        if self.index is None:
            return {"name": self.collection_name, "count": 0}
        self._refresh()
        return {
            "name": self.collection_name,
            "count": len(self.docstore),
            "dimension": self.embedding_model_dims,
            "distance": self.distance_strategy,
            "backend": self.index.backend,
            "embedder": self.embedder_id,
        }

    def list(self, filters: Optional[Dict] = None, top_k: int = 100) -> List[List[OutputData]]:
        """
        List vectors in a collection, newest first.

        Args:
            filters (Optional[Dict], optional): Filters to apply to the list. Defaults to None.
            top_k (int, optional): Number of vectors to return. Defaults to 100.

        Returns:
            List[List[OutputData]]: One list of matching records.
        """
        if self.index is None:
            return [[]]
        self._refresh()
        with self._lock:
            rows = [(vid, p) for vid, p in self.docstore.items() if not filters or matches(p, filters)]
        rows.sort(key=lambda r: str(r[1].get("created_at") or ""), reverse=True)
        limit = len(rows) if top_k is None else top_k
        return [[OutputData(id=vid, score=None, payload=dict(p)) for vid, p in rows[:limit]]]

    def reset(self):
        """Reset the index by deleting and recreating it."""
        logger.warning(f"Resetting index {self.collection_name}...")
        with self._flock:
            self.delete_col()
            self.create_col(self.collection_name)

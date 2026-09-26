"""Semantic index for recall: every observation field (narrative, text, each fact), every summary
field and every prompt is embedded locally and kept in ``<repo>/.cairn/recall/vectors``.

Document ids follow the entity: ``obs_<id>_narrative``, ``obs_<id>_fact_<n>``, ``summary_<id>_learned``,
``prompt_<id>``. ``vector_docs`` in sessions.db holds each document's metadata (type, project, platform,
time) so queries filter before ranking, and results collapse back to one hit per entity.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from .platforms import normalize_platform_source
from .store import Store, parse_list

log = logging.getLogger("cairn.recall")

BATCH = 64
_indexes: dict[str, Any] = {}
_lock = threading.RLock()


def index_dir(root: Path | str) -> Path:
    return Path(root) / ".cairn" / "recall" / "vectors"


def _vectors():
    try:
        from .. import (
            vectors,
        )
        return vectors
    except Exception as exc:  # noqa: BLE001 - semantic search is optional
        log.warning("vector search unavailable: %s", exc)
        return None


def observation_docs(obs: dict) -> list[tuple[str, str, dict]]:
    base = {"doc_type": "observation", "sqlite_id": obs["id"], "project": obs.get("project"),
            "merged_into_project": obs.get("merged_into_project"),
            "platform_source": normalize_platform_source(obs.get("platform_source")),
            "created_at_epoch": obs.get("created_at_epoch")}
    docs = []
    if obs.get("narrative"):
        docs.append((f"obs_{obs['id']}_narrative", obs["narrative"], {**base, "field_type": "narrative"}))
    if obs.get("text"):
        docs.append((f"obs_{obs['id']}_text", obs["text"], {**base, "field_type": "text"}))
    for i, fact in enumerate(parse_list(obs.get("facts"))):
        docs.append((f"obs_{obs['id']}_fact_{i}", fact, {**base, "field_type": "fact"}))
    if not docs and obs.get("title"):  # a record with only a title is still findable
        docs.append((f"obs_{obs['id']}_title", obs["title"], {**base, "field_type": "title"}))
    return docs


def summary_docs(s: dict) -> list[tuple[str, str, dict]]:
    base = {"doc_type": "session_summary", "sqlite_id": s["id"], "project": s.get("project"),
            "merged_into_project": s.get("merged_into_project"),
            "platform_source": normalize_platform_source(s.get("platform_source")),
            "created_at_epoch": s.get("created_at_epoch")}
    return [(f"summary_{s['id']}_{f}", s[f], {**base, "field_type": f})
            for f in ("request", "investigated", "learned", "completed", "next_steps", "notes") if s.get(f)]


def prompt_doc(p: dict) -> tuple[str, str, dict]:
    return (f"prompt_{p['id']}", p["prompt_text"], {
        "doc_type": "user_prompt", "sqlite_id": p["id"], "project": p.get("project"), "merged_into_project": None,
        "platform_source": normalize_platform_source(p.get("platform_source")), "created_at_epoch": p.get("created_at_epoch"),
        "field_type": "prompt"})


class VectorSync:
    """The recall semantic index of one repository (thread-safe; one index object per process)."""

    def __init__(self, root: Path | str, store: Store | None = None):
        self.root = Path(root)
        self.store = store or Store.open(self.root)
        self._v = _vectors()

    @property
    def available(self) -> bool:
        return self._v is not None

    def index(self):
        if self._v is None:
            return None
        key = str(index_dir(self.root))
        with _lock:
            idx = _indexes.get(key)
            if idx is None:
                idx = self._v.VectorIndex(key, dim=self._v.DIM, embedder=self._v.embedder_id())
                _indexes[key] = idx
            return idx

    # ---- writes --------------------------------------------------------------------------------
    def _add(self, docs: list[tuple[str, str, dict]]) -> int:
        idx = self.index()
        if idx is None or not docs:
            return 0
        written = 0
        for i in range(0, len(docs), BATCH):
            chunk = docs[i:i + BATCH]
            try:
                vecs = self._v.embed([d[1] for d in chunk])
            except Exception as exc:  # noqa: BLE001 - a failed embed leaves rows for the next backfill
                log.warning("embedding failed, will retry on backfill: %s", exc)
                continue
            with _lock:
                idx.add([d[0] for d in chunk], vecs)
            self.store.db.executemany(
                "INSERT OR REPLACE INTO vector_docs(doc_id, doc_type, sqlite_id, field_type, project, merged_into_project,"
                " platform_source, created_at_epoch, embedder) VALUES(?,?,?,?,?,?,?,?,?)",
                [(d[0], d[2]["doc_type"], d[2]["sqlite_id"], d[2].get("field_type"), d[2].get("project"),
                  d[2].get("merged_into_project"), d[2].get("platform_source"), d[2].get("created_at_epoch"),
                  getattr(idx, "embedder", None)) for d in chunk])
            written += len(chunk)
        with _lock:
            idx.save()
        return written

    def _observation_rows(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return [dict(r) for r in self.store.db.execute(
            "SELECT o.*, COALESCE(s.platform_source, 'claude') AS platform_source FROM observations o LEFT JOIN"
            f" sdk_sessions s ON s.memory_session_id = o.memory_session_id WHERE o.id IN ({marks})", ids)]

    def _summary_rows(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return [dict(r) for r in self.store.db.execute(
            "SELECT ss.*, COALESCE(s.platform_source, 'claude') AS platform_source FROM session_summaries ss LEFT JOIN"
            f" sdk_sessions s ON s.memory_session_id = ss.memory_session_id WHERE ss.id IN ({marks})", ids)]

    def _prompt_rows(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return [dict(r) for r in self.store.db.execute(
            "SELECT up.*, s.project, COALESCE(s.platform_source, 'claude') AS platform_source FROM user_prompts up JOIN"
            f" sdk_sessions s ON up.session_db_id = s.id WHERE up.id IN ({marks}) AND length(trim(up.prompt_text)) > 0",
            ids)]

    def sync_observations(self, ids: list[int]) -> int:
        self.delete("observation", ids)
        return self._add([d for o in self._observation_rows(ids) for d in observation_docs(o)])

    def sync_summaries(self, ids: list[int]) -> int:
        self.delete("session_summary", ids)
        return self._add([d for s in self._summary_rows(ids) for d in summary_docs(s)])

    def sync_prompts(self, ids: list[int]) -> int:
        return self._add([prompt_doc(p) for p in self._prompt_rows(ids)])

    def delete(self, doc_type: str, ids: list[int]) -> int:
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        doc_ids = [r[0] for r in self.store.db.execute(
            f"SELECT doc_id FROM vector_docs WHERE doc_type=? AND sqlite_id IN ({marks})", (doc_type, *ids))]
        idx = self.index()
        if idx is not None and doc_ids:
            with _lock:
                idx.delete(doc_ids)
                idx.save()
        self.store.db.execute(f"DELETE FROM vector_docs WHERE doc_type=? AND sqlite_id IN ({marks})", (doc_type, *ids))
        return len(doc_ids)

    def backfill(self, limit: int = 2000) -> dict:
        """Embed whatever is not indexed yet (and everything, when the embedding model changed)."""
        idx = self.index()
        if idx is None:
            return {"available": False}
        if idx.stale or (len(idx) == 0 and self.store.db.execute("SELECT 1 FROM vector_docs LIMIT 1").fetchone()):
            with _lock:
                idx.clear()
                idx.stale = False
            self.store.db.execute("DELETE FROM vector_docs")
        def missing(table: str, doc_type: str, extra: str = "") -> list[int]:
            return [r[0] for r in self.store.db.execute(
                f"SELECT t.id FROM {table} t WHERE NOT EXISTS (SELECT 1 FROM vector_docs v WHERE v.doc_type=? AND"
                f" v.sqlite_id = t.id){extra} ORDER BY t.id LIMIT ?", (doc_type, limit))]
        obs = missing("observations", "observation")
        sums = missing("session_summaries", "session_summary")
        prompts = missing("user_prompts", "user_prompt", " AND length(trim(t.prompt_text)) > 0")
        return {"available": True, "observations": self._add([d for o in self._observation_rows(obs)
                                                              for d in observation_docs(o)]),
                "summaries": self._add([d for s in self._summary_rows(sums) for d in summary_docs(s)]),
                "prompts": self._add([prompt_doc(p) for p in self._prompt_rows(prompts)])}

    # ---- reads ---------------------------------------------------------------------------------
    def query(self, text: str, limit: int = 100, *, doc_types: list[str] | None = None, project: str | None = None,
              platform_source: str | None = None, start_epoch: int | None = None,
              end_epoch: int | None = None) -> dict:
        """Nearest entities: ``{"ids": [...], "distances": [...], "metadatas": [...]}`` (one per entity)."""
        idx = self.index()
        empty = {"ids": [], "distances": [], "metadatas": []}
        if idx is None or len(idx) == 0 or not (text or "").strip():
            return empty
        cond, args = [], []
        if doc_types:
            cond.append(f"doc_type IN ({','.join('?' * len(doc_types))})")
            args += doc_types
        if project:
            cond.append("(project = ? OR merged_into_project = ?)")
            args += [project, project]
        if platform_source:
            cond.append("platform_source = ?")
            args.append(normalize_platform_source(platform_source))
        if start_epoch is not None:
            cond.append("created_at_epoch >= ?")
            args.append(start_epoch)
        if end_epoch is not None:
            cond.append("created_at_epoch <= ?")
            args.append(end_epoch)
        where = f"WHERE {' AND '.join(cond)}" if cond else ""
        rows = {r["doc_id"]: dict(r) for r in self.store.db.execute(f"SELECT * FROM vector_docs {where}", args)}
        if not rows:
            return empty
        try:
            vec = self._v.embed([text])[0]
        except Exception as exc:  # noqa: BLE001
            log.warning("query embedding failed: %s", exc)
            return empty
        with _lock:
            hits = idx.search(vec, k=min(len(rows), max(limit * 4, limit)), allowed=list(rows))
        out = dict(empty)
        seen = set()
        for doc_id, score in hits:
            meta = rows.get(doc_id)
            if not meta:
                continue
            key = (meta["doc_type"], meta["sqlite_id"])
            if key in seen:
                continue
            seen.add(key)
            out["ids"].append(meta["sqlite_id"])
            out["distances"].append(round(1.0 - float(score), 6))
            out["metadatas"].append(meta)
            if len(out["ids"]) >= limit:
                break
        return out

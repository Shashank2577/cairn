"""Memory layer: durable knowledge — conventions, decisions, gotchas, preferences, facts.

Base tier: SQLite FTS in the read model (instant, no key).
Deep tier: a semantic memory engine (LLM-backed add/update/dedupe + local embeddings). Every
semantic write is mirrored into the read model so all surfaces stay uniform.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ..project import Project
from ..router import Router
from ..store import Brain

KINDS = ("convention", "decision", "gotcha", "preference", "fact")
log = logging.getLogger("cairn.memory")


class SemanticMemory:
    """Adapter around the semantic memory engine. Lazily constructed; failures never propagate."""

    EMBED_MODEL = "BAAI/bge-small-en-v1.5"  # 384-d, ~70 MB ONNX, runs locally

    def __init__(self, project: Project, router: Router):
        self.project, self.router = project, router
        self._mem = None
        self.error: str | None = None

    @property
    def ready(self) -> bool:
        return self._engine() is not None

    def _engine(self):
        if self._mem is not None or self.error:
            return self._mem
        if not self.router.deep_enabled():
            self.error = "no model key"
            return None
        try:
            from mem0 import Memory  # noqa: WPS433 (optional dependency)

            store = self.project.dir / "memory"
            store.mkdir(parents=True, exist_ok=True)
            llm = ({"provider": "anthropic", "config": {"model": self.router.model("balanced"),
                                                         "api_key": self.router.api_key, "max_tokens": 1500}}
                   if self.router.provider == "anthropic" else
                   {"provider": "openai", "config": {"model": self.router.model("balanced"), "api_key": self.router.api_key,
                                                      "openai_base_url": self.router.base_url}})
            self._mem = Memory.from_config({
                "llm": llm,
                "embedder": {"provider": "fastembed", "config": {"model": self.EMBED_MODEL, "embedding_dims": 384}},
                "vector_store": {"provider": "faiss", "config": {"collection_name": self.project.id, "path": str(store),
                                                                  "embedding_model_dims": 384,
                                                                  "distance_strategy": "cosine"}},
                "history_db_path": str(store / "history.db"),
            })
        except Exception as exc:  # optional engine: any failure degrades to base tier
            self.error = f"{type(exc).__name__}: {exc}"[:200]
            log.info("semantic memory unavailable: %s", self.error)
        return self._mem

    def add(self, text: str, kind: str) -> list[dict]:
        mem = self._engine()
        if mem is None:
            return []
        try:
            res = mem.add(text, user_id=self.project.id, metadata={"kind": kind})
            return res.get("results", []) if isinstance(res, dict) else list(res or [])
        except Exception as exc:
            log.info("semantic add failed: %s", exc)
            return []

    def search(self, query: str, limit: int = 8) -> list[dict]:
        mem = self._engine()
        if mem is None:
            return []
        try:
            res = mem.search(query, user_id=self.project.id, limit=limit)
            return res.get("results", []) if isinstance(res, dict) else list(res or [])
        except Exception as exc:
            log.info("semantic search failed: %s", exc)
            return []

    def history(self, engine_id: str) -> list[dict]:
        mem = self._engine()
        try:
            return mem.history(engine_id) if mem else []
        except Exception:
            return []


class MemoryStore:
    def __init__(self, project: Project, brain: Brain, router: Router | None = None):
        self.project, self.brain = project, brain
        self.router = router or Router(project, brain)
        self.semantic = SemanticMemory(project, self.router)

    def remember(self, text: str, kind: str = "fact", supersedes: str | None = None, source: str = "user",
                 provenance: str = "EXTRACTED", confidence: float = 1.0, link_symbols=None) -> dict:
        text = " ".join(text.split())
        if not text:
            raise ValueError("empty memory")
        kind = kind if kind in KINDS else "fact"
        if supersedes and not self.brain.memory(supersedes):
            raise ValueError(f"unknown memory id {supersedes}")
        # exact duplicate guard (cheap, deterministic)
        dup = self.brain.one("SELECT id FROM memories WHERE text=? AND forgotten=0 AND superseded_by IS NULL", (text,))
        if dup:
            return {"id": dup["id"], "status": "exists"}
        engine_ref = None
        status = "stored"
        if self.router.deep_enabled():
            results = self.semantic.add(f"[{kind}] {text}", kind)
            for r in results:
                ev = (r.get("event") or "").upper()
                if ev == "UPDATE" and r.get("id"):
                    old = self.brain.one("SELECT id FROM memories WHERE engine_ref=? AND superseded_by IS NULL",
                                         (r["id"],))
                    if old and not supersedes:
                        supersedes, status = old["id"], "updated"
                if r.get("id"):
                    engine_ref = r["id"]
        mid = self.brain.add_memory(text, kind=kind, source=source, provenance=provenance, confidence=confidence,
                                    supersedes=supersedes, engine_ref=engine_ref)
        if link_symbols:
            link_symbols(mid, text)
        return {"id": mid, "status": "superseded " + supersedes if supersedes and status == "stored" else status}

    def recall(self, query: str, limit: int = 8) -> list[dict]:
        hits = self.brain.search(query, kinds=["memory"], limit=limit * 2)
        ids = [h["id"].split(":", 1)[1] for h in hits]
        if self.router.deep_enabled():
            for r in self.semantic.search(query, limit=limit):
                row = self.brain.one("SELECT id FROM memories WHERE engine_ref=?", (r.get("id"),))
                if row and row["id"] not in ids:
                    ids.insert(0, row["id"])
        out = []
        for mid in ids:
            m = self.brain.memory(mid)
            if m and not m["forgotten"] and not m["superseded_by"]:
                out.append(m)
        return out[:limit]

    def about(self, labels: list[str], paths: list[str], limit: int = 6) -> list[dict]:
        """Memories linked to (or mentioning) any of these symbols/files."""
        ids = {l["src"].split(":", 1)[1] for l in self.brain.links_to(
            [f"file:{p}" for p in paths] + [f"label:{x.lower()}" for x in labels], rels=("mentions",), limit=200)
            if l["src"].startswith("memory:")}
        words = [w for w in labels if len(w) >= 4][:6] + [Path(p).stem for p in paths[:4] if len(Path(p).stem) >= 4]
        for w in words:
            for m in self.recall(w, limit=4):
                if re.search(rf"\b{re.escape(w)}\b", m["text"], re.I):
                    ids.add(m["id"])
        out = [m for i in ids if (m := self.brain.memory(i)) and not m["forgotten"] and not m["superseded_by"]]
        order = {k: i for i, k in enumerate(KINDS)}
        out.sort(key=lambda m: (order.get(m["kind"], 9), -m["created_at"]))
        return out[:limit]

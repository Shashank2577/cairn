"""Timeline layer (deep tier): a temporal fact graph built from episodes.

Episodes are built deterministically from the read model — one per *day* of commits (≈20× fewer
extraction calls than per-commit), plus spec changes, session summaries and decisions. Facts carry
validity windows ("true from March until the May refactor") and are mirrored into the read model.

Runs with an embedded store by default (no Docker) or a graph server via ``deep.graph_url``.
Embeddings run locally (ONNX) so no embeddings API is required.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

from ..project import Project
from ..router import Budget, BudgetExceeded, Router, estimate_tokens
from ..store import Brain

log = logging.getLogger("cairn.chronicle")
EPISODE_OVERHEAD = 3500   # extraction + dedupe prompts per episode (observed)
EPISODE_MULT = 6          # body tokens are read several times across extraction passes


def _local_embedder():
    from graphiti_core.embedder.client import EmbedderClient

    class LocalEmbedder(EmbedderClient):
        """Local ONNX embeddings — no embeddings API needed."""

        def __init__(self, model: str = "BAAI/bge-small-en-v1.5"):
            from fastembed import TextEmbedding
            self._m = TextEmbedding(model_name=model)

        async def create(self, input_data):
            text = input_data if isinstance(input_data, str) else " ".join(map(str, input_data))
            vec = await asyncio.to_thread(lambda: next(iter(self._m.embed([text]))))
            return [float(x) for x in vec]

        async def create_batch(self, input_data_list):
            vecs = await asyncio.to_thread(lambda: list(self._m.embed(list(input_data_list))))
            return [[float(x) for x in v] for v in vecs]

    return LocalEmbedder()


def _lexical_reranker():
    from graphiti_core.cross_encoder.client import CrossEncoderClient

    class LexicalReranker(CrossEncoderClient):
        """Dependency-free reranker: token overlap with length normalisation."""

        async def rank(self, query, passages):
            q = set(re.findall(r"\w+", query.lower()))
            scored = []
            for p in passages:
                words = re.findall(r"\w+", p.lower())
                overlap = sum(1 for w in words if w in q)
                scored.append((p, overlap / (1 + len(words) ** 0.5)))
            return sorted(scored, key=lambda x: -x[1])

    return LexicalReranker()


class Chronicle:
    def __init__(self, project: Project, brain: Brain, router: Router):
        self.project, self.brain, self.router = project, brain, router
        self._g = None
        self.error: str | None = None

    # ---- engine ---------------------------------------------------------------------------------
    async def engine(self):
        if self._g is not None or self.error:
            return self._g
        if not self.router.deep_enabled():
            self.error = "no model key"
            return None
        try:
            from graphiti_core import Graphiti
            from graphiti_core.llm_client.config import LLMConfig

            if self.router.provider == "anthropic":
                from graphiti_core.llm_client.anthropic_client import AnthropicClient
                llm = AnthropicClient(LLMConfig(api_key=self.router.api_key, model=self.router.model("balanced"),
                                                small_model=self.router.model("fast")))
            else:
                from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
                llm = OpenAIGenericClient(LLMConfig(api_key=self.router.api_key, base_url=self.router.base_url,
                                                    model=self.router.model("balanced"),
                                                    small_model=self.router.model("fast")))
            self._g = Graphiti(graph_driver=self._driver(), llm_client=llm, embedder=_local_embedder(),
                               cross_encoder=_lexical_reranker(), max_coroutines=4)
            if self.brain.get_kv("chronicle.indices") != "1":
                await self._g.build_indices_and_constraints()
                self.brain.set_kv("chronicle.indices", "1")
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"[:240]
            log.info("timeline engine unavailable: %s", self.error)
            self._g = None
        return self._g

    def _driver(self):
        url = str(self.project.cfg("deep.graph_url", "") or "")
        if url.startswith(("falkor://", "redis://")):
            from graphiti_core.driver.falkordb_driver import FalkorDriver
            hostport = url.split("://", 1)[1]
            host, _, port = hostport.partition(":")
            return FalkorDriver(host=host or "localhost", port=int(port or 6379), database=self.project.id)
        if url.startswith(("bolt://", "neo4j://", "neo4j+s://")):
            import os
            from graphiti_core.driver.neo4j_driver import Neo4jDriver
            return Neo4jDriver(url, os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", ""))
        import warnings
        from graphiti_core.driver.kuzu_driver import KuzuDriver
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return KuzuDriver(db=str(self.project.dir / "timeline.kuzu"))

    # ---- episodes -------------------------------------------------------------------------------
    def pending_episodes(self, limit: int = 60) -> list[dict]:
        """Deterministic episode construction from the read model since the cursor."""
        since = float(self.brain.get_kv("chronicle.cursor", "0") or 0)
        days: dict[str, list[dict]] = defaultdict(list)
        for ev in reversed(self.brain.events(kinds=["commit", "spec", "session", "memory"], since=since + 1e-6,
                                             limit=5000)):
            day = datetime.fromtimestamp(ev["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
            days[day].append(ev)
        episodes = []
        for day in sorted(days)[:limit]:
            evs = days[day]
            lines = []
            for ev in evs:
                if ev["kind"] == "commit":
                    risk = f" [{', '.join(ev['meta'].get('risk', []))}]" if ev["meta"].get("risk") else ""
                    files = [r[5:] for r in ev["refs"] if r.startswith("file:")][:8]
                    lines.append(f"- commit {ev['meta'].get('sha', '')[:8]} by {ev['actor']}{risk}: {ev['title']}"
                                 + (f" (files: {', '.join(files)})" if files else ""))
                elif ev["kind"] == "memory":
                    lines.append(f"- team {ev['meta'].get('kind', 'note')}: {ev['body'][:400]}")
                elif ev["kind"] == "session":
                    lines.append(f"- agent session: {ev['title']} — {ev['body'][:300]}")
                else:
                    lines.append(f"- spec progress: {ev['title']}")
            body = f"Engineering activity in repository {self.project.name} on {day}:\n" + "\n".join(lines[:80])
            episodes.append({"name": f"{self.project.id}-{day}", "body": body, "ts": max(e["ts"] for e in evs),
                             "tokens": estimate_tokens(body) * EPISODE_MULT + EPISODE_OVERHEAD})
        return episodes

    async def ingest(self, budget: Budget, on_progress=None) -> dict:
        g = await self.engine()
        if g is None:
            return {"episodes": 0, "note": self.error or "unavailable"}
        from graphiti_core.nodes import EpisodeType

        done, facts = 0, 0
        for ep in self.pending_episodes():
            try:
                budget.check(ep["tokens"])
            except BudgetExceeded as exc:
                return {"episodes": done, "facts": facts, "note": f"budget reached ({exc}); continues next sync"}
            t0 = time.time()
            try:
                res = await g.add_episode(name=ep["name"], episode_body=ep["body"],
                                          source_description="engineering activity", source=EpisodeType.text,
                                          reference_time=datetime.fromtimestamp(ep["ts"], tz=timezone.utc),
                                          group_id=self.project.id)
            except Exception as exc:
                log.info("episode failed: %s", exc)
                return {"episodes": done, "facts": facts, "note": f"stopped: {type(exc).__name__}"}
            budget.charge(ep["tokens"])
            self.brain.log_call("episode", "balanced", self.router.model("balanced"),
                                {"input": ep["tokens"], "output": 0}, True)
            facts += self._mirror(getattr(res, "edges", []) or [])
            self.brain.set_kv("chronicle.cursor", str(ep["ts"]))
            done += 1
            if on_progress:
                on_progress(f"{ep['name']} → {facts} facts ({time.time() - t0:.0f}s)")
        return {"episodes": done, "facts": facts}

    def _mirror(self, edges: Iterable) -> int:
        ents, events = [], []
        for e in edges:
            uid = getattr(e, "uuid", None)
            fact = getattr(e, "fact", "")
            if not uid or not fact:
                continue
            valid = getattr(e, "valid_at", None)
            invalid = getattr(e, "invalid_at", None)
            meta = {"relation": getattr(e, "name", ""), "valid_at": valid.timestamp() if valid else None,
                    "invalid_at": invalid.timestamp() if invalid else None}
            ents.append((f"fact:{uid}", "fact", fact[:240], None, meta, "timeline", fact))
            events.append({"id": f"fact:{uid}", "ts": meta["valid_at"] or time.time(), "kind": "fact",
                           "title": fact[:200], "body": fact, "refs": [f"fact:{uid}"], "meta": meta,
                           "source": "timeline"})
        if ents:
            self.brain.put_entities(ents)
            self.brain.add_events(events)
        return len(ents)

    async def search(self, query: str, limit: int = 6) -> list[dict]:
        g = await self.engine()
        if g is None:
            return []
        try:
            edges = await g.search(query, group_ids=[self.project.id], num_results=limit)
        except Exception as exc:
            log.info("timeline search failed: %s", exc)
            return []
        return [{"id": f"fact:{e.uuid}", "fact": e.fact,
                 "valid_at": e.valid_at.timestamp() if e.valid_at else None,
                 "invalid_at": e.invalid_at.timestamp() if e.invalid_at else None} for e in edges]


def facts_matching(brain: Brain, words: list[str], limit: int = 5) -> list[dict]:
    """Deterministic lookup over mirrored facts (no model call at query time)."""
    if not words:
        return []
    hits = brain.search(" ".join(words), kinds=["fact"], limit=limit)
    out = []
    for h in hits:
        e = brain.entity(h["id"])
        if e:
            out.append({"id": h["id"], "fact": e["name"], **{k: e["meta"].get(k) for k in ("valid_at", "invalid_at")}})
    return out


def dumps(x) -> str:
    return json.dumps(x, default=str)

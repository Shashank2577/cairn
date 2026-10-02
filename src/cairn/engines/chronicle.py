"""Timeline layer (deep tier): the temporal fact graph built from the repository's own history.

Episodes are built deterministically from the read model — one per *day* of activity (commits,
spec progress, agent sessions, team decisions), ≈20× fewer extraction calls than per-commit — and
fed to the temporal engine (``cairn.engines.temporal``) under a token budget. Facts carry validity
windows ("true from March until the May refactor"); when a later day contradicts a fact, the old
one is invalidated, not deleted. Every fact is mirrored into the read model as a ``fact:<uuid>``
entity plus timeline events, so surfaces answer from the read model without touching the graph.

The graph store is embedded (``.cairn/temporal/``) unless ``temporal.url`` names a team server.
Embeddings run locally, so no embeddings API is required. Without a model (``router.available``
is False) ingestion is a clean no-op.
"""
from __future__ import annotations

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
SOURCE = "timeline"
SAGA_SUFFIX = "activity"
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


def _ts(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


class Chronicle:
    def __init__(self, project: Project, brain: Brain, router: Router):
        self.project, self.brain, self.router = project, brain, router
        self.error: str | None = None
        self._service = None

    # ---- engine ---------------------------------------------------------------------------------
    def service(self, budget: Budget | None = None):
        """The project's ``TemporalService`` (the temporal graph facade)."""
        from .temporal.service import TemporalService

        if self._service is None or budget is not None:
            self._service = TemporalService(self.project, self.router, self.brain, budget=budget)
        return self._service

    @property
    def group_id(self) -> str:
        return self.project.id

    @property
    def saga(self) -> str:
        return f"{self.project.id}-{SAGA_SUFFIX}"

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
            lines, refs = [], []
            for ev in evs:
                refs += [r for r in ev["refs"] if r.startswith(("commit:", "file:", "spec:", "task:", "memory:"))]
                if ev["kind"] == "commit" and str(ev["id"]).startswith("commit:"):
                    refs.append(ev["id"])
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
            episodes.append({"name": f"{self.project.id}-{day}", "day": day, "body": body,
                             "ts": max(e["ts"] for e in evs), "refs": sorted(set(refs)),
                             "tokens": estimate_tokens(body) * EPISODE_MULT + EPISODE_OVERHEAD})
        return episodes

    async def ingest(self, budget: Budget, on_progress=None, wall_seconds: float | None = None) -> dict:
        if not getattr(self.router, "available", False):
            return {"episodes": 0, "facts": 0, "skipped": True, "note": "needs a model"}
        pending = self.pending_episodes()
        if not pending:
            return {"episodes": 0, "facts": 0}
        from .temporal.ontology import ENGINEERING_ENTITY_TYPES, ENGINEERING_EXTRACTION_INSTRUCTIONS

        svc = self.service(budget)
        done, facts, ended = 0, 0, 0
        note = None
        started = time.time()
        try:
            async with svc.session() as engine:
                from .temporal.nodes import EpisodeType

                last = self.brain.get_kv("chronicle.last_episode") or None
                for ep in pending:
                    try:
                        budget.check(ep["tokens"])
                    except BudgetExceeded as exc:
                        note = f"budget reached ({exc}); continues next sync"
                        break
                    t0 = time.time()
                    try:
                        res = await engine.add_episode(
                            name=ep["name"], episode_body=ep["body"],
                            source_description="engineering activity", source=EpisodeType.text,
                            reference_time=datetime.fromtimestamp(ep["ts"], tz=timezone.utc),
                            group_id=self.group_id,
                            entity_types=svc.entity_types() or ENGINEERING_ENTITY_TYPES,
                            edge_types=svc.edge_types(), edge_type_map=svc.edge_type_map(),
                            custom_extraction_instructions=(svc.settings.custom_extraction_instructions
                                                            or ENGINEERING_EXTRACTION_INSTRUCTIONS),
                            # Only the previous day as context keeps each episode's prompts small.
                            previous_episode_uuids=[last] if last else [],
                            saga=self.saga, saga_previous_episode_uuid=last,
                        )
                    except BudgetExceeded as exc:
                        note = f"budget reached ({exc}); continues next sync"
                        break
                    except Exception as exc:
                        log.info("episode failed: %s", exc)
                        self.error = f"{type(exc).__name__}: {exc}"[:240]
                        note = f"stopped: {type(exc).__name__}"
                        break
                    new, invalidated = self._mirror(res.edges, ep)
                    facts += new
                    ended += invalidated
                    last = res.episode.uuid
                    self.brain.set_kv("chronicle.cursor", str(ep["ts"]))
                    self.brain.set_kv("chronicle.last_episode", last)
                    done += 1
                    if on_progress:
                        on_progress(f"{ep['name']} → {facts} facts ({time.time() - t0:.0f}s)")
                    # The cap is checked between episodes, so an in-flight day always completes and
                    # the cursor (set above) keeps the run resumable — never more than one day lost.
                    if wall_seconds is not None and time.time() - started > wall_seconds:
                        note = (f"wall clock ({wall_seconds:.0f}s) reached — the remaining "
                                f"{len(pending) - done} day(s) fill in on later syncs")
                        break
        except Exception as exc:  # store unavailable (locked, broken server config)
            self.error = f"{type(exc).__name__}: {exc}"[:240]
            log.info("timeline engine unavailable: %s", self.error)
            return {"episodes": done, "facts": facts, "note": f"unavailable: {self.error}", "error": self.error}
        out = {"episodes": done, "facts": facts, "invalidated": ended}
        if note:
            out["note"] = note
        if note and note.startswith("stopped"):  # a failure, not the budget: the sync shows it as failed
            out["error"] = f"stopped after {done} day{'s' if done != 1 else ''}: {self.error}"
        return out

    # ---- read model mirror ----------------------------------------------------------------------
    def _mirror(self, edges: Iterable, episode: dict | None = None) -> tuple[int, int]:
        """Upsert facts as ``fact:<uuid>`` entities and timeline events.

        Returns (facts mirrored, facts that are no longer true)."""
        ents, events, links = [], [], []
        commits = [r for r in (episode or {}).get("refs", []) if r.startswith("commit:")]
        ended = 0
        for e in edges:
            uid = getattr(e, "uuid", None)
            fact = getattr(e, "fact", "")
            if not uid or not fact:
                continue
            valid, invalid = _ts(getattr(e, "valid_at", None)), _ts(getattr(e, "invalid_at", None))
            expired, created = _ts(getattr(e, "expired_at", None)), _ts(getattr(e, "created_at", None))
            fid = f"fact:{uid}"
            meta = {"relation": getattr(e, "name", ""), "valid_at": valid, "invalid_at": invalid,
                    "expired_at": expired, "created_at": created,
                    "source_node": getattr(e, "source_node_uuid", None),
                    "target_node": getattr(e, "target_node_uuid", None),
                    "episodes": list(getattr(e, "episodes", []) or []), "group_id": getattr(e, "group_id", ""),
                    "current": invalid is None and expired is None}
            ents.append((fid, "fact", fact[:240], None, meta, SOURCE, fact))
            events.append({"id": fid, "ts": valid or created or time.time(), "kind": "fact",
                           "title": fact[:200], "body": fact, "refs": [fid], "meta": meta, "source": SOURCE})
            if invalid is not None or expired is not None:
                ended += 1
                events.append({"id": f"fact-end:{uid}", "ts": invalid or expired or time.time(), "kind": "fact_end",
                               "title": f"No longer true: {fact[:180]}", "body": fact, "refs": [fid],
                               "meta": meta, "source": SOURCE})
            for c in commits:  # facts that name a commit are linked to it
                sha = c.split(":", 1)[1]
                if any(sha.startswith(m) or m.startswith(sha[:7]) for m in _SHA.findall(fact.lower())):
                    links.append((fid, c, "derived_from", "INFERRED", 0.8, SOURCE))
        if ents:
            self.brain.put_entities(ents)
            self.brain.add_events(events)
        if links:
            self.brain.link(links)
        return len(ents), ended

    async def search(self, query: str, limit: int = 6) -> list[dict]:
        if not self.project.dir.joinpath("temporal").exists():
            return []
        try:
            facts = await self.service().search_facts(query, group_ids=[self.group_id], max_facts=limit)
        except Exception as exc:
            log.info("timeline search failed: %s", exc)
            return []
        return [{"id": f"fact:{f['uuid']}", "fact": f["fact"], "valid_at": _ts(f["valid_at"]),
                 "invalid_at": _ts(f["invalid_at"])} for f in facts]

    def resync_mirror(self) -> int:
        """Rebuild the read-model mirror from the graph (after a clear, or a store from a teammate)."""
        async def run():
            out, cursor = [], None
            while True:
                page = await self.service().list_facts(group_id=self.group_id, limit=500, uuid_cursor=cursor)
                if not page:
                    return out
                out += page
                cursor = page[-1]["uuid"]

        from .temporal.service import run_sync

        facts = run_sync(run())
        self.brain.drop_source(SOURCE, kinds=("fact",))

        class _F:  # minimal edge-like view over the JSON shape
            def __init__(self, d):
                self.__dict__.update(d)
                self.name = d.get("name", "")

        n, _ = self._mirror([_F(f) for f in facts])
        return n


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

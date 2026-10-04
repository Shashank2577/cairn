"""Memory layer: durable knowledge — conventions, decisions, gotchas, preferences, facts.

Two tiers, one surface:
  base   the read model (SQLite + FTS) with exact de-duplication — always on, no model, no network
  engine the self-reconciling memory engine (``engines/memstore``): local embeddings for semantic
         recall, a change history per memory and, when a model is available, ADD / UPDATE / DELETE /
         NONE reconciliation so a new memory updates or retires the ones it overlaps with

Every engine write is mirrored into the read model (``Brain.add_memory``) so all surfaces stay uniform;
the read model row keeps the engine id in ``engine_ref``.

``seed_from_repo`` fills memory from what every repository already has — decisions (ADRs), answered
clarifications in specs, conventions (contributing / style docs) and gotchas (reverts, explained fixes)
— deterministically on every sync, each memory citing its source so it updates or disappears with it.
"""
from __future__ import annotations

import hashlib
import json
import contextlib
import logging
import re
import sqlite3
import subprocess
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from ..project import Project
from ..router import Router
from ..store import Brain
from . import vectors
from .memstore.utils.text import _contained, _jaccard, _overlap, same_fact  # one definition, shared with the engine

KINDS = ("convention", "decision", "gotcha", "preference", "fact")
SCOPES = ("project", "team", "user", "session")
log = logging.getLogger("cairn.memory")


def _norm(text: str) -> str:
    return " ".join((text or "").split())


class SemanticMemory:
    """Adapter around the memory engine. Lazily constructed; failures never propagate to callers."""

    EMBED_MODEL = vectors.MODEL  # 384-d, ~70 MB ONNX, runs locally

    def __init__(self, project: Project, router: Router):
        self.project, self.router = project, router
        self._mem = None
        self._lock = threading.Lock()
        self.error: str | None = None

    @property
    def dir(self) -> Path:
        return self.project.dir / "memstore"

    @property
    def enabled(self) -> bool:
        flag = self.project.cfg("memory.engine", "auto")
        return not (flag is False or str(flag).lower() in ("off", "false", "no"))

    @property
    def ready(self) -> bool:
        return self._engine() is not None

    def reset(self) -> None:
        """Close the engine; the next use builds it again with the current settings and router."""
        with self._lock:
            mem, self._mem, self.error = self._mem, None, None
        if mem is not None and hasattr(mem, "close"):
            with contextlib.suppress(Exception):
                mem.close()

    @property
    def engine(self):
        """The engine (``memstore.Memory``) for this repository, or None when unavailable."""
        return self._engine()

    @property
    def can_reconcile(self) -> bool:
        """A model is available for ADD/UPDATE/DELETE/NONE decisions."""
        try:
            return bool(self.router.deep_enabled())
        except Exception:
            return False

    def config(self) -> dict:
        """Engine configuration for this repository (``[memory]`` in .cairn/config.toml)."""
        from .memstore.prompts import DEFAULT_UPDATE_MEMORY_PROMPT, ENGINEERING_FACT_EXTRACTION_PROMPT, \
            ENGINEERING_UPDATE_GUIDANCE

        store = self.project.cfg("memory.vector_store") or {}
        provider = store.get("provider", "faiss") if isinstance(store, dict) else "faiss"
        vcfg = dict(store.get("config") or {}) if isinstance(store, dict) else {}
        if provider == "faiss":
            vcfg.setdefault("path", str(self.dir))
            vcfg.setdefault("collection_name", "memories")
        cfg = {
            "vector_store": {"provider": provider, "config": vcfg},
            "history_db_path": str(self.dir / "history.db"),
            "llm": {"provider": "router", "config": {"task": "memory"}},
            "embedder": {"provider": "local", "config": {}},
            "add_mode": str(self.project.cfg("memory.add_mode", "reconcile")),
            "custom_fact_extraction_prompt": ENGINEERING_FACT_EXTRACTION_PROMPT,
            "custom_update_memory_prompt": DEFAULT_UPDATE_MEMORY_PROMPT + ENGINEERING_UPDATE_GUIDANCE,
            "custom_instructions": self.project.cfg("memory.instructions") or None,
        }
        if self.project.cfg("memory.rerank", False):
            cfg["reranker"] = {"provider": "llm_reranker", "config": {}}
        if self.project.cfg("memory.graph", False):
            cfg["graph_store"] = {"provider": "temporal", "enabled": True}
        return cfg

    def _engine(self):
        if self._mem is not None or self.error:
            return self._mem
        with self._lock:
            if self._mem is not None or self.error:
                return self._mem
            if not self.enabled:
                self.error = "memory engine off (memory.engine)"
                return None
            try:
                from .memstore import Memory

                self.dir.mkdir(parents=True, exist_ok=True)
                self._mem = Memory.from_config(self.config(), router=self.router)
            except Exception as exc:  # optional engine: any failure degrades to the base tier
                self.error = f"{type(exc).__name__}: {exc}"[:200]
                log.info("memory engine unavailable: %s", self.error)
        return self._mem

    def scope_ids(self, scope: str = "project", scope_id: str | None = None) -> dict:
        """Engine scope ids. Every memory carries the project id; narrower scopes add their own."""
        ids = {"project_id": self.project.id}
        if scope == "team":
            ids["team_id"] = scope_id or str(self.project.cfg("team.id", "") or "") or "team"
        elif scope == "user":
            ids["user_id"] = scope_id or _git_user(self.project) or "me"
        elif scope == "session" and scope_id:
            ids["run_id"] = scope_id
        return ids

    def add(self, text: str, kind: str, *, scope: str = "project", scope_id: str | None = None,
            reconcile: bool | None = None, metadata: dict | None = None, timestamp: float | None = None) -> list[dict]:
        mem = self._engine()
        if mem is None:
            return []
        reconcile = self.can_reconcile if reconcile is None else (reconcile and self.can_reconcile)
        try:
            res = mem.add_facts([text], metadata={"kind": kind, **(metadata or {})}, reconcile=reconcile,
                                timestamp=timestamp, **self.scope_ids(scope, scope_id))
            results = res.get("results", []) if isinstance(res, dict) else list(res or [])
            if reconcile and not results:  # the model returned nothing usable: never lose the memory
                res = mem.add_facts([text], metadata={"kind": kind, **(metadata or {})}, reconcile=False,
                                    timestamp=timestamp, **self.scope_ids(scope, scope_id))
                results = res.get("results", [])
            return results
        except Exception as exc:
            log.info("engine add failed: %s", exc)
            return []

    def update(self, engine_id: str, text: str) -> bool:
        mem = self._engine()
        if mem is None or not engine_id:
            return False
        try:
            mem.update(engine_id, text=text)
            return True
        except Exception as exc:
            log.info("engine update failed: %s", exc)
            return False

    def delete(self, engine_id: str) -> bool:
        mem = self._engine()
        if mem is None or not engine_id:
            return False
        try:
            mem.delete(engine_id)
            return True
        except Exception as exc:
            log.info("engine delete failed: %s", exc)
            return False

    def search(self, query: str, limit: int = 8, *, filters: dict | None = None,
               threshold: float | None = None) -> list[dict]:
        mem = self._engine()
        if mem is None or not query.strip():
            return []
        floor = threshold if threshold is not None else float(
            self.project.cfg("memory.semantic_threshold", 0) or vectors.relevance_floor())
        try:
            res = mem.search(query, filters={"project_id": self.project.id, **(filters or {})}, top_k=limit,
                             threshold=floor, rerank=bool(self.project.cfg("memory.rerank", False)))
            return res.get("results", []) if isinstance(res, dict) else list(res or [])
        except Exception as exc:
            log.info("engine search failed: %s", exc)
            return []

    def batch(self):
        """Context for bulk writes (the engine persists once at the end)."""
        mem = self._engine()
        return mem.batch() if mem is not None else nullcontext()

    def history(self, engine_id: str) -> list[dict]:
        mem = self._engine()
        try:
            return mem.history(engine_id) if mem and engine_id else []
        except Exception:
            return []


def _git_user(project: Project) -> str:
    return project.git("config", "user.email").strip() or project.git("config", "user.name").strip()


class MemoryStore:
    def __init__(self, project: Project, brain: Brain, router: Router | None = None):
        self.project, self.brain = project, brain
        self.router = router or Router(project, brain)
        self.semantic = SemanticMemory(project, self.router)

    # ---- writes ----------------------------------------------------------------------------------
    def remember(self, text: str, kind: str = "fact", supersedes: str | None = None, source: str = "user",
                 provenance: str = "EXTRACTED", confidence: float = 1.0, link_symbols=None, *,
                 scope: str = "project", scope_id: str | None = None, reconcile: bool | None = None,
                 timestamp: float | None = None, metadata: dict | None = None) -> dict:
        """Store a memory. Returns ``{"id", "status"}`` with status ``stored``, ``exists``, ``updated``
        (the engine merged it into an overlapping memory, which it supersedes) or ``superseded <id>``."""
        text = _norm(text)
        if not text:
            raise ValueError("empty memory")
        kind = kind if kind in KINDS else "fact"
        scope = scope if scope in SCOPES else "project"
        old = None
        if supersedes:
            old = self.brain.memory(supersedes)
            if not old:
                raise ValueError(f"unknown memory id {supersedes}")
        # exact duplicate guard (cheap, deterministic)
        dup = self.brain.one("SELECT id FROM memories WHERE text=? AND forgotten=0 AND superseded_by IS NULL", (text,))
        if dup:
            return {"id": dup["id"], "status": "exists"}

        engine_ref, status, stored_text = None, "stored", text
        retired: list[str] = []
        extra: list[tuple[str, str]] = []  # further facts the model split out of this one: (engine id, text)
        # an explicit supersession evolves the same engine memory, so its history shows the change
        if old is not None and old["engine_ref"] and self.semantic.update(old["engine_ref"], text):
            engine_ref = old["engine_ref"]
        else:
            meta = {"source": source, "provenance": provenance, **(metadata or {})}

            def engine_add(with_model: bool | None) -> list[dict]:
                return self.semantic.add(text, kind, scope=scope, scope_id=scope_id, reconcile=with_model,
                                         metadata=meta, timestamp=timestamp)

            for r in engine_add(False if old is not None else reconcile):
                ev, rid = (r.get("event") or "").upper(), r.get("id")
                row = self._active_by_ref(rid) if rid else None
                row_text = (self.brain.memory(row["id"]) or {}).get("text", "") if row else ""
                if ev == "NONE" and rid:
                    # the reconciler lists every memory it compared as NONE, related or not: only one that
                    # states the same thing makes this a duplicate (anything else is left alone)
                    if not same_fact(text, row_text or r.get("memory") or ""):
                        continue
                    if row and not supersedes and engine_ref is None:
                        return {"id": row["id"], "status": "exists"}
                    engine_ref = engine_ref or rid  # same fact in the engine, no active row: share it
                elif ev == "UPDATE" and rid:
                    merged = _norm(r.get("memory") or text)
                    before = row_text or r.get("old_memory") or ""
                    if _contained(text, merged) < 0.5 or (before and _overlap(text, before) < 0.3):
                        continue  # a "merge" that drops this fact, or rewrites a memory about something else
                    engine_ref, stored_text = rid, merged
                    if row and not supersedes:
                        supersedes, status = row["id"], "updated"
                    elif row:
                        retired.append(row["id"])
                elif ev == "ADD" and rid and engine_ref is None:
                    engine_ref, stored_text = rid, _norm(r.get("memory") or text)
                elif ev == "ADD" and rid:
                    extra.append((rid, _norm(r.get("memory") or "")))
                elif ev == "DELETE" and row and _overlap(text, row_text) >= 0.3:
                    retired.append(row["id"])  # only what this statement is about can be retired by it
            if engine_ref is None:
                # the model only retired what this contradicts, or recorded nothing for it: keep the new memory
                # searchable in the engine too, under its own record
                engine_ref = next((r["id"] for r in engine_add(False) if r.get("id")), None)
            if stored_text != text:
                dup = self.brain.one("SELECT id FROM memories WHERE text=? AND forgotten=0 AND superseded_by IS NULL",
                                     (stored_text,))
                if dup and dup["id"] != supersedes:
                    return {"id": dup["id"], "status": "exists"}
        mid = self.brain.add_memory(stored_text, kind=kind, scope=scope, source=source, provenance=provenance,
                                    confidence=confidence, supersedes=supersedes, engine_ref=engine_ref)
        retired = [r for r in retired if r not in (mid, supersedes)]
        if retired:
            with self.brain.tx() as db:
                db.executemany("UPDATE memories SET superseded_by=? WHERE id=? AND superseded_by IS NULL",
                               [(mid, r) for r in retired])
        also = [self.brain.add_memory(t, kind=kind, scope=scope, source=source, provenance=provenance,
                                      confidence=confidence, engine_ref=ref) for ref, t in extra if t]
        for m in [mid, *also]:
            if timestamp:
                self._backdate(m, timestamp)
            if link_symbols:
                link_symbols(m, self.brain.memory(m)["text"])
        if supersedes and status == "stored":
            status = "superseded " + supersedes
        out = {"id": mid, "status": status}
        if retired:
            out["retired"] = retired
        if also:
            out["also"] = also
        return out

    def forget(self, mid: str) -> bool:
        """Soft-delete a memory in the read model and remove it from the engine."""
        m = self.brain.memory(mid)
        ok = self.brain.forget(mid)
        if ok and m and m["engine_ref"] and not self._active_by_ref(m["engine_ref"]):
            self.semantic.delete(m["engine_ref"])
        return ok

    def check_engine(self, fix: bool = False) -> dict:
        """Compare every active memory with its engine record, and with ``fix`` repair what is wrong.

        A record is wrong when it is missing, or when its text no longer states what the memory says
        (e.g. a reconciler rewrote a record that belonged to another memory). Repair re-creates the record
        from the memory's own text — reusing a record that already says exactly that — points the memory
        at it, and removes old records that no active memory refers to any more. Two memories share a
        record only when they state the same fact.
        """
        report: dict = {"fix": fix, "checked": 0, "ok": 0, "problems": [], "fixed": 0, "removed_records": []}
        eng = self.semantic.engine
        if eng is None:
            report["note"] = self.semantic.error or "memory engine unavailable"
            return report

        def engine_text(ref: str | None) -> str | None:
            try:
                rec = eng.get(ref) if ref else None
            except Exception:
                rec = None
            return rec.get("memory") if rec else None

        def wrong() -> list[tuple[dict, str, str | None]]:
            out = []
            for r in self.brain.q("SELECT * FROM memories WHERE forgotten=0 AND superseded_by IS NULL "
                                  "ORDER BY created_at"):
                et = engine_text(r["engine_ref"])
                if et is None:
                    out.append((dict(r), "missing", None))
                elif not same_fact(r["text"], et):
                    out.append((dict(r), "mismatched", et))
            return out

        bad = wrong()
        report["checked"] = self.brain.one("SELECT COUNT(*) n FROM memories WHERE forgotten=0 "
                                           "AND superseded_by IS NULL")["n"]
        report["ok"] = report["checked"] - len(bad)
        report["problems"] = [{"memory": r["id"], "engine_ref": r["engine_ref"], "problem": problem,
                               "text": r["text"][:200], "engine_text": (et or "")[:200]} for r, problem, et in bad]
        if not fix:
            return report
        old_refs: set[str] = set()
        with self.semantic.batch():
            for _ in range(3):  # re-pointing one memory can free or claim a record another one needs
                if not bad:
                    break
                for r, _problem, _et in bad:
                    ref = self._record_for(r)
                    if ref is None or ref == r["engine_ref"]:
                        continue
                    if r["engine_ref"]:
                        old_refs.add(r["engine_ref"])
                    with self.brain.tx() as db:
                        db.execute("UPDATE memories SET engine_ref=? WHERE id=?", (ref, r["id"]))
                    report["fixed"] += 1
                bad = wrong()
            for ref in sorted(old_refs):
                if not self._active_by_ref(ref) and self.semantic.delete(ref):
                    report["removed_records"].append(ref)
        report["remaining"] = len(bad)
        return report

    def _record_for(self, row: dict) -> str | None:
        """An engine record stating exactly this memory's text (an existing one, or a new one)."""
        meta = {"source": row["source"], "provenance": row["provenance"], "repaired": True}
        results = self.semantic.add(row["text"], row["kind"], scope=row["scope"], reconcile=False, metadata=meta,
                                    timestamp=row["created_at"])
        return next((r["id"] for r in results if r.get("id") and r.get("event") in ("ADD", "NONE")), None)

    def _active_by_ref(self, engine_ref: str):
        return self.brain.one("SELECT id FROM memories WHERE engine_ref=? AND forgotten=0 AND superseded_by IS NULL "
                              "ORDER BY created_at DESC", (engine_ref,))

    def _backdate(self, mid: str, ts: float) -> None:
        """Date a memory by when its source said it (a commit, an ADR) rather than when it was read."""
        with self.brain.tx() as db:
            db.execute("UPDATE memories SET created_at=? WHERE id=?", (float(ts), mid))
            db.execute("UPDATE events SET ts=? WHERE id=?", (float(ts), f"memory:{mid}"))

    # ---- reads -----------------------------------------------------------------------------------
    def recall(self, query: str, limit: int = 8) -> list[dict]:
        """Full-text and semantic hits, fused by rank (either list alone still works)."""
        scores: dict[str, float] = {}
        for rank, h in enumerate(self.brain.search(query, kinds=["memory"], limit=limit * 3)):
            mid = h["id"].split(":", 1)[1]
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (60 + rank)
        for rank, r in enumerate(self.semantic.search(query, limit=limit * 3)):
            row = self._active_by_ref(r.get("id"))
            if row:
                scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (60 + rank)
        out = []
        for mid in sorted(scores, key=lambda k: -scores[k]):
            m = self.brain.memory(mid)
            if m and not m["forgotten"] and not m["superseded_by"]:
                out.append(m)
            if len(out) >= limit:
                break
        return out

    def about(self, labels: list[str], paths: list[str], limit: int = 6) -> list[dict]:
        """Memories linked to (or mentioning) any of these symbols/files."""
        ids = {lk["src"].split(":", 1)[1] for lk in self.brain.links_to(
            [f"file:{p}" for p in paths] + [f"label:{x.lower()}" for x in labels], rels=("mentions", "cites", "about"),
            limit=200) if lk["src"].startswith("memory:")}
        words = [w for w in labels if len(w) >= 4][:6] + [Path(p).stem for p in paths[:4] if len(Path(p).stem) >= 4]
        for w in words:
            for m in self.recall(w, limit=4):
                if re.search(rf"\b{re.escape(w)}\b", m["text"], re.I):
                    ids.add(m["id"])
        out = [m for i in ids if (m := self.brain.memory(i)) and not m["forgotten"] and not m["superseded_by"]]
        order = {k: i for i, k in enumerate(KINDS)}
        out.sort(key=lambda m: (order.get(m["kind"], 9), -m["created_at"]))
        return out[:limit]

    def history(self, mid: str) -> dict:
        """How a memory came to be: its supersession chain in the read model and the engine's change log."""
        m = self.brain.memory(mid)
        if not m:
            return {"memory": None, "chain": [], "changes": []}
        chain, seen = [], set()
        cur = m
        while cur and cur["id"] not in seen:  # walk back through what this memory superseded
            seen.add(cur["id"])
            chain.append(cur)
            cur = self.brain.one("SELECT * FROM memories WHERE superseded_by=?", (cur["id"],))
            cur = dict(cur) if cur else None
        nxt = m
        while nxt and nxt["superseded_by"] and nxt["superseded_by"] not in seen:  # and forward
            nxt = self.brain.memory(nxt["superseded_by"])
            if nxt:
                seen.add(nxt["id"])
                chain.insert(0, nxt)
        refs = {c["engine_ref"] for c in chain if c.get("engine_ref")}
        changes = sorted((h for ref in refs for h in self.semantic.history(ref)),
                         key=lambda h: (str(h.get("updated_at") or h.get("created_at") or "")))
        return {"memory": m, "chain": chain, "changes": changes}

    def seed(self) -> dict:
        return seed_from_repo(self.project, self.brain, self.router, store=self)


# =================================================================================================
# Seeding: memories every repository already contains
# =================================================================================================
SEED_LINK_SOURCE = "memory-seed"
_DOC_GLOBS = ("docs/adr/*.md", "docs/adrs/*.md", "doc/adr/*.md", "docs/architecture/decisions/*.md",
              "architecture/decisions/*.md", "adr/*.md", "docs/decisions/**/*.md", "ADR*.md", "adr*.md")
_CONVENTION_FILES = ("CONTRIBUTING.md", ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md", "docs/contributing.md",
                     "CONVENTIONS.md", "docs/CONVENTIONS.md", "docs/conventions.md", "STYLE.md", "STYLEGUIDE.md",
                     "STYLE_GUIDE.md", "docs/STYLE.md", "docs/style.md", "docs/style-guide.md", "CODING_STANDARDS.md",
                     "docs/coding-standards.md", "docs/code-style.md")
_WHOLE_FILE_CONVENTIONS = re.compile(r"(conventions?|style|standards)", re.I)
_RULE_HEADING = re.compile(r"(rules?|conventions?|guidelines?|style|standards?|best practices|principles|"
                           r"do'?s|don'?ts|dos and don|code review|coding|how we work|expectations|requirements for)",
                           re.I)
_SKIP_DOC = re.compile(r"(^|/)(readme|index|template|adr-template|\d+-template)[^/]*\.md$", re.I)
_ACCEPTED = {"accepted", "approved", "adopted", "decided", "done", "implemented", "active", "agreed"}
_NOT_DECIDED = re.compile(r"\b(proposed|draft|rejected|superseded|deprecated|withdrawn|obsolete|declined)\b", re.I)
_DECISION_HEADING = re.compile(r"^(the )?(decision( outcome| record| made)?|resolution|chosen option|we decided)\b",
                               re.I)
_CLARIFY = re.compile(r"^\s*[-*]\s*\*{0,2}Q\*{0,2}:\s*(?P<q>.+?)\s*(?:→|->|=>)\s*\*{0,2}A\*{0,2}:\s*(?P<a>.+)$")
_TRAILER = re.compile(r"^(signed-off-by|co-authored-by|change-id|reviewed-by|reviewed-on|acked-by|tested-by|"
                      r"reported-by|suggested-by|cc|closes|fixes|refs|see-also|bug|issue)\s*:", re.I)
_REVERT_SUBJECT = re.compile(r'^revert\s*(?:"(?P<q>.+)"|:\s*(?P<c>.+)|\s+(?P<p>.+))$', re.I)
_REVERTS_SHA = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.I)
_FIX_SUBJECT = re.compile(r"^(fix|fixes|fixed|hotfix|bugfix|bug)(\([^)]*\))?!?[:\s]|\b(regression|crash(es|ed)?|"
                          r"leak|race condition|deadlock|data loss|incident|outage|double[- ]charge|corrupt\w*)\b",
                          re.I)
_CONVENTIONAL_PREFIX = re.compile(r"^[a-z]+(\([^)]*\))?!?:\s*", re.I)
_COSMETIC = re.compile(r"\b(typos?|spelling|docs?|documentation|readme|changelog|lint(ing)?|format(ting)?|"
                       r"whitespace|comments?|wording|grammar)\b", re.I)
SEP_C, SEP_F = "\x1e", "\x1f"


@dataclass
class Seed:
    slot: str             # stable identity of the source statement (file / question / commit)
    kind: str
    text: str
    source: str           # citation shown with the memory: a repo path or commit:<short sha>
    group: str            # statements that can replace each other when edited (same file / heading)
    ts: float | None = None
    cites: list[str] = field(default_factory=list)   # read-model ids the memory cites
    about: list[str] = field(default_factory=list)   # files the memory is about
    confidence: float = 1.0
    replaces_slot: str | None = None  # e.g. a revert replaces the memory of the commit it reverted

    @property
    def digest(self) -> str:
        return hashlib.sha1(f"{self.kind}\n{self.text}".encode()).hexdigest()


def _clip(text: str, limit: int = 420) -> str:
    text = _norm(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[: stop + 1] if stop > limit * 0.5 else cut.rsplit(" ", 1)[0]).rstrip(" ,;:") + "…"


def _md_plain(text: str) -> str:
    """Markdown inline noise removed: links keep their text, emphasis/code markers dropped."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", text)
    return _norm(text)


def _sections(md: str) -> list[tuple[str, int, list[str]]]:
    """(heading, level, lines) for each Markdown section; the preamble has heading ''."""
    out: list[tuple[str, int, list[str]]] = [("", 0, [])]
    in_code = False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            out[-1][2].append(line)
            continue
        m = None if in_code else re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            out.append((m.group(2).strip(), len(m.group(1)), []))
        else:
            out[-1][2].append(line)
    return out


def _paragraphs(lines: list[str]) -> list[str]:
    paras, cur, in_code = [], [], False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code or line.strip().startswith("|") or line.strip().startswith(">"):
            continue
        if not line.strip():
            if cur:
                paras.append(" ".join(cur))
                cur = []
            continue
        cur.append(line.strip())
    if cur:
        paras.append(" ".join(cur))
    return [p for p in paras if p]


def _doc_time(project: Project, rel: str) -> float | None:
    out = project.git("log", "-1", "--format=%at", "--", rel).strip()
    if out.isdigit():
        return float(out)
    try:
        return (project.root / rel).stat().st_mtime
    except OSError:
        return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def adr_seeds(project: Project) -> list[Seed]:
    """One decision per accepted ADR / decision record: its title and what was decided."""
    root, seen, out = project.root, set(), []
    for pattern in _DOC_GLOBS:
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            if rel in seen or not path.is_file() or _SKIP_DOC.search(rel):
                continue
            seen.add(rel)
            seed = _adr_seed(project, rel, _read(path))
            if seed:
                out.append(seed)
    return out


def _adr_status(md: str, sections) -> str:
    m = re.search(r"^\W*status\W*[:\-]?\W*([A-Za-z][\w -]*)", md, re.I | re.M)
    if m:
        return m.group(1).strip().lower()
    for heading, _, lines in sections:
        if heading.lower().startswith("status"):
            for p in _paragraphs(lines):
                return _md_plain(p).lower()
    m = re.search(r"^status:\s*(.+)$", md, re.I | re.M)  # front matter
    return m.group(1).strip().lower() if m else ""


def _adr_seed(project: Project, rel: str, md: str) -> Seed | None:
    if not md.strip():
        return None
    sections = _sections(md)
    title = next((h for h, lvl, _ in sections if lvl == 1), "") or Path(rel).stem.replace("-", " ")
    title = re.sub(r"^(adr|decision|record)?[\s-]*#?\d+[\s.:)-]*", "", _md_plain(title), flags=re.I).strip() or title
    status = _adr_status(md, sections)
    if status and _NOT_DECIDED.search(status.split("·")[0]) and not any(a in status for a in _ACCEPTED):
        return None
    decision = ""
    for heading, _, lines in sections:
        if _DECISION_HEADING.match(_md_plain(heading)):
            paras = [_md_plain(p) for p in _paragraphs(lines)]
            bullets = [re.sub(r"^[-*+]\s+|^\d+[.)]\s+", "", p) for p in paras]
            decision = " ".join(bullets[:3])
            break
    if not decision:
        m = re.search(r"chosen option:?\s*(.+)", md, re.I)
        decision = _md_plain(m.group(1)) if m else ""
    text = f"{title.rstrip('.')}: {decision}" if decision else f"Decided: {title}"
    return Seed(slot=f"adr:{rel}", kind="decision", text=_clip(text), source=rel, group=f"adr:{rel}",
                ts=_doc_time(project, rel), cites=[f"file:{rel}"])


def clarification_seeds(project: Project) -> list[Seed]:
    """Each answered clarification (``- Q: … → A: …``) in a feature spec is a decision."""
    out = []
    for spec in sorted(project.root.glob("specs/*/spec.md")):
        rel = spec.relative_to(project.root).as_posix()
        feature = spec.parent.name
        ts = _doc_time(project, rel)
        lines = _read(spec).splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if re.match(r"^\s*[-*]\s*\*{0,2}Q\*{0,2}:", line):
                block = [line.strip()]
                j = i + 1
                while j < len(lines) and lines[j].strip() and not re.match(r"^\s*([-*]|#)", lines[j]):
                    block.append(lines[j].strip())
                    j += 1
                m = _CLARIFY.match(" ".join(block))
                if m:
                    q = _md_plain(m.group("q")).rstrip("?").strip()
                    a = _md_plain(m.group("a"))
                    if q and a:
                        qid = hashlib.sha1(q.lower().encode()).hexdigest()[:12]
                        out.append(Seed(slot=f"clarify:{rel}#{qid}", kind="decision", text=_clip(f"{q}? {a}"),
                                        source=rel, group=f"clarify:{rel}", ts=ts,
                                        cites=[f"spec:{feature}", f"file:{rel}"]))
                i = j
                continue
            i += 1
    return out


def convention_seeds(project: Project) -> list[Seed]:
    """Rules of thumb from contributing / conventions / style docs (bullet lists under rule-like headings,
    or every bullet in a file that is all about conventions)."""
    out, seen_paths = [], set()
    for rel in _CONVENTION_FILES:
        path = project.root / rel
        if not path.is_file() or path.resolve() in seen_paths:
            continue
        seen_paths.add(path.resolve())
        whole = bool(_WHOLE_FILE_CONVENTIONS.search(Path(rel).stem)) and "contributing" not in rel.lower()
        ts = _doc_time(project, rel)
        for heading, _, lines in _sections(_read(path)):
            if not (whole or _RULE_HEADING.search(heading)):
                continue
            for bullet in _bullets(lines):
                text = _md_plain(bullet)
                if not _looks_like_rule(text):
                    continue
                bid = hashlib.sha1(text.lower().encode()).hexdigest()[:12]
                out.append(Seed(slot=f"convention:{rel}#{bid}", kind="convention", text=_clip(text), source=rel,
                                group=f"convention:{rel}#{heading.lower()}", ts=ts, cites=[f"file:{rel}"]))
    return out


def _bullets(lines: list[str]) -> list[str]:
    items, cur, in_code = [], None, False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = re.match(r"^(\s{0,3})[-*+]\s+(?!\[[ xX]\])(.*)$", line) or re.match(r"^(\s{0,3})\d+[.)]\s+(.*)$", line)
        if m:
            if cur:
                items.append(cur)
            cur = m.group(2).strip()
        elif cur is not None and line.strip() and line.startswith((" ", "\t")):
            cur += " " + line.strip()
        else:
            if cur:
                items.append(cur)
            cur = None
    if cur:
        items.append(cur)
    return items


def _looks_like_rule(text: str) -> bool:
    words = text.split()
    if not (4 <= len(words) <= 80) or len(text) < 20:
        return False
    if re.fullmatch(r"[`\w./ -]+", text) and "`" in text and len(words) <= 3:  # a bare command
        return False
    if text.lower().startswith(("see ", "http", "link", "read ")) and len(words) < 8:
        return False
    return True


def _commit_log(project: Project, rng: list[str], limit: int) -> list[dict]:
    fmt = f"--format={SEP_C}%H{SEP_F}%at{SEP_F}%an{SEP_F}%s{SEP_F}%b{SEP_F}"
    raw = project.git("log", "--no-merges", "--name-only", fmt, f"-n{limit}", *rng, timeout=300)
    commits = []
    for chunk in raw.split(SEP_C):
        parts = chunk.split(SEP_F)
        if len(parts) < 6:
            continue
        sha, at, author, subject, body, rest = parts[0].strip(), parts[1], parts[2], parts[3], parts[4], parts[5]
        files = [f.strip() for f in rest.splitlines() if f.strip()]
        commits.append({"sha": sha, "ts": float(at) if at.strip().isdigit() else None, "author": author,
                        "subject": subject.strip(), "body": body.strip(), "files": files})
    return commits


def _explanation(body: str) -> str:
    """The prose of a commit body: trailers, revert boilerplate and issue refs removed."""
    keep = []
    for line in body.splitlines():
        s = line.strip()
        if not s or _TRAILER.match(s) or _REVERTS_SHA.search(s) or re.fullmatch(r"(#\d+[ ,]*)+", s):
            keep.append("")
            continue
        keep.append(re.sub(r"^[-*]\s+", "", s))
    paras = [p for p in " ".join(x if x else "\n" for x in keep).split("\n") if p.strip()]
    return _norm(paras[0]) if paras else ""


def commit_seeds(commits: Iterable[dict]) -> list[Seed]:
    """Gotchas from history: reverted changes, and fixes whose message explains what went wrong."""
    out = []
    for c in commits:
        sha, subject, body = c["sha"], c["subject"], c["body"]
        short = sha[:10]
        why = _explanation(body)
        m = _REVERT_SUBJECT.match(subject)
        if m:
            orig = (m.group("q") or m.group("c") or m.group("p") or "").strip().strip('"')
            ref = _REVERTS_SHA.search(body)
            text = f'"{orig}" was reverted (commit {short})'
            text += f": {why}" if len(why.split()) >= 4 else ". Find out why before re-applying that change."
            out.append(Seed(slot=f"gotcha:commit:{sha}", kind="gotcha", text=_clip(text), source=f"commit:{short}",
                            group=f"gotcha:commit:{sha}", ts=c["ts"], cites=[f"commit:{sha}"], about=c["files"][:12],
                            confidence=0.9,
                            replaces_slot=f"gotcha:commit:{ref.group(1)}" if ref and len(ref.group(1)) == 40 else None))
            continue
        if _FIX_SUBJECT.search(subject) and not _COSMETIC.search(subject) and len(why.split()) >= 6 and len(why) >= 40:
            title = _CONVENTIONAL_PREFIX.sub("", subject).strip().rstrip(".")
            title = title[:1].upper() + title[1:]
            out.append(Seed(slot=f"gotcha:commit:{sha}", kind="gotcha", text=_clip(f"{title}: {why}"),
                            source=f"commit:{short}", group=f"gotcha:commit:{sha}", ts=c["ts"],
                            cites=[f"commit:{sha}"], about=c["files"][:12], confidence=0.8))
    return out


class SeedLedger:
    """Which memory each seed slot produced (``.cairn/memstore/seeds.db``), so re-runs are idempotent."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS seeds(slot TEXT PRIMARY KEY, memory_id TEXT NOT NULL, digest TEXT NOT NULL,"
            " owned INTEGER NOT NULL, grp TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,"
            " updated_at REAL NOT NULL, cites TEXT NOT NULL DEFAULT '[]', about TEXT NOT NULL DEFAULT '[]');"
            "CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);")

    def rows(self) -> dict[str, dict]:
        out = {}
        for r in self.db.execute("SELECT * FROM seeds"):
            row = dict(r)
            row["cites"], row["about"] = json.loads(row["cites"]), json.loads(row["about"])
            out[row["slot"]] = row
        return out

    def put(self, seed: Seed, memory_id: str, owned: bool) -> dict:
        row = {"slot": seed.slot, "memory_id": memory_id, "digest": seed.digest, "owned": int(owned),
               "grp": seed.group, "kind": seed.kind, "text": seed.text, "updated_at": time.time(),
               "cites": list(seed.cites), "about": list(seed.about)}
        self.db.execute("INSERT OR REPLACE INTO seeds(slot,memory_id,digest,owned,grp,kind,text,updated_at,cites,about)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (*[row[k] for k in ("slot", "memory_id", "digest", "owned", "grp", "kind", "text",
                                            "updated_at")], json.dumps(row["cites"]), json.dumps(row["about"])))
        return row

    def drop(self, slot: str) -> None:
        self.db.execute("DELETE FROM seeds WHERE slot=?", (slot,))

    def get_kv(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_kv(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (key, value))

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.close()


def _missing_commits(project: Project, shas: list[str]) -> set[str]:
    if not shas:
        return set()
    try:
        res = subprocess.run(["git", "-C", str(project.root), "cat-file", "--batch-check"], input="\n".join(shas) + "\n",
                             capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {line.split()[0] for line in res.stdout.splitlines() if line.endswith(" missing")}


def _is_ancestor(project: Project, older: str, newer: str) -> bool:
    try:
        return subprocess.run(["git", "-C", str(project.root), "merge-base", "--is-ancestor", older, newer],
                              capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def collect_seeds(project: Project, ledger: SeedLedger | None = None) -> tuple[list[Seed], bool]:
    """Every seed the repository yields now. Commit history is read incrementally (cursor in the
    ledger); returns (seeds, rewritten) where ``rewritten`` means history no longer contains the cursor."""
    seeds = adr_seeds(project) + clarification_seeds(project) + convention_seeds(project)
    rewritten = False
    if project.is_git and project.git("rev-parse", "HEAD").strip():
        head = project.git("rev-parse", "HEAD").strip()
        cursor = ledger.get_kv("commits.cursor") if ledger else None
        limit = int(project.cfg("memory.seed_commits", 2000))
        rng: list[str] = []
        if cursor and cursor != head:
            if _is_ancestor(project, cursor, head):
                rng = [f"{cursor}..{head}"]
            else:
                rewritten = True
        if cursor != head or rewritten:
            seeds += commit_seeds(_commit_log(project, rng, limit))
        if ledger:
            ledger.set_kv("commits.cursor", head)
    return seeds, rewritten


def seed_from_repo(project: Project, brain: Brain, router: Router | None = None, *,
                   store: MemoryStore | None = None, progress: Callable[[str], None] | None = None,
                   wall_seconds: float | None = None) -> dict:
    """Mirror the repository's own decisions, clarifications, conventions and gotchas into memory.

    Deterministic and idempotent: an unchanged source changes nothing; an edited source supersedes
    its old memory; a removed source retires it (seeded memories only — a memory someone wrote by hand
    is never retired). With a model available, new candidates go through the engine's reconciliation
    (bounded by ``memory.seed_model_limit`` per run), so a seed that restates an existing memory merges
    into it instead of duplicating it.
    """
    if project.cfg("memory.seed", True) is False:
        return {"skipped": True, "note": "memory.seed is off"}
    store = store or MemoryStore(project, brain, router)
    stats = {"candidates": 0, "added": 0, "updated": 0, "merged": 0, "unchanged": 0, "retired": 0, "reconciled": 0}
    # one seed run at a time per repository (a sync and a manual run, or two processes): the ledger is
    # read at the start and decides every write, so runs must not interleave
    try:
        run_lock = vectors.file_lock(project.dir / "memstore" / "seed.lock")
        run_lock.__enter__()
    except TimeoutError:
        return {"skipped": True, "note": "another memory seed run is still in progress"}
    try:
        try:  # repair engine records first, so reconciliation compares against what memories really say
            check = store.check_engine(fix=True)
            stats["engine_check"] = {k: (len(v) if isinstance(v, list) else v) for k, v in check.items()
                                     if k in ("checked", "problems", "fixed", "removed_records", "remaining", "note")}
        except Exception as exc:  # never let the repair block seeding
            stats["engine_check"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        ledger = SeedLedger(project.dir / "memstore" / "seeds.db")
        try:
            with store.semantic.batch():
                _apply_seeds(project, brain, store, ledger, stats, progress, wall_seconds=wall_seconds)
        finally:
            ledger.close()
    finally:
        run_lock.__exit__(None, None, None)
    return stats


def _apply_seeds(project: Project, brain: Brain, store: MemoryStore, ledger: SeedLedger, stats: dict,
                 progress: Callable[[str], None] | None = None,
                 wall_seconds: float | None = None) -> None:
    seeds, rewritten = collect_seeds(project, ledger)
    by_slot: dict[str, Seed] = {}
    for s in seeds:
        by_slot.setdefault(s.slot, s)
    for s in list(by_slot.values()):  # a reverted fix is remembered as the revert, not as a fix
        if s.replaces_slot:
            by_slot.pop(s.replaces_slot, None)
    stats["candidates"] = len(by_slot)
    rows = ledger.rows()
    # commit seeds are read incrementally: keep the ones not re-read unless their commit is gone
    vanished = {slot: row for slot, row in rows.items() if slot not in by_slot}
    commit_slots = [slot for slot in vanished if slot.startswith("gotcha:commit:")]
    gone = {f"gotcha:commit:{sha}" for sha in _missing_commits(
        project, [s.rsplit(":", 1)[1] for s in commit_slots])} if rewritten else set()
    for slot in commit_slots:
        if slot not in gone:
            vanished.pop(slot)
    model_budget = int(project.cfg("memory.seed_model_limit", 25))
    can_model = store.semantic.can_reconcile

    def active(mid: str | None) -> dict | None:
        m = brain.memory(mid) if mid else None
        return m if m and not m["forgotten"] and not m["superseded_by"] else None

    def write(seed: Seed, supersedes: str | None) -> dict:
        nonlocal model_budget
        use_model = can_model and model_budget > 0 and supersedes is None
        if use_model:
            model_budget -= 1
            stats["reconciled"] += 1
        return store.remember(seed.text, kind=seed.kind, supersedes=supersedes, source=seed.source,
                              provenance="EXTRACTED", confidence=seed.confidence, reconcile=use_model,
                              timestamp=seed.ts, metadata={"seed": seed.slot})

    started = time.time()
    processed = 0
    for slot in sorted(by_slot, key=lambda s: (by_slot[s].ts or 0, s)):
        if wall_seconds is not None and time.time() - started >= wall_seconds:
            # a model call inside a seed can wedge (observed in the field); the cap bounds the damage to
            # one in-flight call — the remaining seeds continue on the next sync, the ledger keeps state
            stats["note"] = (f"wall clock ({int(wall_seconds)}s) reached — the remaining "
                             f"{len(by_slot) - processed} seed(s) continue on the next sync")
            break
        seed = by_slot[slot]
        processed += 1
        row = rows.get(slot)
        if row is None and seed.replaces_slot and seed.replaces_slot in rows:
            row = rows[seed.replaces_slot]
            vanished.pop(seed.replaces_slot, None)
            ledger.drop(seed.replaces_slot)
        if row is None:  # an edited statement shows up under a new slot: pair it with the one it replaced
            best, score = None, 0.0
            for vslot, vrow in vanished.items():
                if vrow["grp"] == seed.group and vrow["kind"] == seed.kind:
                    j = _jaccard(vrow["text"], seed.text)
                    if j > score:
                        best, score = vslot, j
            if best and score >= 0.5:
                row = vanished.pop(best)
                ledger.drop(best)
        mapped = brain.memory(row["memory_id"]) if row is not None else None
        if mapped and not row["owned"] and _jaccard(mapped["text"], seed.text) < 0.2:
            mapped = None  # recorded against an unrelated memory (an old reconcile mix-up): write it again
        if row is not None and row["digest"] == seed.digest and row["slot"] == slot and mapped:
            stats["unchanged"] += 1  # includes seeds a person forgot or superseded on purpose
            continue
        prev = active(row["memory_id"]) if row is not None and row["owned"] else None
        res = write(seed, prev["id"] if prev else None)
        # merged into someone else's memory ("updated") or a duplicate of one ("exists"): never ours to retire
        owned = res["status"] == "stored" or res["status"].startswith("superseded")
        rows[slot] = ledger.put(seed, res["id"], owned)
        if res["status"] in ("exists", "updated"):  # a duplicate of, or merged into, an existing memory
            stats["merged"] += 1
        elif prev:
            stats["updated"] += 1
        else:
            stats["added"] += 1
        if progress:
            progress(f"{seed.kind}: {seed.text[:60]}")
    for slot, row in vanished.items():
        if row["owned"] and active(row["memory_id"]):
            store.forget(row["memory_id"])
            stats["retired"] += 1
        ledger.drop(slot)
    ledger.commit()
    # citations: memory -> the file / spec / commit it came from, and the files a gotcha is about
    brain.drop_source(SEED_LINK_SOURCE)
    links = []
    for row in ledger.rows().values():  # every live seed, including commits read on earlier runs
        if not active(row["memory_id"]):
            continue
        src = f"memory:{row['memory_id']}"
        links += [(src, dst, "cites", "EXTRACTED", 1.0, SEED_LINK_SOURCE) for dst in row["cites"]]
        links += [(src, f"file:{f}", "about", "INFERRED", 0.8, SEED_LINK_SOURCE) for f in row["about"]]
    if links:
        brain.link(links)
    stats["links"] = len(links)

"""The recall worker: drains the queue that hooks write into observations and summaries.

Run it detached (the Stop/SessionEnd hooks do: ``python -m cairn.engines.recall.worker --once``),
from the CLI, or hosted in the Cairn server (``run_worker(root, stop_event)``). One worker per
repository at a time (a file lock); a second one exits immediately and the holder picks up its work.

With a model (``cairn.router.Router``), each queued event goes through the observer conversation.
Without one (or when an event keeps failing past ``max_retries``), records are derived
deterministically from the tool events, so capture is never empty. After storing, the worker keeps
the semantic index, folder context files and, after each summary, the memory block that agents
without a session-start hook read from AGENTS.md up to date.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ... import filelock
from . import fallback
from .context import generate_context
from .modes import load_mode
from .observer import ObserverLLM, Outcome, SessionObserver
from .projects import project_context
from .settings import load as load_settings
from .store import Store

log = logging.getLogger("cairn.recall")

MAX_PASSES = 200


def lock_path(root: Path | str) -> Path:
    return Path(root) / ".cairn" / "recall" / "worker.lock"


@contextlib.contextmanager
def worker_lock(root: Path | str, blocking: bool = False) -> Iterator[bool]:
    """Hold the repository's worker lock; yields False when another worker has it."""
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    try:
        if not (filelock.lock(fh, None) if blocking else filelock.try_lock(fh)):
            yield False
            return
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({"pid": os.getpid(), "started": int(time.time())}))
        fh.flush()
        yield True
    finally:
        fh.close()


def make_router(root: Path | str):
    """The model router for this repository (the user's configured provider), or None."""
    try:
        from ...project import Project
        from ...router import Router
    except Exception as exc:  # noqa: BLE001
        log.warning("model router unavailable: %s", exc)
        return None
    project = Project.discover(Path(root)) or Project(root=Path(root))
    brain = None
    try:
        if project.db_path.exists():
            from ...store import Brain
            brain = Brain(project.db_path)
    except Exception:  # noqa: BLE001 - the cost ledger is optional
        brain = None
    return Router(project, brain)



CATCH_UP_CALLS = 100  # a session this far behind drains in about this many model calls


def batch_size(base: int, catch_up: int, backlog: int) -> int:
    """Tool events per model call: ``base`` normally; when a session is far behind (a long autonomous turn,
    or capture that ran while the worker was off), bigger batches up to ``catch_up`` so the backlog drains
    in about ``CATCH_UP_CALLS`` calls instead of one call per few events."""
    base = max(1, base)
    return max(base, min(catch_up, backlog // CATCH_UP_CALLS)) if catch_up > base else base


class Worker:
    """Processes every pending session of one repository."""

    def __init__(self, root: Path | str, *, router: Any = None, settings: dict | None = None,
                 on_event: Callable[[dict], None] | None = None):
        self.root = Path(root)
        self.settings = settings or load_settings(self.root)
        self.router = router
        self.on_event = on_event
        self.project = project_context(str(self.root)).primary
        self.stats = {"sessions": 0, "stored": 0, "derived": 0, "dropped": 0, "preserved": 0, "recycled": 0,
                      "observations": 0, "summaries": 0}
        self._stats_lock = threading.Lock()

    def _bump(self, key: str, n: int = 1) -> None:
        with self._stats_lock:
            self.stats[key] = self.stats.get(key, 0) + n

    def _store(self) -> Store:
        return Store.open(self.root, project_name=self.project)

    # ---- after storing --------------------------------------------------------------------------------
    def after_store(self, store: Store, event: dict) -> None:
        obs_ids = event.get("observation_ids") or []
        self._bump("observations", len(obs_ids))
        if event.get("summary_id"):
            self._bump("summaries")
        if self.settings.get("vectors"):
            try:
                from .vectorsync import VectorSync
                vs = VectorSync(self.root, store)
                if obs_ids:
                    vs.sync_observations(obs_ids)
                if event.get("summary_id"):
                    vs.sync_summaries([event["summary_id"]])
            except Exception as exc:  # noqa: BLE001 - the index catches up on the next backfill
                log.warning("vector sync failed: %s", exc)
        if self.settings.get("folder_context"):
            files = [f for o in event.get("observations") or [] for f in (o.get("files_modified") or [])
                     + (o.get("files_read") or [])]
            if files:
                try:
                    from .folders import update_folder_claude_md_files
                    update_folder_claude_md_files(self.root, files, event.get("project") or self.project,
                                                  self.settings, store)
                except Exception as exc:  # noqa: BLE001 - folder files are a convenience
                    log.warning("folder context update failed: %s", exc)
        if event.get("summary_id"):
            try:
                from .folders import update_cursor_context_for_project
                update_cursor_context_for_project(self.root, event.get("project") or self.project, self.settings)
            except Exception as exc:  # noqa: BLE001
                log.debug("cursor context update skipped: %s", exc)
            try:
                from ...agents import refresh_context
                from ...project import Project
                project = Project.discover(self.root)
                if project is not None:
                    refresh_context(project)
            except Exception as exc:  # noqa: BLE001 - the next summary or sync refreshes it
                log.warning("agent context refresh failed: %s", exc)
        if self.on_event:
            with contextlib.suppress(Exception):
                self.on_event({"type": "stored", **{k: v for k, v in event.items() if k != "session"}})

    # ---- derived records ------------------------------------------------------------------------------
    def derive(self, store: Store, session: dict, messages: list[dict]) -> None:
        """Deterministic observations (one per prompt, merged) and summaries for these messages."""
        memory_id = store.ensure_memory_session_id(session["id"])
        mode = load_mode(self.settings.get("mode"))
        root = str(self.root)
        groups: dict[Any, list[dict]] = {}
        for m in messages:
            if m["message_type"] == "observation":
                groups.setdefault(m.get("prompt_number"), []).append(m)
        stored_obs: list[int] = []
        observations: list[dict] = []
        for number, msgs in groups.items():
            ev = fallback.turn_evidence(msgs, root)
            prompt = store.get_user_prompt(session["content_session_id"], number, session["id"]) if number else ""
            existing = store.find_derived_observation(memory_id, number)
            if existing:
                ev = fallback.merge_evidence(fallback.evidence_from_observation(existing), ev)
            obs = fallback.observation_from_evidence(prompt or "", ev, mode)
            if obs is None:
                continue
            if existing:
                store.update_observation(existing["id"], {k: obs[k] for k in ("type", "title", "subtitle", "facts",
                                                                             "narrative", "concepts", "files_read",
                                                                             "files_modified", "metadata")})
                stored_obs.append(existing["id"])
            else:
                earliest = min(int(m.get("created_at_epoch") or 0) for m in msgs) or None
                res = store.store_observations(memory_id, session["project"] or self.project, [obs], None, number, 0,
                                               earliest, None)
                stored_obs += res["observation_ids"]
            observations.append(obs)
        summary_id = None
        for m in messages:
            if m["message_type"] != "summarize":
                continue
            number = m.get("prompt_number")
            prompt = store.get_user_prompt(session["content_session_id"], number, session["id"]) if number else ""
            rows = store.db.execute("SELECT files_read, files_modified, facts, metadata FROM observations"
                                    " WHERE memory_session_id=? AND prompt_number IS ?", (memory_id, number)).fetchall()
            ev = {"read": [], "modified": [], "commands": [], "searches": [], "tools": {}}
            for r in rows:
                ev = fallback.merge_evidence(ev, fallback.evidence_from_observation(dict(r)))
            summary = fallback.summary_from_turn(prompt or "", ev, m.get("last_assistant_message"))
            res = store.store_summary(memory_id, session["project"] or self.project, summary, number, 0,
                                      int(m.get("created_at_epoch") or 0) or None)
            summary_id = res["id"]
        store.confirm([m["id"] for m in messages])
        self._bump("derived", len(messages))
        if stored_obs or summary_id:
            self.after_store(store, {"session": session, "observation_ids": list(dict.fromkeys(stored_obs)),
                                     "summary_id": summary_id, "observations": observations,
                                     "project": session["project"] or self.project, "derived": True})

    # ---- one session ----------------------------------------------------------------------------------
    def _next_batch(self, store: Store, sid: int) -> int:
        pending = store.peek_pending(sid)
        if not pending:
            return 0
        if pending[0]["message_type"] == "summarize":
            return 1
        size = batch_size(int(self.settings.get("observe_batch") or 1),
                          int(self.settings.get("observe_catch_up") or 0), len(pending))
        n = 0
        for p in pending:
            if p["message_type"] != "observation" or n >= size or p["prompt_number"] != pending[0]["prompt_number"]:
                break
            n += 1
        return max(1, n)

    def process_session(self, sid: int, llm: ObserverLLM | None) -> str:
        """Drain one session; returns 'done', 'deferred' (kept for later) or 'missing'."""
        store = self._store()
        try:
            session = store.get_session_by_id(sid)
            if session is None:
                return "missing"
            store.ensure_memory_session_id(sid)
            session = store.get_session_by_id(sid)
            self._bump("sessions")
            use_model = llm is not None and llm.available
            observer = None
            if use_model:
                projects = project_context(session.get("cwd") or str(self.root)).all_projects or [session["project"]]
                observer = SessionObserver(
                    self.root, store, llm, load_mode(self.settings.get("mode")), self.settings,
                    prior_context=lambda: generate_context(self.root, cwd=session.get("cwd") or str(self.root),
                                                           projects=projects, settings=self.settings),
                    on_stored=lambda ev: self.after_store(store, ev))
            max_retries = int(self.settings.get("max_retries") or 3)
            for _ in range(10_000):
                if not use_model:
                    msgs = store.claim(sid, 10_000)
                    if not msgs:
                        return "done"
                    self.derive(store, session, msgs)
                    continue
                n = self._next_batch(store, sid)
                if not n:
                    return "done"
                msgs = store.claim(sid, n)
                if not msgs:
                    return "done"
                outcome = observer.process(session, msgs)
                self._bump({Outcome.STORED: "stored", Outcome.DROPPED: "dropped", Outcome.PRESERVED: "preserved",
                            Outcome.RECYCLED: "recycled"}.get(outcome, "preserved"))
                if outcome == Outcome.UNAVAILABLE:
                    use_model = False
                    continue
                if outcome in (Outcome.PRESERVED, Outcome.RECYCLED):
                    exhausted = [dict(r) for r in store.db.execute(
                        f"SELECT * FROM pending_messages WHERE id IN ({','.join('?' * len(msgs))}) AND retry_count >= ?",
                        (*[m["id"] for m in msgs], max_retries))]
                    if exhausted:  # keep capture: derive what the model could not record
                        claimed = store.claim_ids([m["id"] for m in exhausted])
                        if claimed:
                            self.derive(store, session, claimed)
                        continue
                    if outcome == Outcome.RECYCLED:
                        conv = store.get_conversation(sid)
                        if conv["paused_until_epoch"]:
                            return "deferred"
                        continue
                    return "deferred"
            return "done"
        finally:
            store.close()

    # ---- the whole queue ------------------------------------------------------------------------------
    def drain(self, stop_event: threading.Event | None = None) -> dict:
        llm = None
        router = self.router
        if router is None:
            router = make_router(self.root)
        if router is not None and getattr(router, "available", False):
            llm = ObserverLLM(router)
        store = self._store()
        try:
            store.reset_to_pending()  # the lock is held: nothing else is processing
        finally:
            store.close()
        deferred: set[int] = set()
        workers = max(1, int(self.settings.get("max_concurrent_sessions") or 1))
        for _ in range(MAX_PASSES):
            if stop_event is not None and stop_event.is_set():
                break
            store = self._store()
            try:
                sessions = [s for s in store.sessions_with_pending() if s not in deferred]
            finally:
                store.close()
            if not sessions:
                break
            if workers > 1 and len(sessions) > 1:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    results = list(pool.map(lambda s: (s, self.process_session(s, llm)), sessions))
            else:
                results = [(s, self.process_session(s, llm)) for s in sessions]
            deferred |= {s for s, r in results if r in ("deferred", "missing")}
        if self.settings.get("vectors"):
            store = self._store()
            try:
                from .vectorsync import VectorSync
                self.stats["vectors"] = VectorSync(self.root, store).backfill()
            except Exception as exc:  # noqa: BLE001
                log.warning("vector backfill failed: %s", exc)
            finally:
                store.close()
        self.stats["deferred_sessions"] = len(deferred)
        self.stats["model"] = bool(llm)
        return self.stats


def run_once(root: Path | str, *, router: Any = None, settings: dict | None = None,
             stop_event: threading.Event | None = None, on_event: Callable[[dict], None] | None = None) -> dict:
    """Drain the queue once. Returns counts, or ``{"status": "busy"}`` when another worker holds the lock."""
    root = Path(root)
    total: dict[str, Any] = {}
    for attempt in range(5):
        with worker_lock(root) as held:
            if not held:
                return total or {"status": "busy"}
            worker = Worker(root, router=router, settings=settings, on_event=on_event)
            stats = worker.drain(stop_event)
            for k, v in stats.items():
                total[k] = (total.get(k, 0) + v) if isinstance(v, int) and not isinstance(v, bool) else v
        # Work queued while we held the lock by a hook whose own worker exited on the lock.
        store = Store.open(root)
        try:
            again = store.sessions_with_pending()
        finally:
            store.close()
        if not again or stats.get("deferred_sessions", 0) >= len(again) or (stop_event and stop_event.is_set()):
            break
    total["status"] = "ok"
    return total


def run_worker(root: Path | str, stop_event: threading.Event, *, poll_seconds: float = 1.0, router: Any = None,
               on_event: Callable[[dict], None] | None = None) -> None:
    """Host the worker (e.g. in the Cairn server): drain, then wait for more work until stopped."""
    root = Path(root)
    while not stop_event.is_set():
        try:
            store = Store.open(root)
            try:
                pending = bool(store.sessions_with_pending())
            finally:
                store.close()
            if pending:
                with worker_lock(root, blocking=False) as held:
                    if held:
                        Worker(root, router=router, on_event=on_event).drain(stop_event)
        except Exception as exc:  # noqa: BLE001 - a hosted loop never dies on one bad pass
            log.warning("recall worker pass failed: %s", exc)
        stop_event.wait(poll_seconds)


def status(root: Path | str) -> dict:
    """Queue depth, worker lock holder and observer health."""
    from . import health
    root = Path(root)
    store = Store.open(root)
    try:
        q = store.queue_status()
        holder = None
        lp = lock_path(root)
        from .hooks import worker_running
        running = worker_running(root)
        if running and lp.exists():
            with contextlib.suppress(OSError, ValueError):
                holder = json.loads(lp.read_text(encoding="utf-8") or "{}")
        return {"queue": q, "isProcessing": bool(q["pending"] or q["processing"]) and running,
                "queueDepth": q["pending"] + q["processing"], "worker": {"running": running, "holder": holder},
                "health": health.read(store)}
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cairn.engines.recall.worker",
                                 description="Turn queued session events into observations and summaries.")
    ap.add_argument("--root", default=None, help="repository root (default: the one containing the cwd)")
    ap.add_argument("--once", action="store_true", help="drain the queue once and exit (default)")
    ap.add_argument("--loop", action="store_true", help="keep running and poll for new work")
    ap.add_argument("--poll", type=float, default=1.0)
    ap.add_argument("--no-model", action="store_true", help="derive records without a model")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .projects import find_store_root
    root = Path(args.root) if args.root else find_store_root(os.getcwd())
    if root is None:
        print("no Cairn repository here (run `cairn init`)", file=sys.stderr)
        return 1
    router = _NoModel() if args.no_model else None
    if args.loop:
        stop = threading.Event()
        try:
            run_worker(root, stop, poll_seconds=args.poll, router=router)
        except KeyboardInterrupt:
            stop.set()
        return 0
    res = run_once(root, router=router)
    log.info("recall worker: %s", json.dumps(res, default=str))
    return 0


class _NoModel:
    available = False


if __name__ == "__main__":
    raise SystemExit(main())

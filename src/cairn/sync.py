"""Sync orchestrator: fans out every layer in parallel, then links, checks drift and (optionally)
runs the deep tier under a token budget. Serialised by a lock so git hooks never pile up.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from . import drift, linker
from .engines import history, journal, mapper, specs
from .engines.chronicle import Chronicle
from .router import Budget

Progress = Callable[[str, str, str], None]  # (step, state: start|done|skip|fail, detail)


def _windows_lock(fh, wait: float):
    """Windows stand-in for flock: hold byte 0 of the lock file via msvcrt. Returns the unlock callable,
    or None when another process held it past ``wait``. Never imported on POSIX; the logic is exercised
    everywhere by stubbing the msvcrt module (see tests/test_windows.py), for real on the Windows CI runner."""
    import msvcrt
    deadline = time.monotonic() + wait
    while True:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return lambda: msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:  # contended (EACCES/EDEADLK): the BlockingIOError of this API
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.2)


@contextmanager
def locked(path, wait: float = 0.0):
    """Hold the cross-process sync lock. With ``wait`` > 0, wait that many seconds for an
    already-running sync (the post-commit hook's background sync) to finish instead of skipping."""
    path.parent.mkdir(exist_ok=True)
    fh = open(path, "w")
    release = None
    try:
        try:
            import fcntl
            deadline = time.monotonic() + wait
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        yield False
                        return
                    time.sleep(0.2)
        except ImportError:  # Windows: msvcrt byte-range lock, best effort (flock is POSIX-only)
            release = _windows_lock(fh, wait)
            if release is None:
                yield False
                return
        yield True
    finally:
        if release:
            with contextlib.suppress(OSError):  # close() drops the OS lock anyway
                release()
        fh.close()


def run(cairn, *, deep: bool | None = None, budget: int | None = None, rebuild_map: bool = True,
        progress: Progress | None = None, wait: float = 0.0) -> dict:
    project, brain = cairn.project, cairn.brain
    say = progress or (lambda *a: None)
    results: dict[str, dict] = {}
    with locked(project.dir / "sync.lock", wait=wait) as ok:
        if not ok:
            say("sync", "skip", "another sync is running")
            return {"skipped": True}
        t0 = time.time()

        def step(name, fn):
            say(name, "start", "")
            t = time.time()
            try:
                res = fn() or {}
                res["seconds"] = round(time.time() - t, 2)
                results[name] = res
                say(name, "fail" if res.get("error") else "done", _summary(name, res))
            except Exception as exc:  # one layer failing never blocks the others
                results[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
                say(name, "fail", results[name]["error"])

        def do_map():
            if rebuild_map:
                ok_, summary = mapper.build(project.root)
                if not ok_:
                    return {"error": summary or "map build failed"}
                if summary != "unchanged" or not (project.map_dir / "wiki").is_dir():
                    with contextlib.suppress(Exception):  # one article per community, written without a model
                        from .engines.graph import api as graph_api
                        graph_api.run(["export", "wiki", "--graph", str(graph_api.graph_json(project.root))],
                                       root=project.root)
            idx = cairn.map
            res = linker.ingest_map(project, brain, idx)
            res["nodes"] = len(idx)
            return res

        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(step, "map", do_map),
                    pool.submit(step, "history", lambda: history.ingest(project, brain)),
                    pool.submit(step, "specs", lambda: specs.ingest(project, brain)),
                    pool.submit(step, "sessions", lambda: journal.ingest(project, brain))]
            for f in futs:
                f.result()
        def do_memory():
            import faulthandler
            # the memory step makes model calls too; if one wedges, dump stacks instead of hanging forever
            faulthandler.dump_traceback_later(600, exit=False)
            try:
                res = seed_from_repo(project, brain, cairn.router, store=cairn.memory,
                                     wall_seconds=float(project.cfg("memory.wall_seconds", 300)))
            finally:
                faulthandler.cancel_dump_traceback_later()
            return res
        step("memory", do_memory)
        step("links", lambda: {"links": linker.relink_memories(brain, cairn.map)})

        def do_drift():
            found = drift.check(cairn)
            drift.record(cairn, found)
            return {"findings": len(found), "high": sum(d["severity"] == "high" for d in found)}
        step("drift", do_drift)

        run_deep = cairn.router.deep_enabled() if deep is None else (deep and cairn.router.available)
        if run_deep:
            b = Budget(budget or int(project.cfg("deep.budget_tokens", 150000)))

            def do_deep():
                import faulthandler
                # The deep tier has deadlocked in the field (asyncio loop + worker lock). If it hangs
                # again, dump every thread's stack to stderr instead of silently spinning forever.
                faulthandler.dump_traceback_later(600, exit=False)
                try:
                    res = asyncio.run(Chronicle(project, brain, cairn.router).ingest(
                        b, on_progress=lambda m: say("timeline", "start", m),
                        wall_seconds=float(project.cfg("deep.wall_seconds", 300))))
                finally:
                    faulthandler.cancel_dump_traceback_later()
                linker.relink_memories(brain, cairn.map)
                res["tokens"] = b.used
                return res
            step("timeline", do_deep)
        elif deep:
            say("timeline", "skip", "needs a model key (ANTHROPIC_API_KEY or models.provider)")
        brain.set_kv("sync.last", str(time.time()))
        brain.set_kv("sync.failed", json.dumps({k: v["error"] for k, v in results.items()
                                                 if isinstance(v, dict) and v.get("error")}))
        with contextlib.suppress(Exception):  # agents that read instruction files get the fresh brief
            from .agents import refresh_context
            refresh_context(project, cairn)
        results["seconds"] = round(time.time() - t0, 2)
    _mark_synced(project)
    return results


def _mark_synced(project) -> None:
    """Tell the project list when this repository was last synced (bookkeeping: never fails a sync)."""
    with contextlib.suppress(Exception):
        from .platform import Platform, ServerConfig
        platform = Platform(config=ServerConfig.load())
        try:
            rec = platform.project_by_root(project.root)
            if rec:
                platform.mark_synced(rec.id)
        finally:
            platform.close()


def _summary(name: str, res: dict) -> str:
    if res.get("error"):
        return res["error"]
    if res.get("note"):
        return res["note"]
    return {
        "map": lambda r: f"{r.get('nodes', 0):,} nodes, {r.get('files', 0):,} files",
        "history": lambda r: f"{r.get('commits', 0):,} new commits, {r.get('risky', 0)} warnings",
        "specs": lambda r: f"{r.get('features', 0)} features, {r.get('tasks', 0)} tasks",
        "sessions": lambda r: f"{r.get('observations', 0)} new observations",
        "memory": lambda r: f"{r.get('added', 0)} learned from the repo, {r.get('updated', 0)} updated, "
                            f"{r.get('retired', 0)} retired",
        "links": lambda r: f"{r.get('links', 0)} memory links",
        "drift": lambda r: f"{r.get('findings', 0)} findings ({r.get('high', 0)} high)",
        "timeline": lambda r: f"{r.get('episodes', 0)} episodes, {r.get('facts', 0)} facts, {r.get('tokens', 0):,} tokens",
    }.get(name, lambda r: "")(res)


def spawn_background(project, *args: str) -> None:
    """Fire-and-forget sync (used by git hooks). Never blocks the developer."""
    log = project.dir / "sync.log"
    project.dir.mkdir(exist_ok=True)
    with open(log, "a") as fh:
        kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000008}
        subprocess.Popen([sys.executable, "-m", "cairn", "sync", "--quiet", *args], cwd=str(project.root),
                         stdout=fh, stderr=fh, stdin=subprocess.DEVNULL, **kwargs)

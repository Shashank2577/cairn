"""Sync orchestrator: fans out every layer in parallel, then links, checks drift and (optionally)
runs the deep tier under a token budget. Serialised by a lock so git hooks never pile up.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable

from . import drift, linker
from .engines import history, journal, mapper, specs
from .engines.chronicle import Chronicle
from .router import Budget

Progress = Callable[[str, str, str], None]  # (step, state: start|done|skip|fail, detail)


@contextmanager
def locked(path):
    path.parent.mkdir(exist_ok=True)
    fh = open(path, "w")
    try:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:  # Windows: best effort
            pass
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        fh.close()


def run(cairn, *, deep: bool | None = None, budget: int | None = None, rebuild_map: bool = True,
        progress: Progress | None = None) -> dict:
    project, brain = cairn.project, cairn.brain
    say = progress or (lambda *a: None)
    results: dict[str, dict] = {}
    with locked(project.dir / "sync.lock") as ok:
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
                say(name, "done", _summary(name, res))
            except Exception as exc:  # one layer failing never blocks the others
                results[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
                say(name, "fail", results[name]["error"])

        def do_map():
            if rebuild_map:
                ok_, summary = mapper.build(project.root)
                if not ok_:
                    return {"error": summary or "map build failed"}
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
                res = asyncio.run(Chronicle(project, brain, cairn.router).ingest(
                    b, on_progress=lambda m: say("timeline", "start", m)))
                linker.relink_memories(brain, cairn.map)
                res["tokens"] = b.used
                return res
            step("timeline", do_deep)
        elif deep:
            say("timeline", "skip", "needs a model key (ANTHROPIC_API_KEY or models.provider)")
        brain.set_kv("sync.last", str(time.time()))
        results["seconds"] = round(time.time() - t0, 2)
    return results


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
        "links": lambda r: f"{r.get('links', 0)} cross-layer links",
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

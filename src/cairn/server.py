"""One server for every project: the HTTP API, the live stream and the page.

Local mode serves every repository registered on this machine with no login (loopback only). Team mode
serves the projects of the signed-in user's teams. Platform routes (auth, teams, tokens, projects, audit)
come from ``cairn.platform.web``; per-project routes live under ``/api/p/{pid}`` and are guarded by the
caller's role on that project (404 when the project isn't visible, 403 when the role is too low).
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import os
import re
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, graphviews, sync
from . import drift as drift_mod
from .core import Cairn
from .engines import specs as specs_engine
from .platform import Platform, ServerConfig
from .platform.web import (
    _me,
    _project_json,
    current_principal,
    get_config,
    get_platform,
    install,
    optional_principal,
    require,
)
from .project import DEFAULTS as CONFIG_DEFAULTS, Project
from .router import PRICES_AS_OF, cost_usd

UI_DIR = Path(__file__).resolve().parent / "ui"
log = logging.getLogger("cairn.server")
MAX_EVENTS_BODY = 16 * 2**20  # one pushed batch of session events (the client halves its batch on 413)


# ---- the hub: one Cairn per project, one live channel per project -----------------------------------------
class Hub:
    """Every project this server serves: one Cairn per project (shared by the page and by agents), one live
    channel per project, and the background threads that relay its session store."""

    def __init__(self, platform: Platform):
        self.platform = platform
        self._cairns: dict[str, Cairn] = {}
        self._lock = threading.RLock()
        self.listeners: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._sync: dict[str, str] = {}              # pid -> "running" | "pending"
        self._watch: dict[str, threading.Event] = {}  # pid -> stop event of its session threads
        self.loop: asyncio.AbstractEventLoop | None = None
        self.on_drop: list = []                       # callbacks(pid) when a project goes away
        platform.on_project_deleted(self.drop)

    @property
    def syncing(self) -> set[str]:
        return set(self._sync)

    def cairn(self, pid: str, surface: str = "ui") -> Cairn:
        """The project's Cairn. Other surfaces (agents over MCP) share its stores: one read model, one memory
        engine and one set of indexes per project, so writers never overwrite each other's in-memory copies."""
        base = self._open(pid)
        if surface == "ui":
            return base
        key = f"{pid}\x00{surface}"
        with self._lock:
            c = self._cairns.get(key)
            if c is None or c.brain is not base.brain:
                c = copy.copy(base)  # same project, brain, router and memory; only the accounting label differs
                c.surface = surface
                self._cairns[key] = c
            return c

    def _open(self, pid: str) -> Cairn:
        with self._lock:
            c = self._cairns.get(pid)
            if c is not None:
                return c
            rec = self.platform.get_project(pid)
            if rec is None or not rec.root:
                raise HTTPException(404, "project not found")
            root = Path(rec.root)
            if not root.is_dir():
                raise HTTPException(409, "the project's files are not available on this server yet")
            proj = Project(root=root)  # exactly the registered folder: a sub-folder project keeps its own stores
            if self.platform.config.mode != "local":
                proj.trusted = False       # network destinations and shared namespaces come from the operator
                proj.scope_id = rec.id     # folder names can collide; the platform id can't
            proj.reload()
            proj.ensure_dir()
            c = Cairn(proj)
            c.surface = "ui"
            self._cairns[pid] = c
            return c

    def reconfigure(self, pid: str) -> None:
        """Settings or files changed: reload them into the live Cairn instead of replacing it (in-flight requests
        and agents keep working, and no second copy of the stores is ever opened)."""
        with self._lock:
            c = self._cairns.get(pid)
            if c is None:
                return
            c.reconfigure()
            for key in [k for k in self._cairns if k.startswith(f"{pid}\x00")]:
                self._cairns.pop(key, None)  # surface views are rebuilt from the refreshed base on next use

    def drop(self, pid: str) -> None:
        """The project was deleted: stop its threads and agents endpoint, and close its stores."""
        stop = self._watch.pop(pid, None)
        if stop:
            stop.set()
        with self._lock:
            base = self._cairns.pop(pid, None)
            for key in [k for k in self._cairns if k.startswith(f"{pid}\x00")]:
                self._cairns.pop(key, None)
        for fn in list(self.on_drop):
            with contextlib.suppress(Exception):
                fn(pid)
        if base is not None:
            base.close()

    def broadcast(self, pid: str, msg: dict) -> None:
        loop = self.loop
        if loop is None:
            return
        for q in list(self.listeners.get(pid, ())):
            loop.call_soon_threadsafe(q.put_nowait, msg)

    def run_sync(self, pid: str, deep: bool | None = None) -> dict:
        """Sync a project. A request while one runs is not dropped: it runs once more right after."""
        with self._lock:
            if pid in self._sync:
                self._sync[pid] = "pending"
                return {"queued": True}
            self._sync[pid] = "running"
        res: dict = {}
        try:
            while True:
                def progress(step: str, state: str, detail: str) -> None:
                    if step == "links":
                        return  # part of the memory step as far as the page is concerned
                    self.broadcast(pid, {"type": "sync", "step": STEP_NAMES.get(step, step), "state": state,
                                         "detail": detail})
                res = sync.run(self.cairn(pid), deep=deep, progress=progress)
                if not res.get("skipped"):
                    self.broadcast(pid, {"type": "sync", "step": "sync", "state": "done",
                                         "detail": json.dumps({"seconds": res.get("seconds")})})
                with self._lock:
                    if self._sync.get(pid) != "pending":
                        break
                    self._sync[pid] = "running"
            return res
        finally:
            with self._lock:
                self._sync.pop(pid, None)

    def watch_sessions(self, pid: str) -> None:
        """Relay the project's session store to its live channel (observations, summaries, prompts written by
        hooks and workers in any process), and host its worker when automatic processing is on."""
        with self._lock:
            if pid in self._watch:
                return
            root = self.cairn(pid).project.root  # may raise (404/409): nothing is marked as watched then
            stop = threading.Event()
            self._watch[pid] = stop

        def relay() -> None:
            from .engines.recall import api as recall_api
            for ev in recall_api.stream(root, stop):
                if ev.get("type") in ("observation", "summary", "prompt", "status"):
                    self.broadcast(pid, ev)
        threading.Thread(target=relay, name=f"sessions-live-{pid}", daemon=True).start()
        from .engines.recall import settings as recall_settings
        if recall_settings.load(root).get("worker_spawn", True):
            from .engines.recall import api as recall_api
            threading.Thread(target=recall_api.host_worker, args=(root, stop, lambda ev: None),
                             kwargs={"poll_seconds": 1.0}, name=f"sessions-worker-{pid}", daemon=True).start()

    def stop_all(self) -> None:
        for stop in list(self._watch.values()):
            stop.set()

    def on_platform_sync(self, record, reason: str) -> None:
        """Called by the platform after a webhook, manual refresh or new registration."""
        self.reconfigure(record.id)  # a fresh clone or pull may bring new settings
        self.run_sync(record.id)


# ---- the system diagram: drawn once per model, not once per request -------------------------------------
@lru_cache(maxsize=32)
def _system_stamp(root: str) -> tuple:
    """What the picture would look like right now, as a cheap comparable stamp.

    Two parts: the stored model's own ``COUNT`` and ``MAX(updated_at)`` (the signal the session brief
    uses), and the repository's HEAD. Both are needed. A repository whose sync has stored a model changes
    when a claim changes; one that has not stored anything is rebuilt from the working tree on every read,
    and that result cannot change until the commit does. Either way the stamp moves exactly when the
    picture would.
    """
    from .project import Project
    from .system.build import head_commit
    proj = Project.discover(Path(root))
    if proj is None:
        return ()
    try:
        row = Cairn(proj).brain.one("SELECT COUNT(*) n, MAX(updated_at) t FROM sm_element")
        stored = (row["n"], row["t"]) if row is not None else (0, None)
    except Exception:
        stored = (0, None)
    return stored + (head_commit(proj.root),)


@lru_cache(maxsize=32)
def _map_stamp(root: str) -> tuple:
    """What the code map would look like right now, as a cheap comparable stamp.

    The layer map is rebuilt from the working tree on every read, so the result cannot change until the
    commit does; the link count catches a sync that has not been committed yet.
    """
    from .project import Project
    from .system.build import head_commit
    proj = Project.discover(Path(root))
    if proj is None:
        return ()
    try:
        row = Cairn(proj).brain.one("SELECT COUNT(*) n FROM links")
        stored = (row["n"],) if row is not None else (0,)
    except Exception:
        stored = (0,)
    return stored + (head_commit(proj.root),)


@lru_cache(maxsize=32)
def _products_by_project_key(roots: tuple, heads: tuple) -> dict:
    """Which declared product each project root belongs to, resolved across all of them at once.

    Only the repository that owns a ``system.yaml`` declares the product, so this has to see every
    project to answer for the siblings; a per-project read finds one member out of five. Members are
    counted per product across every declaration that names it, so a product declared by two repositories
    is not counted twice and a member that is not on this machine still counts. Keyed on the roots and
    their HEADs because that is all it reads.
    """
    from .engines.graph.repos import _product
    size: dict[str, set] = defaultdict(set)
    desc: dict[str, str] = {}
    system_of: dict[str, str] = {}          # member root -> product
    anchor: dict[str, str] = {}             # product -> root that owns the declaration
    for root in roots:
        prod = _product(Path(root))
        if not prod:
            continue
        system = prod["system"]
        size[system].update(m["root"] for m in prod["members"])
        if prod.get("description") and not desc.get(system):
            desc[system] = prod["description"]
        # The repository that owns system.yaml is where the linked product is stored and built, so it
        # is the one to open when someone picks the product rather than a repository inside it.
        anchor.setdefault(system, root)
        for m in prod["members"]:
            system_of.setdefault(m["root"], system)
    return {r: {"system": s, "members": len(size[s]), "description": desc.get(s),
                "anchor": anchor.get(s)}
            for r, s in system_of.items()}


def _products_by_project(records) -> dict:
    """``{project id: {"system", "members"}}`` for the project switcher, so a product is one entry
    rather than five unrelated repositories."""
    roots = tuple(str(Path(r.root).resolve()) for r in records if r.root and Path(r.root).is_dir())
    if not roots:
        return {}
    from .system.build import head_commit
    try:
        heads = tuple(head_commit(Path(r)) for r in roots)
    except Exception:
        heads = ()
    try:
        by_root = _products_by_project_key(roots, heads)
    except Exception:
        return {}
    out = {}
    for r in records:
        if not r.root:
            continue
        hit = by_root.get(str(Path(r.root).resolve()))
        if hit:
            entry = {"system": hit["system"], "members": hit["members"], "description": hit.get("description")}
            # Which repository to open for the product as a whole: the one that owns system.yaml.
            for other in records:
                if other.root and hit.get("anchor") and str(Path(other.root).resolve()) == hit["anchor"]:
                    entry["anchor"] = other.id
                    break
            out[r.id] = entry
    return out


@lru_cache(maxsize=16)
def _architecture_cached(root: str, stamp: tuple) -> dict:
    """The dependency-layer map for one repository, cached on its stamp.

    ``architecture()`` rebuilds the whole file graph and re-rolls it into layers — fifty seconds for a
    repository this size, paid on every page load and every re-render. It is pure with respect to the
    stamp, so a reader moving between Map tabs pays it once and then nothing.
    """
    from .project import Project
    proj = Project.discover(Path(root))
    if proj is None:
        raise HTTPException(404, "no such project")
    return Cairn(proj).architecture()


@lru_cache(maxsize=64)
def _system_diagram_cached(root: str, level: str, scope: str | None, stamp: tuple) -> tuple[str, str]:
    """(svg, description json) for one level of one model.

    Drawing a level runs the linker across every repository: seconds of work for a picture that cannot
    change until the stamp does. Keying on the stamp means a reader moving between levels pays it once and
    then nothing, and a sync or a commit that changes a claim invalidates every level at once.
    """
    from .system.diagram.svg import render_inline
    from .system.views import view as system_view_fn
    try:
        desc = system_view_fn(Path(root), level, scope)
    except ValueError:
        if not scope:
            raise
        # A saved link names a scope from an older model (a flow whose id embeds a commit hash, say). Draw the
        # current model's own view instead of nothing.
        desc = system_view_fn(Path(root), level, None)
        desc["stale_scope"] = scope
    if level == "flows" and not scope:
        from .system.export import _shapes
        from .system.flows import flows as all_flows
        from .system.link import link
        built = all_flows(Path(root), link(Path(root)))
        if len(built) > 1 and len(_shapes(built)) == 1:
            raise ValueError(f"all {len(built)} entry points reach the same containers and stores, so a flow "
                             "would be the same picture for each. Cairn cannot tell them apart without "
                             "following calls inside the code; flows help when a request crosses services.")
    if not desc.get("elements"):
        raise ValueError("nothing to draw at this level yet; run a sync so the system model is built")
    return render_inline(desc), json.dumps(desc, default=str)


_DOC_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.(md|html)")


def _written_docs(root: Path) -> list[dict]:
    """The documentation already in the repository's ``docs/`` folder (what a documentation run left there).

    Each Markdown file is listed once, with its styled HTML twin when there is one (``DATA_FLOW.md`` and
    ``data-flow.html``). Nothing here is computed: Cairn shows what is in the folder.
    """
    folder = root / "docs"
    if not folder.is_dir():
        return []
    out = []
    for md in sorted(folder.glob("*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), md.stem)
        twin = "index.html" if md.stem.lower() == "readme" else md.stem.lower().replace("_", "-") + ".html"
        out.append({"name": md.name, "title": title, "html": twin if (folder / twin).is_file() else None,
                    "bytes": md.stat().st_size, "index": md.stem.lower() == "readme"})
    return sorted(out, key=lambda d: (not d["index"], d["name"]))


_MERMAID_FENCE = re.compile(r"^```mermaid[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)


def _draw_mermaid(md: str) -> tuple[str, list[str]]:
    """Replace the Mermaid blocks Cairn's own reader understands with ``@@diagram:N@@`` markers.

    Returns the Markdown and the drawn SVGs. A block it cannot read (a syntax outside the flowchart,
    sequence and C4 subset) stays a code block, so nothing is hidden. Drawn by the diagram standard's
    renderer, with no browser library and no model call.
    """
    from .system.diagram.mermaid_in import parse
    from .system.diagram.svg import render_inline
    svgs: list[str] = []

    def one(m: re.Match) -> str:
        try:
            svgs.append(render_inline(parse(m.group(1)).description))
        except Exception:
            return m.group(0)
        return f"\n@@diagram:{len(svgs) - 1}@@\n"
    return _MERMAID_FENCE.sub(one, md), svgs


@lru_cache(maxsize=8)
def _docs_bundle_cached(root: str, stamp: tuple):
    """The architecture documents of one model, built once per stamp like the diagrams."""
    from .system.export import bundle
    return bundle(Path(root))


# ---- per-project routes ------------------------------------------------------------------------------------
def project_router(hub: Hub) -> APIRouter:
    r = APIRouter(prefix="/api/p/{pid}")
    read = [Depends(require("project.read"))]
    write = [Depends(require("project.write"))]
    syncing = [Depends(require("project.sync"))]

    def audit(pid: str, principal, action: str, target: str = "", **detail) -> None:
        """Record a change made through the page or API in the team's audit log (never fails the change)."""
        with contextlib.suppress(Exception):
            rec = hub.platform.get_project(pid)
            hub.platform.audit(action, actor=principal, target=target, team_id=rec.team_id if rec else None,
                               project_id=pid, detail=detail or None)

    def c(pid: str) -> Cairn:
        return hub.cairn(pid)

    def pack(cairn: Cairn, p) -> dict:
        src, n = cairn.source_cost(p.data.get("evidence_files", []))
        return {"title": p.title, "header": p.header, "sections": p.sections(), "markdown": p.render(),
                "tokens": p.accounting(), "source": {"tokens": src, "files": n}, **p.data}

    # overview & search
    @r.get("/overview", dependencies=read)
    def overview(pid: str):
        cairn = c(pid)
        o = cairn.overview()
        o["syncing"] = pid in hub.syncing
        deep = cairn.router.deep_state()
        o["models"] = {"available": cairn.router.available, "provider": cairn.router.provider,
                       "deep": deep["enabled"], "deep_state": deep}
        from .budgets import budgets
        o["budgets"] = budgets(cairn.project, cairn.brain)
        from . import agents as agents_mod
        from .engines import journal
        o["capture"] = {"on": journal.installed(cairn.project),
                        "agents": [agents_mod.AGENTS[a] for a in agents_mod.installed(cairn.project)]}
        return o

    @r.get("/search", dependencies=read)
    def search(pid: str, q: str, kinds: str | None = None, limit: int = 20):
        cairn = c(pid)
        hits = cairn.search(q, kinds.split(",") if kinds else None, limit)
        idx = cairn.map
        for h in hits:
            h["kind"] = KIND_NAMES.get(h.get("kind"), h.get("kind"))
            if h["id"].startswith("symbol:"):
                h["community"] = idx.nodes.get(h["id"][7:], {}).get("community")
            elif h["id"].startswith("file:"):
                ids = idx.by_file.get(h["id"][5:], [])
                h["community"] = idx.nodes[ids[0]].get("community") if ids else None
        return hits

    @r.get("/entity", dependencies=read)
    def entity(pid: str, id: str):
        cairn = c(pid)
        ent = cairn.brain.entity(id)
        if not ent and not id.startswith(("symbol:", "file:")):
            raise HTTPException(404, "not found")
        return {"entity": ent or {"id": id, "kind": id.split(":")[0], "name": id.split(":", 1)[1], "meta": {}},
                "links": cairn.brain.links_from(id, limit=60) + cairn.brain.links_to(id, limit=60)}

    @r.post("/sync")
    def start_sync(pid: str, payload: dict = Body(default={}), principal=Depends(require("project.sync"))):
        if pid in hub.syncing:
            return {"started": False, "reason": "already running"}
        c(pid)  # 404/409 before we answer
        threading.Thread(target=hub.run_sync, args=(pid, payload.get("deep")), daemon=True).start()
        audit(pid, principal, "project.sync", pid, deep=bool(payload.get("deep")))
        return {"started": True}

    @r.get("/stream", dependencies=read)
    async def stream(pid: str):
        await asyncio.to_thread(hub.watch_sessions, pid)
        q: asyncio.Queue = asyncio.Queue()
        hub.listeners[pid].append(q)

        async def gen():
            try:
                yield "retry: 3000\n\n"
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=20)
                        yield f"data: {json.dumps(msg, default=str)}\n\n"
                    except TimeoutError:
                        yield ": ping\n\n"
            finally:
                hub.listeners[pid].remove(q)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @r.get("/savings", dependencies=read)
    def savings(pid: str, limit: int = 60):
        return c(pid).savings(limit)

    @r.get("/models", dependencies=read)
    def models(pid: str, since: float = 0):
        router = c(pid).router
        calls = [dict(r) for r in c(pid).brain.q(
            "SELECT ts, task, tier, model, input_tokens, output_tokens, cache_read, cache_write, "
            "input_tokens + cache_read + cache_write AS input_total, ok FROM ledger WHERE ts>=? "
            "ORDER BY ts DESC LIMIT 300", (since,))]
        totals = c(pid).brain.ledger(since)
        for t in totals:
            t["cost_usd"] = cost_usd(t["model"], t["input"] or 0, t["output"] or 0, t["cache_read"] or 0, t["cache_write"] or 0)
        for r in calls:
            r["cost_usd"] = cost_usd(r["model"], r["input_tokens"] or 0, r["output_tokens"] or 0, r["cache_read"] or 0, r["cache_write"] or 0)
        priced = [t["cost_usd"] for t in totals if t["cost_usd"] is not None]
        return {"available": router.available, "provider": router.provider, "table": router.table(),
                "totals": totals, "ledger": calls, "priced_as_of": PRICES_AS_OF,
                "cost_usd": sum(priced) if priced else None, "unpriced_models": sorted({t["model"] for t in totals if t["cost_usd"] is None})}

    def nested_settings(proj: Project) -> dict:
        from .project import OPERATOR_KEYS
        out: dict = {}
        for k in SETTINGS:
            section, key = k.split(".", 1)
            out.setdefault(section, {})[key] = proj.cfg(k)
        # what this server lets a project change: the page disables the rest and says who sets them
        out["locked"] = sorted(k for k in SETTINGS if k in OPERATOR_KEYS and not proj.trusted)
        out["rules"] = SETTING_RULES_TEXT
        out["defaults"] = {k: CONFIG_DEFAULTS[k.split(".", 1)[0]].get(k.split(".", 1)[1]) for k in SETTINGS}  # what an unset key means
        return out

    @r.get("/settings", dependencies=read)
    def get_settings(pid: str):
        return nested_settings(c(pid).project)

    @r.patch("/settings")
    def patch_settings(pid: str, payload: dict = Body(...), principal=Depends(require("project.admin"))):
        from .project import OPERATOR_KEYS, _toml_literal
        proj = c(pid).project
        for key, value in payload.items():
            if key not in SETTINGS:
                raise HTTPException(400, f"unknown setting: {key}")
            if key in OPERATOR_KEYS and not proj.trusted:
                raise HTTPException(403, f"{key} is set by the server's operator (server.toml), not per project")
            expected = SETTINGS[key]
            if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
                raise HTTPException(400, f"{key} must be {expected.__name__ if isinstance(expected, type) else 'text or a boolean'}")
            problem = setting_problem(key, value)
            if problem:
                raise HTTPException(400, problem)
            try:
                _toml_literal(key, value)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        for key, value in payload.items():
            try:  # a server's settings live outside the committed file, so a git refresh never discards them
                proj.set_cfg(key, value, local=not proj.trusted)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        hub.reconfigure(pid)  # providers and budgets are read into the router
        audit(pid, principal, "project.settings", pid, keys=sorted(payload))  # names only: values may be URLs
        return nested_settings(c(pid).project)

    @r.get("/brief", dependencies=read)
    def brief(pid: str):
        return {"brief": c(pid).brief()}

    # impact & why
    @r.get("/impact", dependencies=read)
    def impact(pid: str, target: str, depth: int = 2, budget: int = 1500):
        cairn = c(pid)
        try:
            return pack(cairn, cairn.impact(target, depth, budget))
        except ValueError as exc:  # resolve refuses degenerate targets ("." etc.)
            raise HTTPException(400, str(exc)) from exc

    @r.get("/why", dependencies=read)
    def why(pid: str, target: str, budget: int = 1200):
        cairn = c(pid)
        try:
            return pack(cairn, cairn.why(target, budget))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @r.post("/ask", dependencies=read)
    def ask(pid: str, payload: AskIn):
        return c(pid).ask(payload.question, payload.budget, payload.llm)

    @r.post("/narrate", dependencies=read)
    def narrate(pid: str, payload: NarrateIn, request: Request):
        """A model's explanation of a question, an impact or a why, streamed as it is written: one JSON event
        per line (`start`, `text`…, then `done` or `error`)."""
        cairn = c(pid)
        if not cairn.router.available:
            raise HTTPException(409, "Explaining needs a model. Set one up in Settings.")
        cancel = threading.Event()
        if payload.kind == "ask":
            events = cairn.ask_stream(payload.text, payload.budget, cancel)
        elif payload.kind == "impact":
            events = cairn.narrate_stream(cairn.impact(payload.text, 2, payload.budget or 1500), "impact", cancel)
        else:
            events = cairn.narrate_stream(cairn.why(payload.text, payload.budget or 1200), "why", cancel)

        async def body():
            try:
                while True:
                    pending = asyncio.ensure_future(asyncio.to_thread(next, events, None))
                    while not pending.done():  # a model can think for a while before writing: keep checking
                        await asyncio.wait({pending}, timeout=0.5)
                        if not pending.done() and await request.is_disconnected():
                            cancel.set()
                    event = pending.result()
                    if event is None:
                        break
                    yield json.dumps(event) + "\n"
            finally:  # finished, or the page went away (Stop, closed tab): stop the model call either way
                cancel.set()
        return StreamingResponse(body(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    # graph (code & document knowledge graph)
    @r.get("/graph/architecture", dependencies=read)
    def architecture(pid: str):
        root = c(pid).project.root
        return _architecture_cached(str(root), _map_stamp(str(root)))

    @r.get("/graph/summary", dependencies=read)
    def graph_summary(pid: str):
        cairn = c(pid)
        mj = cairn.project.map_json
        return graphviews.summary(cairn.map, mj.stat().st_mtime if mj.exists() else None)

    @r.get("/graph/data", dependencies=read)
    def graph_data(pid: str, scope: str = "all", limit: int = 600):
        return graphviews.data(c(pid).map, scope, max(10, min(limit, 5000)))

    @r.get("/graph/browse", dependencies=read)
    def graph_browse(pid: str, path: str = "", offset: int = 0, limit: int = graphviews.ROW_CAP, sort: str = "name"):
        """One level of the repository, with what Cairn knows about each row. Never the whole tree."""
        cairn = c(pid)
        facts = {"commits": {}, "fixes": {}, "specs": {}, "memories": {}}
        for r_ in cairn.brain.q("SELECT path, commits, risky FROM filestats"):
            facts["commits"][r_["path"]], facts["fixes"][r_["path"]] = r_["commits"] or 0, r_["risky"] or 0
        for name, sql in (("specs", "SELECT dst, COUNT(DISTINCT src) n FROM links WHERE rel='owns' AND dst LIKE 'file:%' GROUP BY dst"),
                          ("memories", "SELECT dst, COUNT(DISTINCT src) n FROM links WHERE rel='mentions' AND src LIKE 'memory:%' "
                                       "AND dst LIKE 'file:%' GROUP BY dst")):
            facts[name] = {r_["dst"][5:]: r_["n"] for r_ in cairn.brain.q(sql)}
        return graphviews.browse(cairn.map, path, facts, max(0, offset), limit, "risk" if sort == "risk" else "name")

    @r.get("/graph/find", dependencies=read)
    def graph_find(pid: str, q: str = ""):
        return graphviews.find(c(pid).map, q)

    @r.get("/graph/focus", dependencies=read)
    def graph_focus(pid: str, id: str, depth: int = 2, limit: int = 150):
        out = graphviews.focus(c(pid).map, id, depth, max(10, min(limit, 400)))
        if out is None:
            raise HTTPException(404, "no such node")
        return out

    @r.get("/graph/node", dependencies=read)
    def graph_node(pid: str, id: str):
        cairn = c(pid)
        out = graphviews.node(cairn.map, id)
        if out is None:
            raise HTTPException(404, "no such node")
        from .engines.graph import api as graph_api
        out["explain_text"] = graph_api.explain(cairn.project.root, id)  # by id: labels aren't unique
        return out

    @r.get("/graph/path", dependencies=read)
    def graph_path(pid: str, a: str, b: str):
        out = graphviews.path(c(pid).map, a, b)
        if out["found"]:
            steps = [out["nodes"][0]["label"]]
            for link, node in zip(out["links"], out["nodes"][1:]):
                steps.append(f"—{link['rel']}→ {node['label']}" if link["source"] != node["id"]
                             else f"←{link['rel']}— {node['label']}")
            out["text"] = f"{len(out['links'])} step{'s' if len(out['links']) != 1 else ''}: " + " ".join(steps)
        else:
            missing = out.get("missing") or []
            idx = c(pid).map
            name = lambda n: idx.label(n) if n in idx.nodes else n  # noqa: E731 - node names, not ids
            out["text"] = (f"Couldn't find {', '.join(missing)} in the map." if missing
                           else f"No connection between {name(a)} and {name(b)} within 8 steps.")
        return out

    @r.get("/graph/query", dependencies=read)
    def graph_query(pid: str, q: str, budget: int = 2000):
        cairn = c(pid)
        from .engines.graph import api as graph_api
        text = graph_api.query(cairn.project.root, q, budget=budget)
        ids = graphviews.nodes_in_text(cairn.map, text) or cairn.map.resolve(q, limit=12)
        labels = [cairn.map.label(i) for i in ids[:6]]
        summary = (f"{len(ids)} related node{'s' if len(ids) != 1 else ''}: {', '.join(labels)}"
                   + ("…" if len(ids) > 6 else "")) if ids else "Nothing in the map matches that question."
        communities = {i: cairn.map.nodes.get(i, {}).get("community") for i in ids}
        return {"summary": summary, "text": text, "nodes": ids, "node_communities": communities}

    @r.get("/system/view", dependencies=read)
    def system_view(pid: str, level: str = "containers", scope: str | None = None):
        """One level of the computed system model as a diagram description (spec 002, T024)."""
        root = str(c(pid).project.root)
        try:
            _svg, payload = _system_diagram_cached(root, level, scope, _system_stamp(root))
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return Response(payload, media_type="application/json",
                        headers={"Cache-Control": "no-store"})

    @r.get("/system/svg", dependencies=read)
    def system_svg(pid: str, level: str = "containers", scope: str | None = None):
        """The same view drawn by the diagram standard's own renderer, for the page to embed.

        The page draws this rather than laying out its own: one renderer means the picture on screen and
        the exported file cannot disagree. Colours resolve against the page's ``--dg-*`` tokens, so the
        theme switch drives the diagram.
        """
        root = str(c(pid).project.root)
        try:
            svg, _ = _system_diagram_cached(root, level, scope, _system_stamp(root))
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return Response(svg, media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @r.get("/system/flows", dependencies=read)
    def system_flows(pid: str, scope: str | None = None):
        """Every flow this product has, busiest first: the ids and titles the page picks between."""
        from .system.flows import flows as all_flows
        from .system.link import link
        root = c(pid).project.root
        try:
            built = all_flows(root, link(root), scope=scope)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        # One entry per distinct set of steps: 119 endpoints that all draw the same picture are one choice.
        # `scope` is what the view endpoint accepts (the entry point's name), not the description's scope line.
        from .system.export import _shapes
        from .system.flows import entry_points
        names = sorted({e.name for e in entry_points(link(root))}, key=len, reverse=True)
        groups = _shapes(built)
        if len(groups) == 1 and len(built) > 1:
            return []      # every entry point draws the same picture: nothing to choose between, no tab
        out = []
        for group in groups:
            d = group[0]
            name = next((n for n in names if n in d["title"]), d["title"])
            more = len(group) - 1
            out.append({"title": d["title"] + (f" (+{more} more with the same steps)" if more else ""),
                        "scope": name, "count": len(group)})
        return out

    @r.get("/docs", dependencies=read)
    def docs_list(pid: str):
        """The architecture documents computed live from the current model, and how stale that model is."""
        from .system.export import MAX_FLOW_DOCS, commits_behind
        root = c(pid).project.root
        b = _docs_bundle_cached(str(root), _system_stamp(str(root)))
        return {"commit": b.commit[:12], "head": b.head[:12], "behind": commits_behind(root, b.commit),
                "system": b.product.get("system"), "flow_entries": b.flow_entries, "flow_shapes": b.flow_shapes,
                "max_flow_docs": MAX_FLOW_DOCS,
                "narrate_tokens": int(c(pid).project.cfg("system.narrate_tokens", 4000)),
                "export_dir": "docs/architecture", "written": _written_docs(root),
                "docs": [{"slug": d.slug, "title": d.title, "level": d.level, "summary": d.summary,
                          "diagram": d.desc is not None} for d in b.docs]}

    @r.get("/docs/generate", dependencies=read)
    def docs_generate_status(pid: str):
        """The documentation run for this project: idle, running, done or failed, with its cost when done."""
        from . import docgen
        return {**docgen.status(c(pid).project.root), "models": {k: docgen.ESTIMATE[k] for k in docgen.MODELS},
                "budget_usd": docgen.DEFAULT_BUDGET_USD, "skill": docgen.DEFAULT_SKILL}

    @r.post("/docs/generate")
    def docs_generate(pid: str, payload: dict = Body(default={}), principal=Depends(require("project.write"))):
        """Start the documentation skill in this repository (opt-in: it spends the caller's model quota and writes ``docs/``)."""
        from . import docgen
        root = c(pid).project.root
        try:
            job = docgen.start(root, payload.get("model") or "haiku", skill=c(pid).project.cfg("docs.skill", docgen.DEFAULT_SKILL))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        audit(pid, principal, "docs.generate", pid, model=job["model"], budget_usd=job["budget_usd"])
        return job

    @r.post("/docs/generate/cancel")
    def docs_generate_cancel(pid: str, principal=Depends(require("project.write"))):
        from . import docgen
        return docgen.cancel(c(pid).project.root)

    @r.get("/docs/files/{name}", dependencies=read)
    def docs_file(pid: str, name: str):
        """One file of the repository's ``docs/`` folder: the styled HTML page for a frame, or the Markdown."""
        folder = (c(pid).project.root / "docs").resolve()
        path = (folder / name).resolve()
        if not _DOC_FILE.fullmatch(name) or path.parent != folder or not path.is_file():
            raise HTTPException(404, "no such document")
        text = path.read_text(encoding="utf-8", errors="replace")
        if name.endswith(".html"):   # the page is data: no script, nothing fetched, shown in a sandboxed frame
            return Response(text, media_type="text/html; charset=utf-8", headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
                                           "frame-ancestors 'self'",
                "X-Frame-Options": "SAMEORIGIN", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
        md, svgs = _draw_mermaid(text)
        return {"name": name, "md": md, "diagrams": svgs}

    @r.get("/docs/{slug}", dependencies=read)
    def docs_one(pid: str, slug: str):
        """One document: its Markdown, its diagram drawn for the page, and the evidence it cites."""
        from .system.diagram.svg import render_inline
        root = c(pid).project.root
        b = _docs_bundle_cached(str(root), _system_stamp(str(root)))
        doc = next((d for d in b.docs if d.slug == slug), None)
        if doc is None:
            raise HTTPException(404, "no such document")
        refs = []
        for ref in doc.evidence:
            path, _, line = ref.rpartition(":")
            if not path or not line.isdigit():
                path, line = ref, ""
            refs.append({"ref": ref, "file": path.split("/", 1)[1] if "/" in path else path,
                         "line": int(line) if line else None})
        return {"slug": doc.slug, "title": doc.title, "level": doc.level, "md": doc.md, "commit": b.commit,
                "svg": render_inline(doc.desc) if doc.desc else "", "evidence": refs}

    @r.post("/docs/export")
    def docs_export(pid: str, payload: dict = Body(default={}), principal=Depends(require("project.write"))):
        """Write the document set into the repository (``docs/architecture``); ``narrate`` is the only way
        a model is called, and only when the caller asks."""
        from .system.export import export
        root = c(pid).project.root
        narrate = bool(payload.get("narrate"))
        result = export(root, root / "docs" / "architecture", narrate=narrate)
        audit(pid, principal, "docs.export", pid, narrate=narrate)
        return {"written": len(result["written"]), "commit": result["commit"], "narrated": result["narrated"],
                "dir": "docs/architecture"}

    @r.post("/docs/zip")
    def docs_zip(pid: str, request: Request, payload: dict = Body(default={}),
                 principal=Depends(require("project.read"))):
        """The same document set as a zip download, built in a temporary folder so nothing is written to the repository."""
        import io
        import tempfile
        import zipfile
        from .system.export import export
        narrate = bool(payload.get("narrate"))
        if narrate:   # narrating spends model tokens: the same permission as writing
            get_platform(request).require(principal, "project.write", project_id=pid)
        root = c(pid).project.root
        with tempfile.TemporaryDirectory() as tmp:
            result = export(root, Path(tmp), narrate=narrate)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for name in result["written"]:
                    z.write(Path(tmp) / name, f"architecture/{name}")
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="architecture.zip"',
                                 "Cache-Control": "no-store"})

    @r.get("/diagram/tokens", dependencies=read)
    def diagram_tokens(pid: str):
        """The canonical diagram tokens, so the UI's diagrams match the exports (spec 002, T024)."""
        from .system.diagram import load_tokens
        return load_tokens()

    @r.get("/graph/views", dependencies=read)
    def graph_views(pid: str):
        from .engines.graph import api as graph_api
        if not graph_api.graph_json(c(pid).project.root).is_file():
            return []  # nothing to draw yet: the page says "run a sync" instead of showing blank frames
        return [{"kind": k, "title": t} for k, t in GRAPH_VIEWS.items()]

    @r.get("/graph/views/{kind}", dependencies=read)
    def graph_view(pid: str, kind: str, limit: int | None = None, theme: str | None = None):
        """The graph engine's own interactive pages, for the page's Graph tab.

        These are the engine's views of the code map — every node, not the system model's dozen — so they
        answer "what is in here" where the System tab answers "what is this". They are self-contained
        pages rendered by the engine and shown in a sandboxed frame; nothing is drawn here.
        """
        from .engines.graph import api as graph_api
        root = c(pid).project.root
        render = {"graph": lambda: graph_api.graph_view_html(root, asset_base=GRAPH_ASSETS, node_limit=limit),
                  "tree": lambda: graph_api.tree_view_html(root, asset_base=GRAPH_ASSETS),
                  "callflow": lambda: graph_api.callflow_view_html(root, asset_base=GRAPH_ASSETS)}.get(kind)
        if render is None:
            raise HTTPException(404, "no such view")
        html = render()
        if not html:
            raise HTTPException(404, "not enough of a map to draw this view yet; run a sync")
        from .graph_theme import themed
        html = themed(html, kind, theme or "")   # the page's own colours, not the engine's
        # Shown in a frame on this page: allow same-origin framing only.
        return HTMLResponse(html, headers={"X-Frame-Options": "SAMEORIGIN",
                                           "Content-Security-Policy": "frame-ancestors 'self'; sandbox allow-scripts allow-popups"})

    @r.get("/graph/report", dependencies=read)
    def graph_report(pid: str):
        from .engines.graph import api as graph_api
        return {"markdown": graph_api.report(c(pid).project.root)}

    @r.get("/graph/wiki", dependencies=read)
    def graph_wiki(pid: str):
        wiki = c(pid).project.map_dir / "wiki"
        pages = sorted(wiki.glob("*.md")) if wiki.is_dir() else []
        return [{"slug": p.stem, "title": _title(p)} for p in pages]

    @r.get("/graph/wiki/{slug}", dependencies=read)
    def graph_wiki_page(pid: str, slug: str):
        wiki = (c(pid).project.map_dir / "wiki").resolve()
        page = (wiki / f"{slug}.md").resolve()
        if not page.is_relative_to(wiki) or not page.is_file():
            raise HTTPException(404, "no such page")
        return {"slug": slug, "title": _title(page), "markdown": page.read_text(errors="replace", encoding="utf-8")}

    @r.post("/graph/wiki", dependencies=syncing)
    def graph_wiki_build(pid: str):
        from .engines.graph import api as graph_api
        root = c(pid).project.root
        res = graph_api.run(["export", "wiki", "--graph", str(graph_api.graph_json(root))], root=root)
        return {"ok": res.get("code") == 0, "log": (res.get("stdout") or res.get("stderr") or "")[-2000:]}

    @r.get("/graph/tools", dependencies=read)
    def graph_tools(pid: str):
        from .engines.graph import api as graph_api
        return [t for t in graph_api.tools(c(pid).project.root).tools() if t["name"] not in SERVER_HIDDEN_GRAPH_TOOLS]

    @r.post("/graph/tools/{name}", dependencies=read)
    def graph_tool(pid: str, name: str, payload: dict = Body(default={})):
        from .engines.graph import api as graph_api
        from .engines.graph.serve import ToolError
        tools = graph_api.tools(c(pid).project.root)
        spec = next((t for t in tools.tools() if t["name"] == name), None)
        if spec is None or name in SERVER_HIDDEN_GRAPH_TOOLS:
            raise HTTPException(404, "no such graph tool")
        allowed = set((spec.get("input_schema") or {}).get("properties", {})) - {"project_path"}
        args = {k: v for k, v in payload.items() if k in allowed}  # never another project's path
        try:
            return {"text": tools.call(name, args)}
        except ToolError as exc:
            raise HTTPException(400, str(exc)) from exc

    # specs
    @r.get("/specs", dependencies=read)
    def specs(pid: str):
        root = c(pid).project.root
        feats = specs_engine.features(root)
        for f in feats:
            fdir = root / f["path"]
            f["artifacts"] = sorted(p.relative_to(fdir).as_posix() for p in fdir.rglob("*.md") if p.is_file())
            f["clarifications"] = clarifications(fdir / "spec.md")
        return {"constitution": specs_engine.constitution(root), "features": feats}

    @r.get("/specs/{fid}/doc/{name:path}", dependencies=read)
    def spec_doc(pid: str, fid: str, name: str):
        root = c(pid).project.root
        base = (root / "specs" / fid).resolve()
        target = (base / name).resolve()
        if base.parent != (root / "specs").resolve() or not target.is_relative_to(base) or target.suffix != ".md" \
                or not target.is_file():
            raise HTTPException(404, "no such document")
        return {"name": name, "markdown": target.read_text(errors="replace", encoding="utf-8")}

    @r.patch("/specs/{fid}/tasks/{tid}")
    def set_task(pid: str, fid: str, tid: str, payload: dict = Body(...), principal=Depends(require("project.write"))):
        cairn = c(pid)
        try:
            task = specs_engine.set_task_done(cairn.project.root, fid, tid, bool(payload.get("done")))
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        specs_engine.ingest(cairn.project, cairn.brain)
        audit(pid, principal, "task.done" if payload.get("done") else "task.reopen", f"{fid}/{tid}")
        return task

    @r.get("/drift", dependencies=read)
    def drift(pid: str, spec: str | None = None):
        return {"findings": drift_mod.check(c(pid), spec)}

    @r.get("/workflow", dependencies=read)
    def workflow(pid: str):
        root = c(pid).project.root
        state = specs_engine.workflow_state(root)
        has_constitution = bool(state.get("constitution"))
        stages = {s: [] for s in STAGES}
        for f in specs_engine.features(root):
            fdir = root / f["path"]
            done = {"constitution": has_constitution, "specify": (fdir / "spec.md").is_file(),
                    "clarify": bool(clarifications(fdir / "spec.md")), "plan": (fdir / "plan.md").is_file(),
                    "tasks": (fdir / "tasks.md").is_file(), "analyze": (fdir / "analysis.md").is_file(),
                    "implement": bool(f["progress"]["total"]) and f["progress"]["done"] == f["progress"]["total"]}
            for stage, ok in done.items():
                if ok:
                    stages[stage].append(f["id"])
        return {**state, "stages": [{"id": s, "done_for": ids} for s, ids in stages.items()],
                "commands": workflow_commands(), "agents": state.get("integrations", [])}

    # memory
    @r.get("/memories", dependencies=read)
    def memories(pid: str, q: str | None = None, kind: str | None = None, all: bool = False):
        cairn = c(pid)
        rows = cairn.memory.recall(q, 30) if q else cairn.brain.memories(include_inactive=all, limit=300)
        return [m for m in rows if not kind or m.get("kind") == kind]

    @r.post("/memories")
    def add_memory(pid: str, payload: dict = Body(...), principal=Depends(require("project.write"))):
        cairn = c(pid)
        previous = cairn.brain.memory(payload["supersedes"]) if payload.get("supersedes") else None
        try:
            res = cairn.remember(payload.get("text", ""), payload.get("kind", "fact"), payload.get("supersedes"),
                                 source="ui")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        out = memory_result(cairn, res, previous)
        hub.broadcast(pid, {"type": "memory", "op": out["op"], "memory": out})
        audit(pid, principal, "memory.update" if previous else "memory.add", f"memory:{out['id']}", op=out["op"])
        return out

    @r.patch("/memories/{mid}")
    def edit_memory(pid: str, mid: str, payload: dict = Body(...), principal=Depends(require("project.write"))):
        """An edit is a new memory that supersedes the old one, so the history keeps both."""
        cairn = c(pid)
        old_row = cairn.brain.memory(mid)
        if not old_row:
            raise HTTPException(404, "no such memory")
        res = cairn.remember(payload.get("text") or old_row["text"], payload.get("kind") or old_row["kind"],
                             supersedes=mid, source="ui")
        out = memory_result(cairn, res, old_row)
        hub.broadcast(pid, {"type": "memory", "op": out["op"], "memory": out})
        audit(pid, principal, "memory.update", f"memory:{out['id']}", replaces=mid)
        return out

    @r.delete("/memories/{mid}")
    def forget(pid: str, mid: str, principal=Depends(require("project.write"))):
        ok = c(pid).memory.forget(mid)
        hub.broadcast(pid, {"type": "memory", "op": "DELETE", "memory": {"id": mid}})
        if ok:
            audit(pid, principal, "memory.forget", f"memory:{mid}")
        return {"forgotten": ok}

    @r.get("/memories/{mid}/history", dependencies=read)
    def memory_history(pid: str, mid: str):
        cairn = c(pid)
        if not cairn.brain.memory(mid):
            raise HTTPException(404, "no such memory")
        return cairn.memory.history(mid)

    @r.get("/memories/sources", dependencies=read)
    def memory_sources(pid: str):
        rows = c(pid).brain.q(
            "SELECT source, COUNT(*) n, MAX(created_at) last FROM memories WHERE forgotten=0 AND superseded_by IS NULL "
            "GROUP BY source ORDER BY n DESC")
        return [{"source": r["source"], "count": r["n"], "last_seen": r["last"]} for r in rows]

    # the memory engine's full API (scoped to this project unless a narrower scope is given)
    def mem_engine(pid: str):
        eng = c(pid).memory.semantic.engine
        if eng is None:
            raise HTTPException(503, "the memory engine isn't available")
        return eng

    def mem_call(fn, *args, **kwargs):
        from .engines.memstore.api import ApiError
        try:
            return fn(*args, **kwargs)
        except ApiError as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    def scoped(pid: str, body: dict) -> dict:
        """Every engine call is about this project: its id always wins, and a team scope must be its team."""
        body = {**body, "project_id": c(pid).project.id}
        rec = hub.platform.get_project(pid)
        if body.get("team_id") and (rec is None or body["team_id"] != rec.team_id):
            body.pop("team_id")
        if isinstance(body.get("filters"), dict):
            body["filters"] = {**body["filters"], "project_id": c(pid).project.id}
        return body

    def owned(pid: str, mid: str):
        from .engines.memstore import api
        eng = mem_engine(pid)
        item = mem_call(api.get_memory, eng, mid)
        if (item or {}).get("project_id") != c(pid).project.id:
            raise HTTPException(404, "no such memory")
        return eng

    @r.post("/memory/memories", dependencies=write)
    def engine_add(pid: str, payload: dict = Body(...)):
        from .engines.memstore import api
        return mem_call(api.add_memories, mem_engine(pid), scoped(pid, payload))

    @r.get("/memory/memories", dependencies=read)
    def engine_list(pid: str, request: Request, top_k: int | None = None, show_expired: bool = False):
        from .engines.memstore import api
        narrower = {k: v for k, v in request.query_params.items() if k in ("user_id", "agent_id", "run_id")}
        scope = {**narrower, "project_id": c(pid).project.id}
        return mem_call(api.list_memories, mem_engine(pid), top_k=top_k, show_expired=show_expired, **scope)

    @r.post("/memory/search", dependencies=read)
    def engine_search(pid: str, payload: dict = Body(...)):
        from .engines.memstore import api
        return mem_call(api.search_memories, mem_engine(pid), scoped(pid, payload))

    @r.get("/memory/memories/{mid}", dependencies=read)
    def engine_get(pid: str, mid: str):
        from .engines.memstore import api
        return mem_call(api.get_memory, owned(pid, mid), mid)

    @r.put("/memory/memories/{mid}", dependencies=write)
    def engine_update(pid: str, mid: str, payload: dict = Body(...)):
        from .engines.memstore import api
        return mem_call(api.update_memory, owned(pid, mid), mid, payload)

    @r.delete("/memory/memories/{mid}", dependencies=write)
    def engine_delete(pid: str, mid: str):
        from .engines.memstore import api
        return mem_call(api.delete_memory, owned(pid, mid), mid)

    @r.get("/memory/memories/{mid}/history", dependencies=read)
    def engine_history(pid: str, mid: str):
        from .engines.memstore import api
        return mem_call(api.memory_history, owned(pid, mid), mid)

    @r.get("/memory/entities", dependencies=read)
    def engine_entities(pid: str):
        from .engines.memstore import api
        return mem_call(api.list_entities, mem_engine(pid))

    @r.post("/memory/generate-instructions", dependencies=write)
    def engine_instructions(pid: str, payload: dict = Body(...)):
        from .engines.memstore import api
        return mem_call(api.generate_instructions, mem_engine(pid), payload)

    @r.post("/v1/chat/completions", dependencies=write)
    def chat_completions(pid: str, payload: dict = Body(...)):
        """OpenAI-compatible chat that remembers: answers with this project's memories in context."""
        from .engines.memstore import api
        from .engines.memstore.proxy import ChatProxy
        cairn = c(pid)
        return mem_call(api.chat_completions, ChatProxy(memory=mem_engine(pid), router=cairn.router),
                        scoped(pid, payload))

    # temporal facts (the fact graph: what was true, and when)
    def temporal(pid: str):
        """One service per request: each request runs in its own event loop, and the embedded store is a
        single-writer file that must be released between requests so `cairn sync` can write facts."""
        from .engines.temporal import TemporalService
        cairn = c(pid)
        return TemporalService(cairn.project, cairn.router, cairn.brain)

    def tcall(pid: str, coro_of):
        from .engines.temporal import run_sync
        from .engines.temporal.errors import EdgeNotFoundError, NodeNotFoundError
        from .engines.temporal.service import ModelUnavailableError, StoreBusyError
        try:
            return run_sync(coro_of(temporal(pid)))
        except (EdgeNotFoundError, NodeNotFoundError) as exc:
            raise HTTPException(404, str(exc)) from exc
        except ModelUnavailableError as exc:
            raise HTTPException(409, "this needs a model: sign in to Claude Code or set a model key") from exc
        except StoreBusyError as exc:
            raise HTTPException(503, "the fact graph is busy (a sync is writing it); try again shortly") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"invalid request: {exc}") from exc

    @r.get("/facts", dependencies=read)
    def facts(pid: str, q: str | None = None, at: str | None = None, limit: int = 100):
        if at:
            rows = tcall(pid, lambda s: s.facts_at(at, q or "", max_facts=limit))
        elif q:
            rows = tcall(pid, lambda s: s.search_facts(q, max_facts=limit))
        else:
            rows = tcall(pid, lambda s: s.list_facts(limit=limit))
        names = {e["uuid"]: e["name"] for e in tcall(pid, lambda s: s.list_entities(limit=5000))} if rows else {}
        for f in rows:
            f["id"] = f.get("uuid")
            f["source_id"], f["target_id"] = f.get("source_node_uuid"), f.get("target_node_uuid")
            f["source_entity"], f["target_entity"] = names.get(f["source_id"]), names.get(f["target_id"])
        return rows

    @r.get("/facts/entities", dependencies=read)
    def fact_entities(pid: str, q: str | None = None, limit: int = 60):
        if q:
            return tcall(pid, lambda s: s.search_nodes(q, max_nodes=limit))
        return tcall(pid, lambda s: s.list_entities(limit=limit))

    @r.get("/facts/entities/{uuid}", dependencies=read)
    def fact_entity(pid: str, uuid: str):
        return tcall(pid, lambda s: s.get_node(uuid))

    @r.get("/facts/communities", dependencies=read)
    def fact_communities(pid: str):
        return tcall(pid, lambda s: s.list_communities())

    @r.get("/facts/episodes", dependencies=read)
    def fact_episodes(pid: str, limit: int = 30):
        return tcall(pid, lambda s: s.get_episodes(last_n=limit))

    @r.get("/facts/status", dependencies=read)
    def fact_status(pid: str):
        return tcall(pid, lambda s: s.status())

    @r.post("/temporal/{tool}", dependencies=read)
    def temporal_tool(pid: str, tool: str, request: Request, payload: dict = Body(default={}),
                      principal=Depends(current_principal)):
        from .engines.temporal.api import TOOL_INDEX, call_tool
        spec = TOOL_INDEX.get(tool if tool.startswith("cairn_timeline_") else f"cairn_timeline_{tool}")
        if spec is None:
            raise HTTPException(404, f"unknown timeline operation: {tool}")
        if spec.writes and not get_platform(request).can(principal, "project.write", project_id=pid):
            raise HTTPException(403, "your role can't change this project's facts")
        args = {k: v for k, v in payload.items() if k not in ("group_id", "group_ids")}  # this project's facts only
        return tcall(pid, lambda s: call_tool(s, spec.name, args))

    # timeline & sessions (read-model views; the engines add richer routes below when present)
    @r.get("/timeline", dependencies=read)
    def timeline(pid: str, since: float | None = None, target: str | None = None, kinds: str | None = None,
                 limit: int = 300):
        return c(pid).brain.events(kinds.split(",") if kinds else None, since, target, limit)

    # sessions: what agents did (the session memory engine)
    def recall_root(pid: str) -> Path:
        return c(pid).project.root

    def pushed_by_names(rows: list[dict]) -> list[dict]:
        """Sessions pushed from other machines carry the pusher's user id; the page shows their name."""
        names: dict[str, str] = {}
        for row in rows:
            uid = row.get("pushed_by")
            if uid:
                if uid not in names:
                    user = hub.platform.get_user(uid)
                    names[uid] = user.name if user else "a former member"
                row["pushed_by_name"] = names[uid]
        return rows

    @r.get("/sessions/feed", dependencies=read)
    def sessions_feed(pid: str, cursor: str | None = None, limit: int = 50, type: str | None = None,
                      concept: str | None = None, file: str | None = None, q: str | None = None):
        from .engines.recall import api as recall_api
        return recall_api.feed(recall_root(pid), cursor, limit, type, concept, file, q)

    @r.get("/sessions/stats", dependencies=read)
    def sessions_stats(pid: str):
        from .engines.recall import api as recall_api
        return recall_api.stats(recall_root(pid))

    @r.get("/sessions/list", dependencies=read)
    def sessions_list(pid: str, limit: int = 30, offset: int = 0):
        from .engines.recall import api as recall_api
        out = recall_api.sessions(recall_root(pid), limit, offset)
        pushed_by_names(out.get("items") or [])
        return out

    @r.get("/sessions/search", dependencies=read)
    def sessions_search(pid: str, q: str = "", type: str | None = None, limit: int = 30):
        from .engines.recall import api as recall_api
        return recall_api.search(recall_root(pid), q, type, limit)

    @r.get("/sessions/context", dependencies=read)
    def sessions_context(pid: str, full: bool = False):
        from .engines.recall import api as recall_api
        return recall_api.context(recall_root(pid), full)

    @r.get("/sessions/status", dependencies=read)
    def sessions_status(pid: str):
        from .engines.recall import viewer
        return viewer.processing_status(recall_root(pid))

    @r.get("/sessions/observations/{oid}", dependencies=read)
    def sessions_observation(pid: str, oid: int):
        from .engines.recall import viewer
        row = viewer.observation(recall_root(pid), oid)
        if not row:
            raise HTTPException(404, "no such observation")
        return row

    @r.get("/sessions/settings", dependencies=read)
    def sessions_settings(pid: str):
        from .engines.recall import viewer
        return viewer.get_settings(recall_root(pid))

    @r.post("/sessions/settings", dependencies=[Depends(require("project.admin"))])
    def sessions_settings_update(pid: str, payload: dict = Body(...)):
        from .engines.recall import viewer
        try:
            return viewer.update_settings(recall_root(pid), payload)
        except KeyError as exc:
            raise HTTPException(400, f"unknown setting: {exc}") from exc

    @r.get("/sessions/queue", dependencies=read)
    def sessions_queue(pid: str):
        from .engines.recall import viewer
        return viewer.queue(recall_root(pid))

    @r.post("/sessions/queue/{action}", dependencies=syncing)
    def sessions_queue_action(pid: str, action: str):
        from .engines.recall import viewer
        root = recall_root(pid)
        if action == "retry":
            return viewer.retry_failed(root)
        if action in ("clear", "clear-failed"):
            return viewer.clear_queue(root, action == "clear-failed")
        raise HTTPException(404, "no such action")

    @r.post("/sessions/events")
    async def sessions_events(pid: str, request: Request, principal=Depends(require("project.capture"))):
        """Hook events from agents on other machines (`cairn sessions push`), recorded like local hooks."""
        from .engines.recall import api as recall_api
        from .engines.recall.remote import IngestError
        too_big = HTTPException(413, f"request body over {MAX_EVENTS_BODY // 2**20} MB; send smaller batches")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_EVENTS_BODY:
            raise too_big
        body = bytearray()
        async for chunk in request.stream():  # counted as it arrives, so a body without a length is capped too
            body += chunk
            if len(body) > MAX_EVENTS_BODY:
                raise too_big
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise HTTPException(400, "request body is not JSON") from exc
        try:
            # sessions are namespaced by who pushed them, so one member cannot write into another's session
            return await asyncio.to_thread(lambda: recall_api.ingest_remote(recall_root(pid), payload,
                                                                            caller=principal.user_id))
        except IngestError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @r.post("/sessions/process", dependencies=syncing)
    def sessions_process(pid: str, payload: dict = Body(default={})):
        """Turn queued agent activity into observations now (with the model, or deterministically)."""
        from .engines.recall.cli import main as recall_main
        args = ["worker", "--once"] + (["--no-model"] if payload.get("no_model") else [])
        threading.Thread(target=recall_main, args=(args + ["--root", str(recall_root(pid))],),
                         daemon=True).start()
        return {"started": True}

    @r.delete("/sessions/{kind}/{item_id}", dependencies=write)
    def sessions_delete(pid: str, kind: str, item_id: int):
        from .engines.recall import viewer
        if kind not in ("observation", "summary", "prompt"):
            raise HTTPException(404, "no such record type")
        return viewer.delete(recall_root(pid), kind, item_id)

    @r.get("/sessions/{sid}", dependencies=read)
    def sessions_one(pid: str, sid: str):
        from .engines.recall import api as recall_api
        out = recall_api.session(recall_root(pid), sid)
        if out is None:
            raise HTTPException(404, "no such session")
        if isinstance(out.get("session"), dict):
            pushed_by_names([out["session"]])
        return out

    @r.get("/agents", dependencies=read)
    def agent_activity(pid: str, limit: int = 80):
        return c(pid).agent_activity(limit)

    return r


STEP_NAMES = {"timeline": "temporal"}
GRAPH_VIEWS = {"graph": "Interactive graph", "callflow": "Architecture and call flow", "tree": "Folder tree"}
GRAPH_ASSETS = "/assets/graph/"  # public, versioned library files for the sandboxed view frames (no project data)


def _title(page: Path) -> str:
    first = next((ln for ln in page.read_text(errors="replace", encoding="utf-8").splitlines() if ln.startswith("# ")), "")
    return first[2:].strip() or page.stem.replace("-", " ")
STAGES = ("constitution", "specify", "clarify", "plan", "tasks", "analyze", "implement")
KIND_NAMES = {"req": "requirement", "obs": "observation", "rationale": "doc"}
OPS = {"stored": "ADD", "exists": "NONE", "updated": "UPDATE"}


def clarifications(spec_md: Path) -> list[dict]:
    """Answered clarifications in a spec (`- Q: … → A: …`, possibly wrapped over several lines)."""
    if not spec_md.is_file():
        return []
    out = []
    for line in specs_engine._unwrap(spec_md.read_text(errors="replace", encoding="utf-8")):
        s = line.strip()
        if s.startswith("- Q:") and "→" in s:
            q, a = s[4:].split("→", 1)
            out.append({"q": q.strip(), "a": a.strip().removeprefix("A:").strip()})
    return out


def workflow_commands() -> list[dict]:
    base = Path(specs_engine.__file__).resolve().parent / "workflow" / "assets" / "commands"
    out = []
    for f in sorted(base.glob("*.md")):
        text = f.read_text(errors="replace", encoding="utf-8")
        desc = next((ln.split(":", 1)[1].strip().strip('"') for ln in text.splitlines()[:12]
                     if ln.startswith("description:")), "")
        out.append({"name": f"/cairn.{f.stem}", "description": desc})
    return out


def memory_result(cairn: Cairn, res: dict, previous: dict | None) -> dict:
    """A saved memory plus what reconciliation did with it: ADD, UPDATE (merged), DELETE (replaced) or NONE."""
    status = str(res.get("status", "stored"))
    op = "DELETE" if res.get("retired") else OPS.get(status.split()[0], "UPDATE" if status.startswith("superseded")
                                                    else "ADD")
    row = cairn.brain.memory(res["id"]) if res.get("id") else None
    out = {**(row or {}), **res, "op": op}
    if previous:
        out["previous"] = previous.get("text")
    if res.get("retired"):
        gone = [cairn.brain.memory(m) for m in res["retired"]]
        out["replaced"] = [g["text"] for g in gone if g]
    if op == "NONE":
        out["reason"] = "Already known"
    return out


SETTINGS: dict[str, type | tuple] = {
    "models.provider": str, "models.fast": str, "models.balanced": str, "models.deep": str, "models.frontier": str,
    "models.base_url": str, "sessions.capture": bool, "deep.enabled": (bool, str), "deep.budget_tokens": int,
    "context.budget": int, "context.ambient": bool, "deep.wall_seconds": int, "memory.seed": bool, "memory.rerank": bool,
    "memory.wall_seconds": int, "recall.worker_spawn": bool, "recall.observe_batch": int, "recall.context_observations": int}
PROVIDERS = ("auto", "anthropic", "openai", "claude-code")
SETTING_LIMITS = {"context.budget": (200, 20_000), "deep.budget_tokens": (1_000, 5_000_000),
                  "deep.wall_seconds": (10, 7_200), "memory.wall_seconds": (10, 7_200),
                  "recall.observe_batch": (1, 50), "recall.context_observations": (0, 500)}
SETTING_RULES_TEXT = {
    "models.provider": "one of " + ", ".join(PROVIDERS),
    **{f"models.{t}": "a model name" for t in ("fast", "balanced", "deep", "frontier")},
    "models.base_url": "empty, or an http(s) URL",
    "deep.enabled": "true, false or \"auto\"",
    **{k: f"a whole number from {lo:,} to {hi:,}" for k, (lo, hi) in SETTING_LIMITS.items()},
}


def setting_problem(key: str, value) -> str | None:
    """Why ``value`` isn't acceptable for ``key`` (beyond its type), or None."""
    if key == "models.provider" and value not in PROVIDERS:
        return f"models.provider must be {SETTING_RULES_TEXT[key]}"
    if key.startswith("models.") and key not in ("models.provider", "models.base_url") and not str(value).strip():
        return f"{key} needs a model name"
    if key == "models.base_url" and value and not str(value).startswith(("http://", "https://")):
        return "models.base_url must be empty or start with http:// or https://"
    if key == "deep.enabled" and isinstance(value, str) and value != "auto":
        return 'deep.enabled must be true, false or "auto"'
    if key in SETTING_LIMITS:
        lo, hi = SETTING_LIMITS[key]
        if not lo <= value <= hi:
            return f"{key} must be {SETTING_RULES_TEXT[key]}"
    return None


# Pull-request tools run the host's GitHub CLI with the host's login: never offered to other people.
SERVER_HIDDEN_GRAPH_TOOLS = frozenset({"list_prs", "get_pr_impact", "triage_prs"})


class AskIn(BaseModel):
    question: str
    budget: int | None = None
    llm: bool = True


class NarrateIn(BaseModel):
    kind: Literal["ask", "impact", "why"]
    text: str = Field(min_length=1, max_length=2000)  # the question, or the file or symbol
    budget: int | None = Field(default=None, ge=200, le=20000)


# ---- MCP over HTTP: agents on other machines use a team server's memory -------------------------------------
class ProjectMCP:
    """`/mcp/<project id>`: the same tools as `cairn mcp`, answering about that project. The platform gate has
    already authenticated the caller (bearer token or session); the project must be readable, and callers who
    can't write get the read-only tool set."""

    def __init__(self, hub: Hub):
        self.hub = hub
        self.apps: dict[tuple[str, bool], object] = {}
        self.stops: dict[tuple[str, bool], asyncio.Event] = {}
        self._lock: asyncio.Lock | None = None
        hub.on_drop.append(self.drop)

    async def _app(self, pid: str, read_only: bool):
        from mcp.server.transport_security import TransportSecuritySettings

        from . import mcp_server
        self._lock = self._lock or asyncio.Lock()
        async with self._lock:
            key = (pid, read_only)
            if key in self.apps:
                return self.apps[key]
            mode = str(self.hub.cairn(pid).project.cfg("mcp.tools", "core"))
            server = mcp_server.build(mode=mode, resolver=lambda: self.hub.cairn(pid, surface="mcp"),
                                      read_only=read_only)
            app = server.streamable_http_app(
                streamable_http_path="/", stateless_http=True, json_response=True,
                transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
            ready, stop = asyncio.Event(), asyncio.Event()

            async def run() -> None:
                async with app.router.lifespan_context(app):
                    ready.set()
                    await stop.wait()
            asyncio.get_running_loop().create_task(run())
            await ready.wait()
            self.stops[key] = stop
            self.apps[key] = app
            return app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        route = scope["path"].removeprefix(scope.get("root_path", ""))
        pid, _, rest = route.lstrip("/").partition("/")
        platform = self.hub.platform
        principal = (scope.get("state") or {}).get("cairn_principal")
        if principal is None and platform.config.mode == "local":
            principal = platform.local_principal()
        if not pid or principal is None or not platform.can(principal, "project.read", project_id=pid):
            await JSONResponse({"detail": "project not found"}, status_code=404)(scope, receive, send)
            return
        read_only = not platform.can(principal, "project.write", project_id=pid)
        try:
            app = await self._app(pid, read_only)
        except HTTPException as exc:
            await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(scope, receive, send)
            return
        inner = {**scope, "path": "/" + rest, "raw_path": ("/" + rest).encode(), "root_path": ""}
        await app(inner, receive, send)

    def drop(self, pid: str) -> None:
        """A deleted project: stop its endpoints (called from the platform's delete, in any thread)."""
        for key in [k for k in self.apps if k[0] == pid]:
            self.apps.pop(key, None)
            ev = self.stops.pop(key, None)
            loop = self.hub.loop
            if ev is not None and loop is not None:
                loop.call_soon_threadsafe(ev.set)

    def stop(self) -> None:
        for ev in self.stops.values():
            ev.set()


# ---- the app -----------------------------------------------------------------------------------------------
def create_app(*, platform: Platform | None = None, config: ServerConfig | None = None,
               register: list[Path] | None = None) -> FastAPI:
    config = config or ServerConfig.load()
    platform = platform or Platform(config=config)
    hub = Hub(platform)

    mcp_endpoint = ProjectMCP(hub)

    @asynccontextmanager
    async def lifespan(_app):
        hub.loop = asyncio.get_running_loop()
        yield
        mcp_endpoint.stop()
        hub.stop_all()
        for callback in _app.state.on_shutdown:
            with contextlib.suppress(Exception):
                callback()

    app = FastAPI(title="Cairn", version=__version__, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
    app.state.hub = hub
    app.state.on_shutdown = []  # run after the server stops serving (serve() removes its state record here)
    install(app, platform, config, on_sync=hub.on_platform_sync)
    if config.mode == "local":
        for root in register or ():
            platform.register_local_project(root)

    @app.get("/api/health")
    def health():
        """Liveness plus where this server runs from, so a server whose files were removed can be told apart from
        a healthy one: `ok` only means it answers."""
        code_root = Path(__file__).resolve().parent
        return {"ok": True, "version": __version__, "pid": os.getpid(), "code_root": str(code_root),
                "code_root_exists": code_root.is_dir(), "ui_ok": (UI_DIR / "index.html").is_file()}

    @app.get("/api/session")
    def session(request: Request, optional: bool = False, principal=Depends(optional_principal)):
        """Who is signed in. The page's first check passes `optional=1` and gets `{"signed_in": false}`
        instead of a 401, so a signed-out visit doesn't log an error."""
        plat, cfg = get_platform(request), get_config(request)
        if principal is None:
            if optional:
                return {"signed_in": False, "mode": cfg.mode}
            raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
        if principal.must_change_password:  # nothing else until the one-time password is replaced
            return {"signed_in": True, **_me(plat, principal, cfg), "projects": []}
        records = plat.list_projects(principal)
        products = _products_by_project(records)
        projects = [_project_json(plat, principal, p, cfg) for p in records]
        for payload in projects:
            if payload.get("id") in products:
                payload["product"] = products[payload["id"]]
        return {"signed_in": True, **_me(plat, principal, cfg), "projects": projects}

    @app.get("/api/repos/map")
    def repos_map(request: Request, team: str | None = None, principal=Depends(current_principal)):
        """Every repository the caller can read (optionally one team's) and how they depend on each other:
        imports of a package another repository declares. Reads existing maps only; never rebuilds."""
        from .engines.graph.repos import repos
        projects = []
        for rec in get_platform(request).list_projects(principal, team_id=team):
            try:
                cairn = hub.cairn(rec.id)
            except HTTPException:  # files not on this server (yet)
                continue
            if cairn.project.map_json.exists():
                projects.append((rec.id, rec.name, cairn.project.root, cairn.map))
        return repos(projects)

    app.include_router(project_router(hub))
    app.mount("/mcp", mcp_endpoint)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        page = UI_DIR / "index.html"
        if not page.is_file():  # the files this server was started from are gone: say so instead of a bare 500
            body = {"error": "the UI files are missing: this server runs from code that no longer exists",
                    "path": "/", "cause": str(page), "fix": "run `cairn up` to replace the server"}
            if "text/html" in request.headers.get("accept", ""):  # a person in a browser gets a page, not JSON
                return HTMLResponse(
                    "<!doctype html><meta charset=utf-8><title>Cairn server needs a restart</title>"
                    "<body style=\"font:16px/1.5 system-ui;max-width:40rem;margin:12vh auto;padding:0 1rem\">"
                    "<h1>This Cairn server needs a restart</h1>"
                    "<p>It is running from files that no longer exist, so it cannot show the UI.</p>"
                    "<p>In a terminal run <code>cairn up</code>. It replaces the server (other repositories on this "
                    "machine are interrupted for a few seconds). Then reload this page.</p>"
                    f"<p><small>Missing: {page}</small></p>", status_code=503)
            return JSONResponse(body, status_code=503)
        return FileResponse(page, media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.middleware("http")
    async def revalidate_page_assets(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/ui/"):
            response.headers.setdefault("Cache-Control", "no-cache")  # always check for a newer page build
        return response

    if UI_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

    @app.get("/assets/graph/{name}")
    def graph_asset(name: str):
        from fastapi.responses import Response

        from .engines.graph import api as graph_api
        try:
            body, media = graph_api.asset(name)
        except KeyError as exc:
            raise HTTPException(404, "no such asset") from exc
        return Response(body, media_type=media, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.exception_handler(FileNotFoundError)
    async def missing(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(Exception)
    async def failed(request, exc):
        """Any other failure is a JSON body the page can show (what failed, where), never bare text."""
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"error": "the server hit an unexpected error", "path": request.url.path,
                             "cause": f"{type(exc).__name__}: {exc}"}, status_code=500)

    return app


def serve(*, host: str | None = None, port: int | None = None, register: list[Path] | None = None) -> None:
    import uvicorn

    from . import daemon

    overrides = {k: v for k, v in (("host", host), ("port", port)) if v}
    config = ServerConfig.load(overrides)
    app = create_app(config=config, register=register)
    # Record where this server listens (invite links and `cairn ui` read it), unless another live server already
    # holds the record; `cairn up` writes the same record for the server it starts.
    state = daemon._state_file()
    if daemon.info() is None:
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({**daemon.new_record(os.getpid(), config.port), "host": config.host}),
                         encoding="utf-8")

        def forget_state() -> None:
            if json.loads(state.read_text(encoding="utf-8")).get("pid") == os.getpid():
                state.unlink()
        app.state.on_shutdown.append(forget_state)
    # Open live-update streams never end by themselves: give them a moment, then stop, so a stopped server
    # really exits (and shuts its stores and workers down) instead of lingering with old code.
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning", timeout_graceful_shutdown=3)

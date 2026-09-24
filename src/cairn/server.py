"""HTTP API + single-page UI, served by the local daemon (127.0.0.1 only)."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse

from . import __version__, drift as drift_mod, sync
from .core import Cairn
from .engines import specs as specs_engine

UI = Path(__file__).resolve().parent / "ui" / "index.html"


def create_app(cairn: Cairn) -> FastAPI:
    from contextlib import asynccontextmanager

    listeners: list[asyncio.Queue] = []
    state = {"syncing": False}
    loop_holder: dict = {}

    @asynccontextmanager
    async def lifespan(_app):
        loop_holder["loop"] = asyncio.get_running_loop()
        yield

    app = FastAPI(title="Cairn", version=__version__, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)

    def broadcast(msg: dict) -> None:
        loop = loop_holder.get("loop")
        for q in list(listeners):
            if loop:
                loop.call_soon_threadsafe(q.put_nowait, msg)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return UI.read_text()

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "project": cairn.project.name, "root": str(cairn.project.root)}

    @app.get("/api/overview")
    def overview():
        o = cairn.overview()
        o["syncing"] = state["syncing"]
        o["models"] = {"available": cairn.router.available, "deep": cairn.router.deep_enabled()}
        return o

    @app.get("/api/map")
    def map_(area: int | None = None, limit: int = 60):
        idx = cairn.map
        if area is None:
            return {"level": "areas", **idx.area_graph(limit)}
        return {"level": "members", "area": area, **idx.area_members(area, limit=160)}

    @app.get("/api/entity")
    def entity(id: str):
        ent = cairn.brain.entity(id)
        if id.startswith("memory:"):
            m = cairn.brain.memory(id[7:])
            if not m:
                raise HTTPException(404, "not found")
            return {"entity": {"id": id, "kind": "memory", "name": m["text"], "meta": m}, "links": []}
        if not ent and not id.startswith(("symbol:", "file:")):
            raise HTTPException(404, "not found")
        return {"entity": ent or {"id": id, "kind": id.split(":")[0], "name": id.split(":", 1)[1], "meta": {}},
                "links": cairn.brain.links_from(id, limit=60) + cairn.brain.links_to(id, limit=60)}

    @app.get("/api/search")
    def search(q: str, kinds: str | None = None, limit: int = 20):
        return cairn.search(q, kinds.split(",") if kinds else None, limit)

    def _pack(p):
        return {"title": p.title, "header": p.header, "sections": p.sections(), "markdown": p.render(), **p.data}

    @app.get("/api/impact")
    def impact(target: str, depth: int = 2, budget: int = 2400):
        return _pack(cairn.impact(target, depth, budget))

    @app.get("/api/why")
    def why(target: str, budget: int = 2400):
        return _pack(cairn.why(target, budget))

    @app.get("/api/specs")
    def specs():
        return {"constitution": specs_engine.constitution(cairn.project.root),
                "features": specs_engine.features(cairn.project.root)}

    @app.get("/api/drift")
    def drift(spec: str | None = None):
        found = drift_mod.check(cairn, spec)
        return {"findings": found}

    @app.get("/api/memories")
    def memories(q: str | None = None, all: bool = False):
        return cairn.memory.recall(q, 30) if q else cairn.brain.memories(include_inactive=all, limit=300)

    @app.post("/api/memories")
    def add_memory(payload: dict = Body(...)):
        try:
            return cairn.remember(payload.get("text", ""), payload.get("kind", "fact"), payload.get("supersedes"),
                                  source="ui")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.delete("/api/memories/{mid}")
    def forget(mid: str):
        return {"forgotten": cairn.brain.forget(mid)}

    @app.get("/api/timeline")
    def timeline(since: float | None = None, target: str | None = None, kinds: str | None = None, limit: int = 300):
        return cairn.brain.events(kinds.split(",") if kinds else None, since, target, limit)

    @app.get("/api/sessions")
    def sessions(q: str | None = None, limit: int = 60):
        if q:
            ids = [h["id"] for h in cairn.brain.search(q, kinds=["obs"], limit=limit)]
            return [e for i in ids if (e := cairn.brain.entity(i))]
        return [{"id": e["id"], "title": e["title"], "ts": e["ts"], "body": e["body"][:600], "meta": e["meta"]}
                for e in cairn.brain.events(kinds=["session"], limit=limit)]

    @app.post("/api/ask")
    def ask(payload: dict = Body(...)):
        return cairn.ask(payload.get("question", ""), payload.get("budget"), payload.get("llm", True))

    @app.post("/api/sync")
    def start_sync(payload: dict = Body(default={})):
        if state["syncing"]:
            return {"started": False, "reason": "already running"}
        state["syncing"] = True

        def work():
            try:
                res = sync.run(cairn, deep=payload.get("deep"),
                               progress=lambda s, st, d: broadcast({"step": s, "state": st, "detail": d}))
                broadcast({"step": "sync", "state": "done", "detail": json.dumps({k: v for k, v in res.items()
                                                                                  if k == "seconds"})})
            finally:
                state["syncing"] = False
        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    @app.get("/api/stream")
    async def stream():
        q: asyncio.Queue = asyncio.Queue()
        listeners.append(q)

        async def gen():
            try:
                yield "retry: 3000\n\n"
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=20)
                        yield f"data: {json.dumps(msg)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                listeners.remove(q)
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/models")
    def models(since: float = Query(0)):
        return {"available": cairn.router.available, "provider": cairn.router.provider,
                "table": cairn.router.table(), "ledger": cairn.brain.ledger(since)}

    @app.get("/api/brief")
    def brief():
        return {"brief": cairn.brief()}

    return app


def serve(cairn: Cairn, port: int) -> None:
    import uvicorn
    uvicorn.run(create_app(cairn), host="127.0.0.1", port=port, log_level="warning")

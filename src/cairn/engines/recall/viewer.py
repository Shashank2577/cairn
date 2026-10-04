"""Data for the Sessions UI: the live feed of observations, summaries and prompts, filters, search,
stats, processing status, settings, logs, import/export and a server-sent-event stream.

Every function takes the repository root and returns JSON-ready dicts/lists (shapes documented in
the integration note). Hooks and the worker run in other processes, so ``stream()`` watches the
store itself (new rows, queue depth) rather than waiting for in-process broadcasts.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import mcp, schema
from .modes import available_modes, load_mode
from .platforms import normalize_platform_source
from .settings import DEFAULTS
from .settings import load as load_settings
from .settings import save as save_settings
from .store import USER_PROMPT_DEDUPE_WINDOW_MS, Store

log = logging.getLogger("cairn.recall")

MAX_PAGE = 100


def _open(root: Path | str) -> Store:
    return Store.open(root)


def _page(offset: Any, limit: Any) -> tuple[int, int]:
    try:
        off = max(0, int(offset or 0))
    except (TypeError, ValueError):
        off = 0
    try:
        lim = int(limit or 20)
    except (TypeError, ValueError):
        lim = 20
    return off, max(1, min(lim, MAX_PAGE))


def _strip_project_path(path: str, project: str) -> str:
    leaf = project.split("/")[-1] if project else ""
    marker = f"/{leaf}/"
    i = path.find(marker) if leaf else -1
    return path[i + len(marker):] if i != -1 else path


def _sanitize(obs: dict) -> dict:
    out = dict(obs)
    for k in ("files_read", "files_modified"):
        try:
            paths = json.loads(out.get(k) or "null")
        except ValueError:
            continue
        if isinstance(paths, list):
            out[k] = json.dumps([_strip_project_path(str(p), out.get("project") or "") for p in paths])
    return out


# ---- the feed -------------------------------------------------------------------------------------------
def observations(root: Path | str, offset: Any = 0, limit: Any = 20, project: str | None = None,
                 platform_source: str | None = None) -> dict:
    off, lim = _page(offset, limit)
    cond, args = [], []
    if project:
        cond.append("(o.project = ? OR o.merged_into_project = ?)")
        args += [project, project]
    if platform_source:
        cond.append("COALESCE(s.platform_source, 'claude') = ?")
        args.append(normalize_platform_source(platform_source))
    where = f"WHERE {' AND '.join(cond)}" if cond else ""
    with _open(root) as st:
        rows = [dict(r) for r in st.db.execute(
            "SELECT o.id, o.memory_session_id, o.project, o.merged_into_project, COALESCE(s.platform_source, 'claude')"
            " AS platform_source, o.type, o.title, o.subtitle, o.narrative, o.text, o.facts, o.concepts, o.files_read,"
            " o.files_modified, o.prompt_number, o.created_at, o.created_at_epoch, o.metadata, o.generated_by_model,"
            " s.content_session_id AS session_id FROM observations o LEFT JOIN sdk_sessions s"
            f" ON o.memory_session_id = s.memory_session_id {where} ORDER BY o.created_at_epoch DESC LIMIT ? OFFSET ?",
            (*args, lim + 1, off))]
    return {"items": [_sanitize(r) for r in rows[:lim]], "hasMore": len(rows) > lim, "offset": off, "limit": lim}


def summaries(root: Path | str, offset: Any = 0, limit: Any = 20, project: str | None = None,
              platform_source: str | None = None) -> dict:
    off, lim = _page(offset, limit)
    cond, args = [], []
    if project:
        cond.append("(ss.project = ? OR ss.merged_into_project = ?)")
        args += [project, project]
    if platform_source:
        cond.append("COALESCE(s.platform_source, 'claude') = ?")
        args.append(normalize_platform_source(platform_source))
    where = f"WHERE {' AND '.join(cond)}" if cond else ""
    with _open(root) as st:
        rows = [dict(r) for r in st.db.execute(
            "SELECT ss.id, s.content_session_id AS session_id, COALESCE(s.platform_source, 'claude') AS platform_source,"
            " ss.request, ss.investigated, ss.learned, ss.completed, ss.next_steps, ss.notes, ss.files_read,"
            " ss.files_edited, ss.prompt_number, ss.project, ss.created_at, ss.created_at_epoch FROM session_summaries ss"
            f" JOIN sdk_sessions s ON ss.memory_session_id = s.memory_session_id {where}"
            " ORDER BY ss.created_at_epoch DESC LIMIT ? OFFSET ?", (*args, lim + 1, off))]
    return {"items": rows[:lim], "hasMore": len(rows) > lim, "offset": off, "limit": lim}


def prompts(root: Path | str, offset: Any = 0, limit: Any = 20, project: str | None = None,
            platform_source: str | None = None) -> dict:
    off, lim = _page(offset, limit)
    cond, args = [], []
    if project:
        cond.append("s.project = ?")
        args.append(project)
    if platform_source:
        cond.append("COALESCE(s.platform_source, 'claude') = ?")
        args.append(normalize_platform_source(platform_source))
    cond.append("length(trim(up.prompt_text)) > 0")
    cond.append("NOT EXISTS (SELECT 1 FROM user_prompts d WHERE d.session_db_id = up.session_db_id AND d.prompt_text ="
                " up.prompt_text AND (d.created_at_epoch > up.created_at_epoch OR (d.created_at_epoch ="
                " up.created_at_epoch AND d.id > up.id)) AND d.created_at_epoch - up.created_at_epoch <= ?)")
    args.append(USER_PROMPT_DEDUPE_WINDOW_MS)
    with _open(root) as st:
        rows = [dict(r) for r in st.db.execute(
            "SELECT up.id, up.content_session_id, s.project, COALESCE(s.platform_source, 'claude') AS platform_source,"
            " up.prompt_number, up.prompt_text, up.created_at, up.created_at_epoch FROM user_prompts up"
            f" JOIN sdk_sessions s ON up.session_db_id = s.id WHERE {' AND '.join(cond)}"
            " ORDER BY up.created_at_epoch DESC LIMIT ? OFFSET ?", (*args, lim + 1, off))]
    return {"items": rows[:lim], "hasMore": len(rows) > lim, "offset": off, "limit": lim}


def feed(root: Path | str, offset: Any = 0, limit: Any = 20, project: str | None = None,
         platform_source: str | None = None) -> dict:
    """One merged, newest-first feed (``itemType``: observation | summary | prompt)."""
    off, lim = _page(offset, limit)
    take = off + lim + 1
    items = [{**o, "itemType": "observation"} for o in observations(root, 0, take, project, platform_source)["items"]]
    items += [{**s, "itemType": "summary"} for s in summaries(root, 0, take, project, platform_source)["items"]]
    items += [{**p, "itemType": "prompt"} for p in prompts(root, 0, take, project, platform_source)["items"]]
    items.sort(key=lambda it: -int(it.get("created_at_epoch") or 0))
    return {"items": items[off:off + lim], "hasMore": len(items) > off + lim, "offset": off, "limit": lim}


# ---- single items -----------------------------------------------------------------------------------------
def observation(root: Path | str, obs_id: int, platform_source: str | None = None) -> dict | None:
    with _open(root) as st:
        return st.get_observation_by_id(int(obs_id), platform_source)


def observations_batch(root: Path | str, ids: list[int], order_by: str | None = None, limit: int | None = None,
                       project: str | None = None, platform_source: str | None = None) -> list[dict]:
    with _open(root) as st:
        return st.get_observations_by_ids([int(i) for i in ids], order_by=order_by or "date_desc", limit=limit,
                                          project=project, platform_source=platform_source)


def summary(root: Path | str, summary_id: int, project: str | None = None,
            platform_source: str | None = None) -> dict | None:
    with _open(root) as st:
        rows = st.get_session_summaries_by_ids([int(summary_id)], project=project, platform_source=platform_source)
    return rows[0] if rows else None


def prompt(root: Path | str, prompt_id: int, project: str | None = None, platform_source: str | None = None):
    with _open(root) as st:
        rows = st.get_user_prompts_by_ids([int(prompt_id)], project=project, platform_source=platform_source)
    return rows[0] if rows else None


def sdk_sessions_batch(root: Path | str, memory_session_ids: list[str]) -> list[dict]:
    with _open(root) as st:
        return st.get_sdk_sessions_by_memory_ids(memory_session_ids)


def observations_by_file(root: Path | str, paths: list[str], projects: list[str] | None = None,
                         limit: int | None = None, platform_source: str | None = None) -> dict:
    with _open(root) as st:
        rows = st.get_observations_by_file(paths, projects=projects, limit=limit, platform_source=platform_source)
    return {"observations": rows, "count": len(rows)}


def tool_uses(root: Path | str, **filters) -> dict:
    """The cheap listing: identity and sizes, never bodies (bodies come from ``tool_uses_batch``)."""
    with _open(root) as st:
        rows = st.query_tool_uses(**filters)
    return {"count": len(rows), "toolUses": [{
        **{k: r[k] for k in ("id", "tool_use_id", "tool_name", "project", "content_session_id", "memory_session_id",
                             "platform_source", "agent_id", "agent_type", "observation_id", "prompt_number",
                             "created_at", "created_at_epoch")},
        "tool_input_bytes": len((r.get("tool_input") or "").encode("utf-8")),
        "tool_response_bytes": len((r.get("tool_response") or "").encode("utf-8"))} for r in rows]}


def tool_uses_batch(root: Path | str, ids: list, limit: int | None = None, project: str | None = None,
                    content_session_id: str | None = None, platform_source: str | None = None) -> list[dict]:
    with _open(root) as st:
        return st.get_tool_uses_by_ids(ids, limit=min(limit, 200) if limit else None, project=project,
                                       content_session_id=content_session_id, platform_source=platform_source)


def delete(root: Path | str, kind: str, row_id: int) -> dict:
    """Delete an observation / summary / prompt (and its vector documents)."""
    if kind not in ("observation", "summary", "prompt"):
        raise ValueError("kind must be observation, summary or prompt")
    with _open(root) as st:
        ok = st.delete_row(kind, int(row_id))
        if ok:
            try:
                from .vectorsync import VectorSync
                VectorSync(root, st).delete({"observation": "observation", "summary": "session_summary",
                                             "prompt": "user_prompt"}[kind], [int(row_id)])
            except Exception as exc:  # noqa: BLE001
                log.debug("vector cleanup skipped: %s", exc)
    return {"success": ok, "id": int(row_id), "kind": kind}


# ---- sessions ---------------------------------------------------------------------------------------------
def sessions(root: Path | str, offset: Any = 0, limit: Any = 20, project: str | None = None,
             platform_source: str | None = None) -> dict:
    off, lim = _page(offset, limit)
    with _open(root) as st:
        rows = st.list_sessions(lim + 1, off, project, platform_source)
    return {"items": rows[:lim], "hasMore": len(rows) > lim, "offset": off, "limit": lim}


def session_detail(root: Path | str, session_db_id: int) -> dict | None:
    """One session with its prompts, observations, summaries and queued work, oldest first."""
    with _open(root) as st:
        s = st.get_session_by_id(int(session_db_id))
        if not s:
            return None
        mid = s.get("memory_session_id")
        prompts_ = [dict(r) for r in st.db.execute("SELECT * FROM user_prompts WHERE session_db_id=? ORDER BY"
                                                   " prompt_number", (s["id"],))]
        obs = [_sanitize(dict(r)) for r in st.db.execute("SELECT * FROM observations WHERE memory_session_id=? ORDER"
                                                         " BY created_at_epoch", (mid,))] if mid else []
        sums = [dict(r) for r in st.db.execute("SELECT * FROM session_summaries WHERE memory_session_id=? ORDER BY"
                                               " created_at_epoch", (mid,))] if mid else []
        pending = [dict(r) for r in st.db.execute("SELECT id, message_type, tool_name, prompt_number, status,"
                                                  " retry_count, created_at_epoch FROM pending_messages WHERE"
                                                  " session_db_id=? ORDER BY id", (s["id"],))]
    return {"session": s, "prompts": prompts_, "observations": obs, "summaries": sums, "pending": pending}


# ---- overview ---------------------------------------------------------------------------------------------
_started = time.time()


def stats(root: Path | str) -> dict:
    from .worker import status as worker_status
    with _open(root) as st:
        db = st.stats()
    ws = worker_status(root)
    return {"worker": {"running": ws["worker"]["running"], "uptime": int(time.time() - _started),
                       "queue": ws["queue"]}, "database": db}


def projects(root: Path | str, platform_source: str | None = None) -> dict:
    with _open(root) as st:
        if platform_source:
            src = normalize_platform_source(platform_source)
            p = st.get_all_projects(src)
            return {"projects": p, "sources": [src], "projectsBySource": {src: p}}
        return st.get_project_catalog()


def processing_status(root: Path | str) -> dict:
    from .worker import status as worker_status
    ws = worker_status(root)
    q = ws["queue"]
    return {"isProcessing": bool(q["pending"] or q["processing"]), "queueDepth": q["pending"] + q["processing"],
            "failed": q["failed"], "workerRunning": ws["worker"]["running"], "parkedSessions": 0,
            "health": ws["health"]}


def health(root: Path | str) -> dict:
    from . import health as h
    with _open(root) as st:
        state = h.read(st)
        return {"state": state, "unhealthy": h.is_unhealthy(state), "warning": h.warning_text(st)}


def vector_status(root: Path | str, deep: bool = False) -> dict:
    """The semantic index: backend, embedder, documents; ``deep`` runs a real query round trip."""
    settings = load_settings(root)
    if not settings.get("vectors"):
        return {"status": "disabled", "connected": False, "details": "vectors = false in [recall]", "deep": deep}
    from .vectorsync import VectorSync, index_dir
    with _open(root) as st:
        vs = VectorSync(root, st)
        idx = vs.index()
        if idx is None:
            return {"status": "unhealthy", "connected": False, "details": "vector support is not installed",
                    "deep": deep}
        docs = int(st.db.execute("SELECT COUNT(*) FROM vector_docs").fetchone()[0])
        out = {"status": "healthy", "connected": True, "backend": idx.backend, "embedder": idx.embedder,
               "documents": docs, "vectors": len(idx), "stale": bool(idx.stale), "path": str(index_dir(root)),
               "deep": deep, "details": "local vector index"}
        if deep:
            try:
                vs.query("health probe", 1)
                out["details"] = "semantic search round-trip succeeded"
            except Exception as exc:  # noqa: BLE001
                out.update(status="unhealthy", details=f"semantic probe failed: {exc}")
        return out


# ---- context ----------------------------------------------------------------------------------------------
def context_preview(root: Path | str, project: str | None = None, colors: bool = False) -> str:
    from .context import generate_context
    from .projects import project_context
    project = project or project_context(str(root)).primary
    return generate_context(root, cwd=str(root), projects=[project], for_human=colors)


def context_inject(root: Path | str, projects_: list[str] | None = None, *, colors: bool = False, full: bool = False,
                   platform_source: str | None = None) -> str:
    from .context import inject_context
    from .projects import project_context
    return inject_context(root, projects_ or project_context(str(root)).all_projects, platform_source=platform_source,
                          for_human=colors, full=full)


def search(root: Path | str, params: dict) -> dict:
    """``/api/recall/search``: JSON results when ``format=json``, else the agent index text."""
    from .search import RecallSearch
    with RecallSearch(root) as s:
        return s.search(params)


def timeline(root: Path | str, params: dict) -> dict:
    from .search import RecallSearch
    with RecallSearch(root) as s:
        return s.timeline(params)


# ---- settings ---------------------------------------------------------------------------------------------
def get_settings(root: Path | str) -> dict:
    s = load_settings(root)
    mode = load_mode(s.get("mode"))
    return {"settings": s, "defaults": DEFAULTS, "modes": available_modes(),
            "mode": {"id": mode.id, "name": mode.name, "types": mode.observation_types,
                     "concepts": mode.observation_concepts}}


def update_settings(root: Path | str, updates: dict) -> dict:
    from .modes import clear_cache
    saved = save_settings(root, updates)
    clear_cache()
    return {"success": True, "settings": saved}


# ---- memories, import/export -------------------------------------------------------------------------------
def save_memory(root: Path | str, text: str, title: str | None = None, project: str | None = None,
                metadata: dict | None = None) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is required")
    with _open(root) as st:
        return mcp.save_memory(st, text.strip(), title=title, project=project, metadata=metadata, root=Path(root))


def export_memories(root: Path | str, query: str | None = None, project: str | None = None) -> dict:
    """Everything matching ``query`` (all records when empty), with the sessions they belong to."""
    with _open(root) as st:
        if query:
            from .search import RecallSearch
            with RecallSearch(root, st) as s:
                res = s.search({"query": query, "format": "json", "limit": 999_999,
                                **({"project": project} if project else {})})
            obs, sums, prs = res.get("observations", []), res.get("sessions", []), res.get("prompts", [])
        else:
            cond = " WHERE project = ?" if project else ""
            args = (project,) if project else ()
            obs = [dict(r) for r in st.db.execute(f"SELECT * FROM observations{cond} ORDER BY id", args)]
            sums = [dict(r) for r in st.db.execute(f"SELECT * FROM session_summaries{cond} ORDER BY id", args)]
            prs = [dict(r) for r in st.db.execute(
                "SELECT up.*, s.project, s.memory_session_id, s.platform_source FROM user_prompts up JOIN sdk_sessions s"
                f" ON up.session_db_id = s.id{' WHERE s.project = ?' if project else ''} ORDER BY up.id", args)]
        mids = list(dict.fromkeys([o["memory_session_id"] for o in obs if o.get("memory_session_id")]
                                  + [s["memory_session_id"] for s in sums if s.get("memory_session_id")]
                                  + [p["memory_session_id"] for p in prs if p.get("memory_session_id")]))
        ses = st.get_sdk_sessions_by_memory_ids(mids)
    now = schema.now_ms()
    return {"exportedAt": schema.iso(now), "exportedAtEpoch": now, "query": query or "", "project": project,
            "totalObservations": len(obs), "totalSessions": len(ses), "totalSummaries": len(sums),
            "totalPrompts": len(prs), "observations": obs, "sessions": ses, "summaries": sums, "prompts": prs}


def import_memories(root: Path | str, data: dict) -> dict:
    stats_ = {k: 0 for k in ("sessionsImported", "sessionsSkipped", "summariesImported", "summariesSkipped",
                             "observationsImported", "observationsSkipped", "promptsImported", "promptsSkipped")}
    new_obs: list[int] = []
    with _open(root) as st:
        by_key: dict[tuple[str, str], int] = {}
        by_content: dict[str, list[tuple[int, str]]] = {}
        for s in data.get("sessions") or []:
            if not isinstance(s, dict) or not isinstance(s.get("content_session_id"), str):
                continue
            r = st.import_sdk_session(s)
            src = normalize_platform_source(s.get("platform_source"))
            by_key[(src, s["content_session_id"])] = r["id"]
            by_content.setdefault(s["content_session_id"], []).append((r["id"], src))
            stats_["sessionsImported" if r["imported"] else "sessionsSkipped"] += 1
        for s in data.get("summaries") or []:
            if isinstance(s, dict) and s.get("memory_session_id"):
                r = st.import_session_summary(s)
                stats_["summariesImported" if r["imported"] else "summariesSkipped"] += 1
        for o in data.get("observations") or []:
            if isinstance(o, dict) and o.get("memory_session_id"):
                r = st.import_observation(o)
                stats_["observationsImported" if r["imported"] else "observationsSkipped"] += 1
                if r["imported"]:
                    new_obs.append(r["id"])
        if stats_["observationsImported"]:
            st.rebuild_fts()
        for p in data.get("prompts") or []:
            if not isinstance(p, dict) or not isinstance(p.get("content_session_id"), str):
                continue
            rec = dict(p)
            src = normalize_platform_source(p["platform_source"]) if isinstance(p.get("platform_source"), str) else None
            ctx = by_key.get((src, p["content_session_id"])) if src else (
                by_content.get(p["content_session_id"], [])[0][0] if len(by_content.get(p["content_session_id"], []))
                == 1 else None)
            if ctx is not None:
                rec["session_db_id"] = ctx
            r = st.import_user_prompt(rec)
            stats_["promptsImported" if r["imported"] else "promptsSkipped"] += 1
        if new_obs and load_settings(root).get("vectors"):
            try:
                from .vectorsync import VectorSync
                VectorSync(root, st).sync_observations(new_obs)
            except Exception as exc:  # noqa: BLE001
                log.warning("imported observations not indexed yet: %s", exc)
    return {"success": True, "stats": stats_}


# ---- logs -------------------------------------------------------------------------------------------------
def logs(root: Path | str, lines: int = 200) -> dict:
    path = Path(root) / ".cairn" / "recall" / "worker.log"
    try:
        text = path.read_text(errors="replace", encoding="utf-8")
    except OSError:
        return {"path": str(path), "lines": []}
    return {"path": str(path), "lines": text.splitlines()[-max(1, min(int(lines), 5000)):]}


def clear_logs(root: Path | str) -> dict:
    path = Path(root) / ".cairn" / "recall" / "worker.log"
    if path.exists():
        path.write_text("", encoding="utf-8")
    return {"success": True}


# ---- queue maintenance ------------------------------------------------------------------------------------
def queue(root: Path | str) -> dict:
    """The pending queue by session (what `check-pending-queue` showed)."""
    with _open(root) as st:
        rows = [dict(r) for r in st.db.execute(
            "SELECT pm.session_db_id, s.content_session_id, s.project, pm.status, pm.message_type, COUNT(*) AS n,"
            " MIN(pm.created_at_epoch) AS oldest, MAX(pm.retry_count) AS max_retries FROM pending_messages pm"
            " LEFT JOIN sdk_sessions s ON s.id = pm.session_db_id GROUP BY pm.session_db_id, pm.status, pm.message_type"
            " ORDER BY oldest")]
        return {"totals": st.queue_status(), "groups": rows}


def clear_queue(root: Path | str, failed_only: bool = True, session_db_id: int | None = None) -> dict:
    with _open(root) as st:
        return {"removed": st.clear_pending(session_db_id, failed_only=failed_only)}


def retry_failed(root: Path | str) -> dict:
    with _open(root) as st:
        n = st.db.execute("UPDATE pending_messages SET status='pending', retry_count=0 WHERE status='failed'").rowcount
    return {"requeued": n}


# ---- live stream ------------------------------------------------------------------------------------------
def _max_ids(st: Store) -> dict:
    one = lambda sql: int(st.db.execute(sql).fetchone()[0] or 0)
    return {"observation": one("SELECT MAX(id) FROM observations"), "summary": one("SELECT MAX(id) FROM"
                                                                                   " session_summaries"),
            "prompt": one("SELECT MAX(id) FROM user_prompts")}


def stream(root: Path | str, stop_event: threading.Event | None = None, *, poll_seconds: float = 1.0,
           project: str | None = None, since: dict | None = None, heartbeat_seconds: float = 15.0) -> Iterator[dict]:
    """Live events for the UI: ``connected``, ``initial_load``, then ``new_observation`` /
    ``new_summary`` / ``new_prompt`` / ``processing_status`` as they happen (and ``heartbeat``)."""
    stop_event = stop_event or threading.Event()
    yield {"type": "connected", "timestamp": schema.now_ms()}
    with _open(root) as st:
        cursor = dict(since) if since else _max_ids(st)
        yield {"type": "initial_load", "projects": st.get_all_projects(), "timestamp": schema.now_ms()}
    last_status = None
    last_beat = time.time()
    while not stop_event.is_set():
        try:
            with _open(root) as st:
                for r in st.db.execute(
                        "SELECT o.*, COALESCE(s.platform_source, 'claude') AS platform_source, s.content_session_id"
                        " AS session_id FROM observations o LEFT JOIN sdk_sessions s ON o.memory_session_id ="
                        " s.memory_session_id WHERE o.id > ? ORDER BY o.id", (cursor["observation"],)):
                    row = _sanitize(dict(r))
                    cursor["observation"] = row["id"]
                    if not project or row["project"] == project:
                        yield {"type": "new_observation", "observation": row, "timestamp": schema.now_ms()}
                for r in st.db.execute(
                        "SELECT ss.*, COALESCE(s.platform_source, 'claude') AS platform_source, s.content_session_id AS"
                        " session_id FROM session_summaries ss LEFT JOIN sdk_sessions s ON ss.memory_session_id ="
                        " s.memory_session_id WHERE ss.id > ? ORDER BY ss.id", (cursor["summary"],)):
                    row = dict(r)
                    cursor["summary"] = row["id"]
                    if not project or row["project"] == project:
                        yield {"type": "new_summary", "summary": row, "timestamp": schema.now_ms()}
                for r in st.db.execute(
                        "SELECT up.id, up.content_session_id, s.project, COALESCE(s.platform_source, 'claude') AS"
                        " platform_source, up.prompt_number, up.prompt_text, up.created_at_epoch FROM user_prompts up"
                        " JOIN sdk_sessions s ON up.session_db_id = s.id WHERE up.id > ? ORDER BY up.id",
                        (cursor["prompt"],)):
                    row = dict(r)
                    cursor["prompt"] = row["id"]
                    if row["prompt_text"].strip() and (not project or row["project"] == project):
                        yield {"type": "new_prompt", "prompt": row, "timestamp": schema.now_ms()}
                q = st.queue_status()
            status_ = {"isProcessing": bool(q["pending"] or q["processing"]), "queueDepth": q["pending"] + q["processing"]}
            if status_ != last_status:
                last_status = status_
                yield {"type": "processing_status", **status_, "timestamp": schema.now_ms()}
            if time.time() - last_beat >= heartbeat_seconds:
                last_beat = time.time()
                yield {"type": "heartbeat", "timestamp": schema.now_ms()}
        except Exception as exc:  # noqa: BLE001 - a transient read error never ends the stream
            log.debug("recall stream read failed: %s", exc)
        stop_event.wait(poll_seconds)


def sse(event: dict) -> str:
    """One event in text/event-stream framing."""
    return f"data: {json.dumps(event, default=str)}\n\n"

"""The Sessions screen's data, in the shapes Cairn's UI consumes (mounted under ``/sessions/...``).

  feed(root, cursor, limit, type, concept, file, q)  -> {items: [item], next_cursor}
  stats(root)                                         -> {sessions, observations, summaries, prompts, by_type,
                                                          by_concept, tokens{discovery, read, saved}, top_files}
  session(root, sid)                                  -> {session, prompts, observations, summaries} | None
  search(root, q, type, limit)                        -> {results: [item + score]}
  context(root)                                       -> {markdown}
  stream(root, stop_event) / ui_events(worker_event)  -> {type: "observation"|"summary"|"prompt"|"status", ...}
  ingest_remote(root, events)                         -> {received, accepted, skipped, duplicates, rejected[]}
                                                         (POST /sessions/events; raises IngestError)

An item is ``{kind, id, ts, session_id, ...}``: an observation adds type, title, subtitle, narrative,
facts[], concepts[], files_read[], files_modified[], prompt_number, tokens{discovery, read}; a
summary adds request, investigated, learned, completed, next_steps, files_read[], files_edited[]; a
prompt adds text, prompt_number. ``ts`` is epoch seconds.
"""
from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .context import observation_tokens
from .projects import project_context
from .remote import IngestError, ingest_events  # noqa: F401 - IngestError is part of this module's API
from .store import Store, parse_list

KINDS = ("observation", "summary", "prompt")
MAX_LIMIT = 200


def _secs(ms: Any) -> float:
    return round((ms or 0) / 1000.0, 3)


def observation_item(r: dict) -> dict:
    return {"kind": "observation", "id": r["id"], "ts": _secs(r.get("created_at_epoch")),
            "session_id": r.get("content_session_id") or r.get("session_id"), "type": r.get("type"),
            "title": r.get("title"), "subtitle": r.get("subtitle"), "narrative": r.get("narrative"),
            "facts": parse_list(r.get("facts")), "concepts": parse_list(r.get("concepts")),
            "files_read": parse_list(r.get("files_read")), "files_modified": parse_list(r.get("files_modified")),
            "prompt_number": r.get("prompt_number"), "project": r.get("project"),
            "derived": '"derived": true' in (r.get("metadata") or ""),
            "tokens": {"discovery": int(r.get("discovery_tokens") or 0), "read": observation_tokens(r)}}


def summary_item(r: dict) -> dict:
    return {"kind": "summary", "id": r["id"], "ts": _secs(r.get("created_at_epoch")),
            "session_id": r.get("content_session_id") or r.get("session_id"), "request": r.get("request"),
            "investigated": r.get("investigated"), "learned": r.get("learned"), "completed": r.get("completed"),
            "next_steps": r.get("next_steps"), "notes": r.get("notes"), "files_read": parse_list(r.get("files_read")),
            "files_edited": parse_list(r.get("files_edited")), "prompt_number": r.get("prompt_number"),
            "project": r.get("project")}


def prompt_item(r: dict) -> dict:
    return {"kind": "prompt", "id": r["id"], "ts": _secs(r.get("created_at_epoch")),
            "session_id": r.get("content_session_id"), "text": r.get("prompt_text"),
            "prompt_number": r.get("prompt_number"), "project": r.get("project")}


# ---- feed ------------------------------------------------------------------------------------------------
def _split(v: str | list | None) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def _cursor(c: str | None) -> tuple[int, int, int] | None:
    """``<epoch_ms>:<kind index>:<id>`` -> sortable key (newest first)."""
    if not c:
        return None
    try:
        ts, kind, rid = c.split(":")
        return int(ts), int(kind), int(rid)
    except ValueError:
        return None


def _key(ts_ms: int, kind: str, rid: int) -> tuple[int, int, int]:
    return ts_ms, KINDS.index(kind), rid


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def feed(root: Path | str, cursor: str | None = None, limit: int = 50, type: str | list | None = None,
         concept: str | list | None = None, file: str | None = None, q: str | None = None) -> dict:
    """Newest-first mixed feed with keyset pagination (``next_cursor`` is None on the last page)."""
    limit = max(1, min(int(limit or 50), MAX_LIMIT))
    types = _split(type)
    kinds_wanted = {k for k in ("summary", "prompt") if k in types}
    obs_types = [t for t in types if t not in ("summary", "prompt", "observation")]
    want_obs = not types or bool(obs_types) or "observation" in types
    want_sum = (not types or "summary" in kinds_wanted) and not concept
    want_pr = (not types or "prompt" in kinds_wanted) and not concept and not file
    after = _cursor(cursor)
    before_ts = after[0] if after else None
    fetch = limit + 1
    items: list[tuple[tuple[int, int, int], dict]] = []
    with Store.open(root) as st:
        if want_obs:
            cond, args = [], []
            if obs_types:
                cond.append(f"o.type IN ({','.join('?' * len(obs_types))})")
                args += obs_types
            concepts = _split(concept)
            if concepts:
                cond.append("(" + " OR ".join(["EXISTS (SELECT 1 FROM json_each(o.concepts) WHERE value = ?)"]
                                              * len(concepts)) + ")")
                args += concepts
            if file:
                cond.append("(EXISTS (SELECT 1 FROM json_each(o.files_read) WHERE value LIKE ? ESCAPE '\\') OR EXISTS"
                            " (SELECT 1 FROM json_each(o.files_modified) WHERE value LIKE ? ESCAPE '\\'))")
                args += [_like(file), _like(file)]
            if q:
                cond.append("(o.title LIKE ? ESCAPE '\\' OR o.subtitle LIKE ? ESCAPE '\\' OR o.narrative LIKE ? ESCAPE"
                            " '\\' OR o.facts LIKE ? ESCAPE '\\')")
                args += [_like(q)] * 4
            if before_ts is not None:
                cond.append("o.created_at_epoch <= ?")
                args.append(before_ts)
            where = f"WHERE {' AND '.join(cond)}" if cond else ""
            for r in st.db.execute(
                    "SELECT o.*, s.content_session_id FROM observations o LEFT JOIN sdk_sessions s ON"
                    f" s.memory_session_id = o.memory_session_id {where} ORDER BY o.created_at_epoch DESC, o.id DESC"
                    " LIMIT ?", (*args, fetch)):
                r = dict(r)
                items.append((_key(r["created_at_epoch"], "observation", r["id"]), observation_item(r)))
        if want_sum:
            cond, args = [], []
            if file:
                cond.append("(EXISTS (SELECT 1 FROM json_each(ss.files_read) WHERE value LIKE ? ESCAPE '\\') OR EXISTS"
                            " (SELECT 1 FROM json_each(ss.files_edited) WHERE value LIKE ? ESCAPE '\\'))")
                args += [_like(file), _like(file)]
            if q:
                cond.append("(" + " OR ".join(f"ss.{c} LIKE ? ESCAPE '\\'" for c in ("request", "investigated", "learned",
                                                                                   "completed", "next_steps")) + ")")
                args += [_like(q)] * 5
            if before_ts is not None:
                cond.append("ss.created_at_epoch <= ?")
                args.append(before_ts)
            where = f"WHERE {' AND '.join(cond)}" if cond else ""
            for r in st.db.execute(
                    "SELECT ss.*, s.content_session_id FROM session_summaries ss LEFT JOIN sdk_sessions s ON"
                    f" s.memory_session_id = ss.memory_session_id {where} ORDER BY ss.created_at_epoch DESC, ss.id DESC"
                    " LIMIT ?", (*args, fetch)):
                r = dict(r)
                items.append((_key(r["created_at_epoch"], "summary", r["id"]), summary_item(r)))
        if want_pr:
            cond, args = ["length(trim(up.prompt_text)) > 0"], []
            if q:
                cond.append("up.prompt_text LIKE ? ESCAPE '\\'")
                args.append(_like(q))
            if before_ts is not None:
                cond.append("up.created_at_epoch <= ?")
                args.append(before_ts)
            for r in st.db.execute(
                    "SELECT up.*, s.project FROM user_prompts up JOIN sdk_sessions s ON up.session_db_id = s.id"
                    f" WHERE {' AND '.join(cond)} ORDER BY up.created_at_epoch DESC, up.id DESC LIMIT ?",
                    (*args, fetch)):
                r = dict(r)
                items.append((_key(r["created_at_epoch"], "prompt", r["id"]), prompt_item(r)))
    items.sort(key=lambda it: it[0], reverse=True)
    if after:
        items = [it for it in items if it[0] < after]
    page = items[:limit]
    next_cursor = None
    if len(items) > limit:
        ts, k, rid = page[-1][0]
        next_cursor = f"{ts}:{k}:{rid}"
    return {"items": [it[1] for it in page], "next_cursor": next_cursor}


# ---- stats -----------------------------------------------------------------------------------------------
def stats(root: Path | str, top: int = 15) -> dict:
    with Store.open(root) as st:
        one = lambda sql: int(st.db.execute(sql).fetchone()[0] or 0)
        by_type = {r[0]: r[1] for r in st.db.execute("SELECT type, COUNT(*) FROM observations GROUP BY type"
                                                      " ORDER BY COUNT(*) DESC")}
        by_concept = {r[0]: r[1] for r in st.db.execute(
            "SELECT j.value, COUNT(*) FROM observations o, json_each(o.concepts) j GROUP BY j.value"
            " ORDER BY COUNT(*) DESC")}
        discovery = one("SELECT SUM(discovery_tokens) FROM observations")
        read = sum(observation_tokens(dict(r)) for r in st.db.execute(
            "SELECT title, subtitle, narrative, facts FROM observations"))
        files: dict[str, dict[str, int]] = {}
        for col, key in (("files_read", "reads"), ("files_modified", "modifies")):
            for path, n in st.db.execute(f"SELECT j.value, COUNT(*) FROM observations o, json_each(o.{col}) j"
                                         f" WHERE o.{col} LIKE '[%' GROUP BY j.value"):
                files.setdefault(path, {"path": path, "reads": 0, "modifies": 0})[key] = n
        return {"sessions": one("SELECT COUNT(*) FROM sdk_sessions"), "observations": one("SELECT COUNT(*) FROM"
                                                                                           " observations"),
                "summaries": one("SELECT COUNT(*) FROM session_summaries"),
                "prompts": one("SELECT COUNT(*) FROM user_prompts WHERE length(trim(prompt_text)) > 0"),
                "by_type": by_type, "by_concept": by_concept,
                "tokens": {"discovery": discovery, "read": read, "saved": max(0, discovery - read)},
                "top_files": sorted(files.values(), key=lambda f: -(f["modifies"] * 2 + f["reads"]))[:top],
                "queue": st.queue_status()}


# ---- one session ---------------------------------------------------------------------------------------------
def session(root: Path | str, sid: str | int) -> dict | None:
    """``sid`` is the agent's session id (or the numeric row id)."""
    with Store.open(root) as st:
        row = None
        if isinstance(sid, int) or str(sid).isdigit():
            row = st.get_session_by_id(int(sid))
        if row is None:
            found = st.db.execute("SELECT id FROM sdk_sessions WHERE content_session_id=? ORDER BY id DESC LIMIT 1",
                                  (str(sid),)).fetchone()
            row = st.get_session_by_id(found[0]) if found else None
        if row is None:
            return None
        mid = row.get("memory_session_id")
        prompts = [prompt_item({**dict(r), "project": row["project"]}) for r in st.db.execute(
            "SELECT * FROM user_prompts WHERE session_db_id=? ORDER BY prompt_number", (row["id"],))]
        obs = [observation_item({**dict(r), "content_session_id": row["content_session_id"]}) for r in st.db.execute(
            "SELECT * FROM observations WHERE memory_session_id=? ORDER BY created_at_epoch, id", (mid,))] if mid else []
        sums = [summary_item({**dict(r), "content_session_id": row["content_session_id"]}) for r in st.db.execute(
            "SELECT * FROM session_summaries WHERE memory_session_id=? ORDER BY created_at_epoch", (mid,))] if mid else []
        pending = st.pending_count(row["id"])
    ends = [i["ts"] for i in (*prompts, *obs, *sums)]
    first = next((p["text"] for p in prompts if (p["text"] or "").strip()), None)
    return {"session": {"id": row["content_session_id"], "db_id": row["id"],
                        "title": row.get("custom_title") or first or row.get("user_prompt") or "Agent session",
                        "agent": row.get("platform_source"), "model": row.get("observed_model"),
                        "branch": row.get("branch"), "project": row.get("project"),
                        "started": _secs(row.get("started_at_epoch")),
                        "ended": _secs(row["completed_at_epoch"]) if row.get("completed_at_epoch") else
                        (max(ends) if ends else None),
                        "status": row.get("status"), "pending": pending, "pushed_by": row.get("pushed_by")},
            "prompts": prompts, "observations": obs, "summaries": sums}


def sessions(root: Path | str, limit: int = 50, offset: int = 0) -> dict:
    with Store.open(root) as st:
        rows = st.list_sessions(max(1, min(int(limit), MAX_LIMIT)) + 1, int(offset or 0))
    items = [{"id": r["content_session_id"], "db_id": r["id"], "title": r.get("custom_title") or r.get("user_prompt"),
              "agent": r.get("platform_source"), "model": r.get("observed_model"), "status": r.get("status"),
              "started": _secs(r.get("started_at_epoch")),
              "ended": _secs(r["completed_at_epoch"]) if r.get("completed_at_epoch") else None,
              "prompts": r["prompt_count"], "observations": r["observation_count"], "summaries": r["summary_count"],
              "pending": r["pending_count"], "pushed_by": r.get("pushed_by")} for r in rows]
    return {"items": items[:limit], "has_more": len(items) > limit}


# ---- search ------------------------------------------------------------------------------------------------
def search(root: Path | str, q: str, type: str | None = None, limit: int = 20) -> dict:
    """Ranked results across observations, summaries and prompts (``score`` in 0..1, higher is better)."""
    from .search import RecallSearch
    limit = max(1, min(int(limit or 20), MAX_LIMIT))
    types = _split(type)
    category = None
    params: dict[str, Any] = {"query": q, "format": "json", "limit": limit}
    if types and all(t in ("observation", "observations") for t in types):
        category = "observations"
    elif types and all(t in ("summary", "summaries", "sessions") for t in types):
        category = "sessions"
    elif types and all(t in ("prompt", "prompts") for t in types):
        category = "prompts"
    elif types:
        params["obs_type"] = ",".join(t for t in types if t not in ("summary", "prompt", "observation"))
        category = "observations"
    if category:
        params["type"] = category
    if not (q or "").strip():
        return {"results": []}
    with RecallSearch(root) as s:
        res = s.search(params)
        scores: dict[tuple[str, int], float] = {}
        vs = s.vectors()
        if vs is not None and res.get("strategy") == "vectors":
            hits = vs.query(q, max(limit * 3, 30))
            for rid, dist, meta in zip(hits["ids"], hits["distances"], hits["metadatas"]):
                kind = {"observation": "observation", "session_summary": "summary", "user_prompt": "prompt"}.get(
                    meta["doc_type"])
                scores[(kind, rid)] = round(max(0.0, 1.0 - dist), 4)
        with Store.open(root) as st:
            sess = {r[0]: r[1] for r in st.db.execute("SELECT memory_session_id, content_session_id FROM sdk_sessions")}
    ranked = [observation_item({**o, "content_session_id": sess.get(o.get("memory_session_id"))})
              for o in res.get("observations", [])]
    ranked += [summary_item({**x, "content_session_id": sess.get(x.get("memory_session_id"))})
               for x in res.get("sessions", [])]
    ranked += [prompt_item(p) for p in res.get("prompts", [])]
    n = max(1, len(ranked))
    for i, item in enumerate(ranked):
        item["score"] = scores.get((item["kind"], item["id"]), round(1.0 - i / (n + 1), 4))
    ranked.sort(key=lambda it: -it["score"])
    return {"results": ranked[:limit], "strategy": res.get("strategy")}


# ---- context -----------------------------------------------------------------------------------------------
def context(root: Path | str, full: bool = False) -> dict:
    from .context import inject_context
    return {"markdown": inject_context(root, project_context(str(root)).all_projects, full=full)}


# ---- capture from other machines ---------------------------------------------------------------------------
def ingest_remote(root: Path | str, events: Any, *, caller: str | None = None) -> dict:
    """Hook events pushed by agents on other machines (``cairn sessions push``), recorded exactly as
    local hooks record them. ``events``: a list (or ``{"events": [...]}``) of at most 500
    ``{event, platform, payload, ts?, id?, session_id?}``. Raises ``IngestError`` (``.status`` 400 or
    413) for an unusable request; a bad event is listed in ``rejected`` and the rest are recorded.
    With ``caller`` (the pushing user's id) its sessions are ``<caller>:<session id>`` and carry
    ``pushed_by``, so no member can write into another member's session."""
    return ingest_events(root, events, caller=caller)


# ---- live events -------------------------------------------------------------------------------------------
def ui_events(root: Path | str, worker_event: dict) -> list[dict]:
    """Turn a worker ``stored`` event into UI events (``{"type": "observation", "observation": item}`` ...)."""
    out: list[dict] = []
    ids = worker_event.get("observation_ids") or []
    with Store.open(root) as st:
        if ids:
            for r in st.db.execute(
                    "SELECT o.*, s.content_session_id FROM observations o LEFT JOIN sdk_sessions s ON s.memory_session_id"
                    f" = o.memory_session_id WHERE o.id IN ({','.join('?' * len(ids))}) ORDER BY o.id", ids):
                out.append({"type": "observation", "observation": observation_item(dict(r))})
        if worker_event.get("summary_id"):
            r = st.db.execute("SELECT ss.*, s.content_session_id FROM session_summaries ss LEFT JOIN sdk_sessions s ON"
                              " s.memory_session_id = ss.memory_session_id WHERE ss.id=?",
                              (worker_event["summary_id"],)).fetchone()
            if r:
                out.append({"type": "summary", "summary": summary_item(dict(r))})
    return out


def broadcaster(root: Path | str, emit: Callable[[dict], None]) -> Callable[[dict], None]:
    """An ``on_event`` for ``worker.run_worker``/``run_once`` that emits UI events as records are stored."""
    def on_event(ev: dict) -> None:
        if ev.get("type") == "stored":
            for e in ui_events(root, ev):
                emit(e)
    return on_event


def host_worker(root: Path | str, stop_event: threading.Event, emit: Callable[[dict], None] | None = None,
                poll_seconds: float = 1.0, router: Any = None) -> None:
    """Run the recall worker in the server process, emitting UI events for everything it stores."""
    from .worker import run_worker
    run_worker(root, stop_event, poll_seconds=poll_seconds, router=router,
               on_event=broadcaster(root, emit) if emit else None)


def stream(root: Path | str, stop_event: threading.Event | None = None, poll_seconds: float = 1.0) -> Iterator[dict]:
    """UI events from the store itself (records written by any process: hooks, detached workers)."""
    from . import viewer
    for ev in viewer.stream(root, stop_event, poll_seconds=poll_seconds):
        t = ev["type"]
        if t == "new_observation":
            yield {"type": "observation", "observation": observation_item(ev["observation"])}
        elif t == "new_summary":
            yield {"type": "summary", "summary": summary_item(ev["summary"])}
        elif t == "new_prompt":
            yield {"type": "prompt", "prompt": prompt_item(ev["prompt"])}
        elif t == "processing_status":
            yield {"type": "status", "processing": ev["isProcessing"], "queue": ev["queueDepth"]}
        elif t in ("connected", "heartbeat"):
            yield {"type": t}

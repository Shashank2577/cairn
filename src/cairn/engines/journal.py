"""Sessions layer: mirrors the recall store (``.cairn/sessions.db``, ``engines/recall``) into the read model.

Entities ``obs:<id>`` (one per observation: type, title, facts, narrative, concepts, files) and
``session:<agent session id>`` (the request, prompt count, status, latest summary); links
``part_of`` (observation -> session), ``reads`` / ``modifies`` (observation -> ``file:<path>``);
timeline events of kind ``session`` for every observation and summary.

Incremental: only observations inserted or updated since the last sync, new summaries and the
sessions they touch are re-mirrored (a deletion in the recall store triggers one full resync). When no
model is configured the queued events are first turned into derived records here, so a sync always
reflects the captured work; with a model, the recall worker does that in the background.
"""
from __future__ import annotations

import logging
import sqlite3

from ..project import Project
from ..store import Brain
from .recall import schema
from .recall.store import Store, parse_list

log = logging.getLogger("cairn.recall")

SOURCE = "sessions"
STORE_KIND = "recall"


def available(project: Project) -> bool:
    """Something has been captured in this repository."""
    return schema.store_path(project.root).exists()


def installed(project: Project) -> bool:
    """Capture hooks are wired into at least one agent for this repository."""
    from .recall.integrations import project_status
    return any(state["capture"] for state in project_status(project.root).values())


def _drain_without_model(project: Project, brain: Brain) -> dict | None:
    """Derive records for queued events when no model is available (the worker does it otherwise)."""
    try:
        from ..router import Router
        if Router(project, brain).available:
            return None
        from .recall.worker import run_once

        class _NoModel:
            available = False
        return run_once(project.root, router=_NoModel())
    except Exception as exc:  # noqa: BLE001 - mirroring still runs
        log.warning("recall drain skipped: %s", exc)
        return None


def _migrate(brain: Brain) -> None:
    """Rows mirrored by earlier capture formats are replaced by recall's own."""
    if brain.get_kv("sessions.store") == STORE_KIND:
        return
    brain.drop_source(SOURCE, kinds=("obs", "session"))
    with brain.tx() as db:
        db.execute("DELETE FROM events WHERE source=?", (SOURCE,))
    for key in ("sessions.cursor", "sessions.obs_cursor", "sessions.summary_cursor"):
        brain.set_kv(key, "0")
    brain.set_kv("sessions.store", STORE_KIND)


def _outside(path: str) -> bool:
    return path.startswith("/") or path.startswith("..") or (len(path) > 1 and path[1] == ":")


def _secs(ms) -> float:
    return (ms or 0) / 1000.0


def _obs_rows(st: Store, since_ms: int) -> list[dict]:
    return [dict(r) for r in st.db.execute(
        "SELECT o.*, s.content_session_id, COALESCE(s.platform_source, 'claude') AS platform_source FROM observations o"
        " LEFT JOIN sdk_sessions s ON s.memory_session_id = o.memory_session_id WHERE COALESCE(o.updated_at_epoch,"
        " o.created_at_epoch) > ? ORDER BY o.id", (since_ms,))]


def ingest(project: Project, brain: Brain) -> dict:
    _migrate(brain)
    path = schema.store_path(project.root)
    if not path.exists():
        return {"observations": 0, "note": "no agent sessions captured yet"}
    drained = _drain_without_model(project, brain)
    try:
        st = Store.at(path)
    except sqlite3.Error as exc:
        return {"observations": 0, "note": str(exc)}
    try:
        full = st.get_kv("readmodel.resync") == "1"
        if full:
            brain.drop_source(SOURCE, kinds=("obs", "session"))
            with brain.tx() as db:
                db.execute("DELETE FROM events WHERE source=?", (SOURCE,))
            brain.set_kv("sessions.obs_cursor", "0")
            brain.set_kv("sessions.summary_cursor", "0")
        obs_cursor = int(brain.get_kv("sessions.obs_cursor", "0") or 0)
        sum_cursor = int(brain.get_kv("sessions.summary_cursor", "0") or 0)
        known = {e["id"] for e in brain.entities("obs")}
        rows = _obs_rows(st, obs_cursor)
        summaries = [dict(r) for r in st.db.execute(
            "SELECT ss.*, s.content_session_id FROM session_summaries ss LEFT JOIN sdk_sessions s ON"
            " s.memory_session_id = ss.memory_session_id WHERE ss.id > ? ORDER BY ss.id", (sum_cursor,))]
        touched = {r["content_session_id"] for r in rows + summaries if r.get("content_session_id")}
        ents, links, events, new = [], [], [], 0
        for o in rows:
            oid = f"obs:{o['id']}"
            new += oid not in known
            session_id = f"session:{o['content_session_id']}" if o.get("content_session_id") else None
            # the read model links repository files; paths outside the repository stay in the recall store only
            read = [p for p in parse_list(o.get("files_read")) if not _outside(p)]
            modified = [p for p in parse_list(o.get("files_modified")) if not _outside(p)]
            facts, concepts = parse_list(o.get("facts")), parse_list(o.get("concepts"))
            derived = '"derived": true' in (o.get("metadata") or "")
            title = o.get("title") or "Untitled"
            meta = {"type": o["type"], "session": session_id, "facts": facts[:12], "read": read[:30],
                    "modified": modified[:30], "subtitle": o.get("subtitle"), "narrative": (o.get("narrative") or "")[:2000],
                    "concepts": concepts, "prompt_number": o.get("prompt_number"), "ts": _secs(o["created_at_epoch"]),
                    "end": _secs(o.get("updated_at_epoch") or o["created_at_epoch"]), "derived": derived,
                    "recall_id": o["id"], "project": o.get("project"), "platform": o.get("platform_source"),
                    "commands": [f[5:-1] for f in facts if f.startswith("ran `") and f.endswith("`")][:20]}
            body = " ".join([title, o.get("subtitle") or "", o.get("narrative") or "", *facts, *concepts, *modified,
                             *read])
            ents.append((oid, "obs", title[:200], None, meta, SOURCE, body[:4000]))
            if session_id:
                links.append((oid, session_id, "part_of", "EXTRACTED", 1.0, SOURCE))
            links += [(oid, f"file:{p}", "reads", "EXTRACTED", 1.0, SOURCE) for p in read[:30]]
            links += [(oid, f"file:{p}", "modifies", "EXTRACTED", 1.0, SOURCE) for p in modified[:30]]
            events.append({"id": oid, "ts": _secs(o["created_at_epoch"]), "kind": "session", "title": title[:200],
                           "body": "\n".join(facts[:8]) or (o.get("narrative") or "")[:500], "actor": "agent",
                           "refs": [oid, *([session_id] if session_id else []), *[f"file:{p}" for p in modified[:20]]],
                           "meta": {"type": o["type"], "modified": modified[:20], "derived": derived}, "source": SOURCE})
        for s in summaries:
            sid = f"summary:{s['id']}"
            session_id = f"session:{s['content_session_id']}" if s.get("content_session_id") else None
            title = s.get("request") or "Session summary"
            body = "\n".join(f"{k.replace('_', ' ').capitalize()}: {s[k]}" for k in
                             ("investigated", "learned", "completed", "next_steps") if s.get(k))
            events.append({"id": sid, "ts": _secs(s["created_at_epoch"]), "kind": "session", "title": title[:200],
                           "body": body[:2000], "actor": "agent", "refs": [sid, *([session_id] if session_id else [])],
                           "meta": {"summary": True, "prompt_number": s.get("prompt_number"),
                                    "edited": parse_list(s.get("files_edited"))[:20]}, "source": SOURCE})
        for csid in sorted(touched):
            sess = st.db.execute("SELECT * FROM sdk_sessions WHERE content_session_id=? ORDER BY id DESC LIMIT 1",
                                 (csid,)).fetchone()
            if not sess:
                continue
            sess = dict(sess)
            mid = sess.get("memory_session_id")
            prompts = [r[0] for r in st.db.execute("SELECT prompt_text FROM user_prompts WHERE session_db_id=? ORDER BY"
                                                   " prompt_number", (sess["id"],))]
            obs = [dict(r) for r in st.db.execute("SELECT files_read, files_modified, created_at_epoch, updated_at_epoch"
                                                  " FROM observations WHERE memory_session_id=?", (mid,))] if mid else []
            sums = [dict(r) for r in st.db.execute("SELECT request, investigated, learned, completed, next_steps,"
                                                   " created_at_epoch FROM session_summaries WHERE memory_session_id=?"
                                                   " ORDER BY created_at_epoch", (mid,))] if mid else []
            read = {f for r in obs for f in parse_list(r["files_read"])}
            modified = {f for r in obs for f in parse_list(r["files_modified"])}
            first = next((p for p in prompts if p.strip()), "") or sess.get("custom_title") or "Agent session"
            ends = [r["updated_at_epoch"] or r["created_at_epoch"] for r in obs] + [r["created_at_epoch"] for r in sums]
            latest = sums[-1] if sums else None
            meta = {"request": first[:300], "turns": len(prompts), "ts": _secs(sess["started_at_epoch"]),
                    "end": _secs(max(ends) if ends else sess["started_at_epoch"]), "read": len(read),
                    "modified": len(modified), "observations": len(obs), "summaries": len(sums),
                    "status": sess.get("status"), "platform": sess.get("platform_source"),
                    "project": sess.get("project"), "recall_id": sess["id"], "pushed_by": sess.get("pushed_by"),
                    "latest_summary": {k: latest[k] for k in ("request", "investigated", "learned", "completed",
                                                              "next_steps")} if latest else None}
            body = " ".join([first, *prompts[1:6], *(" ".join(str(v or "") for v in s.values()) for s in sums[-5:])])
            ents.append((f"session:{csid}", "session", first[:160], None, meta, SOURCE, body[:4000]))
        if ents:
            brain.put_entities(ents)
        if links:
            brain.link(links)
        if events:
            brain.add_events(events)
        if rows:
            brain.set_kv("sessions.obs_cursor", str(max(r.get("updated_at_epoch") or r["created_at_epoch"]
                                                        for r in rows)))
        if summaries:
            brain.set_kv("sessions.summary_cursor", str(summaries[-1]["id"]))
        if full:
            st.del_kv("readmodel.resync")
        out = {"observations": new, "updated": len(rows) - new, "summaries": len(summaries), "sessions": len(touched)}
        if drained:
            out["derived"] = drained.get("derived", 0)
        return out
    finally:
        st.close()


def for_files(brain: Brain, paths: list[str], limit: int = 5) -> list[dict]:
    """Recent agent observations that read or modified these files."""
    links = brain.links_to([f"file:{p}" for p in paths[:30]], rels=("modifies", "reads"), limit=400)
    ids = list(dict.fromkeys(l["src"] for l in sorted(links, key=lambda l: l["rel"] != "modifies")))
    out = []
    for oid in ids:
        e = brain.entity(oid)
        if e:
            out.append({"id": oid, "title": e["name"], "type": e["meta"].get("type"), "ts": e["meta"].get("ts", 0),
                        "facts": e["meta"].get("facts", [])[:3], "modified": e["meta"].get("modified", [])[:5]})
    out.sort(key=lambda o: -o["ts"])
    return out[:limit]

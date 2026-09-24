"""Sessions layer: what agents did, read from the session-capture store.

Read-only, schema-tolerant (columns detected at runtime), incremental via an id cursor.
Capture itself runs as agent hooks installed by ``cairn init``; Cairn never writes to that store.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

from ..project import Project
from ..store import Brain


def db_path() -> Path:
    base = os.environ.get("CLAUDE_MEM_DATA_DIR") or str(Path.home() / ".claude-mem")
    return Path(base) / "claude-mem.db"


def available() -> bool:
    return db_path().exists()


def can_install() -> bool:
    return bool(shutil.which("npx") and shutil.which("node"))


def install(log_dir: Path | None = None) -> tuple[bool, str]:
    """Install session capture hooks for the user's agents (user-level, one time)."""
    if available():
        return True, "already capturing"
    if not can_install():
        return False, "needs Node.js 18+ (https://nodejs.org) — then run `cairn init` again"
    try:
        res = subprocess.run(["npx", "-y", "claude-mem", "install"], capture_output=True, text=True, timeout=600,
                             input="\n")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if res.returncode == 0 and available():
        return True, "capture installed"
    if log_dir:
        (log_dir / "capture.log").write_text(res.stdout + "\n" + res.stderr)
    lines = [ln.strip() for ln in (res.stderr + "\n" + res.stdout).splitlines()
             if ln.strip() and not ln.lower().startswith("npm notice")]
    hint = lines[-1][:90] if lines else "no output"
    return False, f"not installed ({hint}); details in .cairn/capture.log"


def _cols(db: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


def _json_list(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    try:
        out = json.loads(v)
        return [str(x) for x in out] if isinstance(out, list) else [str(out)]
    except (ValueError, TypeError):
        return [s.strip() for s in str(v).split(",") if s.strip()]


def _rel(path: str, root: Path) -> str:
    p = path.replace("\\", "/")
    r = root.as_posix().rstrip("/") + "/"
    return p[len(r):] if p.startswith(r) else p.lstrip("./")


def project_names(project: Project) -> list[str]:
    names = {project.name}
    if project.cfg("sessions.project"):
        names.add(str(project.cfg("sessions.project")))
    return sorted(names)


def ingest(project: Project, brain: Brain, limit: int = 5000) -> dict:
    path = db_path()
    if not path.exists():
        return {"observations": 0, "note": "session capture not connected"}
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        return {"observations": 0, "note": str(exc)}
    db.row_factory = sqlite3.Row
    try:
        cols = _cols(db, "observations")
        if not {"id", "project"} <= cols:
            return {"observations": 0, "note": "unrecognised capture schema"}
        names = project_names(project)
        cursor = int(brain.get_kv("sessions.cursor", "0") or 0)
        marks = ",".join("?" * len(names))
        rows = db.execute(f"SELECT * FROM observations WHERE project IN ({marks}) AND id > ? ORDER BY id LIMIT ?",
                          (*names, cursor, limit)).fetchall()
        ents, links, events = [], [], []
        max_id = cursor
        for r in rows:
            d = dict(r)
            max_id = max(max_id, int(d["id"]))
            oid = f"obs:{d['id']}"
            sid = f"session:{d.get('memory_session_id') or d.get('sdk_session_id') or 'unknown'}"
            title = d.get("title") or (d.get("text") or "")[:100] or d.get("type", "observation")
            body = "\n".join(x for x in (d.get("subtitle"), d.get("narrative"), d.get("text")) if x)
            facts = _json_list(d.get("facts"))
            read = [_rel(p, project.root) for p in _json_list(d.get("files_read"))]
            modified = [_rel(p, project.root) for p in _json_list(d.get("files_modified"))]
            ts = float(d.get("created_at_epoch") or 0)
            ts = ts / 1000 if ts > 1e12 else ts
            ents.append((oid, "obs", title[:200], None, {"type": d.get("type"), "session": sid, "facts": facts[:8],
                                                         "read": read[:30], "modified": modified[:30], "ts": ts},
                         "sessions", body[:1500] + " " + " ".join(facts)))
            links.append((oid, sid, "part_of", "EXTRACTED", 1.0, "sessions"))
            links += [(oid, f"file:{p}", "reads", "EXTRACTED", 1.0, "sessions") for p in read[:30]]
            links += [(oid, f"file:{p}", "modifies", "EXTRACTED", 1.0, "sessions") for p in modified[:30]]
            events.append({"id": oid, "ts": ts, "kind": "session", "title": title[:200], "body": body[:1500],
                           "actor": d.get("agent_type") or "agent", "refs": [oid, sid] + [f"file:{p}" for p in modified[:20]],
                           "meta": {"type": d.get("type"), "modified": modified[:20]}, "source": "sessions"})
        # session summaries: one entity per session
        scols = _cols(db, "session_summaries")
        if {"memory_session_id", "project"} <= scols:
            for r in db.execute(f"SELECT * FROM session_summaries WHERE project IN ({marks})", names).fetchall():
                d = dict(r)
                sid = f"session:{d['memory_session_id']}"
                summary = {k: d.get(k) for k in ("request", "investigated", "learned", "completed", "next_steps")
                           if d.get(k)}
                ts = float(d.get("created_at_epoch") or 0)
                ts = ts / 1000 if ts > 1e12 else ts
                ents.append((sid, "session", (d.get("request") or "Agent session")[:160], None,
                             {**summary, "ts": ts}, "sessions", " ".join(str(v) for v in summary.values())))
        if ents:
            brain.put_entities(ents)
            brain.link(links)
            brain.add_events(events)
        brain.set_kv("sessions.cursor", str(max_id))
        return {"observations": len(rows)}
    finally:
        db.close()


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

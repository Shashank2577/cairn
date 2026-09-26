"""Session store operations over ``sessions.db`` (standard library only).

Every read and write the hooks, the worker, search and the UI need lives here, so the SQL is in one
place. Epochs are milliseconds; JSON list columns (facts, concepts, files_*) are stored as JSON text.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import schema
from .platforms import (
    DEFAULT_PLATFORM_SOURCE,
    normalize_platform_source,
    sort_platform_sources,
)
from .projects import git_branch
from .tags import normalize_stored_prompt_text

MAX_TOOL_PAYLOAD_BYTES = 64 * 1024
USER_PROMPT_DEDUPE_WINDOW_MS = 10_000
STALE_PROCESSING_MS = 10 * 60_000
DEF = DEFAULT_PLATFORM_SOURCE
_PSRC = f"COALESCE(NULLIF({{a}}.platform_source, ''), '{DEF}')"


def psrc(alias: str) -> str:
    return _PSRC.format(a=alias)


def content_hash(memory_session_id: str, title: str | None, narrative: str | None) -> str:
    raw = "\x00".join([memory_session_id or "", title or "", narrative or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def tool_use_hash(tool_name: str, tool_input: str | None, tool_response: str | None) -> str:
    raw = "\x00".join([tool_name or "", tool_input or "", tool_response or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def truncate_payload(value: str, max_bytes: int = MAX_TOOL_PAYLOAD_BYTES) -> str:
    data = value.encode("utf-8")
    if len(data) <= max_bytes:
        return value
    return data[:max_bytes].decode("utf-8", errors="ignore") + f"…[truncated: {len(data)} bytes]"


def parse_list(value: Any) -> list[str]:
    """A stored JSON list (or a bare string) as a list of strings."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return [str(value)]
    if isinstance(parsed, list):
        return [str(v) for v in parsed]
    return [str(parsed)]


def rollup_file_lists(observations: Iterable[dict]) -> tuple[list[str], list[str]]:
    read, edited = [], []
    for o in observations:
        for f in o.get("files_read") or []:
            if f and f not in read:
                read.append(f)
        for f in o.get("files_modified") or []:
            if f and f not in edited:
                edited.append(f)
    return read, edited


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _row(cur) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r else None


class Store:
    """A thin facade over one ``sessions.db`` connection."""

    def __init__(self, db: sqlite3.Connection, path: Path | None = None):
        self.db = db
        self.path = path

    @classmethod
    def open(cls, root: Path | str, *, project_name: str | None = None, readonly: bool = False) -> Store:
        path = schema.store_path(root)
        return cls(schema.connect(path, readonly=readonly, project_name=project_name), path)

    @classmethod
    def at(cls, path: Path | str, *, readonly: bool = False) -> Store:
        return cls(schema.connect(path, readonly=readonly), Path(path))

    def close(self) -> None:
        try:
            self.db.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def tx(self):
        return _Tx(self.db)

    # ---- kv ----------------------------------------------------------------------------------------
    def get_kv(self, key: str, default: str | None = None) -> str | None:
        r = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_kv(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO kv(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))

    def del_kv(self, key: str) -> None:
        self.db.execute("DELETE FROM kv WHERE key=?", (key,))

    # ---- sessions ----------------------------------------------------------------------------------
    def create_sdk_session(self, content_session_id: str, project: str, user_prompt: str = "",
                           custom_title: str | None = None, platform_source: str | None = None,
                           cwd: str | None = None) -> int:
        """Find-or-create the session row for an agent session id; fills a missing project/title."""
        src = normalize_platform_source(platform_source) if platform_source else DEF
        existing = self.db.execute(
            f"SELECT id, custom_title FROM sdk_sessions WHERE {psrc('sdk_sessions')} = ? AND content_session_id = ?",
            (src, content_session_id)).fetchone()
        if existing:
            if project:
                self.db.execute("UPDATE sdk_sessions SET project=? WHERE id=? AND (project IS NULL OR project='')",
                                (project, existing["id"]))
            if custom_title and existing["custom_title"] is None:
                self.db.execute("UPDATE sdk_sessions SET custom_title=? WHERE id=? AND custom_title IS NULL",
                                (custom_title, existing["id"]))
            if cwd:
                self.db.execute("UPDATE sdk_sessions SET cwd=?, branch=COALESCE(branch, ?) WHERE id=? AND cwd IS NULL",
                                (cwd, git_branch(cwd), existing["id"]))
            return int(existing["id"])
        now = schema.now_ms()
        cur = self.db.execute(
            "INSERT INTO sdk_sessions(content_session_id, memory_session_id, project, platform_source, user_prompt,"
            " custom_title, started_at, started_at_epoch, status, cwd, branch)"
            " VALUES(?, NULL, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (content_session_id, project or "", src, normalize_stored_prompt_text(user_prompt or ""), custom_title,
             schema.iso(now), now, cwd, git_branch(cwd) if cwd else None))
        return int(cur.lastrowid)

    def get_session_by_id(self, session_db_id: int) -> dict | None:
        return _row(self.db.execute(
            f"SELECT id, content_session_id, memory_session_id, project, {psrc('sdk_sessions')} AS platform_source,"
            " user_prompt, custom_title, status, observed_model, started_at, started_at_epoch, completed_at,"
            " completed_at_epoch, prompt_counter, cwd, branch, pushed_by FROM sdk_sessions WHERE id=?",
            (session_db_id,)))

    def find_session_db_id(self, content_session_id: str, platform_source: str | None) -> int | None:
        r = self.db.execute(
            f"SELECT id FROM sdk_sessions WHERE {psrc('sdk_sessions')} = ? AND content_session_id = ? LIMIT 1",
            (normalize_platform_source(platform_source), content_session_id)).fetchone()
        return int(r[0]) if r else None

    def update_memory_session_id(self, session_db_id: int, memory_session_id: str | None) -> None:
        self.db.execute("UPDATE sdk_sessions SET memory_session_id=? WHERE id=?", (memory_session_id, session_db_id))

    def ensure_memory_session_id(self, session_db_id: int, prefix: str = "recall") -> str:
        """Register (once) the id this session's observations hang off; never re-keys an existing one."""
        row = self.db.execute("SELECT memory_session_id, content_session_id FROM sdk_sessions WHERE id=?",
                              (session_db_id,)).fetchone()
        if row is None:
            raise KeyError(f"session {session_db_id} not found")
        if row["memory_session_id"]:
            return row["memory_session_id"]
        memory_id = f"{prefix}-{row['content_session_id']}-{schema.now_ms()}"
        self.db.execute("UPDATE sdk_sessions SET memory_session_id=? WHERE id=? AND memory_session_id IS NULL",
                        (memory_id, session_db_id))
        return self.db.execute("SELECT memory_session_id FROM sdk_sessions WHERE id=?",
                               (session_db_id,)).fetchone()[0]

    def mark_session_completed(self, session_db_id: int) -> None:
        now = schema.now_ms()
        self.db.execute("UPDATE sdk_sessions SET status='completed', completed_at=?, completed_at_epoch=? WHERE id=?",
                        (schema.iso(now), now, session_db_id))

    def set_session_observed_model(self, session_db_id: int, observed_model: str | None) -> None:
        if observed_model:
            self.db.execute("UPDATE sdk_sessions SET observed_model=? WHERE id=?", (observed_model, session_db_id))

    def get_sdk_sessions_by_memory_ids(self, memory_session_ids: list[str]) -> list[dict]:
        if not memory_session_ids:
            return []
        marks = ",".join("?" * len(memory_session_ids))
        return _rows(self.db.execute(
            f"SELECT id, content_session_id, memory_session_id, project, {psrc('sdk_sessions')} AS platform_source,"
            " user_prompt, custom_title, started_at, started_at_epoch, completed_at, completed_at_epoch, status"
            f" FROM sdk_sessions WHERE memory_session_id IN ({marks}) ORDER BY started_at_epoch DESC",
            memory_session_ids))

    def list_sessions(self, limit: int = 50, offset: int = 0, project: str | None = None,
                      platform_source: str | None = None) -> list[dict]:
        cond, args = [], []
        if project:
            cond.append("s.project = ?")
            args.append(project)
        if platform_source:
            cond.append(f"{psrc('s')} = ?")
            args.append(normalize_platform_source(platform_source))
        where = f"WHERE {' AND '.join(cond)}" if cond else ""
        return _rows(self.db.execute(
            f"SELECT s.id, s.content_session_id, s.memory_session_id, s.project, {psrc('s')} AS platform_source,"
            " s.user_prompt, s.custom_title, s.status, s.started_at_epoch, s.completed_at_epoch, s.observed_model,"
            " s.pushed_by, (SELECT COUNT(*) FROM user_prompts up WHERE up.session_db_id = s.id) AS prompt_count,"
            " (SELECT COUNT(*) FROM observations o WHERE o.memory_session_id = s.memory_session_id) AS observation_count,"
            " (SELECT COUNT(*) FROM session_summaries ss WHERE ss.memory_session_id = s.memory_session_id)"
            " AS summary_count, (SELECT COUNT(*) FROM pending_messages pm WHERE pm.session_db_id = s.id"
            " AND pm.status != 'failed') AS pending_count"
            f" FROM sdk_sessions s {where} ORDER BY s.started_at_epoch DESC LIMIT ? OFFSET ?",
            (*args, limit, offset)))

    def get_recent_sessions_with_status(self, project: str, limit: int = 3,
                                        platform_source: str | None = None) -> list[dict]:
        args: list[Any] = [project]
        clause = ""
        if platform_source:
            clause = f"AND {psrc('s')} = ?"
            args.append(normalize_platform_source(platform_source))
        args.append(limit)
        return _rows(self.db.execute(
            "SELECT * FROM (SELECT s.memory_session_id, s.status, s.started_at, s.started_at_epoch, s.user_prompt,"
            " CASE WHEN sum.memory_session_id IS NOT NULL THEN 1 ELSE 0 END AS has_summary"
            " FROM sdk_sessions s LEFT JOIN session_summaries sum ON s.memory_session_id = sum.memory_session_id"
            f" WHERE s.project = ? AND s.memory_session_id IS NOT NULL {clause}"
            " GROUP BY s.memory_session_id ORDER BY s.started_at_epoch DESC LIMIT ?) ORDER BY started_at_epoch ASC",
            args))

    def get_all_projects(self, platform_source: str | None = None) -> list[str]:
        sql = "SELECT DISTINCT project FROM sdk_sessions WHERE project IS NOT NULL AND project != ''"
        args: list[Any] = []
        if platform_source:
            sql += " AND COALESCE(platform_source, ?) = ?"
            args += [DEF, normalize_platform_source(platform_source)]
        return [r[0] for r in self.db.execute(sql + " ORDER BY project ASC", args)]

    def get_project_catalog(self) -> dict:
        rows = self.db.execute(
            f"SELECT COALESCE(platform_source, '{DEF}') AS platform_source, project, MAX(started_at_epoch) AS latest"
            " FROM sdk_sessions WHERE project IS NOT NULL AND project != ''"
            f" GROUP BY COALESCE(platform_source, '{DEF}'), project ORDER BY latest DESC").fetchall()
        projects: list[str] = []
        by_source: dict[str, list[str]] = {}
        for r in rows:
            src = normalize_platform_source(r["platform_source"])
            by_source.setdefault(src, [])
            if r["project"] not in by_source[src]:
                by_source[src].append(r["project"])
            if r["project"] not in projects:
                projects.append(r["project"])
        sources = sort_platform_sources(list(by_source))
        return {"projects": projects, "sources": sources, "projectsBySource": {s: by_source.get(s, []) for s in sources}}

    # ---- prompts -----------------------------------------------------------------------------------
    def _resolve_session(self, content_session_id: str, session_db_id: int | None,
                         platform_source: str | None = None) -> int | None:
        if session_db_id is not None:
            return session_db_id
        if platform_source:
            return self.find_session_db_id(content_session_id, platform_source)
        rows = self.db.execute("SELECT id FROM sdk_sessions WHERE content_session_id=?", (content_session_id,)).fetchall()
        return int(rows[0][0]) if len(rows) == 1 else None

    def get_prompt_number(self, content_session_id: str, session_db_id: int | None = None) -> int:
        sid = self._resolve_session(content_session_id, session_db_id)
        if sid is not None:
            return int(self.db.execute("SELECT COUNT(*) FROM user_prompts WHERE session_db_id=?", (sid,)).fetchone()[0])
        return int(self.db.execute("SELECT COUNT(*) FROM user_prompts WHERE content_session_id=?",
                                   (content_session_id,)).fetchone()[0])

    def save_user_prompt(self, content_session_id: str, prompt_number: int, prompt_text: str,
                         session_db_id: int | None = None, *, raw: bool = False) -> int:
        """Store one numbered prompt. ``raw`` keeps the text as given (an entirely private prompt is
        stored empty so the observations of that turn are withheld)."""
        now = schema.now_ms()
        sid = self._resolve_session(content_session_id, session_db_id)
        text = prompt_text if raw else normalize_stored_prompt_text(prompt_text)
        cur = self.db.execute(
            "INSERT INTO user_prompts(session_db_id, content_session_id, prompt_number, prompt_text, created_at,"
            " created_at_epoch) VALUES(?,?,?,?,?,?)", (sid, content_session_id, prompt_number, text, schema.iso(now), now))
        if sid is not None:
            self.db.execute("UPDATE sdk_sessions SET prompt_counter=? WHERE id=?", (prompt_number, sid))
            if prompt_number == 1 or not self.db.execute("SELECT user_prompt FROM sdk_sessions WHERE id=?",
                                                         (sid,)).fetchone()[0]:
                self.db.execute("UPDATE sdk_sessions SET user_prompt=? WHERE id=? AND (user_prompt IS NULL OR"
                                " user_prompt='')", (text, sid))
        return int(cur.lastrowid)

    def get_user_prompt(self, content_session_id: str, prompt_number: int, session_db_id: int | None = None) -> str | None:
        sid = self._resolve_session(content_session_id, session_db_id)
        if sid is not None:
            r = self.db.execute("SELECT prompt_text FROM user_prompts WHERE session_db_id=? AND prompt_number=? LIMIT 1",
                                (sid, prompt_number)).fetchone()
        else:
            r = self.db.execute("SELECT prompt_text FROM user_prompts WHERE content_session_id=? AND prompt_number=?"
                                " LIMIT 1", (content_session_id, prompt_number)).fetchone()
        return r[0] if r else None

    def get_latest_user_prompt(self, content_session_id: str, session_db_id: int | None = None) -> dict | None:
        sid = self._resolve_session(content_session_id, session_db_id)
        where, arg = ("up.session_db_id = ?", sid) if sid is not None else ("up.content_session_id = ?", content_session_id)
        return _row(self.db.execute(
            f"SELECT up.*, s.memory_session_id, s.project, COALESCE(s.platform_source, '{DEF}') AS platform_source"
            f" FROM user_prompts up JOIN sdk_sessions s ON up.session_db_id = s.id WHERE {where}"
            " ORDER BY up.created_at_epoch DESC, up.id DESC LIMIT 1", (arg,)))

    def get_latest_prompt_text(self, content_session_id: str, session_db_id: int | None = None) -> str | None:
        sid = self._resolve_session(content_session_id, session_db_id)
        where, arg = ("session_db_id = ?", sid) if sid is not None else ("content_session_id = ?", content_session_id)
        r = self.db.execute(
            f"SELECT prompt_text FROM user_prompts WHERE {where} AND prompt_text IS NOT NULL AND"
            " length(trim(prompt_text)) > 0 ORDER BY prompt_number DESC, created_at_epoch DESC LIMIT 1", (arg,)).fetchone()
        return r[0] if r else None

    def find_recent_duplicate_user_prompt(self, content_session_id: str, prompt_text: str,
                                          window_ms: int = USER_PROMPT_DEDUPE_WINDOW_MS,
                                          session_db_id: int | None = None) -> dict | None:
        sid = self._resolve_session(content_session_id, session_db_id)
        where, arg = ("up.session_db_id = ?", sid) if sid is not None else ("up.content_session_id = ?", content_session_id)
        return _row(self.db.execute(
            f"SELECT up.*, s.memory_session_id, s.project, COALESCE(s.platform_source, '{DEF}') AS platform_source"
            f" FROM user_prompts up JOIN sdk_sessions s ON up.session_db_id = s.id WHERE {where}"
            " AND up.prompt_text = ? AND up.created_at_epoch >= ? ORDER BY up.created_at_epoch DESC LIMIT 1",
            (arg, normalize_stored_prompt_text(prompt_text), schema.now_ms() - window_ms)))

    # ---- observations & summaries ------------------------------------------------------------------
    def store_observations(self, memory_session_id: str, project: str, observations: list[dict],
                           summary: dict | None = None, prompt_number: int | None = None,
                           discovery_tokens: int = 0, override_epoch: int | None = None,
                           model: str | None = None) -> dict:
        """Insert observations (deduplicated per session by title+narrative) and an optional summary,
        in one transaction. Observations without a title are skipped."""
        ts = int(override_epoch) if override_epoch else schema.now_ms()
        stamp = schema.iso(ts)
        ids: list[int] = []
        summary_id = None
        with self.tx():
            for o in observations:
                title = (o.get("title") or "").strip()
                if not title:
                    continue
                h = content_hash(memory_session_id, o.get("title"), o.get("narrative"))
                cur = self.db.execute(
                    "INSERT INTO observations(memory_session_id, project, type, title, subtitle, facts, narrative,"
                    " concepts, files_read, files_modified, prompt_number, discovery_tokens, agent_type, agent_id,"
                    " content_hash, created_at, created_at_epoch, generated_by_model, metadata, updated_at_epoch)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(memory_session_id, content_hash) DO NOTHING",
                    (memory_session_id, project, o.get("type") or "discovery", o.get("title"), o.get("subtitle"),
                     json.dumps(o.get("facts") or []), o.get("narrative"), json.dumps(o.get("concepts") or []),
                     json.dumps(o.get("files_read") or []), json.dumps(o.get("files_modified") or []),
                     prompt_number or None, discovery_tokens, o.get("agent_type"), o.get("agent_id"), h, stamp, ts,
                     model or None, o.get("metadata"), schema.now_ms()))
                if cur.rowcount:
                    ids.append(int(cur.lastrowid))
                else:
                    ids.append(int(self.db.execute(
                        "SELECT id FROM observations WHERE memory_session_id=? AND content_hash=?",
                        (memory_session_id, h)).fetchone()[0]))
            if summary:
                rolled_read, rolled_edit = rollup_file_lists(observations)
                cur = self.db.execute(
                    "INSERT INTO session_summaries(memory_session_id, project, request, investigated, learned,"
                    " completed, next_steps, files_read, files_edited, notes, prompt_number, discovery_tokens,"
                    " created_at, created_at_epoch) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (memory_session_id, project, summary.get("request") or "", summary.get("investigated") or "",
                     summary.get("learned") or "", summary.get("completed") or "", summary.get("next_steps") or "",
                     json.dumps(summary["files_read"] if summary.get("files_read") is not None else rolled_read),
                     json.dumps(summary["files_edited"] if summary.get("files_edited") is not None else rolled_edit),
                     summary.get("notes"), prompt_number or None, discovery_tokens, stamp, ts))
                summary_id = int(cur.lastrowid)
        return {"observation_ids": ids, "summary_id": summary_id, "created_at_epoch": ts}

    def store_observation(self, memory_session_id: str, project: str, observation: dict,
                          prompt_number: int | None = None, discovery_tokens: int = 0,
                          override_epoch: int | None = None, model: str | None = None) -> dict:
        if not (observation.get("title") or "").strip():
            raise ValueError("store_observation requires a non-empty title")
        res = self.store_observations(memory_session_id, project, [observation], None, prompt_number,
                                      discovery_tokens, override_epoch, model)
        return {"id": res["observation_ids"][0], "created_at_epoch": res["created_at_epoch"]}

    def store_summary(self, memory_session_id: str, project: str, summary: dict, prompt_number: int | None = None,
                      discovery_tokens: int = 0, override_epoch: int | None = None) -> dict:
        res = self.store_observations(memory_session_id, project, [], summary, prompt_number, discovery_tokens,
                                      override_epoch)
        return {"id": res["summary_id"], "created_at_epoch": res["created_at_epoch"]}

    def find_derived_observation(self, memory_session_id: str, prompt_number: int | None) -> dict | None:
        """The observation derived without a model for one prompt of a session, if any."""
        return _row(self.db.execute(
            "SELECT * FROM observations WHERE memory_session_id=? AND prompt_number IS ? AND metadata LIKE"
            " '%\"derived\": true%' ORDER BY id DESC LIMIT 1", (memory_session_id, prompt_number)))

    def update_observation(self, obs_id: int, fields: dict) -> None:
        cols = {k: (json.dumps(v) if k in ("facts", "concepts", "files_read", "files_modified") and isinstance(v, list)
                    else v) for k, v in fields.items()}
        if "title" in fields or "narrative" in fields:
            row = self.db.execute("SELECT memory_session_id, title, narrative FROM observations WHERE id=?",
                                  (obs_id,)).fetchone()
            if row:
                cols["content_hash"] = content_hash(row["memory_session_id"], fields.get("title", row["title"]),
                                                    fields.get("narrative", row["narrative"]))
        cols["updated_at_epoch"] = schema.now_ms()
        sets = ", ".join(f"{k}=?" for k in cols)
        self.db.execute(f"UPDATE observations SET {sets} WHERE id=?", (*cols.values(), obs_id))

    def get_observation_by_id(self, obs_id: int, platform_source: str | None = None) -> dict | None:
        if not platform_source:
            return _row(self.db.execute("SELECT * FROM observations WHERE id=?", (obs_id,)))
        return _row(self.db.execute(
            "SELECT o.* FROM observations o LEFT JOIN sdk_sessions s ON s.memory_session_id = o.memory_session_id"
            f" WHERE o.id=? AND {psrc('s')} = ?", (obs_id, normalize_platform_source(platform_source))))

    def get_observations_by_ids(self, ids: list[int], *, order_by: str = "date_desc", limit: int | None = None,
                                project: str | None = None, platform_source: str | None = None,
                                type: str | list[str] | None = None, concepts: str | list[str] | None = None,
                                files: str | list[str] | None = None) -> list[dict]:
        if not ids:
            return []
        preserve = order_by == "relevance"
        args: list[Any] = list(ids)
        cond = [f"o.id IN ({','.join('?' * len(ids))})"]
        if project:
            cond.append("(o.project = ? OR o.merged_into_project = ?)")
            args += [project, project]
        if platform_source:
            cond.append(f"{psrc('s')} = ?")
            args.append(normalize_platform_source(platform_source))
        if type:
            types = type if isinstance(type, list) else [type]
            cond.append(f"o.type IN ({','.join('?' * len(types))})")
            args += types
        if concepts:
            cs = concepts if isinstance(concepts, list) else [concepts]
            cond.append("(" + " OR ".join(["EXISTS (SELECT 1 FROM json_each(o.concepts) WHERE value = ?)"] * len(cs)) + ")")
            args += cs
        if files:
            fs = files if isinstance(files, list) else [files]
            cond.append("(" + " OR ".join(["(EXISTS (SELECT 1 FROM json_each(o.files_read) WHERE value LIKE ?) OR"
                                           " EXISTS (SELECT 1 FROM json_each(o.files_modified) WHERE value LIKE ?))"]
                                          * len(fs)) + ")")
            for f in fs:
                args += [f"%{f}%", f"%{f}%"]
        order = "" if preserve else f"ORDER BY o.created_at_epoch {'ASC' if order_by == 'date_asc' else 'DESC'}"
        lim = f"LIMIT {int(limit)}" if limit and not preserve else ""
        rows = _rows(self.db.execute(
            "SELECT o.* FROM observations o LEFT JOIN sdk_sessions s ON s.memory_session_id = o.memory_session_id"
            f" WHERE {' AND '.join(cond)} {order} {lim}", args))
        if not preserve:
            return rows
        by_id = {r["id"]: r for r in rows}
        ordered = [by_id[i] for i in ids if i in by_id]
        return ordered[:limit] if limit else ordered

    def get_session_summaries_by_ids(self, ids: list[int], *, order_by: str = "date_desc", limit: int | None = None,
                                     project: str | None = None, platform_source: str | None = None) -> list[dict]:
        if not ids:
            return []
        preserve = order_by == "relevance"
        args: list[Any] = list(ids)
        cond = [f"ss.id IN ({','.join('?' * len(ids))})"]
        if project:
            cond.append("(ss.project = ? OR ss.merged_into_project = ?)")
            args += [project, project]
        if platform_source:
            cond.append(f"{psrc('s')} = ?")
            args.append(normalize_platform_source(platform_source))
        order = "" if preserve else f"ORDER BY ss.created_at_epoch {'ASC' if order_by == 'date_asc' else 'DESC'}"
        lim = f"LIMIT {int(limit)}" if limit and not preserve else ""
        rows = _rows(self.db.execute(
            "SELECT ss.* FROM session_summaries ss LEFT JOIN sdk_sessions s ON s.memory_session_id = ss.memory_session_id"
            f" WHERE {' AND '.join(cond)} {order} {lim}", args))
        if not preserve:
            return rows
        by_id = {r["id"]: r for r in rows}
        ordered = [by_id[i] for i in ids if i in by_id]
        return ordered[:limit] if limit else ordered

    def get_user_prompts_by_ids(self, ids: list[int], *, order_by: str = "date_desc", limit: int | None = None,
                                project: str | None = None, platform_source: str | None = None) -> list[dict]:
        if not ids:
            return []
        preserve = order_by == "relevance"
        args: list[Any] = list(ids)
        cond = [f"up.id IN ({','.join('?' * len(ids))})"]
        if project:
            cond.append("s.project = ?")
            args.append(project)
        if platform_source:
            cond.append(f"{psrc('s')} = ?")
            args.append(normalize_platform_source(platform_source))
        order = "" if preserve else f"ORDER BY up.created_at_epoch {'ASC' if order_by == 'date_asc' else 'DESC'}"
        lim = f"LIMIT {int(limit)}" if limit and not preserve else ""
        rows = _rows(self.db.execute(
            f"SELECT up.*, s.project, s.memory_session_id, {psrc('s')} AS platform_source FROM user_prompts up"
            f" JOIN sdk_sessions s ON up.session_db_id = s.id WHERE {' AND '.join(cond)} {order} {lim}", args))
        if not preserve:
            return rows
        by_id = {r["id"]: r for r in rows}
        ordered = [by_id[i] for i in ids if i in by_id]
        return ordered[:limit] if limit else ordered

    def get_summary_for_session(self, memory_session_id: str, platform_source: str | None = None) -> dict | None:
        args: list[Any] = [memory_session_id]
        clause = ""
        if platform_source:
            clause = (" AND EXISTS (SELECT 1 FROM sdk_sessions sdk WHERE sdk.memory_session_id ="
                      f" session_summaries.memory_session_id AND {psrc('sdk')} = ?)")
            args.append(normalize_platform_source(platform_source))
        return _row(self.db.execute(
            "SELECT request, investigated, learned, completed, next_steps, files_read, files_edited, notes,"
            f" prompt_number, created_at, created_at_epoch FROM session_summaries WHERE memory_session_id = ?{clause}"
            " ORDER BY created_at_epoch DESC LIMIT 1", args))

    def get_observations_for_session(self, memory_session_id: str, platform_source: str | None = None) -> list[dict]:
        args: list[Any] = [memory_session_id]
        clause = ""
        if platform_source:
            clause = (" AND EXISTS (SELECT 1 FROM sdk_sessions s WHERE s.memory_session_id ="
                      f" observations.memory_session_id AND {psrc('s')} = ?)")
            args.append(normalize_platform_source(platform_source))
        return _rows(self.db.execute(
            f"SELECT id, title, subtitle, type, prompt_number FROM observations WHERE memory_session_id = ?{clause}"
            " ORDER BY created_at_epoch ASC", args))

    def get_observations_by_file(self, paths: list[str], *, projects: list[str] | None = None,
                                 limit: int | None = None, platform_source: str | None = None) -> list[dict]:
        """Observations whose files_read/files_modified contain any of the candidate path forms."""
        cands = list(dict.fromkeys(p for p in paths if isinstance(p, str) and p))
        if not cands:
            return []
        lim = min(limit, 100) if isinstance(limit, int) and limit > 0 else 15
        marks = ",".join("?" * len(cands))
        args: list[Any] = [*cands, *cands]
        clause = ""
        if projects:
            clause += f" AND o.project IN ({','.join('?' * len(projects))})"
            args += projects
        if platform_source:
            clause += f" AND {psrc('s')} = ?"
            args.append(normalize_platform_source(platform_source))
        args.append(lim)
        return _rows(self.db.execute(
            "SELECT o.* FROM observations o LEFT JOIN sdk_sessions s ON s.memory_session_id = o.memory_session_id"
            f" WHERE ((o.files_read LIKE '[%' AND EXISTS (SELECT 1 FROM json_each(o.files_read) WHERE value IN ({marks})))"
            f" OR (o.files_modified LIKE '[%' AND EXISTS (SELECT 1 FROM json_each(o.files_modified) WHERE value IN"
            f" ({marks})))){clause} ORDER BY o.created_at_epoch DESC LIMIT ?", args))

    # ---- timeline ----------------------------------------------------------------------------------
    def get_timeline_around_timestamp(self, anchor_epoch: int, before: int = 10, after: int = 10,
                                      project: str | None = None, platform_source: str | None = None) -> dict:
        return self.get_timeline_around_observation(None, anchor_epoch, before, after, project, platform_source)

    def get_timeline_around_observation(self, anchor_id: int | None, anchor_epoch: int, before: int = 10,
                                        after: int = 10, project: str | None = None,
                                        platform_source: str | None = None) -> dict:
        src = normalize_platform_source(platform_source) if platform_source else None

        def scope(row: str, sess: str, merged: bool) -> tuple[str, list]:
            cond, args = [], []
            if project:
                if merged:
                    cond.append(f"({row}.project = ? OR {row}.merged_into_project = ?)")
                    args += [project, project]
                else:
                    cond.append(f"{row}.project = ?")
                    args.append(project)
            if src:
                cond.append(f"{psrc(sess)} = ?")
                args.append(src)
            return (" AND " + " AND ".join(cond)) if cond else "", args

        obs_c, obs_a = scope("o", "src", True)
        sum_c, sum_a = scope("ss", "src", True)
        pr_c, pr_a = scope("s", "s", False)
        empty = {"observations": [], "sessions": [], "prompts": []}
        join = "FROM observations o LEFT JOIN sdk_sessions src ON src.memory_session_id = o.memory_session_id"
        try:
            if anchor_id is not None:
                b = self.db.execute(f"SELECT o.id, o.created_at_epoch {join} WHERE o.id <= ?{obs_c} ORDER BY o.id DESC"
                                    " LIMIT ?", (anchor_id, *obs_a, before + 1)).fetchall()
                a = self.db.execute(f"SELECT o.id, o.created_at_epoch {join} WHERE o.id >= ?{obs_c} ORDER BY o.id ASC"
                                    " LIMIT ?", (anchor_id, *obs_a, after + 1)).fetchall()
            else:
                b = self.db.execute(f"SELECT o.created_at_epoch {join} WHERE o.created_at_epoch <= ?{obs_c}"
                                    " ORDER BY o.created_at_epoch DESC LIMIT ?", (anchor_epoch, *obs_a, before)).fetchall()
                a = self.db.execute(f"SELECT o.created_at_epoch {join} WHERE o.created_at_epoch >= ?{obs_c}"
                                    " ORDER BY o.created_at_epoch ASC LIMIT ?", (anchor_epoch, *obs_a, after + 1)).fetchall()
        except sqlite3.Error:
            return empty
        if not b and not a:
            return empty
        # the window spans the boundary records' times (ids and times can disagree after an import)
        epochs = [anchor_epoch, *(r["created_at_epoch"] for r in (*b, *a))]
        start, end = min(epochs), max(epochs)
        observations = _rows(self.db.execute(
            f"SELECT o.* {join} WHERE o.created_at_epoch >= ? AND o.created_at_epoch <= ?{obs_c}"
            " ORDER BY o.created_at_epoch ASC", (start, end, *obs_a)))
        sessions = _rows(self.db.execute(
            "SELECT ss.id, ss.memory_session_id, ss.project, ss.request, ss.completed, ss.next_steps, ss.created_at,"
            " ss.created_at_epoch FROM session_summaries ss LEFT JOIN sdk_sessions src ON src.memory_session_id ="
            f" ss.memory_session_id WHERE ss.created_at_epoch >= ? AND ss.created_at_epoch <= ?{sum_c}"
            " ORDER BY ss.created_at_epoch ASC", (start, end, *sum_a)))
        prompts = _rows(self.db.execute(
            "SELECT up.id, up.content_session_id, up.prompt_number, up.prompt_text, s.project,"
            f" {psrc('s')} AS platform_source, up.created_at, up.created_at_epoch FROM user_prompts up"
            f" JOIN sdk_sessions s ON up.session_db_id = s.id WHERE up.created_at_epoch >= ? AND"
            f" up.created_at_epoch <= ?{pr_c} ORDER BY up.created_at_epoch ASC", (start, end, *pr_a)))
        return {"observations": observations, "sessions": sessions, "prompts": prompts}

    # ---- manual memories ---------------------------------------------------------------------------
    def get_or_create_manual_session(self, project: str, platform_source: str = DEF) -> str:
        src = normalize_platform_source(platform_source)
        memory_id = f"manual-{project}-{src}"
        if self.db.execute("SELECT 1 FROM sdk_sessions WHERE memory_session_id=?", (memory_id,)).fetchone():
            return memory_id
        now = schema.now_ms()
        self.db.execute("INSERT INTO sdk_sessions(memory_session_id, content_session_id, project, platform_source,"
                        " started_at, started_at_epoch, status) VALUES(?,?,?,?,?,?, 'active')",
                        (memory_id, f"manual-content-{project}-{src}", project, src, schema.iso(now), now))
        return memory_id

    # ---- deletion ----------------------------------------------------------------------------------
    def delete_row(self, kind: str, row_id: int) -> bool:
        table = {"observation": "observations", "summary": "session_summaries", "prompt": "user_prompts"}[kind]
        ok = self.db.execute(f"DELETE FROM {table} WHERE id=?", (row_id,)).rowcount > 0
        if ok:
            self.set_kv("readmodel.resync", "1")  # mirrors of deleted rows must go too
        return ok

    # ---- import ------------------------------------------------------------------------------------
    def import_sdk_session(self, s: dict) -> dict:
        src = normalize_platform_source(s.get("platform_source"))
        ex = self.db.execute("SELECT id FROM sdk_sessions WHERE platform_source=? AND content_session_id=?",
                             (src, s["content_session_id"])).fetchone()
        if ex:
            return {"imported": False, "id": int(ex[0])}
        cur = self.db.execute(
            "INSERT INTO sdk_sessions(content_session_id, memory_session_id, project, platform_source, user_prompt,"
            " started_at, started_at_epoch, completed_at, completed_at_epoch, status) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (s["content_session_id"], s.get("memory_session_id"), s.get("project") or "", src, s.get("user_prompt") or "",
             s.get("started_at") or schema.iso(s.get("started_at_epoch")), s.get("started_at_epoch") or schema.now_ms(),
             s.get("completed_at"), s.get("completed_at_epoch"), s.get("status") or "completed"))
        return {"imported": True, "id": int(cur.lastrowid)}

    def import_session_summary(self, s: dict) -> dict:
        ex = self.db.execute("SELECT id FROM session_summaries WHERE memory_session_id=?",
                             (s["memory_session_id"],)).fetchone()
        if ex:
            return {"imported": False, "id": int(ex[0])}
        cur = self.db.execute(
            "INSERT INTO session_summaries(memory_session_id, project, request, investigated, learned, completed,"
            " next_steps, files_read, files_edited, notes, prompt_number, discovery_tokens, created_at, created_at_epoch)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s["memory_session_id"], s.get("project") or "", s.get("request"), s.get("investigated"), s.get("learned"),
             s.get("completed"), s.get("next_steps"), s.get("files_read"), s.get("files_edited"), s.get("notes"),
             s.get("prompt_number"), s.get("discovery_tokens") or 0, s.get("created_at") or schema.iso(),
             s.get("created_at_epoch") or schema.now_ms()))
        return {"imported": True, "id": int(cur.lastrowid)}

    def import_observation(self, o: dict) -> dict:
        ex = self.db.execute("SELECT id FROM observations WHERE memory_session_id=? AND title IS ? AND created_at_epoch=?",
                             (o["memory_session_id"], o.get("title"), o.get("created_at_epoch"))).fetchone()
        if ex:
            return {"imported": False, "id": int(ex[0])}
        cur = self.db.execute(
            "INSERT INTO observations(memory_session_id, project, text, type, title, subtitle, facts, narrative,"
            " concepts, files_read, files_modified, prompt_number, discovery_tokens, agent_type, agent_id, content_hash,"
            " created_at, created_at_epoch, updated_at_epoch) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (o["memory_session_id"], o.get("project") or "", o.get("text"), o.get("type") or "discovery", o.get("title"),
             o.get("subtitle"), o.get("facts"), o.get("narrative"), o.get("concepts"), o.get("files_read"),
             o.get("files_modified"), o.get("prompt_number"), o.get("discovery_tokens") or 0, o.get("agent_type"),
             o.get("agent_id"), content_hash(o["memory_session_id"], o.get("title"), o.get("narrative")),
             o.get("created_at") or schema.iso(o.get("created_at_epoch")), o.get("created_at_epoch") or schema.now_ms(),
             schema.now_ms()))
        return {"imported": True, "id": int(cur.lastrowid)}

    def import_user_prompt(self, p: dict) -> dict:
        src = normalize_platform_source(p["platform_source"]) if p.get("platform_source") else None
        sid = None
        if isinstance(p.get("session_db_id"), int):
            row = self.db.execute(f"SELECT id, content_session_id, {psrc('sdk_sessions')} AS ps FROM sdk_sessions"
                                  " WHERE id=?", (p["session_db_id"],)).fetchone()
            if row and row["content_session_id"] == p["content_session_id"] and (not src or row["ps"] == src):
                sid = int(row["id"])
        if sid is None:
            sid = self._resolve_session(p["content_session_id"], None, src)
        where, arg = ("session_db_id = ?", sid) if sid is not None else ("content_session_id = ?", p["content_session_id"])
        ex = self.db.execute(f"SELECT id FROM user_prompts WHERE {where} AND prompt_number = ?",
                             (arg, p["prompt_number"])).fetchone()
        if ex:
            return {"imported": False, "id": int(ex[0])}
        cur = self.db.execute(
            "INSERT INTO user_prompts(session_db_id, content_session_id, prompt_number, prompt_text, created_at,"
            " created_at_epoch) VALUES(?,?,?,?,?,?)",
            (sid, p["content_session_id"], p["prompt_number"], p["prompt_text"], p.get("created_at") or schema.iso(),
             p.get("created_at_epoch") or schema.now_ms()))
        return {"imported": True, "id": int(cur.lastrowid)}

    def rebuild_fts(self) -> None:
        if schema.fts_available(self.db):
            self.db.execute("INSERT INTO observations_fts(observations_fts) VALUES('rebuild')")
            self.db.execute("INSERT INTO session_summaries_fts(session_summaries_fts) VALUES('rebuild')")
            self.db.execute("INSERT INTO user_prompts_fts(user_prompts_fts) VALUES('rebuild')")

    # ---- tool uses (raw tool I/O side index) -------------------------------------------------------
    def upsert_tool_use(self, *, tool_use_id: str, content_session_id: str, tool_name: str, project: str = "",
                        session_db_id: int | None = None, memory_session_id: str | None = None,
                        platform_source: str | None = None, tool_input: str | None = None,
                        tool_response: str | None = None, cwd: str | None = None, prompt_number: int | None = None,
                        agent_type: str | None = None, agent_id: str | None = None,
                        created_at_epoch: int | None = None) -> int | None:
        if not tool_use_id or not content_session_id or not tool_name or is_recursive_memory_tool(tool_name):
            return None
        ts = created_at_epoch or schema.now_ms()
        h = tool_use_hash(tool_name, tool_input, tool_response)
        ti = truncate_payload(tool_input) if tool_input is not None else None
        tr = truncate_payload(tool_response) if tool_response is not None else None
        self.db.execute(
            "INSERT INTO tool_uses(tool_use_id, content_session_id, memory_session_id, session_db_id, project,"
            " platform_source, tool_name, tool_input, tool_response, cwd, prompt_number, agent_type, agent_id,"
            " content_hash, created_at, created_at_epoch) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(content_session_id, tool_use_id) DO UPDATE SET"
            " memory_session_id = COALESCE(excluded.memory_session_id, tool_uses.memory_session_id),"
            " session_db_id = COALESCE(excluded.session_db_id, tool_uses.session_db_id),"
            " project = CASE WHEN excluded.project != '' THEN excluded.project ELSE tool_uses.project END,"
            " platform_source = excluded.platform_source,"
            " tool_input = COALESCE(excluded.tool_input, tool_uses.tool_input),"
            " tool_response = COALESCE(excluded.tool_response, tool_uses.tool_response),"
            " cwd = COALESCE(excluded.cwd, tool_uses.cwd),"
            " prompt_number = COALESCE(excluded.prompt_number, tool_uses.prompt_number),"
            " agent_type = COALESCE(excluded.agent_type, tool_uses.agent_type),"
            " agent_id = COALESCE(excluded.agent_id, tool_uses.agent_id),"
            " content_hash = excluded.content_hash",
            (tool_use_id, content_session_id, memory_session_id, session_db_id, project or "",
             normalize_platform_source(platform_source), tool_name, ti, tr, cwd, prompt_number, agent_type, agent_id,
             h, schema.iso(ts), ts))
        r = self.db.execute("SELECT id FROM tool_uses WHERE content_session_id=? AND tool_use_id=?",
                            (content_session_id, tool_use_id)).fetchone()
        return int(r[0]) if r else None

    def link_tool_uses_to_observation(self, content_session_id: str, tool_use_ids: list[str], observation_id: int,
                                      memory_session_id: str | None = None) -> int:
        ids = [i for i in tool_use_ids if isinstance(i, str) and i]
        if not ids:
            return 0
        return self.db.execute(
            "UPDATE tool_uses SET observation_id = COALESCE(observation_id, ?), memory_session_id = COALESCE(?,"
            f" memory_session_id) WHERE content_session_id = ? AND tool_use_id IN ({','.join('?' * len(ids))})",
            (observation_id, memory_session_id, content_session_id, *ids)).rowcount

    def get_tool_uses_by_ids(self, ids: list, *, limit: int | None = None, project: str | None = None,
                             platform_source: str | None = None, content_session_id: str | None = None) -> list[dict]:
        nums, strs = [], []
        for i in ids:
            if isinstance(i, int) and not isinstance(i, bool):
                nums.append(i)
            elif isinstance(i, str) and i.strip():
                s = i.strip()
                if s.isdigit():
                    nums.append(int(s))
                strs.append(s)
        if not nums and not strs:
            return []
        clauses, args = [], []
        if nums:
            clauses.append(f"id IN ({','.join('?' * len(nums))})")
            args += nums
        if strs:
            clauses.append(f"tool_use_id IN ({','.join('?' * len(strs))})")
            args += strs
        cond = [f"({' OR '.join(clauses)})"]
        if project:
            cond.append("project = ?")
            args.append(project)
        if content_session_id:
            cond.append("content_session_id = ?")
            args.append(content_session_id)
        if platform_source:
            cond.append(f"COALESCE(NULLIF(platform_source, ''), '{DEF}') = ?")
            args.append(normalize_platform_source(platform_source))
        lim = f"LIMIT {int(limit)}" if limit and limit > 0 else ""
        return _rows(self.db.execute(f"SELECT * FROM tool_uses WHERE {' AND '.join(cond)} ORDER BY created_at_epoch DESC"
                                     f" {lim}", args))

    def _tool_filters(self, f: dict) -> tuple[str, list]:
        cond, args = [], []
        for key, col in (("project", "project"), ("content_session_id", "content_session_id"),
                         ("memory_session_id", "memory_session_id"), ("agent_id", "agent_id")):
            if f.get(key):
                cond.append(f"{col} = ?")
                args.append(f[key])
        if isinstance(f.get("session_db_id"), int):
            cond.append("session_db_id = ?")
            args.append(f["session_db_id"])
        if f.get("tool_name"):
            names = f["tool_name"] if isinstance(f["tool_name"], list) else [f["tool_name"]]
            cond.append(f"tool_name IN ({','.join('?' * len(names))})")
            args += names
        if f.get("platform_source"):
            cond.append(f"COALESCE(NULLIF(platform_source, ''), '{DEF}') = ?")
            args.append(normalize_platform_source(f["platform_source"]))
        if isinstance(f.get("date_start"), (int, float)):
            cond.append("created_at_epoch >= ?")
            args.append(f["date_start"])
        if isinstance(f.get("date_end"), (int, float)):
            cond.append("created_at_epoch <= ?")
            args.append(f["date_end"])
        return (f"WHERE {' AND '.join(cond)}" if cond else ""), args

    def query_tool_uses(self, **filters) -> list[dict]:
        where, args = self._tool_filters(filters)
        order = "ASC" if filters.get("order_by") == "date_asc" else "DESC"
        limit = min(max(int(filters.get("limit") or 50), 1), 500)
        offset = max(int(filters.get("offset") or 0), 0)
        return _rows(self.db.execute(f"SELECT * FROM tool_uses {where} ORDER BY created_at_epoch {order}, id {order}"
                                     f" LIMIT {limit} OFFSET {offset}", args))

    def count_tool_uses(self, **filters) -> list[dict]:
        where, args = self._tool_filters(filters)
        return _rows(self.db.execute(f"SELECT tool_name, COUNT(DISTINCT tool_use_id) AS uses FROM tool_uses {where}"
                                     " GROUP BY tool_name ORDER BY uses DESC, tool_name ASC", args))

    # ---- the work queue ----------------------------------------------------------------------------
    def enqueue(self, session_db_id: int, content_session_id: str, message_type: str, **fields) -> int:
        """Append one message; returns its id, or 0 when a tool call with the same id is already queued."""
        cols = ["session_db_id", "content_session_id", "message_type", "created_at_epoch"]
        vals: list[Any] = [session_db_id, content_session_id, message_type, schema.now_ms()]
        for k in ("tool_name", "tool_input", "tool_response", "cwd", "last_user_message", "last_assistant_message",
                  "prompt_number", "agent_type", "agent_id", "tool_use_id"):
            if fields.get(k) is not None:
                cols.append(k)
                vals.append(fields[k])
        cur = self.db.execute(f"INSERT OR IGNORE INTO pending_messages({', '.join(cols)}) VALUES"
                              f" ({', '.join('?' * len(cols))})", vals)
        return int(cur.lastrowid) if cur.rowcount else 0

    def sessions_with_pending(self) -> list[int]:
        return [int(r[0]) for r in self.db.execute(
            "SELECT DISTINCT session_db_id FROM pending_messages WHERE status='pending' ORDER BY session_db_id")]

    def pending_count(self, session_db_id: int | None = None) -> int:
        if session_db_id is None:
            return int(self.db.execute("SELECT COUNT(*) FROM pending_messages WHERE status IN ('pending','processing')")
                       .fetchone()[0])
        return int(self.db.execute("SELECT COUNT(*) FROM pending_messages WHERE session_db_id=? AND status IN"
                                   " ('pending','processing')", (session_db_id,)).fetchone()[0])

    def peek_pending(self, session_db_id: int) -> list[dict]:
        return _rows(self.db.execute("SELECT id, message_type, tool_name, prompt_number, retry_count FROM"
                                     " pending_messages WHERE session_db_id=? AND status='pending' ORDER BY id",
                                     (session_db_id,)))

    def claim(self, session_db_id: int, limit: int = 1) -> list[dict]:
        """Atomically move the next ``limit`` pending messages of a session to 'processing'."""
        with self.tx():
            rows = _rows(self.db.execute("SELECT * FROM pending_messages WHERE session_db_id=? AND status='pending'"
                                         " ORDER BY id LIMIT ?", (session_db_id, limit)))
            if rows:
                now = schema.now_ms()
                self.db.executemany("UPDATE pending_messages SET status='processing', claimed_at_epoch=? WHERE id=?",
                                    [(now, r["id"]) for r in rows])
        return rows

    def claim_ids(self, ids: Iterable[int]) -> list[dict]:
        """Claim specific pending messages (e.g. the ones that exhausted their retries)."""
        ids = list(ids)
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        with self.tx():
            rows = _rows(self.db.execute(f"SELECT * FROM pending_messages WHERE id IN ({marks}) AND status='pending'"
                                         " ORDER BY id", ids))
            if rows:
                self.db.executemany("UPDATE pending_messages SET status='processing', claimed_at_epoch=? WHERE id=?",
                                    [(schema.now_ms(), r["id"]) for r in rows])
        return rows

    def confirm(self, ids: Iterable[int]) -> int:
        ids = list(ids)
        if not ids:
            return 0
        return self.db.execute(f"DELETE FROM pending_messages WHERE id IN ({','.join('?' * len(ids))})", ids).rowcount

    def reset_to_pending(self, ids: Iterable[int] | None = None, *, session_db_id: int | None = None,
                         count_retry: bool = False) -> int:
        bump = ", retry_count = retry_count + 1" if count_retry else ""
        if ids is not None:
            ids = list(ids)
            if not ids:
                return 0
            return self.db.execute(f"UPDATE pending_messages SET status='pending', claimed_at_epoch=NULL{bump}"
                                   f" WHERE id IN ({','.join('?' * len(ids))})", ids).rowcount
        if session_db_id is not None:
            return self.db.execute(f"UPDATE pending_messages SET status='pending', claimed_at_epoch=NULL{bump}"
                                   " WHERE session_db_id=? AND status='processing'", (session_db_id,)).rowcount
        return self.db.execute(f"UPDATE pending_messages SET status='pending', claimed_at_epoch=NULL{bump}"
                               " WHERE status='processing'").rowcount

    def reset_stale_processing(self, older_than_ms: int = STALE_PROCESSING_MS) -> int:
        return self.db.execute("UPDATE pending_messages SET status='pending', claimed_at_epoch=NULL WHERE"
                               " status='processing' AND (claimed_at_epoch IS NULL OR claimed_at_epoch < ?)",
                               (schema.now_ms() - older_than_ms,)).rowcount

    def fail(self, ids: Iterable[int]) -> int:
        ids = list(ids)
        if not ids:
            return 0
        return self.db.execute(f"UPDATE pending_messages SET status='failed' WHERE id IN ({','.join('?' * len(ids))})",
                               ids).rowcount

    def clear_pending(self, session_db_id: int | None = None, *, failed_only: bool = False) -> int:
        cond, args = [], []
        if session_db_id is not None:
            cond.append("session_db_id=?")
            args.append(session_db_id)
        if failed_only:
            cond.append("status='failed'")
        where = f"WHERE {' AND '.join(cond)}" if cond else ""
        return self.db.execute(f"DELETE FROM pending_messages {where}", args).rowcount

    def queue_status(self) -> dict:
        rows = {r[0]: r[1] for r in self.db.execute("SELECT status, COUNT(*) FROM pending_messages GROUP BY status")}
        return {"pending": rows.get("pending", 0), "processing": rows.get("processing", 0),
                "failed": rows.get("failed", 0)}

    # ---- observer conversation ---------------------------------------------------------------------
    def get_conversation(self, session_db_id: int) -> dict:
        r = self.db.execute("SELECT * FROM observer_conversations WHERE session_db_id=?", (session_db_id,)).fetchone()
        if not r:
            return {"history": {}, "chars": 0, "consecutive_overflows": 0, "paused_until_epoch": None}
        try:
            history = json.loads(r["history"])
        except ValueError:
            history = []
        return {"history": history, "chars": r["chars"], "consecutive_overflows": r["consecutive_overflows"],
                "paused_until_epoch": r["paused_until_epoch"]}

    def save_conversation(self, session_db_id: int, history: Any, *, consecutive_overflows: int = 0,
                          paused_until_epoch: int | None = None) -> None:
        text = json.dumps(history)
        chars = len(text)
        self.db.execute(
            "INSERT INTO observer_conversations(session_db_id, history, chars, consecutive_overflows, paused_until_epoch,"
            " updated_at_epoch) VALUES(?,?,?,?,?,?) ON CONFLICT(session_db_id) DO UPDATE SET history=excluded.history,"
            " chars=excluded.chars, consecutive_overflows=excluded.consecutive_overflows,"
            " paused_until_epoch=excluded.paused_until_epoch, updated_at_epoch=excluded.updated_at_epoch",
            (session_db_id, text, chars, consecutive_overflows, paused_until_epoch, schema.now_ms()))

    # ---- file-context injection gate ---------------------------------------------------------------
    def claim_file_context_injection(self, session_id: str, resolved_path: str, newest_epoch: int,
                                     ttl_days: int = 7) -> bool:
        """True when this (session, file) timeline should be injected: never surfaced, or a newer
        observation landed since. Claiming records it, so two concurrent Reads cannot both inject."""
        if not session_id:
            return True
        now = schema.now_ms()
        try:
            if not self.db.execute("SELECT 1 FROM file_context_injections WHERE session_id=? LIMIT 1",
                                   (session_id,)).fetchone():
                self.db.execute("DELETE FROM file_context_injections WHERE injected_at_epoch < ?",
                                (now - ttl_days * 86_400_000,))
            cur = self.db.execute(
                "INSERT INTO file_context_injections(session_id, file_path, observation_epoch, injected_at_epoch)"
                " VALUES(?,?,?,?) ON CONFLICT(session_id, file_path) DO UPDATE SET"
                " observation_epoch=excluded.observation_epoch, injected_at_epoch=excluded.injected_at_epoch"
                " WHERE excluded.observation_epoch > file_context_injections.observation_epoch",
                (session_id, resolved_path, newest_epoch, now))
            return cur.rowcount > 0
        except sqlite3.Error:
            return True

    # ---- stats -------------------------------------------------------------------------------------
    def stats(self) -> dict:
        one = lambda sql: int(self.db.execute(sql).fetchone()[0])
        first = self.db.execute("SELECT created_at FROM observations ORDER BY created_at_epoch ASC LIMIT 1").fetchone()
        return {"observations": one("SELECT COUNT(*) FROM observations"),
                "sessions": one("SELECT COUNT(*) FROM sdk_sessions"),
                "summaries": one("SELECT COUNT(*) FROM session_summaries"),
                "prompts": one("SELECT COUNT(*) FROM user_prompts"),
                "tool_uses": one("SELECT COUNT(*) FROM tool_uses"),
                "firstObservationAt": first[0] if first else None,
                "size": self.path.stat().st_size if self.path and self.path.exists() else 0,
                "path": str(self.path) if self.path else None}


class _Tx:
    """``BEGIN IMMEDIATE`` ... ``COMMIT`` (nested use joins the outer transaction)."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.own = False

    def __enter__(self):
        if not self.db.in_transaction:
            self.db.execute("BEGIN IMMEDIATE")
            self.own = True
        return self.db

    def __exit__(self, exc_type, *exc):
        if not self.own:
            return False
        self.db.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


# Cairn's own recall tools must never be recorded as raw tool payloads: disclosing a body would
# store it again, and the table would grow with every read instead of every tool call.
RECURSIVE_MEMORY_TOOLS = frozenset({"search", "timeline", "get_observations", "get_tool_uses",
                                    "session_start_context", "observation_search", "recall_search",
                                    "recall_timeline", "recall_get_observations", "recall_get_tool_uses",
                                    "cairn_recall"})


def is_recursive_memory_tool(tool_name: str) -> bool:
    if not tool_name:
        return False
    if tool_name.startswith("memory_"):
        return True
    if not tool_name.startswith("mcp__"):
        return False
    parts = tool_name.split("__")
    if len(parts) < 3:
        return False
    return "cairn" in parts[1].lower() and "__".join(parts[2:]) in RECURSIVE_MEMORY_TOOLS

"""Keyword and filter search over the store (FTS5, with substring matching for scripts FTS5 cannot
segment, and LIKE for prompts). Standard library only, so hooks can use it too."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import Any

from . import schema
from .platforms import DEFAULT_PLATFORM_SOURCE, normalize_platform_source
from .projects import is_direct_child
from .store import parse_list

# Hiragana, Katakana, CJK ideographs, Bopomofo and Hangul are not split into words by unicode61, so
# a sub-word query never matches the index; those queries use substring matching instead.
UNSEGMENTED_SCRIPT = re.compile(r"[぀-ヿ㄀-㆏㐀-䶿一-鿿가-힯豈-﫿]")
MISSING_INPUT = "Either query or filters required for search"


class SearchInputError(ValueError):
    pass


def _epoch(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value if value > 1e11 else value * 1000)
    s = str(value).strip()
    if s.isdigit():
        n = int(s)
        return n if n > 1e11 else n * 1000
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(d.timestamp() * 1000)
    except ValueError:
        return None


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def filter_clause(f: dict, args: list, alias: str = "o") -> str:
    cond = []
    if f.get("project"):
        cond.append(f"{alias}.project = ?")
        args.append(f["project"])
    if f.get("platform_source"):
        cond.append(f"COALESCE(NULLIF((SELECT s2.platform_source FROM sdk_sessions s2 WHERE s2.memory_session_id ="
                    f" {alias}.memory_session_id), ''), '{DEFAULT_PLATFORM_SOURCE}') = ?")
        args.append(normalize_platform_source(f["platform_source"]))
    types = _as_list(f.get("type"))
    if types:
        cond.append(f"{alias}.type IN ({','.join('?' * len(types))})")
        args += types
    dr = f.get("date_range") or {}
    if _epoch(dr.get("start")) is not None:
        cond.append(f"{alias}.created_at_epoch >= ?")
        args.append(_epoch(dr.get("start")))
    if _epoch(dr.get("end")) is not None:
        cond.append(f"{alias}.created_at_epoch <= ?")
        args.append(_epoch(dr.get("end")))
    concepts = _as_list(f.get("concepts"))
    if concepts:
        cond.append("(" + " OR ".join([f"EXISTS (SELECT 1 FROM json_each({alias}.concepts) WHERE value = ?)"]
                                      * len(concepts)) + ")")
        args += concepts
    files = _as_list(f.get("files"))
    if files:
        cond.append("(" + " OR ".join([f"(EXISTS (SELECT 1 FROM json_each({alias}.files_read) WHERE value LIKE ?)"
                                       f" OR EXISTS (SELECT 1 FROM json_each({alias}.files_modified) WHERE value LIKE ?))"]
                                      * len(files)) + ")")
        for fl in files:
            args += [f"%{fl}%", f"%{fl}%"]
    return " AND ".join(cond)


def _substring(query: str, columns: list[str]) -> tuple[str, list]:
    pattern = "%" + re.sub(r"([\\%_])", r"\\\1", query) + "%"
    return "(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in columns) + ")", [pattern] * len(columns)


def _order(order_by: str | None, fts: bool, table: str = "observations_fts", alias: str = "o") -> str:
    if order_by in (None, "relevance"):
        return f"ORDER BY {table}.rank ASC" if fts else f"ORDER BY {alias}.created_at_epoch DESC"
    if order_by == "date_asc":
        return f"ORDER BY {alias}.created_at_epoch ASC"
    return f"ORDER BY {alias}.created_at_epoch DESC"


def _fts_phrase(query: str) -> str:
    return '"' + query.replace('"', '""') + '"'


def _fts_terms(query: str) -> str:
    """Every word as a prefix term, OR-joined: forgiving matching for multi-word queries."""
    words = [w for w in re.findall(r"[\w]+", query, re.UNICODE) if len(w) > 1]
    return " OR ".join(f'"{w}"*' for w in dict.fromkeys(w.lower() for w in words))


def search_observations(db: sqlite3.Connection, query: str | None, *, limit: int = 50, offset: int = 0,
                        order_by: str = "relevance", loose: bool = False, **filters) -> list[dict]:
    args: list[Any] = []
    if not query:
        clause = filter_clause(filters, args, "o")
        if not clause:
            raise SearchInputError(MISSING_INPUT)
        rows = db.execute(f"SELECT o.* FROM observations o WHERE {clause} {_order(order_by, False)} LIMIT ? OFFSET ?",
                          (*args, limit, offset)).fetchall()
        return [dict(r) for r in rows]
    if UNSEGMENTED_SCRIPT.search(query) or not schema.fts_available(db):
        clause = filter_clause(filters, args, "o")
        m, margs = _substring(query, ["o.title", "o.subtitle", "o.narrative", "o.text", "o.facts", "o.concepts"])
        rows = db.execute(f"SELECT o.* FROM observations o WHERE {m} {'AND ' + clause if clause else ''}"
                          f" {_order(order_by, False)} LIMIT ? OFFSET ?", (*margs, *args, limit, offset)).fetchall()
        return [dict(r) for r in rows]
    clause = filter_clause(filters, args, "o")
    match = _fts_terms(query) if loose else _fts_phrase(query)
    if not match:
        return []
    rows = db.execute(
        "SELECT o.* FROM observations o JOIN observations_fts ON observations_fts.rowid = o.id"
        f" WHERE observations_fts MATCH ? {'AND ' + clause if clause else ''} {_order(order_by, True)}"
        " LIMIT ? OFFSET ?", (match, *args, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def search_sessions(db: sqlite3.Connection, query: str | None, *, limit: int = 50, offset: int = 0,
                    order_by: str = "relevance", loose: bool = False, **filters) -> list[dict]:
    filters = {k: v for k, v in filters.items() if k not in ("type", "concepts", "files")}
    args: list[Any] = []
    clause = filter_clause(filters, args, "s")
    date_order = "ORDER BY s.created_at_epoch ASC" if order_by == "date_asc" else "ORDER BY s.created_at_epoch DESC"
    if not query:
        if not clause:
            raise SearchInputError(MISSING_INPUT)
        rows = db.execute(f"SELECT s.* FROM session_summaries s WHERE {clause} {date_order} LIMIT ? OFFSET ?",
                          (*args, limit, offset)).fetchall()
        return [dict(r) for r in rows]
    if UNSEGMENTED_SCRIPT.search(query) or not schema.fts_available(db):
        m, margs = _substring(query, ["s.request", "s.investigated", "s.learned", "s.completed", "s.next_steps",
                                      "s.notes"])
        rows = db.execute(f"SELECT s.* FROM session_summaries s WHERE {m} {'AND ' + clause if clause else ''}"
                          f" {date_order} LIMIT ? OFFSET ?", (*margs, *args, limit, offset)).fetchall()
        return [dict(r) for r in rows]
    order = date_order if order_by in ("date_asc", "date_desc") else "ORDER BY session_summaries_fts.rank ASC"
    match = _fts_terms(query) if loose else _fts_phrase(query)
    if not match:
        return []
    rows = db.execute(
        "SELECT s.* FROM session_summaries s JOIN session_summaries_fts ON session_summaries_fts.rowid = s.id"
        f" WHERE session_summaries_fts MATCH ? {'AND ' + clause if clause else ''} {order} LIMIT ? OFFSET ?",
        (match, *args, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def search_user_prompts(db: sqlite3.Connection, query: str | None, *, limit: int = 20, offset: int = 0,
                        order_by: str = "relevance", **filters) -> list[dict]:
    cond, args = [], []
    if filters.get("project"):
        cond.append("s.project = ?")
        args.append(filters["project"])
    if filters.get("platform_source"):
        cond.append(f"COALESCE(NULLIF(s.platform_source, ''), '{DEFAULT_PLATFORM_SOURCE}') = ?")
        args.append(normalize_platform_source(filters["platform_source"]))
    dr = filters.get("date_range") or {}
    if _epoch(dr.get("start")) is not None:
        cond.append("up.created_at_epoch >= ?")
        args.append(_epoch(dr.get("start")))
    if _epoch(dr.get("end")) is not None:
        cond.append("up.created_at_epoch <= ?")
        args.append(_epoch(dr.get("end")))
    if not query and not cond:
        raise SearchInputError(MISSING_INPUT)
    if query:
        cond.append("up.prompt_text LIKE ? ESCAPE '\\'")
        args.append("%" + re.sub(r"([\\%_])", r"\\\1", query) + "%")
    order = "ORDER BY up.created_at_epoch ASC" if order_by == "date_asc" else "ORDER BY up.created_at_epoch DESC"
    rows = db.execute(
        f"SELECT up.*, s.project, s.memory_session_id, COALESCE(NULLIF(s.platform_source, ''),"
        f" '{DEFAULT_PLATFORM_SOURCE}') AS platform_source FROM user_prompts up JOIN sdk_sessions s"
        f" ON up.session_db_id = s.id WHERE {' AND '.join(cond)} {order} LIMIT ? OFFSET ?",
        (*args, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def find_by_concept(db: sqlite3.Connection, concept: str, *, limit: int = 50, offset: int = 0,
                    order_by: str = "date_desc", **filters) -> list[dict]:
    args: list[Any] = []
    clause = filter_clause({**filters, "concepts": concept}, args, "o")
    return [dict(r) for r in db.execute(f"SELECT o.* FROM observations o WHERE {clause} {_order(order_by, False)}"
                                        " LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()]


def find_by_type(db: sqlite3.Connection, type_: str | list[str], *, limit: int = 50, offset: int = 0,
                 order_by: str = "date_desc", **filters) -> list[dict]:
    args: list[Any] = []
    clause = filter_clause({**filters, "type": type_}, args, "o")
    return [dict(r) for r in db.execute(f"SELECT o.* FROM observations o WHERE {clause} {_order(order_by, False)}"
                                        " LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()]


def find_by_file(db: sqlite3.Connection, file_path: str, *, limit: int = 50, offset: int = 0,
                 order_by: str = "date_desc", is_folder: bool = False, **filters) -> dict:
    qlimit = limit * 3 if is_folder else limit
    args: list[Any] = []
    clause = filter_clause({**{k: v for k, v in filters.items() if k != "files"}, "files": file_path}, args, "o")
    observations = [dict(r) for r in db.execute(
        f"SELECT o.* FROM observations o WHERE {clause} {_order(order_by, False)} LIMIT ? OFFSET ?",
        (*args, qlimit, offset)).fetchall()]
    if is_folder:
        observations = [o for o in observations
                        if any(is_direct_child(f, file_path) for f in parse_list(o.get("files_modified"))
                               + parse_list(o.get("files_read")))][:limit]
    sargs: list[Any] = []
    base = []
    if filters.get("project"):
        base.append("s.project = ?")
        sargs.append(filters["project"])
    if filters.get("platform_source"):
        base.append(f"COALESCE(NULLIF((SELECT s2.platform_source FROM sdk_sessions s2 WHERE s2.memory_session_id ="
                    f" s.memory_session_id), ''), '{DEFAULT_PLATFORM_SOURCE}') = ?")
        sargs.append(normalize_platform_source(filters["platform_source"]))
    dr = filters.get("date_range") or {}
    if _epoch(dr.get("start")) is not None:
        base.append("s.created_at_epoch >= ?")
        sargs.append(_epoch(dr.get("start")))
    if _epoch(dr.get("end")) is not None:
        base.append("s.created_at_epoch <= ?")
        sargs.append(_epoch(dr.get("end")))
    base.append("(EXISTS (SELECT 1 FROM json_each(s.files_read) WHERE value LIKE ?) OR EXISTS (SELECT 1 FROM"
                " json_each(s.files_edited) WHERE value LIKE ?))")
    sargs += [f"%{file_path}%", f"%{file_path}%"]
    sessions = [dict(r) for r in db.execute(
        f"SELECT s.* FROM session_summaries s WHERE {' AND '.join(base)} ORDER BY s.created_at_epoch DESC"
        " LIMIT ? OFFSET ?", (*sargs, qlimit, offset)).fetchall()]
    if is_folder:
        sessions = [s for s in sessions if any(is_direct_child(f, file_path) for f in parse_list(s.get("files_read"))
                                               + parse_list(s.get("files_edited")))][:limit]
    return {"observations": observations, "sessions": sessions}

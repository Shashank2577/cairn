"""The Brain: one SQLite read model that every surface (CLI, MCP, HTTP/UI) reads from.

Engines write into it; nothing reads engine stores directly at query time. This keeps answers
fast (<10 ms lookups), uniform across layers and available offline.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS entities(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, path TEXT,
  meta TEXT NOT NULL DEFAULT '{}', source TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_entities_kind ON entities(kind);
CREATE INDEX IF NOT EXISTS ix_entities_path ON entities(path);
CREATE TABLE IF NOT EXISTS links(
  src TEXT NOT NULL, dst TEXT NOT NULL, rel TEXT NOT NULL,
  provenance TEXT NOT NULL DEFAULT 'EXTRACTED', confidence REAL NOT NULL DEFAULT 1.0,
  source TEXT NOT NULL DEFAULT '', PRIMARY KEY(src, dst, rel));
CREATE INDEX IF NOT EXISTS ix_links_dst ON links(dst, rel);
CREATE INDEX IF NOT EXISTS ix_links_source ON links(source);
CREATE TABLE IF NOT EXISTS events(
  id TEXT PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT '', refs TEXT NOT NULL DEFAULT '[]',
  meta TEXT NOT NULL DEFAULT '{}', source TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts DESC);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind, ts DESC);
CREATE TABLE IF NOT EXISTS memories(
  id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fact',
  scope TEXT NOT NULL DEFAULT 'project', source TEXT NOT NULL DEFAULT 'user',
  provenance TEXT NOT NULL DEFAULT 'EXTRACTED', confidence REAL NOT NULL DEFAULT 1.0,
  created_at REAL NOT NULL, superseded_by TEXT, forgotten INTEGER NOT NULL DEFAULT 0,
  engine_ref TEXT);
CREATE TABLE IF NOT EXISTS cochange(
  a TEXT NOT NULL, b TEXT NOT NULL, count INTEGER NOT NULL, last_ts REAL NOT NULL,
  PRIMARY KEY(a, b));
CREATE INDEX IF NOT EXISTS ix_cochange_b ON cochange(b);
CREATE TABLE IF NOT EXISTS filestats(
  path TEXT PRIMARY KEY, commits INTEGER NOT NULL, risky INTEGER NOT NULL DEFAULT 0,
  last_ts REAL NOT NULL, authors TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, task TEXT NOT NULL, tier TEXT NOT NULL,
  model TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
  cache_read INTEGER NOT NULL DEFAULT 0, cache_write INTEGER NOT NULL DEFAULT 0, ok INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS queries(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, surface TEXT NOT NULL, kind TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '', sent_tokens INTEGER NOT NULL, source_tokens INTEGER,
  source_files INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_queries_ts ON queries(ts DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  id UNINDEXED, kind UNINDEXED, label UNINDEXED, title, body, tokenize='porter unicode61');
"""

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD = re.compile(r"[A-Za-z0-9_]{2,}")


def searchable(text: str) -> str:
    """Expand identifiers so 'processPayment' and 'process_payment' match 'process payment'."""
    extra = " ".join(_CAMEL.sub(" ", w).replace("_", " ") for w in _WORD.findall(text or ""))
    return f"{text} {extra}"


def fts_query(q: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", searchable(q)) if len(w) > 1]
    seen: list[str] = []
    for w in words:
        lw = w.lower()
        if lw not in seen:
            seen.append(lw)
    return " OR ".join(f'"{w}"*' for w in seen[:12])


class Brain:
    """Thread-safe facade over the read model."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.executescript(SCHEMA)
            # write only on change: readers (agent hooks) must not queue behind a sync's write transaction
            if self.get_kv("schema_version") != str(SCHEMA_VERSION):
                self.set_kv("schema_version", str(SCHEMA_VERSION))

    # ---- plumbing -------------------------------------------------------------------------------
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self.db.execute("BEGIN")
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def q(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self.db.close()

    # ---- kv ------------------------------------------------------------------------------------
    def get_kv(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else default

    def set_kv(self, key: str, value: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, value))
            self.db.commit()

    # ---- entities & links ----------------------------------------------------------------------
    def put_entities(self, rows: Iterable[tuple[str, str, str, str | None, dict, str, str]]) -> int:
        """Upsert (id, kind, name, path, meta, source, searchable_body) rows in one transaction."""
        now = time.time()
        rows = list(rows)
        with self.tx() as db:
            db.executemany(
                "INSERT INTO entities(id,kind,name,path,meta,source,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,name=excluded.name,path=excluded.path,"
                "meta=excluded.meta,source=excluded.source,updated_at=excluded.updated_at",
                [(i, k, n, p, json.dumps(m, separators=(",", ":")), s, now) for i, k, n, p, m, s, _ in rows])
            db.executemany("DELETE FROM fts WHERE id=?", [(r[0],) for r in rows])
            db.executemany("INSERT INTO fts(id,kind,label,title,body) VALUES(?,?,?,?,?)",
                           [(i, k, n, searchable(n), searchable(b or "")) for i, k, n, _, _, _, b in rows])
        return len(rows)

    def put_entity(self, id: str, kind: str, name: str, path: str | None = None, meta: dict | None = None,
                   source: str = "", body: str = "") -> None:
        self.put_entities([(id, kind, name, path, meta or {}, source, body)])

    def drop_source(self, source: str, kinds: Iterable[str] | None = None) -> None:
        """Remove what a sync source produced last time, so re-ingest is exact (no stale rows)."""
        with self.tx() as db:
            db.execute("DELETE FROM links WHERE source=?", (source,))
            if kinds:
                kinds = list(kinds)
                marks = ",".join("?" * len(kinds))
                ids = [r[0] for r in db.execute(
                    f"SELECT id FROM entities WHERE source=? AND kind IN ({marks})", (source, *kinds))]
                db.executemany("DELETE FROM fts WHERE id=?", [(i,) for i in ids])
                db.execute(f"DELETE FROM entities WHERE source=? AND kind IN ({marks})", (source, *kinds))

    def link(self, rows: Iterable[tuple[str, str, str, str, float, str]]) -> int:
        """Upsert (src, dst, rel, provenance, confidence, source) links."""
        rows = list(rows)
        with self.tx() as db:
            db.executemany(
                "INSERT INTO links(src,dst,rel,provenance,confidence,source) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(src,dst,rel) DO UPDATE SET provenance=excluded.provenance,"
                "confidence=excluded.confidence,source=excluded.source", rows)
        return len(rows)

    def entity(self, id: str) -> dict | None:
        row = self.one("SELECT * FROM entities WHERE id=?", (id,))
        return _ent(row) if row else None

    def entities(self, kind: str, limit: int = 10_000) -> list[dict]:
        return [_ent(r) for r in self.q("SELECT * FROM entities WHERE kind=? LIMIT ?", (kind, limit))]

    def by_path(self, path: str, kinds: tuple[str, ...] = ("file",)) -> list[dict]:
        marks = ",".join("?" * len(kinds))
        return [_ent(r) for r in self.q(f"SELECT * FROM entities WHERE path=? AND kind IN ({marks})", (path, *kinds))]

    def links_to(self, dst: str | list[str], rels: tuple[str, ...] | None = None, limit: int = 500) -> list[dict]:
        dsts = [dst] if isinstance(dst, str) else list(dst)
        if not dsts:
            return []
        sql = f"SELECT * FROM links WHERE dst IN ({','.join('?' * len(dsts))})"
        args: list[Any] = list(dsts)
        if rels:
            sql += f" AND rel IN ({','.join('?' * len(rels))})"
            args += rels
        return [dict(r) for r in self.q(sql + " LIMIT ?", (*args, limit))]

    def links_from(self, src: str | list[str], rels: tuple[str, ...] | None = None, limit: int = 500) -> list[dict]:
        srcs = [src] if isinstance(src, str) else list(src)
        if not srcs:
            return []
        sql = f"SELECT * FROM links WHERE src IN ({','.join('?' * len(srcs))})"
        args: list[Any] = list(srcs)
        if rels:
            sql += f" AND rel IN ({','.join('?' * len(rels))})"
            args += rels
        return [dict(r) for r in self.q(sql + " LIMIT ?", (*args, limit))]

    def counts(self) -> dict[str, int]:
        out = {r["kind"]: r["n"] for r in self.q("SELECT kind, COUNT(*) n FROM entities GROUP BY kind")}
        out["memory_active"] = self.one("SELECT COUNT(*) n FROM memories WHERE forgotten=0 AND superseded_by IS NULL")["n"]
        out["events"] = self.one("SELECT COUNT(*) n FROM events")["n"]
        return out

    # ---- search --------------------------------------------------------------------------------
    def search(self, query: str, kinds: Iterable[str] | None = None, limit: int = 20) -> list[dict]:
        fq = fts_query(query)
        if not fq:
            return []
        sql = "SELECT id, kind, label, bm25(fts, 0, 0, 0, 4.0, 1.0) AS score FROM fts WHERE fts MATCH ?"
        args: list[Any] = [fq]
        kinds = list(kinds or [])
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        sql += " ORDER BY score LIMIT ?"
        try:
            rows = self.q(sql, (*args, limit))
        except sqlite3.OperationalError:
            return []
        return [{"id": r["id"], "kind": r["kind"], "title": r["label"], "score": round(-r["score"], 3)} for r in rows]

    # ---- events --------------------------------------------------------------------------------
    def add_events(self, rows: Iterable[dict]) -> int:
        rows = list(rows)
        with self.tx() as db:
            db.executemany(
                "INSERT OR REPLACE INTO events(id,ts,kind,title,body,actor,refs,meta,source) VALUES(?,?,?,?,?,?,?,?,?)",
                [(e["id"], e["ts"], e["kind"], e["title"], e.get("body", ""), e.get("actor", ""),
                  json.dumps(e.get("refs", [])), json.dumps(e.get("meta", {})), e.get("source", "")) for e in rows])
        return len(rows)

    def events(self, kinds: Iterable[str] | None = None, since: float | None = None, ref: str | None = None,
               limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM events WHERE 1=1", []
        kinds = list(kinds or [])
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        if since:
            sql += " AND ts>=?"
            args.append(since)
        if ref:
            sql += " AND refs LIKE ?"
            args.append(f'%"{ref}"%')
        rows = self.q(sql + " ORDER BY ts DESC LIMIT ?", (*args, limit))
        return [{**dict(r), "refs": json.loads(r["refs"]), "meta": json.loads(r["meta"])} for r in rows]

    # ---- memories ------------------------------------------------------------------------------
    def add_memory(self, text: str, kind: str = "fact", scope: str = "project", source: str = "user",
                   provenance: str = "EXTRACTED", confidence: float = 1.0, supersedes: str | None = None,
                   engine_ref: str | None = None, mem_id: str | None = None) -> str:
        mid = mem_id or uuid.uuid4().hex[:10]
        now = time.time()
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO memories(id,text,kind,scope,source,provenance,confidence,created_at,"
                       "engine_ref) VALUES(?,?,?,?,?,?,?,?,?)",
                       (mid, text, kind, scope, source, provenance, confidence, now, engine_ref))
            if supersedes:
                db.execute("UPDATE memories SET superseded_by=? WHERE id=?", (mid, supersedes))
            db.execute("DELETE FROM fts WHERE id=?", (f"memory:{mid}",))
            db.execute("INSERT INTO fts(id,kind,label,title,body) VALUES(?,?,?,?,?)",
                       (f"memory:{mid}", "memory", text[:140], searchable(text[:80]), searchable(text)))
            db.execute("INSERT OR REPLACE INTO events(id,ts,kind,title,body,actor,refs,meta,source) VALUES(?,?,?,?,?,?,?,?,?)",
                       (f"memory:{mid}", now, "memory", text[:120], text, source, json.dumps([f"memory:{mid}"]),
                        json.dumps({"kind": kind}), "memory"))
        return mid

    def memories(self, include_inactive: bool = False, limit: int = 500) -> list[dict]:
        sql = "SELECT * FROM memories"
        if not include_inactive:
            sql += " WHERE forgotten=0 AND superseded_by IS NULL"
        return [dict(r) for r in self.q(sql + " ORDER BY created_at DESC LIMIT ?", (limit,))]

    def memory(self, mid: str) -> dict | None:
        row = self.one("SELECT * FROM memories WHERE id=?", (mid,))
        return dict(row) if row else None

    def forget(self, mid: str) -> bool:
        with self.tx() as db:
            n = db.execute("UPDATE memories SET forgotten=1 WHERE id=?", (mid,)).rowcount
            db.execute("DELETE FROM fts WHERE id=?", (f"memory:{mid}",))
        return n > 0

    # ---- history stats -------------------------------------------------------------------------
    def cochanged(self, path: str, limit: int = 8) -> list[dict]:
        rows = self.q(
            "SELECT b AS other, count FROM cochange WHERE a=? UNION ALL SELECT a AS other, count FROM cochange WHERE b=? "
            "ORDER BY count DESC LIMIT ?", (path, path, limit * 3))
        me = self.one("SELECT commits FROM filestats WHERE path=?", (path,))
        mine = me["commits"] if me else 0
        out = []
        for r in rows:
            other = self.one("SELECT commits FROM filestats WHERE path=?", (r["other"],))
            denom = max(1, min(mine or 1, other["commits"] if other else 1))
            out.append({"path": r["other"], "count": r["count"], "ratio": round(r["count"] / denom, 2)})
        out.sort(key=lambda x: (x["ratio"] * min(x["count"], 10)), reverse=True)
        return out[:limit]

    def filestat(self, path: str) -> dict | None:
        row = self.one("SELECT * FROM filestats WHERE path=?", (path,))
        return {**dict(row), "authors": json.loads(row["authors"])} if row else None

    # ---- ledger --------------------------------------------------------------------------------
    def log_call(self, task: str, tier: str, model: str, usage: dict, ok: bool) -> None:
        with self._lock:
            self.db.execute("INSERT INTO ledger(ts,task,tier,model,input_tokens,output_tokens,cache_read,cache_write,ok)"
                            " VALUES(?,?,?,?,?,?,?,?,?)",
                            (time.time(), task, tier, model, usage.get("input", 0), usage.get("output", 0),
                             usage.get("cache_read", 0), usage.get("cache_write", 0), int(ok)))
            self.db.commit()

    def ledger(self, since: float = 0) -> list[dict]:
        return [dict(r) for r in self.q(
            "SELECT tier, model, COUNT(*) calls, SUM(input_tokens) input, SUM(output_tokens) output, "
            "SUM(cache_read) cache_read, SUM(cache_write) cache_write, "
            "SUM(input_tokens + cache_read + cache_write) input_total FROM ledger WHERE ts>=? GROUP BY tier, model "
            "ORDER BY calls DESC", (since,))]

    # ---- context served ------------------------------------------------------------------------
    def log_query(self, surface: str, kind: str, target: str, sent_tokens: int, source_tokens: int | None,
                  source_files: int = 0) -> None:
        """Record one context pack handed to a reader. ``source_tokens`` is the size of the files the
        evidence came from (None when there is no file baseline, e.g. the session briefing)."""
        with self._lock:
            self.db.execute("INSERT INTO queries(ts,surface,kind,target,sent_tokens,source_tokens,source_files)"
                            " VALUES(?,?,?,?,?,?,?)",
                            (time.time(), surface, kind, target[:200], sent_tokens, source_tokens, source_files))
            self.db.commit()

    def queries(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM queries ORDER BY ts DESC LIMIT ?", (limit,))]

    def query_totals(self) -> list[dict]:
        return [dict(r) for r in self.q(
            "SELECT surface, kind, COUNT(*) n, SUM(sent_tokens) sent, SUM(source_tokens) source, "
            "SUM(CASE WHEN source_tokens IS NULL THEN 0 ELSE sent_tokens END) sent_with_source "
            "FROM queries GROUP BY surface, kind ORDER BY n DESC")]


def _ent(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["meta"] = json.loads(d.get("meta") or "{}")
    return d

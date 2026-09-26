"""The recall store: ``<repo>/.cairn/sessions.db`` (standard library only).

Tables
  sdk_sessions        one row per agent session (content_session_id = the agent's id,
                      memory_session_id = the id observations hang off once the worker starts)
  user_prompts        every prompt, numbered per session (FTS5: user_prompts_fts)
  pending_messages    the durable work queue written by hooks and drained by the worker
  observations        typed, titled records written by the observer (FTS5: observations_fts)
  session_summaries   request / investigated / learned / completed / next_steps per prompt
                      (FTS5: session_summaries_fts)
  tool_uses           raw tool input/output side index for last-resort disclosure
  file_context_injections   per-session gate for file timelines injected before a Read
  observer_conversations    the observer's running conversation per session (bounded)
  vector_docs         metadata for the local vector index (one row per embedded field)
  remote_outbox       hook events waiting to be pushed to a team server (client side)
  remote_received     ids of events already received from other machines (server side)
  kv                  small state: observer health, quota cooldown, cursors

Migrations are numbered and recorded in ``schema_versions``; ``connect()`` applies any missing
ones, so every process (hook, worker, server) can open the store in any order.
"""
from __future__ import annotations

import contextvars
import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BUSY_TIMEOUT_MS = 5000
SCHEMA_VERSION = 6


def store_path(root: Path | str) -> Path:
    return Path(root) / ".cairn" / "sessions.db"


# The time an event happened, when it is recorded later (events pushed from another machine).
_CLOCK: contextvars.ContextVar[int | None] = contextvars.ContextVar("recall_clock", default=None)


def now_ms() -> int:
    fixed = _CLOCK.get()
    return fixed if fixed is not None else int(time.time() * 1000)


@contextmanager
def clock(epoch_ms: int | None) -> Iterator[None]:
    """Stamp everything written in this block with ``epoch_ms`` (no-op for None)."""
    token = _CLOCK.set(int(epoch_ms) if epoch_ms is not None else None)
    try:
        yield
    finally:
        _CLOCK.reset(token)


def iso(epoch_ms: float | None = None) -> str:
    ms = now_ms() if epoch_ms is None else epoch_ms
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


V1 = """
CREATE TABLE IF NOT EXISTS sdk_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content_session_id TEXT NOT NULL,
  memory_session_id TEXT UNIQUE,
  project TEXT NOT NULL,
  platform_source TEXT NOT NULL DEFAULT 'claude',
  user_prompt TEXT,
  custom_title TEXT,
  started_at TEXT NOT NULL,
  started_at_epoch INTEGER NOT NULL,
  completed_at TEXT,
  completed_at_epoch INTEGER,
  status TEXT CHECK(status IN ('active', 'completed', 'failed')) NOT NULL DEFAULT 'active',
  prompt_counter INTEGER DEFAULT 0,
  observed_model TEXT,
  cwd TEXT,
  branch TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sdk_sessions_platform_content ON sdk_sessions(platform_source, content_session_id);
CREATE INDEX IF NOT EXISTS idx_sdk_sessions_claude_id ON sdk_sessions(content_session_id);
CREATE INDEX IF NOT EXISTS idx_sdk_sessions_sdk_id ON sdk_sessions(memory_session_id);
CREATE INDEX IF NOT EXISTS idx_sdk_sessions_project ON sdk_sessions(project);
CREATE INDEX IF NOT EXISTS idx_sdk_sessions_status ON sdk_sessions(status);
CREATE INDEX IF NOT EXISTS idx_sdk_sessions_started ON sdk_sessions(started_at_epoch DESC);

CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_session_id TEXT NOT NULL,
  project TEXT NOT NULL,
  text TEXT,
  type TEXT NOT NULL,
  title TEXT,
  subtitle TEXT,
  facts TEXT,
  narrative TEXT,
  concepts TEXT,
  files_read TEXT,
  files_modified TEXT,
  prompt_number INTEGER,
  discovery_tokens INTEGER DEFAULT 0,
  content_hash TEXT,
  generated_by_model TEXT,
  relevance_count INTEGER DEFAULT 0,
  merged_into_project TEXT,
  agent_type TEXT,
  agent_id TEXT,
  metadata TEXT,
  created_at TEXT NOT NULL,
  created_at_epoch INTEGER NOT NULL,
  updated_at_epoch INTEGER,
  FOREIGN KEY(memory_session_id) REFERENCES sdk_sessions(memory_session_id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_observations_updated ON observations(updated_at_epoch);
CREATE UNIQUE INDEX IF NOT EXISTS ux_observations_session_hash ON observations(memory_session_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_observations_sdk_session ON observations(memory_session_id);
CREATE INDEX IF NOT EXISTS idx_observations_project ON observations(project);
CREATE INDEX IF NOT EXISTS idx_observations_type ON observations(type);
CREATE INDEX IF NOT EXISTS idx_observations_created ON observations(created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_observations_merged_into ON observations(merged_into_project);
CREATE INDEX IF NOT EXISTS idx_observations_agent_id ON observations(agent_id);

CREATE TABLE IF NOT EXISTS session_summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_session_id TEXT NOT NULL,
  project TEXT NOT NULL,
  request TEXT,
  investigated TEXT,
  learned TEXT,
  completed TEXT,
  next_steps TEXT,
  files_read TEXT,
  files_edited TEXT,
  notes TEXT,
  prompt_number INTEGER,
  discovery_tokens INTEGER DEFAULT 0,
  merged_into_project TEXT,
  created_at TEXT NOT NULL,
  created_at_epoch INTEGER NOT NULL,
  FOREIGN KEY(memory_session_id) REFERENCES sdk_sessions(memory_session_id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_session_summaries_sdk_session ON session_summaries(memory_session_id);
CREATE INDEX IF NOT EXISTS idx_session_summaries_project ON session_summaries(project);
CREATE INDEX IF NOT EXISTS idx_session_summaries_created ON session_summaries(created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_summaries_merged_into ON session_summaries(merged_into_project);

CREATE TABLE IF NOT EXISTS user_prompts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_db_id INTEGER,
  content_session_id TEXT NOT NULL,
  prompt_number INTEGER NOT NULL,
  prompt_text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_at_epoch INTEGER NOT NULL,
  FOREIGN KEY(session_db_id) REFERENCES sdk_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_user_prompts_session ON user_prompts(session_db_id);
CREATE INDEX IF NOT EXISTS idx_user_prompts_claude_session ON user_prompts(content_session_id);
CREATE INDEX IF NOT EXISTS idx_user_prompts_lookup ON user_prompts(session_db_id, prompt_number);
CREATE INDEX IF NOT EXISTS idx_user_prompts_created ON user_prompts(created_at_epoch DESC);

CREATE TABLE IF NOT EXISTS pending_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_db_id INTEGER NOT NULL,
  content_session_id TEXT NOT NULL,
  message_type TEXT NOT NULL CHECK(message_type IN ('observation', 'summarize')),
  tool_name TEXT,
  tool_input TEXT,
  tool_response TEXT,
  cwd TEXT,
  last_user_message TEXT,
  last_assistant_message TEXT,
  prompt_number INTEGER,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'processing', 'failed')),
  retry_count INTEGER NOT NULL DEFAULT 0,
  claimed_at_epoch INTEGER,
  created_at_epoch INTEGER NOT NULL,
  agent_type TEXT,
  agent_id TEXT,
  tool_use_id TEXT,
  FOREIGN KEY (session_db_id) REFERENCES sdk_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pending_messages_session ON pending_messages(session_db_id);
CREATE INDEX IF NOT EXISTS idx_pending_messages_status ON pending_messages(status);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pending_session_tool ON pending_messages(session_db_id, tool_use_id)
  WHERE tool_use_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS tool_uses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tool_use_id TEXT NOT NULL,
  content_session_id TEXT NOT NULL,
  memory_session_id TEXT,
  session_db_id INTEGER,
  project TEXT NOT NULL,
  platform_source TEXT NOT NULL DEFAULT 'claude',
  tool_name TEXT NOT NULL,
  tool_input TEXT,
  tool_response TEXT,
  cwd TEXT,
  prompt_number INTEGER,
  agent_type TEXT,
  agent_id TEXT,
  observation_id INTEGER,
  content_hash TEXT,
  created_at TEXT NOT NULL,
  created_at_epoch INTEGER NOT NULL,
  UNIQUE(content_session_id, tool_use_id)
);
CREATE INDEX IF NOT EXISTS idx_tool_uses_project ON tool_uses(project);
CREATE INDEX IF NOT EXISTS idx_tool_uses_memory_session ON tool_uses(memory_session_id);
CREATE INDEX IF NOT EXISTS idx_tool_uses_content_session ON tool_uses(content_session_id);
CREATE INDEX IF NOT EXISTS idx_tool_uses_session_db_id ON tool_uses(session_db_id);
CREATE INDEX IF NOT EXISTS idx_tool_uses_tool_name ON tool_uses(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_uses_created_at_epoch ON tool_uses(created_at_epoch);
CREATE INDEX IF NOT EXISTS idx_tool_uses_observation_id ON tool_uses(observation_id);

CREATE TABLE IF NOT EXISTS file_context_injections (
  session_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  observation_epoch INTEGER NOT NULL,
  injected_at_epoch INTEGER NOT NULL,
  PRIMARY KEY (session_id, file_path)
);

CREATE TABLE IF NOT EXISTS observer_conversations (
  session_db_id INTEGER PRIMARY KEY,
  history TEXT NOT NULL DEFAULT '[]',
  chars INTEGER NOT NULL DEFAULT 0,
  consecutive_overflows INTEGER NOT NULL DEFAULT 0,
  paused_until_epoch INTEGER,
  updated_at_epoch INTEGER NOT NULL,
  FOREIGN KEY (session_db_id) REFERENCES sdk_sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS vector_docs (
  doc_id TEXT PRIMARY KEY,
  doc_type TEXT NOT NULL,
  sqlite_id INTEGER NOT NULL,
  field_type TEXT,
  project TEXT,
  merged_into_project TEXT,
  platform_source TEXT,
  created_at_epoch INTEGER,
  embedder TEXT
);
CREATE INDEX IF NOT EXISTS idx_vector_docs_entity ON vector_docs(doc_type, sqlite_id);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

V2_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
  title, subtitle, narrative, text, facts, concepts, content='observations', content_rowid='id');
INSERT INTO observations_fts(rowid, title, subtitle, narrative, text, facts, concepts)
  SELECT id, title, subtitle, narrative, text, facts, concepts FROM observations;
CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations BEGIN
  INSERT INTO observations_fts(rowid, title, subtitle, narrative, text, facts, concepts)
  VALUES (new.id, new.title, new.subtitle, new.narrative, new.text, new.facts, new.concepts);
END;
CREATE TRIGGER IF NOT EXISTS observations_ad AFTER DELETE ON observations BEGIN
  INSERT INTO observations_fts(observations_fts, rowid, title, subtitle, narrative, text, facts, concepts)
  VALUES('delete', old.id, old.title, old.subtitle, old.narrative, old.text, old.facts, old.concepts);
END;
CREATE TRIGGER IF NOT EXISTS observations_au AFTER UPDATE ON observations BEGIN
  INSERT INTO observations_fts(observations_fts, rowid, title, subtitle, narrative, text, facts, concepts)
  VALUES('delete', old.id, old.title, old.subtitle, old.narrative, old.text, old.facts, old.concepts);
  INSERT INTO observations_fts(rowid, title, subtitle, narrative, text, facts, concepts)
  VALUES (new.id, new.title, new.subtitle, new.narrative, new.text, new.facts, new.concepts);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS session_summaries_fts USING fts5(
  request, investigated, learned, completed, next_steps, notes,
  content='session_summaries', content_rowid='id');
INSERT INTO session_summaries_fts(rowid, request, investigated, learned, completed, next_steps, notes)
  SELECT id, request, investigated, learned, completed, next_steps, notes FROM session_summaries;
CREATE TRIGGER IF NOT EXISTS session_summaries_ai AFTER INSERT ON session_summaries BEGIN
  INSERT INTO session_summaries_fts(rowid, request, investigated, learned, completed, next_steps, notes)
  VALUES (new.id, new.request, new.investigated, new.learned, new.completed, new.next_steps, new.notes);
END;
CREATE TRIGGER IF NOT EXISTS session_summaries_ad AFTER DELETE ON session_summaries BEGIN
  INSERT INTO session_summaries_fts(session_summaries_fts, rowid, request, investigated, learned, completed, next_steps, notes)
  VALUES('delete', old.id, old.request, old.investigated, old.learned, old.completed, old.next_steps, old.notes);
END;
CREATE TRIGGER IF NOT EXISTS session_summaries_au AFTER UPDATE ON session_summaries BEGIN
  INSERT INTO session_summaries_fts(session_summaries_fts, rowid, request, investigated, learned, completed, next_steps, notes)
  VALUES('delete', old.id, old.request, old.investigated, old.learned, old.completed, old.next_steps, old.notes);
  INSERT INTO session_summaries_fts(rowid, request, investigated, learned, completed, next_steps, notes)
  VALUES (new.id, new.request, new.investigated, new.learned, new.completed, new.next_steps, new.notes);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS user_prompts_fts USING fts5(prompt_text, content='user_prompts', content_rowid='id');
INSERT INTO user_prompts_fts(rowid, prompt_text) SELECT id, prompt_text FROM user_prompts;
CREATE TRIGGER IF NOT EXISTS user_prompts_ai AFTER INSERT ON user_prompts BEGIN
  INSERT INTO user_prompts_fts(rowid, prompt_text) VALUES (new.id, new.prompt_text);
END;
CREATE TRIGGER IF NOT EXISTS user_prompts_ad AFTER DELETE ON user_prompts BEGIN
  INSERT INTO user_prompts_fts(user_prompts_fts, rowid, prompt_text) VALUES('delete', old.id, old.prompt_text);
END;
CREATE TRIGGER IF NOT EXISTS user_prompts_au AFTER UPDATE ON user_prompts BEGIN
  INSERT INTO user_prompts_fts(user_prompts_fts, rowid, prompt_text) VALUES('delete', old.id, old.prompt_text);
  INSERT INTO user_prompts_fts(rowid, prompt_text) VALUES (new.id, new.prompt_text);
END;
"""


def _fts5_available(db: sqlite3.Connection) -> bool:
    try:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp._fts5_probe USING fts5(x)")
        db.execute("DROP TABLE IF EXISTS temp._fts5_probe")
        return True
    except sqlite3.Error:
        return False


def fts_available(db: sqlite3.Connection) -> bool:
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations_fts'").fetchone())


def run_script(db: sqlite3.Connection, script: str) -> None:
    """Execute a multi-statement script inside the caller's transaction (``executescript`` would
    commit it)."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                db.execute(buf)
            buf = ""
    if buf.strip():
        db.execute(buf)


def _v1(db: sqlite3.Connection) -> None:
    run_script(db, V1)


def _v2(db: sqlite3.Connection) -> None:
    if _fts5_available(db):
        run_script(db, V2_FTS)


def _v3(db: sqlite3.Connection) -> None:
    """Import the event log written by Cairn's earlier, minimal capture hook (``events`` table):
    sessions, numbered prompts and one derived observation per prompt, so no history is lost."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
        return
    cols = {r[1] for r in db.execute("PRAGMA table_info(events)")}
    if not {"ts", "session", "kind", "detail", "path"} <= cols:
        return
    from .fallback import legacy_turn_observation  # stdlib-only helper
    rows = db.execute("SELECT ts, session, kind, tool, detail, path FROM events ORDER BY id").fetchall()
    by_session: dict[str, list] = {}
    for r in rows:
        by_session.setdefault(str(r[1]), []).append(r)
    for sid, evs in by_session.items():
        start = int(float(evs[0][0]) * 1000)
        memory_id = f"legacy-{sid}"
        db.execute(
            "INSERT OR IGNORE INTO sdk_sessions(content_session_id, memory_session_id, project, platform_source,"
            " user_prompt, started_at, started_at_epoch, status, completed_at, completed_at_epoch)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, memory_id, "", "claude", "", iso(start), start, "completed", iso(int(float(evs[-1][0]) * 1000)),
             int(float(evs[-1][0]) * 1000)))
        sess = db.execute("SELECT id FROM sdk_sessions WHERE platform_source='claude' AND content_session_id=?",
                          (sid,)).fetchone()
        if not sess:
            continue
        turns: list[dict] = []
        for ts, _s, kind, tool, detail, path in evs:
            if kind == "prompt" or not turns:
                turns.append({"prompt": detail if kind == "prompt" else "", "ts": int(float(ts) * 1000),
                              "read": [], "modified": [], "commands": [], "searches": []})
            t = turns[-1]
            if kind == "read" and path and path not in t["read"]:
                t["read"].append(path)
            elif kind == "edit" and path and path not in t["modified"]:
                t["modified"].append(path)
            elif kind == "command" and detail and detail not in t["commands"]:
                t["commands"].append(detail)
            elif kind == "search" and detail:
                t["searches"].append(detail)
        n = 0
        for t in turns:
            if t["prompt"]:
                n += 1
                db.execute("INSERT INTO user_prompts(session_db_id, content_session_id, prompt_number, prompt_text,"
                           " created_at, created_at_epoch) VALUES(?,?,?,?,?,?)",
                           (sess[0], sid, n, t["prompt"], iso(t["ts"]), t["ts"]))
            obs = legacy_turn_observation(t)
            if obs is None:
                continue
            db.execute(
                "INSERT OR IGNORE INTO observations(memory_session_id, project, type, title, subtitle, facts, narrative,"
                " concepts, files_read, files_modified, prompt_number, content_hash, metadata, created_at,"
                " created_at_epoch, updated_at_epoch) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (memory_id, "", obs["type"], obs["title"], obs["subtitle"], json.dumps(obs["facts"]),
                 obs["narrative"], json.dumps(obs["concepts"]), json.dumps(obs["files_read"]),
                 json.dumps(obs["files_modified"]), n or None, f"legacy-{n}-{t['ts']}",
                 json.dumps({"derived": True, "legacy": True}), iso(t["ts"]), t["ts"], now_ms()))
        db.execute("UPDATE sdk_sessions SET prompt_counter=?, user_prompt=? WHERE id=?",
                   (n, next((t["prompt"] for t in turns if t["prompt"]), ""), sess[0]))
    db.execute("ALTER TABLE events RENAME TO legacy_capture_events")
    db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES('legacy.unnamed', '1')")


def name_legacy_rows(db: sqlite3.Connection, project: str) -> None:
    """Rows imported from the legacy log carry an empty project; name them after the repository."""
    if not project or not db.execute("SELECT 1 FROM kv WHERE key='legacy.unnamed'").fetchone():
        return
    for table in ("sdk_sessions", "observations", "session_summaries"):
        db.execute(f"UPDATE {table} SET project=? WHERE project=''", (project,))
    db.execute("DELETE FROM kv WHERE key='legacy.unnamed'")


def _add_column(db: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _v4(db: sqlite3.Connection) -> None:
    """Observation update times (incremental mirroring) and the git branch of each session."""
    _add_column(db, "observations", "updated_at_epoch", "INTEGER")
    db.execute("CREATE INDEX IF NOT EXISTS idx_observations_updated ON observations(updated_at_epoch)")
    db.execute("UPDATE observations SET updated_at_epoch = created_at_epoch WHERE updated_at_epoch IS NULL")
    _add_column(db, "sdk_sessions", "branch", "TEXT")


V5 = """
CREATE TABLE IF NOT EXISTS remote_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event TEXT NOT NULL,
  platform TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at_epoch INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS remote_received (
  event_id TEXT PRIMARY KEY,
  received_at_epoch INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_remote_received_at ON remote_received(received_at_epoch);
"""


def _v5(db: sqlite3.Connection) -> None:
    """Team server sync: the client's outbox and the server's record of received event ids."""
    run_script(db, V5)


def _v6(db: sqlite3.Connection) -> None:
    """Who pushed a session to this (team server) store: the caller's id, NULL for local capture."""
    _add_column(db, "sdk_sessions", "pushed_by", "TEXT")


MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [(1, _v1), (2, _v2), (3, _v3), (4, _v4),
                                                                      (5, _v5), (6, _v6)]


def migrate(db: sqlite3.Connection) -> list[int]:
    db.execute("CREATE TABLE IF NOT EXISTS schema_versions (id INTEGER PRIMARY KEY, version INTEGER UNIQUE NOT NULL,"
               " applied_at TEXT NOT NULL)")
    done = {r[0] for r in db.execute("SELECT version FROM schema_versions")}
    applied = []
    for version, fn in MIGRATIONS:
        if version in done:
            continue
        db.execute("BEGIN IMMEDIATE")
        try:
            fn(db)
            db.execute("INSERT OR IGNORE INTO schema_versions(version, applied_at) VALUES(?,?)", (version, iso()))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied


def connect(path: Path | str, *, readonly: bool = False, project_name: str | None = None) -> sqlite3.Connection:
    """Open the store with the connection pragmas and every migration applied.

    ``readonly`` opens an existing file without writing (used by the context hook); it raises
    ``FileNotFoundError`` when there is no store yet.
    """
    path = Path(path)
    if readonly:
        if not path.exists():
            raise FileNotFoundError(str(path))
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_MS / 1000,
                             isolation_level=None, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        return db
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    db = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA synchronous = NORMAL")
    db.execute("PRAGMA journal_size_limit = 4194304")
    if fresh:
        db.execute("PRAGMA auto_vacuum = INCREMENTAL")
    try:
        db.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass
    current = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_versions'").fetchone()
    if not current or db.execute("SELECT COUNT(*) FROM schema_versions").fetchone()[0] < len(MIGRATIONS):
        migrate(db)
    if project_name:
        name_legacy_rows(db, project_name)
    return db


def schema_versions(db: sqlite3.Connection) -> list[int]:
    return [r[0] for r in db.execute("SELECT version FROM schema_versions ORDER BY version")]

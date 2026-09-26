"""``platform.db``: connection setup and forward-only schema migrations (tracked in ``PRAGMA user_version``)."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROLE_CHECK = "CHECK (role IN ('owner', 'admin', 'member', 'viewer'))"

MIGRATIONS: list[str] = [
    # 1 — initial schema
    f"""
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

    CREATE TABLE users (
      id TEXT PRIMARY KEY,
      email TEXT NOT NULL UNIQUE COLLATE NOCASE,
      name TEXT NOT NULL DEFAULT '',
      password_hash TEXT,
      is_admin INTEGER NOT NULL DEFAULT 0,
      is_local INTEGER NOT NULL DEFAULT 0,
      must_change_password INTEGER NOT NULL DEFAULT 0,
      disabled INTEGER NOT NULL DEFAULT 0,
      created_at REAL NOT NULL,
      last_login_at REAL
    );

    CREATE TABLE teams (
      id TEXT PRIMARY KEY,
      slug TEXT NOT NULL UNIQUE,
      name TEXT NOT NULL,
      settings TEXT NOT NULL DEFAULT '{{}}',
      created_at REAL NOT NULL
    );

    CREATE TABLE memberships (
      team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
      user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      role TEXT NOT NULL {ROLE_CHECK},
      created_at REAL NOT NULL,
      PRIMARY KEY (team_id, user_id)
    );
    CREATE INDEX memberships_user ON memberships(user_id);

    CREATE TABLE projects (
      id TEXT PRIMARY KEY,
      team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
      slug TEXT NOT NULL,
      name TEXT NOT NULL,
      root TEXT,
      git_url TEXT,
      git_branch TEXT,
      data_dir TEXT NOT NULL,
      managed INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'ready',
      status_detail TEXT NOT NULL DEFAULT '',
      settings TEXT NOT NULL DEFAULT '{{}}',
      webhook_nonce TEXT,
      webhook_secret_hash TEXT,
      created_at REAL NOT NULL,
      updated_at REAL NOT NULL,
      last_synced_at REAL,
      UNIQUE (team_id, slug)
    );
    CREATE UNIQUE INDEX projects_root ON projects(root) WHERE root IS NOT NULL;

    CREATE TABLE project_roles (
      project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      role TEXT NOT NULL CHECK (role IN ('none', 'viewer', 'member', 'admin')),
      created_at REAL NOT NULL,
      PRIMARY KEY (project_id, user_id)
    );

    CREATE TABLE invitations (
      id TEXT PRIMARY KEY,
      team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
      email TEXT NOT NULL COLLATE NOCASE,
      role TEXT NOT NULL {ROLE_CHECK},
      token_hash TEXT NOT NULL UNIQUE,
      invited_by TEXT REFERENCES users(id) ON DELETE SET NULL,
      created_at REAL NOT NULL,
      expires_at REAL NOT NULL,
      accepted_at REAL,
      accepted_by TEXT REFERENCES users(id) ON DELETE SET NULL,
      revoked_at REAL
    );
    CREATE INDEX invitations_team ON invitations(team_id);

    CREATE TABLE api_tokens (
      id TEXT PRIMARY KEY,
      prefix TEXT NOT NULL UNIQUE,
      secret_hash TEXT NOT NULL,
      name TEXT NOT NULL,
      user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
      project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
      scopes TEXT NOT NULL DEFAULT '[]',
      created_by TEXT,
      created_at REAL NOT NULL,
      last_used_at REAL,
      expires_at REAL,
      revoked_at REAL
    );
    CREATE INDEX api_tokens_user ON api_tokens(user_id);
    CREATE INDEX api_tokens_team ON api_tokens(team_id);

    CREATE TABLE web_sessions (
      id TEXT PRIMARY KEY,
      token_hash TEXT NOT NULL UNIQUE,
      user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at REAL NOT NULL,
      expires_at REAL NOT NULL,
      last_seen_at REAL NOT NULL,
      user_agent TEXT NOT NULL DEFAULT '',
      ip TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX web_sessions_user ON web_sessions(user_id);

    CREATE TABLE audit_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts REAL NOT NULL,
      actor_id TEXT,
      actor TEXT NOT NULL,
      action TEXT NOT NULL,
      target TEXT NOT NULL DEFAULT '',
      team_id TEXT,
      project_id TEXT,
      ip TEXT NOT NULL DEFAULT '',
      detail TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE INDEX audit_team_ts ON audit_log(team_id, ts);
    CREATE INDEX audit_ts ON audit_log(ts);
    """,
]


def _statements(script: str) -> list[str]:
    out, buf = [], ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations atomically; safe when several processes open the database at once."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = schema_version(conn)
        if current > len(MIGRATIONS):
            raise RuntimeError(f"platform.db schema v{current} is newer than this Cairn (v{len(MIGRATIONS)})")
        for version in range(current + 1, len(MIGRATIONS) + 1):
            for stmt in _statements(MIGRATIONS[version - 1]):
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {version}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return schema_version(conn)


def _private(path: Path) -> None:
    for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        try:
            if p.exists():
                os.chmod(p, 0o600)
        except OSError:
            pass


def connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA secure_delete = ON")
    migrate(conn)
    _private(path)
    return conn

import contextlib
import json
import logging
import os
import sqlite3
import typing

logger = logging.getLogger(__name__)


class LLMCache:
    """Simple SQLite + JSON cache for LLM responses.

    Replaces diskcache to avoid unsafe pickle deserialization (CVE in diskcache <= 5.6.3).
    Only stores JSON-serializable data.
    """

    def __init__(self, directory: str):
        os.makedirs(directory, exist_ok=True)
        db_path = os.path.join(directory, 'cache.db')
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT)')
        self._conn.commit()

    def get(self, key: str) -> dict[str, typing.Any] | None:
        row = self._conn.execute('SELECT value FROM cache WHERE key = ?', (key,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            logger.warning(f'Corrupted cache entry for key {key}, ignoring')
            return None

    def set(self, key: str, value: dict[str, typing.Any]) -> None:
        try:
            serialized = json.dumps(value)
        except TypeError:
            logger.warning(f'Non-JSON-serializable cache value for key {key}, skipping')
            return
        self._conn.execute(
            'INSERT OR REPLACE INTO cache (key, value) VALUES (?, ?)',
            (key, serialized),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self._conn.close()

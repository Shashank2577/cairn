"""The system layer: sibling repositories of one product, declared file-locally.

Multiple repos belong to one product (UI, Api-Gateway, User-Service, ...); a change in one can
impact the others. A ``system.yaml`` at the repository root (fallback ``.cairn/system.yaml``)
declares those siblings with zero infrastructure::

    system: edgeplus
    repos:
      - path: ../ASG-Edgeplus-Api-Gateway
      - path: ../ASG-Edgeplus-User-Service

This is the file-local complement to the heavier ``cairn graph global add`` + ``/api/repos/map``
mechanism (global graph under ``$CAIRN_HOME/graph/``); the two stay independent. Sibling brains
are queried read-only straight from SQLite — no Cairn objects are constructed for them, so a
review never triggers syncs, model calls or writes.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

from ..linker import GENERIC

# The GENERIC drop list comes from the linker; STRUCTURAL adds platform words that match across
# almost any two repos (``.github/``, ``Adapter`` classes, source dirs) and would flood the
# cross-repo results with noise.
DECLARATIONS = ("system.yaml", ".cairn/system.yaml")  # first match wins
MAX_ROWS = 50_000  # safety cap when scanning a sibling's entity table

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NONWORD = re.compile(r"[^A-Za-z0-9]+")

STRUCTURAL = frozenset("""github gitlab bitbucket azure devops jira src lib pkg cmd internal impl
adapter adapters controller repository dto workflows actions maven gradle java kotlin swift""".split())
_MATCH_DROP = GENERIC | STRUCTURAL  # applied to both sides of a cross-repo match


def tokenize(raw: str | Iterable[str], drop: frozenset[str] = GENERIC) -> list[str]:
    """Split identifiers (camelCase, snake_case, paths) into lowercase tokens; drop generic and
    short ones. ``shop/payments.py`` and ``PaymentHooks`` both become useful query tokens."""
    items = [raw] if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for item in items:
        for part in _CAMEL.split(str(item)):
            for w in _NONWORD.split(part):
                t = w.lower()
                if len(t) >= 3 and t not in drop and t not in out:
                    out.append(t)
    return out


def _canon(token: str) -> str:
    """Loose singular form so ``payments`` matches ``payment`` (applied to both sides)."""
    return token[:-1] if len(token) >= 4 and token.endswith("s") and not token.endswith("ss") else token


@dataclass(frozen=True)
class Sibling:
    name: str
    root: Path
    db: Path


@dataclass(frozen=True)
class System:
    name: str
    root: Path
    siblings: tuple[Sibling, ...]
    skipped: tuple[dict, ...] = ()  # declared but invalid entries: {path, reason}
    declared_in: str = ""

    def query(self, sql: str, args: Sequence[Any] = ()) -> dict[str, list[dict]]:
        """One read-only statement against every sibling brain, as ``{repo: rows}``.

        A locked, missing or unreadable sibling yields ``[]`` — it never blocks the answer.
        """
        out: dict[str, list[dict]] = {}
        for s in self.siblings:
            try:
                out[s.name] = read_only_rows(s.db, sql, args)
            except sqlite3.Error:
                out[s.name] = []
        return out


def read_only_rows(db: Path, sql: str, args: Sequence[Any] = ()) -> list[dict]:
    """Query a brain.db without writing to it: a ``mode=ro`` URI connection (never a Cairn object,
    which would create directories, run schema migrations and spawn engines as side effects).

    Databases in WAL mode are a wrinkle: even a read-only open creates ``-shm``/``-wal`` files next
    to a sibling's brain, leaving traces in another repo's checkout. So when no non-empty ``-wal``
    exists (an idle clone — the common case) the database is opened ``immutable=1``: zero files
    created, and the checkpointed content is complete by definition. A live writer (non-empty
    ``-wal``) is read through ``mode=ro`` so pending commits are visible. Either way the database
    itself is never modified.
    """
    wal = Path(str(db) + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        attempts = (f"file:{db}?mode=ro", f"file:{db}?mode=ro&immutable=1")
    else:
        attempts = (f"file:{db}?mode=ro&immutable=1", f"file:{db}?mode=ro")
    last: sqlite3.OperationalError | None = None
    for uri in attempts:
        try:
            con = sqlite3.connect(uri, uri=True, timeout=5)
        except sqlite3.OperationalError as exc:
            last = exc
            continue
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only=1")
            return [dict(r) for r in con.execute(sql, tuple(args)).fetchall()]
        except sqlite3.OperationalError as exc:
            last = exc
        finally:
            con.close()
    raise last  # a caller (System.query, cross_repo_hits) treats sqlite3.Error as "sibling unavailable"


def discover(root: Path | None = None) -> System | None:
    """The declared system for ``root``, or None when no readable ``system.yaml`` exists.

    Tolerant by design: entries whose path is missing or has no ``.cairn/brain.db`` are skipped
    (reported in ``System.skipped``), a self-reference is dropped, and a declaration without a
    valid ``system:`` name falls back to the repository name.
    """
    root = Path(root or Path.cwd()).resolve()
    decl = next((p for name in DECLARATIONS for p in [root / name] if p.is_file()), None)
    if decl is None:
        return None
    try:
        data = yaml.safe_load(decl.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    name = str(data.get("system") or root.name)
    siblings: list[Sibling] = []
    skipped: list[dict] = []
    for entry in data.get("repos") or []:
        if isinstance(entry, str):  # tolerate a bare path where {path: ...} is expected
            entry = {"path": entry}
        if not isinstance(entry, dict) or not str(entry.get("path") or "").strip():
            continue
        raw = str(entry["path"]).strip()
        p = Path(raw)
        path = (p if p.is_absolute() else root / p).resolve()
        if path == root:
            continue  # a repo is never its own sibling
        db = path / ".cairn" / "brain.db"
        if not path.is_dir():
            skipped.append({"path": raw, "reason": "directory not found"})
        elif not db.is_file():
            skipped.append({"path": raw, "reason": "no .cairn/brain.db (run `cairn init` there)"})
        else:
            siblings.append(Sibling(name=str(entry.get("name") or path.name), root=path, db=db))
    return System(name=name, root=root, siblings=tuple(siblings), skipped=tuple(skipped),
                  declared_in=str(decl))


def cross_repo_hits(root: Path, query_tokens: str | Iterable[str], limit: int = 8) -> list[dict]:
    """Entities in sibling brains whose label/path overlaps ``query_tokens``.

    ``query_tokens`` accepts raw identifiers (file names, symbols) — tokenized here. Each sibling
    contributes at most ``limit`` hits; the combined list is ordered deterministically
    (score desc, then repo/label/path). Returns ``{repo, id, kind, label, path, score}``.
    """
    sysdef = discover(root)
    if sysdef is None or not sysdef.siblings:
        return []
    wanted = {_canon(t) for t in tokenize(query_tokens, drop=_MATCH_DROP)}
    if not wanted:
        return []
    hits: list[dict] = []
    for s in sysdef.siblings:
        try:
            rows = read_only_rows(s.db, f"SELECT id, kind, name, path FROM entities LIMIT {MAX_ROWS}")
        except sqlite3.Error:
            continue
        scored = []
        for r in rows:
            have = {_canon(t) for t in tokenize([r["name"] or "", r["path"] or ""], drop=_MATCH_DROP)}
            score = len(wanted & have)
            if score:
                scored.append((score, r["name"] or "", r["path"] or "", r["id"], r["kind"]))
        scored.sort(key=lambda x: (-x[0], x[1], x[2]))  # deterministic within the sibling
        for score, label, path, eid, kind in scored[:max(1, limit)]:
            hits.append({"repo": s.name, "id": eid, "kind": kind, "label": label, "path": path,
                         "score": score})
    hits.sort(key=lambda h: (-h["score"], h["repo"], h["label"], h["path"]))
    return hits


def system_context(root: Path | None = None) -> dict | None:
    """A small summary of the declared system: name, members and one-line stats per sibling."""
    sysdef = discover(root)
    if sysdef is None:
        return None
    members: list[dict] = []
    for s in sysdef.siblings:
        try:
            kinds = {r["kind"]: r["n"] for r in read_only_rows(
                s.db, "SELECT kind, COUNT(*) n FROM entities GROUP BY kind")}
            mem = read_only_rows(s.db, "SELECT COUNT(*) n FROM memories WHERE forgotten=0 "
                                       "AND superseded_by IS NULL")[0]["n"]
        except sqlite3.Error:
            kinds, mem = {}, 0
        members.append({"repo": s.name, "path": str(s.root), "entities": sum(kinds.values()),
                        "files": kinds.get("file", 0), "symbols": kinds.get("symbol", 0),
                        "memories": mem})
    return {"system": sysdef.name, "root": str(sysdef.root), "declared_in": sysdef.declared_in,
            "members": members, "skipped": list(sysdef.skipped)}

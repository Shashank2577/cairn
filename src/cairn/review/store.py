"""Review tables (in the read model) and their queries. Stdlib only, so `cairn.store` can create them."""
from __future__ import annotations

import hashlib
import json
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews(
  id TEXT PRIMARY KEY, project TEXT NOT NULL, branch TEXT NOT NULL DEFAULT '', pr_number INTEGER,
  base_sha TEXT NOT NULL, head_sha TEXT NOT NULL, trigger TEXT NOT NULL DEFAULT 'cli',
  mode TEXT NOT NULL DEFAULT 'deterministic', status TEXT NOT NULL DEFAULT 'running',
  author TEXT, paid_by TEXT, tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
  skipped TEXT NOT NULL DEFAULT '[]', sent TEXT NOT NULL DEFAULT '{}', context TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL, finished_at REAL,
  UNIQUE(project, head_sha, base_sha));
CREATE INDEX IF NOT EXISTS ix_reviews_created ON reviews(created_at DESC);
CREATE TABLE IF NOT EXISTS review_findings(
  id TEXT PRIMARY KEY, review_id TEXT NOT NULL, severity TEXT NOT NULL, path TEXT NOT NULL DEFAULT '',
  line_start INTEGER, line_end INTEGER, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL, provenance TEXT NOT NULL, citations TEXT NOT NULL DEFAULT '[]',
  author_kind TEXT NOT NULL DEFAULT 'rule', signature TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open',
  reason TEXT, decided_by TEXT, decided_at REAL);
CREATE INDEX IF NOT EXISTS ix_findings_review ON review_findings(review_id);
CREATE INDEX IF NOT EXISTS ix_findings_path ON review_findings(path, state);
CREATE TABLE IF NOT EXISTS review_rules(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, path_glob TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'finding', source_id TEXT, reason TEXT, matches INTEGER NOT NULL DEFAULT 0,
  created_by TEXT, created_at REAL NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
"""

SEVERITIES = ("high", "medium", "low", "note")
KINDS = ("cochange", "drift", "history", "convention", "secret", "test", "model")
PROVENANCE = ("EXTRACTED", "INFERRED")
STATES = ("open", "accepted", "dismissed", "fixed")


class FindingRejected(ValueError):
    """A finding that breaks the rules: no provenance, no citation, or an unknown severity or kind."""


def signature(kind: str, path: str, title: str) -> str:
    """Stable across runs: the same problem in the same file keeps its signature (triage and learning key on it)."""
    return hashlib.sha1(f"{kind}\x1f{path}\x1f{title}".encode("utf-8")).hexdigest()[:16]


def validate(f: dict) -> dict:
    cites = [c for c in (f.get("citations") or []) if isinstance(c, str) and c.strip()]
    if f.get("provenance") not in PROVENANCE:
        raise FindingRejected(f"finding {f.get('title')!r} has no provenance (EXTRACTED or INFERRED)")
    if not cites:
        raise FindingRejected(f"finding {f.get('title')!r} has no citation")
    if f.get("severity") not in SEVERITIES or f.get("kind") not in KINDS:
        raise FindingRejected(f"finding {f.get('title')!r} has an unknown severity or kind")
    if not (f.get("title") or "").strip():
        raise FindingRejected("finding has no title")
    return {**f, "citations": cites, "author_kind": f.get("author_kind", "rule")}


def _jl(value, default):
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default


def _review(row) -> dict:
    d = dict(row)
    for k, dflt in (("skipped", []), ("sent", {}), ("context", {})):
        d[k] = _jl(d.get(k), dflt)
    return d


def _finding(row) -> dict:
    d = dict(row)
    d["citations"] = _jl(d.get("citations"), [])
    return d


def find_review(brain, project: str, head_sha: str, base_sha: str) -> dict | None:
    row = brain.one("SELECT * FROM reviews WHERE project=? AND head_sha=? AND base_sha=?", (project, head_sha, base_sha))
    return _review(row) if row else None


def start_review(brain, *, project: str, branch: str, base_sha: str, head_sha: str, trigger: str = "cli",
                 pr_number: int | None = None, author: str | None = None) -> dict:
    """The one review for (project, head, base): an existing row is reset and reused, never duplicated."""
    old = find_review(brain, project, head_sha, base_sha)
    now = time.time()
    with brain.tx() as db:
        if old:
            db.execute("UPDATE reviews SET status='running', trigger=?, finished_at=NULL, created_at=? WHERE id=?",
                       (trigger, now, old["id"]))
            rid = old["id"]
        else:
            rid = uuid.uuid4().hex[:10]
            db.execute("INSERT INTO reviews(id,project,branch,pr_number,base_sha,head_sha,trigger,author,created_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?)", (rid, project, branch, pr_number, base_sha, head_sha, trigger,
                                                      author, now))
    return get_review(brain, rid)  # type: ignore[return-value]


def add_findings(brain, review_id: str, findings: list[dict]) -> list[dict]:
    """Replace the review's findings, keeping the triage state of a finding whose signature is unchanged.
    Every finding is validated first; one invalid finding rejects the whole set (nothing is written)."""
    good = [validate(f) for f in findings]
    prior = {r["signature"]: r for r in brain.q("SELECT * FROM review_findings WHERE review_id=?", (review_id,))}
    rows = []
    with brain.tx() as db:
        db.execute("DELETE FROM review_findings WHERE review_id=?", (review_id,))
        for f in good:
            sig = f.get("signature") or signature(f["kind"], f.get("path", ""), f["title"])
            old = prior.get(sig)
            fid = old["id"] if old else uuid.uuid4().hex[:10]
            state = old["state"] if old else "open"
            db.execute("INSERT INTO review_findings(id,review_id,severity,path,line_start,line_end,title,body,kind,"
                       "provenance,citations,author_kind,signature,state,reason,decided_by,decided_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (fid, review_id, f["severity"], f.get("path", ""), f.get("line_start"), f.get("line_end"),
                        f["title"], f.get("body", ""), f["kind"], f["provenance"], json.dumps(f["citations"]),
                        f["author_kind"], sig, state, old["reason"] if old else None,
                        old["decided_by"] if old else None, old["decided_at"] if old else None))
            rows.append(fid)
    return findings_of(brain, review_id)


def finish_review(brain, review_id: str, *, status: str = "complete", mode: str = "deterministic", skipped=None,
                  context=None, sent=None) -> dict:
    with brain.tx() as db:
        db.execute("UPDATE reviews SET status=?, mode=?, skipped=?, context=?, sent=?, finished_at=? WHERE id=?",
                   (status, mode, json.dumps(skipped or []), json.dumps(context or {}), json.dumps(sent or {}),
                    time.time(), review_id))
    return get_review(brain, review_id)  # type: ignore[return-value]


def get_review(brain, review_id: str) -> dict | None:
    row = brain.one("SELECT * FROM reviews WHERE id=? OR id LIKE ?", (review_id, review_id + "%"))
    return _review(row) if row else None


def list_reviews(brain, branch: str | None = None, limit: int = 20) -> list[dict]:
    sql, args = "SELECT * FROM reviews", []
    if branch:
        sql += " WHERE branch=?"
        args.append(branch)
    out = [_review(r) for r in brain.q(sql + " ORDER BY created_at DESC LIMIT ?", (*args, limit))]
    for r in out:
        r["counts"] = counts(brain, r["id"])
    return out


def findings_of(brain, review_id: str) -> list[dict]:
    order = {s: i for i, s in enumerate(SEVERITIES)}
    rows = [_finding(r) for r in brain.q("SELECT * FROM review_findings WHERE review_id=?", (review_id,))]
    rows.sort(key=lambda f: (order.get(f["severity"], 9), f["path"], f["line_start"] or 0, f["title"]))
    return rows


def counts(brain, review_id: str) -> dict:
    out = {s: 0 for s in SEVERITIES}
    for r in brain.q("SELECT severity, COUNT(*) n FROM review_findings WHERE review_id=? AND state!='dismissed' "
                     "GROUP BY severity", (review_id,)):
        out[r["severity"]] = r["n"]
    return out


def forget(brain, review_id: str) -> bool:
    r = get_review(brain, review_id)
    if not r:
        return False
    with brain.tx() as db:
        db.execute("DELETE FROM review_findings WHERE review_id=?", (r["id"],))
        db.execute("DELETE FROM reviews WHERE id=?", (r["id"],))
    return True

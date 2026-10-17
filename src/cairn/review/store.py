"""Review tables (in the read model) and their queries. Stdlib only, so `cairn.store` can create them."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews(
  id TEXT PRIMARY KEY, project TEXT NOT NULL, branch TEXT NOT NULL DEFAULT '', pr_number INTEGER,
  base_sha TEXT NOT NULL, head_sha TEXT NOT NULL, trigger TEXT NOT NULL DEFAULT 'cli' CHECK(trigger IN ('cli','ci','server','push','agent')),
  mode TEXT NOT NULL DEFAULT 'deterministic' CHECK(mode IN ('deterministic','model')),
  status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','complete','partial','superseded','failed')),
  author TEXT, paid_by TEXT, tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
  skipped TEXT NOT NULL DEFAULT '[]', sent TEXT NOT NULL DEFAULT '{}', context TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL, finished_at REAL,
  UNIQUE(project, head_sha, base_sha));
CREATE INDEX IF NOT EXISTS ix_reviews_created ON reviews(created_at DESC);
CREATE TABLE IF NOT EXISTS review_findings(
  id TEXT PRIMARY KEY, review_id TEXT NOT NULL, severity TEXT NOT NULL CHECK(severity IN ('high','medium','low','note')), path TEXT NOT NULL DEFAULT '',
  line_start INTEGER, line_end INTEGER, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK(kind IN ('cochange','drift','history','convention','secret','test','model')),
  provenance TEXT NOT NULL CHECK(provenance IN ('EXTRACTED','INFERRED')), citations TEXT NOT NULL DEFAULT '[]',
  author_kind TEXT NOT NULL DEFAULT 'rule' CHECK(author_kind IN ('rule','model')), signature TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','accepted','dismissed','fixed')),
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


class AmbiguousReview(ValueError):
    """A review id prefix that matches more than one review."""


class FindingRejected(ValueError):
    """A finding that breaks the rules: no provenance, no citation, or an unknown severity or kind."""


def signature(kind: str, path: str, rule: str) -> str:
    """Stable across runs and commits: kind + path + a rule id (never a line number or wording)."""
    return hashlib.sha1(f"{kind}\x1f{path}\x1f{rule}".encode("utf-8")).hexdigest()[:16]


def _rule_of(f: dict) -> str:
    return f.get("rule") or re.sub(r"\d+", "#", f.get("title", ""))


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


STAGED = "staged-"


def start_review(brain, *, project: str, branch: str, base_sha: str, head_sha: str, trigger: str = "cli",
                 pr_number: int | None = None, author: str | None = None) -> dict:
    """The one review for (project, head, base). Atomic: insert-or-ignore, then reset and refresh the row."""
    now = time.time()
    with brain.tx() as db:
        db.execute("INSERT OR IGNORE INTO reviews(id,project,branch,pr_number,base_sha,head_sha,trigger,author,created_at)"
                   " VALUES(?,?,?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex[:10], project, branch, pr_number, base_sha, head_sha, trigger, author, now))
        db.execute("UPDATE reviews SET status='running', trigger=?, branch=?, pr_number=COALESCE(?, pr_number), "
                   "author=COALESCE(?, author), finished_at=NULL, created_at=? "
                   "WHERE project=? AND head_sha=? AND base_sha=?",
                   (trigger, branch, pr_number, author, now, project, head_sha, base_sha))
    return find_review(brain, project, head_sha, base_sha)  # type: ignore[return-value]


def add_findings(brain, review_id: str, findings: list[dict]) -> list[dict]:
    """Replace the review's findings. A finding whose signature was triaged before (in this review or an earlier
    review of the same branch) keeps that decision. Every finding is validated first; one invalid finding rejects
    the whole set and nothing is written."""
    good = [validate(f) for f in findings]
    rev = brain.one("SELECT project, branch FROM reviews WHERE id=?", (review_id,))
    own = {r["signature"]: r for r in brain.q("SELECT * FROM review_findings WHERE review_id=?", (review_id,))}
    earlier: dict[str, object] = {}
    if rev and rev["branch"]:
        for r in brain.q("SELECT f.* FROM review_findings f JOIN reviews v ON v.id=f.review_id WHERE v.project=? AND "
                         "v.branch=? AND v.id!=? AND f.state IN ('accepted','dismissed') "
                         "ORDER BY f.decided_at ASC", (rev["project"], rev["branch"], review_id)):
            earlier[r["signature"]] = r  # the latest decision wins
    used: set[str] = set()
    with brain.tx() as db:
        db.execute("DELETE FROM review_findings WHERE review_id=?", (review_id,))
        for f in good:
            base = f.get("signature") or signature(f["kind"], f.get("path", ""), _rule_of(f))
            sig, n = base, 1
            while sig in used:  # two findings with one rule on one path stay distinct, in a stable order
                n += 1
                sig = f"{base}#{n}"
            used.add(sig)
            mine = own.get(sig)
            decided = mine if mine and mine["state"] in ("accepted", "dismissed") else earlier.get(sig)
            fid = mine["id"] if mine else uuid.uuid4().hex[:10]
            db.execute("INSERT INTO review_findings(id,review_id,severity,path,line_start,line_end,title,body,kind,"
                       "provenance,citations,author_kind,signature,state,reason,decided_by,decided_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (fid, review_id, f["severity"], f.get("path", ""), f.get("line_start"), f.get("line_end"),
                        f["title"], f.get("body", ""), f["kind"], f["provenance"], json.dumps(f["citations"]),
                        f["author_kind"], sig, decided["state"] if decided else "open",
                        decided["reason"] if decided else None, decided["decided_by"] if decided else None,
                        decided["decided_at"] if decided else None))
    return findings_of(brain, review_id)


def finish_review(brain, review_id: str, *, status: str = "complete", mode: str = "deterministic", skipped=None,
                  context=None, sent=None) -> dict:
    """Record the result. A finished review of a newer head supersedes the older reviews of the same branch or pull
    request, and their open findings that this review no longer reports become `fixed`."""
    with brain.tx() as db:
        db.execute("UPDATE reviews SET status=?, mode=?, skipped=?, context=?, sent=?, finished_at=? WHERE id=?",
                   (status, mode, json.dumps(skipped or []), json.dumps(context or {}), json.dumps(sent or {}),
                    time.time(), review_id))
        cur = db.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
        if status in ("complete", "partial") and not cur["head_sha"].startswith(STAGED):
            still = {r[0] for r in db.execute("SELECT signature FROM review_findings WHERE review_id=?", (review_id,))}
            olds = db.execute(
                "SELECT id FROM reviews WHERE project=? AND id!=? AND head_sha!=? AND head_sha NOT LIKE 'staged-%' "
                "AND status IN ('complete','partial') AND created_at<=? AND (branch=? OR (? IS NOT NULL AND pr_number=?))",
                (cur["project"], review_id, cur["head_sha"], cur["created_at"], cur["branch"], cur["pr_number"],
                 cur["pr_number"])).fetchall()
            for (oid,) in olds:
                for fid, sig in db.execute("SELECT id, signature FROM review_findings WHERE review_id=? AND "
                                           "state IN ('open','accepted')", (oid,)).fetchall():
                    if sig not in still:
                        db.execute("UPDATE review_findings SET state='fixed' WHERE id=?", (fid,))
                db.execute("UPDATE reviews SET status='superseded' WHERE id=?", (oid,))
    return get_review(brain, review_id)  # type: ignore[return-value]


def get_review(brain, review_id: str) -> dict | None:
    """Exact id, or an unambiguous prefix of hex digits. Anything else (wildcards included) matches nothing."""
    row = brain.one("SELECT * FROM reviews WHERE id=?", (review_id,))
    if row:
        return _review(row)
    if not re.fullmatch(r"[0-9a-f]{2,}", review_id or ""):
        return None
    rows = brain.q("SELECT * FROM reviews WHERE substr(id, 1, ?)=? LIMIT 3", (len(review_id), review_id))
    if len(rows) > 1:
        raise AmbiguousReview(f"'{review_id}' matches more than one review; use more characters.")
    return _review(rows[0]) if rows else None


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

"""Re-derive what changed, and mark what no longer holds (FR-018, FR-019).

A diagram that silently goes stale is worse than one that was never drawn. Every sync compares what the
repository derives now with what the store holds, and marks a claim *stale* — with the reason and the time
it was first seen — when the code that produced it no longer does. The claim is never deleted: the store
keeps it so the picture can show what was true, and ``drift.py`` reports it as ``stale-architecture``.

The re-check is incremental (FR-019). A claim whose evidence files are unchanged since the last sync cannot
have stopped being true, so it is reused exactly as stored and its files are not re-read; only claims with
evidence in changed files are re-derived. Claims with no evidence file — declared in ``system.yaml``, or
inferred from a name — carry nothing to hash and are left alone: a declaration states intent, it is not a
derivation that the code can refute.

Fingerprints are per claim and per file, so the cost of a sync is one stat per evidence file plus one read
of each file that actually moved. Deterministic, zero model calls, no network (FR-005, SC-003).
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from pathlib import Path

from . import model as model_mod
from .limits import MAX_EVIDENCE_PER_CLAIM, MAX_FILE_BYTES, MAX_FILES_SCANNED
from .model import Evidence, Model, clip

STATE_KEY = "sm.recheck:"          # per repository: the fingerprints of the last re-check
MAX_STALE_ROWS = 200               # claims read for drift findings; one repository never reaches it
UNREADABLE, MISSING = "unreadable", "missing"


# ------------------------------------------------------------------------------------------------ reading
def recheck(brain, root: Path, repo: str, fresh: Model, *, now: float | None = None) -> tuple[Model, dict]:
    """``fresh`` with the claims it no longer derives: stale where the evidence moved, reused otherwise.

    The returned model is what the caller stores. Claims in ``fresh`` are already current. Claims only in
    the store are carried over: marked stale when a file their evidence names changed (or when no
    fingerprint was recorded, so the first re-check after an upgrade re-examines everything), and reused
    untouched when nothing they depend on moved. Counting is bounded and deterministic.
    """
    root = Path(root)
    ts = time.time() if now is None else float(now)
    prev = model_mod.load(brain, repo)
    known_files, known_claims = _load_state(brain, repo)
    files = dict(known_files)
    states: dict[str, str] = {}

    def state_of(path: str) -> str:
        if path not in states:
            states[path] = _file_state(root, path, files)
            files[path] = states[path]
        return states[path]

    # A build that produced nothing, or one that ran out of room, is not evidence that every claim is false
    verdict = bool(fresh.elements) and "elements truncated" not in (getattr(fresh, "stats", None) or {})
    stats: Counter = Counter()
    tracked: dict[str, list[str]] = {}
    for kind, target in (("elements", fresh.elements), ("relationships", fresh.relationships)):
        for cid, claim in sorted(getattr(prev, kind).items()):
            paths = sorted({e.file for e in claim.evidence if e.file})[:MAX_EVIDENCE_PER_CLAIM]
            if cid in target:  # derived again: the fresh claim has replaced it, which clears any stale mark
                stats["cleared" if claim.stale_reason else "reused"] += 1
                if paths:
                    tracked[cid] = paths
                continue
            if claim.provenance == "declared" or not paths:
                stats["no-evidence"] += 1
                target[cid] = claim
                continue
            changed = [p for p in paths if state_of(p) != known_files.get(p)]
            if known_claims.get(cid) and not changed:
                stats["reused"] += 1
            else:
                stats["rechecked"] += 1
                if verdict and not claim.stale_reason:
                    _mark(claim, paths, states, ts)
                    stats["stale"] += 1
            target[cid] = claim
            tracked[cid] = paths
    _save_state(brain, repo, files, {cid: paths for cid, paths in tracked.items()})
    return fresh, dict(stats)


def stale_rows(brain, repo: str | None = None, *, limit: int = MAX_STALE_ROWS) -> list[dict]:
    """The stale claims in the store, with the evidence that named them, for drift findings."""
    where, args = ("WHERE stale_reason IS NOT NULL", ()) if repo is None else \
                  ("WHERE stale_reason IS NOT NULL AND repo=?", (repo,))
    els = brain.q(f"SELECT id, repo, level, type, name, stale_reason, stale_since FROM sm_element {where} "
                  "ORDER BY repo, id LIMIT ?", (*args, limit))
    rels = brain.q(f"SELECT id, repo, level, from_id, to_id, what, rule, stale_reason, stale_since "
                   f"FROM sm_relationship {where} ORDER BY repo, id LIMIT ?", (*args, limit))
    names = _names(brain, [r["from_id"] for r in rels] + [r["to_id"] for r in rels])
    out = [{"kind": "element", "id": r["id"], "repo": r["repo"], "name": r["name"], "type": r["type"],
            "rule": "", "what": None, "stale_reason": r["stale_reason"], "stale_since": r["stale_since"]}
           for r in els]
    out += [{"kind": "relationship", "id": r["id"], "repo": r["repo"], "name": "", "type": "",
             "from": names.get(r["from_id"], r["from_id"]), "to": names.get(r["to_id"], r["to_id"]),
             "rule": r["rule"], "what": r["what"], "stale_reason": r["stale_reason"],
             "stale_since": r["stale_since"]} for r in rels]
    ids = [r["id"] for r in out]
    if not ids:
        return out
    refs = _refs(brain, ids)
    for row in out:
        row["evidence"] = refs.get(row["id"], [])
    return sorted(out, key=lambda r: (r["repo"], r["kind"], r["id"]))


# ------------------------------------------------------------------------------------------------ state
def _load_state(brain, repo: str) -> tuple[dict, dict]:
    try:
        doc = json.loads(brain.get_kv(STATE_KEY + repo) or "{}")
    except ValueError:
        return {}, {}
    if not isinstance(doc, dict):
        return {}, {}
    files = doc.get("files") if isinstance(doc.get("files"), dict) else {}
    claims = doc.get("claims") if isinstance(doc.get("claims"), dict) else {}
    return {k: str(v) for k, v in files.items()}, {k: [str(x) for x in v] for k, v in claims.items() if isinstance(v, list)}


def _save_state(brain, repo: str, files: dict, claims: dict) -> None:
    keep = sorted(p for p in files)[:MAX_FILES_SCANNED]
    doc = {"files": {p: files[p] for p in keep},
           "claims": {cid: claims[cid] for cid in sorted(claims) if all(p in files for p in claims[cid])}}
    brain.set_kv(STATE_KEY + repo, json.dumps(doc, sort_keys=True, separators=(",", ":")))


def _file_state(root: Path, path: str, known: dict) -> str:
    """``size:mtime:digest`` for a file, ``missing``/``unreadable`` when it cannot be read.

    Size and mtime come first so an unchanged file costs a stat, never a read; the digest is what decides.
    """
    p = root / path
    try:
        st = p.stat()
    except OSError:
        return MISSING
    head = f"{st.st_size}:{st.st_mtime_ns}"
    if str(known.get(path) or "").startswith(head + ":"):
        return known[path]
    try:
        with p.open("rb") as fh:
            digest = hashlib.sha1(fh.read(MAX_FILE_BYTES)).hexdigest()[:16]
    except OSError:
        return UNREADABLE
    return f"{head}:{digest}"


# ------------------------------------------------------------------------------------------------ marking
def _mark(claim, paths: list[str], states: dict, ts: float) -> None:
    """Mark a claim stale in place, naming the evidence that is gone. The first sighting fixes ``since``."""
    claim.provenance = "stale"
    claim.stale_reason = _reason(claim, paths, states)
    claim.stale_since = claim.stale_since or ts


def _reason(claim, paths: list[str], states: dict) -> str:
    gone = [p for p in paths if states.get(p) == MISSING]
    if gone:
        text = f"evidence file {', '.join(gone[:2])} is gone"
    else:
        refs = [e.ref() for e in claim.evidence if e.file][:2]
        text = f"evidence no longer found: {', '.join(refs)}" if refs else "evidence no longer found"
    return clip(text) or "evidence no longer found"


# ------------------------------------------------------------------------------------------------ reading rows
def _names(brain, ids: list[str]) -> dict:
    out: dict[str, str] = {}
    for i in range(0, len(ids), 400):
        chunk = sorted({x for x in ids[i:i + 400] if x})
        if not chunk:
            continue
        for r in brain.q(f"SELECT id, name FROM sm_element WHERE id IN ({','.join('?' * len(chunk))})", chunk):
            out[r["id"]] = r["name"]
    return out


def _refs(brain, ids: list[str], per_claim: int = 3) -> dict:
    out: dict[str, list[str]] = {}
    for i in range(0, len(ids), 400):
        chunk = sorted(set(ids[i:i + 400]))
        if not chunk:
            continue
        for r in brain.q(f"SELECT claim_id, repo, file, line, entry, kind FROM sm_evidence "
                         f"WHERE claim_id IN ({','.join('?' * len(chunk))}) ORDER BY claim_id, file, line", chunk):
            bucket = out.setdefault(r["claim_id"], [])
            if len(bucket) < per_claim:
                bucket.append(Evidence(repo=r["repo"], kind=r["kind"], file=r["file"], line=r["line"],
                                       entry=r["entry"]).ref())
    return out
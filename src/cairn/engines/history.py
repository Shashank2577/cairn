"""Timeline layer (deterministic part): git history → events, file stats, co-change, risk.

Incremental: a cursor (last ingested commit) means each sync only reads new commits.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from itertools import combinations

from ..project import Project
from ..store import Brain

RISK = re.compile(r"\b(revert(ed|s)?|hot-?fix|fix(es|ed)?|bug|regression|incident|outage|rollback|roll back|"
                  r"security|cve-\d+|vuln|crash|data loss|race condition|deadlock|leak)\b", re.I)
REVERT = re.compile(r"\brevert", re.I)
COSMETIC = re.compile(r"\b(typos?|spelling|docs?|documentation|readme|changelog|lint(ing)?|format(ting)?|style|"
                      r"whitespace|comments?|wording|grammar)\b", re.I)
MAX_FILES_FOR_COCHANGE = 40  # bulk commits (renames, formatting) would add noise, not signal
SEP_C, SEP_F = "\x1e", "\x1f"


def risk_tags(subject: str, body: str = "") -> list[str]:
    text = f"{subject}\n{body}"
    tags = {m.group(1).lower().split("-")[0].replace(" ", "_") for m in RISK.finditer(text)}
    if COSMETIC.search(subject):  # "fix typo", "fix docs" are not risk signals
        tags -= {"fix", "fixes", "fixed", "bug"}
    tags = sorted(tags)
    if REVERT.search(subject):
        tags = sorted(set(tags) | {"revert"})
    return tags


def _parse(raw: str) -> list[dict]:
    commits = []
    for chunk in raw.split(SEP_C):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        head, _, rest = chunk.partition("\n")
        parts = head.split(SEP_F)
        if len(parts) < 5:
            continue
        sha, ts, author, subject, body = parts[0], parts[1], parts[2], parts[3], parts[4]
        # body may contain newlines; numstat lines follow a blank line after the header fields
        body_lines, files = [], []
        for line in (body + "\n" + rest).splitlines():
            m = re.match(r"^(\d+|-)\t(\d+|-)\t(.+)$", line)
            if m:
                path = m.group(3)
                if " => " in path:  # rename: keep the new path
                    path = re.sub(r"\{([^}]*) => ([^}]*)\}", r"\2", path)
                    path = path.split(" => ")[-1]
                path = path.replace("//", "/")
                adds = int(m.group(1)) if m.group(1) != "-" else 0
                dels = int(m.group(2)) if m.group(2) != "-" else 0
                files.append((path, adds, dels))
            elif line.strip():
                body_lines.append(line)
        commits.append({"sha": sha, "ts": float(ts), "author": author, "subject": subject,
                        "body": "\n".join(body_lines).strip(), "files": files})
    return commits


def ingest(project: Project, brain: Brain, max_commits: int | None = None) -> dict:
    """Read new commits since the cursor. Returns stats."""
    if not project.is_git or not project.git("rev-parse", "HEAD").strip():
        return {"commits": 0, "note": "no commits yet"}
    head = project.git("rev-parse", "HEAD").strip()
    cursor = brain.get_kv("history.cursor")
    if cursor == head:
        return {"commits": 0, "note": "up to date"}
    max_commits = max_commits or int(project.cfg("history.max_commits", 3000))
    fmt = f"--format={SEP_C}%H{SEP_F}%at{SEP_F}%an{SEP_F}%s{SEP_F}%b"
    rewritten = bool(cursor) and not _is_ancestor(project, cursor)  # amend/rebase moved the cursor
    if rewritten:  # the commits behind the cursor may be gone: drop this generation before the full
        # re-read so the rebuild is exact, not additive (upsert-by-id only refreshes surviving shas)
        brain.drop_source("history", ["commit"])  # entities + fts + links written by this source
        with brain.tx() as db:
            db.execute("DELETE FROM events WHERE source='history' AND kind='commit'")
    rng = [f"{cursor}..HEAD"] if cursor and not rewritten else []
    raw = project.git("log", "--no-merges", "--numstat", fmt, f"-n{max_commits}", *rng, timeout=300)
    commits = _parse(raw)
    if not commits:
        brain.set_kv("history.cursor", head)
        return {"commits": 0}

    stats: dict[str, dict] = {}
    if not rewritten:  # the rebuild below re-reads the whole log: seeding here would double-count it
        for r in brain.q("SELECT * FROM filestats"):
            stats[r["path"]] = {"commits": r["commits"], "risky": r["risky"], "last_ts": r["last_ts"],
                                "authors": set(json.loads(r["authors"]))}
    pairs: dict[tuple[str, str], list] = defaultdict(lambda: [0, 0.0])
    events, entities, links = [], [], []
    risky_count = 0
    for c in commits:
        tags = risk_tags(c["subject"], c["body"])
        risky_count += bool(tags)
        paths = [p for p, _, _ in c["files"]]
        cid = f"commit:{c['sha']}"
        events.append({"id": cid, "ts": c["ts"], "kind": "commit", "title": c["subject"][:200],
                       "body": c["body"][:2000], "actor": c["author"], "refs": [f"file:{p}" for p in paths[:50]],
                       "meta": {"sha": c["sha"], "risk": tags, "files": len(paths),
                                "churn": sum(a + d for _, a, d in c["files"])}, "source": "history"})
        entities.append((cid, "commit", c["subject"][:200], None,
                         {"sha": c["sha"][:12], "ts": c["ts"], "author": c["author"], "risk": tags}, "history",
                         c["body"][:500]))
        for p in paths[:200]:
            links.append((cid, f"file:{p}", "touches", "EXTRACTED", 1.0, "history"))
            s = stats.setdefault(p, {"commits": 0, "risky": 0, "last_ts": 0.0, "authors": set()})
            s["commits"] += 1
            s["risky"] += bool(tags)
            s["last_ts"] = max(s["last_ts"], c["ts"])
            s["authors"].add(c["author"])
        if 2 <= len(paths) <= MAX_FILES_FOR_COCHANGE:
            for a, b in combinations(sorted(set(paths)), 2):
                pair = pairs[(a, b)]
                pair[0] += 1
                pair[1] = max(pair[1], c["ts"])

    brain.add_events(events)
    brain.put_entities(entities)
    brain.link(links)
    with brain.tx() as db:
        if rewritten:  # filestats/cochange are fully derived from git log: wipe, or cochange doubles
            db.execute("DELETE FROM filestats")
            db.execute("DELETE FROM cochange")
        db.executemany(
            "INSERT INTO filestats(path,commits,risky,last_ts,authors) VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE "
            "SET commits=excluded.commits,risky=excluded.risky,last_ts=excluded.last_ts,authors=excluded.authors",
            [(p, s["commits"], s["risky"], s["last_ts"], json.dumps(sorted(s["authors"])[:20])) for p, s in stats.items()])
        db.executemany(
            "INSERT INTO cochange(a,b,count,last_ts) VALUES(?,?,?,?) ON CONFLICT(a,b) DO UPDATE SET "
            "count=cochange.count+excluded.count,last_ts=max(cochange.last_ts,excluded.last_ts)",
            [(a, b, n, ts) for (a, b), (n, ts) in pairs.items()])
    brain.set_kv("history.cursor", head)
    return {"commits": len(commits), "risky": risky_count, "files": len(stats)}


def _is_ancestor(project: Project, sha: str) -> bool:
    import subprocess
    try:
        return subprocess.run(["git", "-C", str(project.root), "merge-base", "--is-ancestor", sha, "HEAD"],
                              capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def risky_for(brain: Brain, paths: list[str], limit: int = 6) -> list[dict]:
    """Past fixes / reverts / incidents that touched these files — the 'historical warnings'."""
    out, seen = [], set()
    for p in paths[:20]:
        for ev in brain.events(kinds=["commit"], ref=f"file:{p}", limit=200):
            if ev["meta"].get("risk") and ev["id"] not in seen:
                seen.add(ev["id"])
                out.append({"id": ev["id"], "sha": ev["meta"]["sha"][:10], "ts": ev["ts"], "title": ev["title"],
                            "risk": ev["meta"]["risk"], "author": ev["actor"], "file": p})
    out.sort(key=lambda e: ("revert" not in e["risk"], -e["ts"]))
    return out[:limit]


def recent_for(brain: Brain, path: str, limit: int = 5) -> list[dict]:
    return [{"id": e["id"], "sha": e["meta"]["sha"][:10], "ts": e["ts"], "title": e["title"], "author": e["actor"]}
            for e in brain.events(kinds=["commit"], ref=f"file:{path}", limit=limit)]


def origins(project: Project, path: str, line: int | None = None, span: int = 40) -> list[dict]:
    """Commits that shaped a file (or a line range): who introduced it and who last changed it."""
    out: list[dict] = []
    if line:
        raw = project.git("blame", "--line-porcelain", "-w", "-L", f"{line},+{span}", "--", path, timeout=60)
        seen: dict[str, dict] = {}
        cur = None
        for ln in raw.splitlines():
            m = re.match(r"^([0-9a-f]{40}) \d+ \d+", ln)
            if m:
                cur = seen.setdefault(m.group(1), {"sha": m.group(1)[:10], "lines": 0})
                cur["lines"] += 1
            elif cur is not None and ln.startswith("summary "):
                cur["title"] = ln[8:]
            elif cur is not None and ln.startswith("author-time "):
                cur["ts"] = float(ln[12:])
            elif cur is not None and ln.startswith("author "):
                cur["author"] = ln[7:]
        out = sorted((c for c in seen.values() if not c["sha"].startswith("0000000")),
                     key=lambda c: -c["lines"])[:5]
    first = project.git("log", "--diff-filter=A", "--follow", "--format=%H%x1f%at%x1f%an%x1f%s", "-n1", "--", path)
    if first.strip():
        sha, ts, author, subject = (first.strip().split("\x1f") + ["", "", "", ""])[:4]
        intro = {"sha": sha[:10], "ts": float(ts or 0), "author": author, "title": subject, "introduced": True}
        if not any(o["sha"] == intro["sha"] for o in out):
            out.append(intro)
    return out

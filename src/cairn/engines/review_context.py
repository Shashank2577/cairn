"""The review-context pack: what any reviewer needs before reviewing a change.

Three deterministic sections — no model calls, no network, typically well under a second:

  Ticket       branch name (``story/FDY-18-slug``), Work-Item / Requirement trailers of the
               branch's commits (vs its base), and lines in ``specs/`` mentioning those ids.
  Local impact top dependents / warnings per changed file from this repo's own brain.
  Cross-repo   similarly named symbols in sibling repos declared by ``system.yaml``.

Rendered text is hard-capped at ``budget`` tokens (bytes / 4) and truncated gracefully, so any
reviewer (human, OpenCodeReview, an agent) can paste it straight into a review.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from ..core import Cairn
from . import systems

# `story/FDY-42-payment-hooks`, `bug/FDY-200-x`, `#509`, `REQ-002` — but not `story/` or `fix/`
_TICKET_ID = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b|\b#\d+\b")
_TRAILER = re.compile(r"^(Work-Item|Requirement):[ \t]*(.+?)[ \t]*$", re.MULTILINE)
_BASES = ("main", "origin/main", "master", "origin/master", "develop", "trunk")
_IMPACT_SECTIONS = ("Dependents", "Tests likely affected", "Historical warnings", "Changes together")
_MAX_COMMITS, _MAX_SPEC_FILES, _MAX_SPEC_HITS, _MAX_IMPACT_PER_FILE = 20, 200, 6, 4


def _est(text: str) -> int:
    return max(1, len(text) // 4)


def _clip(text: str, width: int = 160) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _git(root: Path, *args: str) -> str:
    try:
        res = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                             timeout=10, errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return res.stdout if res.returncode == 0 else ""


# ---- ticket -------------------------------------------------------------------------------------------
def _branch_ids(branch: str) -> list[str]:
    return _TICKET_ID.findall(branch or "")


def _trailers(body: str) -> tuple[list[str], list[str]]:
    work: list[str] = []
    reqs: list[str] = []
    for m in _TRAILER.finditer(body or ""):
        values = [v.strip() for v in m.group(2).split(",") if v.strip()]  # comma-tolerant
        (work if m.group(1) == "Work-Item" else reqs).extend(v for v in values if v not in work + reqs)
    return work, reqs


def _base_branch(root: Path) -> str | None:
    for b in _BASES:
        if _git(root, "rev-parse", "--verify", "--quiet", b):
            return b
    return None


def _branch_commits(root: Path, base: str | None) -> list[dict]:
    """Commits on this branch that main (or the best base) lacks: subject + body per commit."""
    rng = f"{base}..HEAD" if base else "-20"
    raw = _git(root, "log", "--format=%h%x1f%s%x1f%b%x1e", rng)
    out = []
    for record in raw.split("\x1e"):
        if not record.strip():
            continue
        parts = (record.lstrip("\n").split("\x1f") + ["", ""])[:3]
        sha, subject, body = parts[0].strip(), parts[1].strip(), parts[2]
        if sha:
            out.append({"sha": sha, "subject": subject, "body": body})
    return out


def _spec_hits(root: Path, ids: list[str]) -> list[dict]:
    """Lines in specs/ or requirements/index.md that mention one of the ticket ids."""
    if not ids:
        return []
    lowered = [i.lower() for i in ids]
    files = sorted((root / "specs").rglob("*.md")) if (root / "specs").is_dir() else []
    if (root / "requirements" / "index.md").is_file():
        files.append(root / "requirements" / "index.md")
    hits: list[dict] = []
    for path in files[:_MAX_SPEC_FILES]:
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        for n, line in enumerate(lines, 1):
            low = line.lower()
            if any(i in low for i in lowered):
                hits.append({"path": rel, "line": n, "text": _clip(line.strip(), 120)})
                if len(hits) >= _MAX_SPEC_HITS:
                    return hits
    return hits


def _ticket(root: Path, ticket: str | None) -> dict:
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    base = _base_branch(root)
    commits = _branch_commits(root, base)
    branch_ids = _branch_ids(branch)
    work: list[str] = []
    reqs: list[str] = []
    for c in commits:
        w, r = _trailers(c["body"])
        work += [x for x in w if x not in work]
        reqs += [x for x in r if x not in reqs]
        c["work_items"], c["requirements"] = w, r
    ids = list(dict.fromkeys(branch_ids + work + reqs + ([ticket] if ticket else [])))
    sub_work = [i for w in work for i in _TICKET_ID.findall(w)]  # `org/x#99` also matches as `#99`
    match_ids = list(dict.fromkeys(ids + sub_work))
    matched = None
    if ticket:
        low = ticket.lower()
        matched = bool(low in branch.lower()) or any(low in (c["subject"] + c["body"]).lower()
                                                     for c in commits) \
            or any(low in i.lower() for i in match_ids if i != ticket)
    return {"branch": branch or None, "base": base, "ticket_filter": ticket, "ids": ids,
            "work_items": work, "requirements": reqs,
            "commits": [{k: c[k] for k in ("sha", "subject", "work_items", "requirements")}
                        for c in commits[:_MAX_COMMITS]],
            "spec_hits": _spec_hits(root, match_ids), "filter_matched": matched}


def _ticket_lines(t: dict) -> list[str]:
    lines: list[str] = []
    if t["branch"]:
        ids = _branch_ids(t["branch"])
        lines.append(f"- branch: {t['branch']}" + (f" → {', '.join(ids)}" if ids else ""))
    if t["base"]:
        n = len(t["commits"])
        lines.append(f"- {n} commit{'s' if n != 1 else ''} since {t['base']}")
    for w in t["work_items"]:
        lines.append(f"- Work-Item: {w}")
    for r in t["requirements"]:
        lines.append(f"- Requirement: {r}")
    if t["ticket_filter"] and t["filter_matched"] is False:
        lines.append(f"- ticket {t['ticket_filter']}: not found in branch, trailers or subjects")
    for h in t["spec_hits"]:
        lines.append(f"- {h['path']}:{h['line']}: {h['text']}")
    return lines or ["- no ticket signals (branch name, Work-Item/Requirement trailers, specs)"]


# ---- local impact -------------------------------------------------------------------------------------
def _local_impact(root: Path, files: list[str]) -> tuple[list[str], dict]:
    db = Path(root) / ".cairn" / "brain.db"
    if not db.is_file():
        return (["- no local brain yet — run `cairn init` + `cairn sync` for repo-local impact"],
                {"available": False})
    try:
        c = Cairn.here(root)
    except (Exception, SystemExit):  # an unreadable project never blocks the pack
        return (["- local impact unavailable (no readable cairn brain)"], {"available": False})
    lines: list[str] = []
    per_file: list[dict] = []
    try:
        for f in files:
            entry: dict[str, Any] = {"file": f, "items": []}
            try:
                pack = c.impact(f, depth=1)
                items = sorted((i for i in pack.items if i.section in _IMPACT_SECTIONS),
                               key=lambda i: -i.score)[:_MAX_IMPACT_PER_FILE]
                if items:
                    lines.append(f"- {f}:")
                    for it in items:
                        entry["items"].append({"section": it.section, "text": it.text, "cite": it.cite})
                        lines.append(_clip(f"  - {it.text}" + (f" [{it.cite}]" if it.cite else "")))
                else:
                    lines.append(f"- {f}: no dependents or warnings recorded")
            except (Exception, SystemExit):
                lines.append(f"- {f}: impact lookup failed")
            per_file.append(entry)
    finally:
        c.close()
    return lines, {"available": True, "files": per_file}


# ---- cross-repo ---------------------------------------------------------------------------------------
def _cross_repo(root: Path, files: list[str]) -> tuple[list[str], list[dict], str | None]:
    sysdef = systems.discover(root)
    if sysdef is None:
        return (["- no system.yaml — sibling repos unknown (see `cairn system`)"], [], None)
    hits = systems.cross_repo_hits(root, [Path(f).stem for f in files] + files, limit=6)
    lines = [f"- ⚠ sibling: {h['repo']} — {h['label']} ({h['path']})" for h in hits]
    if not lines:
        lines = ["- no similarly named symbols in sibling repos"]
    return lines, hits, sysdef.name


# ---- the pack -----------------------------------------------------------------------------------------
def review_pack(root: Path | None = None, *, files: list[str], ticket: str | None = None,
                budget: int = 1500) -> dict:
    """Build the review-context pack for ``files``: text (≤ budget tokens) plus structured data."""
    root = Path(root or Path.cwd()).resolve()
    files = [f.strip() for f in files if f and f.strip()]
    t = _ticket(root, ticket)
    impact_lines, impact_data = _local_impact(root, files)
    xr_lines, xr_hits, system_name = _cross_repo(root, files)

    sections: list[tuple[str, list[str]]] = [
        ("Ticket", _ticket_lines(t)),
        ("Local impact", impact_lines),
        ("Cross-repo", xr_lines),
    ]
    title = f"## Review context — {root.name}" + (f" ({t['branch']})" if t["branch"] else "")
    text, used = _render(title, sections, budget)
    return {"text": text, "used": used, "budget": budget, "system": system_name, "ticket": t,
            "local_impact": impact_data, "cross_repo": xr_hits, "files": files}


def _render(title: str, sections: list[tuple[str, list[str]]], budget: int) -> tuple[str, int]:
    """Emit the title, then every line that fits; count what was cut and say so — never exceed.

    Each emitted line is charged its bytes/4 plus the newline that joins it, so the whole text
    stays within the promised budget, not just the sum of its parts.
    """
    def cost(s: str) -> int:
        return _est(s) + 1

    used = _est(title)
    out = [title]
    note = ""
    cut = sum(len(lines) for _, lines in sections)
    cap = budget
    if cut and budget > 24:  # reserve room for the truncation note so it always shows
        note = f"(+{{n}} lines truncated — budget {budget} tokens)"
        cap = budget - cost(note.format(n=cut))
    for name, lines in sections:
        if not lines:
            continue
        header = f"### {name}"
        cheapest = min(cost(ln) for ln in lines)
        if used + cost(header) + cheapest > cap:
            continue  # a header with no room for its first line is noise, not signal
        out.append(header)
        used += cost(header)
        for line in lines:
            c = cost(line)
            if used + c > cap:
                continue  # keep scanning: a shorter line may still earn its place
            out.append(line)
            used += c
            cut -= 1
    if cut > 0 and note:
        text = note.format(n=cut)
        used += cost(text)  # the reserve above made room for this; the final count is the honest one
        out.append(text)
    return "\n".join(out), used

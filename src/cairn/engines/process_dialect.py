"""Process dialect: repositories that run their own spec process instead of the bundled workflow.

Foundry-program style. ``requirements/index.md`` holds a requirement table (``| ID | Requirement |
PRD § |``), ``requirements/coverage.yaml`` declares machine-checkable criteria per requirement, and
work items live in git: ``story/FDY-<n>-<slug>`` / ``bug/FDY-<n>-<slug>`` branches plus
``Work-Item: <org>/<repo>#<n>`` and ``Requirement: REQ-0XX, REQ-0YY`` commit trailers.

Quoting foundry's coverage.yaml: "Satisfaction is the fraction of a requirement's checks that
pass — never a human's opinion of how done something feels." Check semantics mirror that repo's
own checker (dashboards/status.py ``run_check``): repo-root-relative glob patterns (``Path.glob``,
directories count as paths), ``count`` thresholds, plain-substring grep over every glob match, and
unknown kinds fail loudly rather than pass.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

INDEX = Path("requirements") / "index.md"
COVERAGE = Path("requirements") / "coverage.yaml"
FEATURE_PATH = INDEX.parent.as_posix()   # "requirements": the feature's home, like specs/NNN-name/
MAX_COMMITS = 5000                       # trailer-scan bound (history.ingest reads a similar window)
MAX_EVIDENCE = 3                         # file links recorded per check task

ROW_RE = re.compile(r"^\s*\|\s*(?P<id>REQ-\d{3,})\s*\|(?P<text>[^|]*)\|")
BRANCH_RE = re.compile(r"^(?P<type>[A-Za-z][\w-]*)/FDY-(?P<num>\d+)(?:-(?P<slug>.+))?$")
WORK_ITEM_RE = re.compile(r"^Work-Item:[ \t]*(?P<ref>.+?)[ \t]*$", re.IGNORECASE | re.MULTILINE)
REQ_TRAILER_RE = re.compile(r"^Requirement:[ \t]*(?P<ids>.+?)[ \t]*$", re.IGNORECASE | re.MULTILINE)
NUM_RE = re.compile(r"#(\d+)")
REQ_ID_RE = re.compile(r"REQ-\d{3,}")


def detect(root: Path) -> bool:
    """True when the repository carries a process-dialect requirement index. Deliberately dumb."""
    return (Path(root) / INDEX).is_file()


# ---- coverage checks --------------------------------------------------------------------------------
def _git(root: Path, *args: str) -> str:
    """Git output from ``root``; '' when git is missing, the tree is not a repo, or nothing matches."""
    try:
        res = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60,
                             check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return res.stdout if res.returncode == 0 else ""


def _is_ancestor(root: Path, sha: str) -> bool:
    """Whether a commit already reached the current HEAD (foundry's merged-work-item test)."""
    try:
        return subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", sha, "HEAD"],
                              capture_output=True, timeout=60, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _glob(root: Path, pattern: str) -> list[Path]:
    try:
        return sorted(Path(root).glob(pattern))
    except (OSError, ValueError, NotImplementedError):  # malformed, absolute or ..-escaping patterns
        return []


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _check_result(check: dict, root: Path) -> tuple[bool, str, list[tuple[str, bool]]]:
    """(passed, human description, evidence as (path, exists)) — semantics of foundry's run_check."""
    if not isinstance(check, dict):
        return False, "malformed check (not a mapping)", []
    try:
        if "exists" in check:
            target = str(check["exists"])
            ok = (root / target).exists()
            return ok, f"`{target}` exists", [(target, ok)]
        if "glob" in check:
            pattern = str(check["glob"])
            hits = _glob(root, pattern)
            return bool(hits), f"`{pattern}` matches something", [(_rel(root, p), True) for p in hits]
        if "count" in check:
            spec = check["count"]
            pattern, floor = str(spec["glob"]), int(spec["at_least"])
            n = len(_glob(root, pattern))
            return n >= floor, f"`{pattern}` >= {floor} (found {n})", []
        if "grep" in check:
            spec = check["grep"]
            pattern, needle = str(spec["glob"]), str(spec["pattern"])
            for path in _glob(root, pattern):
                try:
                    if needle in path.read_text():
                        return True, f"`{needle}` found in `{pattern}`", [(_rel(root, path), True)]
                except (OSError, UnicodeDecodeError):
                    continue
            return False, f"`{needle}` not found in `{pattern}`", []
    except (KeyError, TypeError, ValueError):
        return False, f"malformed check: {sorted(check)}", []
    if "delivered_by_agent" in check:  # foundry counts these via the GitHub API, not the repo
        return False, ("`delivered_by_agent` counts PRs merged by dispatched sessions on GitHub — "
                       "not checkable from the repository alone"), []
    return False, f"unknown check kind: {sorted(check)}", []


def evaluate_check(check: dict, root: Path) -> bool:
    """One coverage check against the repo root. ``exists`` / ``glob`` / ``count{glob, at_least}`` /
    ``grep{glob, pattern}`` pass as foundry's own checker computes them; anything else fails."""
    return _check_result(check, Path(root))[0]


# ---- sources ----------------------------------------------------------------------------------------
def _requirements(root: Path) -> list[dict]:
    """Rows of the index table: ``[{"id": "REQ-001", "text": "..."}]`` in file order."""
    out = []
    try:
        text = (root / INDEX).read_text(errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        if m := ROW_RE.match(line):
            out.append({"id": m["id"], "text": " ".join(m["text"].split())})
    return out


def _coverage(root: Path) -> dict:
    """The ``requirements:`` section of coverage.yaml, keyed by REQ id. Unreadable -> {} (all REQs
    then fall back to an unticked task, which still shows the requirement on the board honestly)."""
    try:
        import yaml
    except ImportError:  # pyyaml is a declared dependency; kept for embedded installs
        return {}
    try:
        data = yaml.safe_load((root / COVERAGE).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    reqs = data.get("requirements") if isinstance(data, dict) else None
    return reqs if isinstance(reqs, dict) else {}


def _trailer_reqs(root: Path, rev: str) -> dict[str, set[str]]:
    """Work-item number -> requirement ids, from ``Work-Item:``/``Requirement:`` trailers in the
    commit bodies reachable from ``rev``.

    ``git log`` only walks ancestors, so a number found at ``HEAD`` is proof that work item reached
    the current branch — foundry's own definition of a trace. Open branches are read through
    ``HEAD..<tip>``. This deliberately reads git directly (like history.ingest does) instead of the
    events table: it keeps features() a pure function of the repository, with no Brain needed.
    """
    reqs: dict[str, set[str]] = {}
    for chunk in _git(root, "log", rev, f"-n{MAX_COMMITS}", "--format=%x1e%b").split("\x1e"):
        body = chunk.strip("\n")
        if not body:
            continue
        nums = {n for m in WORK_ITEM_RE.finditer(body) for n in NUM_RE.findall(m["ref"])}
        if not nums:
            continue
        rids = {r for m in REQ_TRAILER_RE.finditer(body) for r in REQ_ID_RE.findall(m["ids"])}
        for n in nums:
            reqs.setdefault(n, set()).update(rids)
    return reqs


def _work_items(root: Path, merged: set[str]) -> list[dict]:
    """FDY work items declared by branch refs (local and remote), lowest issue number first."""
    items: dict[str, dict] = {}
    for line in _git(root, "for-each-ref", "--format=%(objectname) %(refname)",
                     "refs/heads", "refs/remotes").splitlines():
        sha, _, ref = line.strip().partition(" ")
        parts = ref.split("/")
        if parts[:2] == ["refs", "remotes"]:
            if parts[-1] == "HEAD":
                continue
            name, remote = "/".join(parts[3:]), True
        elif parts[:2] == ["refs", "heads"]:
            name, remote = "/".join(parts[2:]), False
        else:
            continue
        if not (m := BRANCH_RE.match(name)):
            continue
        num = m["num"]
        if num in items and items[num]["local"]:
            continue  # a local branch is the more authoritative tip than a remote copy
        items[num] = {"num": num, "type": m["type"].lower(), "slug": m["slug"] or "", "local": not remote,
                      "tip": sha, "merged": num in merged or _is_ancestor(root, sha)}
    return sorted(items.values(), key=lambda i: int(i["num"]))


# ---- feature assembly -------------------------------------------------------------------------------
def _clip(text: str, limit: int = 120) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip(" ,;") + "…"


def _fallback_task(req: dict) -> dict:
    """A REQ without coverage checks still shows on the board — unticked, never faked done."""
    return {"id": f"T-{req['id'].replace('-', '')}-1", "done": False, "parallel": False, "story": None,
            "text": f"no machine-checkable criteria in {COVERAGE.as_posix()} yet — satisfaction unknown",
            "phase": "", "reqs": [req["id"]], "files": []}


def _check_tasks(req: dict, checks: list, root: Path) -> list[dict]:
    """One task per coverage check: done = the check passes, right now, against the repository."""
    prefix = req["id"].replace("-", "")  # REQ-001 -> REQ001
    tasks = []
    for n, check in enumerate(checks, 1):
        ok, text, evidence = _check_result(check, root)
        tasks.append({"id": f"T-{prefix}-{n}", "done": ok, "parallel": False, "story": None,
                      "text": text, "phase": "", "reqs": [req["id"]], "files": evidence[:MAX_EVIDENCE]})
    return tasks


def _work_item_task(item: dict, rid: str | None) -> dict:
    state = "merged" if item["merged"] else "open"
    label = f"{item['type']} FDY-{item['num']}" + (f" {item['slug']}" if item["slug"] else "")
    return {"id": f"fdy-{item['num']}", "done": item["merged"], "parallel": False, "story": None,
            "text": f"{label} ({state})", "phase": "", "reqs": [rid] if rid else [], "files": []}


def _feature(req: dict | None, tasks: list[dict], root: Path) -> dict:
    base = Path(root) / FEATURE_PATH
    return {"id": f"process:{req['id'].lower()}" if req else "process:work-items",
            "title": _clip(req["text"]) if req else "Work items (no Requirement trailer)",
            "status": "", "stories": [], "requirements": [dict(req)] if req else [], "tasks": tasks,
            "progress": {"done": sum(t["done"] for t in tasks), "total": len(tasks)},
            "clarifications": 0,
            "artifacts": sorted(p.name for p in base.iterdir()) if base.is_dir() else [],
            "has_plan": False, "path": FEATURE_PATH, "plan_mentions": []}


def features(root: Path) -> list[dict]:
    """Requirements as features, coverage checks as tasks, FDY branches as work-item tasks.

    Shapes match ``specs.parse_feature`` exactly, so the same ingest/drift/read-model path serves
    them. Task ids: ``T-REQ001-<n>`` per coverage check (done = the check passes), ``fdy-<n>`` per
    FDY branch (done = merged: the branch tip is an ancestor of HEAD, or a commit on HEAD carries
    the matching ``Work-Item: ...#<n>`` trailer — squash merges rewrite the tip, the trailer is the
    trace). Each work-item task attaches to every REQ feature whose commits pair that ``Work-Item``
    with a ``Requirement: REQ-0XX`` trailer — read from commits already on HEAD (merged work) and
    from the branch's own unmerged commits (open work); unlinked ones land in
    ``process:work-items`` so the board still shows them instead of silently hiding them.
    """
    root = Path(root)
    reqs = _requirements(root)
    if not reqs:
        return []
    coverage = _coverage(root)
    reqs_by_item = _trailer_reqs(root, "HEAD")
    feats = []
    for req in reqs:
        spec = coverage.get(req["id"])
        checks = spec.get("checks") if isinstance(spec, dict) else None
        tasks = _check_tasks(req, checks, root) if isinstance(checks, list) and checks \
            else [_fallback_task(req)]
        feats.append(_feature(req, tasks, root))
    by_id = {f["id"]: f for f in feats}
    unlinked = []
    for item in _work_items(root, set(reqs_by_item)):
        rids = reqs_by_item.get(item["num"], set()) | \
            _trailer_reqs(root, f"HEAD..{item['tip']}").get(item["num"], set())
        hosts = [by_id[f"process:{rid.lower()}"] for rid in sorted(rids)
                 if f"process:{rid.lower()}" in by_id]
        if hosts:
            for host in hosts:
                host["tasks"].append(_work_item_task(item, host["requirements"][0]["id"]))
        else:
            unlinked.append(_work_item_task(item, None))
    if unlinked:
        feats.append(_feature(None, unlinked, root))
    for f in feats:  # progress counts check tasks and linked work items alike
        f["progress"] = {"done": sum(t["done"] for t in f["tasks"]), "total": len(f["tasks"])}
    return feats

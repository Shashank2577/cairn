"""Specs layer: intent. Bootstraps the spec-driven workflow and parses its artifacts into
features → user stories → requirements → tasks → files.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from ..project import Project
from ..store import Brain

EXTENSION_DIR = Path(__file__).resolve().parent.parent / "speckit_extension"
SPEC_REPO = "git+https://github.com/github/spec-kit.git"

TASK_RE = re.compile(r"^\s*[-*]\s+\[(?P<done>[ xX])\]\s+(?P<id>T\d{3,4})\b(?P<rest>.*)$")
STORY_RE = re.compile(r"^###\s+User Story\s+(?P<n>\d+)\s*[-—–:]\s*(?P<title>.+?)\s*\(Priority:\s*(?P<p>P\d)\)", re.I)
REQ_RE = re.compile(r"^\s*[-*]\s+\*\*(?P<id>(?:FR|NFR|SC)-\d{3})\*\*:?\s*(?P<text>.+)$")
PATHISH = re.compile(r"`([^`\s]+)`|(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,6}|[\w-]+\.(?:py|ts|tsx|js|jsx|go|"
                     r"rs|java|kt|rb|php|cs|swift|md|sql|yaml|yml|toml|json|sh|html|css))(?![\w/])")


# ---- tooling ---------------------------------------------------------------------------------------
def cli() -> list[str] | None:
    """How to invoke the workflow CLI: installed binary, else uvx from source, else None."""
    if shutil.which("specify"):
        return ["specify"]
    if shutil.which("uvx"):
        return ["uvx", "--from", SPEC_REPO, "specify"]
    return None


def initialized(root: Path) -> bool:
    return (root / ".specify").is_dir()


def bootstrap(project: Project, integration: str = "claude") -> tuple[bool, str]:
    """Add the workflow to the repo without touching existing files."""
    if initialized(project.root):
        return True, "already set up"
    cmd = cli()
    if not cmd:
        return False, "needs `uv` (https://docs.astral.sh/uv) — then run `cairn init` again"
    args = [*cmd, "init", "--here", "--integration", integration, "--non-interactive", "--ignore-agent-tools", "--force"]
    try:
        res = subprocess.run([*args, "--offline"], cwd=project.root, capture_output=True, text=True, timeout=300)
        if res.returncode != 0:  # offline bundle missing in some installs: fall back to online
            res = subprocess.run(args, cwd=project.root, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    ok = res.returncode == 0 and initialized(project.root)
    return ok, "workflow added" if ok else (res.stderr or res.stdout).strip().splitlines()[-1:][0] if (res.stderr or res.stdout) else "failed"


def install_extension(project: Project) -> tuple[bool, str]:
    """Register Cairn's hooks/commands inside the workflow (idempotent)."""
    if not initialized(project.root):
        return False, "workflow not set up"
    if (project.root / ".specify" / "extensions" / "cairn" / "extension.yml").exists():
        return True, "already installed"
    cmd = cli()
    if not cmd:
        return False, "workflow CLI unavailable"
    try:
        res = subprocess.run([*cmd, "extension", "add", "cairn", "--dev", str(EXTENSION_DIR)], cwd=project.root,
                             capture_output=True, text=True, timeout=180, input="y\n")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    ok = (project.root / ".specify" / "extensions" / "cairn").exists()
    return ok, "hooks installed" if ok else (res.stderr or res.stdout).strip()[-300:]


def new_feature(project: Project, description: str) -> dict:
    script = project.root / ".specify" / "scripts" / "bash" / "create-new-feature.sh"
    if not script.exists():
        raise RuntimeError("spec workflow not set up — run `cairn init`")
    res = subprocess.run(["bash", str(script), "--json", description], cwd=project.root, capture_output=True,
                         text=True, timeout=60)
    line = next((ln for ln in res.stdout.splitlines() if ln.startswith("{")), "{}")
    return json.loads(line)


# ---- parsing ---------------------------------------------------------------------------------------
def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def paths_in(text: str, root: Path) -> list[tuple[str, bool]]:
    out: dict[str, bool] = {}
    for m in PATHISH.finditer(text):
        raw = (m.group(1) or m.group(2) or "").strip().strip(".,;:()")
        if not raw or raw.startswith(("http", "#", "$")) or "*" in raw or len(raw) > 200:
            continue
        if "/" not in raw and "." not in raw:
            continue
        rel = raw.lstrip("./")
        if rel.startswith("/"):
            continue
        out.setdefault(rel, (root / rel).exists())
    return list(out.items())


def parse_feature(fdir: Path, root: Path) -> dict:
    spec = _read(fdir / "spec.md")
    tasks_md = _read(fdir / "tasks.md")
    plan = _read(fdir / "plan.md")
    title = next((ln.split(":", 1)[1].strip() for ln in spec.splitlines() if ln.startswith("# ") and ":" in ln),
                 fdir.name)
    status = next((ln.split(":", 1)[1].strip(" *") for ln in spec.splitlines() if ln.startswith("**Status**")), "")
    stories = [{"id": f"US{m['n']}", "title": m["title"].strip(), "priority": m["p"]}
               for ln in spec.splitlines() if (m := STORY_RE.match(ln))]
    reqs = [{"id": m["id"], "text": m["text"].strip()} for ln in spec.splitlines() if (m := REQ_RE.match(ln))]
    clar = len(re.findall(r"^- Q:", spec, re.M))
    tasks = []
    phase = ""
    for ln in tasks_md.splitlines():
        if ln.startswith("## "):
            phase = ln[3:].strip()
        m = TASK_RE.match(ln)
        if not m:
            continue
        rest = m["rest"]
        story = re.search(r"\[(US\d+)\]", rest)
        text = re.sub(r"\[(P|US\d+)\]\s*", "", rest).strip()
        tasks.append({"id": m["id"], "done": m["done"].lower() == "x", "parallel": "[P]" in rest,
                      "story": story.group(1) if story else None, "text": text, "phase": phase,
                      "reqs": sorted(set(re.findall(r"\b(?:FR|NFR|SC)-\d{3}\b", rest))),
                      "files": paths_in(rest, root)})
    done = sum(t["done"] for t in tasks)
    artifacts = sorted(p.name for p in fdir.iterdir() if p.is_file() or p.is_dir())
    return {"id": fdir.name, "title": title, "status": status, "stories": stories, "requirements": reqs,
            "tasks": tasks, "progress": {"done": done, "total": len(tasks)}, "clarifications": clar,
            "artifacts": artifacts, "has_plan": bool(plan), "path": fdir.relative_to(root).as_posix(),
            "plan_mentions": sorted(set(re.findall(r"\b(?:FR|NFR|SC)-\d{3}\b", plan)))}


def constitution(root: Path) -> dict | None:
    text = _read(root / ".specify" / "memory" / "constitution.md")
    if not text or "[PROJECT_NAME]" in text:
        return None
    version = re.search(r"\*\*Version\*\*:\s*([\d.]+)", text)
    principles = [ln[4:].strip() for ln in text.splitlines() if ln.startswith("### ")]
    return {"version": version.group(1) if version else "", "principles": principles[:12]}


def features(root: Path) -> list[dict]:
    base = root / "specs"
    if not base.is_dir():
        return []
    return [parse_feature(d, root) for d in sorted(base.iterdir()) if d.is_dir() and (d / "spec.md").exists()]


def ingest(project: Project, brain: Brain) -> dict:
    """Parse all features into the read model. Skips work when artifacts are unchanged."""
    feats = features(project.root)
    digest = hashlib.sha1(json.dumps(feats, sort_keys=True, default=str).encode()).hexdigest()
    const = constitution(project.root)
    if brain.get_kv("specs.digest") == digest:
        return {"features": len(feats), "note": "unchanged"}
    brain.drop_source("specs", kinds=("spec", "story", "req", "task"))
    ents, links, events = [], [], []
    for f in feats:
        sid = f"spec:{f['id']}"
        ents.append((sid, "spec", f["title"], f["path"], {k: f[k] for k in ("status", "progress", "clarifications",
                                                                            "artifacts")}, "specs",
                     " ".join(r["text"] for r in f["requirements"])))
        for s in f["stories"]:
            ent = f"story:{f['id']}/{s['id']}"
            ents.append((ent, "story", s["title"], f["path"], {"priority": s["priority"]}, "specs", ""))
            links.append((ent, sid, "part_of", "EXTRACTED", 1.0, "specs"))
        for r in f["requirements"]:
            ent = f"req:{f['id']}/{r['id']}"
            ents.append((ent, "req", f"{r['id']} {r['text'][:120]}", f["path"], {}, "specs", r["text"]))
            links.append((ent, sid, "part_of", "EXTRACTED", 1.0, "specs"))
        for t in f["tasks"]:
            ent = f"task:{f['id']}/{t['id']}"
            ents.append((ent, "task", f"{t['id']} {t['text'][:120]}", f["path"],
                         {"done": t["done"], "story": t["story"], "phase": t["phase"], "parallel": t["parallel"],
                          "files": t["files"]}, "specs", t["text"]))
            links.append((ent, sid, "part_of", "EXTRACTED", 1.0, "specs"))
            if t["story"]:
                links.append((ent, f"story:{f['id']}/{t['story']}", "implements", "EXTRACTED", 1.0, "specs"))
            for rid in t["reqs"]:
                links.append((ent, f"req:{f['id']}/{rid}", "implements", "EXTRACTED", 1.0, "specs"))
            for path, exists in t["files"]:
                links.append((ent, f"file:{path}", "owns", "EXTRACTED", 1.0 if exists else 0.5, "specs"))
        mtime = max(((project.root / f["path"] / a).stat().st_mtime for a in ("spec.md", "tasks.md", "plan.md")
                     if (project.root / f["path"] / a).exists()), default=0)
        events.append({"id": f"specstate:{f['id']}:{f['progress']['done']}", "ts": mtime, "kind": "spec",
                       "title": f"{f['title']} — {f['progress']['done']}/{f['progress']['total']} tasks",
                       "refs": [sid], "meta": f["progress"], "source": "specs"})
    brain.put_entities(ents)
    brain.link(links)
    brain.add_events(events)
    brain.set_kv("specs.digest", digest)
    brain.set_kv("specs.constitution", json.dumps(const))
    return {"features": len(feats), "tasks": sum(len(f["tasks"]) for f in feats)}
